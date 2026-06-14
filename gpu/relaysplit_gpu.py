"""
RelaySplit GPU container — real-time vocal separation, region-pinned to the UK.

The data-plane processing node: a warm, region-pinned GPU running a causal-family separation
model that (next slice) joins the WebRTC session as a peer. The brief's thesis: do something
that ONLY a datacenter GPU can do fast enough, over the network, at the latency floor.

Model: Asteroid **Conv-TasNet** (the brief's named family — time-domain, GPU-friendly), the
ORIGINAL asteroid checkpoint `groadabike/ConvTasNet_DAMP-VSEP_enhboth` — 2 sources
(vocals / accompaniment), trained on DAMP-VSEP.
- Why this repo: the popular `hugggof`/`oreillyp` "DAMP-Vocals" forks are Audacity TorchScript
  exports created with torch.jit.trace, which bakes constants onto CPU — device-locked, can't
  use a GPU. The groadabike original is asteroid-native (pytorch_model.bin) and runs on GPU.
- Why Conv-TasNet not Demucs: Demucs is offline-architecture (big windows, lookahead), wrong
  for live. The SOTA real-time target is HS-TasNet (L-Acoustics, 23 ms frames) but it has no
  public weights — cited as the upgrade path. The causal-vs-quality trade-off is deliberate
  (see LEARNING.md), and measured here.
- Licence: model CC-BY-SA-4.0; DAMP-VSEP training data is research-only — fine for a demo, and
  an honest "for production you'd train on licensed data" point.

Model slice, part 1 of 2: prove the model loads on a UK GPU and MEASURE real-time feasibility
(RTF + per-block latency). The CPU baseline for the same model was marginal (realtime ratio
0.63 at 1 s blocks rising to 0.97 at 64 ms) — i.e. CPU runs out of headroom exactly at the
low-latency end, which is the whole point of using the GPU. Part 2 wires it into an aiortc track.

    modal run gpu/relaysplit_gpu.py        # build (bakes weights), benchmark on an L4
"""
import modal

REGION = "uk"  # confirmed latency-critical pin (docs/setup-machine2.md)
MODEL_ID = "groadabike/ConvTasNet_DAMP-VSEP_enhboth"  # asteroid-native Conv-TasNet, vocals/accompaniment


def _bake_model():
    # Download the weights at BUILD time so a cold start is container scheduling only, never a
    # model download (the brief's #1 cold-start mitigation). Baked into the image layer.
    from asteroid.models import ConvTasNet

    ConvTasNet.from_pretrained(MODEL_ID)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "numpy<2",  # asteroid + older deps break on numpy 2
        "torch==2.4.1",
        "torchaudio==2.4.1",
        "huggingface_hub==0.23.0",  # asteroid 0.7.0 needs the legacy cached_download (gone in >=0.26)
        "requests",  # asteroid.utils.hub_utils imports it but doesn't declare it
        "asteroid==0.7.0",
        "scipy==1.13.1",  # resample_poly for 48k<->model-rate
        "soundfile==0.12.1",  # decode/encode WAV/FLAC/OGG clips for the listen test
    )
    .run_function(_bake_model)
)

app = modal.App("relaysplit-gpu", image=image)


