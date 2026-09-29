"""Unit tests for core.llm_providers (branch: exp/free-cloud-llm).

All HTTP is faked -- no network, no API key needed.
"""
import json
import sys
import types

import pytest

from core import config
from core import llm_providers
from core.llm_providers import (
    GeminiLLMProvider,
    OllamaLLMProvider,
    PollinationsLLMProvider,
    default_model_for,
    get_llm_provider,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, status=200, lines=None, text=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self._lines = lines or []
        self._text = text
        self.closed = False

    @property
    def text(self):
        return self._text if self._text is not None else json.dumps(self._payload)

    @property
    def content(self):
        return self.text.encode("utf-8")

    def json(self):
        return self._payload

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def close(self):
        self.closed = True


class FakeRequests:
    """Stand-in for the `requests` module: records calls, serves canned replies."""
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def post(self, url, headers=None, params=None, json=None, timeout=None,
             stream=False):
        self.calls.append({"url": url, "json": json, "params": params,
                           "stream": stream})
        return self.response

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return self.response


@pytest.fixture()
def fake_requests(monkeypatch):
    fake = FakeRequests(FakeResponse(_gen_text("hello")))
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


def _gen_text(text, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}],
                                        "role": "model"},
                            "finishReason": finish}]}


def _make_gemini(fake):
    return GeminiLLMProvider(api_key="test-key", model="gemini-2.5-flash")


# ---------------------------------------------------------------------------
# Body mapping
# ---------------------------------------------------------------------------

def test_system_and_roles_mapped(fake_requests):
    p = _make_gemini(fake_requests)
    p.chat("gemini-2.5-flash", [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Offers?"},
    ])
    body = fake_requests.calls[0]["json"]
    assert body["system_instruction"] == {"parts": [{"text": "Be brief."}]}
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "user"]


def test_consecutive_same_role_turns_merged(fake_requests):
    p = _make_gemini(fake_requests)
    p.chat("gemini-2.5-flash", [
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
    ])
    contents = fake_requests.calls[0]["json"]["contents"]
    assert len(contents) == 1
    assert [x["text"] for x in contents[0]["parts"]] == ["one", "two"]


def test_options_mapped_ollama_keys_to_gemini(fake_requests):
    p = _make_gemini(fake_requests)
    p.chat("gemini-2.5-flash", [{"role": "user", "content": "x"}],
           options={"num_predict": 200, "temperature": 0.2, "top_p": 0.9,
                    "num_ctx": 1536, "repeat_penalty": 1.1})
    gen_cfg = fake_requests.calls[0]["json"]["generationConfig"]
    assert gen_cfg == {"temperature": 0.2, "topP": 0.9, "maxOutputTokens": 200}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_nonstream_returns_ollama_shaped_dict(fake_requests):
    p = _make_gemini(fake_requests)
    resp = p.chat("gemini-2.5-flash", [{"role": "user", "content": "x"}])
    assert resp == {"message": {"content": "hello"}, "done": True}


def test_safety_block_raises(fake_requests):
    fake_requests.response = FakeResponse({"promptFeedback": {"blockReason": "SAFETY"}})
    p = _make_gemini(fake_requests)
    with pytest.raises(RuntimeError, match="safety block"):
        p.chat("gemini-2.5-flash", [{"role": "user", "content": "x"}])


def test_http_error_carries_status_and_hint(fake_requests):
    fake_requests.response = FakeResponse({"error": {"message": "not found"}}, status=404)
    p = _make_gemini(fake_requests)
    with pytest.raises(RuntimeError, match="HTTP 404.*GEMINI_MODEL"):
        p.chat("gemini-2.5-flash", [{"role": "user", "content": "x"}])


def test_stream_yields_deltas_then_done(fake_requests):
    lines = [
        'data: {"candidates": [{"content": {"parts": [{"text": "hel"}]}}]}',
        'data: {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]}',
        "",
        "event: done",
    ]
    fake_requests.response = FakeResponse(lines=lines)
    p = _make_gemini(fake_requests)
    chunks = list(p.chat("gemini-2.5-flash",
                         [{"role": "user", "content": "x"}], stream=True))
    assert [c["message"]["content"] for c in chunks[:-1]] == ["hel", "lo"]
    assert all(c["done"] is False for c in chunks[:-1])
    assert chunks[-1]["done"] is True
    assert fake_requests.calls[0]["url"].endswith(":streamGenerateContent")
    assert fake_requests.calls[0]["params"] == {"alt": "sse"}


