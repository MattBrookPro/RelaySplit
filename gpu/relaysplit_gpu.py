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

# Chosen streaming config: 0.1 s chunk + 5 s past context + short crossfade. chunk = algorithmic
# latency; context is FREE (inference is ~81 ms flat regardless of segment length on L4/fp16 — see
# `knee`), so keep it large for quality. fade is a small future-lookahead that removes block-edge clicks.
CHUNK_S = 0.1
CONTEXT_S = 5.0
FADE_S = 0.01


class OnlineSeparator:
    """Stateful, online version of the streaming separator for the LIVE path. Push inbound audio
    `(ch, n)` at the model rate; pop emitted vocal blocks (one hop each). It keeps a rolling input
    buffer for past context, runs Demucs on `[pos-context, pos+hop+fade]`, and linear-crossfades
    each block's edge with the previous one (a held, faded-out tail) — the online equivalent of
    `_stream_vocals`. `separate_voc` is a callback: `(ch, t)` numpy -> vocal `(ch, t)` numpy."""

    def __init__(self, separate_voc, sr, chunk_s, context_s, fade_s, channels=2):
        import numpy as np

        self.sep = separate_voc
        self.sr = sr
        self.hop = max(1, int(chunk_s * sr))
        self.ola = max(0, int(fade_s * sr))
        self.ctx = int(context_s * sr)
        self.max_seg = int(7.6 * sr)  # Demucs window
        self.inbuf = np.zeros((channels, 0), dtype="float32")
        self.base = 0  # absolute index of inbuf[:, 0]
        self.processed = 0  # absolute count of emitted samples (k * hop)
        self.held = np.zeros((channels, self.ola), dtype="float32")  # prev block's faded-out tail
        self.k = 0
        self.fin = np.linspace(0.0, 1.0, self.ola, dtype="float32") if self.ola else None
        self.fout = np.linspace(1.0, 0.0, self.ola, dtype="float32") if self.ola else None

    def push(self, x):
        import numpy as np

        self.inbuf = np.concatenate([self.inbuf, x.astype("float32")], axis=1)

    def pop(self):
        import numpy as np

        emitted = []
        # Process whenever we have a full hop + the crossfade lookahead buffered ahead of `processed`.
        while self.base + self.inbuf.shape[1] >= self.processed + self.hop + self.ola:
            pos = self.processed
            seg_end = pos + self.hop + self.ola
            start = max(0, pos - self.ctx, seg_end - self.max_seg)
            seg = self.inbuf[:, start - self.base : seg_end - self.base]
            voc = self.sep(seg)  # (ch, seg_end-start)
            blk = voc[:, pos - start : seg_end - start]  # (ch, hop+ola)
            if self.ola:
                head = blk[:, : self.ola].copy() if self.k == 0 else blk[:, : self.ola] * self.fin + self.held
                mid = blk[:, self.ola : self.hop]
                self.held = blk[:, self.hop : self.hop + self.ola] * self.fout  # hold tail for next block
                emit = np.concatenate([head, mid], axis=1)
            else:
                emit = blk[:, : self.hop]
            emitted.append(emit)
            self.processed += self.hop
            self.k += 1
            keep = max(0, self.processed - self.max_seg)  # drop input no longer needed for context
            if keep > self.base:
                self.inbuf = self.inbuf[:, keep - self.base :]
                self.base = keep
        return emitted


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
        if self.device == "cuda":  # match the live container's fast path
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
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
            if self.device == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    out = apply_model(self.model, x[None].to(self.device), split=split, overlap=0.1, device=self.device)[0]
            else:
                out = apply_model(self.model, x[None].to(self.device), split=split, overlap=0.1, device=self.device)[0]
        return out.float() * std + mean

    def _stream_vocals(self, wav, chunk_s, context_s, fade_s=FADE_S):
        """Simulate the live path: emit vocals block-by-block using PAST context, with a short
        LINEAR crossfade between consecutive blocks so edges don't click. Each block is fed
        [pos-context, pos+chunk+fade]; the `fade` overlap is a small future-lookahead, so live
        algorithmic latency = chunk + fade. Linear (not equal-power) because adjacent blocks are the
        same vocal, just computed with slightly different context — they sum to a constant level.
        Returns (vocals (ch,time) numpy, avg per-chunk infer ms)."""
        import time

        import numpy as np

        sr = self.sr
        hop = max(1, int(chunk_s * sr))
        ola = max(0, int(fade_s * sr))
        ctx = int(context_s * sr)
        max_seg = int(7.6 * sr)  # Demucs htdemucs window is ~7.8 s — keep the fed segment under it
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8  # global normalisation (no inter-block pumping)

        total = wav.shape[1]
        out = np.zeros((wav.shape[0], total), dtype="float32")
        fade_in = np.linspace(0.0, 1.0, ola, dtype="float32") if ola else None
        fade_out = np.linspace(1.0, 0.0, ola, dtype="float32") if ola else None

        infer_total, nb, pos, k = 0.0, 0, 0, 0
        while pos < total:
            seg_end = min(pos + hop + ola, total)  # +ola future-lookahead for the crossfade tail
            start = max(0, pos - ctx, seg_end - max_seg)  # cap context to the model window
            seg = wav[:, start:seg_end].contiguous()
            t0 = time.perf_counter()
            est = self._separate_seg(seg, mean, std, split=False)  # single pass (seg < window)
            self._sync()
            infer_total += time.perf_counter() - t0
            nb += 1

            blk = est[self.voc_idx].cpu().numpy()[:, pos - start : seg_end - start]  # region [pos, seg_end)
            length = blk.shape[1]
            win = np.ones(length, dtype="float32")
            if ola and k > 0:  # crossfade with the previous block's faded-out tail
                win[:ola] = fade_in
            if ola and seg_end < total:  # this tail gets crossfaded by the next block's fade-in
                win[-ola:] = fade_out
            out[:, pos : pos + length] += blk * win
            pos += hop
            k += 1
        return out, (infer_total / max(nb, 1)) * 1000

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
    def knee_sweep(self, configs, fade_s: float = 0.01, reps: int = 7):
        """The latency-assault measurement: for each (hop_s, context_s), time the fp16 inference on a
        representative steady-state segment (context+hop+fade) and report whether it sustains real time
        (infer < hop) plus the GPU utilisation it implies. Timing is content-independent, so noise is
        fine here; quality at the chosen config is judged separately on real audio."""
        import time

        import numpy as np

        max_seg = 7.6
        res = []
        for hop_s, ctx_s in configs:
            seg_s = min(ctx_s + hop_s + fade_s, max_seg)
            n = int(seg_s * self.sr)
            wav = self.torch.from_numpy((0.1 * np.random.randn(self.channels, n)).astype("float32"))
            ref = wav.mean(0)
            mean, std = ref.mean(), ref.std() + 1e-8
            for _ in range(3):  # warm the kernels for THIS shape (cudnn.benchmark)
                self._separate_seg(wav, mean, std, split=False)
            self._sync()
            ts = []
            for _ in range(reps):
                t0 = time.perf_counter()
                self._separate_seg(wav, mean, std, split=False)
                self._sync()
                ts.append((time.perf_counter() - t0) * 1000)
            infer = sorted(ts)[len(ts) // 2]  # median
            algo = (hop_s + fade_s) * 1000
            res.append({
                "hop_s": hop_s, "context_s": ctx_s, "seg_s": round(seg_s, 3),
                "infer_ms": round(infer, 1), "algo_ms": round(algo, 1),
                "gpu_util_pct": round(100 * infer / (hop_s * 1000), 1),
                "rt_feasible": bool(infer < hop_s * 1000),
                "core_latency_ms": round(algo + infer, 1),  # algo + compute; add net+jitter for end-to-end
            })
        return {"device": self.device, "sr": self.sr, "results": res}

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
    def latency_sweep(self, audio_bytes: bytes, configs, max_seconds: int = 20, fade_s: float = FADE_S):
        """For each [chunk_s, context_s]: stream the clip (with crossfade) and report measured
        latency + the vocal WAV. Algorithmic latency = chunk + fade (the crossfade lookahead)."""
        wav = self._decode(audio_bytes, max_seconds)
        results = []
        for chunk_s, context_s in configs:
            vocals, infer_ms = self._stream_vocals(wav, chunk_s, context_s, fade_s)
            algo_ms = (chunk_s + fade_s) * 1000
            results.append(
                {
                    "chunk_s": chunk_s,
                    "context_s": context_s,
                    "fade_ms": round(fade_s * 1000, 1),
                    "algo_latency_ms": round(algo_ms, 1),
                    "infer_ms_per_chunk": round(infer_ms, 1),
                    # live latency = buffer the chunk+fade + infer it (+ network RTT in the live path)
                    "live_latency_est_ms": round(algo_ms + infer_ms, 1),
                    # real-time only holds if a chunk infers faster than its own hop
                    "rt_feasible": bool(infer_ms < chunk_s * 1000),
                    "vocals_wav": self._to_wav(vocals),
                }
            )
        return {"sr": self.sr, "results": results}

    def _sep_voc(self, seg):
        # seg: torch (ch, t) -> vocal (ch, t) numpy. Per-segment normalisation (online has no global
        # stats); the 5 s+ window keeps std stable enough that this doesn't pump between blocks.
        from demucs.apply import apply_model

        torch = self.torch
        ref = seg.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        x = (seg - mean) / std
        with torch.no_grad():
            out = apply_model(self.model, x[None].to(self.device), split=False, device=self.device)[0]
        out = out * std + mean
        return out[self.voc_idx].cpu().numpy()

    @modal.method()
    def stream_test(self, audio_bytes: bytes, frame_ms: int = 20, max_seconds: int = 20):
        """Validate the ONLINE separator offline: feed the clip in `frame_ms` frames (as WebRTC
        would) through OnlineSeparator and reconstruct the vocal. Confirms the streaming buffer +
        crossfade logic before it goes near aiortc. overall_rtf < 1 ⇒ keeps up in real time."""
        import time

        import numpy as np

        torch = self.torch
        wav = self._decode(audio_bytes, max_seconds).numpy()
        online = OnlineSeparator(
            lambda s: self._sep_voc(torch.from_numpy(np.ascontiguousarray(s))),
            self.sr, CHUNK_S, CONTEXT_S, FADE_S, channels=wav.shape[0],
        )
        frame = max(1, int(frame_ms / 1000 * self.sr))
        parts = []
        t0 = time.perf_counter()
        for i in range(0, wav.shape[1], frame):
            online.push(wav[:, i : i + frame])
            parts.extend(online.pop())
        self._sync()
        wall = time.perf_counter() - t0
        vocals = np.concatenate(parts, axis=1) if parts else np.zeros((wav.shape[0], 0), dtype="float32")
        audio_s = vocals.shape[1] / self.sr
        return {
            "sr": self.sr,
            "wall_s": round(wall, 2),
            "audio_s": round(audio_s, 2),
            "overall_rtf": round(wall / max(audio_s, 1e-3), 3),
            "vocals_wav": self._to_wav(vocals),
        }


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(Separator().benchmark.remote(), indent=2))


