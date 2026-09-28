#!/usr/bin/env bash
# Waffarha chatbot — fetch + rebuild the vector index (Linux/macOS).
#
#   bash update_index.sh --plan                  # dry-run lineage report (no ClickHouse, no writes)
#   bash update_index.sh                         # full: fetch from ClickHouse + rebuild
#   bash update_index.sh --skip-fetch            # rebuild from existing data/ files (no ClickHouse)
#   bash update_index.sh --steps "sources build" # run selected steps only
#
# Stop the chat servers (:8000/:8001) first — the Qdrant index folder can't be
# rebuilt while a server holds its folder lock.
set -euo pipefail

MODE=venv; PLAN=0; SKIP_FETCH=0; BACKEND=""; MODEL=""; STEPS=""
for a in "$@"; do
  case "$a" in
    --system) MODE=system ;;
    --venv) MODE=venv ;;
    --plan) PLAN=1 ;;
    --skip-fetch) SKIP_FETCH=1 ;;
    --backend=*) BACKEND="${a#--backend=}" ;;
    --model=*) MODEL="${a#--model=}" ;;
    --steps=*) STEPS="${a#--steps=}" ;;
    --help|-h) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $a (see --help)" >&2; exit 1 ;;
  esac
done

[[ -f ingestion/refresh.py ]] || { echo "ERROR: run this from the project root." >&2; exit 1; }
PY=venv/bin/python; [[ "$MODE" == system ]] && PY=python3
[[ "$MODE" == venv && ! -x "$PY" ]] && { echo "ERROR: venv not found. Run bash setup_linux.sh first (or use --system)." >&2; exit 1; }

EXTRA=()
[[ -n "$BACKEND" ]] && EXTRA+=(--backend "$BACKEND")
[[ -n "$MODEL" ]] && EXTRA+=(--embedding-model "$MODEL")

if [[ "$PLAN" == 1 ]]; then
  echo "==> Index plan (dry-run)"
  exec "$PY" ingestion/refresh.py --plan "${EXTRA[@]}"
fi

# Warn if servers hold the Qdrant lock
for port in 8000 8001; do
  if "$PY" -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',$port))==0 else 1)" 2>/dev/null; then
    echo "WARNING: port $port is BUSY — stop run_servers.py first or the rebuild may fail on the Qdrant folder lock." >&2
  fi
done

if [[ "$SKIP_FETCH" != 1 ]]; then
  [[ -f .env ]] || { echo "ERROR: .env missing. Run bash setup_linux.sh first, then fill CLICKHOUSE_*." >&2; exit 1; }
  grep -Eq '^CLICKHOUSE_HOST\s*=\s*\S+' .env || { echo "ERROR: .env has no CLICKHOUSE_HOST — fetching is impossible. Use --skip-fetch." >&2; exit 1; }
  grep -Eq '^CLICKHOUSE_PASSWORD\s*=\s*\S+' .env || echo "WARNING: CLICKHOUSE_PASSWORD empty in .env — fetch will fail unless your ClickHouse user has no password." >&2
fi

CMD=(ingestion/refresh.py)
[[ "$SKIP_FETCH" == 1 ]] && CMD+=(--skip-fetch)
# shellcheck disable=SC2206
[[ -n "$STEPS" ]] && CMD+=(--steps $STEPS)
echo "==> Updating index: $PY ${CMD[*]} ${EXTRA[*]}"
exec "$PY" "${CMD[@]}" "${EXTRA[@]}"
