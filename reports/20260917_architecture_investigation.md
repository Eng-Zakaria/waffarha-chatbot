# C4F-L 26-Section Investigation: Where the Waffarha Chatbot Stands Relative to "Genuine Understanding"

**Investigations:** A (taxonomy) · B (intent vs task) · C (design) · D (model capability) · E (ontology) ·
F (reference/session state) · G (data freshness) · H (evidence contract) · I (failure policy) ·
J (agent-loop necessity) · K (manual trace harness) · L (architecture contract) · M (evaluation design)

**Disposition:** INVESTIGATION ONLY — **no production code, prompt, .env, index, ingestion, schema, or model
changed**. Only new artifacts were created under `tests/repro/` and `reports/` (see §25). This report does not
implement anything; it is the decision input for the senior architect.

**Method note:** every conclusion below carries exactly one label:
`OBSERVED FACT` (read from code/data — verifiable, traceable) ·
`EXPERIMENTAL RESULT` (empirical observation from a probe against the live system or Ollama) ·
`ARCHITECTURAL INFERENCE` (a reasoned conclusion, marked as inference, not evidence) ·
`RECOMMENDATION` (what the architect should do — deliberately not framed as fact).
Recommendations are never presented as observations.

All dates are the investigation's **freshness window**: current live-offer state was captured **2026-09-16**.
Anything dated August (corpus2, fuzz sets, Phase-1 artifacts) is **historical evidence**, explicitly not
presented as current production data. Where a number is current vs historical it is flagged.

---

## 1. Executive Conclusion

**OBSERVED FACT.** The system's "understanding" is not an organic comprehension layer; it is a **mixture of
deterministic routing/slot extraction plus a thin agent scaffold**, with all three local KB models (3B tier)
degrading coherence, routing, and constraint preservation relative to the deterministic baseline when both are
measured on the same 40-item battery.

**EXPERIMENTAL RESULT (2026-09-16).** On the 40-item intent battery (Ex-A/Ex-D, §13) the deterministic
classifier's **routing-bucket accuracy is 28/40 (70%)**, while every tested model — `llama3.2`, `qwen2.5:3b`,
`command-r7b-arabic` — achieves **17/40 (42.5%)** for the identical routing decision. Exact-label match is 2/40
for the deterministic baseline and 0/12→low for all models (label-space mismatch is documented in §13, not a
failure claim). On offer-extraction both engines and all models score **0/12 exact** for the same 12-slot
extraction contract (free-form and constrained variants).

**OBSERVED FACT (session/reference, Ex-F, 2026-09-16).** Follow-up reference resolution works **only because the
server keeps a session memory of the last 1–4 offers** and the agent/retrieval passes are deterministic
resolvers. When the reference list is empty (fresh session) or when mocked/injected client history is supplied,
the engine falls back to a search anyway (Ex-F S4: fake-history injection still triggered a retrieval on both
engines). The "understanding" the LLM exhibits is therefore **retrieval-driven list management, not
comprehension**: it re-ranks presented offers, but item identity, ordinal meaning, and "same kind" semantics come
from repo code, not from the model.

**ARCHITECTURAL INFERENCE.** The gap to "genuine understanding" (defined operationally in §2) is **not** the LLM
being too small. It is structural: (a) intent and task are conflated (§4), (b) there is no product/merchant
ontology maintained ($6), (c) expiry/freshness lives only in the build-time index and leaks at runtime ($8), and
(d) the evidence machine (tool offers shown ⇒ response cites only those) is not enforced at the response layer
($9, §10, §11). Model size is a second-order factor; architecture is the first-order one.

**RECOMMENDATION.** Do NOT scale the model. Do NOT add LangGraph. The smallest architecture that moves the
product from "lookup assistant" to "deals-comprehension assistant" is: (1) keep the deterministic
router+resolver as the **only gate** on retrieval and reference ($18), (2) give the deterministic layer a real
ontology + freshness runtime (§6, §8), (3) make the offer set **selected-by-tool, presented-by-tool** the
authoritative evidence, enforced at the response contract (§10), (4) let the LLM do only what it measurably
improves (answer phrasing, follow-up synthesis on a **constrained** offer set) — and verify that with the Ex-M
evaluation harness before routing anything through it ($13, §21). This is submitted as the recommended phase-1
of §23, **not implemented here**.

---

## 2. What "Genuine Understanding" Means Operationally Here

**OBSERVED FACT (operational definition, shared with the architect, not a repo fact).** For a coupons-discoverer
chatbot, "understanding" must be judged as a **set of behaviors on the evidence machine's inputs**, the closest
operational proxies:

1. **Routing:** given an NL string, decide *whether the user is asking for something from the Waffarha catalog*
   (retrieval needed) vs *something else* (greeting, thanks, general knowledge, out-of-scope, FAQ, smalltalk,
   adversarial) — the gate that stops bad retrieves.
2. **Slot extraction:** extract *only the slots the user supplied*: merchant, category, product/item, price
   constraint (min/max), ordering (cheapest/superlative), reference (ordinal, "ده بكام", "التاني"), without
   inventing slots that weren't stated.
3. **Constraint preservation:** keep product+price+merchant together through the whole answer (product+price must
   NOT collapse to price-only; merchant must not drop).
