# Stage 1 Router Design (pre-planner intent router)

Status: DESIGN NOTE for review. No code written, nothing committed after the
.gitignore fix. Waiting on sign-off before any router implementation.

## Purpose

The gate shipped in gate_wip.patch is a post-planner retrieval block; the
spec requires a pre-planner intent router with a cheap path that never
invokes the planner. This note is the single coherent design replacing the
six KEEP-MODIFIED items, so the router is built as one reviewed whole, not
six patches stitched together.

## 1. Unified intent table

Router output is one of the intents below; each row states the deterministic
signals that decide it and whether the planner is required at all.

| Intent | Deterministic signal(s) | Planner needed? |
|---|---|---|
| greeting | exact/contains core list (merged table, section 2); no offer-topic word | **no** — template reply |
| smalltalk | short, non-refusable chit-chat ("كيفك", "عامل ايه", "كلمني") not in greeting list | **no** — template/mild affordance reply |
| thanks | thanks words (شكرا, تسلم, thank you...) exact/contains | **no** — template reply |
| farewell | closing words (_CLOSING set: باي، مع السلامة، goodbye) | **no** — template reply |
| offer_search | offer-topic word (عروض/offers/deals/coupons) + entity or explicit browse; or default when nothing else matched and query is a real request | **yes** — planner plans tool+args |
| offer_detail | get_offer signal: merchant name alone, "دلوقتي" single offer, coupon id mention | **yes** — planner resolves get_offer vs search |
| offer_compare | comparison words (قارن/الفرق بين/against/vs) | **yes** — planner picks compare_offers args within budget |
| offer_recommend | "أحسن/افضل/arakhs/best/cheapest/deal me" on offers | **yes** — planner routes superlative/compare |
| faq | `_route_faq_topic` hit (payment/refund/status/how-to/company), or FAQ keyword w/o offer word | **no** — direct to retrieve_faq tool plan |
| payment_methods | payment verb + wallet/brand (pay_* rules) | **no** — retrieve_faq (faq_3 / payment_<n>_info) |
| order_status | status rules + meaning cue, or "طلبي فين/حالة الطلب" | **no** — retrieve_faq (purchasing_status_n) |
| complaint/escalation | complaint words (شكوى, غلط, مشكلة, اشتكي, أرضني) | **no** — deterministic escalation reply + capture topic |
| out_of_scope | GK/identity regex, non-Waffarha topic, refusals (checked BEFORE any planner delegation, Gap 2) | **no** — _OUT_OF_SCOPE reply |
| adversarial | `_looks_like_injection_attempt`, prompt-injection patterns | **no** — injection reply |

Decision order (fixed, reviewed as a whole):
1. **adversarial** (first — highest priority, safest)
2. **greeting/thanks/farewell** (only if NO offer-topic word present; an offer
   word anywhere flips it to offer_search — the gate's "offer wins over
   opener" rule, kept)
3. **faq / payment_methods / order_status** (via `_route_faq_topic` + keyword sets)
4. **complaint/escalation**
5. **offer_compare / offer_detail / offer_recommend** (subset signals)
6. **offer_search** — the (a)-(d) positive signals from Gap 2 (offer-topic
   word, explicit-browse phrase, resolvable catalog entity, OFFER_LOOKUP).
   NOT a blanket "default when nothing else matched": the residual is decided
   by the guards and the planner verdict below, and offer_search only fires on
   an actual positive signal.
7. **out_of_scope — a signal-triggered pre-planner guard, NOT a last-resort
   catch-all.** The OOS regex (GK/identity, non-Waffarha topic, refusals) is
   checked BEFORE the "delegate to planner" fallback. Any message whose
   subject matches OOS resolves to out_of_scope with `_OUT_OF_SCOPE` reply,
   zero planner calls. It sits after steps 1-6 only in position ordinal, not
   as a bucket for "whatever is left": a GK question (e.g. "من هو رئيس مصر؟")
   that fails (a)-(d) is intercepted here by the OOS regex, not delegated to
   the planner.
8. **length guard** → `unclear` when `message is empty OR normalized length
   < 2 OR pure punctuation/symbols`: asdf, "و", "!!!" → clarify path, no
   planner.
9. Only after steps 1-8 all fail does the router delegate the residual (a
   real, well-formed, weak request with no positive signal and no OOS match)
   to the **planner's verdict** (planner returns `unclear`/`clarify`/
   `respond_one`, never `catalog`; see Gap 2).

