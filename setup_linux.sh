#!/usr/bin/env bash
# Waffarha chatbot — fresh-machine setup (Linux/macOS).
#
#   Default (venv, setup only):   bash setup_linux.sh
#   System libs, no venv:         bash setup_linux.sh --system --yes
#   Setup + start servers:        bash setup_linux.sh --run
#   Skip Ollama work:             bash setup_linux.sh --skip-ollama
#
# Modes: --venv (default, isolated ./venv) | --system (pip-install into the
# current python3 env directly, no venv). --run starts run_servers.py
# (:8000 + :8001) after a successful setup. Re-running is safe: every step
# checks what already exists first and only fills the gaps.
set -euo pipefail

MODE=venv; RUN=0; SKIP_OLLAMA=0; YES=0
for a in "$@"; do
  case "$a" in
    --system) MODE=system ;;
    --venv) MODE=venv ;;
    --run) RUN=1 ;;
    --skip-ollama) SKIP_OLLAMA=1 ;;
    --yes|-y) YES=1 ;;
    --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $a (see --help)" >&2; exit 1 ;;
  esac
done

step()  { echo -e "\n==> $1"; }
found() { echo "  [FOUND]  $1"; }
missing(){ echo "  [MISSING] $1"; }
fixed() { echo "  [FIXED]  $1"; }

# 0. Must run from project root
if [[ ! -f requirements.txt && ! -f docs/requirements.txt ]]; then
  echo "ERROR: run this from the project root (requirements.txt not found)." >&2; exit 1
fi
REQ=requirements.txt; [[ -f "$REQ" ]] || REQ=docs/requirements.txt

# 1. Python version check
step "1/7 Python"
python3 --version
python3 -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info < (3,14) else 1)" \
  || { echo "ERROR: need Python 3.11–3.13 (e.g. sudo apt install python3.11 python3.11-venv)" >&2; exit 1; }
found "$(python3 --version 2>&1)"

# 2. Pick interpreter: venv (default) or system libs directly
step "2/7 Python environment (mode: $MODE)"
if [[ "$MODE" == system ]]; then
  if [[ "$YES" != 1 ]]; then
    read -r -p "Mode=system installs into your CURRENT python3 (no venv). Continue? [y/N] " ans
    [[ "$ans" == y || "$ans" == Y || "$ans" == yes ]] || { echo "Aborted. Re-run with default --venv."; exit 0; }
  fi
  PY=python3
  missing "venv (skipped by design in system mode) -> using current python3"
else
  if [[ -x venv/bin/python ]]; then found "venv already exists -> reusing";
  else missing "venv -> creating"; python3 -m venv venv; fixed "venv created"; fi
  PY=venv/bin/python
fi

# 3. Python deps (check first, install gaps — pip is idempotent)
step "3/7 Python packages ($REQ)"
if "$PY" -c "import fastapi, uvicorn, sentence_transformers, rank_bm25, qdrant_client, redis, clickhouse_connect, numpy" 2>/dev/null; then
  found "key imports already work -> refreshing from $REQ (fast, no-op if satisfied)"
else
  missing "some imports failed -> installing"
fi
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install --default-timeout=120 --retries 10 -r "$REQ"
"$PY" -c "import fastapi, uvicorn, sentence_transformers, rank_bm25, qdrant_client, redis, clickhouse_connect, numpy; print('  [OK] py deps verified')"

# 4. .env (never overwrite)
step "4/7 .env"
if [[ -f .env ]]; then found ".env exists -> untouched";
elif [[ -f .env.example ]]; then cp .env.example .env; fixed ".env created from .env.example (edit CLICKHOUSE_* only to rebuild the index)";
else missing ".env.example not found -> skipped"; fi

# 5. Ollama: binary -> server -> model (each checked before acting)
step "5/7 Ollama"
MODEL="${OLLAMA_MODEL:-qwen2.5:3b-instruct}"
if [[ "$SKIP_OLLAMA" == 1 ]]; then
  missing "skipped via --skip-ollama (servers will fail without it)"
elif ! command -v ollama >/dev/null 2>&1; then
  missing "ollama binary not found -> install: curl -fsSL https://ollama.com/install.sh | sh, then re-run"
else
  found "$(ollama --version 2>&1)"
  if ollama list >/dev/null 2>&1; then
    found "ollama server responding"
  else
    missing "ollama server not responding -> starting 'ollama serve' in background"
    (ollama serve >/tmp/ollama.log 2>&1 &) || true
    tries=0
    while ! ollama list >/dev/null 2>&1 && [[ $tries -lt 30 ]]; do sleep 1; tries=$((tries+1)); done
    ollama list >/dev/null 2>&1 && fixed "ollama server is up" \
      || echo "WARNING: ollama serve did not respond; start it manually and re-run." >&2
  fi
  if ollama show "$MODEL" >/dev/null 2>&1; then
    found "model '$MODEL' already cached -> no download"
  else
    missing "model '$MODEL' not cached -> pulling (~2GB, one time)"
    ollama pull "$MODEL"
  fi
fi

# 6. Readiness: index + ports
step "6/7 Readiness"
IDX_COUNT="$("$PY" -c "import pathlib; print(len(list(pathlib.Path('data/index').glob('*'))))" 2>/dev/null || echo 0)"
if [[ "$IDX_COUNT" -gt 0 ]]; then found "data/index present ($IDX_COUNT entries)";
else missing "data/index empty -> copy it in or run: $PY ingestion/refresh.py (needs CLICKHOUSE_* in .env)"; fi
for port in 8000 8001; do
  if "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)" 2>/dev/null; then
    missing "port $port is BUSY (server already running?)"
  else
    found "port $port free"
  fi
done

# 7. Run (optional)
if [[ "$RUN" == 1 ]]; then
  step "7/7 Starting servers"
  echo "Serving :8000 (cascade) + :8001 (agent) — Ctrl+C to stop."
  exec "$PY" run_servers.py
else
  step "7/7 Done (not started)"
  echo "Start with: $PY run_servers.py   OR   re-run with --run"
  echo "Then open http://localhost:8000 and http://localhost:8001"
fi