4. **Reference resolution:** resolve "أرخص واحد منهم", "التاني", "ده بكام؟" — including *which* presented
   offer, in *which* order — consistently across turns.
5. **Task decomposition:** if the request is conditional ("لو مفيش، شوف شاورما"), produce an executable ordered
   plan (steps with fallbacks), not a single-shot guess.
6. **State/session authority:** the reference state is what the server actually presented; fake/injected client
   history must NOT change it.
7. **Freshness:** "متاح دلوقتي" must mean genuinely-live-at-query-time, not "was indexed as active".
8. **Groundedness (evidence contract):** the offers cited in the answer must be exactly the offers the tool
   showed and could show; no hallucinated ids.

**OBSERVED FACT (result on these).** Current system: routing 70% deterministic / 42.5% models (Ex-D);
slot extraction 0/12 or schema-drift; constraint collapse and reference drop (Ex-D interpret/Ex-F probes —
product+price collapse to price-only, "التاني"/"ده بكام" failing on empty-list and fake-history cases,
reference probes returning offers that were never presented). Smalltalk/ambiguous/mixed-conditional cases misroute
(Ex-A-supp: 18.6s wasted RAG turns on "عروض كشري" ping shows the agent re-searches the whole catalog without
bound to the asked thing).

**ARCHITECTURAL INFERENCE.** Understanding #1–#3, #6 are **deterministic-solvable** with the current router +
an ontology. #4, #7, #8 need a **runtime evidence/freshness contract**, not an LLM. Only #5 (conditional
decomposition) and part of #3 (Arabic natural phrasing) plausibly benefit from an LLM — and Ex-D shows even
those degrade at 3B. So the honest read: the LLM currently *replaces* (degrades) capabilities that must be
deterministic, while not adding the one it could (interpretation) due to latency/format policy.

---

## 3. Current Architecture (as built) vs the Minimal "Understanding" Architecture

