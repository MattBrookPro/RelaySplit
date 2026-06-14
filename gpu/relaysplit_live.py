"""
RelaySplit LIVE — WebRTC audio -> streaming Demucs vocal separation -> WebRTC audio, on a warm,
region-pinned UK GPU. The moment of truth: play music into the browser, hear just the vocal come
back through a cloud GPU, with the latency measured on screen.

Design (self-contained signalling for now; VPS /ws integration is the next slice):
  - The container is a WebRTC peer (aiortc). Inbound 48 kHz audio -> stateful resample to 44.1 kHz
    -> OnlineSeparator (0.25 s chunk / 5 s context / 20 ms crossfade, validated offline) -> resample
    back to 48 kHz -> outbound track. Demucs inference runs OFF the event loop so audio never stalls.
  - ICE config comes from our DEPLOYED control plane: the container fetches ephemeral TURN creds
    from https://relaysplit.vaguelystrange.com/api/turn and serves them to the page via /ice
    (same-origin -> no CORS). A serverless container has no public inbound, so media is TURN-relayed.
  - The page shows the live latency meter: network RTT (getStats) + per-chunk inference (data channel).

    modal deploy gpu/relaysplit_live.py     # then open the printed *.modal.run URL
"""
import modal

REGION = "uk"
MODEL_NAME = "htdemucs"
CHUNK_S, CONTEXT_S, FADE_S = 0.25, 5.0, 0.02
MODEL_SR, WEBRTC_SR = 44100, 48000
TURN_ENDPOINT = "https://relaysplit.vaguelystrange.com/api/turn"


class OnlineSeparator:
    """Stateful streaming separator (same logic validated offline in relaysplit_gpu.py). Push
    inbound audio (ch, n) at MODEL_SR; pop emitted vocal blocks (one hop each), linear-crossfaded."""

    def __init__(self, separate_voc, sr, chunk_s, context_s, fade_s, channels=2):
        import numpy as np

        self.sep = separate_voc
        self.hop = max(1, int(chunk_s * sr))
        self.ola = max(0, int(fade_s * sr))
        self.ctx = int(context_s * sr)
        self.max_seg = int(7.6 * sr)
        self.inbuf = np.zeros((channels, 0), dtype="float32")
        self.base = 0
        self.processed = 0
        self.held = np.zeros((channels, self.ola), dtype="float32")
        self.k = 0
        self.fin = np.linspace(0.0, 1.0, self.ola, dtype="float32") if self.ola else None
        self.fout = np.linspace(1.0, 0.0, self.ola, dtype="float32") if self.ola else None

    def push(self, x):
        import numpy as np

        self.inbuf = np.concatenate([self.inbuf, x.astype("float32")], axis=1)

    def pop(self):
        import numpy as np

        out = []
        while self.base + self.inbuf.shape[1] >= self.processed + self.hop + self.ola:
            pos = self.processed
            seg_end = pos + self.hop + self.ola
            start = max(0, pos - self.ctx, seg_end - self.max_seg)
            seg = self.inbuf[:, start - self.base : seg_end - self.base]
            blk = self.sep(seg)[:, pos - start : seg_end - start]  # (ch, hop+ola)
            if self.ola:
                head = blk[:, : self.ola].copy() if self.k == 0 else blk[:, : self.ola] * self.fin + self.held
                self.held = blk[:, self.hop : self.hop + self.ola] * self.fout
                emit = np.concatenate([head, blk[:, self.ola : self.hop]], axis=1)
            else:
                emit = blk[:, : self.hop]
            out.append(emit)
            self.processed += self.hop
            self.k += 1
            keep = max(0, self.processed - self.max_seg)
            if keep > self.base:
                self.inbuf = self.inbuf[:, keep - self.base :]
                self.base = keep
        return out


def _bake_model():
    from demucs.pretrained import get_model

    get_model(MODEL_NAME)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "numpy<2",
        "torch==2.4.1",
        "torchaudio==2.4.1",
        "demucs==4.0.1",
        "aiortc==1.9.0",
        "av==12.3.0",
        "fastapi[standard]==0.115.4",
    )
    .run_function(_bake_model)
)

