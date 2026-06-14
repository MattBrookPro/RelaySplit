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
# Latency (measured, fp16): htdemucs inference is ~81 ms FLAT regardless of segment length on L4 (it's
# launch-overhead-bound, not compute-bound), so past context is genuinely FREE — keep 5 s for quality —
# and the only latency lever is the hop. The live per-chunk cost ≈ forward + ~45 ms of aiortc/event-loop
# overhead, so the hop is sized ADAPTIVELY at startup to whatever GPU we landed on (see web()): ~0.15 s
# on L4, ~0.10 s on L40S/A100. CHUNK_S here is only the fallback if the startup measurement fails.
CHUNK_S, CONTEXT_S, FADE_S = 0.15, 5.0, 0.01
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


DEMO_TRACK = "/demo-track.ogg"  # baked public-domain clip for the always-on /demo feed


def _bake_demo():
    # Bake a public-domain track into the image so /demo always has something to separate live, with
    # no external fetch at runtime (Bessie Smith, "Downhearted Blues", 1923 — public domain).
    import urllib.request

    url = "https://commons.wikimedia.org/wiki/Special:FilePath/Bessie%20Smith%20-%20Downhearted%20Blues%20(1923).ogg"
    req = urllib.request.Request(url, headers={"User-Agent": "RelaySplit/0.1 (demo; research)"})
    with urllib.request.urlopen(req, timeout=90) as r, open(DEMO_TRACK, "wb") as f:
        f.write(r.read())


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
        "websockets==12.0",
    )
    .run_function(_bake_model)
    .run_function(_bake_demo)
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
const CHANNEL=new URLSearchParams(location.search).get("channel")||"";  // broadcast key (peers tune into it)
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

    if(window.__demo){
      // Always-on demo: separate a baked public-domain track — no file/mic, one click only.
      const buf=await (await fetch("demo-track")).arrayBuffer();
      const actx=new (window.AudioContext||window.webkitAudioContext)();
      const el=new Audio(URL.createObjectURL(new Blob([buf]))); el.loop=true;
      const dest=actx.createMediaStreamDestination();
      actx.createMediaElementSource(el).connect(dest);
      await actx.resume(); await el.play();
      srcStream=dest.stream;
    } else if($("mic").checked){
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
    const ans=await (await fetch("offer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel:CHANNEL,sdp:pc.localDescription.sdp,type:pc.localDescription.type})})).json();
    await pc.setRemoteDescription(ans);
    if(CHANNEL)log("broadcasting channel "+CHANNEL+" — assigned peers can tune in");
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


# GPU fallback list: keep the uk latency pin but schedule on whatever's available there — uk+L4
# alone can hit capacity waits, which would leave an interviewer staring at "waiting for GPU".
SESSION_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/><title>RelaySplit session (/ws)</title>
<style>body{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}
button{font-size:15px;padding:8px 16px;cursor:pointer}.b{font-family:ui-monospace,monospace;background:#eee;padding:2px 8px;border-radius:4px}
.ok{background:#d4f7d4}.bad{background:#f7d4d4}#log{white-space:pre-wrap;background:#111;color:#6f6;padding:10px;border-radius:6px;margin-top:12px;font:12px ui-monospace,monospace;min-height:80px}</style>
</head><body>
<h1>RelaySplit — session via the VPS /ws control plane</h1>
<p>Signalling goes through the VPS (not the container directly), the brief's end-state. Pick a file,
   Start, hear the isolated vocal. <b>Headphones.</b></p>
<div><input type="file" id="file" accept="audio/*"/> <label><input type="checkbox" id="mic"/> use mic</label></div>
<div style="margin-top:10px"><button id="start">Start</button>
 &nbsp; conn <span id="conn" class="b">—</span> &nbsp; RTT <span id="rtt" class="b">— ms</span> &nbsp; inference <span id="inf" class="b">— ms</span></div>
<audio id="out" autoplay></audio><div id="log"></div>
<script>
const $=id=>document.getElementById(id),log=m=>{$("log").textContent+=m+"\n";console.log(m)};
const VPS="wss://relaysplit.vaguelystrange.com/ws";
let ws,pc,modalId;
$("start").onclick=async()=>{
 $("start").disabled=true;
 try{
  const {iceServers}=await (await fetch("ice")).json();
  pc=new RTCPeerConnection({iceServers});
  pc.onconnectionstatechange=()=>{const s=pc.connectionState;$("conn").textContent=s;$("conn").className="b "+(s=="connected"?"ok":(s=="failed"?"bad":""));if(s=="connected")poll()};
  pc.ontrack=e=>{$("out").srcObject=e.streams[0]};
  const dc=pc.createDataChannel("stats");
  dc.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.infer_ms!=null)$("inf").textContent=d.infer_ms.toFixed(0)+" ms"}catch{}};
  let src;
  if($("mic").checked){src=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});}
  else{const f=$("file").files[0];if(!f){log("pick a file or tick mic");$("start").disabled=false;return}
   const a=new (window.AudioContext||window.webkitAudioContext)();const el=new Audio(URL.createObjectURL(f));el.loop=true;
   const d=a.createMediaStreamDestination();a.createMediaElementSource(el).connect(d);await a.resume();await el.play();src=d.stream;}
  src.getAudioTracks().forEach(t=>pc.addTrack(t,src));
  ws=new WebSocket(VPS);
  ws.onmessage=async ev=>{
   const m=JSON.parse(ev.data);
   if(m.type=="welcome"){ws.send(JSON.stringify({type:"join",room:"gpu-lobby",role:"sender"}))}
   else if(m.type=="joined"){const mp=(m.peers||[]).find(p=>p.role=="modal");if(mp){modalId=mp.id;await sendOffer()}else log("no modal peer in room yet")}
   else if(m.type=="peer-joined"&&m.role=="modal"&&!modalId){modalId=m.peerId;await sendOffer()}
   else if(m.type=="signal"&&m.data&&m.data.type=="answer"){await pc.setRemoteDescription(m.data);log("answer applied — negotiating")}
  };
  ws.onerror=e=>log("ws error");
 }catch(err){log("ERROR "+err);$("start").disabled=false}
};
async function sendOffer(){
 await pc.setLocalDescription(await pc.createOffer());
 await new Promise(r=>{if(pc.iceGatheringState=="complete")return r();const c=()=>{if(pc.iceGatheringState=="complete"){pc.removeEventListener("icegatheringstatechange",c);r()}};pc.addEventListener("icegatheringstatechange",c);setTimeout(r,4000)});
 ws.send(JSON.stringify({type:"signal",to:modalId,data:{type:"offer",sdp:pc.localDescription.sdp}}));
 log("offer sent to modal peer via /ws");
}
function poll(){setInterval(async()=>{const s=await pc.getStats();s.forEach(r=>{if(r.type=="candidate-pair"&&r.nominated&&r.currentRoundTripTime!=null)$("rtt").textContent=(r.currentRoundTripTime*1000).toFixed(0)+" ms"})},1000)}
</script></body></html>
"""


# Receiver page: tune in to a peer's live separated broadcast (the DATA-plane other half of sharing).
# Pure downlink — no mic/file, no uplink audio. ?channel=<id> says which broadcast to receive.
LISTEN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RelaySplit — tune in</title>
<style>
  body{font:15px/1.5 system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 16px}
  h1{font-size:20px} button{font-size:15px;padding:8px 16px;cursor:pointer}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0}
  .badge{padding:2px 10px;border-radius:4px;background:#eee;font-family:ui-monospace,monospace}
  .ok{background:#d4f7d4}.bad{background:#f7d4d4}.warn{background:#f7efd4}
  .meter{font-size:22px;font-family:ui-monospace,monospace}
  #log{white-space:pre-wrap;background:#111;color:#6f6;padding:10px;border-radius:6px;margin-top:12px;font:12px ui-monospace,monospace;min-height:80px}
</style></head><body>
<h1>RelaySplit — tune in to a live broadcast</h1>
<p>Receiving a peer's <b>separated vocal</b> straight from the UK GPU. <b>Headphones recommended.</b></p>
<div class="row">
  channel <span id="ch" class="badge">—</span>
  <button id="start">Tune in</button><button id="stop" disabled>Stop</button>
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
const CHANNEL=new URLSearchParams(location.search).get("channel")||"";
$("ch").textContent=CHANNEL||"(none)";
let pc, statsTimer;
async function start(){
  if(!CHANNEL){log("no ?channel= in the URL");return}
  $("start").disabled=true;
  try{
    const {iceServers}=await (await fetch("ice")).json();
    pc=new RTCPeerConnection({iceServers});
    pc.onconnectionstatechange=()=>{const s=pc.connectionState;setb($("conn"),s,s=="connected"?"ok":(s=="failed"?"bad":"warn"));log("conn "+s);if(s=="connected")poll()};
    pc.ontrack=e=>{log("remote track: "+e.track.kind);$("out").srcObject=e.streams[0]};
    const dc=pc.createDataChannel("stats");
    dc.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.infer_ms!=null)setb($("inf"),d.infer_ms.toFixed(0)+" ms","ok")}catch{}};
    pc.addTransceiver("audio",{direction:"recvonly"});  // downlink only — we send no audio
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r=>{if(pc.iceGatheringState=="complete")return r();const c=()=>{if(pc.iceGatheringState=="complete"){pc.removeEventListener("icegatheringstatechange",c);r()}};pc.addEventListener("icegatheringstatechange",c);setTimeout(r,4000)});
    const ans=await (await fetch("subscribe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel:CHANNEL,sdp:pc.localDescription.sdp,type:pc.localDescription.type})})).json();
    if(ans.error){log("ERROR "+ans.error);$("start").disabled=false;return}
    await pc.setRemoteDescription(ans);
    log("subscribed to channel "+CHANNEL);$("stop").disabled=false;
  }catch(err){log("ERROR "+err);setb($("conn"),"error","bad");$("start").disabled=false}
}
function poll(){
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
function stop(){clearInterval(statsTimer);statsTimer=null;if(pc)pc.close();$("stop").disabled=true;$("start").disabled=false;log("stopped")}
$("start").onclick=start;$("stop").onclick=stop;
// Embedded in the web app (iframe with allow="autoplay") -> auto-tune so the parent's Listen toggle just works.
if(new URLSearchParams(location.search).get("auto"))start();
</script></body></html>
"""


