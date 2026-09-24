# Verification Report — Waffarha Chatbot Audit (`audit/verification`)

Date: 2026-09-22. `--now 2026-09-21` for all expiry math.
Config under test: `embedding=BAAI/bge-m3 backend=qdrant memory=local personal=True
identity=static (user_id=55) include_expired=True`. Redis unavailable (local backend used
throughout); Ollama `qwen2.5:3b-instruct` reachable; ClickHouse not exercised
(`CATALOG_QUERIES_ENABLED=false`, personal service never triggered in traces).

Artifacts: `eval/turn_trace.py` (harness), `eval/acceptance_cases.json`,
`eval/acceptance_traces.jsonl` (48 records), `eval/expiry_probe.py` +
`eval/expiry_probe.json`, `eval/summarize_traces.py`, `eval/inspect_records.py`,
`tests/acceptance/test_acceptance.py` (20 passed, 21 xfailed),
`docs/ARCHITECTURE.md`, `reports/trace_summary.txt`, `reports/record_details.txt`,
`reports/verify/20260922_094358_verify_baseline/` (rerun baseline).

Behavior neutrality: **no application file was modified by this audit.**
`git status` shows only the pre-existing working-tree modifications (present on
`master` before the branch) plus new files under `eval/`, `tests/`,
`reports/`, `docs/`. Tracing is by runtime wrapping inside the harness process
only, so the before/after-eval proof is vacuous by construction — there is no
hook in the app to be neutral or not.

## (a) Claims table

