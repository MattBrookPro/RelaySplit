"""
RelaySplit automated end-to-end test client.

Proves the LIVE container actually separates audio over WebRTC — no browser, no human ears. An
aiortc client (region uk) sends a baked public-domain track to the live container's /offer, records
the returned separated vocal, and reports the connection state + audio energy. Doubles as a
CI-able regression test for the whole data plane (WebRTC + TURN + streaming Demucs).

    modal run gpu/relaysplit_test_client.py
    modal run gpu/relaysplit_test_client.py --target https://blitzncs--relaysplit-live-web.modal.run --seconds 12

KNOWN LIMITATION (environmental, not a pipeline bug): when run Modal->Modal, BOTH peers sit behind
symmetric NAT, so neither's srflx works and both rely on TURN *relay*. coturn does not relay between
two allocations on its own external IP by default, so relay<->relay fails and ICE never connects.
Real clients (the browser, and the JUCE plugin from a normal network) get a reachable srflx the
container's relay can hit directly, so the live path works — proven by the working browser demo.
To make THIS test pass, run the client from a non-symmetric network, or enable relay-to-self on
coturn (allowed-peer-ip). Kept as a tool + a documented WebRTC/TURN finding.
"""
import modal

REGION = "uk"
TURN_ENDPOINT = "https://relaysplit.vaguelystrange.com/api/turn"
DEMO_TRACK = "/demo-track.ogg"


def _bake_demo():
    import urllib.request

    url = "https://commons.wikimedia.org/wiki/Special:FilePath/Bessie%20Smith%20-%20Downhearted%20Blues%20(1923).ogg"
    req = urllib.request.Request(url, headers={"User-Agent": "RelaySplit/0.1 (test)"})
    with urllib.request.urlopen(req, timeout=90) as r, open(DEMO_TRACK, "wb") as f:
        f.write(r.read())


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("aiortc==1.9.0", "av==12.3.0", "numpy<2")
    .run_function(_bake_demo)
)

app = modal.App("relaysplit-test-client", image=image)


@app.function(region=REGION, timeout=240)
async def roundtrip(target: str, seconds: float = 12.0):
    import asyncio
    import json
    import os
    import tempfile
    import urllib.request

    import av
    import numpy as np
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaPlayer, MediaRecorder

    def get_ice():
        req = urllib.request.Request(
            TURN_ENDPOINT,
            data=json.dumps({"label": "test"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["iceServers"]

    # Pre-warm the GPU container — its first request cold-loads Demucs (~15-30 s); without this the
    # /offer POST below can time out.
    try:
        urllib.request.urlopen(target.rstrip("/") + "/ice", timeout=120).read()
    except Exception:
        pass

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**s) for s in get_ice()]))
    pc.addTrack(MediaPlayer(DEMO_TRACK).audio)  # send the baked track as our outbound audio
    out_path = os.path.join(tempfile.gettempdir(), "separated.wav")
    recorder = MediaRecorder(out_path)
    state = {"conn": None}

    @pc.on("connectionstatechange")
    async def _on_state():
        state["conn"] = pc.connectionState

    @pc.on("track")
    async def _on_track(track):
        recorder.addTrack(track)  # record the separated vocal the container sends back
        await recorder.start()

    # Non-trickle: gather fully before sending the offer (the TURN-permission lesson from the spike).
    await pc.setLocalDescription(await pc.createOffer())
    for _ in range(80):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.1)

    body = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}).encode()
    req = urllib.request.Request(
        target.rstrip("/") + "/offer", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    answer = json.loads(urllib.request.urlopen(req, timeout=60).read())
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    await asyncio.sleep(seconds)
    await recorder.stop()
    await pc.close()

    data = open(out_path, "rb").read()
    # Decode the recording and measure RMS — clearly non-zero means real separated audio came back.
    frames = [f.to_ndarray().astype("float32") for f in av.open(out_path).decode(audio=0)]
    arr = np.concatenate(frames, axis=1) if frames else np.zeros((1, 1), dtype="float32")
    rms = float(np.sqrt(np.mean((arr / 32768.0) ** 2))) if arr.size else 0.0
    return {"conn": state["conn"], "bytes": len(data), "seconds": seconds, "rms": round(rms, 5), "wav": data}


@app.local_entrypoint()
def main(target: str = "https://blitzncs--relaysplit-live-web.modal.run", seconds: float = 12.0):
    res = roundtrip.remote(target, seconds)
    verdict = "PASS" if (res["conn"] == "connected" and res["rms"] > 0.001) else "FAIL"
    print(f"{verdict}  conn={res['conn']}  rms={res['rms']}  bytes={res['bytes']}")
    with open("gpu/separated_roundtrip.wav", "wb") as f:
        f.write(res["wav"])
    print("wrote gpu/separated_roundtrip.wav")