def test_key_sent_as_header_not_url(fake_requests):
    p = _make_gemini(fake_requests)
    p.chat("gemini-2.5-flash", [{"role": "user", "content": "x"}])
    url = fake_requests.calls[0]["url"]
    assert url.endswith("models/gemini-2.5-flash:generateContent")
    assert "test-key" not in url  # key travels in x-goog-api-key, not the URL
    assert p._headers()["x-goog-api-key"] == "test-key"


# ---------------------------------------------------------------------------
# Factory / defaults
# ---------------------------------------------------------------------------

def test_factory_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_provider("watson")


def test_factory_gemini_without_key_raises(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_llm_provider("gemini")


def test_default_model_for():
    assert default_model_for("gemini") == config.GEMINI_MODEL
    assert default_model_for("ollama") == config.OLLAMA_MODEL
    assert default_model_for("??") == config.OLLAMA_MODEL


def test_ollama_provider_delegates(monkeypatch):
    calls = {}

    class FakeClient:
        def __init__(self, host=None, timeout=None):
            calls["host"] = host

        def chat(self, model=None, messages=None, options=None, stream=False):
            calls.update(model=model, messages=messages,
                         options=options, stream=stream)
            return {"message": {"content": "ok"}}

    fake_module = types.ModuleType("ollama")
    fake_module.Client = FakeClient
    monkeypatch.setitem(sys.modules, "ollama", fake_module)

    p = OllamaLLMProvider(host="http://x:11434")
    assert isinstance(p, OllamaLLMProvider)
    resp = p.chat("m", [{"role": "user", "content": "hi"}],
                  options={"temperature": 0}, stream=False)
    assert resp == {"message": {"content": "ok"}}
    assert calls["host"] == "http://x:11434"
    assert calls["model"] == "m"


def test_factory_ollama_kind(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama",
                        types.ModuleType("ollama"))
    sys.modules["ollama"].Client = lambda host=None, timeout=None: object()
    assert isinstance(get_llm_provider("ollama"), OllamaLLMProvider)
    # case-insensitive + default comes from config
    monkeypatch.setattr(config, "LLM_PROVIDER", "OLLAMA")
    assert isinstance(get_llm_provider(), OllamaLLMProvider)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")


# ---------------------------------------------------------------------------
# Pollinations (no-signup tier)
# ---------------------------------------------------------------------------

def _poll_response(text="hello"):
    return FakeResponse({"choices": [{"message": {"content": text}}]})


def test_pollinations_body_and_shape(fake_requests):
    fake_requests.response = _poll_response("hi there")
    p = PollinationsLLMProvider(model="openai")
    resp = p.chat("openai", [{"role": "system", "content": "Be brief."},
                             {"role": "user", "content": "Hello"}],
                  options={"num_predict": 50, "temperature": 0.3})
    call = fake_requests.calls[0]
    assert call["url"].endswith("/openai")
    assert call["json"]["model"] == "openai"
    assert call["json"]["max_tokens"] == 50
    assert call["json"]["temperature"] == 0.3
    assert call["json"]["messages"] == [
        {"role": "user",
         "content": "System instructions:\nBe brief.\n\nUser request: Hello"},
    ]
    assert resp == {"message": {"content": "hi there"}, "done": True}


def test_pollinations_stream_parses_done_sentinel(fake_requests):
    lines = [
        'data: {"choices": [{"delta": {"content": "hel"}}]}',
        'data: {"choices": [{"delta": {"content": "lo"}}]}',
        "data: [DONE]",
    ]
    fake_requests.response = FakeResponse(lines=lines)
    p = PollinationsLLMProvider(model="openai")
    chunks = list(p.chat("openai", [{"role": "user", "content": "x"}],
                         stream=True))
    assert [c["message"]["content"] for c in chunks[:-1]] == ["hel", "lo"]
    assert chunks[-1] == {"message": {"content": ""}, "done": True}


def test_pollinations_http_error_suggests_retry(fake_requests):
    fake_requests.response = FakeResponse({"error": "busy"}, status=500)
    p = PollinationsLLMProvider(model="openai")
    with pytest.raises(RuntimeError, match="HTTP 500.*retry"):
        p.chat("openai", [{"role": "user", "content": "x"}])


def test_factory_pollinations_kind():
    assert isinstance(get_llm_provider("pollinations"),
                      PollinationsLLMProvider)


class _OutageFake:
    """/openai 500s, legacy GET works -- mirrors the observed outage."""
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return FakeResponse({"error": "ENOSPC"}, status=500)

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return FakeResponse(status=200, text="fallback answer")


def test_pollinations_get_fallback_on_openai_outage(monkeypatch):
    fake = _OutageFake()
    monkeypatch.setitem(sys.modules, "requests", fake)
    p = PollinationsLLMProvider(model="openai")
    resp = p.chat("openai", [{"role": "system", "content": "Be brief."},
                             {"role": "user", "content": "Hello"}])
    assert resp == {"message": {"content": "fallback answer"}, "done": True}
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["POST", "GET"]
    get_params = fake.calls[1][2]
    assert get_params == {"model": "openai"}  # no separate system param
    from urllib.parse import unquote
    prompt_path = unquote(fake.calls[1][1].split(".ai/", 1)[1])
    assert "Be brief." in prompt_path  # system folded into the prompt
    assert "Hello" in prompt_path


def test_pollinations_stream_falls_back_to_single_chunk(monkeypatch):
    fake = _OutageFake()
    monkeypatch.setitem(sys.modules, "requests", fake)
    p = PollinationsLLMProvider(model="openai")
    chunks = list(p.chat("openai", [{"role": "user", "content": "x"}],
                         stream=True))
    assert [c["message"]["content"] for c in chunks] == ["fallback answer", ""]
    assert chunks[-1]["done"] is True


# ---------------------------------------------------------------------------
# UTF-8 regression: requests misdetects encoding when the server omits a
# charset (Arabic comes back as latin-1 mojibake via resp.json()/resp.text).
# Providers must decode resp.content as UTF-8 explicitly.
# ---------------------------------------------------------------------------

class _MojibakeResponse(FakeResponse):
    """Behaves like real `requests` with a missing charset: .json()/.text
    return latin-1 mojibake, .content holds the true UTF-8 bytes."""
    def _raw(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._payload, ensure_ascii=False)

    @property
    def content(self):
        return self._raw().encode("utf-8")

    @property
    def text(self):
        return self.content.decode("latin-1")

    def json(self):
        return json.loads(self.text)


def _arabic_payload(text="عروض الفطار"):
    return {"candidates": [{"content": {"parts": [{"text": text}]},
                            "finishReason": "STOP"}]}


def test_gemini_arabic_not_mojibake(fake_requests):
    fake_requests.response = _MojibakeResponse(_arabic_payload("عروض الفطار"))
    p = GeminiLLMProvider(api_key="k", model="m")
    resp = p.chat("m", [{"role": "user", "content": "x"}])
    assert resp["message"]["content"] == "عروض الفطار"


def test_gemini_stream_bytes_not_mojibake(fake_requests):
    line = ('data: {"candidates": [{"content": {"parts": '
            '[{"text": "أهلا"}]}}]}').encode("utf-8")
    fake_requests.response = FakeResponse(lines=[line])
    p = GeminiLLMProvider(api_key="k", model="m")
    chunks = list(p.chat("m", [{"role": "user", "content": "x"}], stream=True))
    assert chunks[0]["message"]["content"] == "أهلا"


def test_pollinations_arabic_not_mojibake(fake_requests):
    payload = {"choices": [{"message": {"content": "تمام يا فندم"}}]}
    fake_requests.response = _MojibakeResponse(payload)
    p = PollinationsLLMProvider(model="openai")
    resp = p.chat("openai", [{"role": "user", "content": "x"}])
    assert resp["message"]["content"] == "تمام يا فندم"
