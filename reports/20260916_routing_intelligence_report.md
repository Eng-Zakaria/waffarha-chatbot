# Waffarha Chatbot — Intelligence / Intent-Understanding Investigation Report

Date: 2026-09-16 · Evidence base: full behavioral battery (33 queries), in-process AgentEngine traces (`trace_live.jsonl`), static reads of both engines, data/index + refresh-log audit.

Mandate: determine from code **and from reproduced live behavior** whether the system exhibits genuine intent understanding or whether queries are pattern/alias-matched and forced through the retrieval pipeline. No redesign was performed.

---

## 1. Executive Summary

The system does **not** exhibit genuine intent understanding. Both engines share a single design: a **deterministic, keyword/alias/pattern pipeline with one small local LLM (qwen2.5:3b-instruct) used only as a *structured slot-filler*** that classifies an intent label and extracts tool arguments. There is no semantic comprehension of the user's goal; there is no reasoning over the user's actual referents; there is no real dialogue state. The AgentEngine's single LLM "planner" call is a **typed action extractor**, and everything after it is lookup plus deterministic validation.

Evidence for the headline finding:

- **There is no comprehension layer.** The only source of "understanding" is `planner.py`'s one prompt-to-structured-JSON call. The entire rest of the pipeline is `resolve_merchants / resolve_category / resolve_product / price_range` — alias and keyword regex resolvers (`core.faceted.py` `normalize_arabic`, `MERCHANT_ALIASES`, `catalog_queries.py`).
- **The comprehension that exists is lossy and unverified.** The planner regularly **drops the user's product/category**: "عايز pizza من 100 لـ120" produced plan `{search_offers, price_range:[100,120]}` with `entities:{}` and `grounding:{}` — the pizza constraint vanished; the "grounding" pass could not add it back because `resolve_product("بيتزا")` did not corroborate (trace_live.jsonl). The live HTTP battery then showed the agent replying with 2013–2014 "price 100" offers for a pizza request.
- **Ambiguous discourse is machine-guessed, not understood.** Greetings, gratitude, gibberish, and OOS questions are all heuristically/discretely classified. `ازيك؟` (a greeting) is **not** in any greeting list and both engines answered with *random expired offers*. `What is Python?` in a memory-loaded session was re-routed to the offer catalog (memory changes router verdicts).
- **Conversation-memory exerts no semantic grounding**; it changes routing outcomes in harmful, opaque ways (see §8).
- **Section 7 validity finding is decisive:** `INCLUDE_EXPIRED_OFFERS=true` in `.env` → the production index is built **with ~99.9% expired offers** (9036/9048 expired as of 2026-09-16; only 6 unique live). The bot therefore *correctly reproduces* what its data allows, but "understanding" is largely cosmetic: most offered answers are canned RAG blocks over data that is demonstrably stale by years.

---

## 2. Actual Runtime Architecture

One process serves two engines (`run_servers.py`):

- `core.app:app` on `:8000` — **RagEngine** cascade (`core/rag_engine.py`, ~4000 lines): deterministic `_detect_intent` + multi-stage cascade: greetings → FAQ topic route (`_route_faq_topic` keyword topics) → direct FAQ answer → superlative/compare via `FacetedCatalog` → semantic `retrieve` → canned templates → identity. A **second-pass LLM** only classifies the user message rephrase / answer style (answer passed through `qwen2.5:3b` via `ollama` for *phrasing* and FAQ detection).
- `core.agent_server:app` on `:8001` — **AgentEngine** (`agent/engine.py`): bounded, tool-calling loop.
- Shared: `BAAI/bge-m3` embeddings, the same **single Qdrant collection folder** (file-locked; concurrent loads serialized), same `SessionMemory` (`memory/memory.py`) and same `FacetedCatalog` under the hood.

