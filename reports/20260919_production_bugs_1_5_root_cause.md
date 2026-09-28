# Production Bugs 1–5 — Root-Cause Report

Date: 2026-09-19 · Status: REPORT ONLY (no fixes implemented) · Engines: live servers started 2026-09-18 22:38 (PID 11480, `run_servers.py`, :8000 old cascade + :8001 AgentEngine, one process)

## Method note (per-bug evidence discipline)

- Live re-tests were driven by UTF-8 python scripts (`PYTHONIOENCODING=utf-8`); PowerShell 5.1 mangles Arabic and produced one false artefact ("I didn't catch that.") — discarded, all live verbatim quotes below come from the UTF-8 repro.
- The live process imports code as of its start (Sep 18 22:38). Files edited later (Sep 19) are **not** running live: `agent/engine.py`, `agent/grounding.py`, `core/rag_engine.py` (all Sep 19 13:57) contain the uncommitted retrieval-gate / `_facade_faq_topic` / greeting-variant work. Live runs committed HEAD (`4646e56`, includes `aede429` Sep 13 about-company rules).
- In-process RagEngine tracing is impossible while the server holds the Qdrant folder lock; all reproductions are module-level offline calls or raw API calls.

---

## Bug 1 — Answer text glued together (missing spaces)

**Observed (live, UTF-8 repro):**
- Query "مرحبا" (greeting) → `_GREETING_REPLY` words run together turning "عرض ليك" into "عرضليك", "وفئة" into "ولافئة".
- Query 1 (refund policy) → `_NO_ANSWER_FOUND`: "لم أجد عروضاً مطابقة**الآن**. جرب تاجراً أو فئة**مختلفة**."
- Query 2 → clarify "…أحسن عرض**ليك**. قصدك متجر معين (زي زينتاكي)**و**لافئة…" (all tokens joined at chunk boundaries).

**Root cause chain:**
- `_chunk_text()` at `core/app.py:798` splits the finished answer on spaces into ≤24-char chunks; a chunk boundary can fall *between* two words (chunk has no trailing space).
- SSE streamer per-token loop `core/app.py:986-999` reassembles with `full_text += payload` — **no separator** — so the space lost at every chunk boundary never comes back, and `done.answer` (line 1036) returns the glued text.
- Same in the agent path (`core/agent_server.py`), sharing the glued answer; both engines feed the post-stream reassembly.

**Independent / shared:** Independent. Transport/presentation layer only (affects both engines equally).

**Fix category (NOT yet applied):** Chunk-reassembly must preserve the inter-word space (e.g. keep trailing space in each chunk, or join with a re-primed separator at reassembly). No changes to retrieval or generation.

---

## Bug 2 — Offer card shows the wrong price (Rush Hub 200 EGP shown as 400)

**Observed (live):** Offer 7188 "Rush Hub": card displays top-level summary price = 400.0 EGP (title "Games balance…", old_price 600.0, discount 33%) while the offer body's pricing tiers clearly state "Pizza meal coupon: 200.0 EGP (was 351 EGP, 43% off)".

**Root cause chain:**
- The offer's per-tier data lives only as **unstructured text** folded into `rec["text"]` (the "Pricing options" lines ~`build_index.py:404-416` fold each tier's price line into the document text).
- Live index `data/index/BAAI__bge-m3/qdrant/docs.pkl` built **Sep 9 20:55** — predates `type_prices.json` (Sep 12 21:22) and the tier-embedding commit `c030a1c` (Sep 12). It has **no `meta.tiers` / min / max / n_tiers** on any doc.
- Card renderer `_tier_card_lines()` `core/app.py:1030` and `_format_offer_card()` `core/app.py:1055` **do** support a price range when `meta["tiers"]`/`min_price`/`max_price` exist — but with tiers absent they fall back to the single top-level `meta["price"]` (400).
- A rebuild *was* attempted (Sep 12 22:01, loaded 3237 tiers across 1077 offers) but **failed to write** to Qdrant: `RuntimeError: Storage folder already accessed by another instance` — the live server held the lock (PID 11480).

**Independent / shared:** Data/schema + index-staleness defect. **Shares its upstream cause with Bug 3** (one root cause: multi-tier pricing stored as unstructured single-field text + stale index); the *symptom* is different and the fix category differs.

