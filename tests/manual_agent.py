r"""
Stage-3 manual driver for the Agentic Intelligence Layer.

Continuous REPL against the REAL RagEngine (faceted index + BGE-M3) that
shows, per turn: Goal / Constraints / Plan / Tools / Observation / Decision /
Answer, plus per-stage latency (planning / replan / reference / tool / render),
LLM-call / tool-call metrics and the full SAFE decision trace (never
chain-of-thought).

Conversation state (history + recent_offers) is preserved across the session
exactly like a live chat, so anchored follow-ups ("cheaper than this",
"this coupon") are resolved deterministically against the actual shown offers.

Usage:
    $env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe tests\manual_agent.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.engine import AgentEngine
from agent.tools import ToolRegistry
from agent.tools.cascade_tools import register_cascade_tools
from agent.tools.catalog_tools import register_catalog_tools


def _print_label(label: str):
    print(f"\n  ▶ {label}")


def _offer_item(item) -> dict:
    meta = item.get("metadata", {})
    return {
        "metadata": {
            "source": meta.get("source"),
            "id": meta.get("id"),
            "merchant": meta.get("merchant"),
            "price": meta.get("price"),
        }
    }


def _show_turn(query, report):
    tn = time.perf_counter()
    metrics = report.get("metrics", {})
    print("\n" + "=" * 76)
    print(f"USER : {query}")
    print("=" * 76)
    _print_label("GOAL")
    print("  " + (report.get("goal") or "(none)"))
    _print_label("INTENT")
    print("  " + (report.get("intent_note") or metrics.get("intent_candidate") or "(none)"))
    _print_label("ENTITIES")
    print("  " + _fmt(report.get("entities")))
    _print_label("CONSTRAINTS")
    print("  " + _fmt(report.get("constraints")))
    _print_label("GROUNDING")
    grounded = report.get("grounding") or {}
    if grounded:
        print("  confirmed: " + _fmt(grounded))
    dropped = metrics.get("grounding_dropped") or []
    for d in dropped:
        print("  · " + str(d))
    if not grounded and not dropped:
        print("  (no entity grounding needed)")
    _print_label("REFERENCES")
    refs = report.get("resolved_references") or {}
    if isinstance(refs, dict) and refs.get("kind"):
        print(f"  verdict  = {refs.get('verdict')} ({refs.get('kind')})")
        print(f"  reason   = {refs.get('reason_code')}")
        if refs.get("target_ids"):
            print("  targets  = " + ", ".join(str(i) for i in refs["target_ids"]))
        if refs.get("anchor_merchant"):
            print("  anchor   = " + str(refs["anchor_merchant"]))
    ref_dropped = metrics.get("reference_dropped") or []
    for d in ref_dropped:
        print("  · dropped: " + str(d))
    if not refs and not ref_dropped:
        print("  (no reference claims to corroborate)")
    _print_label("PLAN")
    plan = report.get("plan") or []
    for step in plan:
        print(f"  · {step.get('tool')}({_fmt(step.get('args'))})")
    _print_label("TOOLS")
    for o in report.get("observations", []):
        print("  · " + str(o))
    _print_label("DECISION")
    print("  next_action = " + str(report.get("next_action")))
    if report.get("decision_note"):
        print("  note: " + report["decision_note"])
    _print_label("ANSWER")
    answer = report.get("final_response") or ""
    print(answer)
    _print_label("METRICS")
    print("  planning_ms   = " + _ms(metrics.get("planning_ms")))
    print("  replan_ms     = " + _ms(metrics.get("replan_ms"))
          + " (calls " + str(metrics.get("replan_count") or 0) + ")")
    print("  reference_ms  = " + _ms(metrics.get("reference_ms")))
    print("  tool_ms       = " + _ms(metrics.get("tool_ms")))
    print(f"  llm_calls     = {metrics.get('budget_llm_calls')} budget | used {metrics.get('llm_calls', 0)}")
    print(f"  tool_calls    = {metrics.get('budget_tool_calls')} budget | used {metrics.get('tool_calls', 0)}")
    print(f"  evidence      = {report.get('evidence_count', 0)} docs")
    print(f"  turn_ms       = {round((time.perf_counter() - tn) * 1000, 1)}")
    print("-" * 76)


def _fmt(value) -> str:
    import json
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def _ms(value) -> str:
    return f"{value}ms" if value is not None else "n/a"


def main():
    print("=" * 76)
    print("Waffarha Agentic Layer — Stage 3 manual driver")
    print("Type queries (English/Arabic/Arabizi), or 'exit' to quit.")
    print("=" * 76)

    try:
        from agent.facade import rag_facade
        from core.config import AGENT_MODEL
        from core.rag_engine import RagEngine
        facade = rag_facade(RagEngine())
    except Exception as e:  # noqa: BLE001 -- CLI tool, report and exit
        print(f"Could not load RagEngine: {e}")
        print("Ensure the index is built and Ollama is reachable.")
        return

    registry = register_catalog_tools(ToolRegistry())
    register_cascade_tools(registry)
    engine = AgentEngine(facade, registry)
    print(f"Tools ready: {registry.names()}")
    print(f"Planner model: {AGENT_MODEL}; render: deterministic (no 2nd LLM)")

    history = []
    recent_offers = []

    while True:
        try:
            query = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not query:
            continue
        if query.lower() in ("exit", "quit", "خروج"):
            print("bye")
            break

        started = time.perf_counter()
        answer = ""
        report = {}
        for piece in engine.answer_stream(
                query, history=list(history[:8]),
                recent_offers=list(recent_offers[-8:])):
            if isinstance(piece, str):
                answer = piece
            elif isinstance(piece, dict) and piece.get("kind") == "agent_turn_report":
                report = piece["data"]
        turn_ms = round((time.perf_counter() - started) * 1000, 1)

        history.append({"role": "user", "content": query})
        if answer:
            history.append({"role": "assistant", "content": answer})
        if report:
            _show_turn(query, report)
            # track shown offers so anchored follow-ups have context
            for ev in getattr(engine, "_last_evidence", []) or []:
                mid = ev.get("metadata", {})
                if mid.get("source") == "offer":
                    recent_offers.append(_offer_item(ev))
            print(f"  total_turn_ms = {turn_ms}")
        else:
            print(answer)


if __name__ == "__main__":
    main()