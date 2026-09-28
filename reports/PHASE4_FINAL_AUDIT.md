# PHASE-4 SEMANTIC-BOUNDARY FINAL AUDIT (read-only; no experiment/lift changes)

Audit of the Phase-4 outcome for the Waffarha coupon chatbot, run wholly in read-only mode against the on-disk evidence produced during Phase-4 on 2026-09-16. This audit spawns no models, runs no classify/extract climbs, and does NOT touch rebuild/servers/repo state. It only re-administers the artifact census, re-crosses the numbers, and confirms the boundary decision.

## 1. Audit origin and boundary

Boundary honored: the Phase-4 goal is a *decision* on where to place the semantic boundary (deterministic vs LLM), grounded in measured evidence. This audit re-verifies that evidence and the resulting decision; it does not implement anything.

## 2. Files read during this audit (read-only census)

- `reports/20260917_architecture_investigation.md` — the canonical Phase-4 report (§1–§26).
- `reports/report_numbers.json` — root-level summary of recorded numbers.
- `results/model_cap.jsonl` — per-case raw captures for the main models.
- `results/model_cap_all.jsonl` — per-case raw captures for all three models + deterministic baseline.

Nothing under these paths was modified; the git working tree was not altered by this audit.

## 3. Evidence census (labels in the canonical report)

Re-run of the orthography/evidence-label census over the canonical report:
- Sections §1–§26: present, unique, sequential (26/26).
- Line/byte count of the canonical report: 478 lines / 41,977 bytes.
- Evidence label counts: OBSERVED-FACT=20, EXPERIMENTAL-RESULT=14, INFERENCE=17, RECOMMENDATION=19.
- No section heading is duplicated; no § is out of order.

## 4. Number cross-checks (abstracted from raw artifacts)

- Deterministic baseline routing accuracy on the 40-case corpus: **28/40 (0.70)** (`report_numbers.json` → `deterministic_baseline_classify`).
- Every local model (llama3.2:latest, qwen2.5:3b-instruct, command-r7b-arabic:latest), same 40 cases: **17/40 (0.425)** (`model_cap_all.jsonl` → `routing_acc=17/40` for each of the three models).
- Multilingual free/constrained semantic extraction, 12+12 cases, exact-match: **0/12 free and 0/12 constrained** for all three models (`extract_free`/`extract_con` → `free_exact=0`, `con_exact=0`).
- Interpret / reference / decompose rows: 6 / 7 / 3 (match artifact counts).
- All figures above re-derived from the raw JSON/JSONL artifacts during this audit; the earlier-reported tables are confirmed accurate to the artifact level.

## 5. Contamination check

Re-audited the canonical report for injected/oracle content and prohibited vocabulary:
- 0 hits for ORM, courier, rebound, formula, `__slots__`.
- 0 hits for LangChain; the only "LangGraph" occurrences are the two explicit "Do NOT add LangGraph" recommendation statements in §25/§26 — prohibition language, not a library change.
- No external/oracle model rows, no introduced dependencies, no openai/remote keys; all model captures are from the four local models listed in the artifact banner (2026-09-16).

## 6. Decision under audit

The Phase-4 decision, abstracted from the evidence: the deterministic routing baseline (28/40) already achieves the best measured routing accuracy; every tested LLM underperforms it (17/40) and adds latency (2.5–5.6 s avg) plus 0/12 exact extraction. Therefore the semantic boundary is placed **deterministic-first**: rule-based intent/classify routing on the request path, with an LLM interpreter only as a post-routing recovery/second-stage step — and no new library, model upgrade, index/ingest change, or LangGraph is to be introduced (confirmed by the contamination check: the report's own §25/§26 state exactly this).

## 7. Standing facts from the audit

- The deterministic boundary is the authority on the primary path; the LLM is a conditional secondary interpreter (render as "oracle only when deterministic is insufficient").
- Semantic lift for all local models: 0.0 exact on extraction, 17/40 routing — i.e., the deterministic baseline already covers the semantics with less latency.
- The smallest architecture that keeps the product moving is the deterministic core + recovery path that already exists; scaling the model and adding a framework (LangGraph) are both explicitly rejected by the report.

## 8. Audit result

READ-ONLY PASS. No file under the workspace was created or modified by this audit except this audit file. No model was spawned. No further semantic-lift work is required to justify the Phase-4 boundary decision. Clean exit.
