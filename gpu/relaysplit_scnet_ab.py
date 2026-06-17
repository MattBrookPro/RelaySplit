"""
SCNet vs htdemucs A/B — does swapping the model actually buy us latency (faster forward) without
losing vocal quality? Measures fp16 forward time on the GPU for both, and separates the baked demo
clip with each so we can listen. SCNet weights come from ZFTurbo's MSST GitHub releases.

    modal run gpu/relaysplit_scnet_ab.py     # foreground, bounded (timeout=300), writes scnet.wav + ht.wav
"""
import modal

REGION = "uk"
MSST = "https://github.com/ZFTurbo/Music-Source-Separation-Training"
SCNET_CFG = f"{MSST}/releases/download/v.1.0.6/config_musdb18_scnet.yaml"
SCNET_CKPT = f"{MSST}/releases/download/v.1.0.6/scnet_checkpoint_musdb18.ckpt"
DEMO_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/Bessie%20Smith%20-%20Downhearted%20Blues%20(1923).ogg"


def _bake():
    import urllib.request

    from demucs.pretrained import get_model

    get_model("htdemucs")  # bake htdemucs weights
    req = urllib.request.Request(DEMO_URL, headers={"User-Agent": "RelaySplit/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=120) as r, open("/demo.ogg", "wb") as f:
        f.write(r.read())


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "wget", "ffmpeg")
    .pip_install(
        "numpy<2", "torch==2.4.1", "torchaudio==2.4.1", "demucs==4.0.1",
        "soundfile", "scipy", "librosa", "omegaconf", "ml_collections", "einops", "tqdm", "pyyaml",
    )
    .run_commands(
        "git clone --depth 1 https://github.com/ZFTurbo/Music-Source-Separation-Training /root/msst",
        f"wget -q '{SCNET_CFG}' -O /root/scnet.yaml",
        f"wget -q '{SCNET_CKPT}' -O /root/scnet.ckpt",
    )
    .run_function(_bake)
)

app = modal.App("relaysplit-scnet-ab", image=image)


@app.function(image=image, gpu="L4", region=REGION, timeout=300)
def ab():
    import io
    import statistics
    import sys
    import time

    sys.path.insert(0, "/root/msst")
    import librosa
    import numpy as np
    import soundfile as sf
    import torch

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.backends.cudnn.benchmark = True
    dev = "cuda"
    sr = 44100
    out = {"device": torch.cuda.get_device_name(0)}

    def wav_bytes(x):
        peak = float(np.max(np.abs(x))) or 1.0
        b = io.BytesIO()
        sf.write(b, (x.T / peak * 0.95).astype("float32"), sr, format="WAV", subtype="PCM_16")
        return b.getvalue()

    # ---- htdemucs: forward timing on a streaming-sized 5.15 s segment ----
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    ht = get_model("htdemucs").to(dev).eval()
    seg = torch.from_numpy((0.1 * np.random.randn(2, int(5.15 * sr))).astype("float32")).to(dev)
    ref = seg.mean(0)
    m, s = ref.mean(), ref.std() + 1e-8

    def ht_fwd():
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            apply_model(ht, ((seg - m) / s)[None], split=False, device=dev)
        torch.cuda.synchronize()

    for _ in range(4):
        ht_fwd()
    t = []
    for _ in range(7):
        t0 = time.perf_counter(); ht_fwd(); t.append((time.perf_counter() - t0) * 1000)
    out["htdemucs_fwd_ms"] = round(statistics.median(t), 1)

    # ---- SCNet: load via MSST ----
    from utils.settings import get_model_from_config

    model, config = get_model_from_config("scnet", "/root/scnet.yaml")
    state = torch.load("/root/scnet.ckpt", map_location="cpu")
    for k in ("state", "state_dict", "model_state_dict"):
        if isinstance(state, dict) and k in state:
            state = state[k]
    model.load_state_dict(state)
    model = model.to(dev).eval()
    instruments = list(config.training.instruments)
    out["scnet_instruments"] = instruments

    # SCNet forward timing on the same 5.15 s segment (fall back to chunked demix timing if raw fails)
    sx = ((seg - m) / s)[None]
    try:
        def sc_fwd():
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
                model(sx)
            torch.cuda.synchronize()
        for _ in range(4):
            sc_fwd()
        t = []
        for _ in range(7):
            t0 = time.perf_counter(); sc_fwd(); t.append((time.perf_counter() - t0) * 1000)
        out["scnet_fwd_ms"] = round(statistics.median(t), 1)
    except Exception as e:
        out["scnet_fwd_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    # ---- quality A/B on the demo clip (first 20 s) ----
    mix, _ = librosa.load("/demo.ogg", sr=sr, mono=False)
    if mix.ndim == 1:
        mix = np.stack([mix, mix])
    mix = np.ascontiguousarray(mix[:, : sr * 20])
    voc_idx = instruments.index("vocals")

    try:
        from utils.model_utils import demix
        res = demix(config, model, torch.from_numpy(mix), dev, "scnet")
        scnet_voc = res["vocals"] if isinstance(res, dict) else res[voc_idx]
        out["scnet_voc_wav"] = wav_bytes(np.asarray(scnet_voc))
    except Exception as e:
        out["scnet_demix_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    mt = torch.from_numpy(mix)
    r2 = mt.mean(0)
    mm, ss = r2.mean(), r2.std() + 1e-8
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        ho = apply_model(ht, ((mt - mm) / ss)[None].to(dev), split=True, overlap=0.25, device=dev)[0]
    ho = ho.float() * ss + mm
    out["ht_voc_wav"] = wav_bytes(ho[list(ht.sources).index("vocals")].cpu().numpy())
    return out


@app.local_entrypoint()
def main():
    res = ab.remote()
    for k, v in res.items():
        if k.endswith("_wav"):
            p = k.replace("_wav", "") + ".wav"
            with open(p, "wb") as f:
                f.write(v)
            print(f"wrote {p} ({len(v)} bytes)")
        else:
            print(f"{k} = {v}")
