"""Turn tracer for the Waffarha chatbot verification audit.

Runs any query against BOTH engines in-process, constructed exactly the way
the servers construct them (shared singletons from core.app, same config,
same data/index), and emits one JSON record per (engine, query turn).

Tracing is done purely by wrapping existing helper functions inside this
harness (unittest-free manual monkeypatching, always restored). No
application file is modified, so behavior neutrality holds by construction:
the wrapped callables are the originals.

Usage:
    venv/Scripts/python eval/turn_trace.py --query "عايز عروض بيتزا" --out eval/traces.jsonl
    venv/Scripts/python eval/turn_trace.py --cases eval/acceptance_cases.json --out eval/traces.jsonl
    venv/Scripts/python eval/turn_trace.py --cases eval/acceptance_cases.json --engines cascade --out out.jsonl --now 2026-09-21
"""
import argparse
import copy
import datetime as _dt
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _reply_lang_of(text):
    return "ar" if _ARABIC_RE.search(text or "") else "en"


def _parse_expiry(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    s = str(value).strip()[:10]
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Recorder: installs wrappers, restores everything afterwards.
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.events = []      # chronological {t, kind, name, decisive, detail}
        self.llm_calls = []   # {phase, model}
        self.phase = "other"
        self._patched = []    # (obj, attr, original)
        self._t0 = time.perf_counter()

    def _stamp(self):
        return round((time.perf_counter() - self._t0) * 1000.0, 1)

    def log(self, kind, name, decisive=False, detail=None):
        self.events.append({"t": self._stamp(), "kind": kind,
                            "name": name, "decisive": bool(decisive),
                            "detail": detail})

    def patch_obj(self, obj, attr, wrapper_factory):
        orig = getattr(obj, attr)
        wrapped = wrapper_factory(orig, self)
        setattr(obj, attr, wrapped)
        self._patched.append((obj, attr, orig))

    def patch_module(self, module, name, wrapper_factory):
        self.patch_obj(module, name, wrapper_factory)

    def restore(self):
        for obj, attr, orig in reversed(self._patched):
            setattr(obj, attr, orig)
        self._patched = []


def _wrap_predicate(name, gate, decisive_when_true=True, phase=None):
    def factory(orig, rec):
        def inner(*a, **k):
            prev = rec.phase
            if phase:
                rec.phase = phase
            try:
                out = orig(*a, **k)
            except Exception:
                raise
            finally:
                if phase:
                    rec.phase = prev
            dec = bool(out) if decisive_when_true else False
            if dec or name in ("_classify_intent", "_smart_source_intent",
                               "classify_intent_robust", "_judge_is_faq"):
                rec.log("intent" if name in ("_classify_intent", "_smart_source_intent",
                                             "classify_intent_robust", "_judge_is_faq")
                        else "gate", gate if dec else name,
                        decisive=dec, detail=str(out)[:200])
            return out
        return inner
    return factory


def _wrap_answer_method(gate, phase=None):
    def factory(orig, rec):
        def inner(*a, **k):
            prev = rec.phase
            if phase:
                rec.phase = phase
            try:
                out = orig(*a, **k)
            finally:
                if phase:
                    rec.phase = prev
            decisive = out is not None and out is not False and out != "" \
                and out != [] and out != {}
            rec.log("gate", gate, decisive=decisive,
                    detail=(str(out)[:120] if out else None))
            return out
        return inner
    return factory


# Inline closing replies in RagEngine.answer_stream (not a wrapped helper).
# Source: the _closing_reply dict built per turn ("You're welcome! ..." /
# "عافاك! ..."). Matched on exact answer text by the harness only.
CLOSING_REPLIES = {
    "You're welcome! Let me know if there's anything else.",
    "عافاك! لو محتاج أي حاجة تانية، أنا موجود",
}


# --------------------------------------------------------------------------
# Engine construction (same singletons the servers use).
# --------------------------------------------------------------------------
def build_engines():
    import core.app as appmod
    cascade = appmod.get_engine()
    agent = appmod.get_agent_engine()
    memory_store = appmod.get_memory_store()
    return appmod, cascade, agent, memory_store


def install_cascade_wrappers(rec, R, RagEngine, ollama_mod):
    preds = [
        ("_looks_like_greeting", "greeting", True, None),
        ("_looks_like_gibberish", "gibberish", True, None),
        ("_looks_like_injection_attempt", "injection", True, None),
        ("_looks_like_out_of_scope", "out-of-scope", True, None),
        ("check_out_of_scope_guardrail", "out-of-scope-guardrail", True, None),
        ("is_personal_query", "personal", True, None),
        ("is_catalog_query", "catalog", True, None),
        ("_unmatched_brand_mention", "unmatched-brand", True, None),
        ("_classify_intent", "_classify_intent", False, None),
        ("_smart_source_intent", "_smart_source_intent", False, None),
        ("classify_intent_robust", "classify_intent_robust", False, None),
        ("_judge_is_faq", "_judge_is_faq", False, "intent-judge"),
        ("judge_faq_vs_offer", "intent-judge-llm", False, "intent-judge"),
        ("extract_price_range", "price-range", True, None),
        ("_detect_multi_item", "multi-item", True, None),
        ("_route_faq_topic", "faq-topic-router", True, None),
    ]
    for attr, gate, dec, phase in preds:
        if hasattr(R, attr):
            rec.patch_module(R, attr, _wrap_predicate(attr, gate, dec, phase))
    methods = [
        ("_inactive_merchant_mention", "inactive-merchant"),
        ("_followup_anchored_answer", "followup-anchored"),
        ("_get_superlative_offer_answer", "superlative"),
        ("_faceted_answer", "faceted"),
        ("_faq_topic_answer", "faq-topic"),
        ("_explicit_validity_answer", "validity"),
        ("_get_faq_direct_answer", "direct-faq"),
        ("_get_stock_direct_answer", "direct-stock"),
        ("_get_comparison_answer", "comparison"),
        ("_llm_offer_intro", "offer-intro", "offer-intro"),
        ("_offer_card_blocks", "card-blocks", None),
        ("_context_is_relevant", "relevance-check", "relevance-check"),
        ("_resolve_followup_targets", "followup-resolve", "followup-classify"),
    ]
    for m in methods:
        attr, gate = m[0], m[1]
        phase = m[2] if len(m) > 2 else None
        if hasattr(RagEngine, attr):
            rec.patch_obj(RagEngine, attr, _wrap_answer_method(gate, phase))

    # retrieve: capture candidates
    orig_retrieve = RagEngine.retrieve
    def retrieve_wrapper(self, *a, **k):
        out = orig_retrieve(self, *a, **k)
        slim = []
        for d in (out or [])[:20]:
            m = d.get("metadata", {})
            slim.append({"embedding_score": d.get("embedding_score"),
                         "bonus_total": d.get("bonus_total"),
                         "combined_score": d.get("combined_score"),
                         "lexical_hits": d.get("lexical_hits"),
                         "source": m.get("source"), "id": m.get("id")})
        rec.log("retrieval", "retrieve", decisive=True,
                detail={"n": len(out or []), "top": slim[:10]})
        return out
    RagEngine.retrieve = retrieve_wrapper
    rec._patched.append((RagEngine, "retrieve", orig_retrieve))

    # Personal / catalog service handles
    try:
        from personal.personal_queries import PersonalQueryService
        rec.patch_obj(PersonalQueryService, "handle", _wrap_answer_method("personal-handle"))
    except Exception:
        pass
    try:
        from catalog.catalog_queries import CatalogQueryService
        rec.patch_obj(CatalogQueryService, "handle", _wrap_answer_method("catalog-handle"))
    except Exception:
        pass

    # LLM chat counting with phase attribution
    def chat_factory(orig, rec):
        def inner(self, *a, **k):
            model = k.get("model", "")
            rec.llm_calls.append({"phase": rec.phase, "model": model})
            return orig(self, *a, **k)
        return inner
    rec.patch_obj(ollama_mod.Client, "chat", chat_factory)

    # _judge_is_faq phase attribution is handled in the preds loop above.


def install_agent_wrappers(rec, engine):
    import agent.engine as E

    def planner_factory(orig, rec):
        def inner(*a, **k):
            prev = rec.phase
            rec.phase = "planner"
            try:
                out = orig(*a, **k)
            finally:
                rec.phase = prev
            plan = (out.get("plan") or {}) if isinstance(out, dict) else {}
            rec.log("agent-plan", "planner", decisive=True, detail={
                "tool": plan.get("tool"), "intent": plan.get("intent"),
                "next_action": plan.get("next_action"),
                "goal": str(plan.get("goal") or "")[:160],
                "entities": plan.get("entities") or {},
                "constraints": plan.get("constraints") or {},
                "missing_information": str(plan.get("missing_information") or "")[:160],
                "references": plan.get("references") or []})
            return out
        return inner
    rec.patch_obj(engine, "_planner", planner_factory)

    rec.patch_obj(type(engine), "_safety_gate", _wrap_answer_method("agent-safety"))
    rec.patch_obj(type(engine), "_needs_clarification", _wrap_answer_method("agent-clarify-check"))
    rec.patch_obj(type(engine), "_evidence_gate", _wrap_answer_method("agent-evidence-gate"))
    rec.patch_obj(type(engine), "_replan", _wrap_answer_method("agent-replan"))

    def fallback_factory(orig, rec):
        def inner(*a, **k):
            reason = None
            if len(a) >= 4:
                reason = a[3]
            reason = k.get("fallback_reason", reason)
            out = orig(*a, **k)
            rec.log("gate", "agent-fallback", decisive=True,
                    detail="fallback_reason=%s" % (reason,))
            return out
        return inner
    rec.patch_obj(type(engine), "_finish_fallback", fallback_factory)
    rec.patch_obj(type(engine), "_turn_params", _wrap_answer_method("agent-turn-params"))

    registry = engine._registry
    orig_call = registry.call
    def call_wrapper(name, ctx, args):
        prev = rec.phase
        rec.phase = "tool:%s" % name
        t0 = time.perf_counter()
        try:
            out = orig_call(name, ctx, args)
        finally:
            rec.phase = prev
        items = getattr(out, "items", None) or []
        rec.log("agent-tool", "tool:%s" % name, decisive=True, detail={
            "args": args, "ok": getattr(out, "ok", None),
            "n_items": len(items),
            "ms": round((time.perf_counter() - t0) * 1000.0, 1)})
        return out
    registry.call = call_wrapper
    rec._patched.append((registry, "call", orig_call))


# --------------------------------------------------------------------------
# Exit-gate derivation
# --------------------------------------------------------------------------
_CASCADE_GATE_ORDER = ["closing", "greeting", "gibberish", "injection",
                       "unmatched-brand", "inactive-merchant", "out-of-scope",
                       "out-of-scope-guardrail", "personal", "personal-handle",
                       "catalog", "catalog-handle", "followup-anchored",
                       "superlative", "faceted", "faq-topic", "direct-faq",
                       "direct-stock", "comparison"]


def derive_cascade_exit(rec, engine, answer):
    import core.rag_engine as _R
    a = (answer or "").strip()
    if a in CLOSING_REPLIES:
        return "closing"
    try:
        fb = set(_R.FALLBACK_MESSAGE.values())
    except Exception:
        fb = set()
    decisive = [e for e in rec.events if e["kind"] == "gate" and e["decisive"]]
    names = [e["name"] for e in decisive]
    if a in fb:
        # Refusal TEXT is shared by several gates; prefer a specifically
        # observed gate (out-of-scope etc.) over the floor inference.
        for n in reversed(names):
            if n in _CASCADE_GATE_ORDER:
                return n
        rel = [e for e in rec.events if e["name"] == "relevance-check"]
        if rel:
            return "refusal-relevance"
        return "refusal-strict-floor"
    for n in reversed(names):
        if n in _CASCADE_GATE_ORDER:
            return n
    card_ev = [e for e in rec.events if e["name"] == "card-blocks" and e["decisive"]]
    if card_ev:
        return "llm-cards-intro"
    gen_calls = [c for c in rec.llm_calls if c["phase"] in ("generation", "other")]
    if gen_calls:
        return "llm-full"
    if "closing" in names:
        return "closing"
    return names[-1] if names else ("empty" if not a else "unknown")


def derive_agent_exit(rec, engine):
    for e in reversed(rec.events):
        if e["kind"] == "agent-tool":
            return e["name"]
        if e["name"] == "agent-fallback" and e["decisive"]:
            d = e["detail"] or ""
            return "agent-fallback:%s" % (d.replace("fallback_reason=", "")[:60]
                                          if isinstance(d, str) else d)
        if e["name"] == "agent-clarify-check" and e["decisive"]:
            return "agent-clarify"
        if e["name"] == "agent-safety" and e["decisive"]:
            return "agent-safety"
    plans = [e for e in rec.events if e["kind"] == "agent-plan"]
    if plans:
        na = (plans[-1]["detail"] or {}).get("next_action")
        return "agent-direct:%s" % na
    return "unknown"


# --------------------------------------------------------------------------
# One traced turn
# --------------------------------------------------------------------------
def run_turn(engine_name, engine, query, session, recent_offers, history,
             user_id, reply_lang, now_date, topk):
    import core.rag_engine as R
    from core.rag_engine import RagEngine, detect_lang
    import ollama as ollama_mod

    rec = Recorder()
    if engine_name == "cascade":
        install_cascade_wrappers(rec, R, RagEngine, ollama_mod)
    else:
        install_agent_wrappers(rec, engine)
        # agent turns can also hit cascade retrieve/tools via facade; count LLM too
        def chat_factory(orig, rec):
            def inner(self, *a, **k):
                rec.llm_calls.append({"phase": rec.phase, "model": k.get("model", "")})
                return orig(self, *a, **k)
            return inner
        rec.patch_obj(ollama_mod.Client, "chat", chat_factory)

    t0 = time.perf_counter()
    try:
        if engine_name == "cascade":
            rec.phase = "generation"
            chunks = list(engine.answer_stream(query, history, recent_offers, user_id))
            answer = "".join(chunks)
            evidence = list(getattr(engine, "_last_retrieved", []) or [])
        else:
            pieces = list(engine.answer_stream(
                query, reply_lang=reply_lang, history=history,
                recent_offers=recent_offers, user_id=user_id,
                identity=("u%d" % user_id) if user_id else None))
            answer = ""
            turn_evidence = None
            for p in pieces:
                if isinstance(p, str):
                    answer = p
                elif isinstance(p, dict) and p.get("kind") == "agent_turn_report":
                    # Per-turn evidence from the report (always fresh). The
                    # engine's _last_evidence attribute is sticky: fallback
                    # paths never reset it, so reading it here would attribute
                    # the PREVIOUS turn's cards to this turn.
                    turn_evidence = p.get("evidence") or []
            evidence = (turn_evidence if turn_evidence is not None
                        else list(getattr(engine, "_last_evidence", []) or []))
    finally:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        rec.restore()

    if engine_name == "cascade":
        exit_gate = derive_cascade_exit(rec, engine, answer)
    else:
        exit_gate = derive_agent_exit(rec, engine)

    intents = {}
    for e in rec.events:
        if e["kind"] == "intent":
            intents[e["name"]] = e["detail"]
    for e in rec.events:
        if e["kind"] == "agent-plan":
            intents["planner_intent"] = (e["detail"] or {}).get("intent")
            intents["planner_tool"] = (e["detail"] or {}).get("tool")
    for e in rec.events:
        if e["name"] == "agent-turn-params" and e["decisive"]:
            pass
    # detected_intent hint lives in planner prompt params; capture via plan event is enough,
    # plus explicit facade call for the record:
    try:
        intents["detected_hint"] = engine._facade.classify_intent_robust(query) \
            if engine_name == "agent" else None
    except Exception:
        intents["detected_hint"] = None

    retr_ev = [e for e in rec.events if e["name"] == "retrieve"]
    tool_ev = [e for e in rec.events if e["kind"] == "agent-tool"]
    retrieval_ran = bool(retr_ev) or any(
        t["name"].split(":", 1)[1] in ("search_offers", "get_offer", "retrieve_faq",
                                       "compare_offers", "superlative_offer", "catalog")
        for t in tool_ev)
    topk_out = []
    if retr_ev:
        topk_out = (retr_ev[-1]["detail"] or {}).get("top", [])[:topk]

    tools = []
    for t in tool_ev:
        d = t["detail"] or {}
        tools.append({"tool": t["name"].split(":", 1)[1], "args": d.get("args"),
                      "ok": d.get("ok"), "n_items": d.get("n_items")})
    eg = [e for e in rec.events if e["name"] == "agent-evidence-gate"]
    evidence_gate = None
    if eg:
        import re as _re2
        m = _re2.match(r"\((True|False),\s*'([^']*)'", str(eg[-1]["detail"]))
        if m:
            evidence_gate = {"accepted": m.group(1) == "True", "reason": m.group(2)}
        else:
            evidence_gate = {"accepted": None, "reason": str(eg[-1]["detail"])}
    relax_replan = [ {"event": e["name"], "detail": e["detail"]}
                     for e in rec.events if e["name"] in ("agent-replan", "agent-fallback") ]

    cards = []
    expired = 0
    null_exp = 0
    for d in evidence:
        m = d.get("metadata", {})
        if m.get("source") != "offer":
            continue
        if len(cards) >= 5:
            break
        exp = _parse_expiry(m.get("expiry"))
        if exp is None:
            null_exp += 1
            flag = "null"
        elif exp < now_date:
            expired += 1
            flag = True
        else:
            flag = False
        cards.append({"id": m.get("id"), "title": m.get("title"),
                      "expiry": m.get("expiry"), "price": m.get("price"),
                      "expired_vs_now": flag})

    events = [{"t": e["t"], "kind": e["kind"], "name": e["name"],
               "decisive": e["decisive"],
               "detail": (None if e["name"] == "retrieve" else e["detail"])}
              for e in rec.events]

    return {
        "engine": engine_name,
        "query": query,
        "exit_gate": exit_gate,
        "events": events,
        "intents": intents,
        "retrieval_ran": retrieval_ran,
        "topk": topk_out,
        "tools": tools,
        "evidence_gate": evidence_gate,
        "relax_replan": relax_replan,
        "llm_calls": [{"n": len(rec.llm_calls),
                       "phases": sorted(set(c["phase"] for c in rec.llm_calls))},
                      *rec.llm_calls],
        "cards": cards,
        "expired_count": expired,
        "null_expiry_count": null_exp,
        "reply_lang": _reply_lang_of(answer),
        "query_lang": detect_lang(query),
        "latency_ms": latency_ms,
        "answer_chars": len(answer),
        "answer_prefix": answer[:300],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", default=[])
    ap.add_argument("--cases", default=None)
    ap.add_argument("--engines", default="both")
    ap.add_argument("--out", default="eval/traces.jsonl")
    ap.add_argument("--now", default=None)
    ap.add_argument("--session", default="audit")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--user-id", type=int, default=None)
    args = ap.parse_args()

    now_date = _dt.date.fromisoformat(args.now) if args.now else _dt.date.today()
    # Phase 1: pin the app's injectable freshness clock (REFERENCE_DATE, the
    # single source of truth read by core/freshness, faceted and the tools)
    # to the same date the harness uses for its own expiry math. setdefault:
    # an explicitly exported REFERENCE_DATE still wins.
    os.environ.setdefault("REFERENCE_DATE", now_date.isoformat())
    engines = ["cascade", "agent"] if args.engines == "both" else [args.engines]

    cases = []
    if args.cases:
        with open(args.cases, encoding="utf-8") as f:
            cases = json.load(f)
    for q in args.query:
        cases.append({"id": q[:40], "turns": [q], "session": args.session})

    from core import config
    from core.app import get_identity
    print("config: embedding=%s backend=%s memory=%s personal=%s identity=%s include_expired=%s" % (
        config.EMBEDDING_MODEL, config.VECTOR_STORE_BACKEND, config.MEMORY_BACKEND,
        config.PERSONAL_QUERIES_ENABLED, config.IDENTITY_BACKEND,
        config.INCLUDE_EXPIRED_OFFERS), flush=True)
    appmod, cascade, agent, memory_store = build_engines()
    print("engines ready (cascade=%s agent=%s memory=%s)" % (
        type(cascade).__name__, type(agent).__name__,
        type(memory_store).__name__), flush=True)

    if args.user_id is not None:
        user_id = args.user_id
    elif config.PERSONAL_QUERIES_ENABLED:
        try:
            user_id = get_identity().resolve(None)
        except Exception as e:
            print("identity resolve failed: %s" % e, flush=True)
            user_id = None
    else:
        user_id = None
    print("user_id=%s" % (user_id,), flush=True)

    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for ci, case in enumerate(cases):
            sid = "%s-%d" % (case.get("session", args.session), ci)
            history = []
            for ti, q in enumerate(case["turns"]):
                session = memory_store.get(sid)
                try:
                    recent = session.recent()
                except Exception:
                    recent = []
                for eng in engines:
                    engine = cascade if eng == "cascade" else agent
                    rec = run_turn(eng, engine, q, session, recent, history,
                                   user_id, None, now_date, args.topk)
                    rec.update({"case": case.get("id"), "turn": ti,
                                "session_id": sid, "user_id": user_id,
                                "memory_backend": config.MEMORY_BACKEND})
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    n += 1
                    qsafe = q.encode("ascii", "backslashreplace").decode()
                    line = "[%s/%s] exit=%s retr=%s cards=%d llm=%d ms=%s" % (
                        eng, qsafe[:40], rec["exit_gate"], rec["retrieval_ran"],
                        len(rec["cards"]), rec["llm_calls"][0]["n"],
                        rec["latency_ms"])
                    print(line.encode("ascii", "backslashreplace").decode(), flush=True)
                # server-like memory update from the cascade evidence
                try:
                    ev = list(getattr(cascade, "_last_retrieved", []) or [])[:3]
                    if ev:
                        session.remember(ev)
                    # answer text for turns memory: reuse last cascade answer
                    session.remember_turns(q, "")
                except Exception as e:
                    print("memory update failed: %s" % e, flush=True)
                history.append({"role": "user", "content": q})
    print("wrote %d records to %s" % (n, args.out), flush=True)


if __name__ == "__main__":
    main()