| # | Claim | Verdict | Evidence (file:function) + how tested |
|---|---|---|---|
| 1 | Cascade order = closing→greeting→gibberish→injection→sanitize→arabizi→unmatched-brand/inactive-merchant/out-of-scope→personal→catalog→FAQ-topic→superlative→faceted→follow-up→retrieve | **PARTIALLY CONTRADICTED** | Read `RagEngine.answer_stream` (core/rag_engine.py:3688-4161) + wrapper traces. Real order: closing→greeting→gibberish→injection→sanitize→arabizi→unmatched-brand→inactive-merchant→out-of-scope→guardrail→personal→catalog→**anchored-followup→superlative**→[no-retrieval guard]→**faceted→faq-topic**→retrieve→strict-floor→relevance→faq-direct→stock→comparison→cards-intro→full. Three positions differ: anchored runs before superlative (not after faceted); faq-topic runs after faceted (not before superlative). |
| 2 | "عثرت على عروض مناسبة" header + "جميع العروض أعلاه حقيقية…" footer producers and users | **CONFIRMED** | `AgentEngine._render_cards` (agent/engine.py:975-985, header at :982) + `_evidence_note_text` (agent/engine.py:1284-1287, footer). Agent route only — observed verbatim in agent fallback answers (fp_gym/loc traces). Cascade never emits them; its intros are `_llm_offer_intro` or the "دي العروض اللي لقتها لك:" default (core/rag_engine.py:4088-4093). |
| 3 | Expiry enforcement per path; freshness gate on the SERVED path; INCLUDE_EXPIRED_OFFERS | **CONFIRMED (with nuance)** | Served-path probe (`eval/expiry_probe.py`, same singletons as servers): `retrieve()` returned 8 candidates, 7 expired — **no filter** (source has no `expir` reference in `retrieve`). `offers_for_*` — no filter (core/faceted.py:665-698). `cheapest/most_expensive/in_price_range/highest_discount` — prefer-fresh with fallback `fresh or pool` (core/faceted.py:734,783); probe: `_rec_is_expired` executed (1792/4495 calls), `in_price_range` returned 0 expired here but CAN return expired via fallback. Catalog `_answer_*` — no date predicate (only `deleted_at IS NULL AND offer_status='active'` in SQL). Agent `_evidence_gate` (agent/engine.py:740-755) checks empty/offer-source/price only — **no expiry**. `SearchOffersTool` price branch executed `_fresh_only` on the served path (probe: 1 call) but it is lenient (keeps stale list if all dropped). `INCLUDE_EXPIRED_OFFERS` exists (core/config.py:95), read ONLY at build time (ingestion/loaders/build_index.py:325); never at chat time. `.env=true` → index holds 7,692 expired offer docs; traces served expired cards on faceted/llm paths (e.g. greet_ya_hala 5/5 expired). |
| 4 | Refusal floors vs direct-answer shortcuts: embedding_score or combined_score? | **MEASURED (see table)** | Floors + `_context_is_relevant` + risk log read `combined_score` (core/rag_engine.py:3989/3995/4011/:2426/4025). Direct-answer shortcuts read BOTH: raw-`embedding_score` floor 0.55 first, then `combined_score` 0.85 (`_get_faq_direct_answer` :2966/:2972, `_get_offer_direct_answer` :3054/:3060, `_get_stock_direct_answer` :3159/:3165). Bonus split: `combined = score + lexical + intent + entity + title + price` (:2716); BM25-only candidates get fabricated dense `0.5` (:2613); RRF only expands the pool, never merged into the score (:2600-2613). |
| 5 | Lexical bonus token vs substring; "يا" stopword; digit substring; BM25 fabricated score | **CONFIRMED** | Lexical hits = SUBSTRING on folded text (:2671-2672); price digits = SUBSTRING (:2704/:2712); title bonus = TOKEN match (:2691-2692 `in title_words_fold`). "يا" is in NEITHER `_LEXICAL_STOPWORDS` (:730-739, verified by reading) NOR bm25 stopwords (vectorstores/bm25_store.py:28-43). BM25-only `0.5` fabricated score CONFIRMED (:2613). |
| 6 | Evidence gate with no constraints; planner hint source + default; `classify_intent_robust` LLM reachability | **CONFIRMED** | Gate accepts iff non-empty (+offer-source for `search_offers`); price checked only if present (agent/engine.py:740-755; observed `(True,'ok')` on greeting queries). Hint = `facade.classify_intent_robust(query)` (agent/engine.py:117), default `"other"` (:118); observed hints: OFFER_LOOKUP/GREETING/FAQ_INQUIRY/OUT_OF_SCOPE/MULTI_MERCHANT. LLM branch UNREACHABLE in production: `agent/facade.py:33-34` passes no client; all callers single-arg. |
| 7 | One embedder + one Qdrant handle shared | **CONFIRMED** | `run_servers.py` single process + `get_engine`/`get_agent_engine` double-checked singletons (core/app.py:208-260); `core/agent_server.py` reuses `core.app` singletons; Qdrant embedded single-opener (vectorstores/vectorstores.py). Trace logs show one embedder load serving both engines. |
| 8 | Safety gates: cascade-only vs agent-only vs shared | **CONFIRMED** | Cascade-only: closing/greeting/gibberish/injection/unmatched-brand/inactive-merchant/out-of-scope×2 (answer_stream :3711-3828). Agent-only: `_safety_gate` (:1030-1059), `_retrieval_allowed` gate (:1515), `_needs_clarification` (:805). Shared: `core/greetings.py:social_turn_kind`, re-exported (core/rag_engine.py:33-37); agent keeps local copies. |
| 9 | Prod gaps: rate limiting, CORS, static identity | **CONFIRMED** | `ALLOWED_ORIGINS` identical in core/app.py:162 and core/agent_server.py:66 (github.io + localhost). No rate limiter — only the generation semaphore → 503+Retry-After. `IDENTITY_BACKEND` default `static`; default user 0→None (anonymous); this env `STATIC_TEST_USER_ID=55` so traces ran as user 55 with personal enabled. |
| 10 | Numbers: rerun baseline + full eval; define 146/172 and hit@k; offer counts | **MEASURED below** | Reran `utils/run_full_eval.py --retrieval-only`: **150/172 checked passed (225 total, 0 errors), hit@1=0.315, hit@k=0.741, mrr=0.483** (reports/verify/20260922_094358_verify_baseline/; exit 1 = FAIL by design). Full RAG pipeline (`--rag-only`, same queries): **82/225 checked passed, 0 errors, 0 scaffolding leaks** (reports/verify/20260922_095930_verify_full/). Definitions from run_full_eval.py:339-387: `n_checked`=172 records with scorable ground truth of 225; `passed`=expected doc (or source) anywhere in the `retrieve()` candidate pool (k=40, 50 multi); hit@1=rank 1; hit@k=in-pool; MRR=mean(1/rank). Counts (--now 2026-09-21): offers 17,974 (active 9,010 / disable 8,964); all: unexpired 1,472 / expired 16,498 / null-expiry 4; active: 1,318 / 7,692 / 0; index 9,106 docs (96 faq + 9,010 offer), expired offer docs **7,692**, manifest `include_expired_offers=true`. Full RAG eval (`--rag-only`) completed during the audit: 82/225, 0 errors, 0 leaks (reports/verify/20260922_095930_verify_full/). Ollama/Redis/ClickHouse: Ollama reachable; Redis DOWN (local backend used); ClickHouse untested (`CATALOG_QUERIES_ENABLED=false`, personal never triggered). |

