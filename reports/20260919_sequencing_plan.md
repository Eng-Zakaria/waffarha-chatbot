# Sequencing Plan — Stage 1 router + production bug fixes

Date: 2026-09-19 · Status: PLAN ONLY (no code written on either track)
WHO CLOSED (this revision): A2/A4 disjointness proven (§1a); B1 scratch path
locked + lock-scope confirmed (§3.1); promotion steps defined (§3.2/§3.3);
named gates GATE-1/2/3 (§5); B2 rollback plan written (§6).

Both tracks are cleared to proceed per approved docs
(`20260919_stage1_router_design.md`, `20260919_production_bugs_1_5_root_cause.md`).
This document sequences them before any line of code is written.

---

## 1. Dependency check — does the index rebuild block Stage 1 router testing?

**Verdict: NO — Stage 1 router testing is unblocked immediately.** The router
can be tested against synthetic/mocked catalog data; it does NOT need the
rebuilt production index. Per-signal evidence:

| Router signal (design §1 / Gap 2) | Index dependency | Evidence |
|---|---|---|
| greeting/thanks/farewell/smalltalk tables | none | `core/greetings.py` constants + normalizer (pure) |
| offer-topic words, explicit-browse phrases, length guard | none | string/contains checks on normalized text |
| OOS regex | none | regex over normalized form (engine `_OUT_OF_SCOPE_QUESTION_RE` pattern set, no index read) |
| **catalog-entity corroboration** (a)-(d): `resolve_merchants` / `resolve_category` / `resolve_product` | **none** | `FacetedCatalog.__init__(docs, ...)` (faceted.py:217) is constructor-fed from a `docs` list; `tests/unit/test_faceted_routing.py` already builds it from synthetic `_pair()` docs (lines 55-76) — merchant/category/product resolution works fully offline |
| faq intent — `_route_faq_topic` | **none** | module-level pure regex router over `query + normalized_query` (rag_engine.py:479-510), no engine instance, no index read |
| `classify_intent_robust` (d) | none | deterministic classifier path in `rag_perfection`; tests use deterministic inputs / stub any LLM fallback |

Two clarifications so this is not over-read:

1. **The router test for the `faq` intent asserts the mini-plan, not the
   answer.** Design §4's test asserts "intent is `faq`, tool is
   `retrieve_faq`, zero planner calls" — it does NOT execute the tool. The
   actual FAQ *answer* is fetched at runtime by the existing
   `RetrieveFaqTool` → `_faq_topic_answer` → index docs, which is reused code
   and is the only part that touches the index.
2. **The rebuilt index is a RUNTIME delivery requirement (bugs 2/3/5q1), not
   a test-time requirement for the router.** Nothing in the router's own
   test surface waits on it.

**Consequence:** Stage 1 Phase 1 (intent table + normalizer, with its own
passing test file) can start immediately and run on synthetic fixtures —
independent of the rebuild timeline.

### 1a. A2 ↔ A4 ordering — verified disjoint, parallel-safe

Read both change surfaces against source. They are **genuinely disjoint**;
no explicit ordering is required (A2 first is recommended only because it
unblocks the C-tier review queue, not for correctness).

**A2 (router phase 1) edit surface = two new files only.** Per design §2 the
unified greeting table + normalizer live in a NEW `core/greetings.py`, the
router in its own module, tests in their own file. The only existing-file
change is re-export shims in `core/rag_engine.py` (`_GREETING_PHRASES` becomes
re-exports). A2 makes **no edit to `core/faceted.py`**.

**A2 read surface at runtime** (design Gap-2 (c), line 286:
`facaded.resolve_merchants/resolve_category/resolve_product` → passthrough at
`agent/facade.py:52-53` → `RagEngine.faceted` → `FacetedCatalog.resolve_*`):

- `resolve_merchants` (`core/faceted.py:513-569`) reads `_aliases`,
  `_merchant_names`, `_name_to_canonical`, `_canonical_info`, `self.merchants`
  — built at lines 384-392.
- `resolve_category` (571-582) reads `_category_variants_low` (431-434).
- `resolve_product` (584-595) reads `_product_variants_low` (427-430).