## 2. Unified greeting/phrase table (single source of truth)

**One table, one owner.** The duplicate pair (`core.rag_engine._GREETING_PHRASES`
and `agent/engine.py::_GREETING_OR_THANKS_PHRASES`) is merged into a single
module-level constant in `core/greetings.py`:

- `GREETING_PHRASES`: pure greeting set (hi, hello, مرحبا, اهلا, صباح الخير,
  هلا...).
- `THANKS_PHRASES`: شكرا/تسلم/متشكر/thank you/thanks.
- `SMALL_TALK_PHRASES`: كيفك, عامل ايه, ازيك (diaspora), كلمني...
- plus the normalizer `normalize_greeting_text` (diacritics, hamza, tanween
  collapse) and the matcher `is_greeting_turn(text)` used everywhere.

Location rule: `core/greetings.py` (import-light module, no rag_engine, no
planner deps). It is the **only** greeting source. Consumers:
- `core.rag_engine` calls it instead of its private `_GREETING_PHRASES`
  (which is converted to re-exports, keeping old call sites and tests green).
- `agent/engine.py` and the router import from it — no local duplicate.
- `core.rag_perfection.classify_intent_robust` and the safety gate both go
  through the same table.

**Spec phrases confirmed missing and added here:** `عامل ايه` (not in either
list — verified), `ازيك` (only in engine list, not core), and `izayak`
(never present; only `ezayak` variants existed). All three land in the single
table. Founder/Arabizi variants (azayak, izayk, izay, 3amel eh, 3amal eh,
39amel eh) normalize via the normalizer before matching.

## 3. Call sequence — cheap path exits BEFORE the planner

Current gate order (post-planner block) vs new order (pre-planner exit):

```
NEEDED:
  _turn_params
    → router.intent(query)              # deterministic, no LLM
    → if intent in NO_PLANNER_INTENTS:  # greeting/smalltalk/thanks/farewell/
        answer = template_table[intent] # faq/payment/order/complaint/out_of_scope/
                                        # adversarial → also no planner
        emit decision trace (reason_code=intent)
        return {answer, no tool call, evidence=[]}
    → else if OOS regex matches:        # pre-planner guard, BEFORE delegation
        answer = template_table[out_of_scope]
        emit decision trace (reason_code=out_of_scope)
        return {answer, no tool call, evidence=[]}
    → else if intent == unclear from the
      offer_search (a)-(d) evaluate:    # length guard: unclear → clarify,
                                        # no planner
      answer = template_table[unclear/clarify]
      emit decision trace (reason_code=unclear)
      return {answer, no tool call, evidence=[]}
    → else: planner call (offer_* intents ONLY reach the planner)
      build_plan_prompt(...) → call_planner(...)
      ...existing plan path, then RETRIEVAL GATE stays as a backstop...
```

Guarantee: for every `NO_PLANNER_INTENTS` member AND for every message that
matches the OOS regex, `build_plan_prompt` and `call_planner` are **never
executed** — not "checked before the tool", but before the prompt is even
assembled. The planner only ever runs for offer_search/offer_detail/
offer_compare/offer_recommend or the residual delegate (a real request with
no positive signal and no OOS match). Nothing reaches the planner/LLM
without first clearing the OOS check.

The existing `RETRIEVAL GATE` block (engine.py `_execute`) stays as a
belt-and-braces backstop: even if the planner path sneaks a blocked turn
through (e.g. a model returning an offer tool for a greeting), the gate still
blocks it — zero cards, same metrics/trace. It is no longer the primary
enforcement, only the last line of defense, so a future planner change can't
silently re-open the hole.

## 4. FAQ is a first-class pre-planner intent

The dead `_facade_faq_topic` override (engine-side, `_facade_faq_topic`) is
**not resurrected**. Instead the router makes `faq` a real intent:

- Router calls the **same** `core.rag_engine._route_faq_topic(query, nq)`
  now used by `RetrieveFaqTool.run` via `_lazy`. The router lives in a
  module that imports from core, so no getattr/facade indirection and no dead
  wiring — direct import, frozen at load time, matching FIX 2's behavior.
- On a hit: build a deterministic mini-plan `tool=retrieve_faq,
  args={query}` and hand it to the **existing** tool-execution path —
  identical to today's committed behavior when the planner picks
  retrieve_faq, so `_get_faq_direct_answer`/faq docs/answer scoring are all
  reused untouched.