## (b) Acceptance results

Full per-record table: `reports/trace_summary.txt` (48 records + 84 verdicts).
Suite: `pytest tests/acceptance/` → **20 passed, 21 xfailed** (xfails carry observed reasons).

| Case | Cascade exit / result | Agent exit / result |
|---|---|---|
| يا هلا / هلااااا / مرحبا يا باشا | llm-cards-intro or refusal-text WITH retrieval; 0–5 cards — FAIL (×3) | search_offers, 2–5 cards — FAIL (×3) |
| هلا / ازيك / hello / شكرا / asdkjh | greeting/closing/gibberish, 0 cards, 0 retrieval — PASS | agent-safety — PASS |
| عايز عروض بيتزا | faceted, 5 cards, 0 expired — PASS | search_offers, 5 cards, 0 expired — PASS |
| …under 100 EGP | **closing** ("no more"⊂"no more than"), 0 cards — FAIL | search_offers, 5 cards ≤39 EGP — PASS |
| Meal RECORD | pizza-breakfast faceted ok; ramadan/iftar faceted, 4/5 expired, NO Tamara card (Zadna/Smoothie/Arabiata instead); sohour llm-cards 5/5 expired | ramadan 1 card; Tamara→Zadna coffee (wrong merchant); sohour→clarify |
| FP delete/hair/physio/gym | out-of-scope refusal — FAIL per task (×4) | retrieve_faq ok (delete); cards served on hair/physio/gym-fallback — FAIL per task (×4) |
| abandoned cart / actually valid | **closing** ("done"⊂"abandoned"; literal "actual") — FAIL (×2) | cards served — FAIL per task (×2) |
| Follow-up compare | anchored, [7270]⊆t0, no fresh retrieval — PASS | get_offer ran but returned subset member only — PASS (recorded) |
| KFC Nasr City location | faceted, 2 KFC cards (1 expired 2023), no address — FAIL | fallback plan_parse_error, KFC+PizzaHut cards, no address — FAIL |
| Personal بتاعي | llm-full support-contact text, 0 cards — PASS (route recorded) | relax_failed after 3 empty searches, 0 cards — PASS (route recorded) |
| Language | all match | **fp_valid mismatch** (en query → mixed/ar reply) — FAIL ×1 |

## (c) Discrepancies: review vs docs vs code

1. Review's cascade order wrong at 3 positions (claim 1).
2. `docs/README.md` says FAISS is the default backend; `core/config.py` + root README + served default is **qdrant**.
3. Docs' "146/172, hit@k 0.667" stale vs rerun "150/172, hit@k 0.741" (queries/code drifted).
4. Phase-3-era "80%" figures noted non-reproducible in docs themselves — consistent with rerun.
5. Harness caveat: on refusal turns one LLM call is logged phase `generation`; source read shows it is the `_smart_source_intent`→`judge_faq_vs_offer` direct call (core/rag_engine.py:831), not answer generation. Harness fixed post-run (wraps `judge_faq_vs_offer`); traces keep the old label.

## (d) Measured numbers

