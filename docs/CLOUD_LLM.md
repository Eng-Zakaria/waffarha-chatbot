# Free cloud LLM (Gemini) — testing & local-vs-cloud comparison

Branch: `exp/free-cloud-llm`. Everything on this branch is **testing-only**:
it lets the chatbot generate with a free cloud model instead of (or next to)
the local Ollama model, so you can compare quality/latency before spending
anything. Retrieval, embeddings, the index, and all guardrails are unchanged
— only the *generation backend* switches.

## How it works

All LLM calls (`RagEngine`, agent planner, intent judge) go through one
small contract — `client.chat(model, messages, options, stream)` returning
Ollama-shaped dicts. `core/llm_providers.py` implements it twice:

- `OllamaLLMProvider` — local model, exactly the old behavior (default).
- `GeminiLLMProvider` — Google Gemini REST API over plain HTTPS
  (`requests`, already a dependency — no new SDK to install).

`LLM_PROVIDER` in `.env` picks the backend. One switch flips every call
site; embeddings and retrieval stay local either way. Supported values:
`ollama` (default), `gemini`, `pollinations`.

## No-signup option: pollinations

`LLM_PROVIDER=pollinations` needs **no key and no account** — it uses the
pollinations.ai anonymous tier (`POLLINATIONS_MODEL`, default `openai`).
Verified working, but with honest caveats observed while building this
branch: the anonymous tier is flaky (intermittent HTTP 500/402 from some
networks), only the `openai` alias works anonymously (the old `mistral` /
`qwen` / `deepseek` / `llama` aliases are retired), and requests carrying a
separate `system` role fail — the provider folds system instructions into
the first user turn and falls back to the legacy GET endpoint on `/openai`
outages automatically. Fine for zero-friction smoke tests; prefer Gemini
(free key, much more reliable) for any serious comparison.

## Setup (free, ~2 minutes)

1. Get a free API key at **https://aistudio.google.com/apikey**
   (Google AI Studio; free tier includes a daily request allowance — enough
   for testing and eval runs, $0).
2. Add to your `.env`:
   ```ini
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=AIza...your-key...
   # GEMINI_MODEL=gemini-2.5-flash   # default; see below
   ```
3. Run the server as usual (`python -m core.app` / docker / `agent_server`).
   To switch back: `LLM_PROVIDER=ollama` (or delete the line).

## Picking a model

- `gemini-2.5-flash` (default) — best quality on the free tier.
- `gemini-2.5-flash-lite` — faster/cheaper, slightly weaker; good for
  high-volume eval sweeps.
- Set any other current name via `GEMINI_MODEL=...`.

If the API answers **404**, the model name changed or your key can't use
it — the error message tells you how to list the models available to your
key, then set `GEMINI_MODEL` to one of them.

## Comparing local vs cloud

```bash
# needs GEMINI_API_KEY + a built index (same requirements as the server)
python eval/compare_local_vs_cloud.py
python eval/compare_local_vs_cloud.py --limit 10 --out eval/my_run.json
python eval/compare_local_vs_cloud.py --queries-file eval/my_queries.json
python eval/compare_local_vs_cloud.py --no-retrieval   # pure LLM-vs-LLM, no RAG
```

Runs the same queries through both backends over the **same index** and
writes `eval/local_vs_cloud_<timestamp>.json` with both answers, per-query
latencies, and errors. The intent-judge deliberately stays on Ollama for
both runs so differences come from generation, not judging. Startup loads
the embedding model twice (~2x RAM/time) — normal for this script.

Suggested comparison angles: Arabic dialect quality, price/merchant factuality
against retrieved context, follow-up resolution, and tokens/sec.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `GEMINI_API_KEY is not set` | key missing from env/`.env` |
| `HTTP 401/403` | invalid, revoked, or HTTP-restricted key |
| `HTTP 404` | wrong `GEMINI_MODEL` for your key (see above) |
| `HTTP 429` | free-tier quota exhausted — wait, retry, or use flash-lite |
| Safety-block error | Gemini refused that prompt span — rephrase |
| Arabic comes back as `Ù„ÙŠØ³`-style mojibake | encoding-guess bug in the HTTP layer — fixed in `core/llm_providers.py` (`_parse_json_body`/`_decode_line` decode `resp.content` as UTF-8 explicitly); if you see it again, the response path bypassed those helpers |
| Everything falls back to rules | cloud failing → call sites catch and degrade, check logs |

## Notes / limits of this branch

- One global switch: `LLM_PROVIDER=gemini` also sends the agent planner
  and intent judge to Gemini. (The compare script overrides per-engine, so
  mixed setups are possible there via `RagEngine(llm_provider=...)`.)
- `num_ctx`/`repeat_penalty` have no Gemini equivalent and are ignored;
  `num_predict` → `maxOutputTokens`, `temperature`/`top_p` pass through.
- Don't put `GEMINI_API_KEY` in git — `.env` is gitignored, `.env.example`
  only documents the variable names.