@app.function(image=image, gpu=["L4", "A10G", "L40S", "A100"], region=REGION, max_containers=1, scaledown_window=300)
@modal.concurrent(max_inputs=12)
@modal.asgi_app()
def web():
    import asyncio
    import json
    import logging
    import time
    import urllib.request
    import uuid
    from fractions import Fraction

    import av
    import numpy as np
    import torch
    import websockets
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("relaysplit-live")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        # Headroom matters: the chunk hop is 250 ms, so inference must stay well under that or the
        # real-time stream starves and the audio turns jumpy. TF32 + fp16 autocast roughly halve the
        # conv cost on the L4/Ada (and A10G/L40S/A100) with no audible quality change; cudnn.benchmark
        # picks the fastest kernels for our (now steady) segment size.
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
    model = get_model(MODEL_NAME).to(device).eval()
    sources = list(model.sources)
    voc_idx = sources.index("vocals")
    log.info("model loaded on %s; sources=%s", device, sources)

    # All inference goes through ONE worker thread: a single GPU can't truly run two separations at
    # once, so serialising avoids thrash when several broadcasts are live (each would otherwise launch
    # its own concurrent apply_model). It also keeps GPU work off the event loop entirely.
    from concurrent.futures import ThreadPoolExecutor
    gpu_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="demucs")

    def sep_voc(seg_np):
        seg = torch.from_numpy(np.ascontiguousarray(seg_np))
        ref = seg.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        x = (seg - mean) / std
        with torch.no_grad():
            if device == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    out = apply_model(model, x[None].to(device), split=False, device=device)[0]
            else:
                out = apply_model(model, x[None].to(device), split=False, device=device)[0]
        return (out.float() * std + mean)[voc_idx].cpu().numpy()

    # Adaptive hop: size the chunk to whatever GPU we landed on. The live per-chunk cost is
    # forward + ~OVERHEAD (aiortc SRTP/RTP + numpy on the event loop, GPU-independent); keep the hop a
    # safe margin above that so the stream never falls behind. Faster GPU -> smaller hop -> lower latency,
    # automatically, while the GPU fallback list stays intact (no "waiting for GPU" risk).
    chunk_s = CHUNK_S
    if device == "cuda":
        try:
            warm = np.zeros((2, int((CONTEXT_S + 0.15) * MODEL_SR)), dtype="float32")  # ~live segment shape
            for _ in range(4):  # warm cudnn.benchmark kernels for this shape
                sep_voc(warm)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(3):
                sep_voc(warm)  # full per-chunk cost INCLUDING the .cpu()/numpy postproc
            torch.cuda.synchronize()
            per_chunk_ms = (time.perf_counter() - t0) * 1000 / 3
            # hop = ~30% above the measured per-chunk cost, so the stream never falls behind real time
            chunk_s = min(0.25, max(0.10, round(per_chunk_ms * 1.3 / 1000, 2)))
            # one-time breakdown (forward-only vs full) to see where the per-chunk time goes
            wt = torch.from_numpy(warm)[None].to(device)
            ref = wt[0].mean(0); m, s = ref.mean(), ref.std() + 1e-8
            t1 = time.perf_counter()
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
                _ = apply_model(model, (wt[0] - m) / s, split=False, device=device)
            torch.cuda.synchronize()
            fwd_only = (time.perf_counter() - t1) * 1000
            log.info("warmup: per-chunk=%.0f ms (forward_only=%.0f ms) -> adaptive hop=%.0f ms",
                     per_chunk_ms, fwd_only, chunk_s * 1000)
        except Exception as e:
            log.warning("gpu warmup/measure failed: %s", e)
    jitter_s = max(0.06, round(0.6 * chunk_s, 2))  # cushion < 1 hop: covers timing jitter, less latency
    HOP_MS = int(round(chunk_s * 1000))

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

    # --- The fan-out HUB: one Broadcast per channel, many FanoutTrack readers --------------------
    # This is what turns peer ASSIGNMENT (control plane: the shares table) into actual audio
    # DELIVERY (data plane). A sender feeds ONE Broadcast (a single OnlineSeparator writing to a
    # shared, trimmed timeline); the sender's own monitor AND every assigned receiver each attach a
    # FanoutTrack that reads that timeline independently. aiortc tracks are single-consumer, so the
    # split (one producer / N independent reader cursors) is what lets the separated stream fan out.
    broadcasts = {}  # channel key -> Broadcast

    class Broadcast:
        MAXKEEP = WEBRTC_SR * 2  # keep ~2 s of separated history for slow/late-joining receivers

        def __init__(self, key):
            self.key = key
            self.sep = OnlineSeparator(sep_voc, MODEL_SR, chunk_s, CONTEXT_S, FADE_S)  # adaptive hop
            self.r_in = av.AudioResampler(format="fltp", layout="stereo", rate=MODEL_SR)
            self.r_out = av.AudioResampler(format="fltp", layout="stereo", rate=WEBRTC_SR)
            self.lock = asyncio.Lock()
            self.buf = np.zeros((2, 0), dtype="float32")  # shared separated output @ 48 kHz
            self.base = 0          # absolute sample index of buf[:, 0]
            self.write = 0         # absolute sample index just past buf's end
            self.infer_ms = 0.0
            self.subscribers = 0   # FanoutTracks currently attached
            self.source = None     # the sender's inbound track (None between senders)
            self.box = {}          # sender's stats data channel holder
            self._opts = 0
            self.task = asyncio.ensure_future(self._consume())

        def set_source(self, track, box):
            # A (re)connecting sender takes over the broadcast; the separator state simply continues.
            self.source = track
            self.box = box

        async def _consume(self):
            loop = asyncio.get_event_loop()
            idle_since = None
            try:
                while True:
                    src = self.source
                    if src is None:
                        # Reap a broadcast with no source AND nobody listening (keeps the dict clean).
                        if self.subscribers == 0:
                            idle_since = idle_since or time.time()
                            if time.time() - idle_since > 30:
                                break
                        else:
                            idle_since = None
                        await asyncio.sleep(0.1)
                        continue
                    idle_since = None
                    try:
                        frame = await src.recv()
                    except Exception:
                        if self.source is src:  # sender went away; keep the broadcast alive for receivers
                            self.source = None
                        continue
                    for rf in self.r_in.resample(frame):
                        self.sep.push(rf.to_ndarray())  # (2, n) float32 @ 44.1k
                    t0 = time.perf_counter()
                    blocks = await loop.run_in_executor(gpu_exec, self.sep.pop)  # serialised GPU, off the loop
                    if blocks:
                        self.infer_ms = (time.perf_counter() - t0) * 1000 / len(blocks)
                        ch = self.box.get("dc")
                        if ch and ch.readyState == "open":
                            try:
                                ch.send(json.dumps({"infer_ms": round(self.infer_ms, 1), "hop_ms": HOP_MS}))
                            except Exception:
                                pass
                    for blk in blocks:
                        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(blk), format="fltp", layout="stereo")
                        af.sample_rate = MODEL_SR
                        af.pts = self._opts
                        af.time_base = Fraction(1, MODEL_SR)
                        self._opts += blk.shape[1]
                        for of in self.r_out.resample(af):
                            arr = of.to_ndarray()  # (2, m) @ 48k
                            async with self.lock:
                                self.buf = np.concatenate([self.buf, arr], axis=1)
                                self.write += arr.shape[1]
                                if self.buf.shape[1] > self.MAXKEEP:  # trim history, advance base
                                    drop = self.buf.shape[1] - self.MAXKEEP
                                    self.buf = self.buf[:, drop:]
                                    self.base += drop
            except Exception as e:
                log.info("broadcast %s consume error: %s", self.key, e)
            finally:
                if broadcasts.get(self.key) is self:
                    del broadcasts[self.key]
                log.info("broadcast %s closed", self.key)

    def get_broadcast(key):
        bc = broadcasts.get(key)
        if bc is None:
            bc = Broadcast(key)
            broadcasts[key] = bc
        return bc

    class FanoutTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self, bc):
            super().__init__()
            self.bc = bc
            bc.subscribers += 1
            self._stopped = False
            self.cursor = None  # absolute read index into the broadcast timeline; locks on at 1st audio
            self._start = None
            self._ts = 0

        def stop(self):
            if not self._stopped:
                self._stopped = True
                self.bc.subscribers -= 1
            super().stop()

        async def recv(self):
            n = WEBRTC_SR // 50  # 20 ms
            if self._start is None:
                self._start = time.time()
                self._ts = 0
            self._ts += n
            wait = self._start + self._ts / WEBRTC_SR - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            out = None
            JITTER = int(jitter_s * WEBRTC_SR)  # play ~1 hop behind live so bursty per-chunk production
            async with self.bc.lock:                                # can't starve the steady 20 ms reads
                w, b = self.bc.write, self.bc.base
                if self.cursor is None and (w - b) >= JITTER:
                    self.cursor = w - JITTER  # lock on once a cushion exists (not at the bare live edge)
                if self.cursor is not None:
                    if self.cursor < b:
                        self.cursor = b  # we fell off the trimmed history — jump to oldest kept
                    if w - self.cursor > WEBRTC_SR // 2:
                        self.cursor = max(b, w - JITTER)  # >0.5 s behind: resync, keeping the cushion
                    if w - self.cursor >= n:
                        off = self.cursor - b
                        out = self.bc.buf[:, off:off + n].copy()
                        self.cursor += n
            if out is None:
                out = np.zeros((2, n), dtype="float32")  # warming up / starved: silence, hold cursor
            s16 = (np.clip(out, -1, 1) * 32767).astype("int16")
            frame = av.AudioFrame.from_ndarray(s16.T.reshape(1, -1), format="s16", layout="stereo")
            frame.sample_rate = WEBRTC_SR
            frame.pts = self._ts - n
            frame.time_base = Fraction(1, WEBRTC_SR)
            return frame

    api = FastAPI()
    pcs = set()

    async def stats_pinger(pc, bc, box):
        # Receivers don't share the sender's stats data channel, so report the broadcast's current
        # inference time on the receiver's own channel once a second (drives its latency meter).
        try:
            while pc.connectionState in ("new", "connecting", "connected"):
                ch = box.get("dc")
                if ch and ch.readyState == "open":
                    try:
                        ch.send(json.dumps({"infer_ms": round(bc.infer_ms, 1), "hop_ms": HOP_MS}))
                    except Exception:
                        pass
                await asyncio.sleep(1)
        except Exception:
            pass

    @api.get("/", response_class=HTMLResponse)
    async def index():
        return INDEX_HTML

    @api.get("/demo", response_class=HTMLResponse)
    async def demo():
        # Same page, flagged to auto-use the baked track as source — one-click live demo for an
        # interviewer (no mic/file needed). Autoplay policy still requires the single Start click.
        return INDEX_HTML.replace("const $=", "window.__demo=true;\nconst $=", 1)

    @api.get("/demo-track")
    async def demo_track():
        return FileResponse(DEMO_TRACK, media_type="audio/ogg")

    @api.get("/session", response_class=HTMLResponse)
    async def session_page():
        # Sender that signals through the VPS /ws control plane (vs the self-contained /offer path).
        return SESSION_HTML

    @api.get("/ice")
    async def ice():
        return JSONResponse({"iceServers": get_ice()})

    @api.post("/offer")
    async def offer(request: Request):
        # Sender path. With a `channel`, the sender feeds that named broadcast (which assigned peers
        # can tune into); without one, a private per-connection broadcast (the original 1:1 demo).
        params = await request.json()
        key = str(params.get("channel") or ("solo-" + uuid.uuid4().hex))
        servers = [RTCIceServer(**s) for s in get_ice()]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))
        pcs.add(pc)
        box = {}
        bc = get_broadcast(key)

        @pc.on("datachannel")
        def on_dc(channel):
            box["dc"] = channel

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("pc(offer %s) state %s", key, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                pcs.discard(pc)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                bc.set_source(track, box)

        pc.addTrack(FanoutTrack(bc))  # send the separated stream back as the sender's local monitor
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "channel": key})

    @api.post("/subscribe")
    async def subscribe(request: Request):
        # Receiver path (Modal-direct): no uplink audio — the receiver just attaches a FanoutTrack to
        # the requested broadcast. The broadcast is created on demand so a receiver can tune in before
        # the sender starts (it hears silence, then the separated vocal the instant the sender goes live).
        params = await request.json()
        key = str(params.get("channel") or "")
        if not key:
            return JSONResponse({"error": "channel required"}, status_code=400)
        servers = [RTCIceServer(**s) for s in get_ice()]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))
        pcs.add(pc)
        bc = get_broadcast(key)
        box = {}

        @pc.on("datachannel")
        def on_dc(channel):
            box["dc"] = channel
            asyncio.ensure_future(stats_pinger(pc, bc, box))

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("pc(subscribe %s) state %s", key, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                pcs.discard(pc)

        pc.addTrack(FanoutTrack(bc))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "channel": key})

    @api.get("/live")
    async def live():
        # What's on air right now — a sender feeding it (live) and/or receivers attached.
        return JSONResponse({"broadcasts": [
            {"channel": k, "live": b.source is not None, "subscribers": b.subscribers}
            for k, b in broadcasts.items()
        ]})

    @api.get("/listen", response_class=HTMLResponse)
    async def listen_page():
        return LISTEN_HTML

    # --- VPS /ws session peer (brief's end-state: signalling via the control plane) ----------
    # The container also joins the VPS /ws as a "modal" peer in a lobby room. A sender that joins the
    # same room can negotiate via relayed signalling instead of the self-contained /offer above. This
    # runs ALONGSIDE /offer (which keeps working), so it can't break the live demo.
    VPS_WS = "wss://relaysplit.vaguelystrange.com/ws"
    ROOM = "gpu-lobby"

    async def handle_ws_offer(ws, sender_id, data):
        # Same broadcast model as /offer + /subscribe, but signalled through the VPS. The offer's data
        # carries `channel` (broadcast key) and `recv` (true = tune in only, false/absent = broadcast).
        key = str(data.get("channel") or ("solo-" + uuid.uuid4().hex))
        recv = bool(data.get("recv"))
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**s) for s in get_ice()]))
        pcs.add(pc)
        box = {}
        bc = get_broadcast(key)

        @pc.on("datachannel")
        def _dc(channel):
            box["dc"] = channel
            if recv:
                asyncio.ensure_future(stats_pinger(pc, bc, box))

        @pc.on("connectionstatechange")
        async def _st():
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                pcs.discard(pc)

        if not recv:
            @pc.on("track")
            def _tr(track):
                if track.kind == "audio":
                    bc.set_source(track, box)

        pc.addTrack(FanoutTrack(bc))  # sender monitor or receiver downlink — same separated timeline
        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        await ws.send(json.dumps({"type": "signal", "to": sender_id,
                                  "data": {"type": "answer", "sdp": pc.localDescription.sdp}}))

    async def ws_session_loop():
        while True:
            try:
                async with websockets.connect(VPS_WS) as ws:
                    await ws.send(json.dumps({"type": "join", "room": ROOM, "role": "modal", "name": "modal-gpu"}))
                    log.info("joined VPS /ws room '%s' as modal peer", ROOM)
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "signal" and (msg.get("data") or {}).get("type") == "offer":
                            await handle_ws_offer(ws, msg["from"], msg["data"])
            except Exception as e:
                log.warning("ws session loop reconnecting: %s", e)
                await asyncio.sleep(3)

    @api.on_event("startup")
    async def _on_startup():
        asyncio.ensure_future(ws_session_loop())

    return api
