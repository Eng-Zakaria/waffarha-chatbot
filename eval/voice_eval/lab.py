"""Waffarha Voice Lab — EG/SA TTS+STT test bench on :8002.
Lazy-loads one model at a time (4GB VRAM safe). Run: python lab.py
"""
import io, json, os, time, wave
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
AUDIO = BASE / "audio"; AUDIO.mkdir(exist_ok=True)
OUT = BASE / "outputs"; OUT.mkdir(exist_ok=True)
W = BASE / "weights"; W.mkdir(exist_ok=True)
NOTES = BASE / "notes.jsonl"
CORPUS = json.loads((BASE / "corpus_eg_sa.json").read_text(encoding="utf-8"))["sentences"]

ESPEAK_DIR = BASE / "espeak-ng" / "SourceDir" / "eSpeak NG"
os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", str(ESPEAK_DIR / "libespeak-ng.dll"))
os.environ.setdefault("ESPEAKNG_DATA_PATH", str(ESPEAK_DIR / "espeak-ng-data"))
os.environ["PATH"] = str(ESPEAK_DIR) + os.pathsep + os.environ["PATH"]
os.environ.setdefault("PYTHONUTF8", "1")


def _local(slug, repo):
    d = W / slug
    return str(d) if (d.exists() and any(d.iterdir())) else repo

app = FastAPI(title="Waffarha Voice Lab (EG/SA)")

MODELS = [
    # STT — ready / downloadable
    {"id": "fw-small", "kind": "stt", "label": "Faster-Whisper small (baseline)", "dialects": ["msa", "eg", "sa"], "status": "ready", "note": "installed, int8, fast on 1650Ti"},
    {"id": "qwen3-asr-06", "kind": "stt", "label": "Qwen3-ASR 0.6B (open SOTA)", "dialects": ["msa", "eg", "sa"], "status": "ready", "note": "local weights/ or HF; Arabic=ar generic; fits 4GB"},
    {"id": "whisper-ar-dialect", "kind": "stt", "label": "Whisper-small Arabic dialectal (oddadmix)", "dialects": ["eg", "sa", "lev", "maghrebi"], "status": "ready", "note": "local weights/ or HF oddadmix/whisper-small-arabic-dialectal"},
    {"id": "whisper-medium-egy", "kind": "stt", "label": "Whisper-medium Egyptian (MAdel121)", "dialects": ["eg"], "status": "ready", "note": "local weights/ or HF MAdel121/whisper-medium-egy; EG only"},
    # TTS — instant cloud baseline + local on-demand
    {"id": "edge-eg-salma", "kind": "tts", "label": "Edge ar-EG-Salma (EG female, instant)", "dialects": ["eg"], "status": "ready", "note": "no GPU, good EG reference"},
    {"id": "edge-eg-shakir", "kind": "tts", "label": "Edge ar-EG-Shakir (EG male, instant)", "dialects": ["eg"], "status": "ready", "note": "no GPU"},
    {"id": "edge-sa-hamed", "kind": "tts", "label": "Edge ar-SA-Hamed (SA male, instant)", "dialects": ["sa"], "status": "ready", "note": "Najdi-ish MSA"},
    {"id": "edge-sa-zariyah", "kind": "tts", "label": "Edge ar-SA-Zariyah (SA female, instant)", "dialects": ["sa"], "status": "ready", "note": "no GPU"},
    {"id": "qwen3-tts-06", "kind": "tts", "label": "Qwen3-TTS 0.6B Base (clone test, NO official Arabic)", "dialects": ["en"], "status": "ready", "note": "local weights/ or HF download; Arabic unsupported — clone experiment only"},
    {"id": "nabra-82m", "kind": "tts", "label": "Nabra-82M (open MSA only, 1 voice)", "dialects": ["msa"], "status": "ready", "note": "82M Kokoro-AR + local espeak + camel diacritizer; MSA only, needs tashkeel ideally"},
    {"id": "chatterbox-eg", "kind": "tts", "label": "Chatterbox-Egyptian (open EG Masri)", "dialects": ["eg"], "status": "ready", "note": "oddadmix/chatterbox-egyptian-v0 — EG target for Waffarha"},
    {"id": "lahgtna", "kind": "tts", "label": "Lahgtna (open EG/SA/MA/IQ)", "dialects": ["eg", "sa"], "status": "ready", "note": "oddadmix multi-dialect, EG+SA in one model (experimental wiring)"},
    # Commercial — need API key, slots for your notes
    {"id": "nabrah-api", "kind": "both", "label": "Nabrah.ai (SA commercial, key needed)", "dialects": ["sa"], "status": "needs-key", "note": "paste key in UI later"},
    {"id": "silma-v2", "kind": "tts", "label": "SILMA TTS v2 (Najdi, key needed)", "dialects": ["sa"], "status": "needs-key", "note": "~170ms TTFT claim"},
    {"id": "elevenlabs", "kind": "both", "label": "ElevenLabs (key needed)", "dialects": ["eg", "sa"], "status": "needs-key", "note": "Scribe STT + Flash TTS"},
    {"id": "munsit", "kind": "both", "label": "Munsit Faseeh (25+ dialects, key needed)", "dialects": ["eg", "sa"], "status": "needs-key", "note": "STT+TTS sovereign option"},
]