A2 **never calls** `offers_for_product`, `offers_for_merchant`,
`offers_for_category`, `_recs_to_entries`, `cheapest`, or `_superlative_pool`.
Its corroboration signal is a catalog **key** (canonical merchant string /
category label / product key), never the offer pool. Router fixtures follow
the existing pattern (`tests/unit/test_faceted_routing.py:55-76`) — build
`FacetedCatalog(docs)` from synthetic docs, assert `resolve_*` keys.

**A4 (bug 3 matcher) edit surface** = the `_product_offers` pool construction
inside `FacetedCatalog.__init__`, lines **416-426** — specifically the
substring scan at 417-423 (`if any(v in t for v in low)` over `rec["text"]`).
The fix excludes the "Pricing options:" block (folded into `rec["text"]` by
`build_index.py:410-416`) from that scan. `offers_for_product` (666-686) is a
consumer; its signature and return shape are unchanged.

**Non-overlap proof:** every line A2's `resolve_*` reads (384-392, 427-434)
is distinct from every line A4 changes (416-426). The only shared data is the
module constant `PRODUCT_LEXICON`, read-only by both. A4 changes the **pool
membership** of `_product_offers`; `resolve_product` returns a lexicon key
that is independent of pool membership. No signature/return-shape change from
A4 affects anything A2 asserts.

**One adjacency guardrail (review note, not a dependency):** A4 must not touch
lines 427-430 (`_product_variants_low`) or 431-434 (`_category_variants_low`) —
those feed `resolve_product`/`resolve_category`. Enforce in A4's code review.

**Footnote — A3 (bug 4) also edits `faceted.py`** but at a third disjoint
region: the superlative-pool / `cheapest` area (~691-712). A4 (416-426), A3
(~691-712) and the A2-read regions (384-392, 427-434) are pairwise
line-disjoint; the only shared object is the file itself. Land A3 and A4 as
separate diffs in sequence (never interleaved in one PR) to avoid same-file
merge churn.

---

## 2. Bug 5q2 — resolved AS PART OF Stage 1, removed from the bug-fix list

**Yes — 5q2 is a side effect of Stage 1 landing correctly, not a standalone
bug fix.**

Chain of fact:

- The company-info FAQ answer **already exists in the live index**:
  `faq_12_about` is present in `data/index/BAAI__bge-m3/qdrant/docs.pkl`
  (verified this session). So 5q2 has zero index-rebuild dependency.
- 5q2 fails today only because the **planner never picks `retrieve_faq`**
  for "ما هي وفرها؟" (planner deterministically returns `search_offers`,
  ungrounded → `_needs_clarification` → clarify), and the engine-side
  `_facade_faq_topic` override that could reroute it is dead (never bound,
  and not present in the committed HEAD the live server runs).
- Design §4 makes `faq` a **first-class pre-planner intent**: the router
  calls the same `_route_faq_topic` (rag_engine.py:479) that currently only
  runs inside `RetrieveFaqTool.run`; the about-company rules already classify
  "ما هي وفرها" → `faq_12_about`. On a hit the router emits a deterministic
  mini-plan (`tool=retrieve_faq, args={query}`), zero planner calls.

Therefore:

- **5q2 is tracked as a Stage-1 acceptance criterion** (design §4 +
  rewritten `test_faq_topic_query_reroutes_to_retrieve_faq_not_offer_search`),
  stated as:
  > *"GK/company-info queries (`ما هي وفرها؟`, `معلومات عن الشركة`,
  > `What is Waffarha?`) must route to the `faq` intent → `retrieve_faq`
  > mini-plan and answer from `faq_12_about`, without ever calling the
  > planner."*
- The standalone bug-fix list keeps **5q1** (missing `faq_refund_policy` doc
  → index rebuild), which is a data problem, and drops **5q2** (a routing
  problem) entirely from that list. Fixing it separately would duplicate the
  router's `faq` handling — explicitly to be avoided.

---

## 3. Ordering proposal

Three work queues. Independent strands run in parallel; only the production
index write has a hard environmental constraint (the Qdrant folder lock held
by the live server — the Sep 12 rebuild attempt died on
`"Storage folder already accessed by another instance"`).

### Queue A — immediate, zero index dependency (start now)