# --- Multi-GPU forward-time bench (no region pin = best availability; each call is BOUNDED and
# cancelled on timeout so an unavailable GPU can't hang/loop). Run foreground: modal run ...::gpubench
def _bench_forward(label):
    import statistics
    import time

    import numpy as np
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    name = torch.cuda.get_device_name(0) if dev == "cuda" else "cpu"
    model = get_model(MODEL_NAME).to(dev).eval()
    sr = int(model.samplerate)
    wav = torch.from_numpy((0.1 * np.random.randn(2, int((CONTEXT_S + CHUNK_S) * sr))).astype("float32")).to(dev)
    ref = wav.mean(0)
    x = ((wav - ref.mean()) / (ref.std() + 1e-8))[None]

    def fwd():
        with torch.no_grad():
            if dev == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    apply_model(model, x, split=False, device=dev)
                torch.cuda.synchronize()
            else:
                apply_model(model, x, split=False, device=dev)

    for _ in range(5):
        fwd()
    ts = []
    for _ in range(9):
        t0 = time.perf_counter()
        fwd()
        ts.append((time.perf_counter() - t0) * 1000)
    return {"gpu": label, "device": name, "forward_ms": round(statistics.median(ts), 1),
            "min_ms": round(min(ts), 1), "max_ms": round(max(ts), 1)}


