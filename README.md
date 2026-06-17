# RelaySplit

Real-time neural vocal separation streamed over WebRTC to a region-pinned cloud GPU.

RelaySplit captures audio in a browser or a DAW plugin, sends it over WebRTC to a warm GPU
container, isolates the vocal from the mix in real time, and streams the result back. A
separate control plane handles signalling, authentication, peer sharing, and TURN relaying,
so audio never passes through the control server.

## How it works

RelaySplit is split into a control plane and a data plane.

Control plane (VPS, Node and TypeScript):

* WebRTC signalling over WebSockets.
* Accounts, sessions, channels, and peer sharing (SQLite).
* Ephemeral TURN credentials minted per session (coturn).
* A small web app for sign in, broadcasting, and tuning into live sources.

Data plane (Modal GPU):

* A serverless GPU container runs the separation model and is itself a WebRTC peer (aiortc).
* Inbound audio is resampled, separated in a sliding window, and streamed back.
* One sender feeds a broadcast and any number of receivers tune into it (a fan-out hub).
* The container also joins the control plane over WebSocket as a session peer.

Client:

* The web app connects to the GPU directly for the lowest latency.
* A JUCE plugin (VST3 and Standalone) is a native WebRTC client with broadcast and receive
  modes and session-aware peer assignment. Audio crosses the network boundary on a lock-free
  FIFO so the audio callback never blocks, allocates, or locks.

## Separation model

The GPU runs SCNet-small, a frequency-domain separation network, in half precision. It was
chosen over Hybrid Transformer Demucs after an A/B test: it is roughly 2.5 times faster per
forward pass and produces cleaner vocals. The streaming wrapper feeds a fixed-length window so
the GPU keeps a single warmed kernel, and the chunk size adapts at startup to the GPU the
container lands on.

## Latency

End-to-end latency is around 0.2 seconds on a UK-to-UK path. The budget is the sum of the
network round trip, the chunk the separator must buffer before it can run (the algorithmic
floor), GPU inference, and a small receiver jitter buffer. Inference runs in the tens of
milliseconds, so the chunk size is the dominant term. Pushing below this floor would require a
causal model.

## Repository layout

```
server/   control plane: signalling, accounts, sharing, ephemeral TURN, web app
gpu/      Modal GPU container, the streaming separator, and benchmarks
plugin/   JUCE plugin (VST3 and Standalone), a native WebRTC client
spike/    the initial proof that WebRTC audio can round-trip through a GPU container
```

Each directory has its own README with build and run instructions.

## Quick start

Control server (Node 20):

```
cd server
npm install
npm run dev
```

GPU container (Modal):

```
modal deploy gpu/relaysplit_live.py
```

Plugin (Windows, CMake and MSVC, vcpkg providing libdatachannel and Opus). See
`plugin/PHASE2.md` for the full build and test notes:

```
cmake -S plugin -B plugin/build-static -G "Visual Studio 18 2026" -A x64 -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows-static-md
cmake --build plugin/build-static --config Release
```

## Tech stack

* WebRTC: aiortc (Python), libdatachannel (C++), Opus, coturn.
* GPU: Modal, PyTorch, SCNet.
* Control plane: Node, TypeScript, Express, ws, SQLite.
* Plugin: JUCE, CMake, vcpkg.

## License

MIT. See [LICENSE](LICENSE).