| # | Item | Notes |
|---|---|---|
| A1 | **Bug 1** — `_chunk_text` spacing fix (core/app.py:798) | "isolated, no dependencies"; chunk reassembly is a transport-layer fix usable on :8000/:8001 immediately. Single diff. |
| A2 | **Stage 1 Phase 1** — unified intent table + normalizer (`core/greetings.py` + `normalize_router_text`) as working code with its own passing test file | *Incremental rule: review this in isolation FIRST.* Synthetic fixtures only (facets as in `test_faceted_routing.py`; `_route_faq_topic` direct calls). No `_turn_params` wiring in this diff. |
| A3 | **Bug 4** — `SuperlativeOfferTool` schema (+ `product`), freshness gate on the superlative pool, grounded fallback scoping | Testable offline with synthetic doc fixtures (existing faceted test harness). No index, no router dependency. Slots anywhere in the A strand; not gated by A2. |
| A4 | **Bug 3 matcher** — tier-aware product matcher (faceted.py:416-426 no longer substring-scans pricing-options lines) | CODE side of bug 3, testable with synthetic tier-priced docs immediately. Interacts with the index at runtime only (see B-2). Keep separate from A2 to avoid two review concerns in one diff. |

### Queue B — index rebuild (bugs 2/3/5q1 + bug-3 data side)

| # | Item | Notes |
|---|---|---|
| B1 | **Rebuild pipeline validation** — run `build_index` with `--out-dir` into the concrete scratch store (`data/index_scratch/BAAI__bge-m3/qdrant`) | Parallel to A, no server interaction (scratch path has its own lock — see §3.1). Validates 3237-tier/1077-offer load seen in the Sep 12 log into a fresh store, proves the write path, produces the promotion artifact. |
| B2 | **Production write (gated by GATE-2)** — stop server, promote scratch store into the live path, validate before cutover, verify bugs 2/3/5q1 | See §3.2 for the exact promotion/validation steps and §6 for rollback. |

### Queue B — §3.1 scratch store (B1), concrete

- **Exact path:** `data/index_scratch/BAAI__bge-m3/qdrant` — outside the live
  `data/index/...` tree entirely.
- **Command:** `python ingestion/loaders/build_index.py --backend qdrant --embedding-model BAAI/bge-m3 --out-dir data/index_scratch/BAAI__bge-m3/qdrant`
  (or `--out-dir data/index_scratch/BAAI__bge-m3/faiss` for a FAISS-dir layout).
- **Lock scope confirms it does not collide with PID 11480:** `QdrantStore`
  locks **per-folder**. Embedded mode does `QdrantClient(path=persist_path)`
  (vectorstores.py:184-185) and the docstring states "only ONE process may
  hold it open at a time"; the live `.lock` file lives inside
  `data/index/BAAI__bge-m3/qdrant/.lock` (verified on disk). A second client
  pointed at a **different** folder is a fully independent instance — the
  Sep 12 failure was a build writing to the SAME live folder while PID 11480
  held it. A build into `data/index_scratch/...` never touches that lock.
- **Same-source guarantee:** B1 and B2 use the SAME corpus snapshot
  (`data/faqs.json` + `data/offers_raw.json` + `data/type_prices.json`),
  `build_index.py main()` encodes once and loops backends, so `--backend
  qdrant` alone (one backend) is the lean run.

### Queue B — §3.2 promotion (scratch → production, B2)

Order of operations, in one maintenance window:

1. **Gate GATE-2 go/no-go passes** (§5). Server (`run_servers.py`, PID 11480)
   is STOPPED first — the Qdrant `.lock` on the live folder is held for the
   whole server lifetime, so promotion must not start while it is up.
2. **Back up the live folder** (see §6): `data/index/BAAI__bge-m3/qdrant`
   → `data/backup/index/BAAI__bge-m3/qdrant.<timestamp>`. Copy the whole
   directory (docs.pkl + index store + bm25.pkl + manifest, whatever exists).