- On a miss: fall through to the keyword tier (payment_methods / order_status
  keyword rules as proxies) then to planner planning `retrieve_faq`, then to
  offer_search.
- Consequence: `مش عايز عروض، عايز اعرف سياسة الاسترجاع` routes to
  retrieve_faq pre-planner regardless of what the planner would otherwise pick.
  The engine-side post-plan reroute and its dead façade binding are deleted,
  and `test_faq_topic_query_reroutes_to_retrieve_faq_not_offer_search` is
  rewritten against the router (asserting the intent is `faq` and the tool is
  retrieve_faq with zero planner calls).

## 5. payment_methods / order_status / complaint — not a dead end

These intents don't just route — they need real data. Current wiring today:

- **payment_methods**: real data exists — `payment_<n>_info` FAQ docs and
  `faq_3` in the corpus, reached via `_route_faq_topic` pay_* rules →
  `RetrieveFaqTool` → `_get_faq_direct_answer` → corpus doc. On match the
  router emits a retrieve_faq plan; a "no data" fallback is the existing
  clean-negative path (negative → semantic search → if still nothing, the
  `_NOTOOL_RESPONSE` style answer). Sketch: query user's wallet/brand if the
  pay rule demands one, else answer from faq_3.

- **order_status**: data exists for status *meaning* docs (`purchasing_status_n`
  corpus entries, reached via status_* rules + meaning cue). But "حالة طلبي"
  (my actual order's live status) needs per-user order integration which does
  **not** exist in the codebase today. Sketch: router answers the *meaning*
  tier now (what each status label means, via FAQ docs); a live-order lookup
  intent is recognized and captured in the trace/metrics as an explicit
  recognized-but-undeliverable request, answered with a deterministic
  "I can explain statuses, but live order lookup isn't wired yet" reply
  (never an offer dump). Wiring the real order API is future Stage-5+ work,
  explicitly out of scope for this router, and stated so the router doesn't
  fake an answer.

- **complaint/escalation**: no corpus or service exists; the router's job is
  routing + safe handoff, not a fake resolution. Sketch: capture the topic in
  the trace/metrics, reply with the deterministic escalation notice
  (acknowledge + give the human support path), never call a tool, never dump
  offers. Full complaint tooling is a future stage; the router guarantees the
  turn doesn't degrade into an offer search.

## 6. test_agent_engine.py plan

Module-level imports at lines 14-23 couple all 33 tests to gate symbols
(`_greeting_or_thanks`, `_has_offer_topic_signal`, `_looks_like_explicit_browse`,
`_retrieval_allowed`). Plan:

- **Keep as-is (rewrite only the imports line):** the REG batch
  — `test_normal_plan_tool_gate_render`, `test_planner_error_triggers_fallback`,
  `test_no_known_merchant_fallback`, `test_price_gate_failure_triggers_relax`,
  all `test_stage3_*`, `test_coerce_span`, `test_widen`,
  `test_recent_offers_keep_metadata_shape_for_tools`,
  `test_report_is_structured_decision_trace`, `test_unknown_next_action_is_plan`,
  `test_evidence_note_en`, `test_grounding_drops_invented_merchant_before_tool_call`,
  `test_grounded_product_reaches_tool_args`.
- **Rewrite test-by-test against the router:** the SPEC batch that currently
  couples to gate internals — `test_retrieval_gate_zero_cards_for_non_retrieval_turns`
  (re-expressed as "no planner + no tool + template answer" assertions),
  `test_retrieval_gate_decision_pure_function` (re-expressed against the
  router's intent table), `test_low_signal_catalog_default_clarifies_not_dumps`,
  `test_explicit_browse_still_returns_catalog` (now offer_search),
  `test_planner_unclear_intent_clarifies`, `test_faq_topic_reroute_leaves_offer_lookup_alone`,
  and the two FAQ override tests (one deleted as dead-implementation; the
  positive case rewritten as a router test, see section 4).
- **Strengthen weak assertions:** greeting/gibberish/injection safety-gate
  tests get exact-template assertions instead of "non-empty or 'offer'".
- **Drop:** `test_browse_marker_helpers` (stop-gap marker tables are removed
  by the router; browse is now the offer_search intent). `_looks_like_explicit_browse`
  and `_has_offer_topic_signal` cease to exist as gate functions.

Net: router lands with a rewritten `tests/unit/test_agent_engine.py` — split
if the import coupling is painful, otherwise cleaned in place — plus a new
`tests/unit/test_intent_router.py` covering the intent table decision order,
the cheap-path-before-planner guarantee (asserting no plan prompt was built),
the greeting table merges (عامل ايه / ازيك / izayak), and the faq/payment/
order/complaint routing. The 255-pass baseline + surviving tests stay green.

## Open items (for sign-off)

1. greeting vs smalltalk split: is "كيفك/عامل ايه" greeting (same reply as
   hello) or a distinct smalltalk template? This note keeps them distinct,
   third second close to call.
2. complaint/escalation reply text + whether escalation should include the
   user's user_id in the trace (privacy/monitoring call).
3. Whether order_status meaning-tier answers belong to the FAQ corpus
   (existing purchasing_status_n docs) or need a new corpus section. The
   router can answer either way today.

---

# Addendum (per review round 2 — gaps closed, decisions locked)

## Gap 1 — adaptive result count (1-5): OWNED BY STAGE 2, router passes through

Decision: **Stage 2's responsibility.** The router assigns an intent and (where
relevant) a parsed `limit` when the user's phrasing dictates one; the Stage 2
planner/tool layer owns the adaptive-count behavior and the final clamp.

Concretely, today the count lives entirely in Stage 2: `engine.py:372`
`target = _clamp(int(constraints.get("limit") or 6), 1, 12)` then
`args.setdefault("limit", target)` (engine.py:372-375), with the same default
repeated in the metrics/steps bookkeeping (`or 6` at engine.py:425, 454). The
router does **not** touch limit for generic offer searches — it passes
`constraints` through untouched.

The router's only role in counts: the cardinality hints a user explicitly
says. `"هادي كام إنترية"`-style phrasing is **not** parsed by the router;
plurality is a Stage 2 signal. The router never emits a hardcoded `5`, and
nothing in the router path introduces a new constant — the existing
`1..12` clamp and `6` default remain Stage 2's, unchanged, and Stage 2 is
held responsible for making the count adaptive (a committed follow-up task
against the existing clamp, not new router surface).

Stated plainly so it cannot slip: **adaptive 1-5 is a Stage 2 requirement,
tracked as such; the router does not claim it and does not block it.** It will
be implemented where the clamp lives (engine.py:372), reviewed, and tested.

## Gap 2 — offer_search default vs. unclear boundary: the literal rule

The distinction is not vibes; it is the **planner's explicit verdict**, with
the router drawing a single concrete pre-planner line for the degenerate cases:

1. Router order (unchanged from section 1): adversarial → social
   (greeting/thanks/farewell/smalltalk) → faq family → complaint →
   offer_* subset signals → **offer_search default** → (nothing matched).

2. The literal rule for **offer_search vs. unclear/other**:

   - If the input survives the higher tiers and any of these hold, it is a
     real offer request → **offer_search** (planner runs, limit untouched):
     (a) contains an offer-topic word (`عروض`, `العروض`, `offers`, `deals`,
     `كوبون`, `coupon`, `خصم`, `discount`, `سعر`, `price`) — normalized, from
     the single normalizer (Gap 3);
     (b) contains an explicit-browse phrase (`show me`, `وريني`, `عايز اشوف`,
     `شوف العروض`) — the gate's marker tables, now moved into the router's
     offer-search signal set;
     (c) contains **any** catalog-entity noun resolvable through
     `facaded.resolve_merchants` / `resolve_category` / `resolve_product`
     (a merchant name like "كنتاكي"/"KFC", a category, a product);
     (d) `classify_intent_robust` returned OFFER_LOOKUP for genuine content.
   - If **none** of (a)-(d) holds **and** the message is real but weak, the
     router still delegates to the **planner's** verdict: the planner
     (planner.py:102-106) is instructed that an unrecognizable/low-content
     message must return intent `"unclear"` with `next_action="clarify"` or
     `"respond_one"`, NEVER `"catalog"`. The existing engine behavior
     (`_needs_clarification`, engine.py:841-859, which already treats
     `"unclear"` and bare-browse-without-signal as clarify) then fires.
   - **One obligation is placed on Gap 2 BEFORE this delegation may run: the
     OOS regex check.** The out-of-scope signal set (GK/identity patterns
     such as `من هو`/`who is`, non-Waffarha topics, refusals — fed by the
     unified normalizer from Gap 3) is evaluated on the message **before**
     anything is handed to the planner. If the OOS regex matches, the turn is
     `out_of_scope` unconditionally: `_OUT_OF_SCOPE` reply, no planner, no
     tool. The check is NOT contingent on what the planner would have said —
     a GK question must not consume an LLM call to be correctly refused.
     (This deliberately does not sit before the offer_search *positive*
     signals (a)-(d): the "offer word wins over a GK-sounding opener"
     principle, e.g. "أهلاً، ايه أرخص عروض عندكم؟", is kept — an explicit
     offer ask is still offer_search even if it opens like smalltalk. The
     requirement is narrower and stricter: **nothing reaches the
     planner/LLM without first clearing the OOS check.**)
   - The **only** router-side pre-planner terminates for this tier are:
     (i) `message is empty OR normalized length < 2 OR pure
     punctuation/symbols` → `unclear`/`out_of_scope` immediately, no planner
     — the concrete boundary for "w", "asdf", "!!!"; and (ii) the OOS regex
     check above. Everything else that survives the higher tiers, the
     positives (a)-(d), and these two terminates is a **residual delegate**.

So: "w" → fails (a)-(d) (no offer word, no browse phrase, no resolvable
entity, not OFFER_LOOKUP), fails the OOS regex (no GK/identity subject), and
fails the length test → router says `unclear`, no planner, clarify. "من هو
رئيس مصر؟" → fails (a)-(d) (no offer word, no browse phrase, no resolvable
catalog entity; `classify_intent_robust` is documented unreliable on
short/ambiguous input — Phase 2 report item F4 "offer leak on non-intent" —
so it is not trusted for this well-formed GK question), **then the OOS regex
check fires first** (`من هو` → GK/identity) → `out_of_scope`, `_OUT_OF_SCOPE`
reply, zero planner calls. "هات عروض كنتاكي" → (a) + (c) → offer_search,
planner runs. The gate's old `_has_offer_topic_signal` heuristic is retained
in substance but relocated: browse phrases and offer words become the
router's offer-search positive signal, and the "catalog default with no
signal must clarify" rule stays in the planner prompt +
`_needs_clarification`, which is where the gate's stop-gap node lived anyway.
The OOS veto (checked after the offer-search positives, before the residual
delegate) is the new, non-negotiable pre-planner guarantee.