**Fix category:** (1) rebuild the index from the current schema so `meta` carries structured tiers/min/max; (2) verify rebuild actually lands in Qdrant (lock handling, or stop-the-world rebuild window). Code renderer already handles tiers — no code change expected.

---

## Bug 3 — Non-pizza merchants in "pizza" results (first 5 cards: Anas, Pizza Hut, Chuck E. Cheese, Pizza Station, Rush Hub)

**Observed (live):** Query "بيتزا" → answer header "عثرت على عروض مناسبة:" and (in order) Anas 95 EGP, Pizza Hut 275 EGP, Chuck E. Cheese 450 EGP, Pizza Station 149 EGP, Rush Hub 400 EGP.

**Root cause chain (NOT constraint loss):**
- Grounding is verified working: planner args `{merchant: Waffarha, category: Pizza, price_range: [0,1000]}` → `ground_search_args()` → cleaned `{"product": "pizza"}` (drops the unconfirmed Waffarha/price facets, keeps corroborated product). Executed path = `offers_for_product("pizza")`.
- Pool construction is **substring matching over the full folded `rec["text"]`** — `core/faceted.py:416-426` scans `rec["text"].lower()` for any product variant. Because Bug 2's root cause folds pricing-tier lines into that text, a tier that happens to name the product ("Pizza meal coupon", "Chuck E. Cheese pizza…") tags the whole offer regardless of its real category.
- Ranking `core/faceted.py:686` sorts by `(sold, discount)` — matches the live card order exactly (offline `offers_for_product('pizza')` returns the identical 6-offer pool, top-5 ordering equal to the live cards).
- The six pizza-pool offers (8291 Anas, 7270 Pizza Hut, 7162 Chuck E. Cheese, 7729 Pizza Station, 7188 Rush Hub, 6990 ROMA Pizza To Go) all contain a "pizza" substring only in tier/pricing-options lines.