- Retrieval baseline rerun: 150/172, hit@1 0.315, hit@k 0.741, mrr 0.483, 0 errors.
- Full RAG pipeline rerun: 82/225 checked passed, 0 errors, 0 scaffolding leaks.
- Offers: 17,974 total (9,010 active / 8,964 disable); active unexpired 1,318 / expired 7,692 / null 0.
- Index: 9,106 docs (96 faq + 9,010 offer); expired offer docs 7,692.
- Trace latencies: deterministic gates ~1ms; faceted ~14s (1 offer-intro LLM call); retrieval+LLM turns ~12–35s.
- Full RAG eval: launched (`verify_full`), pending.

## (e) Top 5 root causes of failing non-search cases

1. **Closing-phrase substring match** — `answer_stream` closing check `any(phrase in
   query.lower())` (core/rag_engine.py:~3732) with list entries `"no more"` (:3714),
   `"done"` (:3714, ⊂"aban**done**d"), literal `"actual"` (:3730). Evidence:
   `offer_under100 → exit=closing, 0 LLM, answer="You're welcome!…"`.
2. **Greeting whole-message equality** — `_looks_like_greeting` (core/rag_engine.py:563-569)
   `q in _GREETING_PHRASES`: "هلا" passes, "يا هلا"/"مرحبا يا باشا" miss (no vocative
   handling; "يا" is no stopword anywhere). Evidence: `greet_ya_hala → llm-cards-intro,
   5 cards all expired 2019–2024`.
3. **Elongation + relevance gap** — "هلااااا" misses greeting, retrieves (best combined
   0.524, lexical_hits 0), relevance rejects → refusal text but retrieval already ran.
   Agent planner labels it `catalog` → 2 cards. Evidence: `exit=llm-full, retr=True`.
4. **Cascade over-refusal vs agent over-service** — `_looks_like_out_of_scope` (:694) +
   guardrail refuse account/health/gym queries (0 cards, 0 LLM) that the task expects
   answered; the agent serves cards for the same queries. Engines contradict each other
   on identical input.
5. **Agent fallback renders cards anyway** — `plan_parse_error` (fp_gym/fp_valid/loc) and
   invented entities (`merchant=["Waffarha"]`, `price_range=[0,1000]`) pass the evidence
   gate `(True,'ok')` (agent/engine.py:740-755, no constraint to violate), and
   `_finish_fallback` renders tool evidence under the "عثرت على عروض مناسبة:" header —
   e.g. location query answered with KFC+Pizza Hut cards and zero address data.

## What I would fix first (waiting for go)
1. Closing-phrase match → token-boundary/whole-message match (kills 3 cascade HARD fails,
   including the price-ceiling query). 2. Greeting normalization (strip vocative يا,
   collapse elongation). 3. Ground planner entities + tighten evidence gate so
   `plan_parse_error`/fallback never renders cards for non-offer queries. STOP — fixing
nothing until you approve.

## Phase 0 addendum (branch fix/routing-and-freshness): live Redis + ClickHouse

Setup: Docker Desktop daemon started; `docker run -d -p 6379:6379 redis:7-alpine`
(ping True). No local ClickHouse container (daemon was down at first, remote test
instance reachable instead): `CLICKHOUSE_HOST=clickhouse-test.waffarha.tech:443`,
read-only `clickhouse_read` account, `dim_offers` count 8,990. Traces reran with
`MEMORY_BACKEND=redis`, `CATALOG_QUERIES_ENABLED=true` (PERSONAL was already true,
static user 55). See `docs/LIVE_DEPS.md` for the exact commands.

Baseline with Phase 0 (no app diffs): **150/172, hit@1 0.315, hit@k 0.741, mrr 0.483 —
identical to the audit rerun** (reports/verify/20260922_153238_phase0_check/).

Acceptance with live deps (`pytest tests/acceptance/` → 18 passed, 1 skipped, 22 xfailed):

Xfail → PASS (marker removed, resolved-note kept in tests/acceptance/test_acceptance.py):
- `loc_kfc_nasr` cascade: live `catalog-handle` answers with zero cards. New reason:
  the answer is an honest no-data message ("Couldn't find location info...") —
  NO verified address data is used anywhere (offline `part_address` empty, live
  location lookup found nothing for Nasr City).