## Gap 3 — ONE normalization layer for every signal, not just greetings

Decision: **unified normalization feeds all router signals.** The single
normalizer from section 2 becomes `normalize_router_text(text)` in
`core/greetings.py` (renamed mentally as the shared text-normalization module
or moved to a `core/normalize.py` if the name misleads — decision below), and
**every** router signal type runs through it:

- greeting / thanks / smalltalk tables (section 2) — normalized exact/contains
- offer-topic markers (`عروض`, `offers`, `خصم`...) — normalized contains
- explicit-browse phrases (`وريني`, `show me`...) — normalized contains
- out-of-scope regex (`who is`, `من هو`...) — normalized subject text
- FAQ keyword rules and the `_route_faq_topic` call — the blob `query +
  normalized_query` already exists; the normalized form used is the **same**
  normalizer output, so `izayak`-spelling variants and hamza/diacritic
  differences can never diverge between lists again

  (explicitly: today there are **three** distinct normalizers: engine's local
  `_strip_diacritics_and_noise` (engine.py:1339), `core.rag_perfection.
  normalize_arabizi_and_arabic` (rag_perfection.py:66), which feeds
  `_route_faq_topic`'s `normalized_query` at rag_engine.py:3797 and the FAQ
  tool path, and the facade's passthrough. All three collapse into the one
  `normalize_router_text` from this section; `_route_faq_topic` receives its
  `normalized_query` from the unified normalizer, and the old names are
  re-exported so existing call sites and tests stay green)
