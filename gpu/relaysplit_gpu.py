"""
RelaySplit GPU container — high-quality vocal separation on a region-pinned UK GPU, with a
streaming path engineered for the lowest latency the model allows.

Model: Meta **Demucs v4 / htdemucs** (full-band 44.1 kHz, SOTA-tier open vocal separation).
Quality-first pivot from the brief's causal Conv-TasNet (which was unusable) — Demucs is heavy,
so it genuinely needs a datacenter GPU, and we make the latency HONEST and measured rather than
hidden. HS-TasNet is the cited causal upgrade path.

LATENCY MODEL (the whole game). Demucs is non-causal, so to emit audio for [t, t+chunk] you must
have captured up to t+chunk. Therefore:
  - algorithmic latency  ≈ chunk length              (the cost you pay to buffer the chunk)
  - past context         = already-captured audio    (improves quality, FREE in latency)
  - compute latency      = inference over (context+chunk)  (costs time + GPU $, NOT extra latency
                            beyond the chunk, but it eats into the real-time budget)
  - network RTT          = host <-> Modal (UK<->UK)   (the physical floor)
So we feed [t-context, t+chunk], keep only the chunk's output, and push `chunk` as small as
quality allows. `latency_sweep` measures this trade-off empirically so we pick the knee.

    modal run gpu/relaysplit_gpu.py                          # per-chunk inference benchmark
    modal run gpu/relaysplit_gpu.py::listen --input clip.ogg # whole-clip separation (best quality)
    modal run gpu/relaysplit_gpu.py::sweep  --input clip.ogg # latency sweep: stems @ several chunks
"""
import modal

REGION = "uk"  # confirmed latency-critical pin (docs/setup-machine2.md)
MODEL_NAME = "htdemucs"  # Demucs v4; sources: drums, bass, other, vocals


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
        "soundfile==0.12.1",
        "scipy==1.13.1",
    )
    .run_function(_bake_model)
)

app = modal.App("relaysplit-gpu", image=image)


