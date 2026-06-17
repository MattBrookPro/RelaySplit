# RelaySplit GPU

The data plane: a warm, region-pinned (`uk`) GPU that is itself a WebRTC peer. Inbound audio is
resampled to 44.1 kHz, separated in a sliding window, and streamed back. One sender feeds a broadcast
and any number of receivers tune in (a fan-out hub).

## Files

* `relaysplit_live.py` is the LIVE container (this is what you deploy). It loads SCNet, runs the
  streaming separator, serves the sender and receiver pages, signals over both `POST /offer` and the
  VPS `/ws` control plane, and reports liveness back to the control plane.
* `relaysplit_gpu.py` is the offline benchmark and dev harness, on the htdemucs baseline: the latency
  sweep, the fp16 real-time knee, the per-GPU forward benchmark, and offline streaming validation.
* `relaysplit_scnet_ab.py` is the SCNet vs htdemucs A/B (forward time plus a quality render).
* `relaysplit_test_client.py` is an automated aiortc client that round-trips audio through the live
  container (see its note on a Modal-to-Modal symmetric-NAT limitation).

## Model

The live path runs SCNet-small (ZFTurbo MSST checkpoint, MUSDB vocal SDR about 9.9) in half precision.
It was chosen over Hybrid Transformer Demucs (htdemucs) after an A/B: roughly 2.5 times faster per
forward pass and cleaner vocals. htdemucs remains the quality baseline used by the benchmark harness.
The brief's causal Conv-TasNet was tried first and dropped for quality. HS-TasNet is the causal,
lower-latency path, but it has no public pretrained weights.

## Latency

Inference is roughly flat regardless of segment length (the model is launch-overhead bound), so past
context is effectively free and the chunk size (the hop) is the only real latency lever. The live
container measures its per-chunk cost at startup and sizes the hop adaptively to whatever GPU it lands
on. End to end is around 0.2 seconds on a UK-to-UK path. SCNet is non-causal, so the buffered chunk is
the algorithmic floor.

## Run

```
modal deploy gpu/relaysplit_live.py        # the live container
modal run gpu/relaysplit_gpu.py::knee      # fp16 real-time knee
modal run gpu/relaysplit_gpu.py::gpubench  # forward time across GPU types
```

## Cost note

Nothing external polls this container: clients poll the VPS for liveness, and the container self-pings
only while peers are connected, so it scales to zero when idle.
