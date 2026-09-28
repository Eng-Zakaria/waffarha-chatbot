"""Pluggable LLM providers (branch: exp/free-cloud-llm).

Every generation call in this repo goes through the same tiny contract::

    client.chat(model=..., messages=[{"role": ..., "content": ...}],
                options={...}, stream=False)

Non-streaming returns ``{"message": {"content": str}}``; streaming yields
``{"message": {"content": delta}, "done": bool}`` chunks. That is the Ollama
shape, and the providers below all speak it -- so switching the chatbot
between the local Ollama model and a free cloud model (Gemini free tier)
is a config change, not a code change::

    LLM_PROVIDER=ollama   # default, unchanged behavior (local qwen via Ollama)
    LLM_PROVIDER=gemini   # cloud, needs GEMINI_API_KEY (free from AI Studio)
    LLM_PROVIDER=pollinations  # cloud, NO signup and NO key (anonymous tier)

The cloud providers use plain HTTPS (``requests``, already a dependency) --
no extra SDK needed.
"""

import json
import logging

from core import config

log = logging.getLogger("waffarha-app")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def default_model_for(provider_kind: str) -> str:
    """Default generation model tag for a provider kind.

    Lets callers (RagEngine, compare scripts) resolve "which model" without
    hardcoding the other provider's tag -- passing an Ollama tag to Gemini
    (or vice versa) would just 404 at call time.
    """
    if (provider_kind or "").lower() == "gemini":
        return config.GEMINI_MODEL
    return config.OLLAMA_MODEL


def get_llm_provider(kind: str | None = None):
    """Factory: return the configured LLM provider instance.

    ``kind`` overrides ``config.LLM_PROVIDER`` for this instance only (used
    by eval/compare scripts to run local-vs-cloud side by side in one
    process). Unknown kinds raise ValueError immediately, not at first call.
    """
    name = (kind or config.LLM_PROVIDER or "ollama").lower()
    if name == "ollama":
        return OllamaLLMProvider()
    if name == "gemini":
        return GeminiLLMProvider()
    if name == "pollinations":
        return PollinationsLLMProvider()
    raise ValueError(
        "Unknown LLM_PROVIDER=%r. Supported: 'ollama', 'gemini', "
        "'pollinations'." % (name,)
    )


# ---------------------------------------------------------------------------
# Ollama (local) -- preserves the exact pre-branch behavior
# ---------------------------------------------------------------------------

class OllamaLLMProvider:
    """Thin wrapper over ollama.Client exposing the shared chat contract."""

    def __init__(self, host: str | None = None, timeout: int | None = None):
        from ollama import Client  # deferred: mirrors the codebase style
        self._client = Client(
            host=host or config.OLLAMA_HOST,
            timeout=timeout or config.INTENT_LLM_JUDGE_TIMEOUT,
        )

    def chat(self, model: str, messages: list, options: dict | None = None,
             stream: bool = False):
        return self._client.chat(
            model=model, messages=messages,
            options=options or {}, stream=stream,
        )


# ---------------------------------------------------------------------------
# Gemini (free cloud tier via Google AI Studio key)
# ---------------------------------------------------------------------------

