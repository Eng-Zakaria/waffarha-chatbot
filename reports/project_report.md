# Project Findings Report – Waffarha Chatbot

**Prepared:** 2026‑08‑31  
**Author:** Claude Code (session‑wide analysis)

---

## 1. Overview

The repository is a Python‑based chatbot that provides AI‑driven assistance for the Waffarha offers platform. Core components include:

| Layer | Files / Directories | Purpose |
|------|---------------------|---------|
| **Configuration** | `.env`, `.env.example`, `config/` | Holds API keys, DB credentials, feature flags, and environment‑specific settings. |
| **Data & Indexing** | `data/`, `ingestion/`, `vectorstores/` | Stores raw offers, processed embeddings, FAISS/BM25 indexes, and cached session memory. |
| **Core Logic** | `core/` (e.g., `rag_engine.py`, `config.py`, `identity.py`) | Retrieval‑augmented generation (RAG), intent classification, direct‑answer shortcuts, stock‑query handling, and follow‑up resolution. |
| **Application Entry** | `app.py` | CLI/UI launch point that wires together the RAG engine, Ollama LLM, and HTTP endpoints. |
| **Tests** | `tests/` (mostly third‑party libraries) | Unit / integration tests for utility modules; no project‑specific test suite present. |
| **Utilities & Ops** | `scripts/`, `docker-compose.yml`, `dockerfile` | Helper scripts, Docker orchestration, concurrency reports, and deployment artifacts. |
| **Documentation** | `CONCURRENCY_REPORT.md`, `PRODUCTION_SCALE.md`, `EVALUATION_REPORT.md`, `README.md` (absent) | Operational metrics, scaling plans, evaluation summaries. |

---

## 2. Immediate Observations

| Area | Observation | Potential Impact |
|------|-------------|------------------|
| **Environment Variables** | • `.env` contains placeholder values for `WAFFARHA_SECURITY_KEY`, `CLICKHOUSE_PASSWORD`, etc., but **no actual secrets** are committed.<br>• `.env.example` leaves many variables empty (`CLICKHOUSE_PORT=`, `CLICKHOUSE_PASSWORD=`, `CLICKHOUSE_USERNAME=`). | If a developer runs the app without filling these, start‑up will raise runtime errors (`RuntimeError` in `config.py`). |
| **Secrets Management** | No `.env.local` or `.env.Production` files; secrets are expected to be injected externally (e.g., CI secrets store). | Risk of accidental secret leakage if a developer commits a filled `.env`. |
| **Missing `README.md`** | No top‑level `README.md` explaining project setup, run‑books, or test commands. | New contributors must dig through issue threads or memory notes, increasing onboarding friction. |
| **Test Coverage** | Test files exist only under `venv/Lib/site-packages/...` (third‑party libs). No project‑specific tests under `tests/` besides generic library tests. | Lack of unit/integration coverage for `rag_engine.py`, `config.py`, and `app.py`. No CI pipeline defined to enforce test results. |
| **Concurrency & Rate Limits** | `MAX_CONCURRENT_GENERATIONS=4` derived from `OLLAMA_NUM_PARALLEL`. The code caps generations at 4 but does not enforce a hard back‑pressure mechanism; reliance on Ollama’s own queue timeout (`GENERATION_QUEUE_TIMEOUT=30`). | Under heavy load the process could return 503 errors and may need manual scaling or a more robust queue (e.g., Redis‑based job queue). |
| **Redis / Memory Backend** | `MEMORY_BACKEND` defaults to `redis` but can be switched to `local`. No explicit instruction on which to use in production. | In production, a local dict backend would be inappropriate; missing explicit config could cause unintended state sharing across workers. |
| **Direct‑Answer Logic** | Complex branching in `_get_offer_direct_answer` and `_get_faq_direct_answer`. Multiple guard clauses (`multi_item`, score thresholds, margin checks). | Future changes to scoring or threshold values could unintentionally disable valid shortcuts, leading to fallback LLM calls that increase latency and cost. |
| **Error Handling** | `get_clickhouse_client()` raises a `RuntimeError` if `CLICKHOUSE_PASSWORD` missing, but other DB‑related errors (e.g., connection failures) are only logged (`warning`) and not retried. | Transient DB outages could cause silent failures or degrade answer quality without user feedback. |
| **Bidi & Unicode Cleaning** | Functions like `_clean_bidi_artifacts` and `_iso` handle Arabic/LTR mixing. The code notes that English replies are “no‑op”, but there is no test coverage for edge‑case Unicode sequences. | Potential visual corruption in logged responses under certain terminal emulators. |
| **Docker & Deployment** | `docker-compose.yml` references a `redis` service but does not expose ports or set environment variables for it. `dockerfile` is minimal and does not copy `.env` by default. | The container may start but fail to connect to Redis or ClickHouse if environment variables are not mounted correctly. |
| **Concurrency Report** | `CONCURRENCY_REPORT.md` and `concurrency-report.html` exist but are not referenced in CI. No indication they are automatically refreshed on each push. | Performance regressions could go unnoticed. |
| **Evaluation Report** | `EVALUATION_REPORT.md` is present with a size of ~20 KB, but no details on how it was generated or what metrics were used. | Hard to assess model performance trends over time. |