@app.cls(gpu="L4", region=REGION, scaledown_window=120)
class Separator:
    @modal.enter()
    def load(self):
        # Load the model onto the GPU ONCE at container startup and keep it resident, so per-block
        # inference pays no load cost. @modal.enter runs after the container is scheduled + warm.
        import torch
        from asteroid.models import ConvTasNet

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = ConvTasNet.from_pretrained(MODEL_ID).to(self.device).eval()
        # The model's native rate (DAMP-VSEP models are 8 kHz). Read it rather than assume.
        self.sr = int(getattr(self.model, "sample_rate", 8000) or 8000)

    def _sync(self):
        if self.device == "cuda":
            self.torch.cuda.synchronize()

    @modal.method()
    def benchmark(self, seconds: float = 10.0, block_sizes=(16000, 8000, 4000, 2000, 1024)):
        """Measure real-time feasibility on the GPU.

        RTF = inference_time / audio_duration; RTF << 1 means headroom to run live. Per-block
        latency shows how small a streaming chunk we can afford (algorithmic latency is dominated
        by block size). Input is synthetic (tone + noise) — this measures SPEED, not separation
        quality; quality is judged live once the model is in the audio path.
        """
        import time
        import numpy as np

        torch = self.torch
        n = int(seconds * self.sr)
        t = np.arange(n) / self.sr
        x = (0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.random.randn(n)).astype("float32")

        out = {"device": self.device, "model": MODEL_ID, "sr": self.sr}

        with torch.no_grad():
            wav = torch.from_numpy(x).to(self.device).unsqueeze(0)
            est = self.model(wav)  # warmup + shape probe -> (batch, n_src, time)
            self._sync()
            t0 = time.perf_counter()
            self.model(wav)
            self._sync()
            dt = time.perf_counter() - t0
        out["whole_clip"] = {
            "audio_s": seconds,
            "infer_s": round(dt, 4),
            "rtf": round(dt / seconds, 4),
            "out_shape": list(est.shape),
        }

        out["blocks"] = []
        for bs in block_sizes:
            blk = torch.from_numpy(x[:bs]).to(self.device).unsqueeze(0)
            with torch.no_grad():
                self.model(blk)  # warmup this size
                self._sync()
                times = []
                for _ in range(20):
                    t0 = time.perf_counter()
                    self.model(blk)
                    self._sync()
                    times.append(time.perf_counter() - t0)
            median_ms = sorted(times)[len(times) // 2] * 1000
            block_ms = bs / self.sr * 1000
            out["blocks"].append(
                {
                    "block_samples": bs,
                    "block_ms": round(block_ms, 1),
                    "infer_ms_median": round(median_ms, 2),
                    # realtime headroom: infer must be well under the block's own duration
                    "realtime_ratio": round(median_ms / block_ms, 3),
                }
            )
        return out

    @modal.method()
    def separate_clip(self, audio_bytes: bytes):
        """Separate one clip into its two stems. Returns BOTH as 16-bit WAV bytes so the caller can
        listen and decide which is the vocal (DAMP-VSEP source order isn't guaranteed). Whole-clip
        offline inference — this is the quick quality listen test, not the real-time path."""
        import io
        import time
        from math import gcd

        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly

        torch = self.torch
        audio, in_sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
        mono = audio.mean(axis=1)  # downmix to mono (the model is single-channel)
        if int(in_sr) != self.sr:
            g = gcd(int(in_sr), self.sr)
            mono = resample_poly(mono, self.sr // g, int(in_sr) // g).astype("float32")
        mono = mono[: self.sr * 30]  # cap at 30 s — enough to judge quality, avoids long-clip OOM

        with torch.no_grad():
            wav = torch.from_numpy(mono).to(self.device).unsqueeze(0)
            t0 = time.perf_counter()
            est = self.model(wav)
            self._sync()
            infer_ms = (time.perf_counter() - t0) * 1000
        est = est.squeeze(0).cpu().numpy()  # (n_src, time)

        def to_wav(x):
            buf = io.BytesIO()
            peak = float(np.max(np.abs(x))) or 1.0
            sf.write(buf, (x / peak * 0.95).astype("float32"), self.sr, format="WAV", subtype="PCM_16")
            return buf.getvalue()

        return {"sr": self.sr, "infer_ms": round(infer_ms, 1), "stems": [to_wav(s) for s in est]}


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(Separator().benchmark.remote(), indent=2))


@app.local_entrypoint()
def listen(input: str, outdir: str = "."):
    # Offline quality test: send a real clip, get both separated stems back as WAV files to play.
    import os

    with open(input, "rb") as f:
        data = f.read()
    res = Separator().separate_clip.remote(data)
    print(f"inference: {res['infer_ms']} ms  sr={res['sr']}  stems={len(res['stems'])}")
    for i, b in enumerate(res["stems"]):
        p = os.path.join(outdir, f"out_stem{i}.wav")
        with open(p, "wb") as f:
            f.write(b)
        print("wrote", p)