- adversarial patterns (`ignore all instructions`, prompt-injection) —
  normalized contains

No intent brings its own ad hoc text handling. The one place this does **not**
apply is the deep model-side `classify_intent_robust` LLM fallback (it takes
raw text), but that runs **after** the deterministic router and only for
genuine offer-seeking candidates that already passed the shared normalizer.
The normalizer is trivial (strip diacritics + collapse hamza variants + fold
case + bound punctuation, as in `_strip_diacritics_and_noise`, engine.py:1339-
1347) — the point is that there is exactly **one** such function consumed by
all of the above, defined once, imported everywhere, and unit-tested once
(any query in the tables with any diacritic/hamza/case variant matches).

## Open item 2 — complaint/escalation user_id: NO PII logging, confirmed

Convention check result: `user_id` and `identity` are **param-only** today.
They flow into `_turn_params` (engine.py:91-148), are exposed to tools via
`_tool_context` (engine.py:1038-1041; `ctx.user_id`, tools/__init__.py:42), and
are used by `_personal_subjects` (engine.py:165-172) and the catalog personal
service (cascade_tools.py:368,396) — but they are **never written** to
`AgentState.metrics`, any `trace_step`, or the `snapshot()` (state.py:115-150
contains no user_id/identity field). There is no existing convention of
logging user_id in trace/metrics.