@app.function(image=image, gpu="L4", timeout=180)
def bench_l4():
    return _bench_forward("L4")


@app.function(image=image, gpu="A10G", timeout=180)
def bench_a10g():
    return _bench_forward("A10G")


@app.function(image=image, gpu="L40S", timeout=180)
def bench_l40s():
    return _bench_forward("L40S")


@app.function(image=image, gpu="A100", timeout=180)
def bench_a100():
    return _bench_forward("A100")


@app.local_entrypoint()
def gpubench():
    fns = [("L4", bench_l4), ("A10G", bench_a10g), ("L40S", bench_l40s), ("A100", bench_a100)]
    for label, fn in fns:
        call = None
        try:
            call = fn.spawn()
            res = call.get(timeout=100)  # bounded wait; if no capacity, skip rather than hang
            print(f"{label:>6}: forward={res['forward_ms']}ms (min {res['min_ms']} / max {res['max_ms']})  [{res['device']}]")
        except Exception as e:
            if call is not None:
                try:
                    call.cancel()  # CANCEL so an unavailable GPU can't keep running/queuing
                except Exception:
                    pass
            print(f"{label:>6}: SKIP ({type(e).__name__}: {str(e)[:80]})")


@app.local_entrypoint()
def knee():
    # Find the fp16 real-time knee: how small can the hop go while inference still beats it?
    configs = [
        [0.25, 5.0],  # current
        [0.10, 3.0], [0.10, 2.0], [0.10, 1.0],
        [0.05, 2.0], [0.05, 1.0], [0.05, 0.5],
        [0.025, 1.0], [0.025, 0.5],
    ]
    r = Separator().knee_sweep.remote(configs)
    print(f"device={r['device']} sr={r['sr']}")
    print(f"{'hop':>6} {'ctx':>5} {'seg':>5} {'infer_ms':>9} {'algo_ms':>8} {'util%':>6} {'rt':>5} {'core_ms':>8}")
    for x in r["results"]:
        print(f"{x['hop_s']:>6} {x['context_s']:>5} {x['seg_s']:>5} {x['infer_ms']:>9} "
              f"{x['algo_ms']:>8} {x['gpu_util_pct']:>6} {str(x['rt_feasible']):>5} {x['core_latency_ms']:>8}")


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
    # Render the chosen streaming config WITH crossfade, to confirm the block edges are clean.
    configs = [[CHUNK_S, CONTEXT_S]]
    res = Separator().latency_sweep.remote(data, configs)
    print(f"sr={res['sr']}")
    for r in res["results"]:
        print(f"chunk={r['chunk_s']}s ctx={r['context_s']}s fade={r['fade_ms']}ms  "
              f"algo={r['algo_latency_ms']}ms  infer={r['infer_ms_per_chunk']}ms  "
              f"live_est={r['live_latency_est_ms']}ms  rt={r['rt_feasible']}")
        p = os.path.join(outdir, f"final_voc_c{r['chunk_s']}_ctx{r['context_s']}_xfade.wav")
        with open(p, "wb") as f:
            f.write(r["vocals_wav"])
        print("  wrote", p)


@app.local_entrypoint()
def streamtest(input: str, outdir: str = "."):
    # Offline validation of the ONLINE streaming separator (the live path's core logic).
    import os

    with open(input, "rb") as f:
        data = f.read()
    res = Separator().stream_test.remote(data)
    print(f"sr={res['sr']}  audio={res['audio_s']}s  wall={res['wall_s']}s  overall_rtf={res['overall_rtf']}")
    p = os.path.join(outdir, "out_streamtest.wav")
    with open(p, "wb") as f:
        f.write(res["vocals_wav"])
    print("wrote", p)