3. **Promote the scratch store into the live path** — two supported options:
   - **A (path swap, recommended):** move/copy
     `data/index_scratch/BAAI__bge-m3/qdrant` → `data/index/BAAI__bge-m3/qdrant`
     (delete or rename the old live folder first), then confirm the engine can
     open it by running the validation probe (§3.3) against the new live path
     in a standalone process. With the server stopped, no lock is held, so
     this is a plain filesystem op.
   - **B (re-run on live path):** re-run `build_index.py` WITHOUT `--out-dir`
     while stopped (writes into the canonical live path). Slower (re-encodes)
     but keeps a single source of truth for the live dir. Use only if the
     scratch store's artifacts can't be moved cleanly.
4. **Pre-cutover validation (§3.3) on the promoted live path: PASS required**
   before the server restarts or the backup (§6) is touched.
5. **Restart the servers**; smoke-verify bug 2 (Rush Hub card range), bug-3
   data half (pizza pool), 5q1 (`faq_refund_policy` retrievable).
6. The backup from step 2 is kept until smoke verification completes (§6).

### Queue B — §3.3 promotion validation probe (pre-cutover, on the live path)

A standalone script (no server up) that loads the promoted folder exactly as
the engine would and asserts:

1. `docs.pkl` loads and contains `faq_refund_policy` (5q1) and the tier
   metadata (`meta.min_price`/`max_price`/`n_tiers`) on target offer docs — the
   presence that fixes bug 2.
2. Qdrant collection exists and `search()` returns non-empty hits (a
   prompt e.g. `"بيتزا عروض"`) — proves the store opened under the new path /
   collection name (collection name is derived from `persist_path`, so the
   promoted folder must be the path the engine will load).
3. Manifest/`docs.pkl` cross-check passes (engine's Phase-4 check,
   rag_engine.py:1709-1736).
4. `bm25.pkl` present if `ENABLE_HYBRID_RETRIEVAL` (hybrid path isn't
   silently degraded).

Only when all four pass is the backup allowed to be retained-only (not
deleted) and the server restarted against the promoted store.

### Queue C — Stage 1 cutover (after A2 review + B2)

| # | Item | Notes |
|---|---|---|
| C1 | **Stage 1 Phase 2** — wire the router into `_turn_params`, delete the old gate (`_retrieval_allowed` family per design §6 rewrite), keep retrieval-gate backstop | *Incremental rule: SEPARATE reviewable step from A2.* Depends on A2 (the router) being reviewed; the rebuild (B) is not required for the wiring's correctness (faq intent path uses live-index-only at answer time, but that is existing tool code). |

### Explicit sequence (folded)

```
Now        A1 (bug 1)           A2 (router phase 1)     B1 (rebuild validate, scratch)
Parallel   A3 (bug 4)           A4 (bug 3 matcher)
Gate1      GATE-1: A2 isolated diff signed off  ->  unblocks C1
Downtime   GATE-2: go/no-go (B1 probe pass + window + backup + go-record)
           B2 (promote scratch -> live, §3.2; probe §3.3 before restart; rollback §6)
Invariant: nothing writes the live Qdrant folder while PID 11480 holds it;
           backup + probe PASS required before restart / backup deletion.
Cutover    C1 (router phase 2: _turn_params wiring + old-gate removal)
Acceptance GATE-3: smoke (bug 2 range, bug 3 pool, 5q1 refund) then C1's
           faq-intent criteria incl. 5q2 verified against live index.
```

### Rebuild interaction with Stage 1 — statement

The rebuild does not gate any Stage 1 *testing* (per §1). It gates:
(a) the **runtime** faq answer content for a small doc subset (`faq_refund_policy`,
5q1) and structured price tiers (bugs 2/3 data half), and (b) the final
**end-to-end verification** that the tier-aware matcher and the router behave
against real production data. Both are runtime/verification gates, sequenced
as C1's acceptance — not as prerequisites for writing or reviewing Stage 1.

---

## 4. Things deliberately NOT scheduled

- 5q2 is not scheduled as a bug fix (folded into C1 acceptance, §2).
- No planner/`.env` changes (frozen per Phase 2 constraints).
- No bug-3 "fix-by-rebuild-alone" shortcut (report: rebuild alone does not fix
  bug 3; A4 + B2 both land).
- No merging of Stage 1 Phase 1 + Phase 2 into one diff (incremental rule).

---

