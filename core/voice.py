"""Voice agent bridge (STT/TTS) for the Waffarha chatbot.

Design: the chatbot never loads audio models itself (4GB-VRAM safe, keeps
the chatbot venv lean). All synthesis/recognition is delegated to the voice
lab service (eval/voice_eval/lab.py, default :8002), which owns model
loading, VRAM arbitration, and dialect presets. TTS model defaults to
Chatterbox-Egyptian per real-test results (reports/20260924... §10).

Endpoints (mounted on both :8000 and :8001 via voice_router):
  GET  /api/voice/status  -- enabled flag + lab reachability + active models
  POST /api/voice/stt     -- multipart audio -> {text, latency_s, model}
  POST /api/voice/tts     -- {text} -> audio bytes (mp3/wav from the lab)
"""
import asyncio
import logging
import re

import requests
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from core import config

log = logging.getLogger("waffarha.voice")

voice_router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB: ~minutes of compressed mic audio


def _lab_url(path: str) -> str:
    return config.VOICE_LAB_URL + path


def _ensure_enabled():
    if not config.VOICE_ENABLED:
        raise HTTPException(503, "Voice agent disabled (set VOICE_ENABLED=true)")


def _ar_number(n: int) -> str:
    """Minimal MSA cardinal verbalizer (0-9999): digit runs make the TTS
    sampler loop, and spoken digits are friendlier than '35%' guesswork."""
    ones = ["صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة",
            "سبعة", "ثمانية", "تسعة", "عشرة", "أحد عشر", "اثنا عشر"]
    tens = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون",
            "سبعون", "ثمانون", "تسعون"]
    if 0 <= n <= 12:
        return ones[n]
    if n < 20:
        return ones[n - 10] + " عشر"
    if n < 100:
        t, o = divmod(n, 10)
        return ones[o] + " و" + tens[t] if o else tens[t]
    if n < 1000:
        h, r = divmod(n, 100)
        head = "مئة" if h == 1 else ("مئتان" if h == 2 else ones[h] + " مئة")
        return head if not r else head + " و" + _ar_number(r)
    if n < 10000:
        th, r = divmod(n, 1000)
        head = "ألف" if th == 1 else ("ألفان" if th == 2 else _ar_number(th) + " آلاف")
        return head if not r else head + " و" + _ar_number(r)
    return str(n)


def _verbalize_numbers(t: str) -> str:
    def _rep(m):
        num = m.group(1)
        try:
            spoken = _ar_number(int(num))
        except ValueError:
            return m.group(0)
        return spoken + (" بالمئة" if m.group(2) else "")
    return re.sub(r"(\d+)(%)?", _rep, t)


def clean_for_tts(text: str) -> str:
    """Strip chat markup down to speakable plain text.

    Bot replies contain markdown, offer cards, and URLs -- none of which
    should be read aloud. Chatterbox handles Arabic + English + Arabizi
    script, so no transliteration is applied here.
    """
    if not text:
        return ""
    t = text
    # Control characters (C0/C1, incl. U+0080-U+009F that ride in with offer
    # data/LLM output) tokenize to rare ids and derail the TTS sampler into
    # repetition loops or OOB crashes. Strip them before anything else.
    t = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]", "", t)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)   # code fences
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)  # images -> alt
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # links -> label
    t = re.sub(r"https?://\S+", " ", t)             # bare URLs
    t = re.sub(r"<[^>]+>", " ", t)                  # html tags
    # emojis / pictographs / symbols: unspeakable, and they derail the
    # Chatterbox sampler into garbage or its empty-input fallback sentence.
    t = re.sub("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF"
               "\uFE00-\uFE0F\u200D\u2640-\u2642\u2190-\u21FF\u2300-\u23FF]", " ", t)
    t = re.sub(r"[*_#>`|~\-]{1,}", " ", t)          # markdown punctuation
    t = _verbalize_numbers(t)  # digits/% derail the sampler; speak them
    # Newlines are sentence boundaries the TTS chunker needs: turn them
    # into explicit stops BEFORE collapsing whitespace, or multi-sentence
    # replies fuse into one long generation that loops/crashes the sampler.
    t = t.replace("\n", ". ")
    t = re.sub(r"\s+", " ", t).strip()
    limit = config.VOICE_TTS_MAX_CHARS
    if len(t) > limit:  # cut at a sentence boundary when possible
        cut = max(t.rfind(".", 0, limit), t.rfind("؟", 0, limit),
                  t.rfind("!", 0, limit), t.rfind("،", 0, limit))
        t = t[: cut + 1 if cut > limit // 2 else limit].strip()
    return t


def _lab_post(path: str, **kwargs):
    """Blocking lab call (run in to_thread). Maps failures to HTTP errors."""
    try:
        r = requests.post(_lab_url(path), timeout=config.VOICE_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        log.warning("voice lab unreachable: %s", e)
        raise HTTPException(503, "Voice service unavailable")
    if r.status_code != 200:
        try:
            detail = r.json().get("error", r.text[:200])
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise HTTPException(502, f"Voice service error: {detail}")
    return r


@voice_router.get("/status")
async def voice_status():
    """Enabled flag + lab reachability + active models (cheap, no model load)."""
    if not config.VOICE_ENABLED:
        return {"enabled": False}
    try:
        r = await asyncio.to_thread(
            requests.get, _lab_url("/api/models"), timeout=5)
        ok = r.status_code == 200
    except requests.RequestException:
        ok = False
    return {"enabled": True, "lab_reachable": ok,
            "lab_url": config.VOICE_LAB_URL,
            "stt_model": config.VOICE_STT_MODEL,
            "tts_model": config.VOICE_TTS_MODEL}


@voice_router.post("/stt")
async def voice_stt(file: UploadFile = File(...)):
    """Mic audio -> transcribed text (then the widget submits it as chat)."""
    _ensure_enabled()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty audio upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Audio too large (10MB max)")
    r = await asyncio.to_thread(
        _lab_post, "/api/stt",
        data={"model": config.VOICE_STT_MODEL},
        files={"file": (file.filename or "mic.webm", raw,
                        file.content_type or "audio/webm")})
    out = r.json()
    out["model"] = out.get("model", config.VOICE_STT_MODEL)
    return out


@voice_router.post("/tts")
async def voice_tts(payload: dict):
    """Chat reply text -> spoken audio (Chatterbox-EG default)."""
    _ensure_enabled()
    text = clean_for_tts(str(payload.get("text", "")))
    if not text:
        raise HTTPException(400, "Nothing speakable in text")
    r = await asyncio.to_thread(
        _lab_post, "/api/tts",
        json={"model": config.VOICE_TTS_MODEL, "text": text})
    # Lab returns {"audio": "/outputs/..."}; fetch bytes and relay so the
    # widget needs only this origin (no extra CORS/port exposure).
    audio_path = (r.json().get("audio") or "")
    if not audio_path.startswith("/outputs/"):
        raise HTTPException(502, "Voice service returned no audio")
    try:
        a = await asyncio.to_thread(
            requests.get, _lab_url(audio_path), timeout=config.VOICE_TIMEOUT)
        a.raise_for_status()
    except requests.RequestException as e:
        log.warning("voice audio fetch failed: %s", e)
        raise HTTPException(502, "Voice audio fetch failed")
    ctype = "audio/mpeg" if audio_path.endswith(".mp3") else "audio/wav"
    return Response(content=a.content, media_type=ctype)
