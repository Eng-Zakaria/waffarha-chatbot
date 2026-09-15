"""
Planner: turns the user's query (plus conversation context) into ONE
structured JSON decision -- the agent's "understanding + plan" for the
turn. Deterministic tool execution proceeds from this single call.

The planner is the ONLY unstructured LLM call in Stage 2. Everything after
it (validation, coercion, tool selection, evidence gating, rendering) is
deterministic, so a single bad/short answer from the model degrades
gracefully into the standard semantic fallback instead of an unbounded loop.
"""
from __future__ import annotations

import json
import re

_KNOWN_TOOLS = ("search_offers", "get_offer", "retrieve_faq",
                "compare_offers", "superlative_offer", "catalog")
_KNOWN_FIELDS = (
    "tool", "args", "goal", "entities", "constraints", "intent",
    "references", "known_information", "missing_information",
    "note", "next_action",
)


class PlanParseError(ValueError):
    """Raised when the model's plan cannot be interpreted safely."""


def build_plan_prompt(query: str, params: dict, tool_names: list,
                      tool_docs: str) -> str:
    """Assemble the planner prompt for one turn.

    params (context): reply_lang, detected_intent, recent_offers, references,
    conversation summary, personal profile subjects, identity status.
    """
    ctx_lines = []
    ctx_lines.append(f"- reply_language: {params.get('reply_lang', 'en')}")
    if params.get("detected_intent"):
        ctx_lines.append(f"- detected_intent (inferred only): {params['detected_intent']}")
    if params.get("conversation"):
        ctx_lines.append(f"- conversation_so_far: {params['conversation']}")
    if params.get("references"):
        ctx_lines.append(
            f"- references_from_context (anaphora, e.g. 'هالكوبون', 'this coupon'): "
            f"{json.dumps(params['references'][-6:], ensure_ascii=False)}")
    if params.get("recent_offers"):
        ctx_lines.append(
            f"- already_discussed_offers (ids only): "
            f"{[o.get('id') for o in params['recent_offers'][-8:]]}")
    if params.get("personal_subjects"):
        ctx_lines.append(f"- known_personal_subjects: {params['personal_subjects']}")
    if params.get("identity"):
        ctx_lines.append(f"- identity: {params['identity']}")
    ctx_block = "\n".join(ctx_lines) if ctx_lines else "(no context supplied)"

    return (
        "You are the planner of a Waffarha offers bot. Decide, in a SINGLE JSON answer, "
        "how this turn should be handled. You do NOT generate the final answer text; "
        "you only plan which tool to run and what evidence will satisfy the user. "
        "The final answer is then produced deterministically from tool evidence, so any "
        "claim you make here must be re-derivable from the tool results.\n\n"
        f"TOOLS AVAILABLE:\n{tool_docs}\n\n"
        "USER QUERY:\n"
        f"{query[:1000]}\n\n"
        "CONTEXT:\n"
        f"{ctx_block}\n\n"
        "Think for a moment, then answer with ONLY a JSON object, no prose before or after, "
        "with these exact keys:\n"
        '  "tool": one of ' + ", ".join(f'"{t}"' for t in tool_names) + ' or "none"\n'
        '  "args": object of arguments for that tool -- the tool schema decides which keys '
        "(search_offers/catalog: query, merchant, category, product, price_range as [min,max], "
        "exclude as list of ids, limit as int; get_offer: id and source; retrieve_faq: query; "
        "compare_offers: offer_ids (ids from the already_discussed_offers context "
        "block ONLY) or merchant; "
        "superlative_offer: direction cheapest|most_expensive|highest_discount plus optional "
        "merchant/category; use a tool only if you can fill its required args from the query or "
        "context)\n"
        '  "goal": one short sentence describing what the user wants\n'
        '  "entities": object — merchant/product/category names mentioned, e.g. '
        '{"merchant": ["KFC"]}\n'
        '  "constraints": object — {"price_range": [min, max], "exclude": [ids], "limit": n}\n'
        '  "intent": one of "catalog", "comparison", "personal", "anchored", "quality", '
        '"faq", "greeting", "closing", "out_of_scope", "other"\n'
        '  "references": list of strings — phrases that reference an offer/coupon mentioned '
        "in conversation (e.g. \"that coupon\", \"هالخصم\"); empty if none\n"
        '  "known_information": list of strings — concrete facts already resolved from '
        "conversation that ground this turn (e.g. \"merchant=KFC\"); empty if none\n"
        '  "missing_information": "string — what is still unknown that would make the answer '
        'precise (e.g. the merchant); empty if none"\n'
        '  "note": short planning note\n'
        '  "next_action": one of "plan", "clarify", "respond_one", '
        '"respond_trace", "respond_empty", "report_unknown"\n'
        "\n"
        'Rules:\n'
'- "plan" means: run the chosen tool now; decide "respond" afterwards.\n'
        '- "clarify": when the query is genuinely ambiguous and no tool can satisfy it yet. '
        "Reply in the user's language; ask ONE concrete question; do not run a tool.\n"
        '- "respond_one": answer directly with what is already known, no tool needed.\n'
        '- "respond_trace": also safe to answer now.\n'
        '- "respond_empty": answer with no offer evidence.\n'
        "- Constraints: ONLY include a constraint the user EXPLICITLY stated (their language). "
        "Do NOT invent price ranges, limits or exclusions. price_range must be a JSON array of "
        "TWO numbers [min, max]; if only an upper bound is known use [0, max].\n"
        "- References rule (hard): the already_discussed_offers block lists the ONLY ids that "
        "exist for this turn. get_offer and compare_offers may ONLY be chosen when the target id "
        "is in already_discussed_offers -- never invent an id, merchant, price, or ordinal from "
        "conversation. A follow-up (\"the second one\", \"ده\", \"compare the two\") resolves to "
        "those stored ids or the deterministic reference pass drops it.\n"
        "- Tool guidance: retrieve_faq for how-to/policy/status questions ('how', 'policy', "
        "'refund', 'يعني ايه'); superlative_offer for 'cheapest/highest discount/most expensive'; "
        "compare_offers for 'compare/difference between/which is better'; catalog with "
        "scope=personal only when the user asks about THEIR OWN coupons/account; search_offers "
        "otherwise.\n"
        "- Assigned role: flow only. User anonymous: assume public persona.\n"
        '- If a merchant was mentioned and you are not sure it exists, still plan search_offers '
        'with "merchant" set — the tool reports unknown merchants deterministically.\n'
        "- NEVER invent offer content, prices, or merchants. Your plan must be satisfiable "
        "by the tools' real output.\n"
        "- If the user is greeting/closing/out-of-scope, choose 'none' as tool and "
        "'respond_one' as next_action.\n"
        "- Reply language should match the user's language for any future clarify/respond text.\n"
        "- conversational style: concise, confident, unapologetic; use Markdown bullets.\n"
        "- Output JSON only."
    )