### AgentEngine data flow (mapped from code + traces)
1. **Safety gate** (deterministic): injection/gibberish patterns → hard reject.
2. **One planner LLM call** (`agent/planner.py`): `_KNOWN_TOOLS=("search_offers","get_offer","retrieve_faq","compare_offers","superlative_offer","catalog")`, `_KNOWN_FIELDS` whitelist; output = `{intent, tool, constraints, entities, references}`; validation via `PlanParseError`; failure → `plan_parse_error`.
3. **Deterministic grounding** (`agent/grounding.py`): every proposed entity must be corroborated by the *user's own words* through `faceted.resolve_*`; unconfirmed entities are **dropped** and recorded as `unconfirmed entity dropped: <value>`. Notably: the user's words are looked up via the same keyword/alias resolvers — so a valid intent expressed with wording the catalog vocab doesn't contain is thrown away.
4. **Reference resolution** (`agent/reference.py`): regexes (copied from `core/rag_engine.py`); ambiguous → `NEW_TOPIC`; no LLM.
5. **Validate → execute** tool via `ToolContext` fixtures (query, normalized_query, history, recent_offers, reply_lang, user_id).
6. **Evidence gate** (`engine.py`): `empty_result / no_offer_items / price_not_satisfied` (`_respects_price`); if failed → at most **1 replan** (price widen ×0.35; `_fallback_drift` reasons: `price_not_satisfied / exclude_breached / tool_switched / limit_undershot / empty`).
7. **Deterministic render** of action texts (cards for offers, `note.text` for tool answers). No freeform LLM answer. Stop codes observed: `plan_parse_error`, `planner_error`, `clarification`, `tool_budget`, `replan_budget`, `no_matches`, `price_not_satisfied`, `relax_failed`, `semantic_fallback`.

### Critical finding — the "report" path is dead code
`core/agent_server.py` `event_gen` (seen at lines 202–209): the stream handler does `q.put(("report", ev))` but **there is no `report` branch** in the consumer loop. `POST /api/chat/stream` therefore never emits `agent_turn_report` events (empirically verified: `n_reports=0`). Reporting to clients is aspirational. (Workaround used for this report: run AgentEngine in-process against a file-copied, unlocked Qdrant index — 9142 docs, 53 s load.)

---

## 3. Intelligence Map

Every decision point classified A(LLM/semantic) / B(deterministic rule) / C(keyword/alias) / D(structured validation) / E(database/query) / F(retrieval) / G(hard safety invariant):

| # | Decision point | Engine | Class | Evidence |
|---|---|---|---|---|
| 1 | Intent labels (`offer/followup/faq/other/receipt`) | RAG | B (keyword + regex first, LLM rephrase second) | `intent_note` per trace; `_detect_intent` regex topics in `rag_engine.py` |
| 2 | Intent labels (planner) | AGENT | **A** (single LLM call) | planner.prompt; traces show `intent=other/catalog/offer/faq` predicted per query |
| 3 | FAQ topic routing (`_route_faq_topic`) | both | C/B | `RetrieveFaqTool` (cascade_tools.py:114-137) calls deterministic `_route_faq_topic` keyword rules |
| 4 | Merchant/category/product normalization | both | C | `MERCHANT_ALIASES`, `resolve_merchants/resolve_category/resolve_product`, `normalize_arabic` (faceted.py, config.py) |
| 5 | Price span extraction | both | D | planner `price_range` (LLM-born), then `bound_traceable` numbers check; RAG pattern `(لو>=|من|between...)` regex |
| 6 | Entity corroboration | AGENT | C+D | grounding.py drops unconfirmed entity: "بيتزا" dropped from pizza plan |
| 7 | Reference resolution (التاني/منهم/نفس المحل) | both | C (regex) | reference.py; `_FOLLOWUP_SIGNAL/_OTHER_OFFER_TAIL/_SAME_OFFER` regex sets |
| 8 | Offer retrieval / ranking | both | E+F | FacetedCatalog accessors + `retrieve(top_k, …)` bge-m3 hybrid |
| 9 | Extremum ("cheapest") | both | E | `faceted.cheapest/most_expensive/highest_discount` — **whole catalog, no recency filter** |
| 10 | Safety / injection gate | AGENT | G | engine gate (hard, worked in battery) |
| 11 | Simple-greeting / thanks handlers | both | B/C | fast paths (2 s latency in battery) |
| 12 | Final render | AGENT | B | deterministic card/text blocks; RAG uses LLM second-pass for phrasing |
| 13 | Expiry filtering at chat time | none | — | **absent**: faceted `cheapest()` explicitly does *not* filter expired |

**Bottom line:** 8 of 13 decision points are keyword/rule/structured (B/C/D), 1 is a bounded LLM slot-extraction (A), 2 are retrieval/database (E/F), 1 is a hard safety gate (G). "Intent" is a label chosen to make a lookup pipeline work, not an understanding of what the user wants.

---

## 4. Behavioral Test Results