class GeminiLLMProvider:
    """Gemini chat provider speaking the shared (Ollama-shaped) contract.

    Transport is the ``v1beta`` REST API with the key in the
    ``x-goog-api-key`` header (never in the URL, so it can't leak into
    logs). Only the option keys the app actually uses are mapped --
    ``num_predict`` -> ``maxOutputTokens``, ``temperature``/``top_p`` pass
    through; Ollama-only keys (``num_ctx``, ``repeat_penalty``) are ignored
    because the API has no equivalent.
    """

    def __init__(self, api_key: str | None = None,
                 model: str | None = None,
                 api_base: str | None = None,
                 timeout: float | None = None):
        import requests  # already a hard dependency (ingestion pipeline)

        self._requests = requests
        self.api_key = api_key or config.GEMINI_API_KEY
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey and set it in your "
                ".env (see .env.example), then retry."
            )
        self.model = model or config.GEMINI_MODEL
        self.api_base = (api_base or config.GEMINI_API_BASE).rstrip("/")
        self.timeout = timeout or config.GEMINI_TIMEOUT

    # -- request building -------------------------------------------------

    @staticmethod
    def _to_gemini_body(messages: list, options: dict | None) -> dict:
        system_parts: list[str] = []
        contents: list[dict] = []
        for m in messages or []:
            role = (m.get("role") or "user").lower()
            text = m.get("content") or ""
            if role == "system":
                if text:
                    system_parts.append(text)
                continue
            grole = "model" if role == "assistant" else "user"
            # Gemini rejects two consecutive same-role turns -- merge them.
            if contents and contents[-1]["role"] == grole:
                contents[-1]["parts"].append({"text": text})
            else:
                contents.append({"role": grole, "parts": [{"text": text}]})
        # Gemini also requires the FIRST turn to be from the user.
        while contents and contents[0]["role"] != "user":
            contents.pop(0)

        body: dict = {"contents": contents}
        if system_parts:
            body["system_instruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        gen_cfg: dict = {}
        options = options or {}
        if options.get("temperature") is not None:
            gen_cfg["temperature"] = float(options["temperature"])
        if options.get("top_p") is not None:
            gen_cfg["topP"] = float(options["top_p"])
        if options.get("num_predict") is not None:
            try:
                gen_cfg["maxOutputTokens"] = max(1, int(options["num_predict"]))
            except (TypeError, ValueError):
                pass
        if gen_cfg:
            body["generationConfig"] = gen_cfg
        return body

    def _url(self, action: str) -> str:
        return f"{self.api_base}/models/{self.model}:{action}"

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key,
                "Content-Type": "application/json"}

    @staticmethod
    def _error_hint(status: int) -> str:
        if status in (401, 403):
            return " (check GEMINI_API_KEY -- invalid, revoked, or restricted)"
        if status == 404:
            return (" (unknown model -- list the models your key can use at "
                    "https://generativelanguage.googleapis.com/v1beta/models "
                    "with your key, then set GEMINI_MODEL to one of them)")
        if status == 429:
            return " (free-tier quota exhausted -- wait and retry, or use a smaller model)"
        return ""

    def _raise_for_status(self, resp, action: str) -> None:
        if resp.status_code < 400:
            return
        try:
            snippet = resp.text[:400]
        except Exception:  # noqa: BLE001
            snippet = "<unreadable body>"
        raise RuntimeError(
            f"Gemini {action} failed: HTTP {resp.status_code}: {snippet}"
            f"{self._error_hint(resp.status_code)}"
        )

    # -- response parsing ---------------------------------------------------

    @staticmethod
    def _delta_text(payload: dict) -> tuple[str, bool]:
        """Extract (text, blocked) from one generateContent response object."""
        feedback = payload.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            return "", True
        text_parts = []
        candidates = payload.get("candidates") or []
        blocked = False
        if candidates:
            first = candidates[0] or {}
            if (first.get("finishReason") or "").upper() == "SAFETY":
                blocked = True
            content = first.get("content") or {}
            for part in content.get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    text_parts.append(part["text"])
        return "".join(text_parts), blocked

    # -- public contract ------------------------------------------------------

    def chat(self, model: str, messages: list, options: dict | None = None,
             stream: bool = False):
        # `model` is accepted for contract compatibility but the REST URL --
        # and therefore the model -- comes from this instance's config, so a
        # stale Ollama tag passed by a caller can't silently misroute.
        if model and model != self.model:
            log.debug("Gemini provider: caller asked for %r, using %r",
                      model, self.model)
        body = self._to_gemini_body(messages, options)
        if stream:
            return self._chat_stream(body)
        resp = self._requests.post(
            self._url("generateContent"), headers=self._headers(),
            json=body, timeout=self.timeout,
        )
        self._raise_for_status(resp, "generateContent")
        text, blocked = self._delta_text(resp.json())
        if blocked:
            raise RuntimeError(
                "Gemini refused the prompt (safety block, no candidates). "
                "Try rephrasing without the blocked span."
            )
        return {"message": {"content": text}, "done": True}

    def _chat_stream(self, body: dict):
        resp = self._requests.post(
            self._url("streamGenerateContent"), headers=self._headers(),
            params={"alt": "sse"}, json=body, timeout=self.timeout,
            stream=True,
        )
        self._raise_for_status(resp, "streamGenerateContent")
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                text, blocked = self._delta_text(payload)
                if blocked:
                    raise RuntimeError(
                        "Gemini stopped the stream (safety block)."
                    )
                if text:
                    yield {"message": {"content": text}, "done": False}
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        yield {"message": {"content": ""}, "done": True}


# ---------------------------------------------------------------------------
# Pollinations (free, NO signup, NO api key -- anonymous tier)
# ---------------------------------------------------------------------------

