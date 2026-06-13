# RelaySplit GPU container

The data-plane processing node: a warm, region-pinned (`uk`) GPU running a **Conv-TasNet**
vocal/accompaniment separator. Next slice wires it into a WebRTC (`aiortc`) track so live audio
is separated through the container.

## Model

`groadabike/ConvTasNet_DAMP-VSEP_enhboth` — asteroid-native Conv-TasNet, 2 sources
(vocals / accompaniment), 8 kHz. Weights are **baked into the image** at build time so a cold
start is scheduling only, never a download.

- **Why this repo, not the popular forks:** `hugggof`/`oreillyp` "DAMP-Vocals" are Audacity
  TorchScript exports created with `torch.jit.trace`, which bakes constants onto the CPU and is
  device-locked — they can't use a GPU. The groadabike original is asteroid-native and runs on GPU.
- **Why Conv-TasNet, not Demucs:** Demucs is offline-architecture (lookahead, big windows). The
  real-time SOTA is HS-TasNet (23 ms frames) but has no public weights — the upgrade path.
- **Licence:** model CC-BY-SA-4.0; DAMP-VSEP training data is research-only (fine for a demo;
  production would train on licensed data).

## The GPU case, measured (why this needs a datacenter)

`modal run gpu/relaysplit_gpu.py` benchmarks the model. On an **L4**, inference is **~20 ms per
block regardless of block size** (whole-clip RTF 0.0035). The **same model on CPU** is marginal —
realtime ratio 0.63 at 1 s blocks rising to **0.97 at 64 ms**, i.e. it runs out of headroom
exactly at the low-latency end. The GPU is what makes low-latency separation possible:

| block | GPU infer | GPU realtime ratio | CPU realtime ratio |
|------:|----------:|-------------------:|-------------------:|
| 1000 ms | 22 ms | 0.02 | 0.64 |
| 500 ms | 20 ms | 0.04 | 0.64 |
| 250 ms | 19 ms | 0.08 | 0.70 |
| 128 ms | 19 ms | 0.15 | 0.81 |
| 64 ms | — | — | 0.97 |

## Run

```bash
modal run gpu/relaysplit_gpu.py     # build (bakes weights), benchmark on an L4 in uk
```

## Next (part 2)

Wire the model into an `aiortc` `MediaStreamTrack`: resample 48 kHz ↔ 8 kHz, block + overlap-add,
emit the chosen stem, report inference time toward the live latency meter. Then connect the
container to the VPS signalling server as a session peer.
