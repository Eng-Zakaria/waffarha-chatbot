# Waffarha Assistant — RAG-Powered Chatbot

Bilingual (Arabic/English, incl. Franco) customer-support chat for [Waffarha](https://waffarha.com), Egypt's deals platform. Retrieves real offers/FAQs from a vector index and live ClickHouse data, and answers personal queries only under a trusted identity.

Two engines run side-by-side in the same dev process:

| Port | Engine | Stack |
|------|--------|-------|
| **8000** | `RagEngine` (legacy cascade) | deterministic gates → intent → retrieval → direct answers → LLM fallback |
| **8001** | `AgentEngine` (agentic layer) | safety gate → planner (single LLM call) → typed tools → evidence gate → deterministic rendering |

Both expose the same widget and the same `/api/chat` + `/api/chat/stream` (SSE) endpoints, so the front-end is agnostic to which engine serves it.

---

## Quick Start (fresh machine)

One-command setup (checks what exists first, fills only the gaps — Python
deps, `.env`, Ollama binary/server/model, index + port readiness):

```powershell
# Windows — venv is the default; system libs via -Mode system -Yes
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 -Run   # setup + start :8000/:8001
```

```bash
# Linux/macOS — venv is the default; system libs via --system --yes
bash setup_linux.sh
bash setup_linux.sh --run   # setup + start :8000/:8001
```

Then open http://localhost:8000 and http://localhost:8001. First message takes
~30–60s (model load); later turns are fast.

### Index updates (fetch + rebuild)

`data/index/` is gitignored, so a fresh clone either copies it in or rebuilds
(stop the servers first — Qdrant holds a folder lock while running):

```powershell
.\update_index.ps1 -Plan         # dry-run lineage report (no ClickHouse, no writes)
.\update_index.ps1               # full: fetch from ClickHouse + rebuild (needs CLICKHOUSE_* in .env)
.\update_index.ps1 -SkipFetch    # rebuild from existing data/ files (no ClickHouse)
```

```bash
bash update_index.sh --plan
bash update_index.sh
bash update_index.sh --skip-fetch
```

### Manual setup (fallback, no scripts)

```bash
# 1. Services
ollama serve                                   # LLM (qwen2.5:3b-instruct); pulls on demand
docker run -d -p 6379:6379 redis:7-alpine      # optional if MEMORY_BACKEND=local

# 2. Environment
python -m venv venv && venv\Scripts\activate   # Windows (source venv/bin/activate on *nix)
pip install -r requirements.txt
copy .env.example .env                         # edit as needed

# 3. Index (first time / when data changes)
python ingestion/refresh.py                    # fetch from ClickHouse + build the vector index

# 4. Run BOTH engines at once
python run_servers.py                          # old cascade :8000, agent :8001
```

> `core/app.py` (`uvicorn app:app --port 8000`) and `core/agent_server.py`
> (`uvicorn core.agent_server:app --port 8001`) are the single-engine entry
> points — `run_servers.py` shares one embedding model + one Qdrant lock
> across both.

---

## Architecture

```
static/index.html  (vanilla-JS widget, no build step)
        │ POST /api/chat[+/stream] (SSE)
        ▼
┌─────────────────────────────── FastAPI ───────────────────────────────┐
│  core/app.py  (:8000)  ragged    core/agent_server.py  (:8001) bandit │
└───────────────────────────────┬───────────────────────────────────────┘
        ┌───────────────────────┴────────────────────────
        ▼  RagEngine (core/rag_engine.py)      |   AgentEngine (agent/engine.py)
  1. deterministic gates:                       1. SAFETY GATE (greeting/gibberish/
     greeting / gibberish / injection             injection short-circuits + arabizi)
  2. intent classification                     2. PLANNER: one structured JSON plan
  3. retrieval (vector + BM25 RRF)               3. REFERENCE PASS (deterministic):
  4. direct answers (FAQ/offer/stock)              resolve follow-ups vs stored offers
  5. price-span / entity filters                4. EXECUTE: typed tools
  6. LLM fallback (grounded)                       • search_offers / get_offer / catalog
                                                5. EVIDENCE GATE (deterministic)
                                                6. relax / bounded replan (max budget)
                                                7. deterministic grounded rendering
        └──────────────┬───────────────────────────────┘
                       ▼
        vectorstores/ (FAISS/Qdrant/…) · bm25_store.py
        catalog/ · personal/ (ClickHouse) · memory/ (Redis/local)
```

Design rule that binds both paths: **deterministic business logic is
authoritative**; the LLM may plan/select, but every surfaced answer is
re-derivable from tool/database evidence — no freeform generation in the
agent path, and the legacy path grounds its fallback in retrieved docs.

---

## Module Map

| Path | Responsibility |
|------|----------------|
| `core/` | `rag_engine.py` (retrieval, direct answers, deterministic gates & greets), `app.py` (FastAPI :8000), `agent_server.py` (FastAPI :8001), `config.py`, `identity.py`, `rag_perfection.py` (arabizi, guardrails) |
| `agent/` | `engine.py` (per-turn bounded loop), `planner.py` (single LLM decision), `grounding.py` (corroborate entities), `reference.py` (resolve follow-ups), `facade.py` (writer interface over RagEngine), `state.py` (typed state + safe trace), `tools/` (catalog_tools, cascade_tools) |
| `vectorstores/` | Vector backends (FAISS/Chroma/Qdrant/LanceDB/pgvector) + BM25 |
| `catalog/` | Faceted in-memory catalog + ClickHouse catalog queries |
| `personal/` | ClickHouse personal queries (coupons, orders) — identity-gated |
| `memory/` | Redis/local session memory (shown offers, turns) |
| `session/` | Session manager (catalog comparison context) |
| `ingestion/` | `refresh.py` (fetch + build, `--plan`), loaders, ClickHouse source fetchers |
| `eval/` | `queries.json` suite + `run_eval.py` (CI exit codes), comparators |
| `tests/` | `unit/`, `integration/`, `conftest.py`, `manual_agent.py`, `stage4_agent_eval.py` |
| `static/` | Chat widget (served by both apps) |
| `run_servers.py` | Both engines in one process (shared model, one Qdrant lock) |

---

## Agentic Layer (`agent/`)

The agent *orchestrates* the existing deterministic capabilities behind a
registry of safe, typed tools; it is **not** a free-form agent.

Pipeline per turn: `understanding → plan → tool → observe/validate →
(relax | replan | clarify) → grounded response`.

- The **planner is the only unstructured LLM call** per turn; output is one
  JSON object with tool selection + args + goal + entities + references.
  A bad plan degrades to the deterministic fallback, never an unbounded loop
  (`MAX_AGENT_LLM_CALLS` / `MAX_AGENT_TOOL_CALLS` enforced). Planner output
  is never the user-facing text.
- **Reference pass** corroborates every follow-up claim ("ده", "التاني",
  "أرخص من ده") against the *stored last-shown offers* of the session —
  never freeform history. Unconfirmable references are dropped.
- **Evidence gate** rejects tool results that don't satisfy the plan's
  constraints (content type, price span, counts), then relaxes (price widen /
  no-match broaden) or replans **once** with the observation as plain context.
- **No chain-of-thought is exposed**: the per-turn trace carries only
  structured decisions (goal, constraints, tool selection, evidence summary,
  next action) — safe for logs and the UI.
- **Tools** (`agent/tools/`): `search_offers`, `get_offer`, `retrieve_faq`,
  `compare_offers`, `superlative_offer`, `catalog`, plus the cascade tools
  re-exposing the old engine's grounded text answers. All data access flows
  through the facade/FacetedCatalog; no tool touches SQL, the filesystem, or
  arbitrary HTTP.

Since 2026-09, the safety gate gained a **low-signal backstop**: inputs with
no corroborated entity and no offer/browse topic signal (`"w"`, `"asdf"`,
unknown greetings, ambiguous small talk) are clarified instead of triggering
a silent full-catalog dump; the greeting phrase list was expanded for the
same reason (`core/rag_engine.py`, `agent/planner.py`, `agent/engine.py`).

---

## Configuration & Data

All runtime settings via env vars (see `core/config.py`). Highlights:

| Variable | Default | Notes |
|----------|---------|-------|
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | must match the built index |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | LLM for planner/fallback |
| `VECTOR_STORE_BACKEND` | `qdrant` | faiss \| chroma \| qdrant \| lancedb \| pgvector |
| `MIN_RELEVANCE_SCORE` / `_STRICT` | `0.30` / `0.45` | soft / hard refusal floors |
| `DIRECT_ANSWER_MIN_EMBEDDING_SCORE` | `0.55` | unbonused floor before direct answers fire |
| `MEMORY_BACKEND` | `redis` | `local` for dev without Redis |
| `IDENTITY_BACKEND` | `static` | `auth` is the production-safe option |
| `AUTH_SIGNING_SECRET` | *(empty)* | empty ⇒ personal queries refused |
| `PERSONAL_QUERIES_ENABLED` | `false` | only with a trusted identity backend |
| `AGENT_MODEL` / `MAX_AGENT_*_CALLS` | — | agent planner model + budgets |

Data files land in `data/` (`offers_raw.json`, `partners/`, `type_prices.json`,
`faqs*.json`, `index/…`). Build/refresh the index with `python ingestion/refresh.py`
(or `--skip-fetch` to reuse cached data, `--plan` for a dry-run lineage report).
See [docs/INGESTION.md](docs/INGESTION.md).

---

## Testing & Evaluation

```bash
pytest                          # fast baseline (engine model tests skipped by default)
pytest -m engine                # full suite incl. index/model-dependent tests
python eval/run_eval.py         # 100+ case suite; exit 1 on any failure (CI-ready)
python tests/manual_test_runner.py        # 5-pillar manual verification
python tests/manual_agent.py              # live agent REPL with per-turn trace
python tests/eval/stage4_agent_eval.py    # 12-turn seeded corpus, budget metrics
```

Test files under `tests/unit/` are **git-ignored** by convention — they live
in the working tree but are intentionally not versioned.

---

## Known Issues / Open Work (2026-09-20)

- **Confident-wrong "pizza + price" answer (agent, FIXED 2026-09-20).**
  `عايز بيتزا من 100 ل150 جنيه` previously returned 5 in-range non-pizza
  offers. Two layers now cover it: `ground_search_args` merges the
  query-corroborated `product=pizza` into the tool args, and
  `SearchOffersTool`'s price branch re-grounds product/category from the
  query text before any blind price scan (`agent/tools/catalog_tools.py`).
  Live-verified on `:8001` (5 in-range pizza offers).
- **Stale offer cards on refusal/FAQ turns (`:8000`, FIXED 2026-09-20).**
  `RagEngine.answer_stream` never reset `_last_retrieved`, so a greeting /
  refusal / FAQ-topic turn rendered the *previous* turn's offer cards next
  to the refusal text. Now reset per turn (`core/rag_engine.py`).
- **Merchant question-stem misroute (`:8000`, FIXED 2026-09-20).**
  `What are / What is the KFC offers?` hit the FAQ guard ("what are" is a
  guard token) and fell to semantic retrieval, mixing in Kansas Fried
  Chicken + expired rows. The guard now yields when a merchant/product is
  resolved AND offer language is present; the query serves exactly KFC's
  cards (live-verified).
- **Short-Latin merchant substring false positive (FIXED 2026-09-20).**
  `...KFC Zinger...` matched merchant `ZING` ("zing" ⊂ "zinger").
  `resolve_merchants` now requires token boundaries for short (≤5 char)
  Latin names; Arabic/long names keep substring matching
  (`core/faceted.py`).
- **Price-browse junk on top (FIXED 2026-09-20).** `in_price_range` sorted
  raw ascending, so 0-price placeholder rows (2017 freebies) topped
  "offers under 150". Now prefers nonzero then fresh offers, with fallback
  to the full pool on stale snapshots — same contract as `cheapest()`
  (`core/faceted.py`). Benefits both engines + the agent price branch.
- **Expired-offer leakage.** Evidence gate does not filter expiry/validity; an
  offer expiring 2013-04-06 was returned. Deliberately unfiled date/validity
  design work. (Dev index sets `INCLUDE_EXPIRED_OFFERS=true` and the corpus
  max expiry is 2026-08-31, so expired rows are expected until a refresh.)
- **Duplicate catalog offers.** Same اكسبريس offer seen twice with different
  expiry dates in some queries — catalog-side, no action taken.
- **`ايه عروض رمضان طيب` clarify behavior** — verification still pending.
- Uncommitted working tree: `agent/engine.py`, `agent/planner.py`,
  `core/rag_engine.py`, `docs/*`, `run_servers.py` edits + numerous untracked
  eval/docs files.

---

## Documentation Index

| Doc | Content |
|-----|---------|
| [docs/README.md](docs/README.md) | Full module reference, env reference, identity/session security, evaluation system, performance tables |
| [docs/DOCKER.md](docs/DOCKER.md) | Docker deployment (model-baked images, zero runtime network) |
| [docs/INGESTION.md](docs/INGESTION.md) | Data lineage, refresh cadence, CLI reference |
| [docs/CLICKHOUSE_LOCAL_SETUP.md](docs/CLICKHOUSE_LOCAL_SETUP.md) | Local ClickHouse setup |
| [docs/MANUAL_TEST_RESULTS.md](docs/MANUAL_TEST_RESULTS.md) | Manual verification results |
| [docs/PHASE0_ASSESSMENT.md](docs/PHASE0_ASSESSMENT.md) | Phase-0 assessment |
| [EVALUATION_REPORT.md](EVALUATION_REPORT.md) | Latest full evaluation results |
| [reports/HALLUCINATION_ANALYSIS.md](reports/HALLUCINATION_ANALYSIS.md) | Hallucination audit |
| [reports/BUG_AUDIT_2026-09-15.md](reports/BUG_AUDIT_2026-09-15.md) | Bug audit |
| [eval/CLAUDE_COMPARISON_REPORT.md](eval/CLAUDE_COMPARISON_REPORT.md) | Chatbot vs Claude comparisons |

---

## License

Internal Waffarha project — not for external distribution.