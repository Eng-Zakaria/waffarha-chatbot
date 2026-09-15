"""
Typed agent state for the Waffarha agentic layer.

Every turn of the bounded agent loop carries one AgentState object through
understand -> plan -> execute -> observe -> decide -> respond.

The state captures exactly the structured information the agent needs for
multi-turn execution and for a SAFE trace. Trace entries are structured
decisions (goal, constraints, tool selection, evidence summaries, next
action) -- NOT chain-of-thought. Private model reasoning is never stored
or rendered in these fields.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

TRACE_KIND_UNDERSTANDING = "understanding"
TRACE_KIND_PLAN = "plan"
TRACE_KIND_TOOL = "tool"
TRACE_KIND_OBSERVATION = "observation"
TRACE_KIND_DECISION = "decision"
TRACE_KIND_RESPONSE = "response"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

CLARIFICATION = "clarification"
RESPOND = "respond"
RESPOND_NONE = "respond_none"
REPLAN = "replan"
DONE = "done"


@dataclass
class TraceStep:
    """A single structured step in the safe decision trace."""
    kind: str
    summary: str
    reason_code: str | None = None
    detail: str | None = None
    ms: float = 0.0

    def to_text(self) -> str:
        ms = f" ({self.ms:.0f}ms)" if self.ms else ""
        head = f"[{self.kind.upper()}] {self.summary}{ms}"
        if self.reason_code:
            head += f"\n  reason: {self.reason_code}"
        if self.detail:
            head += f"\n  detail: {self.detail}"
        return head


@dataclass
class AgentState:
    """One turn of agentic execution. Every field is structured; none is raw
    hidden reasoning."""
    messages: list = field(default_factory=list)
    goal: str | None = None
    intent_note: str = ""
    entities: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    references: list = field(default_factory=list)
    resolved_references: list = field(default_factory=list)
    known_information: list = field(default_factory=list)
    missing_information: str = ""
    grounding: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    selected_tool: str | None = None
    tool_calls: int = 0
    observations: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    current_step: str = "understand"
    retries: int = 0
    completion: bool = False
    confidence: str = CONFIDENCE_NONE
    next_action: str = "plan"
    decision_note: str = ""
    final_response: str = ""
    trace: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)

    def trace_step(self, kind: str, summary: str, reason_code: str | None = None,
                   detail: str | None = None, ms: float = 0.0) -> TraceStep:
        self.trace.append(TraceStep(kind, summary, reason_code, detail, ms))
        return self.trace[-1]

    def add_evidence(self, items: list):
        if not items:
            return
        seen = set(self.evidence)
        for it in items:
            meta = it.get("metadata", {})
            key = meta.get("source") if meta else None
            oid = meta.get("id") if meta else None
            ident = (key, oid) if (key is not None and oid is not None) else None
            if ident and ident in seen:
                continue
            if ident:
                seen.add(ident)
            self.evidence.append(it)

    def to_trace_text(self) -> str:
        lines = []
        for step in self.trace:
            lines.append(step.to_text())
            lines.append("")
        if not lines:
            return "(no trace)"
        return "\n".join(lines).strip()

    def snapshot(self) -> dict:
        """Serialisable snapshot of the state's structured fields (no chain-
        of-thought). Used for logs and the eval harness."""
        return {
            "goal": self.goal,
            "intent_note": self.intent_note,
            "entities": self.entities,
            "constraints": self.constraints,
            "references": self.references,
            "resolved_references": self.resolved_references,
            "grounding": self.grounding,
            "missing_information": self.missing_information,
            "plan": [
                {"tool": p.get("tool"), "args": p.get("args"), "note": p.get("note")}
                for p in self.plan
            ],
            "tool_calls": self.tool_calls,
            "observations": list(self.observations),
            "evidence_count": len(self.evidence),
            "next_action": self.next_action,
            "decision_note": self.decision_note,
            "confidence": self.confidence,
            "completion": self.completion,
            "final_response": self.final_response,
            "metrics": self.metrics,
            "trace": [
                {
                    "kind": s.kind,
                    "summary": s.summary,
                    "reason_code": s.reason_code,
                    "detail": s.detail,
                    "ms": round(s.ms, 1),
                }
                for s in self.trace
            ],
        }

    def __repr__(self) -> str:
        return (
            f"AgentState(goal={self.goal!r}, next_action={self.next_action!r}, "
            f"tool_calls={self.tool_calls}, evidence={len(self.evidence)})"
        )