# Live dependencies for the `fix/routing-and-freshness` branch (Phase 0)

Acceptance traces on this branch run against REAL Redis and ClickHouse, not the
`local`/offline fallbacks. Reproduced 2026-09-22 on Windows + Docker Desktop.

## Redis (session memory)

```powershell
docker run -d --name waffarha-redis -p 6379:6379 redis:7-alpine
venv/Scripts/python -c "import redis; print(redis.Redis.from_url('redis://localhost:6379/0').ping())"
# True
```

Traces run with `MEMORY_BACKEND=redis` (default `REDIS_URL=redis://localhost:6379/0`).
`.env` keeps `MEMORY_BACKEND=local` — live mode is per-run env override only.

## ClickHouse (catalog + personal paths)

No local instance: the branch points at the existing read-only test instance
(bila docker needed):

```powershell
$env:CLICKHOUSE_HOST='clickhouse-test.waffarha.tech'  # already in .env
$env:CLICKHOUSE_PORT='443'                            # already in .env
# username/password/database already in .env (read-only service account)
venv/Scripts/python -c "import clickhouse_connect; ..."
# dim_offers count: 8990
```

Traces run with `CATALOG_QUERIES_ENABLED=true` (default in `.env` is false) and
the existing `PERSONAL_QUERIES_ENABLED=true` + `IDENTITY_BACKEND=static`
(`STATIC_TEST_USER_ID=55`). Verified live before the rerun:
`CatalogQueryService.handle('KFC branch location','en')` returns KFC rows;
`PersonalQueryService.handle('my coupons',55,'en')` returns an honest empty answer.

## Full live acceptance command

```powershell
$env:MEMORY_BACKEND='redis'; $env:CATALOG_QUERIES_ENABLED='true'; $env:PYTHONIOENCODING='utf-8'
venv/Scripts/python eval/turn_trace.py --cases eval/acceptance_cases.json --out eval/acceptance_traces.jsonl --now 2026-09-21
venv/Scripts/python eval/summarize_traces.py eval/acceptance_traces.jsonl reports/trace_summary.txt
venv/Scripts/python -m pytest tests/acceptance/ -q
```

## Caveats

- Live catalog answers reflect remote-test data, so `catalog`-exit records are
  only reproducible while that instance is reachable.
- The remote serves expired rows too (`offer_status='active'` does not imply
  unexpired) — Phase 1 filters these at query time.
- The harness pins `REFERENCE_DATE` from its `--now` argument (setdefault, an
  exported `REFERENCE_DATE` still wins), so app-side freshness clocks agree
  with trace-side expiry math. Always pass `--now` for comparable runs.
- Redis session ids are stable across runs (`<session>-<case-idx>`): flush
  (`FLUSHDB`) before a definitive multi-turn run, or a follow-up turn will
  anchor to a previous run's remembered offers.

## Freshness two-run procedure (Phase 1 anchored-exemption proof)

```powershell
$env:MEMORY_BACKEND='redis'; $env:PYTHONIOENCODING='utf-8'
venv/Scripts/python eval/turn_trace.py --cases eval/freshness_show.json --out eval/freshness_show.jsonl --now 2026-09-21
venv/Scripts/python eval/turn_trace.py --cases eval/freshness_followup.json --out eval/freshness_followup.jsonl --now 2027-06-01
venv/Scripts/python -m pytest tests/acceptance/test_freshness.py -q
```
Show turn serves Pizza Hut id 7270 live; follow-up past its 2026-11-01 expiry
resolves it via `followup-anchored` with zero retrieval.
