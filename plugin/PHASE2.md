# Plugin Phase 2 — the native WebRTC client (implementation + build plan)

Phase 1 (the building plugin foundation + real-time architecture + latency-meter UI) is done.
Phase 2 makes the plugin a **native WebRTC peer** to the Modal container — the same flow the
browser client already uses successfully, just in C++. This is the one remaining piece, and it is
**DAW-gated** (audio can only be verified by loading it in a DAW / running the Standalone and
listening), so it's documented here precisely rather than half-built.

## Architecture (mirrors the working browser client)

```
 audio thread (processBlock)            worker thread (WebRtcClient)
 ──────────────────────────            ───────────────────────────────────────────────
 input  → [lock-free FIFO] ──────────▶ Opus encode → libdatachannel track.send (RTP)
 output ← [lock-free FIFO] ◀────────── Opus decode ← track.onMessage (RTP)         │
   (only copies + meters;                signalling: GET /ice, POST /offer (JUCE URL)│
    never blocks/allocs/locks)           PeerConnection: ICE/DTLS/SRTP via TURN ─────┘
```

- **Lock-free handoff:** `juce::AbstractFifo` (two: to-network, from-network), `float` stereo.
  `processBlock` only writes input / reads output / updates the atomic meters. Everything else is
  off-thread. This is the audio-engineering showcase (no lock/alloc/socket on the callback).
- **Signalling = identical to the browser:** `GET <container>/ice` for the ICE servers (the
  container proxies the VPS `/api/turn`), then `POST <container>/offer` with the local SDP, apply
  the answer. Use `juce::URL` (works on Windows via WinINet; `JUCE_USE_CURL=0` is fine).
- **Media:** Opus @ 48 kHz/2ch to match the aiortc container (payload type 111). libdatachannel's
  `rtc::Track` + `OpusRtpPacketizer` to send; `RtcpReceivingSession` + `track->onMessage` to
  receive RTP, then Opus-decode.
- **Connect → warm → ready:** the Connect button starts the session; show `connecting` until the
  first separated audio arrives, then `connected` + the live RTT/inference meter (RTT from the
  PeerConnection stats; inference from a data channel, as the browser does).

## Build blocker found (and how to clear it)

Adding libdatachannel + Opus via CMake `FetchContent` got as far as fetching everything (incl.
submodules), then failed at configure:

```
Could NOT find MbedTLS ... (libdatachannel/deps/libsrtp/CMakeLists.txt: find_package(MbedTLS))
```

i.e. with `USE_MBEDTLS=ON`, libdatachannel builds mbedTLS as a subproject, but its vendored
**libsrtp does its own `find_package(MbedTLS)`** which fails (no installed mbedTLS to find).
(Also: CMake 4.2 needs `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` for the old bundled `plog`.)

Resolution options, easiest first:
1. **OpenSSL via vcpkg** (recommended): `vcpkg install openssl libdatachannel opus`, then configure
   with `-DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake` and link the vcpkg
   targets. Avoids the bundled-mbedTLS/libsrtp clash entirely.
2. **Prebuilt libdatachannel**: download a Windows release and link it (skip building deps).
3. **Fix the bundled build**: point libsrtp at the built mbedTLS (set `MbedTLS_*` cache vars /
   `CMAKE_PREFIX_PATH`), or disable libsrtp's own TLS detection and pass libdatachannel's.

Once libdatachannel + Opus link, re-add them to `CMakeLists.txt` (see the commented block there)
and build `RelaySplit_Standalone`.

## Implementation checklist

- [ ] `src/Fifo.h` — `juce::AbstractFifo`-backed lock-free stereo float FIFO.
- [ ] `src/WebRtcClient.{h,cpp}` — PeerConnection lifecycle, Opus enc/dec, RTP send/recv, the
      `GET /ice` + `POST /offer` signalling (`juce::URL`), RTT + inference reporting.
- [ ] `PluginProcessor` — own a `WebRtcClient` + two FIFOs; `processBlock` does only FIFO copies +
      meter writes; `connectButton` starts/stops the client; feed `netRttMs`/`inferenceMs` atomics.
- [ ] Point the client at the live container (`https://blitzncs--relaysplit-live-web.modal.run`).

## DAW test plan

1. Build `RelaySplit_Standalone` (quickest — no DAW) or `RelaySplit_VST3`.
2. Standalone: pick an input device / play a track into it; click Connect.
3. Expect: the isolated **vocal** returns in real time; the meter shows net RTT + inference
   (target ~13 ms RTT / ~118 ms inference, matching the browser client).
4. VST3: load on a track in your DAW, play a song, Connect — same result, in-session.