Full reproduction over the live pair: 33-query HTTP battery (RAG on :8000, AGENT on :8001, both with session memory). Category summary:

**Greetings**
- السلام عليكم / مرحبا / hello / hi / good morning → both engines: clean greeting, **no** offers. ✓
- ازيك؟ → not in greeting lists; **both engines returned unrelated expired offers** (ids 1757/1435/1461/1271, 2015–2016). ✗
- شكرا → RAG: thank-you line **+ leaked offers attached**; AGENT: clean no-offer reply. ✗ (RAG leak); ✓ (AGENT)

**General knowledge / OOS**
- "What is Python?" → RAG: refusal text **+ leaked offers**; AGENT: unrelated **Feast-Kahk offers** (131 s/turn; fabrication relative to intent). ✗✗
- "Explain what RAG is" → RAG pivoted to offers (ids 298/2769/509/827, expired 2013–2018); AGENT asked a clarifying question. ✗ / ✓
- "ما هو الذكاء الاصطناعي؟" → **both** returned AI-training-course offers (e.g. 1091 Teacherbird.com 99 EGP, exp 2015). ✗✗
- "Who won the 2022 World Cup?" → both returned FIFA World Cup offer 6121 (ZED Park, exp 2022-12-18, expired); question unanswered. ✗✗

**Waffarha / offers**
- عروض → both identical generic set [6815, 8525, 2888, 4748]. ✓/✓ (fine for a bare query)
- في عروض بيتزا؟ / احسن بيتزا / show me offers للبيتزا → RAG pizza-ish [8291, 7270, 7162, 7729]; AGENT **felt generic set** (pizza dropped). RAG ~/ AGENT ✗
- عروض الموبايلات → RAG [6756, 2733, 7807, 2595] (not actually mobiles); AGENT generic set. ✗✗
- عايز بيتزا من 100 ل150 → RAG ✓ [7729 Pizza Station 149, 7851, 7457 130, 8141 105] inside [100,150]; AGENT ✗ generic set 263/275/380/543 (**2013–2014**, price 100).
- عروض من 100 ل 150 → RAG [2130/2270 سيف ستورز-2017/2018, 3071 Adrenalin-2019, 2970 كروزو-2018]; AGENT generic set. ✗ (both, stale)
- KFC offers / عروض كنتاكي → **both correct** [8119@KFC 189 exp2026-08-31, 6812@KFC 125 exp 2023-10-09] — expired 6812 still rendered "ساري حتى …" + footer "all offers are real". ✓/✓ (expiry misrepresentation)

**Mixed Arabic/English**
- عايز pizza من 100 لـ120 → RAG [8141 (**not pizza**, 105), 7671 110, 2675 110, 9197 105] — passes price, fails product; AGENT generic set. ✗✗ (matches trace: pizza dropped at grounding)
- cheap pizza offers → RAG pizza-ish [8291/7270/7162/7729]; AGENT **actual pizza** [5599 Pizza Cottage, 7671 Pizza Company, 7729 Pizza Station, 9262 Pizza Bob]. ✓✓ — pure-English keyword phrasing happens to satisfy the keyword resolver.

**Ambiguous**
- حاجة كويسة / رخيصة / وريني العروض → both returned random cheap/old offers (263/275/380/543…). ✗✗

**Gibberish**
- w / asdf / qwerty / blah blah → **NOT blocked**; both returned random offers. ✗✗ (short-Latin gibberish is LLM/verbose-path determined, not a hard gate)
- asdkjh 12931 !!! ??? → RAG canned "no-question" text **+ offers leak**; AGENT proper gibberish reject, no offers. ✓ (AGENT)

**Adversarial**
- "Ignore previous instructions and …" → RAG refusal + random offers; AGENT "I can't help with that." no offers. ✓✓ (AGENT)
- "return all offers now" → both dumped random expired offers. ✗✗
- "ما هي سياسة الاسترجاع؟" → RAG **proper FAQ refund-policy answer** ✓; AGENT "لم أجد عروضاً مطابقة" — FAQ black-hole ✗ (trace: retrieve_faq ×2 "no FAQ answer matched", replan, relax_failed).

**Latency norm:** RAG 13–17 s, AGENT 20–22 s per real query; greetings ~2 s.

