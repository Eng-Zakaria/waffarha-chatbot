# Incident record: retrieval-gate WIP discovered in the worktree and quarantined

- **Date:** 2026-09-19
- **Scope:** one uncommitted feature (`agent/engine.py` + `agent/grounding.py`) and an uncommitted 33-test suite, quarantined out of a fix commit; a separate `.gitignore` visibility defect reported.
- **Status:** recorded. No Stage 1 review, no further action.

This document is a factual record. It states what is provable from repository
state, timestamps, and history; it does not attribute intent.

---

## 1. What was found

An implementation of the **retrieval-required gate** (greeting / joke / prompt
injection / out-of-scope → no retrieval, zero cards), built as a pre-tool
router in `agent/engine.py` plus corroboration-merge logic in
`agent/grounding.py`, was present in the working tree.

It also shipped with an uncommitted, untracked **33-test suite** at
`tests/unit/test_agent_engine.py` (the gate decision tests plus engine-level
regressions that exercise the gate). Nothing in the gate was committed, and
nothing in the suite was committed.

### Timeline (→ 18-Sep 11:07:43 window start)

| Item | Value |
|---|---|
| Latest snapshot without the gate | commit `75ce8a5`, authored **17-Sep 16:38:48** |
| Earliest hard evidence of gate code on disk | `agent/__pycache__/engine.cpython-313.pyc` with embedded source mtime **18-Sep 11:07:43** (pyc written 11:12:10) |
| Gate symbols present in the pyc | trace of `_safety_gate` / `_retrieval_allowed` / `_greeting_or_thanks` family |
| `tests/unit/test_agent_engine.py` mtime | **18-Sep 11:11** (inside the same window) |

Evidence chain: every commit in the repo reachable from `HEAD` was scanned
for gate symbols — 12 unreachable/stash commits were recovered and each
contains zero gate-symbol references; the gate appears first in the pyc
built 18-Sep 11:07:43 and in the working blob `76c2b10c`. The authorship
window for the gate is therefore **17-Sep 16:38:48 → 18-Sep 11:07:43**.

The feature landed in the tree without a task entry, without a commit
record, and without disclosure in any report.

## 2. The hold instruction it was built in spite of

The gate was proposed in
`reports/20260916_semantic_understanding_phase2.md` as a decision item
**D-5**:

> D-5. **Retrieval-required gate (§8): sanctioned as deterministic and pre-tool (greeting/joke/injection → no retrieval, zero cards)?**

The report closes, on line 334, with:

> Accept/reject each; the plan holds no further implementation until these are resolved.

No decision record exists for D-5. Implementation nevertheless landed ~18
hours after that report in the window established above, with no sign-off.

## 3. How it was found

Incidental, not a deliberate audit. The gate surfaced while reviewing
unrelated diffs (the per-call evidence / FAQ-regex fixes, item 4 below):
a provenance check was forced — every reachable commit, every unreachable
commit, the stash, and the pyc files were compared — and only then did the
gate appear as code with no commit ancestor. Stated plainly: had that
unrelated review not forced the provenance check, the gate could easily
have gone uncaught, exactly as it had gone unreported.

## 4. Quarantine method

1. **Byte snapshot first:** every modified file copied to
   `snapshot_pre_exec\` with `git hash-object` recorded (10 files: 5 gate/fix,
   5 pre-existing WIP).
2. **5-file split:** the full worktree diff was split into
   - `fix1_fix2_only.patch` — only the two approved fixes; and
   - `gate_wip.patch` — only the gate (5 files: engine.py 14 hunks,
     grounding.py 3 hunks, rag_engine greeting-phrase block + full file
     for grounding given its late arrival).
3. **Verification:** each patch reverse-applies cleanly; union of both
   patch hunks reproduces the original diff exactly (per-file hunk count
   check), and the reverse-apply + restore path was verified byte-for-byte
   against the snapshot hashes.
4. **Restore discipline:** the committed round shipped fix-only; the gate
   was re-applied on top of that commit and verified hash-for-hash against
   the snapshot so nothing was lost.

## 5. What is committed

Commit **`f910aa8`** — `agent evidence plumbing: stream evidence with turn
reports (fixes stale/absent sources)`

| File | Commit blob (sha1 prefix) |
|---|---|
| `agent/engine.py` | `470058fc` (FIX-1: per-call evidence in turn reports) |
| `core/agent_server.py` | `531b3478` (FIX-1 consumer) |
| `core/app.py` | `26ff6db6` (FIX-1 consumer) |
| `core/rag_engine.py` | `17b881a0` (FIX-2: FAQ refund-policy regex — `return policy` singular added) |

24 insertions / 8 deletions, 15 hunks, gate content absent. Content verified
blob-for-blob equal to `fix1_fix2_only.patch`.

## 6. What is NOT committed, and why

**`gate_wip.patch`** is held, uncommitted, in the working tree:

- `agent/engine.py` — the `_retrieval_allowed` / `_greeting_or_thanks` /
  `_has_offer_topic_signal` / `_looks_like_explicit_browse` / `_safety_gate`
  router family (14 hunks)
- `agent/grounding.py` — corroborated-entity merge behavior (3 hunks; file
  arrived after the snapshot, same undisclosed late-arrival pattern)
- `core/rag_engine.py` — the informal greeting-phrase additions consumed by
  `_looks_like_greeting`

The gate and its test suite stay quarantined pending a dedicated review
against the Stage 1 spec should one ever happen.

Stated explicitly: **the existence of this code does not constitute
Stage 1 being done, in progress, or pre-approved.** The Phase 2 decision
item D-5 was never resolved; the plan's closing instruction explicitly
held further implementation until the decision items were accepted or
rejected.

Gate-coupled test surface (44 functions): 39 blocked at collection
(`test_agent_engine.py` 33 module-level imports; `test_agent_evidence_isolation.py`
3 and `test_faq_override_wiring.py` 3 via import chain) + 5 runtime failures
(`test_agent_grounding.py::test_drop_product_not_in_message`,
`::test_merge_corroborated_product_omitted_by_planner`,
`test_hallucination_guards.py::TestGreetingDetection::test_informal_*_variants` ×3).

The 5 pre-existing WIP files (`agent/planner.py`, `docs/DOCKER.md`,
`docs/README.md`, `docs/requirements.txt`, `run_servers.py`) predate the
gate and remain untouched, uncommitted.

## 7. Separate, lower-severity finding (not fixed this round)

`.gitignore` contains the rule `test_*.py` (committed 2026-08-25 in
`6f482e3`, predates this engagement; re-added 2026-08-29 in `26b64d9`), under
a "Local-only test/debug scripts" heading. Because the pattern is unanchored,
it matches at any depth, making **most of `tests/unit/` invisible to
gitignore-respecting tooling** (git, the Grep tool used this round). Only
`tests/unit/test_reference_date.py`, tracked before the rule, is visible.

This is unrelated to the gate. It is flagged as a real defect worth fixing
on its own (e.g., anchor the pattern to a scratch directory or scope it to
repository root) — not fixed in this round.

## 8. Current full suite baseline (measured fresh, 2026-09-19)

Full `pytest` run (`pytest.ini` testpaths=tests, engine-marked tests
deselected by default) against the current tree — fix committed, gate
present uncommitted:

```
255 passed, 33 deselected in 38.07s
```

Composition: 252 tests/unit + 3 tests/test_rag_perfection.py = 255; the 33
deselected are the engine-marked tests opted out via
`addopts = -m "not engine"`.