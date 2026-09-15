"""Pure, deterministic metric helpers for the Stage-4 eval harness.

These classify what the engine ALREADY recorded in ``report.metrics`` (the
``steps`` and ``evidence_ids`` arrays) -- they run OUT of process, in the eval
harness, never inside the agent loop. No heavy imports, no I/O.

Metrics defined here:
  - ``classify_unnecessary_step``   -> the unnecessary-tool-call rate. Strict
    definition: a tool step is unnecessary when every offer id it returned was
    already shown earlier in the session (the same result was available without
    calling the tool), OR when its result never reached the final answer.
    A step that returned no offer ids at all ('empty result') is counted
    separately as ``empty_result`` -- it is not label-able one way or the other.
"""
from __future__ import annotations


def classify_unnecessary_step(step_ids, shown_before_ids, answer_ids):
    """Classify one executed tool STEP (``report.metrics.steps[i]``).

    Args:
        step_ids: offer ids the step returned (metadata ids, source=='offer').
        shown_before_ids: all offer ids shown in the session BEFORE this turn.
        answer_ids: all offer ids the final answer actually rendered.

    Returns a dict:
        redundant_refetch: every returned id was already shown before (the
            data existed without the call -- e.g. the planner re-searched the
            exact merchant that was just shown).
        unused_result: none of the returned ids appear in the final answer
            (fetched, then the turn answered from a different source).
        unnecessary: redundant_refetch OR unused_result.
        empty_result: the step returned no offer ids (cannot be classified).
    """
    shown = {str(i) for i in (shown_before_ids or [])}
    answer = {str(i) for i in (answer_ids or [])}
    ids = [str(i) for i in (step_ids or [])]
    if not ids:
        return {"redundant_refetch": False, "unused_result": False,
                "unnecessary": False, "empty_result": True}
    refetch = all(i in shown for i in ids)
    unused = not any(i in answer for i in ids)
    return {"redundant_refetch": refetch, "unused_result": unused,
            "unnecessary": bool(refetch or unused), "empty_result": False}