---

## 3. Suggested Remediation Steps

| Step | Rationale | Commands / Actions |
|------|-----------|--------------------|
| **1. Secure `.env`** | Populate `.env` with real secrets **only** in a secure, non‑git‑tracked location (e.g., CI secret store). Add a `.env.local` template to `.gitignore`. | ```bash<br># Create a local env template<br>cp .env.example .env.local<br># Edit .env.local with real values<br>``` |
| **2. Add `README.md`** | Provide onboarding steps, test run instructions, and environment variable documentation. | Use `Write` to generate a concise `README.md` (≈300 words) describing: <br>• Prereqs (Docker, Ollama, Redis)<> • `docker compose up -d`<br>• Populate `.env`<br>• Run `python -m pytest` (once tests are added). |
| **3. Create Project‑Specific Tests** | Add unit tests for critical functions: `test_rag_engine.py` (direct‑answer logic, intent classification, follow‑up resolution). | ```bash<br>mkdir -p tests/unit<br>touch tests/unit/test_rag_engine.py<br>```<br>Then write sample pytest fixtures and assertions. |
| **4. Add CI / GitHub Actions** | Ensure linting (`ruff`/`flake8`), testing (`pytest`), and coverage (`pytest‑cov`) run on every PR. Also rebuild `CONCURRENCY_REPORT.md` on each merge. | Create `.github/workflows/ci.yml` with steps: checkout → set up Python → install deps → `pytest --cov=core` → upload artifact. |
| **5. Fill Missing `.env.example` Values** | Provide realistic defaults (e.g., `CLICKHOUSE_PORT=8123`, `CLICKHOUSE_USERNAME=default`, `CLICKHOUSE_PASSWORD=`) and comment on security. | Update `.env.example` with comments (`# Set a strong password for ClickHouse`). |
| **6. Harden Error Handling** | Add retry logic for ClickHouse connectivity and a graceful fallback when the DB is unreachable. | Use `tenacity` library: `@retry(stop_after_attempt=3, wait=wait_exponential(multiplier=1, min=2, max=10))` around `get_clickhouse_client()`. |
| **7. Enforce Environment Validation** | At app start, validate that all required vars are present and non‑empty, failing fast with clear messages. | Add a `validate_env()` function in `config.py` called from `app.py`. |
| **8. Documentation for Docker Compose** | Add a `docker-compose.override.yml` example that mounts a local `.env` for dev, and expose Redis port (`6379`). | Document in `README.md` and `docker-compose.yml` comments. |
| **9. Add Unit Tests for Unicode/Bidi Handling** | Verify that Arabic answer formatting does not introduce stray control characters. | Use `pytest` with sample Arabic strings; assert output length & absence of control chars. |
| **10. Review Dependency Versions** | Pin exact versions of critical libraries (`sentence-transformers`, `faiss-cpu`, `ollama`, `clickhouse-connect`) to avoid accidental upgrades. | Run `pip freeze > requirements.txt` and commit the lock file. |
| **11. Publish Evaluation Metrics** | Export `EVALUATION_REPORT.md` findings to a version‑controlled location (e.g., `docs/eval/`). Include model IDs, token usage, and latency numbers. | Move the file to `/docs/eval/` and add a symlink in repo root. |
| **12. Publish Concurrency Report Automatically** | Add a GitHub Action that runs `python scripts/generate_concurrency_report.py` (or similar) on each PR and updates `CONCURRENCY_REPORT.md`. | Use `Action` to checkout, set up Python, run script, commit & push changes (with a bot token). |

---

## 4. Test Execution Plan

Below is a concrete plan to verify the current state of the project locally.

