"""
The bounded agent engine that runs Stage 3 of the Waffarha agentic layer.

Per turn:
  1. SAFETY GATE (deterministic, pre-agent): closing / greeting / gibberish /
     injection-detection short-circuits + sanitize + arabizi normalization.
     These stay in front of the agent per the approved design.
  2. UNDERSTAND + PLAN: one structured planner call.
  3. REFERENCE PASS (deterministic): corroborate every follow-up claim against
     the STORED last-shown offers (never freeform history) -- ordinals,
     merchant mentions, comparison/other-offer wording, price pointers. Unconfirmable
     planner-proposed references are dropped. A resolved reference reroutes the
     turn to compare_offers / get_offer / an anchored other-merchant search.
  4. VALIDATE: coerce args, reject unknown tools.
  5. EXECUTE: run the tool with provenance tracking and budget.
  6. OBSERVE / EVIDENCE GATE: deterministic check that returned evidence can
     satisfy the plan's constraints (content type, price span, counts).
  7. RELAXATION and BOUNDED REPLAN: price widen / no-match broaden (no LLM);
     if the gate still fails and LLM+tool budgets remain, ONE replan runs with
     the tool observation as plain context (no chain-of-thought), then a final
     execute. No unbounded loop: MAX_AGENT_LLM_CALLS / MAX_AGENT_TOOL_CALLS
     are enforced before every planner and tool call.
  8. RESPOND: deterministic grounded rendering (semantic cards via
     facade._offer_card_blocks) or the cascade tool's own grounded text.
     NO freeform LLM answer.

The trace only exposes structured decisions -- never chain-of-thought.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import agent.state as S
from agent.grounding import (ground_search_args, grounded_category,
                             grounded_product)
from agent.planner import PlanParseError, build_plan_prompt, call_planner
from agent.reference import corroborate_references, resolve_reference
from agent.state import AgentState
from core.config import AGENT_MODEL, MAX_AGENT_LLM_CALLS, MAX_AGENT_TOOL_CALLS

_MAX_HISTORY = 8


def _defaults_default():
    return 0


class AgentMetrics:
    """Timing/counting facts for the turn report and manual run."""

    def __init__(self):
        self.llm_calls = 0
        self.tool_calls = 0
        self.phases_ms = {}

    def add(self, phase: str, seconds: float):
        self.phases_ms[phase] = round(seconds * 1000.0, 1)


class AgentEngine:
    """Stage-2 agent. Create one instance per process and call
    answer_stream(query, reply_lang, ...) once per turn, exactly like the
    RagEngine it wraps."""

    def __init__(self, facade: Any, registry: Any,
                 planner: Callable | None = None,
                 plan_prompt_builder: Callable | None = None):
        self._facade = facade
        self._registry = registry
        self._planner = planner or call_planner
        self._plan_prompt_builder = plan_prompt_builder or build_plan_prompt
        self._client = self._new_client()

    def _new_client(self):
        try:
            from ollama import Client
            return Client(host="http://localhost:11434")
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # Public entry (mirrors rag_engine.answer_stream signature)
    # ------------------------------------------------------------------ #
    def answer_stream(self, query: str, reply_lang: str | None = None,
                      history: list | None = None,
                      recent_offers: list | None = None,
                      user_id: int | None = None,
                      identity: str | None = None,
                      user_name: str | None = None,
                      skip_transform: bool = False,
                      _budget=None,
                      progress=None,
                      **kwargs):
        params = self._turn_params(query, reply_lang, history or [],
                                   recent_offers or [],
                                   user_id, identity, user_name)
        out = self._run_turn(params, progress=progress)
        yield out["answer"]
        yield {"kind": "agent_turn_report", "data": out["report"],
               "evidence": out.get("evidence") or []}

    # ------------------------------------------------------------------ #
    # Turn parameterisation
    # ------------------------------------------------------------------ #
    def _turn_params(self, query, reply_lang, history, recent_offers,
                     user_id, identity, user_name) -> dict:
        facade = self._facade
        raw_query = str(query or "")
        query = facade._sanitize_user_query(raw_query)
        detected = facade.classify_intent_robust(query) \
            if hasattr(facade, "classify_intent_robust") else "other"
        lang = reply_lang or (facade.detect_lang(query)
                              if hasattr(facade, "detect_lang") else "en")

        conversation = ""
        for item in history[-_MAX_HISTORY:]:
            role, content = item.get("role"), str(item.get("content", ""))
            if content:
                conversation += f"{role}: {content[:300]}\n"
        conversation = conversation.strip()

        identity_note = "known customer" if identity else "public (anonymous)"

        raw_offers = (recent_offers or [])[-8:]
        # Retrieval gate: a deterministic, inspectable decision made ONCE per
        # turn, BEFORE any catalog call. Greetings/thanks/smalltalk and
        # general-knowledge questions must never trigger offer retrieval.
        retrieval_allowed, retrieval_reason = _retrieval_allowed(
            query, detected, _looks_like_explicit_browse(query))
        return {
            "query": query,
            "reply_lang": lang,
            "detected_intent": detected,
            "retrieval_allowed": (retrieval_allowed, retrieval_reason),
            "conversation": conversation,
            "references": self._extract_references(history),
            "recent_offers": [
                {k: o.get("metadata", {}).get(k) for k in ("id", "merchant", "source")}
                for o in raw_offers
            ],
            "recent_offers_full": list(raw_offers),
            "personal_subjects": self._personal_subjects(user_id),
            "identity": identity_note,
            "user_id": user_id,
        }

    def _extract_references(self, history) -> list:
        phrases = [
            "هالكوبون", "هالخصم", "ها الكوبون", "هذا الكوبون", "هذا الخصم",
            "هذا العرض", "هاي الكوبون", "this offer", "this coupon",
            "this discount", "اللي حكيت عنه", "الي ذكرته", "اللي قلته",
        ]
        found = []
        for item in (history or []):
            low = str(item.get("content", "")).lower()
            for p in phrases:
                if p.lower() in low and p not in found:
                    found.append(p)
        return found

    def _personal_subjects(self, user_id) -> list:
        if not user_id:
            return []
        hook = getattr(self._facade, "_personal_subjects", None)
        if hook is None:
            return []
        try:
            return hook(user_id)
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ #
    # Turn run
    # ------------------------------------------------------------------ #
    def _run_turn(self, params: dict, progress=None) -> dict:
        query, lang = params["query"], params["reply_lang"]
        state = AgentState(messages=params["conversation"])
        m = AgentMetrics()
        state.metrics["lang"] = lang
        state.metrics["intent_candidate"] = params["detected_intent"]
        state.metrics["retrieval_allowed"] = params["retrieval_allowed"][0]
        state.metrics["retrieval_gate_reason"] = params["retrieval_allowed"][1]
        state.metrics["budget_llm_calls"] = MAX_AGENT_LLM_CALLS
        state.metrics["budget_tool_calls"] = MAX_AGENT_TOOL_CALLS

        # 0. DETERMINISTIC SAFETY GATE ------------------------------------
        if progress is not None:
            progress("greeting")
        early = self._safety_gate(params)
        if early is not None:
            kind, summary, text = early
            state.trace_step(kind, summary)
            state.trace_step(S.TRACE_KIND_RESPONSE, "deterministic reply",
                             reason_code=summary.split(";")[0])
            state.final_response = text[lang]
            state.next_action = S.DONE
            state.completion = True
            self._last_evidence = []
            return self._finalize(
                {"answer": text[lang], "report": {}}, state, m)

        # 1. UNDERSTAND + PLAN --------------------------------------------
        if progress is not None:
            progress("understanding")
        t0 = time.perf_counter()
        prompt = self._plan_prompt_builder(
            query, params,
            tool_names=self._registry.names(),
            tool_docs=self._registry.description_block())
        try:
            decision = self._planner(
                client=self._client,
                model=AGENT_MODEL,
                prompt=prompt,
                tools=self._registry.names(),
                params=params,
                num_predict=900)
        except PlanParseError as e:
            m.llm_calls += 1
            state.trace_step(S.TRACE_KIND_DECISION, "planner output unusable",
                             reason_code="plan_parse_error", detail=str(e))
            return self._finish_fallback(params, state,
                                         fallback_reason="plan_parse_error")
        except Exception as e:  # noqa: BLE001 -- planner must fail safely
            state.trace_step(S.TRACE_KIND_DECISION, "planner unavailable",
                             reason_code="planner_error", detail=str(e)[:300])
            return self._finish_fallback(params, state,
                                         fallback_reason="planner_error")
        m.llm_calls += 1
        plan = decision["plan"]
        ms = (time.perf_counter() - t0) * 1000.0
        m.add("planning", time.perf_counter() - t0)
        state.metrics["planning_ms"] = round(ms, 1)
        if progress is not None:
            progress("plan")
        state.goal = str(plan.get("goal") or "")[:280]
        state.intent_note = str(plan.get("intent") or "")
        state.entities = plan.get("entities") or {}
        state.references = plan.get("references") or []
        state.known_information = plan.get("known_information") or []
        state.missing_information = str(plan.get("missing_information") or "")
        state.trace_step(
            S.TRACE_KIND_PLAN,
            f"plan: tool={plan.get('tool')} next_action={plan.get('next_action')} "
            f"goal={state.goal[:120]!r}",
            reason_code=plan.get("intent") or "other",
            detail=_json_dump({k: plan.get(k) for k in
                               ("entities", "constraints", "missing_information")
                               if plan.get(k)})[:400])

        # FAQ-topic override (deterministic, no second router): when the
        # existing FAQ topic router fires on THIS message (e.g. "مش عايز
        # عروض، عايز اعرف سياسة الاسترجاع"), the turn is a policy/how-to
        # question, not an offer search -- the plan is rewritten to
        # retrieve_faq BEFORE the direct-respond / clarification / execute
        # gates can claim the query as a failed offer lookup. It only fires
        # when a FAQ-topic signal exists (_route_faq_topic is the router) and
        # the planner picked anything but retrieve_faq.
        if self._facade_faq_topic(params.get("query") or ""):
            if plan.get("tool") != "retrieve_faq" and "retrieve_faq" in self._registry.names():
                plan["tool"] = "retrieve_faq"
                args = dict(plan.get("args") or {})
                if args.get("query") is None:
                    args["query"] = params.get("query") or ""
                plan["args"] = args
                plan["intent"] = "faq"
                plan["next_action"] = "plan"
                state.metrics["planned_tool"] = "retrieve_faq"
                state.trace_step(
                    S.TRACE_KIND_DECISION,
                    "faq-topic router matched; rerouting plan to retrieve_faq",
                    reason_code="faq_topic_override",
                    detail=params.get("query", "")[:200])

        # direct-action plan types that need no tool run
        if plan.get("next_action") in ("respond_one", "respond_trace", "respond_empty"):
            self._last_evidence = []
            return self._finalize(self._turn_respond(params, state, plan), state, m)

        # clarification (agentic behavior #5): ask when genuinely missing
        if self._needs_clarification(plan, params, state):
            state.trace_step(
                S.TRACE_KIND_DECISION,
                "clarification: missing_information cannot be resolved "
                "from entities/references/session context",
                reason_code="clarification",
                detail=plan.get("missing_information") or "")
            state.metrics["clarification"] = True
            answer = self._clarify(plan, params)
            state.final_response = answer
            state.next_action = "clarify"
            state.completion = True
            self._last_evidence = []
            return self._finalize(
                {"answer": answer, "report": {}}, state, m)

        # 2. EXECUTE -------------------------------------------------------
        if progress is not None:
            progress("execute")
        out = self._execute(plan, params, state, m)
        if out.get("kind") == "try_replan":
            if (MAX_AGENT_LLM_CALLS - m.llm_calls <= 0
                    or MAX_AGENT_TOOL_CALLS - state.tool_calls <= 0):
                state.trace_step(S.TRACE_KIND_DECISION,
                                 "budget exhausted; skipping replan",
                                 reason_code="replan_budget")
                out = self._finish_fallback(params, state,
                                           fallback_reason="replan_budget")
            else:
                if progress is not None:
                    progress("replan")
                plan2 = self._replan(params, state, m, out.get("observation", ""))
                if plan2 is None:
                    out = self._finish_fallback(params, state,
                                               fallback_reason="replan_unusable")
                else:
                    out = self._execute(plan2, params, state, m, replan=True)
        self._last_evidence = list(state.evidence or [])
        return self._finalize(out, state, m)

    def _finalize(self, out, state, m) -> dict:
        """Stamp the live call counters onto the report before it is returned."""
        state.metrics["llm_calls"] = m.llm_calls
        state.metrics["tool_calls"] = state.tool_calls
        state.metrics["evidence_ids"] = _offer_ids(state.evidence)
        out["evidence"] = list(state.evidence or [])
        out["report"] = state.snapshot()
        return out

    def _status_text(self, lang: str, code: str) -> str:
        """Humanized status line for the streaming UI, so a planning-heavy
        turn shows progress on the frontend instead of looking hung."""
        en = {
            "greeting": "greeting…",
            "understanding": "understanding your request…",
            "plan": "planning the answer…",
            "execute": "searching offers…",
            "replan": "refining the search…",
            "respond": "putting the answer together…",
        }
        ar = {
            "greeting": "بقرا سؤالك…",
            "understanding": "بفهم طلبك…",
            "plan": "بخطط للإجابة…",
            "execute": "ببحث عن العروض…",
            "replan": "بظبط نتايج البحث…",
            "respond": "بجهز الإجابة…",
        }
        table = ar if lang == "ar" else en
        return table.get(code, code)

    def _execute(self, plan, params, state, m=None, replan=False) -> dict:
        if state.tool_calls >= MAX_AGENT_TOOL_CALLS:
            state.trace_step(S.TRACE_KIND_DECISION, "tool budget exhausted",
                             reason_code="tool_budget")
            return self._finish_fallback(params, state,
                                         fallback_reason="tool_budget")

        tool = plan.get("tool")
        if tool != "none" and tool not in self._registry.names():
            tool = "search_offers"  # planner named nothing safe -> semantic fallback tool

        constraints = plan.get("constraints") or {}
        want_exclude = constraints.get("exclude") or []
        if isinstance(want_exclude, (str, int)):
            want_exclude = [want_exclude]
        want_price = _coerce_span(constraints.get("price_range"))
        target = _clamp(int(constraints.get("limit") or 6), 1, 12)

        args = dict(plan.get("args") or {})
        args.setdefault("limit", target)
        if want_price and "price_range" not in args:
            args["price_range"] = want_price
        if want_exclude and args.get("exclude") is None and tool == "search_offers":
            args["exclude"] = [str(x) for x in want_exclude]
        if args.get("query") is None and params.get("query"):
            args["query"] = params["query"]

        # 2b. ENTITY GROUNDING (deterministic) ----------------------------
        # Every entity/price filter the planner proposed must be corroborated
        # by the user's own words (via FacetedCatalog resolvers + literal
        # numbers in the message). Unconfirmed filters are dropped from the
        # tool call and recorded in the trace -- never passed through.
        if tool in _GROUNDABLE_TOOLS:
            args, grounding = self._ground_search_args(args, params, state)
            state.grounding = grounding.get("grounded") or {}
            state.metrics["grounding_dropped"] = grounding.get("dropped") or []
            state.metrics["grounding_merged"] = grounding.get("merged") or []
            for note in grounding.get("dropped") or []:
                state.trace_step(S.TRACE_KIND_TOOL, note,
                                 reason_code="unconfirmed_entity_dropped",
                                 detail=note)
            for note in grounding.get("merged") or []:
                state.trace_step(S.TRACE_KIND_TOOL, note,
                                 reason_code="corroborated_entity_merged",
                                 detail=note)
            if grounding.get("dropped"):
                state.trace_step(S.TRACE_KIND_DECISION, "grounding pass applied",
                                 reason_code="unconfirmed_entity_dropped",
                                 detail=_json_dump(grounding)[:400])

        # 2c. REFERENCE PASS (deterministic, stored state only) -----------
        # Corroborate every follow-up claim against the actual last-shown
        # offers. A reference that resolves reroutes the turn; a reference that
        # the user's words do not support is dropped and reported.
        tool, args, ref_ms = self._reference_pass(tool, args, plan, params, state)
        if ref_ms is not None and m is not None:
            m.add("reference", ref_ms)
        if ref_ms is not None:
            state.metrics["reference_ms"] = round(ref_ms * 1000.0, 1)
        if m is None:
            m = AgentMetrics()

        tool_ctx = self._tool_context(params, state)
        # Stage 4 (measurement): record the FINAL executed constraints (what
        # grounding + the reference pass actually produced) so the fallback can
        # later measure drift against what the user was promised.
        state.metrics["planned_tool"] = tool
        state.metrics["planned_price"] = args.get("price_range")
        state.metrics["planned_exclude"] = list(args.get("exclude") or [])
        state.metrics["planned_limit"] = int(args.get("limit") or 6)

        # RETRIEVAL GATE (deterministic, decided once per turn): a non-retrieval
        # turn (greeting/thanks/smalltalk/general-knowledge) must NOT invoke the
        # catalog -- zero offer cards. The gate is at the single chokepoint all
        # catalog calls run through, so the fallback paths in
        # _handle_no_matches / _handle_gate_failure / _finish_fallback cannot
        # leak offers either.
        allowed, reason = params.get("retrieval_allowed") or (True, None)
        if not allowed and tool in _CATALOG_RETRIEVAL_TOOLS:
            return self._no_retrieval_reply(params, state, reason)

        result = self._registry.call(tool, tool_ctx, args)
        state.observations.append(result.summary)
        state.plan.append({"tool": tool, "args": args,
                           "note": "executed step"})
        state.metrics["tool_calls"] = tool_ctx.state.tool_calls

        if result.error:
            state.trace_step(S.TRACE_KIND_TOOL, result.summary,
                             reason_code="tool_error", detail=result.error)
            return self._finish_fallback(params, state,
                                         fallback_reason="tool_error")

        obtained = result.items or []
        state.add_evidence(obtained)
        state.metrics.setdefault("steps", []).append({
            "tool": tool,
            "ids": _offer_ids(obtained),
            "limit": int(args.get("limit") or 6),
            "replan": bool(replan),
        })
        state.trace_step(S.TRACE_KIND_TOOL,
                         f"{result.summary} ({len(obtained)} docs)",
                         reason_code=(result.note or {}).get("provenance") or "tool")

        note = result.note or {}
        if note.get("kind") == "no_matches" or (
                note.get("kind") in ("no_user", "personal_disabled", "not_personal",
                                     "no_match") and not obtained):
            return self._handle_no_matches(params, state, tool_ctx, replan)

        # 3. EVIDENCE GATE --------------------------------------------------
        ok_gate, why = self._evidence_gate(tool, args, obtained, state)
        if not ok_gate:
            return self._handle_gate_failure(params, state, tool, args,
                                             tool_ctx, why, replan)

        # 4. RENDER ---------------------------------------------------------
        return self._render_grounded(tool, result, params, state)

    # ------------------------------------------------------------------ #
    # Deterministic entity grounding
    # ------------------------------------------------------------------ #
    def _ground_search_args(self, args, params, state):
        """Drop planner filters that the user's own words do not corroborate.

        Cross-checks every proposed merchant / category / product against
        FacetedCatalog.resolve_* on the normalized user text, and keeps a
        price bound only when it equals a number literally present in the
        message. Unconfirmed filters never reach the tool; they are reported
        in the trace as "unconfirmed entity dropped: <value>". Corroborated
        entities the planner omitted are merged back into the args (traced as
        "corroborated entity merged: <key>=<value>").
        """
        faceted = getattr(self._facade, "faceted", None)
        if faceted is None:
            return args, {"dropped": [], "grounded": {}}
        q = params.get("query") or ""
        return ground_search_args(args, q, faceted)

    def _facade_faq_topic(self, query: str):
        """Answer from the existing deterministic FAQ router: True-ish when the
        user's own words match a FAQ/policy topic (refund policy, payment
        method, order status, about-the-company). Uses the SAME `_route_faq_topic`
        router the RetrieveFaqTool consults, so the routing decision is made by
        the existing FAQ router -- never a new one and never an LLM."""
        facade = self._facade
        route_fn = getattr(facade, "_route_faq_topic", None)
        if route_fn is None:
            return None
        norm = getattr(facade, "normalize_arabizi_and_arabic", None)
        nq = norm(query) if callable(norm) else query
        try:
            return route_fn(query, nq) or None
        except Exception:  # noqa: BLE001 -- a router miss must not crash a turn
            return None

    def _message_corroborates_facet(self, params) -> bool:
        """True when the user's own words name a category or product that the
        catalog resolver corroborates. Same resolvers grounding uses, so a
        corroborated-but-planner-omitted entity is enough to proceed to the
        tool call instead of firing a needless clarification."""
        faceted = getattr(self._facade, "faceted", None)
        if faceted is None:
            return False
        q = params.get("query") or ""
        if not q.strip():
            return False
        try:
            return bool(grounded_category(q, faceted) or grounded_product(q, faceted))
        except Exception:  # noqa: BLE001 -- a resolver miss must not crash a turn
            return False

    # ------------------------------------------------------------------ #
    # Reference resolution (Stage 3, deterministic, stored state only)
    # ------------------------------------------------------------------ #
    def _reference_pass(self, tool, args, plan, params, state):
        """Corroborate follow-up claims against the ACTUAL last-shown offers.

        `recent_offers_full` is the stored state the renderer used (the same
        list memory.SessionMemory.recent() / the old cascade's
        _resolve_followup_targets operated on) -- never a freeform reading of
        the conversation. A reference that deterministically resolves reroutes
        the tool call; a reference the user's own words do not support is
        dropped and traced.

        Returns (tool, args, reference_ms) -- reference_ms is None when the
        pass didn't run anything measurable.
        """
        t0 = time.perf_counter()
        stored = params.get("recent_offers_full") or []
        faceted = getattr(self._facade, "faceted", None)
        resolved = resolve_reference(params.get("query") or "", stored, faceted)
        state.resolved_references = {
            "kind": resolved["kind"],
            "verdict": resolved["verdict"],
            "intent": resolved["intent"],
            "reason_code": resolved["reason_code"],
            "target_ids": resolved["target_ids"],
            "anchor_merchant": resolved["anchor_merchant"],
            "price_direction": resolved.get("price_direction"),
            "anchor_price": resolved.get("anchor_price"),
        }

        _, dropped = corroborate_references(
            plan.get("references") or [], params.get("query") or "", resolved)
        state.metrics["reference_dropped"] = dropped
        for item in dropped:
            state.trace_step(
                S.TRACE_KIND_TOOL,
                f"unconfirmed reference dropped: {item}",
                reason_code="unconfirmed_reference_dropped", detail=item)

        kind = resolved["kind"]
        if kind == "none":
            return tool, args, time.perf_counter() - t0

        targets = resolved.get("targets") or []
        target_ids = resolved.get("target_ids") or []
        if not targets or not target_ids:
            state.trace_step(S.TRACE_KIND_DECISION,
                             f"reference pass produced no targets "
                             f"({resolved.get('reason_code')}); treating as new topic",
                             reason_code="reference:new_topic")
            return tool, args, time.perf_counter() - t0

        if kind in ("compare",) and self._registry.has("compare_offers"):
            state.trace_step(
                S.TRACE_KIND_DECISION,
                f"reference '{kind}' -> compare_offers "
                f"(targets={target_ids})",
                reason_code=f"reference:{kind}",
                detail=_json_dump({"reference": kind, "target_ids": target_ids})[:300])
            return "compare_offers", {"offer_ids": target_ids, "limit": 6}, \
                time.perf_counter() - t0

        if kind in ("ordinal", "same_offer"):
            reas = resolved.get("reason_code") or ""
            if kind == "same_offer" and reas in ("merchant", "merchant_compare"):
                # Merchant-naming that matches a SHOWN merchant re-pins the
                # search on that merchant (old cascade path), it does NOT
                # collapse to a single get_offer -- "tell me about KFC again"
                # must keep showing KFC's offers, not one cached card.
                anchor = str(_metadata_of(targets[0]).get("merchant") or "").strip()
                if anchor not in (state.grounding.get("anchored") or []):
                    state.grounding.setdefault("anchored", []).append(anchor)
                args = dict(args)
                args["merchant"] = anchor
                state.trace_step(
                    S.TRACE_KIND_DECISION,
                    f"reference '{kind}' pins merchant {anchor!r} "
                    f"(stored state)",
                    reason_code="reference:merchant_pin",
                    detail=_json_dump({"merchant": anchor})[:240])
                return tool, args, time.perf_counter() - t0
            direction = resolved.get("price_direction")
            if kind == "same_offer" and reas == "cross_ref_price" and direction:
                # "العرض ده غالي، هاتلي حاجة شبهه بس أرخص": the user wants an
                # ALTERNATIVE relative to the shown offer. The planner's search
                # stays (with whatever grounding kept); the STORED merchant and
                # the STORED anchor price become the search's anchor + bound.
                # This runs AFTER grounding, so grounding can never drop it.
                anchor = str(_metadata_of(targets[0]).get("merchant") or "").strip()
                anchor_price = _to_float(_metadata_of(targets[0]).get("price"))
                args = dict(args)
                if anchor:
                    if anchor not in (state.grounding.get("anchored") or []):
                        state.grounding.setdefault("anchored", []).append(anchor)
                    args["merchant"] = anchor
                if "price_range" not in args and anchor_price is not None:
                    args["price_range"] = ([0.0, anchor_price]
                                           if direction == "below"
                                           else [anchor_price, None])
                args["exclude"] = _dedup_ids(list(args.get("exclude") or [])
                                             + target_ids)
                state.trace_step(
                    S.TRACE_KIND_DECISION,
                    f"reference 'same_offer' anchors merchant {anchor!r} + "
                    f"{'upper' if direction == 'below' else 'lower'} price "
                    f"bound from STORED state; excluding shown ids",
                    reason_code="reference:anchor_price",
                    detail=_json_dump({"merchant": anchor,
                                       "anchor_price": anchor_price,
                                       "direction": direction,
                                       "exclude": target_ids})[:300])
                return tool, args, time.perf_counter() - t0
            if self._registry.has("get_offer"):
                state.trace_step(
                    S.TRACE_KIND_DECISION,
                    f"reference '{kind}' -> get_offer:{target_ids[0]}",
                    reason_code=f"reference:{kind}",
                    detail=_json_dump({"reference": kind, "target_id": target_ids[0]})[:240])
                return "get_offer", {
                    "id": target_ids[0],
                    "source": _metadata_of(targets[0]).get("source") or "offer",
                }, time.perf_counter() - t0
            return tool, args, time.perf_counter() - t0

        if kind == "other_offer":
            anchor = resolved.get("anchor_merchant")
            exclude = _dedup_ids(list(args.get("exclude") or []) + target_ids)
            if anchor:
                # grounded in STORED state, so grounding must keep it
                if anchor not in (state.grounding.get("anchored") or []):
                    state.grounding.setdefault("anchored", []).append(anchor)
                args = dict(args)
                args["merchant"] = anchor
                args["exclude"] = exclude
                note = (f"reference 'other_offer' anchored merchant "
                        f"{anchor!r} from stored state; excluding shown ids")
                state.trace_step(
                    S.TRACE_KIND_DECISION, note,
                    reason_code="reference:other_merchant",
                    detail=_json_dump({"anchor": anchor, "exclude": exclude})[:300])
                return tool, args, time.perf_counter() - t0
            state.trace_step(
                S.TRACE_KIND_DECISION,
                "reference 'other_offer' without anchor; excluding shown "
                f"ids={target_ids}",
                reason_code="reference:other_offer",
                detail=_json_dump({"exclude": exclude})[:240])
            return tool, dict(args, exclude=exclude), time.perf_counter() - t0

        return tool, args, time.perf_counter() - t0

    def _replan(self, params, state, m, observation) -> dict | None:
        """ONE bounded replan on evidence-gate failure: a second planner call
        whose context includes the tool observation as plain context (no
        chain-of-thought). None when the LLM budget is exhausted or the
        planner cannot produce a usable plan."""
        if m is None or MAX_AGENT_LLM_CALLS - m.llm_calls <= 0:
            return None
        t0 = time.perf_counter()
        replan_params = dict(params)
        if observation:
            replan_params["conversation"] = (
                (params.get("conversation") or "")
                + f"\n[tool_observation] {str(observation)[:200]}").strip()
        prompt = self._plan_prompt_builder(
            str(params.get("query") or ""), replan_params,
            tool_names=self._registry.names(),
            tool_docs=self._registry.description_block())
        try:
            decision = self._planner(
                client=self._client, model=AGENT_MODEL, prompt=prompt,
                tools=self._registry.names(), params=replan_params,
                num_predict=900)
        except PlanParseError as e:
            m.llm_calls += 1
            state.trace_step(S.TRACE_KIND_DECISION, "replan output unusable",
                             reason_code="plan_parse_error", detail=str(e))
            return None
        except Exception as e:  # noqa: BLE001 -- replan must fail safely
            state.trace_step(S.TRACE_KIND_DECISION, "replan unavailable",
                             reason_code="planner_error", detail=str(e)[:300])
            return None
        m.llm_calls += 1
        plan = decision["plan"]
        m.add("replan", time.perf_counter() - t0)
        state.metrics["replan_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        state.metrics["replan_count"] = int(state.metrics.get("replan_count") or 0) + 1
        state.trace_step(
            S.TRACE_KIND_PLAN,
            f"replan: tool={plan.get('tool')} goal={str(plan.get('goal') or '')[:120]!r}",
            reason_code="replan",
            detail=_json_dump({k: plan.get(k) for k in
                               ("entities", "constraints", "missing_information")
                               if plan.get(k)})[:300])
        return plan

    # ------------------------------------------------------------------ #
    # Evidence gate, relaxation, fallback
    # ------------------------------------------------------------------ #
    def _evidence_gate(self, tool, args, obtained, state) -> tuple:
        if not obtained:
            return False, "empty_result"
        if tool == "search_offers" and not any(
                o.get("metadata", {}).get("source") == "offer" for o in obtained):
            return False, "no_offer_items"
        price = args.get("price_range")
        if price and not _respects_price(obtained, price):
            return False, "price_not_satisfied"
        return True, "ok"

    def _handle_no_matches(self, params, state, tool_ctx, replan=False):
        state.trace_step(S.TRACE_KIND_DECISION, "tool found no exact matches",
                         reason_code="no_matches")
        if not replan:
            return {"kind": "try_replan", "observation": "no matches found"}
        if state.tool_calls >= MAX_AGENT_TOOL_CALLS:
            return self._finish_fallback(params, state,
                                         fallback_reason="tool_budget")
        # deterministic one-shot broadening, no LLM call
        retry = self._registry.call(
            "search_offers", tool_ctx,
            {"query": params["query"], "limit": 6})
        state.observations.append(retry.summary)
        state.trace_step(S.TRACE_KIND_TOOL, f"broaden: {retry.summary}",
                         reason_code="relax_no_match")
        if retry.ok and retry.items:
            state.add_evidence(retry.items)
            if self._evidence_gate("search_offers", {}, retry.items, state)[0]:
                return self._render_grounded("search_offers", retry, params, state)
        return self._finish_fallback(params, state,
                                     fallback_reason="relax_failed")

    def _handle_gate_failure(self, params, state, tool, args, tool_ctx, why,
                             replan=False):
        state.trace_step(S.TRACE_KIND_DECISION,
                         f"evidence gate failed ({why})",
                         reason_code=why)
        if not replan:
            return {"kind": "try_replan", "observation": f"evidence gate failed ({why})"}
        if state.tool_calls >= MAX_AGENT_TOOL_CALLS:
            return self._finish_fallback(params, state,
                                         fallback_reason="tool_budget")
        if why == "price_not_satisfied" and args.get("price_range"):
            wider = _widen(_coerce_span(args["price_range"]), factor=0.35)
            retry_args = dict(args)
            retry_args["price_range"] = wider
            retry = self._registry.call(tool, tool_ctx, retry_args)
            state.observations.append(retry.summary)
            state.trace_step(S.TRACE_KIND_TOOL, f"relax: {retry.summary}",
                             reason_code="price_relax")
            if retry.ok and retry.items and \
                    self._evidence_gate(tool, retry_args, retry.items, state)[0]:
                return self._render_grounded(tool, retry, params, state)
        return self._finish_fallback(params, state,
                                     fallback_reason="relax_failed")

    # -- clarification (agentic behavior #5: ask when genuinely missing) ----

    def _needs_clarification(self, plan: dict, params: dict, state) -> bool:
        """Deterministic gate: does this plan need a clarification question
        instead of a tool call?

        Fires when:
          (a) the planner explicitly returned next_action="clarify", OR
          (b) the plan flagged missing_information AND nothing else can fill
              the gap, OR
          (c) the intent is a SPECIFIC, ungrounded search/comparison
              ("offer_lookup", "personal", "comparison") with no merchant /
              category / product, no reference claims, and no session offers
              to default from -- even when the planner optimistically wrote
              missing_information="" (the 3B model does this) it still cannot
              fill the required fields for its own inferred goal.

        Does NOT fire when:
          - entities already cover a path (merchant, category, product), OR
          - the user's own words corroborate a category/product the planner
            omitted (grounding will merge it into the tool args), OR
          - a reference pass already resolved targets, OR
          - known_information from session context fills the gap, OR
          - recent session offers exist (the reference layer hasn't run yet
            but will resolve a merchant/product/category from stored state).
        Broad-browse catalog intents ("catalog" / "faq" / "quality" /
        "greeting" / "closing") never fire this gate -- that IS a reasonable
        default for "show me what you have".

        This is a safety-net AFTER the planner; it adds friction ONLY when the
        structured decision genuinely can't proceed -- not a new default path.
        """
        if plan.get("next_action") == "clarify":
            return True
        missing = str(plan.get("missing_information") or "").strip()
        has_entity = bool(
            (plan.get("entities") or {}).get("merchant")
            or (plan.get("entities") or {}).get("category")
            or (plan.get("entities") or {}).get("product")
        )
        has_reference = bool(plan.get("references") or state.references)
        has_known = bool(plan.get("known_information") or state.known_information)
        if has_entity or has_reference or has_known:
            return False
        # The planner may omit a facet the user's words clearly name (e.g.
        # "pizza between 100-150" -> args carry only the price). Grounding
        # merges that corroborated product in _execute, so it is a real path:
        # do not ask the user for information their own message supplied.
        if self._message_corroborates_facet(params):
            return False
        recent = params.get("recent_offers_full") or []
        if recent:
            return False
        if missing:
            return True
        intent = str(plan.get("intent") or "")
        # NEW: a genuine "unclear" verdict (low-signal input: single char,
        # keyboard noise, unrecognized greeting, no act-on-able target). Never
        # proceeds to a catalog dump -- same clarification path as an
        # ungrounded specific search.
        if intent == "unclear":
            return True
        # NEW: planner fell back to a bare catalog/other default on input with
        # NO corroborated entity AND NO offer topic signal. "w"/"asdf"/"إيه
        # ده" are not browse requests; dump-of-everything is only legitimate
        # for explicit browse phrasing or a named offer topic.
        if intent in ("catalog", "other", "") and not _has_offer_topic_signal(
                str(params.get("query") or "")):
            return True
        # "offer_lookup"/"comparison" are specific, ungrounded searches that
        # need a merchant/category; "personal" and "catalog" have their own
        # default paths (session user's coupons / broad browse) and must not
        # clarify.
        return intent in ("offer_lookup", "comparison")

    def _clarify(self, plan: dict, params: dict) -> str:
        """Render a short, deterministic clarification question (zero tools).

        The decision (WHETHER to ask) reuses the planner's missing_information;
        the message itself is clean, non-technical, and language-localised.
        """
        reply = params.get("reply_lang") or "ar"
        if reply == "en":
            return ("_I'd like to find the right offer for you. "
                    "_What are you looking for? A merchant (e.g. KFC, Amazon) "
                    "or a category (e.g. electronics, fashion, food)?_")
        return ("_عشان ألاقي أحسن عرض ليك. قصدك متجر معين "
                "(زي كينتاكي أو أمازون) ولا فئة "
                "(زي إلكترونيات أو ملابس أو أكل)؟_")

    def _finish_fallback(self, params, state, fallback_reason=None) -> dict:
        """Deterministic fallback when planning/execution could not produce a
        grounded answer within budget.

        `fallback_reason` says WHY we are here (replan_budget / tool_budget /
        plan_parse_error / planner_error / replan_unusable / relax_failed).
        Stage 4: every call records the drift between the last EXECUTED plan's
        hard constraints and what the fallback returned, and the budget-
        exhaustion case (replan_budget ONLY) also surfaces a SHORT,
        non-technical note so a hard user constraint is never silently
        dropped -- never a trace dump.
        """
        answer = _NO_ANSWER_FOUND.get(params["reply_lang"], _NO_ANSWER_FOUND["en"])
        fallback_items = []
        tool_ctx = self._tool_context(params, state)
        allowed, reason = params.get("retrieval_allowed") or (True, None)
        if not allowed:
            return self._no_retrieval_reply(params, state, reason)
        if state.tool_calls >= MAX_AGENT_TOOL_CALLS:
            state.trace_step(S.TRACE_KIND_DECISION, "tool budget exhausted; no fallback",
                             reason_code="tool_budget")
        else:
            result = self._registry.call("search_offers", tool_ctx,
                                         {"query": params["query"], "limit": 6})
            state.observations.append(result.summary)
            state.trace_step(S.TRACE_KIND_TOOL, f"fallback: {result.summary}",
                             reason_code="semantic_fallback")
            if result.ok and result.items:
                state.add_evidence(result.items)
                fallback_items = list(result.items)
                answer = self._render_cards(fallback_items, params["reply_lang"])
                state.trace_step(S.TRACE_KIND_OBSERVATION, "fallback evidence ok")
            else:
                state.trace_step(S.TRACE_KIND_OBSERVATION, "no evidence",
                                 reason_code="no_matches")
        drift = _fallback_drift(state.metrics, fallback_items)
        state.metrics["fallback_reason"] = fallback_reason
        state.metrics["fallback_drift"] = drift
        state.metrics["fallback_note"] = False
        if (fallback_reason == "replan_budget" and drift and answer
                != _NO_ANSWER_FOUND.get(params["reply_lang"],
                                        _NO_ANSWER_FOUND["en"])):
            answer = answer.rstrip() + "\n" + _BUDGET_NOTE.get(
                params["reply_lang"], _BUDGET_NOTE["en"])
            state.metrics["fallback_note"] = True
        state.decision_note = "fallback: grounded in retrieval evidence"
        state.trace_step(S.TRACE_KIND_RESPONSE, "fallback reply",
                         reason_code=fallback_reason, detail=answer[:200])
        state.final_response = answer
        state.next_action = S.DONE
        state.completion = True
        return {"answer": answer, "report": state.snapshot(),
                "evidence": list(state.evidence or [])}

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _render_grounded(self, tool, result, params, state) -> dict:
        answer = self._render_answer(tool, result, params["reply_lang"])
        state.decision_note = "answer grounded ONLY in tool evidence"
        state.trace_step(S.TRACE_KIND_OBSERVATION, "evidence satisfied gate")
        state.trace_step(S.TRACE_KIND_RESPONSE, "grounded reply (no LLM)",
                         detail=answer[:200])
        state.final_response = answer
        state.next_action = S.DONE
        state.completion = True
        return {"answer": answer, "report": state.snapshot()}

    def _render_answer(self, tool, result, lang) -> str:
        note = result.note or {}
        if isinstance(note, dict) and note.get("text"):
            return str(note["text"])
        items = result.items or []
        if tool == "get_offer":
            if not items:
                return _NOTOOL_RESPONSE.get(lang, _NOTOOL_RESPONSE["en"])
            return self._render_single(items[0], lang)
        offers = [o for o in items if o.get("metadata", {}).get("source") == "offer"]
        if not offers:
            return _NOTOOL_RESPONSE.get(lang, _NOTOOL_RESPONSE["en"])
        return self._render_cards(offers, lang)

    def _render_cards(self, offers, lang) -> str:
        try:
            cards = self._facade._offer_card_blocks(offers, lang)
        except Exception:  # noqa: BLE001
            cards = []
        if not cards:
            return _NO_ANSWER_FOUND.get(lang, _NO_ANSWER_FOUND["en"])
        header = "Found matching offers:" if lang == "en" else "عثرت على عروض مناسبة:"
        grid = "\n".join(cards)
        note = _evidence_note_text(lang)
        return f"{header}\n{grid}\n\n{note}"

    def _render_single(self, item, lang) -> str:
        try:
            card = self._facade._offer_card_blocks([item], lang)[0]
        except Exception:  # noqa: BLE001
            return _NO_ANSWER_FOUND.get(lang, _NO_ANSWER_FOUND["en"])
        header = "Here is the offer you asked about:" if lang == "en" \
            else "هذا هو العرض الذي سألت عنه:"
        return f"{header}\n{card}"

    def _turn_respond(self, params, state, plan) -> dict:
        """Respond using already-known context; no tool run needed."""
        answer = _NOTOOL_RESPONSE.get(params["reply_lang"], _NOTOOL_RESPONSE["en"])
        state.trace_step(S.TRACE_KIND_RESPONSE, "direct reply, no tool")
        state.final_response = answer
        state.next_action = S.DONE
        state.completion = True
        return {"answer": answer, "report": state.snapshot()}

    def _no_retrieval_reply(self, params, state, reason) -> dict:
        """Zero-card deterministic reply for a turn the retrieval gate blocks.

        Greetings/smalltalk/thanks get the greeting affordance; anything else
        (general-knowledge, out-of-scope) gets the out-of-scope notice. Never
        touches the catalog, so the answer can never contain offer cards."""
        key = "greeting" if reason == "greeting" else "out_of_scope"
        table = _GREETING_REPLY if key == "greeting" else _OUT_OF_SCOPE_REPLY
        answer = table.get(params["reply_lang"], table["en"])
        state.trace_step(
            S.TRACE_KIND_DECISION,
            f"retrieval gate blocked catalog tool "
            f"(retrieval_allowed={params.get('retrieval_allowed')!r})",
            reason_code=f"retrieval_blocked:{reason or 'not_allowed'}")
        state.trace_step(S.TRACE_KIND_RESPONSE, "deterministic reply, zero cards",
                         reason_code=f"retrieval_blocked:{reason or 'not_allowed'}")
        state.decision_note = f"retrieval gate: {reason or 'blocked'} (no catalog call)"
        state.final_response = answer
        state.next_action = S.DONE
        state.completion = True
        return {"answer": answer, "report": state.snapshot(), "evidence": []}

    # ------------------------------------------------------------------ #
    # Safety gate & context
    # ------------------------------------------------------------------ #
    def _safety_gate(self, params) -> tuple | None:
        query = params["query"]
        low = query.lower()
        if _any_substring(low, _CLOSING):
            return (S.TRACE_KIND_DECISION, "closing; polite farewell",
                    _FAREWELL)
        if self._facade._looks_like_greeting(query):
            return (S.TRACE_KIND_DECISION, "greeting; brief greeting + affordance",
                    _GREETING_REPLY)
        if self._facade._looks_like_gibberish(query):
            return (S.TRACE_KIND_DECISION, "gibberish rejected", _GIBBERISH_REPLY)
        if self._facade._looks_like_injection_attempt(query):
            return (S.TRACE_KIND_DECISION, "injection attempt rejected", _INJECTION_REPLY)
        return None

    def _tool_context(self, params, state):
        facade = self._facade
        ctx_query = params["query"]
        return _ToolContextStandin(
            facade=facade,
            reply_lang=params["reply_lang"],
            query=ctx_query,
            normalized_query=getattr(facade, "normalize_arabizi_and_arabic", None) and
            facade.normalize_arabizi_and_arabic(ctx_query) or None,
            history=params["conversation"].split("\n") if params["conversation"] else [],
            recent_offers=params["recent_offers_full"],
            user_id=params["user_id"],
            state=state,
        )


class _ToolContextStandin:
    """Lightweight ToolContext substitute to avoid importing agent.tools at
    engine import time (keeps stage-2 imports cheap)."""

    def __init__(self, facade, reply_lang, query, normalized_query,
                 history, recent_offers, user_id, state):
        self.facade = facade
        self.reply_lang = reply_lang
        self.query = query
        self.normalized_query = normalized_query
        self.history = history
        self.recent_offers = recent_offers
        self.user_id = user_id
        self.state = state


# ------------------------------------------------------------------ #
# Module helpers
# ------------------------------------------------------------------ #
def _json_dump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(obj)


def _any_substring(text: str, words) -> bool:
    return any(w in text for w in words)


# NEW: explicit-browse phrasing that makes a bare "catalog" default plan
# legitimate. A plan with intent "catalog"/"other" but NO corroborated
# entity and NO browse phrasing is low-signal input ("w", "asdf", ambiguous
# small talk) that must be clarified -- never silently answered with a full
# offer listing. Phrasing is matched on lower-cased text; Arabic needs no
# case folding.
_EXPLICIT_BROWSE_MARKERS = (
    "show me", "show all", "show offers", "what do you have",
    "what you have", "all offers", "all the offers", "list offers",
    "list of offers", "browse", "what's available", "what is available",
    "any offers", "do you have", "have any offers", "show me offers",
    "what offers", "see offers", "view offers", "show everything",
    # Arabic
    "وريني", "عرضولي", "بينولي", "عايز اشوف", "دلوقتي العروض",
    "كل العروض", "العروض كلها", "اشوف العروض", "شوف العروض",
    "عندك ايه", "عندك اي", "عندكم ايه", "مفيش حاجة", "أنت ليك عروض",
    "ما عندك", "ديني", "أريني",
    # Franco
    "waryni", "bynoly", "kol el 3orood", "3orood", "shouf",
)


def _looks_like_explicit_browse(query: str) -> bool:
    """True if the user explicitly asked to SEE the offers (broad browse),
    as opposed to an ambiguous/underspecified message."""
    q = (query or "").strip().lower()
    if not q:
        return False
    return _any_substring(q, _EXPLICIT_BROWSE_MARKERS)


# NEW: minimal topical signal. When the planner defaults an unspecific input
# to "catalog" but the user never even said the word offers ("w", "asdf",
# "إيه ده"), it is insufficient-signal input, not a browse. Explicit browse
# phrasing OR a bare offer topic word ("عروض", "offers", "deals") keeps the
# catalog default.
_OFFER_TOPIC_MARKERS = ("عروض", "العروض", "offers", "deals", "عروضا")


def _has_offer_topic_signal(query: str) -> bool:
    q = (query or "").strip().lower()
    return _any_substring(q, _OFFER_TOPIC_MARKERS) or _looks_like_explicit_browse(q)


def _coerce_span(value) -> list | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = [value.get("min"), value.get("max")]
    if isinstance(value, (int, float)):
        value = [None, value]
    if not isinstance(value, (list, tuple)):
        return None
    vals = []
    for v in value[:2]:
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            vals.append(None)
    lo, hi = (vals + [None] * 2)[:2]
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return [lo, hi]


def _widen(span, factor: float) -> list | None:
    if span is None:
        return None
    lo, hi = span
    width = (hi - lo) if (lo is not None and hi is not None) else abs(hi or lo or 1)
    expand = max(width * factor, 1.0)
    return [(lo - expand) if lo is not None else None,
            (hi + expand) if hi is not None else None]


def _respects_price(items, span) -> bool:
    lo, hi = span
    offer_prices = []
    for it in items:
        meta = it.get("metadata", {})
        if meta.get("source") != "offer":
            continue
        raw = meta.get("price")
        try:
            price = float(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        offer_prices.append(price)
    if not offer_prices:
        return False
    return all(
        (lo is None or p >= lo) and (hi is None or p <= hi)
        for p in offer_prices)


_GROUNDABLE_TOOLS = ("search_offers", "compare_offers", "superlative_offer",
                     "catalog")


def _clamp(value, lo, hi) -> int:
    return max(lo, min(hi, value))


def _metadata_of(entry) -> dict:
    return entry.get("metadata", {}) if isinstance(entry, dict) else {}


def _to_float(value):
    try:
        return float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _dedup_ids(values) -> list:
    seen = set()
    out = []
    for v in values or []:
        key = str(v or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _offer_ids(items) -> list:
    """Ordered, deduped metadata ids of OFFER items in `items`."""
    seen, out = set(), []
    for it in (items or []):
        mid = it.get("metadata", {}) if isinstance(it, dict) else {}
        oid = str(mid.get("id") or "")
        if mid.get("source") == "offer" and oid and oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def _fallback_drift(metrics: dict, fallback_items: list) -> list:
    """Concrete user-visible differences between the last EXECUTED plan's hard
    constraints and what the fallback actually returned. The planned values are
    the FINAL executed args (post-grounding + post-reference), so a bound the
    fallback silently dropped is visible here. Returns a list of short keys;
    empty means the fallback faithfully reflected the executed plan."""
    out = []
    planned_price = metrics.get("planned_price")
    if planned_price and fallback_items \
            and not _respects_price(fallback_items, planned_price):
        out.append("price_not_satisfied")
    exclude = metrics.get("planned_exclude") or []
    if exclude:
        got = set(_offer_ids(fallback_items))
        if got & {str(x).strip() for x in exclude}:
            out.append("exclude_breached")
    tool = metrics.get("planned_tool")
    if tool and tool not in ("search_offers", "none", None):
        out.append(f"tool_switched({tool})")
    limit = int(metrics.get("planned_limit") or 0)
    if limit and len(_offer_ids(fallback_items)) < limit:
        out.append("limit_undershot")
    if not fallback_items:
        out.append("empty")
    return out


# Stage 4: the ONLY note the engine adds about an incomplete turn. Short and
# non-technical, surfaced via the answer text (same style as the tool note
# rendering path) and ONLY when the budget-exhaustion fallback drifted from
# the constraints the executed plan promised.
_BUDGET_NOTE = {
    "en": "_Note: this may not fully match what you asked for; here are the "
          "closest real offers right now._",
    "ar": "_ملاحظة: قد لا يطابق هذا طلبك تماماً — وهذه أقرب العروض الحقيقية المتاحة حالياً._",
}


def _evidence_note_text(lang: str) -> str:
    return ("_All offers above are real, ranked from the catalog evidence_"
            if lang == "en" else
            "_جميع العروض أعلاه حقيقية ومرتبة حسب دليل الكتالوج_")


def _build_engine(facade, registry, planner=None, prompt_builder=None):
    return AgentEngine(facade, registry, planner or call_planner,
                       prompt_builder or build_plan_prompt)


_CLOSING = (
    "bye", "goodbye", "السلام عليكم", "مع السلامة", "وداعا", "تصبح على خير",
    "خلاص", "مش محتاج", "لا داعي", "شكراً", "شكرا",
)
_FAREWELL = {
    "en": "Goodbye! If you want to compare any of these offers, just say the word.",
    "ar": "إلى اللقاء! إذا أردت مقارنة أي من هذه العروض، فقط قل لي.",
}
_GREETING_REPLY = {
    "en": "Hello! I can help you find real Waffarha offers, compare them, and "
          "track coupons. Try asking: what offers does KFC have right now?",
    "ar": "مرحباً! يمكنني مساعدتك في العثور على عروض وفرها الحقيقية ومقارنتها "
          "وتتبع الكوبونات. جرب أن تسأل: ما هي عروض KFC الحالية؟",
}
_GIBBERISH_REPLY = {
    "en": "I didn't catch that. Could you rephrase? I can find real Waffarha "
          "offers, compare them, and track coupons.",
    "ar": "لم أفهم. هل يمكنك إعادة الصياغة؟ يمكنني إيجاد عروض وفرها الحقيقية "
          "ومقارنتها وتتبع الكوبونات.",
}
_INJECTION_REPLY = {
    "en": "I can't help with that request.",
    "ar": "لا أستطيع المساعدة في هذا الطلب.",
}
_NOTOOL_RESPONSE = {
    "en": "I couldn't find offers for that yet. Try a merchant name or a "
          "category (e.g. 'offers from KFC').",
    "ar": "لم أجد عروضاً لذلك بعد. جرب اسم تاجر أو فئة (مثال: «عروض من KFC»).",
}
_NO_ANSWER_FOUND = {
    "en": "I couldn't find matching offers right now. Try a different merchant "
          "or category.",
    "ar": "لم أجد عروضاً مطابقة الآن. جرب تاجراً أو فئة مختلفة.",
}
# Deterministic non-retrieval reply used when the retrieval gate blocks a
# catalog turn. Words here are generic enough that the same phrasing is safe
# for smalltalk, thanks, or an out-of-scope general-knowledge question.
_OUT_OF_SCOPE_REPLY = {
    "en": "I'm the Waffarha assistant, so I can help with Waffarha offers, "
          "orders, cashback, and account questions -- but that's outside what "
          "I can answer here.",
    "ar": "أنا مساعد وفرها، فبستطيع مساعدتك في عروض وفرها وطلباتك والكاش باك "
          "وأسئلة الحساب — لكن هذا خارج ما أستطيع الإجابة عنه هنا.",
}


# ------------------------------------------------------------------ #
# Retrieval gate (deterministic, decided once per turn).
#
# Greetings / thanks / smalltalk / general-knowledge turns must return ZERO
# offer cards. The router verdict (classify_intent_robust via the facade) is
# the primary signal; the phrase list below is a cheap local complement so the
# engine package stays import-light (core.rag_engine is heavy and must not be
# imported by agent.engine). Same purpose as core's _GREETING_PHRASES, kept in
# sync here deliberately -- a normalized exact-match, never a second router.
# ------------------------------------------------------------------ #
_GREETING_OR_THANKS_PHRASES = (
    # English
    "hi", "hello", "hey", "hey there", "hello there", "hi there", "yo",
    "good morning", "good afternoon", "good evening", "good night",
    "thanks", "thank you", "thanks a lot", "thank you very much", "thanks so much",
    "cheers", "appreciated", "ok thanks", "okay thanks", "thank u",
    # Arabic (normalized: no diacritics, no hamza variants)
    "مرحبا", "اهلا", "اهلا بك", "اهلا بيك", "هاي", "هلا",
    "صباح الخير", "مساء الخير", "السلام عليكم",
    "شكرا", "شكرا لك", "شكرا ليك", "شكرا ليكم", "شكرا لكم",
    "تسلم", "تسلملي", "متشكر", "متشكرين",
    "اهلا وسهلا", "اهلا وسهلا بيك", "مرحبا بك", "مرحبا بيك",
    "بونا صباح الخير", "بوني صباح الخير", "بوني صباحو", "مرحبا شلونك",
    "شلونك", "ازيك", "كيفك", "كيف حالك",
    # Franco / Arabizi
    "salam", "salam 3aleikom", "ahlan", "ahlan wa sahlan", "ahlan bik",
    "marhaba", "mar7aba", "sabah el kheir", "sabah el 5er", "ezayak",
    "kefak", "kifak", "shlonak", "shukran", "thanks", "thx",
)


def _strip_diacritics_and_noise(text: str) -> str:
    """Collapse Arabic diacritics, Hamza variants and boundary punctuation so
    the greeting list can be matched on normalized tokens (same intent as
    core.rag_engine._looks_like_greeting, implemented locally to keep agent.engine
    import-light)."""
    s = (text or "").strip().lower()
    s = re.sub(r"[\u064B-\u0652\u06D6-\u06ED]", "", s)      # Arabic diacritics
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    return re.sub(r"[^\w\s]", " ", s).strip().lower()


def _greeting_or_thanks(query: str) -> bool:
    """True when the message is a PURE greeting/thanks/smalltalk line -- no
    offer topic signal -- so the catalog must not be touched. Explicit-browse
    or offer-word phrasing is checked separately by the caller."""
    cleaned = _strip_diacritics_and_noise(query)
    if not cleaned:
        return False
    if cleaned in _GREETING_OR_THANKS_PHRASES:
        return True
    # A greeting core (two-word greeting like "صباح الخير") can follow a
    # dialectal prefix ("بونا صباح الخير"). Match the greeting core at either
    # the start or the end of the message, but never mid-question.
    if any(g in cleaned for g in ("صباح الخير", "مساء الخير", "اهلا وسهلا",
                                  "شلونك", "كيفك", "ازيك", "thank you",
                                  "thanks a lot", "سلام عليكم")):
        return True
    return False


# Deterministic identity / general-knowledge question detector. These are
# NEVER offer lookups -- no catalog answer exists for them -- so retrieval must
# be skipped even though the router's keyword fallback would say OFFER_LOOKUP.
_OUT_OF_SCOPE_QUESTION_RE = re.compile(
    r"(who is|who are|tell me about|what is the capital of|"
    r"من هو|من هي|من هم|مين هو|مين هي|مين هم|ما هي عاصمة|"
    r"من هو رئيس|مين هو رئيس|من هو صاحب|مين هو صاحب)",
    re.IGNORECASE,
)


def _out_of_scope_question(query: str) -> bool:
    """True for general-knowledge / identity questions with no offer content."""
    if not (query or "").strip():
        return False
    return bool(_OUT_OF_SCOPE_QUESTION_RE.search(query))


# Router verdicts from classify_intent_robust that by themselves mean the
# turn must never touch the offer catalog.
_RETRIEVAL_BLOCKED_INTENTS = ("greeting", "out_of_scope", "prompt_injection",
                              "closing")


def _retrieval_allowed(query: str, detected_intent: str,
                       explicit_browse: bool = False) -> tuple[bool, str | None]:
    """Decide ONCE per turn, before any catalog tool may run, whether this
    turn may retrieve offers. Returns (allowed, reason) where reason is the
    deterministic gate code when blocked (None when allowed). Pure function of
    the router verdict + the message text -- no LLM, no second router.

    Order matters: a real offer ask (explicit-browse phrasing OR an offer
    topic word like "عروض"/"offers") wins over a greeting opener, so "hello,
    show me offers" still retrieves. Pure greetings/thanks/smalltalk and
    identity/general-knowledge questions are blocked."""
    intent = str(detected_intent or "").strip().lower()
    if _has_offer_topic_signal(query):
        return True, None
    if intent in _RETRIEVAL_BLOCKED_INTENTS:
        return False, intent
    if _out_of_scope_question(query):
        return False, "out_of_scope"
    if _greeting_or_thanks(query):
        return False, "greeting"
    return True, None


# Catalog tools that surface offer cards; a non-retrieval turn must never
# invoke any of them. CatalogTool(scope=personal) is intentionally NOT here --
# it is the account/coupon service, not offer retrieval.
_CATALOG_RETRIEVAL_TOOLS = ("search_offers", "catalog", "superlative_offer",
                            "compare_offers")