@app.cls(gpu="L4", region=REGION, scaledown_window=120)
class Separator:
    @modal.enter()
    def load(self):
        import torch
        from demucs.pretrained import get_model

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = get_model(MODEL_NAME).to(self.device)
        self.model.eval()
        self.sr = int(self.model.samplerate)  # 44100
        self.channels = int(self.model.audio_channels)  # 2
        self.sources = list(self.model.sources)  # ['drums','bass','other','vocals']
        self.voc_idx = self.sources.index("vocals")

    def _sync(self):
        if self.device == "cuda":
            self.torch.cuda.synchronize()

    # ---- audio helpers --------------------------------------------------------------------
    def _decode(self, audio_bytes, max_seconds):
        import io
        from math import gcd

        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly

        audio, in_sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)  # (time, ch)
        wav = audio.T  # (ch, time)
        if int(in_sr) != self.sr:
            g = gcd(int(in_sr), self.sr)
            wav = resample_poly(wav, self.sr // g, int(in_sr) // g, axis=1).astype("float32")
        if wav.shape[0] == 1:  # Demucs is stereo
            wav = np.repeat(wav, 2, axis=0)
        elif wav.shape[0] > 2:
            wav = wav[:2]
        wav = wav[:, : self.sr * max_seconds]
        return self.torch.from_numpy(np.ascontiguousarray(wav))

    def _to_wav(self, x):  # x: (ch, time) numpy -> 16-bit WAV bytes
        import io

        import numpy as np
        import soundfile as sf

        buf = io.BytesIO()
        peak = float(np.max(np.abs(x))) or 1.0
        sf.write(buf, (x.T / peak * 0.95).astype("float32"), self.sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    # ---- separation -----------------------------------------------------------------------
    def _separate_seg(self, seg, mean, std, split):
        # seg: (ch, time) -> (n_src, ch, time). Uses GLOBAL mean/std so streamed blocks don't pump.
        from demucs.apply import apply_model

        torch = self.torch
        x = (seg - mean) / std
        with torch.no_grad():
            out = apply_model(
                self.model, x[None].to(self.device), split=split, overlap=0.1, device=self.device
            )[0]
        return out * std + mean

    def _stream_vocals(self, wav, chunk_s, context_s):
        """Simulate the live path: emit vocals block-by-block using PAST context only (no future
        beyond the current chunk). Returns (vocals (ch,time) numpy, avg per-chunk infer ms)."""
        import time

        torch = self.torch
        sr = self.sr
        chunk = max(1, int(chunk_s * sr))
        ctx = int(context_s * sr)
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8  # global normalisation

        pos, outs, infer_total, nb = 0, [], 0.0, 0
        total = wav.shape[1]
        while pos < total:
            end = min(pos + chunk, total)
            start = max(0, pos - ctx)
            seg = wav[:, start:end].contiguous()
            t0 = time.perf_counter()
            out = self._separate_seg(seg, mean, std, split=False)  # small seg -> single pass
            self._sync()
            infer_total += time.perf_counter() - t0
            nb += 1
            voc = out[self.voc_idx]  # (ch, segtime)
            outs.append(voc[:, pos - start : pos - start + (end - pos)].cpu())
            pos = end
        vocals = torch.cat(outs, dim=1).numpy()
        return vocals, (infer_total / max(nb, 1)) * 1000

    # ---- entrypoint methods ---------------------------------------------------------------
    @modal.method()
    def benchmark(self, chunk_seconds=(6.0, 3.0, 1.5, 0.75)):
        import time

        import numpy as np

        torch = self.torch
        out = {"device": self.device, "model": MODEL_NAME, "sr": self.sr, "chunks": []}
        for cs in chunk_seconds:
            n = int(cs * self.sr)
            wav = torch.from_numpy((0.1 * np.random.randn(self.channels, n)).astype("float32"))
            ref = wav.mean(0)
            self._separate_seg(wav, ref.mean(), ref.std() + 1e-8, split=False)  # warmup
            self._sync()
            t0 = time.perf_counter()
            self._separate_seg(wav, ref.mean(), ref.std() + 1e-8, split=False)
            self._sync()
            dt = time.perf_counter() - t0
            out["chunks"].append({"chunk_s": cs, "infer_ms": round(dt * 1000, 1), "rtf": round(dt / cs, 3)})
        return out

    @modal.method()
    def separate_clip(self, audio_bytes: bytes, max_seconds: int = 30):
        """Whole-clip (best-quality) separation -> vocal + accompaniment WAV bytes."""
        import time

        wav = self._decode(audio_bytes, max_seconds)
        ref = wav.mean(0)
        t0 = time.perf_counter()
        out = self._separate_seg(wav, ref.mean(), ref.std() + 1e-8, split=True)
        self._sync()
        infer_ms = (time.perf_counter() - t0) * 1000
        vocals = out[self.voc_idx].cpu().numpy()
        accomp = sum(out[i] for i in range(len(self.sources)) if i != self.voc_idx).cpu().numpy()
        return {
            "sr": self.sr,
            "infer_ms": round(infer_ms, 1),
            "stems": {"vocals": self._to_wav(vocals), "accompaniment": self._to_wav(accomp)},
        }

    @modal.method()
    def latency_sweep(self, audio_bytes: bytes, configs, max_seconds: int = 20):
        """For each [chunk_s, context_s]: stream the clip and report measured latency + the vocal
        WAV, so we can hear where quality breaks vs how low the latency goes."""
        wav = self._decode(audio_bytes, max_seconds)
        results = []
        for chunk_s, context_s in configs:
            vocals, infer_ms = self._stream_vocals(wav, chunk_s, context_s)
            results.append(
                {
                    "chunk_s": chunk_s,
                    "context_s": context_s,
                    "algo_latency_ms": round(chunk_s * 1000, 1),
                    "infer_ms_per_chunk": round(infer_ms, 1),
                    # live latency = buffer the chunk + infer it (+ network RTT, added in the live path)
                    "live_latency_est_ms": round(chunk_s * 1000 + infer_ms, 1),
                    "vocals_wav": self._to_wav(vocals),
                }
            )
        return {"sr": self.sr, "results": results}


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(Separator().benchmark.remote(), indent=2))


@app.local_entrypoint()
def listen(input: str, outdir: str = "."):
    import os

    with open(input, "rb") as f:
        data = f.read()
    res = Separator().separate_clip.remote(data)
    print(f"inference: {res['infer_ms']} ms  sr={res['sr']}")
    for name, b in res["stems"].items():
        p = os.path.join(outdir, f"out_{name}.wav")
        with open(p, "wb") as f:
            f.write(b)
        print("wrote", p)


@app.local_entrypoint()
def sweep(input: str, outdir: str = "."):
    import os

    with open(input, "rb") as f:
        data = f.read()
    # chunk_s (latency) shrinks; context_s (past, free-latency) kept generous for quality.
    configs = [[2.0, 4.0], [1.0, 3.0], [0.5, 2.0], [0.25, 1.5]]
    res = Separator().latency_sweep.remote(data, configs)
    print(f"sr={res['sr']}")
    print(f"{'chunk_s':>8} {'ctx_s':>6} {'algo_ms':>8} {'infer_ms':>9} {'live_est_ms':>12}")
    for r in res["results"]:
        print(f"{r['chunk_s']:>8} {r['context_s']:>6} {r['algo_latency_ms']:>8} "
              f"{r['infer_ms_per_chunk']:>9} {r['live_latency_est_ms']:>12}")
        p = os.path.join(outdir, f"sweep_vocals_chunk{r['chunk_s']}.wav")
        with open(p, "wb") as f:
            f.write(r["vocals_wav"])
        print("  wrote", p)
