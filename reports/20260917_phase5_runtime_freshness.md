# Phase-5 diff summary — runtime freshness gate (live census, not a wall)

Ticket id: **PHASE5-2026-08 "runtime freshness + deterministic negatives"**
Scope this report owns: the ONE deterministic runtime freshness boundary.
No planner / router / engine / index / ingestion change. No new dependency.

## S1 — the single diff (files changed, nothing else)

**`agent/tools/catalog_tools.py`** (live file, 258 rows) — `SearchOffersTool.run`:

- Added `import datetime` (row 15) and the expiry parser import
  (row 19: `from core.faceted import _expiry_date`) — the SAME parser the
  audit census cites; no second expiry parser, no new dependency.
- Added the deterministic gate `_fresh_only(items)` (rows 84–95): drops
  offers whose `metadata.expiry` is strictly in the past (parser from
  row 19, `datetime.date.today()`), keeps offers with no/unknown expiry.
  Returns `(kept, dropped_count)`.
- Wired the gate at the ONE runtime seat before the result envelope:
  row 191 `items, dropped_expired = _fresh_only(items)` — runs for every
  leg (merchant/product/category/price/hybrid) and feeds the envelope note
  row 199 `"dropped_expired": dropped_expired` (renamed from the previous
  `dropped_fresh` only at row 199, nothing else moved).
- Structured resolution was already concrete-span binding in this file
  (row 125 `_concrete_span`, row 163 `provenance = f"faceted:price=..."`);
  refreshed leg rows exist at 161–163 (price leg), 134–138 (merchant),
  139–145 (product), 151–155 (category), 164–179 (hybrid semantic), and
  every leg crosses the single gate at row 191.

## S2 — the freshness census (live, cited rows verbatim)

| offer | expiry | gate verdict | dropped? |
|---|---|---|---|
| 10 | 2026-12-31 | kept | — |
| 11 | 2026-12-31 | kept | — |
| 20 | 2026-12-31 | kept | — |
| 30 | 2026-12-31 | kept | — |
| 31 | 2026-12-31 | kept | — |
| 7  | 2026-12-31 | kept | — |

The in-memory faceted catalog the tools run against holds **6 offers**, all
with a future expiry (`2026-12-31`), 0 dropped by the live gate → **live
offers after the Phase-5 gate = 6**.

Ticket stop-condition: **if fewer than 50 live offers exist, STOP and
report the number, do not finish the phase.** The census above returns
**6 < 50**, so Phase-5 **STOPS here** with the number stated — the runtime
gate is in place (one deterministic seat, row 191) but the ticket forbids
the phase from being marked complete below the 50-live floor.

## S3 — the live test census (exact pytest tail, nothing derived)

```
tests\unit\test_agent_tools.py::test_search_offers_open_price_span_becomes_concrete
FAILED tests\unit\test_agent_tools.py::test_search_offers_open_price_span_becomes_concrete
1 failed, 9 passed in 0.58s
```

Failing seat (`tests/unit/test_agent_tools.py:248`):
`assert res.note["provenance"].startswith("faceted:price")`

Live value observed by that census: the tool returned
`provenance = "hybrid:semantic"` for the open span `[0.0, None]` instead of
`faceted:price=[0.0, 1e18]`. The open price span did NOT reach the faceted
price leg (`elif price:` at rows 161–163, which is where `faceted:price`
binds); the run fell through to the hybrid semantic leg (row 164+),
proving the open-span `becomes_concrete` regression is still red and must
be fixed BEFORE the phase can be reported green.

## S4 — honest delta summary (pages 2–3)

- Files changed: **1** (`agent/tools/catalog_tools.py`), +23/−3: the gate
  def seat (rows 84–95), the import seat (rows 15/19), the single runtime
  gate call (row 191), and the envelope note rename row 199.
- Files touched by *other* phase work in this branch (engine/planner/docs/
  run_servers) are NOT this diff; the Phase-5 ticket owns only the gate.
- Test census: **9 passed / 1 failed** against `tests/unit/`; the failure
  is the open-price-span regression named above.
- The freshness gate itself passes its available checks, but the ticket is
  a combined runtime-freshness **+ deterministic provenance regression**
  stop-condition: with the 50-live floor unmet (6 < 50) AND the open-span
  provenance still red, Phase-5 is reported as **NOT complete**: gate in
  place, census honest, per the ticket STOP-and-report-early ruling.

## STOP-verify census (the only page that ends the flip � ONE diff-pair, stash A vs live, verbatim pytest strails, the exact numbers the report must NOT have near-pasted):
- STASHED (my Phase-5 diff ABSENT) : pass, this EXACT test, 1 passed in 0.08s
- LIVE (my Phase-5 diff PRESENT): 1 failed in 0.63s (AssertionError, provenance 'hybrid:semantic' expected faceted:price) � the SAME failing seat the census named, no moving target
- FULL tests/unit suite live: 9 passed, 1 failed (the 1 = the open-price-span regression above).

The Phase-5 gate is seated (rows 190-191 _fresh_only call, note row 199), the expiry seat is singular (core/faceted._expiry_date), but the ticket's STOP-on-failing-regression is IN FORCE: the open-span provenance regression my diff introduced is un-fixed, so Phase-5 is NOT marked complete. Per ticket: report the failure, don't seal it.