| Phase | Command | Expected Outcome |
|-------|---------|-------------------|
| **A. Environment Setup** | ```bash<br># 1. Clone repo (if not already)<br>git clone https://github.com/your‑org/waffarha-chatbot.git<br>cd waffarha-chatbot<br># 2. Create a virtual env (optional but recommended)<br>python -m venv .venv && source .venv/Scripts/activate  # Windows<br># 3. Install dependencies<br>pip install -r requirements.txt  # (generate if missing)<br># 4. (Optional) Start local services<br>docker compose up -d  # starts Redis (if present) <br>``` | All dependencies installed; Redis container running (if defined). |
| **B. Validate `.env`** | ```bash<br># Check required keys<br>grep -E 'WAFFARHA_SECURITY_KEY|CLICKHOUSE_PASSWORD|OLLAMA_HOST' .env || echo "Missing variables"<br>``` | Should print “Missing variables” → confirms that `.env` is incomplete and needs filling. |
| **C. Run Linting** | ```bash<br># Using ruff (if installed) or flake8<br>ruff check core/ tests/ --output-format=github<br>``` | No lint errors (or a list of warnings). |
| **D. Execute Test Suite** | ```bash<br># Since no project‑specific tests exist, we can at least run generic pytest on third‑party test folders<br>python -m pytest --co -q > test_list.txt  # list all discovered tests<br># Or simply attempt to import core modules<br>python -c "import core.rag_engine; print('import success')"<br>``` | Import succeeds without errors; if not, the output will indicate missing modules orruntime issues. |
| **E. Start the Application** | ```bash<br>python app.py --help  # or simply `python app.py` if it has a default entry point<br>``` | Should display CLI help; otherwise, any import‑time errors will surface (e.g., missing env vars). |
| **F. Verify RAG Engine Instantiation** | ```bash<br>python - <<EOS<br>from core.rag_engine import RagEngine<br>engine = RagEngine()\nprint('RagEngine instantiated')\nEOS\n``` | Should print “RagEngine instantiated”; any missing index or config errors will be shown. |
| **G. Perform a Sample Query** | ```bash<br># Assuming the app exposes a `/chat` endpoint via HTTP<br>curl -X POST http://localhost:3001/chat -H "Content-Type: application/json" -d '{"question":"What are the current offers for KFC?"}'\n``` | Receives a JSON response (or a 500/503). This validates the full request‑processing pipeline. |
| **H. Capture Performance Metrics** | ```bash<br># Use the concurrency‑report script (if present)\npython scripts/generate_concurrency_report.py\n``` | Generates or updates `CONCURRENCY_REPORT.md`; inspect for any warnings. |
| **I. Review Evaluation Data** | ```bash<br>head -n 20 EVALUATION_REPORT.md\n``` | Shows the first 20 lines – should contain model version, token usage, and accuracy numbers. |
| **J. Clean Up** | ```bash<br>docker compose down -v   # stop containers and remove volumes\n``` | Ensures no stray containers remain. |

**Note:** Many of the above steps will surface missing pieces (e.g., empty environment variables, absent `requirements.txt`, missing `pytest` config). Those gaps are themselves findings that should be recorded in the project backlog.

---

## 5. Summary of Critical Findings

1. **Security & Configuration** – `.env` placeholders are empty; secrets could be inadvertently committed. A missing `README.md` exacerbates onboarding risk.
2. **No Project‑Specific Test Suite** – Current `tests/` only contains third‑party library tests. Critical business logic in `rag_engine.py` lacks unit/integration coverage.
3. **Incomplete `docker-compose` Setup** – Redis service lacks explicit port exposure and environment‑variable documentation; `.env` handling is not codified.
4. **Fragile Direct‑Answer Logic** – Complex guard clauses may become outdated if scoring thresholds change, potentially causing unnecessary LLM calls.
5. **Observability Gaps** – Concurrency and evaluation reports exist but are not generated automatically; no CI to enforce regeneration.
6. **Potential Runtime Errors** – Missing required env vars cause `RuntimeError`s at runtime; a more graceful validation layer is advisable.
7. **Dependency Version Drift** – No `requirements.txt` lockfile; future `pip install -r requirements.txt` could break silently on CI.

---

## 6. Next Steps for the Team

1. **Create & publish a `README.md`** (immediate communication improvement).  
2. **Add a `requirements.txt`** (pin versions) and commit it.  
3. **Implement a minimal test suite** focusing on `RagEngine`’s public methods (`_get_offer_direct_answer`, `_classify_intent`, `_resolve_followup_targets`).  
4. **Set up CI** (GitHub Actions) to run lint, tests, and generate reports on every PR.  
5. **Finalize `.env.example`** with sensible defaults and clear comments about secret handling.  
6. **Add validation logic** in `app.py` to abort early if required variables are missing.  
7. **Document Docker Compose** usage, including a `docker-compose.override.yml` example for local development.  
8. **Schedule a review** of the direct‑answer thresholds and scoring constants to ensure they still align with business goals.  

---

*End of Report.*  

(You can find this report in the repository at `project_report.md`.)  
