"""
RelaySplit — Step 0 SPIKE: WebRTC audio round-trip THROUGH a Modal container.

No model. No GPU. This proves the single genuinely uncertain thing in the whole
project: an `aiortc` peer running inside a serverless Modal container can join a
WebRTC session, receive an audio track, and send a track back — with media
relayed through the VPS TURN server (the container has no public inbound, so
relay candidates are the realistic path).

Signalling is self-contained on purpose: the VPS Node signalling server isn't
running yet (nginx returns 502 on :8080), so this container serves its own test
page and does the SDP offer/answer over plain HTTP. When the real signalling
server exists, the only change is *where* the SDP/ICE exchange happens.

    Deploy:  modal deploy spike/relaysplit_spike.py
    Dev:     modal serve  spike/relaysplit_spike.py    # hot-reload

Then open the printed *.modal.run URL in Chrome/Edge, allow the microphone, and
listen for your own voice echoed back through Modal. USE HEADPHONES (echo
cancellation is disabled so the round-trip is audible; speakers would feed back).

The page shows the ICE connection state and the selected candidate pair type
(host / srflx / relay) so you can see *how* the media connected, not just that
it did.
"""

import os
import modal

# Confirmed in docs/setup-machine2.md — UK<->UK is the latency-critical path.
REGION = "uk"

image = (
    modal.Image.debian_slim(python_version="3.12")
    # PyAV (av) wheels bundle FFmpeg — no system ffmpeg/apt needed.
    .pip_install(
        "aiortc==1.9.0",
        "av==12.3.0",
        "fastapi[standard]==0.115.4",
    )
)

app = modal.App("relaysplit-spike")


# The browser test page. Plain JS, no build step. Served from "/".
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>RelaySplit spike — WebRTC ↔ Modal</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; }
  h1 { font-size: 20px; }
  button { font-size: 15px; padding: 8px 16px; cursor: pointer; }
  #log { white-space: pre-wrap; background: #111; color: #6f6; padding: 12px; border-radius: 6px;
         margin-top: 16px; min-height: 120px; font-family: ui-monospace, monospace; font-size: 13px; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .badge { padding: 2px 8px; border-radius: 4px; background: #eee; font-family: ui-monospace, monospace; }
  .ok { background: #d4f7d4; } .bad { background: #f7d4d4; } .warn { background: #f7efd4; }
</style>
</head>
<body>
<h1>RelaySplit spike — audio round-trip through Modal</h1>
<p>Click <b>Start</b>, allow the mic, and speak. You should hear yourself echoed back
   through the Modal container. <b>Wear headphones.</b></p>
<div class="row">
  <button id="start">Start</button>
  <button id="stop" disabled>Stop</button>
  <span>ICE: <span id="ice" class="badge">—</span></span>
  <span>Conn: <span id="conn" class="badge">—</span></span>
  <span>Path: <span id="path" class="badge">—</span></span>
</div>
<audio id="remote" autoplay></audio>
<div id="log"></div>
<script>
const $ = (id) => document.getElementById(id);
const log = (m) => { $("log").textContent += m + "\n"; console.log(m); };
function setBadge(el, text, cls) { el.textContent = text; el.className = "badge " + (cls || ""); }

// Non-trickle signalling: wait for ICE gathering to finish so the offer SDP
// carries our candidates. Without this the remote peer never learns our address
// and (over TURN) never installs a relay permission for us — ICE then fails.
function waitForIceGathering(pc, timeoutMs) {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    let done = false;
    const finish = () => { if (done) return; done = true; pc.removeEventListener("icegatheringstatechange", onChange); resolve(); };
    const onChange = () => { if (pc.iceGatheringState === "complete") finish(); };
    pc.addEventListener("icegatheringstatechange", onChange);
    setTimeout(finish, timeoutMs);  // fallback so a stalled ICE server can't hang us
  });
}

let pc, localStream, statsTimer;