app = modal.App("relaysplit-live", image=image)


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RelaySplit — live vocal separation</title>
<style>
  body{font:15px/1.5 system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 16px}
  h1{font-size:20px} button{font-size:15px;padding:8px 16px;cursor:pointer}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0}
  .badge{padding:2px 10px;border-radius:4px;background:#eee;font-family:ui-monospace,monospace}
  .ok{background:#d4f7d4}.bad{background:#f7d4d4}.warn{background:#f7efd4}
  .meter{font-size:22px;font-family:ui-monospace,monospace}
  #log{white-space:pre-wrap;background:#111;color:#6f6;padding:10px;border-radius:6px;margin-top:12px;font:12px ui-monospace,monospace;min-height:80px}
</style></head><body>
<h1>RelaySplit — live vocal separation through a cloud GPU</h1>
<p>Pick a music file (or use your mic), hit <b>Start</b>, and you'll hear just the <b>vocal</b>
   come back, separated live on a UK GPU. <b>Headphones recommended.</b></p>
<div class="row">
  <input type="file" id="file" accept="audio/*"/>
  <label><input type="checkbox" id="mic"/> use mic instead</label>
</div>
<div class="row">
  <button id="start">Start</button><button id="stop" disabled>Stop</button>
  <span>conn <span id="conn" class="badge">—</span></span>
  <span>path <span id="path" class="badge">—</span></span>
</div>
<div class="row meter">
  net RTT <span id="rtt" class="badge">— ms</span>
  &nbsp; inference <span id="inf" class="badge">— ms</span>
</div>
<audio id="out" autoplay></audio>
<div id="log"></div>
<script>
const $=id=>document.getElementById(id), log=m=>{$("log").textContent+=m+"\n";console.log(m)};
const setb=(el,t,c)=>{el.textContent=t;el.className="badge "+(c||"")};
let pc, srcStream, statsTimer, dc;

async function start(){
  $("start").disabled=true;
  try{
    const {iceServers}=await (await fetch("ice")).json();
    log("ICE: "+iceServers.map(s=>s.urls).join(", "));
    pc=new RTCPeerConnection({iceServers});

    pc.onconnectionstatechange=()=>{const s=pc.connectionState;setb($("conn"),s,s=="connected"?"ok":(s=="failed"?"bad":"warn"));log("conn "+s);if(s=="connected")poll()};
    pc.ontrack=e=>{log("remote track: "+e.track.kind);$("out").srcObject=e.streams[0]};

    // data channel for the container's inference-time reports
    dc=pc.createDataChannel("stats");
    dc.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.infer_ms!=null)setb($("inf"),d.infer_ms.toFixed(0)+" ms","ok")}catch{}};

    if($("mic").checked){
      srcStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
    }else{
      const f=$("file").files[0]; if(!f){log("pick a file or tick mic");$("start").disabled=false;return}
      // Route the file through Web Audio to a capture destination and DO NOT connect to speakers,
      // so ONLY the separated vocal coming back is audible (not the original mix overlaid).
      const actx=new (window.AudioContext||window.webkitAudioContext)();
      const el=new Audio(URL.createObjectURL(f)); el.loop=true;
      const dest=actx.createMediaStreamDestination();
      actx.createMediaElementSource(el).connect(dest);  // -> WebRTC only; not actx.destination => silent locally
      await actx.resume(); await el.play();
      srcStream=dest.stream;
    }
    srcStream.getAudioTracks().forEach(t=>pc.addTrack(t,srcStream));
    log("sending "+srcStream.getAudioTracks().length+" audio track(s)");

    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r=>{if(pc.iceGatheringState=="complete")return r();const c=()=>{if(pc.iceGatheringState=="complete"){pc.removeEventListener("icegatheringstatechange",c);r()}};pc.addEventListener("icegatheringstatechange",c);setTimeout(r,4000)});
    const ans=await (await fetch("offer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sdp:pc.localDescription.sdp,type:pc.localDescription.type})})).json();
    await pc.setRemoteDescription(ans);
    log("negotiating...");$("stop").disabled=false;
  }catch(err){log("ERROR "+err);setb($("conn"),"error","bad");$("start").disabled=false}
}
async function poll(){
  if(statsTimer)return;
  statsTimer=setInterval(async()=>{
    const s=await pc.getStats(); let pair,c={};
    s.forEach(r=>{if(r.type=="candidate-pair"&&r.nominated&&r.state=="succeeded")pair=r;if(r.type.endsWith("-candidate"))c[r.id]=r});
    if(pair){
      if(pair.currentRoundTripTime!=null)setb($("rtt"),(pair.currentRoundTripTime*1000).toFixed(0)+" ms","ok");
      const l=c[pair.localCandidateId],rc=c[pair.remoteCandidateId];
      if(l&&rc)setb($("path"),l.candidateType+" / "+rc.candidateType,(l.candidateType=="relay"||rc.candidateType=="relay")?"warn":"ok");
    }
  },1000);
}
function stop(){clearInterval(statsTimer);statsTimer=null;if(pc)pc.close();if(srcStream)srcStream.getTracks().forEach(t=>t.stop());$("stop").disabled=true;$("start").disabled=false;log("stopped")}
$("start").onclick=start;$("stop").onclick=stop;
</script></body></html>
"""


@app.function(image=image, gpu="L4", region=REGION, max_containers=1, scaledown_window=300)
@modal.concurrent(max_inputs=12)
@modal.asgi_app()
def web():
    import asyncio
    import json
    import logging
    import time
    import urllib.request
    from fractions import Fraction

    import av
    import numpy as np
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("relaysplit-live")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = get_model(MODEL_NAME).to(device).eval()
    sources = list(model.sources)
    voc_idx = sources.index("vocals")
    log.info("model loaded on %s; sources=%s", device, sources)

    def sep_voc(seg_np):
        seg = torch.from_numpy(np.ascontiguousarray(seg_np))
        ref = seg.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        x = (seg - mean) / std
        with torch.no_grad():
            out = apply_model(model, x[None].to(device), split=False, device=device)[0]
        return (out * std + mean)[voc_idx].cpu().numpy()

    def get_ice():
        try:
            req = urllib.request.Request(
                TURN_ENDPOINT,
                data=json.dumps({"label": "modal-live"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())["iceServers"]
        except Exception as e:  # fall back to STUN-only if the control plane is unreachable
            log.warning("get_ice failed (%s); STUN only", e)
            return [{"urls": ["stun:stun.l.google.com:19302"]}]

    class SeparatedTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self, source, channel_box):
            super().__init__()
            self.source = source
            self.channel_box = channel_box  # mutable holder for the stats data channel
            self.sep = OnlineSeparator(sep_voc, MODEL_SR, CHUNK_S, CONTEXT_S, FADE_S)
            self.r_in = av.AudioResampler(format="fltp", layout="stereo", rate=MODEL_SR)
            self.r_out = av.AudioResampler(format="fltp", layout="stereo", rate=WEBRTC_SR)
            self.ring = np.zeros((2, 0), dtype="float32")
            self.lock = asyncio.Lock()
            self._start = None
            self._ts = 0
            self._opts = 0
            self.task = asyncio.ensure_future(self._consume())

        async def _consume(self):
            loop = asyncio.get_event_loop()
            try:
                while True:
                    frame = await self.source.recv()
                    for rf in self.r_in.resample(frame):
                        self.sep.push(rf.to_ndarray())  # (2, n) float32 @ 44.1k
                    t0 = time.perf_counter()
                    blocks = await loop.run_in_executor(None, self.sep.pop)  # GPU off the loop
                    if blocks:
                        infer_ms = (time.perf_counter() - t0) * 1000 / len(blocks)
                        ch = self.channel_box.get("dc")
                        if ch and ch.readyState == "open":
                            ch.send(json.dumps({"infer_ms": round(infer_ms, 1)}))
                    for blk in blocks:
                        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(blk), format="fltp", layout="stereo")
                        af.sample_rate = MODEL_SR
                        af.pts = self._opts
                        af.time_base = Fraction(1, MODEL_SR)
                        self._opts += blk.shape[1]
                        for of in self.r_out.resample(af):
                            arr = of.to_ndarray()  # (2, m) @ 48k
                            async with self.lock:
                                self.ring = np.concatenate([self.ring, arr], axis=1)
            except Exception as e:
                log.info("consume ended: %s", e)

        async def recv(self):
            n = WEBRTC_SR // 50  # 20 ms
            if self._start is None:
                self._start = time.time()
                self._ts = 0
            self._ts += n
            wait = self._start + self._ts / WEBRTC_SR - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            async with self.lock:
                if self.ring.shape[1] >= n:
                    out = self.ring[:, :n]
                    self.ring = self.ring[:, n:]
                else:
                    out = np.zeros((2, n), dtype="float32")
            s16 = (np.clip(out, -1, 1) * 32767).astype("int16")
            frame = av.AudioFrame.from_ndarray(s16.T.reshape(1, -1), format="s16", layout="stereo")
            frame.sample_rate = WEBRTC_SR
            frame.pts = self._ts - n
            frame.time_base = Fraction(1, WEBRTC_SR)
            return frame

    api = FastAPI()
    pcs = set()

    @api.get("/", response_class=HTMLResponse)
    async def index():
        return INDEX_HTML

    @api.get("/ice")
    async def ice():
        return JSONResponse({"iceServers": get_ice()})

    @api.post("/offer")
    async def offer(request: Request):
        params = await request.json()
        servers = [RTCIceServer(**s) for s in get_ice()]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))
        pcs.add(pc)
        box = {}

        @pc.on("datachannel")
        def on_dc(channel):
            box["dc"] = channel

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("pc state %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                pcs.discard(pc)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                pc.addTrack(SeparatedTrack(track, box))

        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    return api