Therefore: **the complaint/escalation trace captures topic + intent only.**
`user_id` is NOT added anywhere in the router path. No new PII-logging
behavior is introduced; this matches the existing engine convention and stays
that way until a data-handling policy exists.

## Open item 1 & 3 — locked as approved

1. greeting vs. smalltalk: **distinct**, as designed (separate template rows,
   same shared table module).
3. order_status meaning-tier: **use the existing `purchasing_status_n` docs**;
   live lookup stays an honest-negative ("not wired yet"), consistent with
   payment_methods and complaint/escalation.

## Resulting changes to the design (supersedes any earlier line)

- Section 1 row `offer_search`: replaced per Gap 2 (the (a)-(d) rule + planner
  verdict + length guard).
- Section 1 decision order: **out_of_scope is elevated from last-resort
  catch-all to a signal-triggered pre-planner guard.** The OOS regex veto is
  checked after the offer_search positives (a)-(d) and before the
  delegate-to-planner fallback; a GK/identity message (`من هو رئيس مصر؟`)
  resolves to out_of_scope with zero LLM calls. The step-7 "only when nothing
  else matched" framing is withdrawn.
- Section 2 `/` Gap 3: one normalizer, one home module, all signals feed it.
- Section 1 row `offer_search`: "never hardcode 5" is carried by **Stage 2**
  (the existing clamp at engine.py:372), router passes counts through.
- Section 5 complaint row: no user_id in trace, topic+intent only.

## Addendum (2026-09-20) — A2 phase-1 delivery + finding 3 note

### A2 phase-1 (router phase 1) landed as an isolated diff
- `core/greetings.py` (new): unified `GREETING_PHRASES` / `THANKS_PHRASES` /
  `SMALL_TALK_PHRASES` / `FAREWELL_PHRASES`, `normalize_router_text`,
  `social_turn_kind`, `is_greeting_turn`.
- `core/intent_router.py` (new): `intent(query, *, catalog, classify)` —
  decision order above, cheap intents never build a plan prompt.
- `core/rag_engine.py`: `_GREETING_PHRASES` is now a re-export union
  (`GREETING_PHRASES | THANKS_PHRASES | SMALL_TALK_PHRASES`), a strict
  superset of the historical literal set — all legacy call sites and tests
  keep exact behavior; only existing-file change in this diff.
- `tests/unit/test_intent_router.py` (29 tests): decision order, findings 1-2
  fixtures, cheap-path-before-planner (classifier spy raises if called),
  faq/payment/order/complaint, offer positives (a)-(d), OOS veto, noise guard,
  legacy shim.
- No `_turn_params` wiring, no edit to `core/faceted.py`, engine tables not yet
  deleted: that is C1, blocked on GATE-1 sign-off of this diff.

### Finding 3 — root cause + disposition
Planner trace (5/5 runs each): both "عروض البيتزا" and "عايز بيتزا" map to the
same `search_offers` tool, intent `catalog`, identical args
`{"query":"بيتزا","merchant/category/product":null,...,"limit":5}`. NOT two
different planner paths — the treated differently is entirely in the post-plan
fallback templates:
- `_NOTOOL_RESPONSE` (engine.py:1286-1290) — used when the chosen tool group
  returns no tool, reached via `_render_answer` (:951/:955) and `_turn_respond`
  (:981);
- `_NO_ANSWER_FOUND` (engine.py:1291-1295) — used when retrieval/tool output
  yields nothing, reached via `_finish_fallback` (:888), `_render_cards`
  (:964), `_render_single` (:974).

Disposition: consolidate these two into ONE canonical "no matching offers"
message at C1. No behavioral change to the router; the templates are cosmetic
and both are user-facing no-result replies.