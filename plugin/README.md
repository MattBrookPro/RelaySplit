# RelaySplit JUCE plugin

A native (C++/JUCE) WebRTC client to the Modal GPU separator: it sits on a track, ships the audio
to the warm UK GPU over WebRTC, and plays back the separated vocal — with a live latency meter.

## Phased build

- **Phase 1 (now):** a plugin that **builds** (VST3 + Standalone) with the real-time architecture
  and latency-meter UI scaffolded. This is the setup guide's "confirm a hello plugin builds" gate —
  de-risk JUCE + CMake + MSVC before the hard part. `processBlock` is passthrough for now.
- **Phase 2:** the WebRTC client. Plan: **libdatachannel** (lightweight C++ WebRTC — ICE/DTLS/SRTP)
  + **Opus** encode/decode. The audio thread only moves samples through two lock-free FIFOs; a
  worker thread does Opus + RTP + signalling (POST the offer to the container `/offer`, fetch ICE
  from the VPS `/api/turn`, exactly like the browser client). The Connect button starts the session.

## Build (Windows, MSVC)

CMake + the VS 2026 toolset are required (bundled with Visual Studio Build Tools). From `plugin/`:

```powershell
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
& $cmake -S . -B build -G "Visual Studio 18 2026" -A x64      # fetches JUCE on first run
& $cmake --build build --config Release --target RelaySplit_Standalone
```

The Standalone target runs as an app (no DAW needed) for quick testing; the VST3 loads in a DAW.

## Real-time discipline (the thing interviewers probe)

The audio callback (`processBlock`) never blocks, allocates, or locks. All networking, Opus, and
WebRTC happen off the audio thread; samples cross the boundary through lock-free FIFOs, and the
latency figures cross back as plain atomics. This is the heart of audio-plugin engineering.