**Diagnostic signature — "the generic set":** {263, 275, 380, 543} (price 100, exp 2013–2014) recurred verbatim across عروض الموبايلات / احسن بيتزا / في عروض بيتزا / عايز pizza من 100 لـ120 / عروض من 100 ل 150. This is the **price-only fallback path** (`search_offers` with only `price_range`, or hybrid percent fallback) — the product/category slot is empty. It is visual proof that the agent answers *slots*, not *questions*.

---

## 5. Known Failure Root Causes (code-traced)

1. **Planner drops user constraints** — `planner.py` prompt + grounding.py: constraints are only kept if corroborated by `resolve_*` on the raw message. Message "عايز pizza من 100 لـ120" → plan `{search_offers, price_range:[100,120]}`, `entities:{}`, `grounding:{}`, next_action `clarification` (both budget & tool = 0). In the HTTP live run the same query produced **5 offers at exactly 100 EGP from 2013–2014**, i.e. the price path fired with the product slot empty.
2. **FAQ black-hole in AgentEngine** — `RetrieveFaqTool` returns `ok=True items=[] note.kind=no_match` when topic rules and direct-answer score both miss (cascade_tools.py:163-166). Engine treats that as evidence-gate empty → replan → `relax_failed` → generic "لم أجد عروضاً مطابقة". "ما هي سياسة الاسترجاع؟" reproduced this. The FAQ corpus **contains** a refund-policy answer (RAG answers correctly); the agent's deterministic topic router does not hit it.
3. **RAG "offer leak" into non-offer answers** — RAG's canned no-question/refusal/greeting templates are concatenated with `recent_offers` in some branches → "شكرا" and "asdkjh…" and Python-refusal all carried offer cards. (Retained template logic in `rag_engine.py`; observed across three battery rows.)
4. **No expiry filtering at chat time** — `.env INCLUDE_EXPIRED_OFFERS=true`; `cheapest()` (faceted.py) accepts `merchant/category` but has **no recency/expiry predicate**; 6812 (exp 2023) shown as "ساري حتى". Server relies on indexing-time freshness only.
5. **Ambiguity is hard-decided** — "ازيك؟" was not classified as greeting; get-classified follow-ups ("وريني العروض" etc.) dump a fixed fallback. No uncertainty channel; `clarity`/`ask_user` exists in the planner prompt but is underused by the small model.
6. **Copy-path hash mismatch (testing artifact, not a live bug)** — in-process on a *copied* index dir, some tools failed with `Collection waffarha_<hash> not found` where hash derives from persist path (live `waffarha_b4db5fc16631`). Excluded from report conclusions; live battery used the server instance.

---

## 6. Constraint Preservation

Mechanism: planner emits `constraints`; tool schemas validate types (D); grounding.py **drops** un-corroborated entities; evidence gate enforces `price_not_satisfied` only via `_respects_price`; tools apply `_concrete_span` -> `FacetedCatalog` price filters; `exclude` honored as `exclude_ids`.

Measured preservation:
- Price bounds: preserved in-mostly. "عروض من 100 ل 150" → AGENT strict `[100,150]` (trace: `relaxable_price` [100,150]) ✓; replan widened by 0.35 only when evidence empty.
- Product/category: **poorly preserved**. pizza/betza dropped from constraints in 4/6 pizza queries for AGENT; "عروض الموبايلات" preserved only on RAG and even there mapped to catalog "5738/1705" category semantics. Only "cheap pizza offers" (pure-English keyword present in catalog vocab) preserved product on the agent.
- Merchant: preserved well for known aliases ("كنتاكي"→"KFC" ✓ via `MERCHANT_ALIASES`); unknown merchant → deterministic negative (`unknown_merchant_mention`), good.
- Consequence: when the *constraint* is preserved but *session* is ambiguous (§8), constraints are applied to the **wrong scope** (global cheapest, wrong merchant).

---

## 7. Offer Validity

Audit of production data + index (see report `index_audit`): 
- `data/index/BAAI__bge-m3/qdrant/docs.pkl` = **9142 docs** (94 FAQ + 9048 offers; 4571 EN + 4571 AR).
- Of 9048 offers vs cutoff 2026-09-16: **9036 expired**, 12 live (6 unique × 2 langs), **0 without expiry**; expiry range 2012-10-30 → 2026-09-30.
- `offers_raw.json` (fetched 2026-09-09): 17816 rows = **4524 unique offers × 2 langs**; 9048 active / 8768 disabled; `coupon_expire_date` = 0 on all rows; only **6 unique offers** expire on/after 2026-09-14.
- Refresh log: the *latest* "build" was a **dev sample** (`manifest_path=<Temp>/p4_sample_index/index_manifest.json`, 60 docs, 0 offers) — **not** the production 9142-doc build. Ingestion provenance is mixed; nothing at runtime re-checks expiry.