_cache = {}  # one loaded model at a time per family

@app.get("/api/models")
def models():
    return {"models": MODELS}

@app.get("/api/corpus")
def corpus():
    return {"sentences": CORPUS}

@app.get("/api/notes")
def notes():
    if not NOTES.exists():
        return {"notes": []}
    rows = [json.loads(l) for l in NOTES.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"notes": rows[-100:]}

@app.post("/api/note")
async def note(payload: dict):
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(NOTES, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"ok": True}

# ---------- STT ----------
def _fw(model_size="small"):
    from faster_whisper import WhisperModel
    key = f"fw-{model_size}"
    if key not in _cache:
        _unload_prefix("fw-"); _unload_prefix("qwen3-asr"); _unload_prefix("hf-whisper")
        _cache[key] = WhisperModel(model_size, device="cuda", compute_type="int8")
    return _cache[key]

def _unload_prefix(p):
    for k in [k for k in _cache if k.startswith(p)]:
        del _cache[k]
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

@app.post("/api/stt")
async def stt(model: str = Form("fw-small"), file: UploadFile = File(...)):
    t0 = time.time()
    raw = await file.read()
    tmp = OUT / f"stt_in_{int(t0)}.wav"
    tmp.write_bytes(raw)
    try:
        if model == "fw-small":
            m = _fw("small")
            segs, _ = m.transcribe(str(tmp), language="ar")
            text = " ".join(s.text for s in segs).strip()
        elif model == "qwen3-asr-06":
            import torch
            from qwen_asr import Qwen3ASRModel
            key = "qwen3-asr-06"
            if key not in _cache:
                _unload_prefix("fw-"); _unload_prefix("qwen3-asr"); _unload_prefix("hf-whisper")
                _cache[key] = Qwen3ASRModel.from_pretrained(
                    _local("qwen3-asr-06", "Qwen/Qwen3-ASR-0.6B"), dtype=torch.float16, device_map="cuda:0",
                    max_inference_batch_size=8, max_new_tokens=256)
            r = _cache[key].transcribe(audio=str(tmp), language=None)
            text = r[0].text if r else ""
        elif model in ("whisper-ar-dialect", "whisper-medium-egy"):
            from transformers import pipeline
            repo = _local({"whisper-ar-dialect": "whisper-ar-dialect",
                             "whisper-medium-egy": "whisper-medium-egy"}[model],
                            {"whisper-ar-dialect": "oddadmix/whisper-small-arabic-dialectal",
                             "whisper-medium-egy": "MAdel121/whisper-medium-egy"}[model])
            key = f"hf-whisper-{model}"
            if key not in _cache:
                _unload_prefix("fw-"); _unload_prefix("qwen3-asr"); _unload_prefix("hf-whisper")
                _cache[key] = pipeline("automatic-speech-recognition", model=repo, device=0)
            text = _cache[key](str(tmp), generate_kwargs={"language": "arabic", "task": "transcribe"})["text"]
        else:
            return JSONResponse({"error": f"model {model} needs key/setup — see registry note"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    dt = round(time.time() - t0, 2)
    return {"text": text, "latency_s": dt, "model": model}

def _cb_load(ckpt, dev):
    # like ChatterboxMultilingualTTS.from_local but tolerant of
    # oddadmix checkpoints missing s3gen buffers (tokenizer.window)
    from pathlib import Path as _P
    from safetensors.torch import load_file as _ls
    from chatterbox.models.t3 import T3
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.tokenizers import MTLTokenizer
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    import chatterbox.mtl_tts as _m
    import torch as _t
    ckpt = _P(ckpt)
    map_location = None
    ve = VoiceEncoder()
    ve.load_state_dict(_t.load(ckpt / "ve.pt", map_location=map_location, weights_only=True))
    ve.to(dev).eval()
    t3 = T3(T3Config.multilingual())
    t3_state = _ls(ckpt / "t3_mtl23ls_v2.safetensors")
    if "model" in t3_state.keys():
        t3_state = t3_state["model"][0]
    t3.load_state_dict(t3_state)
    t3.to(dev).eval()
    s3gen = S3Gen()
    s3gen.load_state_dict(_t.load(ckpt / "s3gen.pt", map_location=map_location, weights_only=True), strict=False)
    s3gen.to(dev).eval()
    tokenizer = MTLTokenizer(str(ckpt / "grapheme_mtl_merged_expanded_v1.json"))
    conds = None
    if (ckpt / "conds.pt").exists():
        conds = _m.Conditionals.load(ckpt / "conds.pt", map_location=map_location).to(dev)
    return ChatterboxMultilingualTTS(t3, s3gen, ve, tokenizer, dev, conds=conds)


# ---------- TTS ----------
@app.post("/api/tts")
async def tts(payload: dict):
    model, text = payload.get("model", ""), payload.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    t0 = time.time()
    if model.startswith("edge-"):
        try:
            import edge_tts
        except ImportError:
            return JSONResponse({"error": "pip install edge-tts"}, status_code=400)
        voice = {"edge-eg-salma": "ar-EG-SalmaNeural", "edge-eg-shakir": "ar-EG-ShakirNeural",
                 "edge-sa-hamed": "ar-SA-HamedNeural", "edge-sa-zariyah": "ar-SA-ZariyahNeural"}[model]
        out = OUT / f"tts_{model}_{int(t0)}.mp3"
        await edge_tts.Communicate(text, voice).save(str(out))
        return {"audio": f"/outputs/{out.name}", "latency_s": round(time.time() - t0, 2), "model": model}
    if model == "qwen3-tts-06":
        try:
            import torch, soundfile as sf
            from qwen_tts import Qwen3TTSModel
        except ImportError:
            return JSONResponse({"error": "pip install qwen-tts (+flash-attn optional)"}, status_code=400)
        key = "qwen3-tts-06"
        if key not in _cache:
            _cache[key] = Qwen3TTSModel.from_pretrained(_local("qwen3-tts-06", "Qwen/Qwen3-TTS-12Hz-0.6B-Base"), device_map="cuda:0", dtype=torch.float16)
        wavs, sr = _cache[key].generate_voice_clone(text=text, language="English",
            ref_audio="https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-TTS-Repo/clone.wav",
            ref_text="Okay. Yeah. I resent you. I love you. I respect you.")
        import numpy as np
        out = OUT / f"tts_{model}_{int(time.time())}.wav"
        sf.write(str(out), np.concatenate([w.detach().cpu().numpy() for w in wavs]), sr)
        return {"audio": f"/outputs/{out.name}", "latency_s": round(time.time() - t0, 2), "model": model,
                "warn": "no official Arabic support — clone experiment only"}
    if model in ("chatterbox-eg", "lahgtna"):
        # Synthesize in a disposable worker process: a T3 sampler assert
        # kills only the worker, never the lab's CUDA context (see §10).
        try:
            import subprocess as _sp
            import sys as _sys
            slug = "chatterbox-eg" if model == "chatterbox-eg" else "lahgtna"
            repo = "oddadmix/chatterbox-egyptian-v0" if model == "chatterbox-eg" else "oddadmix/lahgtna-chatterbox-v1"
            ckpt = _local(slug, repo)
            out = OUT / f"tts_{model}_{int(time.time())}.wav"
            proc = _sp.run([_sys.executable, str(BASE / "cb_worker.py"), ckpt, text, str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900)
            if proc.returncode != 0:
                tail = (proc.stderr or "")[-500:]
                if proc.returncode == 2:
                    return JSONResponse({"error": "no speakable content after cleanup"}, status_code=400)
                return JSONResponse({"error": f"tts worker crashed: {tail}"}, status_code=500)
            return {"audio": f"/outputs/{out.name}", "latency_s": round(time.time() - t0, 2),
                    "model": model, "worker": (proc.stdout or "").strip()[-200:]}
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    if model == "nabra-82m":
        try:
            r = _nabra_synth(text)
            return {"audio": r[0], "latency_s": r[1], "model": model, "diac": r[2]}
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    return JSONResponse({"error": "commercial model — paste API key flow not wired yet; log notes manually"}, status_code=400)

# ---------- Nabra-82M (Kokoro-AR fork + local espeak + camel diacritizer) ----------
def _nabra_synth(text):
    import time as _t
    t0 = _t.time()
    import numpy as np, torch, soundfile as sf
    from huggingface_hub import list_repo_files  # noqa
    slug = _local("nabra-82m", "oddadmix/Nabra-82M-v0.1")
    if slug.startswith("oddadmix"):
        from huggingface_hub import snapshot_download
        slug = snapshot_download(repo_id=slug)
    import sys
    sys.path.insert(0, str(BASE))
    from arabic_g2p import ArabicG2P, EXTRA_SYMBOLS, clean_phonemes, normalize_text
    from kokoro import KModel, KPipeline
    from kokoro import pipeline as kpipeline_mod
    files = list(Path(slug).iterdir())
    model_path = str(next(f for f in files if f.suffix == ".pth"))
    voice_path = str(next(f for f in files if f.suffix == ".pt"))
    config = str(next(f for f in files if f.name == "config.json"))
    key = "nabra-pipe"
    if key not in _cache:
        kmodel = KModel(repo_id=slug, config=config, model=model_path, disable_complex=True).eval()
        kmodel.vocab.update(EXTRA_SYMBOLS)
        kpipeline_mod.LANG_CODES.setdefault("ar", "ar")
        pipe = KPipeline(lang_code="ar", repo_id=slug, model=kmodel)
        _orig = pipe.g2p

        def _g2p(t):
            # fork expects misaki>=0.9.4 (tuple); we have 0.7.4 (plain str)
            r = _orig(t)
            ph = r[0] if isinstance(r, tuple) else r
            return clean_phonemes(ph), t

        pipe.g2p = _g2p
        voice = torch.load(voice_path, map_location="cpu", weights_only=True)
        _cache[key] = (pipe, voice)
    else:
        pipe, voice = _cache[key]
    try:
        g2p = ArabicG2P(diacritize=True)
        tnorm, _ = normalize_text(text)
        diac = g2p.diacritize(tnorm)
        used = "camel-mle"
    except Exception:
        diac, used = text, "raw(no-camel-data-yet)"
    audios = [a for _, _, a in pipe(diac, voice=voice, speed=1.0)]
    wav = np.concatenate([a.detach().cpu().numpy() for a in audios]).astype(np.float32)
    out = OUT / f"tts_nabra-82m_{int(t0)}.wav"
    sf.write(str(out), wav, 24000)
    return f"/outputs/{out.name}", round(_t.time() - t0, 2), used


app.mount("/outputs", StaticFiles(directory=str(OUT)), name="outputs")
app.mount("/", StaticFiles(directory=str(BASE / "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