## 5. Explicit sign-off gates (named checkpoints)

**GATE-1 — A2 sign-off (Stage 1 router Phase 1, isolated).**
A2 is the working-code diff `core/greetings.py` + normalizer with its own
passing test file, reviewed **in isolation** (no `_turn_params` wiring). This
gate is **required before C1 (cutover) may start**. C1 is not permitted to
proceed off the strength of the A2 diff alone or a passing test run — the
intent-table/normalizer surface must be explicitly signed off first.

**GATE-2 — B2 go/no-go (production index write).**
B2 must not start on a schedule; it requires an explicit go/no-go with ALL of:

1. **B1 scratch validation passed:** a standalone probe run against
   `data/index_scratch/BAAI__bge-m3/qdrant` satisfies the §3.3 conditions
   (open-able, 3237-tier/1077-offer doc set present, `faq_refund_policy`
   present). Failure of any → NO-GO, B1 re-run, do not proceed.
2. **Confirmed maintenance window:** server stopped, PID 11480 not holding
   the live Qdrant `.lock` (verified by attempting the engine-load probe while
   stopped — success proves no lock).
3. **Rollback plan armed** (§6): live `data/index/BAAI__bge-m3/qdrant`
   backed up to `data/backup/index/BAAI__bge-m3/qdrant.<timestamp>` with the
   pre-copy checksum/listing recorded, recovery steps rehearsed.
4. **Explicit go/no-go recorded** (who approved, timestamp) in
   `refresh_log.json` or the report file before the promotion command runs.

B2 without GATE-2 = blocked, matching the Sep 12 lock-failure precedent.

**GATE-3 — post-B2 smoke (implicit, inside B2's own validation).**
The pre-cutover §3.3 probe must pass before restart; the smoke verification
(bug 2 range, bug 3 pizza pool, 5q1 refund) runs after restart, and the §6
backup is retained until that smoke finishes (could be several hours).

---

## 6. B2 rollback plan (added — concrete, not a placeholder)

**Is the current live store backed up before B2 writes over it? YES — this is
a mandatory B2 pre-step (GATE-2 item 3), not optional.**

- **What gets backed up:** the entire live folder
  `data/index/BAAI__bge-m3/qdrant/` (all files, including `docs.pkl`, the
  Qdrant collection files, `bm25.pkl`, manifest, `.lock`) is copied verbatim
  to `data/backup/index/BAAI__bge-m3/qdrant.<timestamp>/` (timestamped, never
  overwritten).
- **Backup is verified before promotion:** record the pre-copy file listing +
  size/sha256 of `docs.pkl` (and any other checksums chosen) in the report /
  log, and confirm the backup copy has the same file count + checksum. If the
  backup does not verify, B2 is NO-GO.

**Recovery paths per failure mode (all with server STOPPED):**

1. **Write fails partway (e.g. copy interrupted, disk full):** the promotion
   targets a fully-written scratch folder — a failed copy leaves the live path
   untouched or half-populated but the engine is not running (server stopped),
   so no corrupted state is served. Recovery: re-run promotion step 3 (copy
   again) or restore backup. If the live folder is half-written, delete the
   partial promoted contents first, then restore the backup.
2. **Promoted store corrupt/incomplete tiers / probe fails:** rename the
   promoted folder aside (`.../qdrant.promote-failed.<ts>`), copy the backup
   folder back into the live path, run the §3.3 probe against the restored
   path (must pass), keep investigating the failed promotion offline.
3. **Lock recurs mid-write ("Storage folder already accessed by another
   instance"):** this is the Sep 12 failure class. Abort immediately — do NOT
   force. Confirm no stray process holds the live path (check running python
   processes; PID 11480 must be gone; probe open must succeed when stopped).
   Restore the backup if the live folder was touched. Do not restart servers
   until the probe passes.
4. **Post-restart regression (server boots but answers wrong):** roll back per
   case 2: server → backup → probe → restart. The §6 backup is retained
   through smoke verification precisely so this is cheap.

**Backup retention:** retained at minimum until GATE-3 smoke verification
passes (bug 2/3/5q1 verified against the promoted store on a running server).
After that the backup may be archived/house-kept; it must not be deleted
before smoke passes.