PASS → XFAIL (new markers with reasons):
- `offer_pizza` cascade: live `catalog-handle` returns a TEXT answer with zero
  structured cards — and the text presents a 2021-expired row under a "current
  offers" header. The live catalog path serves expired inventory as live.
- `followup_compare` agent: turn-1 (live catalog, zero cards) stores no evidence,
  so turn-2 compare cannot anchor; `relax_failed` fallback renders 5 unrelated
  expired (2013–2016) cards outside any turn-1 set. Cascade turn-1 also stores
  nothing → subset test SKIPS in live mode (was PASS offline).

Remain xfail for a DIFFERENT reason than recorded: none change category — all other
19 xfails reproduce with the same gates (greeting misses, closing substrings,
out-of-scope refusals, agent fallback cards, fp_valid lang mismatch).

Still never exercised end to end: the personal ClickHouse service. `is_personal_query`
is False for the acceptance query ("الطلب بتاعي وصل ولا لسه؟" — possessive gap), so
`PersonalQueryService.handle` never runs in traces; it was verified live only via
direct call (`my coupons` → honest empty answer for user 55). Catalog paths DO execute
(`catalog-handle` exits across pizza/meal/location cases).

Methodology fix: with the redis backend, harness session ids are stable across runs,
so a rerun's turn-2 anchored to the previous run's remembered offers (id=368,
expired 2013). Found via trace, fixed by flushing redis before the definitive run;
`docs/LIVE_DEPS.md` notes it. Follow-up t1 (both engines) in the final run resolves
against genuinely-shown offers only.

Anomaly (environment, unverified cause): one agent turn (`هلااااا`, run of 2026-09-22)
recorded latency_ms=2479848 (~41 min) while completing normally (2 cards). Suspected
Ollama stall, not product behavior; flagged, not asserted.

## Phase 1 addendum (same branch): query-time live-offer filtering

No reindex: expired offers stay IN the index (7,692 expired docs untouched).
New default organic behavior behind `LIVE_ONLY_ORGANIC` (core/config.py, default true):
`core/freshness.py` (`today()` on the existing REFERENCE_DATE clock, `is_live`,
`split_live`); `retrieve()` filters the candidate pool BEFORE scoring (over-fetch
x2+10; BM25 hits likewise); faceted pools (`offers_for_*`, superlatives,
`in_price_range`) take `live_only` (anchored call sites pass False); catalog
`list_*/get_*` apply `_live_rows`; `_fresh_only` leniency gated behind the flag;
new narrow `_explicit_validity_answer` cascade path (validity phrasing + resolvable
referent + expired match -> honest metadata-only text, no card, no LLM);
`build_index.py` writes `offer_status`/`valid_until` for future builds.
Qdrant native filter attempted and REVERTED with reason in code: this
qdrant-client's Range is float-only, payload expiry is a string, no payload index
— pool-level filtering covers all backends uniformly (verified by probe + traces).

Before/after (validity cases): before (unpatched) Mado cascade served live catalog
text / Arabiata cascade served 5 faceted cards with zero honest framing; after:
both cascade turns exit `validity` with honest expired text, 0 cards, 0 LLM.
Agent has no validity path (Phase 1 scoped cascade) — new xfails record it.

Acceptance deltas (`pytest tests/acceptance/` -> 20 passed, 1 skipped, 24 xfailed;
`tests/acceptance/test_freshness.py` -> 2 passed): EVERY organic path now serves
zero expired cards (greetings 5x exp=0, Tamara faceted 5x exp=0, agent tools exp=0,
catalog text live-only); `سحور` cascade is now an honest strict-floor refusal
(empty live pool) instead of 5 invented-expired cards. New xfails: validity agent
x2 (+1 lang), all citing this phase. Anchored exemption PROVEN by --now-split
traced procedure (show at --now 2026-09-21, follow-up at --now 2027-06-01 over
redis memory): cascade `followup-anchored` resolved Pizza Hut id 7270 while
expired, zero retrieval. Unit set unchanged (same 4 pre-existing failures).
Baseline post-Phase-1 (reports/verify/20260923_153121_phase1_check/):
**151/172 checked passed (was 150), hit@1 0.315 -> 0.481, hit@k 0.741 -> 0.796,
mrr 0.483 -> 0.585.** Rank-1 selection improved sharply from removing expired
candidates that were outranking the right doc — the Phase 4 target signal
appearing early; bonus-inflation work (Phase 4) is still pending.