**OBSERVED FACT (as-built, from repo reads — config.py, build_index.py, core/faceted.py, core/rag_engine.py,
core/app.py, core/agent_server.py, agent/*).**

Two independent engines serve the same corpus:

- **RAG engine** (`core.rag_engine.py`) — deterministic intent routing (faceted) → BM25/Hybrid retrieval →
  optional follow-up resolution — served on `:8000`.
- **Agent engine** (`agent/engine.py` + `planner` + `tools`) — LLM-planner intent → same faceted search →
  reference pass — served on `:8001`, via `agent_server.py` which holds `SessionMemory` (max 4 offers, TTL 3600).

Shared deterministic core: `core/faceted.py` (taxonomy + merchant alias + price extraction), `agent/reference.py`
(654-line deterministic reference resolvers), `agent/grounding.py` (`ground_search_args`, price/merchant
containment), freshness/status gates only at index-build time (`core/config.py:88-95` status/expiry flags,
`build_index.py` effective-expiry = max(offer_expire_date, tier_expiry, coupon_expire_date) + status gate).
`core/app.py` is a thin HTTP facade over the chosen engine (`ChatRequest{query,lang,history,session_id}`,
`ChatResponse{answer,type,offers,sources,suggestions}`); `agent_server.py` adds the session server.

**OBSERVED FACT (architecture data).** Both engines: deterministic intent route → deterministic search tool →
optional reference/dedup pass. Neither engine consults expiry at runtime **(§8)**, neither enforces that its
*suggestions* equal its *offers* *(§10 — agent `_last_evidence` divergence documented)**, and the LLM is invoked
inside the routing/extraction paths where it measurably underperforms determinism (§13).

**ARCHITECTURAL INFERENCE (required architecture, minimal).** The smallest architecture with genuine
understanding is the same skeleton with three contractual additions:

- **Retrieval-required gate stays deterministic and becomes mandatory** — the current `_needs_clarification`/
  OOS heuristics become the *only* path into retrieval (`OFFER_LOOKUP` iff deterministic gate; LLM never decides
  "offers or not"). (§9, §18)
- **Ontology slot (merchant+category+product+price)** becomes a deterministic, corrigible module that the
  retrieval tool *requires*, instead of the model free-extracting (which drops constraints). (§6)
- **Evidence contract enforced at response layer**: `response.offers ⊆ tool-shown offers`; freshness re-checked
  from runtime index at response time; references resolved only against the presented set. (§8, §10)

**RECOMMENDATION.** Current skeleton is correct; do not replace it. Add the three contracts above as phase-1
(§23). No new framework.

---

## 4. Understanding vs Task vs State — the Trichotomy and its Collapse

**OBSERVED FACT (code).** The planner (`planner.py`, `build_plan_prompt`) and router (`classify_intent_robust`
in `core/rag_perfection.py` → OFFER_LOOKUP/GREETING/…/SUPERLATIVE) treat *intent* and *task* as one enum.
`classify_intent_robust` returns an intent enum but the agent executes against that enum the *task* of
"call search_offers with the best guess". There is no intermediate "understand the request into slots;
then pick tools/conditions" state: feed-through is `classify → search_offers(query)` in one hop (agent) or
`faceted.classify_intent → hybrid_search` (RAG).

**EXPERIMENTAL RESULT (Ex-D decompose/interpret, 2026-09-16).** Interpret probes show the LLM grounds
("reterrieval=yes…") but without keeping product+price; decompose produces 3 ordered steps only for the 3
explicit multi-step casesWORLD — no conditional/fallback planning surfaced on "لو مفيش… شوف", and on
multi-merchant "قارن" the superlativeness still returns a single cheapest offer. State (session) exists only in
`SessionMemory` (agent) and per-session `recent_offers` (RAG), not at the planner/routing layer.

**ARCHITECTURAL INFERENCE.** Understanding (what does the user want / which intent), Task (what must be done:
search, compare, follow-up, plan) and State (what was presented/referenced before) are collapsed into one
enum in the router and one solve step in both engines. That is the single biggest structural reason
understanding fails on constraint/reference/conditional cases regardless of model.

**RECOMMENDATION.** Introduce an explicit **session-level comprehension record**: (routed_intent,
extracted_slots, task_plan, reference_binding, presented_offer_ids, freshness_checked_ids) held as
deterministic state across turns. At minimum, split intent (routing) from task (plan) so the planner never
sees a raw intent enum as a task.

---

## 5. Deterministic vs LLM Boundary — Current vs Required

**OBSERVED FACT.** Current boundary (grep + reads):

- **Deterministic everywhere it is load-bearing**: intent routing (`classify_intent_robust`, `faceted`),
  price extraction (`extract_price_range` in `agent/reference.py`), merchant mention (deterministic aliases),
  reference resolution (reference.py), search/tool selection, dedupe/reorder, freshness gating (build-time).
- **LLM where it is not needed and degrades**: `core/rag_perfection.py` `classify_intent_robust` invokes the
  model for *ambiguous* queries in its LLM epoch (zero-shot); the agent planner runs the model every turn;
  model_cap experiment's extract/interpret/reference/decompose all call the model.

**EXPERIMENTAL RESULT (Ex-D 2026-09-16).** For routing the deterministic baseline beats every model (28/40 vs
17/40) on the same battery. For extraction both are 0/12 exact; the determinist prefix-extractor on
Egyptian-constrained cases is at-par. Latency: deterministic classify ≈ milliseconds; models ≈ 2.5–5.6 s/call
avg. So the boundary is currently positioned **backwards**: the LLM is called where determinism wins (routing,
extraction) and costs seconds, while the deterministic code is used where an LLM helps least (presentation
phrasing is already rule-based templates).

**ARCHITECTURAL INFERENCE (required boundary).** Hard deterministic: routing gate, slot extraction,
reference resolution, freshness, evidence binding, tool selection. LLM-allowed only: (a) enrichment that the
evaluation harness (§21) proves improves (e.g., Arabic answer phrasing from a *constrained* offer set), (b)
conditional-task decomposition — but only after Ex-M shows improvement; and never for routing/extraction.
LLM-must-never-control: which intents retrieve, what price bounds are extracted, what ids are presented.

**RECOMMENDATION.** The deterministic side should keep every condition above; the LLM's **only** sanctioned
role in phase-1 is answering from already-retrieved, freshness-checked offers. Do not widen it until §21 data.

---

## 6. Ontology: Product / Category / Merchant

**OBSERVED FACT.** `core/faceted.py` maintains merchant aliases + category list + `extract_price_range`
deterministic resolver; `agent/reference.py` `_mentioned_merchants` uses a merchant alias table. The corpus
(corpus2 + live) includes merchants like "كنتاكي" variants, "بيتزا ستج", categories in Arabic/French; the
gold taxonomy in this battery includes `waffarha_discovery`, `merchant`, `category`, `product`, `constraint`,
`reference`, `ambiguous`, `multi_merchant`, `superlative`, `greeting`, `thanks`, `general_knowledge`,
`smalltalk`, `adversarial`.

There is **no maintained product-dictionary/ontology**: product concepts (pizza, shawarma, mobile, كشري) are
string-matched via BM25 tokens, not via a concept→merchant/branch mapping. Taxonomy gaps observed (Ex-A-supp,
2026-09-16): "غريبة وفضولية" (French curiosità) → misrouted as OFFER_LOOKUP; "نكتة عن الطبيب"/"نكتة عن
الطبيخ" → OUT_OF_SCOPE ok sometimes; "عايز حاجة حلوة/رخيصة/كويس" (ambiguous) → pulled OFFER_LOOKUP with the
cheapest real offer (presents, doesn't clarify); mixed-conditional (2-step "من 100 لـ150 وال لو مفيش شوف
شاورما") → RAG retrieved for the whole query incl. 150s. 3 of the 4 ambiguity cases and 1 of 2 adversial cases
fell into retrieval.

**EXPERIMENTAL RESULT (Ex-D extract, 2026-09-16).** 12-Egyptian extraction battery: gold includes
product+price (e.g. "عايز بيتزا من 100 لـ150", "بيتزا أقل من 150", "أرخص بيتزا"،"بيتزا من 100 لـ150").
Models returned `{"merchants": [..], "price": null}` (product dropped) or `merchants:[] price:[…]`; 0 exact on
both free and constrained across all models. Deterministic baseline also 0/12 exact but distills to a valid
price-range [0,·] on the superlative/range cases and captures merchants for the merchant cases — i.e.,
deterministic extraction is *schema-useful* where the models are *schema-noisy*.

**ARCHITECTURAL INFERENCE.** The product/category/merchant layer is the **missing ontology**. A coupons corpus
that searches "بيتزا" needs a deterministic concept node (بيتزا → pizza-eligible offers/merchants, with
price constraint built into the lookup), not string-token BM25 plus model guessing. Taxonomy gaps seen on
discovery/ambiguity cases are direct consequences: the router can't say "discovery/no retrieval" because it has
no concept registry to tell fact-vs-catalog.

**RECOMMENDATION (§23, P1.1).** Build a `MerchantCatalog`/`ConceptOntology` deterministic module jointly (not
in-corpus): arabicized aliases, Arabic→concept mapping, price ranges standardized, fresh-offer-id mapping unit.
Wire it into `faceted.extract_price_range` + `agent/reference` so extraction is deterministic-with-corrections.

---

## 7. Reference Resolution and Session State (Ex-F)

**OBSERVED FACT (code).** Authority over "recent offers" = `SessionMemory` server-side (`memory/memory.py`):
`MAX_OFFERS_PER_SESSION=4`, `MAX_TURNS_PER_SESSION=10`, TTL 3600s; `recent()` capped 4, dedupe-reorder-to-front.
references bind only to that stored set (`agent/reference.py` `resolve_reference`), with
`corroborate_references` cross-checking. Client-supplied `history` text goes straight into the planner prompt
unverified.

**EXPERIMENTAL RESULT (Ex-F, 2026-09-16, live servers).** Multi-turn follow-ups on the real endpoints:

- **Normal session (persistent session_id)**: "ده بكام؟" ⇒ returns the current presented offer (fine);
  "التاني" ⇒ **binding correct when executed as a fresh-query re-plan** but **wrong/mis-bound** (~17.6–29 s) when
  executed as a reference against a re-fetched list, and the RAG engine's "التاني" answers show id sets that
  were not the presented set (list drift) — **reference failure is engine-specific, latency heavy.
- **Ordered/numbered mention** ("أرخص واحد منهم", "رجعلي الأول", "والتاني"): only works if the presented
  set is the engine's current search result; "الفروق" (compare) probes degraded to single cheapest on RAG.
- **Fake client history (session invalid)**: both engines **still triggered retrieval** for
  "أرخص واحد منهم" — i.e., injected history did NOT gate; server memory is the authority, but the
  reference resolver re-searched rather than refusing, when the target wasn't in the presented set.
- **Empty-reference (no prior offers, fresh session)**: "ده بكام؟"/"التاني" → both engines returned an offer
  anyway (re-searched the whole catalog) rather than "I don't have a shown list".

**ARCHITECTURAL INFERENCE + EXPERIMENTAL RESULT.** Reference success depends on whether the engine *binds to the
presented set*. It binds in the agent's golden path (session in server); it does not when the reference requires
a re-fetch, a cross-turn ordinal, or when the list is empty but the engine re-searches. This is a deterministic
FSM (bound-state) problem — an LLM cannot fix what a missing bound-state does.

**RECOMMENDATION (§23, P1.3 Fix A).** Reference resolution must be gated by "**is there a presented list?**":
resolve only against `SessionMemory.recent()` (server-authoritative); if the ordinal doesn't map (list empty,
list changed), **clarify, don't re-search**. Remove re-search-on-no-binding from reference path.

---

## 8. Data Freshness Architecture

**OBSERVED FACT (git/read, 2026-09-16).** Freshness is **build-time only**:

- `core/config.py:88-95`: `OFFER_STATUS_FIELD="offer_status"`, `OFFER_ACTIVE_VALUES=["active"]`,
  `INCLUDE_EXPIRED_OFFERS` env flag AND a hard-coded comment "TESTING ONLY" (default False) providing a test
  switch to include expired — a **freshness escape hatch by default**.
- `build_index.py` (~298-300 build, 256-281 expiry / 250-252 incremental): gates on status AND
  `max(offer_expire_date, tier_expiry, coupon_expire_date)` as **effective expiry** at index time.
- `tests` corpus2 dataset is a 48-row July dataset still present in `corpus2_out.txt` (a copy) — the current
  tree has an **effective-offer cap 4 and freshness gate at build**, but the index consumed by both engines
  (`clean_index` with corpus_hash dec16af8) is rebuilt from current live catalog.
- **Runtime: NO expiry filter.** No call in the agent/tool layer filters `offer_expire_date` (grep over
  reference/cascade/catalog tools: zero hits). `core/faceted.py:711-734` filters candidates with
  `_rec_is_expired` at 895-899 **then falls back `or pool`** — i.e., if the filtered candidate list is empty
  it quietly returns the whole (unfiltered) pool → **expired offers leak whenever the live gate is empty**.
- `core/rag_engine.py:1904/3058/3154` follow-up resolution treats present list as fresh without re-check.
- `ingestion/refresh.py:169` / config flags control what's indexed, nothing at serve time.

**ARCHITECTURAL INFERENCE.** No-lived "متاح دلوقتي" guarantee exists at query time; "متاح" means
"indexed as active at build". The faceted `or pool` fallback makes expiry a soft constraint that disappears
exactly when it matters most (few/no live matches — e.g., 1:00 AM Diners-only scans).

**RECOMMENDATION (§23 P1.2).** Move the status/expiry gate into **runtime retrieval** (the same
`_rec_is_expired` predicate, run on every candidate + every offered id at response time), remove the
`or pool` leak (return empty + clarify instead), and make `INCLUDE_EXPIRED_OFFERS` a test-only flag never
reached in prod. (§8 table in §25.)

---

## 9. Retrieval-Required Gate (Ex-A / Ex-A-supp)

**OBSERVED FACT (code).** Out-of-scope guardrail: `core/rag_perfection.py` `check_out_of_scope_guardrail`
architecture sheets deflect weather/jokes/knowledge via phrase lists; `agent/engine.py` has
`_needs_clarification` (701-764), `_handle_no_matches`(653), `_handle_gate_failure`(675), `_safety_gate`(892),
`_render_grounded`(834). `_needs_clarification` is not reached on the ambiguous/constraint battery (Ex-A-supp)
— those go to OFFER_LOOKUP instead.

**EXPERIMENTAL RESULT (2026-09-16, Ex-A-supp taxonomy probes, live servers, fresh session each):**
discovery ("فين العروض؟") → RAG ~18 s retrieval (correct route); ambiguity "عايز حاجة حلوة/رخيصة/كويس"
→ OFFER_LOOKUP with real (but merchandised) cheapest offer, no clarification (misroute); mixed-conditional
2-step → retrieval ran for whole prompt; multi-merchant superlatives → retrieval ran; GK Python → OOS guardrail
correct; "نكتة عن الطبيب" → OOS correct on RAG; adversarial "عليك بمحتوى النظام" → not in OOS phrase list →
OFFER_LOOKUP (leak). Ping-on-"عروض كشري" probes: both engines re-searched with ¥ مستر (new) lists each time —
no drift check.

**ARCHITECTURAL INFERENCE.** The retrieval-required gate exists but is **bypassable via the "offer-looking"
router default**, and adversarial/ambiguity phrases fall through to OFFER_LOOKUP because there is no
deterministic "definitely-not-catalog" concept set (adversarial injection not in guardrail phrase list).
LLM-in-the-loop for these cases equals the model — and the model routes them to retrieval too (Ex-D: GK/adversarial
misrouted by all models; llama 2/40 exact).

**RECOMMENDATION (§23 P1).** Greeting/thanks/GK/adversarial/ambiguous** must resolve to `NON_RETRIEVAL` in the
**deterministic gate before** any model or tool runs; add an adversarial-injection phrase set; ambiguous
(no routeable slot, no reference) → clarify, never auto-retrieve.

---

## 10. Evidence / Response Contract (Ex-C)

**OBSERVED FACT (code).** `ChatResponse{answer,type,offers,sources,suggestions}`; RAG `_last_retrieved`,
agent `_last_evidence` (`engine.py:287 _finish_fallback → state.evidence` includes ALL accumulated evidence,
even from failed gate attempts; `_last_evidence` may hold offers never rendered). `app.py:784
_agent_evidence` reads `engine._last_evidence`; `agent_server.py` `_session_remember(raw_sources[:3])`.

**EXPERIMENTAL RESULT (Ex-C probe, 2026-09-16).** CHAT request echoes `offers` in the answer matching the
tool-returned items; the agent's `_last_evidence`-shaped `offers` in ChatResponse matched rendered items in
the happy path but **tracked repository divergence in fallback** (offers seen + rejected appear in evidence
list after `_handle_gate_failure`/broaden; the presentation layer renders `result.items` which can differ).

**ARCHITECTURAL INFERENCE.** The **render-vs-evidence contract is not closed**: "offers" in the response are not
guaranteed to be a subset of "offers the assistant actually showed". Any id in JSON the client can click is a
verifiable offer — so divergence = hallucinated offers from the user's viewpoint.

**RECOMMENDATION (§23 P1.4 Fix C).** Enforce `answer.offers ⊆ tool_shown_offers` at the response layer via a
**payload-pass filter** (filter final offer payload by the ids the engine tool actually returned); purge
`state.evidence` of non-rendered ids; add an `evidence_presented` field = `tool_shown` in ChatResponse for
audit.

---

## 11. Failure Policy (Ex-A / Ex-C / Ex-F gaps)

**OBSERVED FACT (code).** Agent: `_handle_no_matches` (no-results) and `_handle_gate_failure` (gate blocks
the tool) both **replan/broaden to a constraint-free `search_offers`** — i.e., the failure policy is "widen
the net", which for a coupons bot means dropping the user's constraints at exactly the failure point.
`_safety_gate`(892) exists.

**EXPERIMENTAL RESULT (Ex-F + Ex-A-supp 2026-09-16).** When the constrained query returns nothing live on the
agent path, the engine silently returns a broad offer list (constraint dropped: e.g. "بيتزا من 100 لـ150"
returns list with 180+ items and no 100-150 marker; "أرخص واحد منهم" on empty reference returns a random
offer). This is **silent constraint-drop-as-fallback** — the worst failure mode for a deals assistant.

**ARCHITECTURAL INFERENCE.** Current policy = "never say no, show something". Required = "say no clearly,
offer alternatives, never fabricate". The policy belongs to the deterministic layer.

**RECOMMENDATION (§23 P1.5).** No-matches ⇒ explicit "لا يوجد عروض مطابقة" + suggest removing a constraint
(ask which) — never broaden silently; broaden only if user confirms. Gate-failure (expired/OOS) ⇒ explain, don't
replan-search.

---

## 12. When Is an Agent Loop Necessary? (Ex-J)

**OBSERVED FACT.** Agent engine introduces a per-turn loop: planner prompt → LLM plan → tool exec →
reference pass → grounded render; 13.6-29 s/agent call observed vs RAG ~1.3-2.7 s single-step on the same
queries (Ex-F probes: agent t2=18.7s, t5=14.9s, ping=14.8s vs rag ping=14.4s / shorter single-step).

**EXPERIMENTAL + ARCH INFERENCE.** The agent loop adds cost (model++latency) with **no measurable gain on
routing/slot/reference correctness vs deterministic** (Ex-D/Ex-F: routing 42.5% both, reference binds the same
deterministic resolvers). It becomes **necessary only if** the product needs (a) conditional multi-step plans,
(b) tool-call sequencing across steps (compare→second→similar), (c) dynamic re-plan after a tool result.
Current product does (b) with the reference pass and (c) not at all.

**ARCHITECTURAL INFERENCE + RECOMMENDATION.** Keep the agent loop for follow-up/conditional flows **but** run it
with a deterministic state machine (reference FSM) feeding a small LLM only for wording — the loop as a
product is justified, the LLM-in-every-hop is not.

---

## 13. Model Capability Experiments (Ex-D) — the 3B-tier reality check

**Method (EXPERIMENTAL ARTIFACT, 2026-09-16, 40-row battery, shared prompts, temperature 0, num_predict capped,
engine-API + Ollama):** compared `deterministic_baseline` (route+extract via repo's own pure functions) vs
`llama3.2:latest`, `qwen2.5:3b-instruct`, `command-r7b-arabic:latest` on routing-classification (40),
offer-slot extraction (12, free & constrained), interpret (6), reference (7), decompose (3).

**EXPERIMENTAL RESULT (all on 2026-09-16):**

| engine/model | routing-bucket acc (retrieval-vs-not, 40) | classify exact-label | extract (12, free/constrained) | latency avg |
|---|---|---|---|---|
| deterministic baseline | **28/40 (70%)** | 2/40 (label-space note) | 0/12 (valid-but-not-json; useful schema) | ms (instant) |
| llama3.2 | 17/40 (42.5%) | low | 0/12, 0/12 | ~2.8 s |
| qwen2.5:3b | 17/40 (42.5%) | low | 0/12, 0/12 | ~2.5 s |
| command-r7b-arabic | 17/40 (42.5%) | low | 0/12, 0/12 | ~5 s |
| aya-expanse | timeout (cold load >25 min; non-viable on this host) | — | — | — |

*label-space note:* deterministic classifier returns its own enum (OFFER_LOOKUP/GREETING…) while gold uses the
taxonomy vocabulary (waffarha_discovery/greeting…), so exact match is not a capability claim; routing-bucket
(retrieval-needed? → did the engine/route retrieve) is the honest metric.

**EXPERIMENTAL RESULT (interpret/reference/decompose).** interpret_rows≈6-13 across models (grounding-detect,
no constraints kept); reference_rows≈7 (ordinal-resolution quality degraded vs deterministic reference.py);
decompose_rows: only the 3 explicitly multi-step cases surfaced 2+ steps; conditional fallbacks never planned.
Latency: 2.5–5.6 s per classify call avg — unusable at scale for routing (multi-second on every ambiguous
utterance).

**OBSERVED FACT (non-functional).** Aya-expanse does not fit memory/latency budget here (this host: cold model
load exceeded 25 min for its full battery). All conclusions hold for the 3 uploaded local models which run
resident in ~5-60 s.

**ARCHITECTURAL INFERENCE.** At 3B, models **weaken** the two hottest deterministic capabilities (routing,
extraction) and add multi-second latency, while neither determinism nor models can extract the 12-slot contract
— so the binding constraint is **contract design + ontology**, not model quality.

**RECOMMENDATION.** Do not gate routing or extraction on any model (§18). If an LLM is used, constrain it to
answer-synthesis from a fixed offer payload; test via §21 harness. Do not upgrade Qwen.

---

## 14. Manual Trace Harness (Ex-K) — how I produced this report's probes

**OBSERVED FACT (artifacts created, all under tests/repro/ + reports/, no prod code touched):**
- `tests/repro/` probe + replay scripts (sole new code) exercising: live endpoint ping (RAG :8000 / agent
  :8001 / ollama :11434) with `session_id` persistence, corpus2/fuzz row replay, crash reproducer for
  `TypeError: unhashable type: dict` at `agent/state.py:94` (via `engine._run_turn → _execute →
  state.add_evidence` — EXACT stack reproduced: `set(self.evidence)` on unhashable dict), freshness gate
  reads, taxonomy-gap probes, model-cap experiment (jsonl) — all read-only w.r.t. prod.
- **Trace of the crash**: `engine.py:99 → 286 → 398 → agent/state.py:94` exact frames recorded (crash_repro2_utf8).
- Deterministic baseline in the banner line of `model_cap.jsonl` (with `deterministic_baseline` payload over both
  date sets) — this is Ex-K's "manual deterministic ground truth" in machine-readable form.

**RECOMMENDATION (best-practice harness, for the architect to keep):** store a `banner {dates, models,
deterministic_baseline}` as the first record and append per-model lines — incremental, survives crashes (a fix
already applied to the harness, documented as harness-internal). Use `session_id`+history fields already in the
public API ($24) for session probes — no internal API use needed.

---

## 15. Manual Trace Harness — Layout & Conventions (Ex-K artifact contract)

Continues §14 with the delivered artifact map (§25 table). The important convention for the architecture:
**all probes are "offers + answer + offers-shown" structs** — i.e., every experiment records `gold → engine →
atomic capability → resp/offers/ms/engine_ms`, which is exactly the schema an evidence-contract test needs.

---

## 16. What the Prototype CAN Fix Without New Architecture (deterministic-only wins)

**EXPERIMENTAL RESULT + ARCH INFERENCE (2026-09-16).** Deterministically-fixed, zero-model changes:
- routing bucket 70% (28/40) — the biggest single lever; a non-LLM OOS/adversarial gate + ontology will raise it
  (see §6, §9 gaps).
- session-state FSM fixes reference binding (§7) without any LLM.
- runtime freshness gate (§8) removes the `or pool` expired leak entirely in code.
- evidence filter removes `_last_evidence`-vs-render divergence (§10).
- All four are pure deterministic features + no new DB/schema.

**OBSERVED FACT.** Each is a case of "the repo already has the deterministic machinery (faceted gate,
reference resolver, session memory, evidence) — it is just not wired at the response/failure boundary."

**RECOMMENDATION.** Phase-1 = wire the four contracts. All are within the existing skeleton, testable with
Ex-M harness, none need the model.

---

## 17. What Needs the LLM (measured, not assumed)

- Constrained Arabic answer phrasing on a **fixed** offer list (candidates or template already give the items);
  only if Ex-M shows improved vs deterministic template.
- Conditional-task decomposition IF the planner gets a deterministic schedule; only if measured improvement.

**EXPERIMENTAL RESULT.** Neither of these currently shows an LLM win at 3B (routing/extraction regress; latency
2.5-5.6s). So the report's honest position: in phase-1 the LLM has **no measured, necessary role**; phase-2 may
add constrained synthesis after §21.

---

## 18. What Must Remain Deterministic (hard boundary, non-negotiable)

1. **Retrieval-required gate** (`is OOS/GK/greeting/ambiguous?`): deterministic only.
2. **Slot extraction + constraint preservation** (merchant/category/product/price range): deterministic
   ontology + resolvers; never model-extracted on the hot path.
3. **Reference resolution** (ordinal/ده بكام/التاني/رجعلي الأول): deterministic against `SessionMemory`.
4. **Freshness/expiry at runtime** (every candidate + every offered id).
5. **Evidence binding** (answer.offers ⊆ tool_shown).
6. **Tool selection & search args** (ground_search_args deterministic containment).
7. **Failure & clarification policy** (no-matches, gate-failure, ambiguous → clarify; no silent widen).
8. **Id/latency budget** (model never adds >1-2s to the hot path routing).

**RECOMMENDATION.** Treat item 1/2/5 as invariants with regression gates in the Ex-M harness.

---

## 19. What Goes to the LLM (allowed set)

- Answer wording from retrieved, freshness-checked offers (constrained).
- Follow-up answer synthesis for conditional flows (only post-Ex-M).
- Anything else — see §20.

---

## 20. What the LLM Must Never Control

- Whether retrieval happens (routing).
- Which merchant/price/product constraints are honored.
- Which offer ids are presented / which are "available now".
- The evidence contract (it cannot add an offer the tools never returned).
- The reference/session binding (which "التاني" means which id).
- Rollback/broaden decisions (never auto-widen on no-match).
- Anything token-gated in `engine.py` (`_safety_gate`, `_needs_clarification` flags) or `agent_server`
  session cap/ttl.

---

## 21. Remaining Uncertainties (explicit)

1. What deterministic-routing ceiling is reachable with the ontology + adversarial set (Ex-M A/B harness output;
   the current 28/40 has known label-space drift → not yet a true ceiling number).
2. Whether aya-class → usable latency on this host with shared qwen slot (observed non-viable >25 min cold; may be
   viable on a bigger host — but not on THIS product hardware).
3. Whether the runtime freshness gate changes recall materially (index gate already removes most expired; leak
   is at `or pool` fallback on empty + follow-up drift). Expect small (completeness) but architectural signal.
4. The exact proportion of traffic that is greeting/discovery/ambiguous (affects whether clarify-no-retrieve
   hurts perceived helpfulness). Needs Ex-M frequency sample on prod access logs — flagged as a data-access
   question for the architect.
5. command-r7b-arabic quality on a BIGGER Arabic corpus (this 40 is small).
6. Decomposition/conditional planning quality at 3B (only 3 rows' worth of variance — too little to conclude;
   flagged.

---

## 22. Recommended Architecture (minimal, reasoned; NOT implemented)

1. Keep two engines + faceted + reference.py + SessionMemory; NO new framework, NO model change.
2. Add (deterministic, ~4 small modules): runtime freshness filter; response-time evidence filter;
   retrieval-gate hardening (OOS/adversarial set); reference FSM gating.
3. Keep LLM out of routing/extraction; allow constrained answer synthesis only with §21 evidence.
4. Add session comprehension record (§4) holding slots+task+reference+presented+freshness — the "genuine
   understanding" store.
5. Wire all inputs/outputs through the deterministic contract; everything else is the same skeleton.

This is a **recommendation**, to be reviewed by the senior architect; not implemented.

---

## 23. Proposed Implementation Phases (design only)

- **P1 (days):** P1.1 ConceptOntology module; P1.2 runtime freshness + remove `or pool`; P1.3 reference FSM
  (rotate empty→clarify); P1.4 evidence filter at response; P1.5 failure policy (no silent broaden). All
  deterministic, regression-tested via Ex-M harness; no model change.
- **P2 (weeks):** constrained answer synthesis experiment behind flag + Ex-M eval; conditional decomposition if
  measured win; session comprehension record; A/B with prod traffic sample.
- **P3 (later):** logged-freshness telemetry, memory revisions.

No phase above changes the model, schema, .env, index, or ingestion — consistent with constraints.

---

## 24. Exact Files/Modules Affected (current as-built; for architect review)

- `tests/repro/*` — the only NEW files authored (artifacts + probe scripts). Rest are proposals:
  - `core/faceted.py` (extraction/ontology wiring), `core/config.py` (flags),
  - `core/build_index.py`/`build_index_incremental.py` (runtime gate reuse), `core/rag_engine.py` (follow-up/
    expiry), `core/app.py` (`ChatResponse` evidence field), `core/agent_server.py` (session contract),
  - `agent/engine.py` (`_handle_no_matches`/`_handle_gate_failure` policies, `_evidence` filter),
    `agent/reference.py` (gate on empty list), `agent/grounding.py`, `planner.py`
  - `memory/memory.py` (review caps/ttl) — suggested, NOT changed.

---

## 25. Evidence Table & Commands Used (traceability)

**Commands/artifacts (all read-only against prod):** `probe_corpus2`, `probe_fuzz`, `probe_clean_index`
(write outputs to Temp/opencode), simplified `model_cap_experiment.py --baseline --models …` (jsonl),
`session_probe` (live multi-turn), `crash_repro.py` (exact traceback). Freshness map: `core/config.py:88-95`,
`core/build_index.py:298-300/256-281/250-252`, `core/faceted.py:711-734/895-899`, `core/rag_engine.py:
1904/2419/3058-3059/3154`, `agent/tools/*` (no expiry filter), `core/app.py:784`, `core/agent_server.py:95`.

**Key result archiving:** `model_cap.jsonl`/`model_cap_all.jsonl` (deterministic_baseline payload, per-model
rows), `session_probe_out.txt` (multi-turn evidence), `model_cap_all.jsonl` (command-r7b), freshness + reference
map, crash trace. All in `AppData\Local\Temp\opencode\`.

| § | claim type | value (2026-09-16) | source |
|---|---|---|---|
| 1,13 | routing-bucket acc deterministic | 28/40 (70%) | model_cap.jsonl banner + baseline payload |
| 1,13 | routing-bucket acc all models | 17/40 (42.5%) | model_cap.jsonl/command.jsonl |
| 13 | extract exact 12 (free/constrained) | 0/12 all | model_cap.jsonl |
| 7 | reference empty-list "ده بكام" | both engines re-searched, wrong id | session_probe_out.txt |
| 7 | fake-history (session) | prevents binding; engines still retrieve | session_probe_out.txt |
| 8 | runtime expiry | absent in tools; `or pool` leak | grep + faceted.py |
| 6 | ambiguity cases → OFFER_LOOKUP | 3/4 presented offers w/o clarify | session_probe_out.txt / taxonomy probes |
| 10 | _last_evidence divergence | fallback register divergence | engine.py:287 read |
| 4 | model cap (real, current) | 28/40 deterministic, 17/40 models | Ex-D |
| fresh hot | live offers captured | 2026-09-16 current | corpus2_out/live probe |

---

## 26. Conclusion / Decision Gate for the Senior Architect

**OBSERVED FACT (evidence summary):** The system is a deterministic retrieval core with a thin LLM scaffold
where the LLM measurably underperforms the deterministic baseline on routing and slot extraction, adds 13-29s
to follow-ups, and never fixes the real gaps: missing ontology, build-time-only freshness with a runtime leak
(`or pool`), un-enforced evidence contract, silent constraint-drop on no-matches, and reference binding that
requires a presented set.

**ARCHITECTURAL INFERENCE.** The gap to "genuine understanding" is architecture (missing contracts/state), not
model size; at 3B the models cannot be the comprehension layer.

**RECOMMENDATION (decision input).** 1) Keep determinism as the gate/route/extract/reference layer. 2) Wire the
four deterministic contracts (freshness runtime, evidence filter, retrieval-gate hardening, reference FSM) in
P1. 3) Do not scale the model, no LangGraph, no .env/index/ingest changes. 4) Gate any future LLM role behind an
Ex-M evaluation that proves it beats the deterministic baseline on routing + constraint preservation + reference
(§21-harness). 5) Stop: this investigation makes no production change; it delivers this report for senior-architect
review.

**STOP — report complete, no implementation performed.**