class PollinationsLLMProvider:
    """pollinations.ai OpenAI-compatible endpoint, same shared contract.

    Totally free with no registration: no key, no account. Trade-offs to
    know before trusting it: anonymous rate limits, occasional 5xx / slow
    cold starts, and the model roster changes over time (POLLINATIONS_MODEL
    default "openai" tracks their default model alias). Good for
    zero-friction testing; not for production.
    """

    def __init__(self, model: str | None = None,
                 api_base: str | None = None,
                 timeout: float | None = None):
        import requests  # already a hard dependency (ingestion pipeline)

        self._requests = requests
        self.model = model or config.POLLINATIONS_MODEL
        self.api_base = (api_base or config.POLLINATIONS_API_BASE).rstrip("/")
        self.timeout = timeout or config.POLLINATIONS_TIMEOUT

    def _url(self) -> str:
        return f"{self.api_base}/openai"

    def _payload(self, model: str, messages: list,
                 options: dict | None, stream: bool) -> dict:
        if model and model != self.model:
            log.debug("Pollinations provider: caller asked for %r, using %r",
                      model, self.model)
        # The anonymous tier currently 500s on requests carrying a separate
        # `system` role (observed ENOSPC) -- fold system instructions into the
        # first user turn instead. Semantically equivalent for our prompts.
        system_txt = "\n\n".join(
            (m.get("content") or "")
            for m in messages or []
            if (m.get("role") or "").lower() == "system" and m.get("content")
        )
        adapted = [{"role": (m.get("role") or "user").lower(),
                    "content": m.get("content") or ""}
                   for m in (messages or [])
                   if (m.get("role") or "").lower() != "system"]
        if system_txt:
            prefix = f"System instructions:\n{system_txt}"
            if adapted and adapted[0]["role"] == "user":
                adapted[0] = {"role": "user",
                              "content": f"{prefix}\n\nUser request: "
                                         f"{adapted[0]['content']}"}
            else:
                adapted.insert(0, {"role": "user", "content": prefix})
        payload: dict = {
            "model": self.model,
            "messages": adapted,
            "stream": stream,
        }
        options = options or {}
        if options.get("num_predict") is not None:
            try:
                payload["max_tokens"] = max(1, int(options["num_predict"]))
            except (TypeError, ValueError):
                pass
        if options.get("temperature") is not None:
            payload["temperature"] = float(options["temperature"])
        if options.get("top_p") is not None:
            payload["top_p"] = float(options["top_p"])
        return payload

    def _raise_for_status(self, resp) -> None:
        if resp.status_code < 400:
            return
        try:
            snippet = resp.text[:300]
        except Exception:  # noqa: BLE001
            snippet = "<unreadable body>"
        raise RuntimeError(
            f"Pollinations request failed: HTTP {resp.status_code}: {snippet} "
            "(anonymous tier -- retry shortly; if it persists the service "
            "may be down or the model alias retired, try another "
            "POLLINATIONS_MODEL)"
        )

    def _get_fallback(self, messages: list) -> str:
        """Legacy GET endpoint (proven alive when /openai 500s).

        Folds the conversation -- system instructions included, since the
        endpoint also rejects a separate `system` param right now -- into one
        prompt string. Returns plain text.
        """
        from urllib.parse import quote

        system_txt = "\n\n".join(
            (m.get("content") or "")
            for m in messages or []
            if (m.get("role") or "").lower() == "system" and m.get("content")
        )
        turns = []
        if system_txt:
            turns.append(f"System instructions:\n{system_txt}")
        for m in messages or []:
            role = (m.get("role") or "user").lower()
            if role == "system":
                continue
            who = "Assistant" if role == "assistant" else "User"
            turns.append(f"{who}: {m.get('content') or ''}")
        url = f"{self.api_base}/{quote(chr(10).join(turns) or 'Hello', safe='')}"
        resp = self._requests.get(url, params={"model": self.model},
                                  timeout=self.timeout)
        self._raise_for_status(resp)
        return (resp.text or "").strip()

    def chat(self, model: str, messages: list, options: dict | None = None,
             stream: bool = False):
        payload = self._payload(model, messages, options, stream)
        if stream:
            return self._chat_stream(payload, messages)
        resp = self._requests.post(self._url(), json=payload,
                                   timeout=self.timeout)
        if resp.status_code >= 500:
            # Service-side outage on /openai (observed: ENOSPC 500s) --
            # degrade to the legacy GET endpoint instead of failing.
            log.warning("Pollinations /openai HTTP %s, using GET fallback",
                        resp.status_code)
            return {"message": {"content": self._get_fallback(messages)},
                    "done": True}
        self._raise_for_status(resp)
        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(
                f"Pollinations returned an unexpected body: "
                f"{resp.text[:300]!r} ({e})"
            )
        return {"message": {"content": text}, "done": True}

    def _chat_stream(self, payload: dict, messages: list):
        resp = self._requests.post(self._url(), json=payload,
                                   timeout=self.timeout, stream=True)
        if resp.status_code >= 500:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            log.warning("Pollinations /openai HTTP %s, using GET fallback",
                        resp.status_code)
            yield {"message": {"content": self._get_fallback(messages)},
                   "done": False}
            yield {"message": {"content": ""}, "done": True}
            return
        self._raise_for_status(resp)
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload_obj = json.loads(data)
                except ValueError:
                    continue
                try:
                    delta = (payload_obj["choices"][0].get("delta") or {}) \
                        .get("content") or ""
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield {"message": {"content": delta}, "done": False}
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        yield {"message": {"content": ""}, "done": True}