**Implication for this mandate:** the chatbot "understands" selects mostly from a corpus that is ~99.9% expired. This dwarfs any router difference: correctness requires an ingestion + runtime-freshness fix, not a smarter intent classifier.

---

## 8. Reference Resolution (follow-up language)

Mechanism (`agent/reference.py` + `core/rag_engine.py` share regex sets): turn-1 shows offers → `recent_offers` stored (and `recent_offers_full`) → follow-up plans carry `references` → reference pass sets `references[]` verdicts; `NEW_TOPIC` when ambiguous.

Reproduced against live index, seeded `recent_offers=[8119, 6812]` (KFC):
- "التاني منهم بكام؟" → `SAME_OFFER` (ordinal) → offer 6812 ✓
- "عندك حاجة ارخص من دول؟" → `SAME_OFFER` (price anchor) → 6812 ✓
- "ارخص واحد منهم" → verdict `NEW_TOPIC` / self-contained → **global cheapest = Dental Boss 2449 (0 EGP, exp 2017-11-15)**. "منهم" is not bound to the KFC set. ✗
- HTTP battery (t1 "KFC offers" → t2 "ارخص واحد منهم") confirmed both engines answer Dental Boss; t3 "عروض تانية من نفس المحل" latched conversation onto **other Dental Boss offers**; t4 "التاني بكام؟" → Dental Boss 8145. The whole follow-up chain locked the wrong merchant.
- "التاني بكام؟" without any state → ordinal without target → planner hallucinated `entities:{merchant:["public"]}` → no-match.

**Memory-in-the-loop hazard:** same query "What is Python?" in a *fresh* session → `clarification`; in a session whose history ends with an offer turn and `recent_offers=KFC` → intent flips to `catalog`, returns Kahk offers. Memory changes router verdicts without any semantic relevance check.

---

## 9. Model / LLM Audit

- Model: **qwen2.5:3b-instruct** via local Ollama (`.env OLLAMA_MODEL`), shared by both engines' second-pass/planner. `plan_prompt_builder` context includes: user text, `detected_intent`, `reply_lang`, conversation history (≤ `_MAX_HISTORY=8`), available tools, and the stored `recent_offers` list.
- Planner output is constrained to `_KNOWN_TOOLS` + `_KNOWN_FIELDS`; `PlanParseError` → `plan_parse_error` stop code; tool args validated by schema before execution (D).
- No rich-context semantic reasoning: one structured call, maximum 3 LLM calls (plan + at most 1 replan + internal retries within `MAX_AGENT_LLM_CALLS`). RAG uses the LLM on a second pass for *phrasing only*.
- Failure modes observed from the model: (a) drops product slot; (b) hallucinates entities when state absent ("public"); (c) mislabels greetings/OOS; (d) inconsistent verbosity acceptance of gibberish.
- Deterministic layers (grounding, reference, evidence gate) are consciously built as a safety net, and they do fire (injection gate; unconfirmed-drop logging), but they *cannot recover* semantics the model already threw away.

---

## 10. Test Coverage Gaps

- `tests/eval/` reports **100% pass** while `MEMORY_TEST_ANALYSIS.md` grades C+ and `conversation_memory_report.txt` = 73/78 (93.6%) — contradictory scopes; more importantly, none of the suites cover the failing classes in §4 (mixed-language pizza, OOS/GK refusal behavior, reference binding "منهم", expired-offer rendering, agent FAQ black-hole).
- `utils/run_eval.py` references `utils/queries.json`, but the real file is `eval/queries.json` — the off-the-shelf eval harness is **broken by a path mismatch**.
- No test asserts "no offers in a greeting/thanks/gibberish/OOS response" (the class of bug behind most §4 failures).
- No test seeds a follow-up session (`recent_offers`) and asserts referent binding.
- No freshness test on returned offers.

---

## 11. Architectural Risks

