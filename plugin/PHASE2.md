# Plugin Phase 2 — native WebRTC client (BUILT)

The plugin is now a native WebRTC peer to the Modal separator: it builds as **VST3 + Standalone**,
signs in to the control plane, **assigns peers** (session-aware matrix + group edit), and runs in two
modes — **broadcast** (ship this track's audio to the GPU, monitor the separated vocal, and let
assigned peers tune in) or **receive** (tune into a peer's broadcast, downlink only). It has a latency
meter. It links the whole WebRTC stack **statically** into one self-contained binary (no runtime DLLs),
so the VST3 loads in any host. **The audio round-trip still needs a DAW/Standalone listen on your
machine** (autonomous testing can't drive real audio I/O), but it's compiled, linked, and launches clean.

## Architecture (as built)

```
 audio thread (processBlock)            WebRtcClient worker thread
 ──────────────────────────            ───────────────────────────────────────────────
 input  → [StereoFifo] ──────────────▶ Opus encode (20 ms) → libdatachannel Track.sendFrame (RTP)
 output ← [StereoFifo] ◀────────────── Opus decode ← onMessage (parse RTP) ← Track
   (only interleave/copy;               signalling: GET /ice, POST /offer (juce::URL → WinINet)
    never blocks/allocs/locks)          PeerConnection: ICE/DTLS/SRTP, TURN-relayed
```

- [`src/StereoFifo.h`](src/StereoFifo.h) — lock-free SPSC stereo float FIFO (`juce::AbstractFifo`).
- [`src/WebRtcClient.{h,cpp}`](src/WebRtcClient.cpp) — PeerConnection + Opus enc/dec + signalling,
  all on a worker thread; libdatachannel/Opus hidden behind a pImpl.
- [`src/PluginProcessor.cpp`](src/PluginProcessor.cpp) — `processBlock` only interleaves input into
  the to-net FIFO and reads separated audio from the from-net FIFO (silence while warming).

## Build (static — recommended)

Build the WebRTC stack with the **static** vcpkg triplet so nothing is loaded at runtime:

```powershell
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
C:\vcpkg\vcpkg.exe install "libdatachannel[core,ws,srtp]:x64-windows-static-md" "opus:x64-windows-static-md"
& $cmake -S plugin -B plugin/build-static -G "Visual Studio 18 2026" -A x64 `
    -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows-static-md
& $cmake --build plugin/build-static --config Release --target RelaySplit_Standalone RelaySplit_VST3
```

### Why static (the Cubase bug)

A **dynamic** build (`x64-windows` triplet) puts the plugin's deps — `datachannel.dll`, `opus.dll`,
`libssl/libcrypto`, `srtp2`, `juice` — in the VST3 bundle, but **hosts don't add the plugin's own
folder to the DLL dependency search path**. Verified with `LoadLibraryEx`: default search →
`ERROR_MOD_NOT_FOUND (126)`; only `LOAD_WITH_ALTERED_SEARCH_PATH` succeeds. So Cubase fails to load the
VST3 and **blocklists** it (reactivating just retries the same failing load → error). The Standalone is
unaffected because an `.exe`'s own directory is always searched. Static linking removes every runtime
DLL → the VST3 loads in any host with the default search. It also drops `/FORCE:MULTIPLE`: that was a
dynamic-only LNK2005 (the import lib vs `juce_core` both defining `std::vector<std::byte>`); with static
libs the COMDAT instantiations fold normally. `CMakeLists.txt` skips `/FORCE:MULTIPLE` for static
triplets. (`VST3_AUTO_MANIFEST FALSE` is kept; the receive path parses RTP manually because the
depacketizer template isn't exported.)

## Test (your machine)

1. **Broadcast (easiest):** run `plugin\build-static\RelaySplit_artefacts\Release\Standalone\RelaySplit.exe`.
   Set the audio device to **48 kHz**, pick an input, click **Connect** (with "Listen to: Broadcast my
   input"). You should hear the isolated vocal back, and the meter should show net RTT + inference
   (~13 ms / ~150 ms, matching the browser client).
2. **Assign peers + receive:** log in (top of the window), use the **peer matrix** to assign peers to
   this instance's broadcast, then on another machine/account either open the web **▶ Tune in** link
   or, in another plugin instance, hit **↻** next to *Listen to*, pick the shared broadcast, and
   **Connect** — it receives the separated vocal with no uplink.
3. **Sibling / group edit:** load **multiple** instances in a DAW; they appear as rows in the matrix.
   Tick the per-row selects and use the top **Group apply** chips to assign a peer across all selected
   instances at once.
4. **VST3:** copy `plugin\build-static\RelaySplit_artefacts\Release\VST3\RelaySplit.vst3` to your VST3
   folder. It's self-contained (no DLLs to ship), so it loads in Cubase/etc. Load on a 48 kHz track, Connect.

Broadcast keys the stream by this instance's control-plane channel id (so assigned peers — and the web
`/listen?channel=<id>` — find it); receive POSTs a recvonly offer to `/subscribe`. Both target the
live container (`https://blitzncs--relaysplit-live-web.modal.run`). Known follow-ups: host sample-rate
≠ 48 kHz needs resampling (assumed 48 kHz for now); RTT via `pc.rtt()`.
