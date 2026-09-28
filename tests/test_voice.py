"""Voice bridge tests (lab service mocked -- no models, no GPU)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core import config
from core.app import app
from core.voice import clean_for_tts


@pytest.fixture()
def voice_on(monkeypatch):
    monkeypatch.setattr(config, "VOICE_ENABLED", True)
    monkeypatch.setattr(config, "VOICE_LAB_URL", "http://lab:8002")
    monkeypatch.setattr(config, "VOICE_STT_MODEL", "fw-small")
    monkeypatch.setattr(config, "VOICE_TTS_MODEL", "chatterbox-eg")
    monkeypatch.setattr(config, "VOICE_TIMEOUT", 5)
    monkeypatch.setattr(config, "VOICE_TTS_MAX_CHARS", 600)


def test_clean_for_tts_strips_markup():
    out = clean_for_tts("**عرض** [كنتاكي](http://x) https://y.com `code` #hi")
    assert "http" not in out and "[" not in out and "**" not in out
    assert "عرض" in out and "كنتاكي" in out


def test_clean_for_tts_truncates_at_sentence():
    out = clean_for_tts("أهلا. " + "كلام كثير " * 200)
    assert len(out) <= config.VOICE_TTS_MAX_CHARS


def test_verbalize_numbers():
    out = clean_for_tts("خصم 35% والسعر 65 جنيه")
    assert "35" not in out and "%" not in out and "65" not in out
    assert "بالمئة" in out and "جنيه" in out


def test_clean_strips_control_chars():
    out = clean_for_tts("عثـ\u0084رت على عروض")
    assert "\u0084" not in out and "عروض" in out


def test_status_disabled(monkeypatch):
    monkeypatch.setattr(config, "VOICE_ENABLED", False)
    assert TestClient(app).get("/api/voice/status").json() == {"enabled": False}


def test_stt_forwards_to_lab(voice_on):
    fake = MagicMock(status_code=200)
    fake.json.return_value = {"text": "عايز بيتزا", "latency_s": 1.2}
    with patch("core.voice.requests.post", return_value=fake) as p:
        r = TestClient(app).post(
            "/api/voice/stt", files={"file": ("m.webm", b"data", "audio/webm")})
    assert r.status_code == 200 and r.json()["text"] == "عايز بيتزا"
    assert p.call_args.args[0].endswith("/api/stt")


def test_tts_relays_audio(voice_on):
    meta = MagicMock(status_code=200)
    meta.json.return_value = {"audio": "/outputs/x.wav"}
    audio = MagicMock(status_code=200, content=b"WAVE")
    audio.raise_for_status.return_value = None
    with patch("core.voice.requests.post", return_value=meta), \
         patch("core.voice.requests.get", return_value=audio):
        r = TestClient(app).post("/api/voice/tts", json={"text": "مرحبا"})
    assert r.status_code == 200 and r.content == b"WAVE"
    assert r.headers["content-type"] == "audio/wav"


def test_tts_rejects_empty(voice_on):
    r = TestClient(app).post("/api/voice/tts", json={"text": "http://x"})
    assert r.status_code == 400


def test_agent_server_has_voice_routes(voice_on):
    # fastapi>=0.141 materializes included routers at startup, so assert
    # via a live TestClient call (lab mocked) instead of app.routes.
    from core.agent_server import app as agent_app
    fake = MagicMock(status_code=200)
    fake.json.return_value = {"enabled": True, "lab_reachable": True}
    with patch("core.voice.requests.get", return_value=fake):
        r = TestClient(agent_app).get("/api/voice/status")
    assert r.status_code == 200 and r.json()["lab_reachable"] is True