async function start() {
  $("start").disabled = true;
  try {
    const iceResp = await fetch("ice");
    const { iceServers } = await iceResp.json();
    log("ICE servers: " + iceServers.map(s => s.urls).join(", "));

    pc = new RTCPeerConnection({ iceServers });

    pc.oniceconnectionstatechange = () => {
      const s = pc.iceConnectionState;
      setBadge($("ice"), s, (s === "connected" || s === "completed") ? "ok" : (s === "failed" ? "bad" : "warn"));
      log("iceConnectionState: " + s);
    };
    pc.onconnectionstatechange = () => {
      const s = pc.connectionState;
      setBadge($("conn"), s, s === "connected" ? "ok" : (s === "failed" ? "bad" : "warn"));
      log("connectionState: " + s);
      if (s === "connected") startStatsPolling();
    };
    pc.ontrack = (e) => {
      log("remote track: " + e.track.kind);
      $("remote").srcObject = e.streams[0];
    };

    // Disable processing so the echo is clearly audible (needs headphones).
    localStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
    });
    localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
    log("mic captured, " + localStream.getAudioTracks().length + " audio track(s)");

    await pc.setLocalDescription(await pc.createOffer());
    log("gathering ICE candidates...");
    await waitForIceGathering(pc, 4000);
    const candCount = (pc.localDescription.sdp.match(/a=candidate/g) || []).length;
    log("offer ready (" + candCount + " candidates), posting...");
    const answerResp = await fetch("offer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
    });
    const answer = await answerResp.json();
    await pc.setRemoteDescription(answer);
    log("answer applied — negotiating ICE...");
    $("stop").disabled = false;
  } catch (err) {
    log("ERROR: " + err);
    setBadge($("conn"), "error", "bad");
    $("start").disabled = false;
  }
}

async function startStatsPolling() {
  if (statsTimer) return;
  statsTimer = setInterval(async () => {
    const stats = await pc.getStats();
    let pair, cands = {};
    stats.forEach(r => {
      if (r.type === "candidate-pair" && r.nominated && r.state === "succeeded") pair = r;
      if (r.type === "local-candidate" || r.type === "remote-candidate") cands[r.id] = r;
    });
    if (pair) {
      const l = cands[pair.localCandidateId], rc = cands[pair.remoteCandidateId];
      const desc = (l ? l.candidateType : "?") + " ↔ " + (rc ? rc.candidateType : "?");
      const isRelay = (l && l.candidateType === "relay") || (rc && rc.candidateType === "relay");
      setBadge($("path"), desc, isRelay ? "warn" : "ok");
    }
  }, 1000);
}

function stop() {
  clearInterval(statsTimer); statsTimer = null;
  if (pc) pc.close();
  if (localStream) localStream.getTracks().forEach(t => t.stop());
  $("stop").disabled = true; $("start").disabled = false;
  log("stopped");
}

$("start").onclick = start;
$("stop").onclick = stop;
</script>
</body>
</html>
"""


@app.function(
    image=image,
    region=REGION,
    max_containers=1,          # single sticky container holds the peer connections
    scaledown_window=300,      # stay warm 5 min after last request (interactive testing)
    secrets=[modal.Secret.from_name("relaysplit-turn-spike")],
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    import logging

    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from aiortc import (
        RTCConfiguration,
        RTCIceServer,
        RTCPeerConnection,
        RTCSessionDescription,
    )
    from aiortc.contrib.media import MediaRelay

    logging.basicConfig(level=logging.INFO)
    api = FastAPI()
    pcs = set()
    relay = MediaRelay()

    def ice_servers():
        host = os.environ["TURN_HOST"]
        user = os.environ["TURN_USERNAME"]
        cred = os.environ["TURN_CREDENTIAL"]
        return [
            {"urls": ["stun:stun.l.google.com:19302"]},
            {"urls": [f"turn:{host}:3478?transport=udp"], "username": user, "credential": cred},
            {"urls": [f"turns:{host}:5349?transport=tcp"], "username": user, "credential": cred},
        ]

    @api.get("/", response_class=HTMLResponse)
    async def index():
        return INDEX_HTML

    @api.get("/ice")
    async def ice():
        # Browser fetches its ICE config here (mirrors the real signalling server's role).
        return JSONResponse({"iceServers": ice_servers()})

    @api.post("/offer")
    async def offer(request: Request):
        params = await request.json()
        desc = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        servers = [RTCIceServer(**s) for s in ice_servers()]
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))
        pcs.add(pc)
        logging.info("new peer; total=%d", len(pcs))

        @pc.on("connectionstatechange")
        async def on_state():
            logging.info("connectionState=%s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                pcs.discard(pc)

        @pc.on("track")
        def on_track(track):
            logging.info("track received: %s", track.kind)
            if track.kind == "audio":
                # Passthrough echo: send the inbound audio straight back.
                # (This is where the causal separation model goes in the full build.)
                pc.addTrack(relay.subscribe(track))

            @track.on("ended")
            async def on_ended():
                logging.info("track ended: %s", track.kind)

        await pc.setRemoteDescription(desc)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return JSONResponse(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        )

    return api