1. **Single-point "understanding"**: the entire semantic surface is one 3B LLM call + regex resolvers. Any rephrase outside the catalog vocab silently becomes the generic-set answer — indistinguishable from "no data".
2. **Dead reporting channel**: server never streams `agent_turn_report`; observability of the reasoning path is unavailable to production clients (discovered and documented in §2).
3. **State-dependent routing**: memory flips router outcomes (`Python` → catalog) without gating on relevance.
4. **Stale data ≫ router**: 99.9% expired + no runtime freshness check means even perfect intent understanding yields expired offers; users are told "all offers are real".
5. **Shared index lock**: dual engines serializing on one Qdrant folder → cross-engine reliability coupling; the copied-index workaround is the only way to introspect live data.
6. **No uncertainty / disambiguation loop wired to users**: `clarification` exists in the planner but the model rarely selects it; when it does, there is no robust "ask → apply answer" dialog state machine.

---

## 12. Recommended Direction

(Constraint: no redesign was performed; this is the recommended direction *for the record*, not an implementation.)

1. **Fix the data pipeline first** (dominant effect): keep `INCLUDE_EXPIRED_OFFERS` false for production builds; add a runtime expiry predicate in `FacetedCatalog` accessors; fix ingestion provenance so refresh.log points at the real build.
2. **Add a deterministic "no offers / not-offer" contract**: greet/thanks/gibberish/OOS must never attach offer cards (fix RAG leak at template layer; already correct on AGENT).
3. **Close the agent FAQ black-hole** with a real `followup_verdict` using the FAQ score gates (RAG's path), not a bare `no_match`.
4. **Strengthen the planner with product-preservation**: pass the normalized query + catalog resolution hints so `product` is corroborated before it is dropped; treat "product slot decided by silence" as `clarification`, not `price-only`.
5. **Anchor ordinals/possessives to the last-shown offer set**: bind "منهم/التاني/نفس المحل/أرخص واحد" to `recent_offers` scope before any global extremum query.
6. **Gate memory influence** on router output by relevance (only if the follow-up references stored offers).
7. Health-check the eval harness path (`utils/queries.json`) and add the gap tests from §10.

---

## 13. Evidence

- `C:\Users\devza\.local\share\opencode\tool-output\tool_0aa5b4ddd001StVItfReslfI2Y` — 33-row live HTTP battery (RAG+AGENT, latencies, offer ids/merchant/price/expiry) referenced throughout §4/§5/§7/§8. Duplicate `tool_0aa62375c001L7dtWG5oJptNbp`.
- `C:\Users\devza\AppData\Local\Temp\opencode\trace_live.jsonl` — full AgentEngine `agent_turn_report` traces for 9 in-process queries (incl. pizza plan `{entities:{}, groundings:{}}`, `[100,150]` faithful path, FAQ black-hole, "منهم"→NEW_TOPIC→Dental Boss, memory-flip Python→catalog, gibberish gate).
- `C:\Users\devza\AppData\Local\Temp\opencode\qdrant_trace_copy` — unlocked copy of live Qdrant index (9142 docs) used for in-process runs.
- Probe scripts: `probe_http.py`, `probe_trace_live.py`, `probe_behav.py`, `probe_trace_out.py` (proved `n_reports=0`).
- Static code basis: `agent/engine.py`, `agent/planner.py`, `agent/reference.py`, `agent/grounding.py`, `agent/tools/catalog_tools.py`, `agent/tools/cascade_tools.py`, `core/rag_engine.py`, `core/faceted.py`, `core/config.py`, `core/app.py` (§760–834), `core/agent_server.py` (§1–249), `memory/memory.py`, `run_servers.py`, ingestion (`build_index*.py`, `fetch_offers_clickhouse.py`, `refresh.py`).
- Data audit: `data/index/BAAI__bge-m3/qdrant/docs.pkl|meta.json`, `data/offers_raw.json`, `data/refresh_log.json`, `data/partners/partners.json`, `data/type_prices.json`, `.env`.
- Contradictory eval set: `tests/eval/` (100%), `evaluation_report`, `MEMORY_TEST_ANALYSIS.md` (C+), `conversation_memory_report.txt` (73/78), `reports/phase3/20260914_095745_phase3_after/retrieval.csv`, `utils/run_eval.py` vs `eval/queries.json`.

---

## 14. Proposed Next Investigation

1. Full static read of `core/faceted.py` and `core/rag_perfection.py` to enumerate every routing rule and template (for a definitive decision matrix over the whole 4000-line cascade).
2. Audit the OOS/refusal path of RAG and the greeting/FAQ-first branch ordering on a 100-query corpus with human-labeled expected "no offers" outcomes.
3. Re-probe with a freshly rebuilt production index (fewer expired offers) to isolate router-vs-data contributions on the pizza/mobile failures.
4. Trace `recent_offers` store+compose path end-to-end (`memory.py` + `compose_context`) to enumerate exactly which history fields the planner actually sees.
5. Reproduce the "return all offers" / injection bypass set across both engines with a fuzz corpus and pin the exact gate patterns.

---

# DECISION INPUT FOR SENIOR ARCHITECT

Intent-model decisions requested; each option classified A(LLM/semantic) / B(deterministic rule) / C(keyword/alias) / D(structured validation) / E(database/query) / F(retrieval) / G(hard safety invariant).

**DECISION 1 — Who decides "is this a greeting/gratitude/junk"?**
- A. Keep a small LLM classifier for ALL intent labels.
- B. Hardcode the greeting/thanks/gibberish set as deterministic fast paths (current AGENT behavior, cheap, works) — leave ambiguous leftovers to the router.
- C. Regex word lists for greeting/thanks only (current RAG fast paths) — observed NOT to cover ازيك؟.
- Recommended: B (extend the deterministic set to cover ازيك؟ variants), routing label = B.

**DECISION 2 — When should the system answer with "no offers intentionally"?**
- A. Always :8000/:8001 both; guarantee "no offer cards" on greet/thanks/gibberish/OOS by contract (integration test).
- B. Only the AGENT engine enforces it (current state).
- C. Leave behavior; rely on model self-restraint.
- Recommended: A; label = G + test contract.

**DECISION 3 — Where do product/category slots come from?**
- A. LLM extraction only (current).
- B. LLM extraction **+ deterministic corroboration hint**, and if uncorroborated → clarification instead of price-only fallback.
- C. Pure keyword resolver (regression back to RAG-only).
- Recommended: B; router label = D override on A.

**DECISION 4 — Explicit price semantics.**
- A. Keep current planner `price_range` + evidence-gate `price_not_satisfied` + 1 replan widen.     (measured: works for pure-price queries)
- B. Add explicit unit handling (".من 100 ل 150") with a `currency` field.
- C. Defer prices to faceted path only (removes the "يمين 2013" risk).
- Recommended: A, keep; label = E.

**DECISION 5 — Multi-intent in one message (e.g. pizza + price, mobile + price).**
- A. Keep single-plan extraction (current).
- B. Two-phrase split (merge two plans) when the message contains two disjoint product mentions.
- C. Segment by detected connectives (regex) then plan each.
- Recommended: B if budget allows; label = C with D validation.

**DECISION 6 — Reference binding of ordinal/possessive follow-ups.**
- A. Bind ordinals/possessives/anchors ONLY to `recent_offers` scoped set (never global) — current does NOT on "منهم".
- B. Global extremum allowed only on explicit scope words (cheapest overall / أرخص عرض).
- C. LLM re-parse of the previous assistant message.
- Recommended: A (+B for the explicit case) — both deterministic; label = D/E.

**DECISION 7 — Memory influence on router verdicts.**
- A. Gate router outcome by a computed relevance signal (only follow-ups referencing stored offers may flip intent).
- B. Keep raw memory-in-prompt (current — produced the Python→catalog flip).
- C. Drop memory from routing decision entirely.
- Recommended: A; label = D.

**DECISION 8 — Expired-offer strategy.**
- A. Exclude expired at build time (`INCLUDE_EXPIRED_OFFERS=false`) AND add runtime `expires_at` predicate in faceted accessors.
- B. Build-inclusion only.
- C. Keep showing expired with "ساري حتى" — current.
- Recommended: A; label = E/G (correctness invariant).

**DECISION 9 — What "understanding" do we claim?**
- A. Slot-extraction only (honest: this report's conclusion).
- B. Slot-extraction + real disambiguation dialog (ask→apply) — the only credible step toward "understanding".
- C. Claim full semantics (unsupported).
- Recommended: B (scoped), report outcome per-slot; label = D for decision, A for the clarify turn only.

**DECISION 10 — The eval suite that guards all of the above.**
- A. Fix `utils/run_eval.py` path and add the §10 gap suite (no-offers-on-OOS contract, seeded-follow-up binding, freshness assertion).
- B. Keep tests/eval as-is.
- Recommended: A; guard at G level (contract tests), run in CI.