def _extract_json_block(raw: str) -> str:
    """Pull the JSON object out of a model reply that may wrap it in fences
    or stray prose."""
    if not raw:
        raise PlanParseError("empty planner output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise PlanParseError("no JSON object found in planner output")
    return raw[start:end + 1]


def parse_plan(raw: str, tool_names: list) -> dict:
    """Parse + validate the planner's JSON decision into a safe, typed plan.
    Raises PlanParseError on anything unusable."""
    names = set(tool_names)
    try:
        data = json.loads(_extract_json_block(raw))
    except (json.JSONDecodeError, PlanParseError):
        raise PlanParseError("planner produced invalid JSON")
    if not isinstance(data, dict):
        raise PlanParseError("planner output is not an object")

    plan = {k: data.get(k) for k in _KNOWN_FIELDS if k in data}

    tool = plan.get("tool", "none")
    if tool != "none" and tool not in names:
        raise PlanParseError(f"planner proposed unknown tool {tool!r}")

    args = plan.get("args")
    if args is not None and not isinstance(args, dict):
        raise PlanParseError("planner args must be an object")

    next_action = plan.get("next_action")
    if next_action and next_action not in (
            "plan", "clarify", "respond_one", "respond_trace",
            "respond_empty", "report_unknown"):
        raise PlanParseError(f"unknown next_action {next_action!r}")

    entities = plan.get("entities")
    if entities is not None and not isinstance(entities, dict):
        raise PlanParseError("entities must be an object")
    constraints = plan.get("constraints")
    if constraints is not None and not isinstance(constraints, dict):
        raise PlanParseError("constraints must be an object")

    references = plan.get("references") or []
    if not isinstance(references, list):
        references = [str(references)]
    known = plan.get("known_information") or []
    if not isinstance(known, list):
        known = [str(known)]

    plan["references"] = [str(r) for r in references]
    plan["known_information"] = [str(k) for k in known]
    plan["missing_information"] = plan.get("missing_information") or ""
    return plan


def call_planner(client, model: str, prompt: str, tools: list,
                 params: dict, num_predict: int = 600) -> dict:
    """Run the single structured planning call. Returns
    {"raw":..., "plan":{...}} or raises PlanParseError on failure.

    `client` is an ollama.Client or None; a None client (offline) raises
    PlanParseError so the engine degrades to deterministic fallback."""
    if client is None:
        raise PlanParseError("no planner client available")
    stream = client.chat(model=model, messages=[{
        "role": "user",
        "content": prompt,
    }], options={"temperature": 0.0, "num_predict": num_predict}, stream=True)

    chunks = []
    for piece in stream:
        if piece.get("done"):
            break
        chunks.append(piece.get("message", {}).get("content", ""))
    raw = "".join(chunks).strip()

    try:
        plan = parse_plan(raw, tools)
    except PlanParseError as e:
        raise PlanParseError(f"planner decision unusable ({e})")

    # contextual references fold into the plan
    contextual = params.get("references") or []
    plan["references"] = _dedup_strings(list(contextual) + plan.get("references", []))
    return {"raw": raw, "plan": plan}


def _dedup_strings(values: list) -> list:
    seen = set()
    out = []
    for v in values:
        key = v.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out