## Phase 2 addendum (same branch): agent evidence-gate hole closed

Path inventory (agent/engine.py): the ONLY ungated route to rendered cards was
`_finish_fallback` — reached on plan_parse_error / planner_error /
replan_unusable / tool_error / tool_budget / relax_failed / replan_budget, it
unconditionally ran a semantic `search_offers` fallback (no grounding, no
evidence gate) and rendered whatever came back, including `state.evidence`
collected under the failed plan. All other routes are gated or card-free
(safety/clarify/respond/no-retrieval/no_matches-broaden-then-gate).

Fix: `_finish_fallback` serves zero cards with emptied evidence behind
`STRICT_AGENT_FALLBACK` (core/config.py, default true); legacy search-and-render
preserved under `false`. Reproduced pre-fix via trace (location query fallback
rendered KFC+Pizza Hut with zero address evidence); post-fix the same turns
answer honestly with no cards.

Regression test `tests/acceptance/test_agent_gate.py` (8 passed, no LLM):
planner mocked to raise PlanParseError on 6 varied queries (location, personal,
off-topic, ambiguous, well-formed AR/EN) + valid-plan-then-replan-failure on 2
more — all assert zero evidence, zero card markers, non-empty reply.

Acceptance deltas: `fp_gym`/`fp_valid` agent xfail→pass (fallback now honest);
follow-up agent subset test SKIPs (t1 fallback renders nothing — nothing to
compare, reported not hidden); `loc_kfc_nasr` agent stays xfail (successful-plan
search, 1 card, no address data). Suite: 32 passed, 2 skipped, 21 xfailed.
Harness fix in passing: agent per-turn evidence now comes from the yielded turn
report, not the sticky `_last_evidence` attribute (which fallback paths never
reset — previously misattributed the prior turn's cards).
Baseline post-Phase-2 (reports/verify/20260923_204001_phase2_check/):
**151/172, hit@1 0.481, hit@k 0.796, mrr 0.585 — identical to post-Phase-1**,
as expected for an agent-only change.

## Phase 3 addendum (same branch): cascade text-matching fixes

`_looks_like_greeting`: shared `_normalize_short_text` helper (extracted
verbatim) + flag-gated normalization (`SOCIAL_GREETING_NORMALIZE`, default
true) — vocative strip, elongation collapse (3+ repeats), hamza/ya fold —
before the UNCHANGED closed-set lookup. Closing check → `_is_closing_message`
(token/phrase-boundary on the normalized message + filler remainder,
`CLOSING_TOKEN_MATCH` default true). Verified at unit level: يا هلا/هلااااا
now greet; مرحبا يا باشا still misses (باشا kept out per closed-set rule);
"no more than"→thanks, "abandoned"→done, "actually"→actual all dead;
"شكرا يا باشا" still closes.

Acceptance: `greet_ya_hala` x2, `greet_hala_long` x2, `fp_cart`/`fp_valid`
cascade, `fp_valid` agent+lang — **8 xfails removed** (all hit greeting/
safety/closing/catalog-handle pre-retrieval with zero cards). `مرحبا يا باشا`
x2 stays (documented باشا limitation). Suite: 38 passed, 2 skipped, 15 xfailed.
Unit set: my greeting edit initially broke 1 passing test (fold mapped listed
"أهلاً بيك" to unlisted "اهلا بيك") — fixed additively (raw OR folded); the 2
budget-note tests pinned legacy render-cards fallback and were updated to the
Phase-2 zero-card contract with comments (not among the protected 4, which
remain untouched and still failing). Final: 285 passed, same 4 pre-existing
failures. Baseline post-Phase-3 (reports/verify/20260924_024327_phase3_check/):
**identical 151/172, 0.481/0.796/0.585** (social matching doesn't touch scoring).

## Phase 4 addendum (same branch): retrieval qualification on embedding only

`SCORE_GATES_EMBEDDING_ONLY` (core/config.py, default true) + `qual_score()`
(core/freshness.py): MIN pool floor, strict floor, relevance check + classifier,
risk log, and all three direct-answer shortcuts (threshold AND margins, with
strict unmeasured handling) read raw embedding similarity; combined_score orders
only. Lexical hits are TOKEN matches on folded text (digits excluded — prices
have their own bonus, now token-match on title + numeric equality on the price
field, so "150" can't hit "1500"). BM25-only candidates carry
embedding_score=None (0.5 kept as neutral ORDERING prior only): gates skip them
instead of judging them; direct shortcuts never fire on them. Qdrant native
date filter documented as infeasible without reindex (client Range float-only).
Recovered mid-phase: None-embedding TypeError crash in the shortcut floors
(now None-safe with exact legacy reproduction under the flag).

Measured (reports/verify/20260924_162207_phase4_check/ vs .../phase4_check2/):
**151/172 both, hit@k 0.796 flat, hit@1 0.481 -> 0.444 (DOWN 0.037), mrr
0.585 -> 0.569.** Honest negative: the target signal did NOT materialize.
Post-mortem: ordering still uses combined_score, and the token-lexical +
digit-exclusion changes shrank the very bonuses that were putting several
expected docs at rank 1 — bonuses were helping rank-1 on this suite more than
hurting it. Gates are now principled (no weak-match qualification; refusal
paths read uninflated scores — e.g. باشا now refuses at embedding 0.444), but
rank-1 selection per se wants ordering work, not gate work: input for the
turn_router design decision. Acceptance: 38 passed, 2 skipped, 15 xfailed
(basha-cascade reason updated to embedding-floor refusal; all else stable).
Unit set: 3 direct-answer guard tests updated to the embedding-only contract
(blocked-cases pass unchanged); final 285 passed, same 4 pre-existing failures.

## Phase 5 addendum (same branch): 82/225 breakdown (reporting only, no fixes)

Categorizer: `eval/categorize_full.py` (one category per failed record —
routing/intent, retrieval-quality, generation/wording, card-rendering,
language-mismatch, other; rule in file header). Baseline 82/225 splits as
**routing/intent 79, generation/wording 46, retrieval-quality 15,
language-mismatch 3, card-rendering 0, other 0**. Card-rendering never fires:
whenever retrieval is right, wording passes too (or no cards render) — the
"right offers, wrong card" failure mode does not occur in this suite.

Post-phase re-run (`--rag-only`, reports/verify/*_phase5_rerun/): **64/225**
— routing/intent 109 (+30), generation/wording 37 (-9), retrieval-quality 14
(-1), language-mismatch 1 (-2). Per-id movement: 3 fixed, 21 broke
(reports/phase5_movement.txt). Of the 21 broke, **15 are honest
refusals/empties** (the eval's expected answer is expired inventory Phase 1
now withholds — e.g. spa/gym/nail-course offers; stale ground truth, not a
regression) and 6 served-other: `offer_expiry_check_ar` is the new validity
path answering honestly ("انتهى... 2023-11-01") where the eval wants a
live-style card (correct behavior, stale expectation); zadna/wafflicious/
dentalboss/same-merchant serve LIVE right-merchant offers failing only on
strict id/keywords; `price_range_over_1000_ar` picked a worse "closest"
(paragliding 9277 over the prior pick) — the one genuine ranking shift to
watch. Fixed include `offer_nonexistent_starbucks` (served a coffee offer
for a nonexistent brand before → honest refusal now).

Net reading: the pipeline got more conservative — fewer wrong answers served
(Starbucks-class hallucinations gone, zero expired cards anywhere organic),
at the cost of refusing queries whose ground truth is expired stock. The
82->64 drop measures ground-truth staleness + honest refusal, not worse
understanding. Rank-1 ordering itself (hit@1 0.481->0.444 in Phase 4) is the
open item this breakdown points at — which is exactly the turn_router
question below.