**Independent / shared:** Shares the **upstream root cause with Bug 2** (unstructured tier text in `rec["text"]` + stale index). Bug 3 has an *additional* logic defect: the product matcher consumes that unstructured text, so **a rebuild alone does not fix Bug 3** (Rush Hub's tier line would still match).

**Fix category:** Tier-aware matching: exclude pricing-options lines from product-substring tagging (or match only structured/category metadata instead of full text); then rebuild the index. Two-part fix — rebuild **plus** matcher change.

---

## Bug 4 — "Cheapest" returns a 0 EGP offer expired in 2017 (Dental Boss)

**Observed (live):** "ارخص عرض" / superlative follow-up → Dental Boss offer o-2449, **0 EGP, expiry 2017-11-15**, surfaced as "the cheapest available".

**Root cause chain:**
- `SuperlativeOfferTool` `agent/tools/cascade_tools.py:256` has an `input_schema` with only `direction/merchant/category` — **no `product` field**. `coerce_args()` `agent/tools/__init__.py:71` silently drops unknown keys, so the grounded `product=pizza` merged by grounding is discarded.
- Planner for "أرخص واحد فيهم إيه؟" → `superlative_offer` with a **hallucinated** `merchant="KFC"` → grounding drops the unconfirmed merchant → call becomes unscoped.
- `cheapest()` `core/faceted.py:710` — with an explicit comment (line 711-712) that expired/0-price offers are **intentionally NOT filtered** — returns Dental Boss (lowest literal price).
- `_finish_fallback()` `agent/engine.py:876` calls `search_offers` with bare `{"query":…, "limit": 6}` — ungrounded.

**Independent / shared:** Independent. Schema gap (no product facet) + deliberate no-freshness policy + hallucinated-merchant drop.

**Fix category:** (1) add freshness/validity filter to the superlative pool (expiry in the past ⇒ excluded; zero-price excluded unless zero is a real tier); (2) add `product` to `SuperlativeOfferTool.input_schema`; (3) scope the fallback path with the grounded args instead of an empty query.

---

## Bug 5 — Retail/FAQ queries

### 5q1 — Refund-policy query falls back to "no offers found"

**Observed (live):** "عايز اعرف سياسة الاسترجاع" → `_NO_ANSWER_FOUND` ("لم أجد عروضاً مطابقة…").

**Root cause:** The `faq_refund_policy` document exists in `data/faqs.json` (Sep 12) but is **absent from the live Qdrant pickle** (docs.pkl Sep 9). Retrieval finds no doc → empty evidence → `_finish_fallback`/no-answer text. (Same staleness as Bugs 2/3.)

**Independent / shared:** Shares the *index-staleness* root cause with Bugs 2/3, but the missing-doc ticket is its own doc — filed separately (`20260919_ticket_faq_refund_policy_missing_doc.md`).

**Fix category:** Rebuild index (same event as Bug 2), then re-verify the FAQ doc is retrievable.

### 5q2 — "ما هي وفرها؟" / company info -> clarification question instead of FAQ answer

**Observed (live):** "ما هي وفرها؟" → `_clarify` text "_عشان ألاقي أحسن عرضليك. قصدك متجر معين (زينتاكي أو أمازون) وفئة (زي إلكترونيات أو ملابس أو أكل)؟_" (verbatim; note the Bug 1 glue).

**Root cause chain (fully reconciled this session):**
- The planner prompt is built with the **real tool registry order** (`core/app.py:242` catalog-first ⇒ `search_offers` listed first; not cascade-first). Reproduced 5/5 with the exact committed-HEAD `_turn_params` bundle: `"ما هي وفرها؟"` (with question mark) → **`search_offers`, intent `offer_lookup`, args all null/ungrounded**. (Earlier `retrieve_faq` capture was a *registry-order artefact* of a scorecard script that registered cascade-first — the real engine order never produced it.)
- Ungrounded `offer_lookup` + no entity/reference/known ⇒ `_needs_clarification()` `agent/engine.py:788` (case c / tail rule) ⇒ `_clarify()` `agent/engine.py:861` — the observed reply. This is **deterministic**, not model noise: identical outcome on repeated runs.
- Without the question mark ("ما هي وفرها") the planner picks `search_offers`, intent `catalog`, args merchant=Waffarha/category=all — broad browse (different path).
- The about-company FAQ rules (`aede429` Sep 13) exist and the `faq_12_about` doc **is** in the index — but the planner never chooses `retrieve_faq` for this phrasing, and the engine-level FAQ-topic override (`_facade_faq_topic`) that could fix it **is not running live** (it is uncommitted Sep 19 code, absent from committed HEAD) — and per the working tree it would not have helped either, since `_RagFacade` doesn't bind `_route_faq_topic` (`__getattr__` passthrough → `None`) so the override never fires.

**Independent / shared:** Independent. Planner prompt/tool-ordering behaviour interacting with the dead FAQ-topic override.

**Fix category:** (1) make the deterministic FAQ topic override actually wired (route via the module-level `_route_faq_topic`, not the facade attribute); (2) or push company/about phrasings (ما هي وفرها / معلومات عن الشركة / What is Waffarha) earlier in the router before planner defaults. Also note the `unclear`/greeting-against-catalog work in the uncommitted tree only partially addresses this.

---

## Cross-cutting summary

| Bug | Root cause | Shared? | Fix category |
|---|---|---|---|
| 1 | chunk reassembly drops spaces (`app.py:798`/`986`) | no | transport/streaming |
| 2 | multi-tier price in unstructured text + stale index (no tiers in pickle; rebuild blocked on lock) | **with #3** | index rebuild (data/schema) |
| 3 | product matcher substring-scans folded text incl. tier lines + stale index | **with #2** | tier-aware matching + index rebuild |
| 4 | superlative: no product facet, no freshness gate, fallback ungrounded | no | tool schema + freshness + grounded fallback |
| 5q1 | FAQ refund doc absent from live pickle | staleness-shared with #2/#3 | index rebuild |
| 5q2 | planner tool-order/params ⇒ ungrounded search_offers; FAQ override dead/not-live | no | wired FAQ override or earlier router rule |

Raw companion evidence: `offers_for_product('pizza')` pool, `/tmp` planner scripts (`plan_stability.py`, `plan_live_params.py`, `plan_lean_vs_full.py`), UTF-8 live repro script, `build_index_qdrant_rebuild.log`, `git show HEAD:agent/engine.py` greps.