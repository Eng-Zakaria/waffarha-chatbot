"""Ex-F: reference/session-state transitions (multi-turn) + Ex-A-supp taxonomy gaps.

Drives the LIVE server pair (:8000 RAG engine, :8001 Agent engine) with
persistent session_ids so server-side SessionMemory (recent_offers) is the
authoritative state. History is deliberately sent EMPTY for follow-ups so the
experiment isolates whether *server-memory* binding works, then S4 sends a fake
history to test client-vs-server authority.

Investigation-only.
"""
import json
import sys
import time
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8")

PORT_RAG = 8000
PORT_AGENT = 8001


def call(port, q, session_id, history=None, timeout=200):
    body = json.dumps({"query": q, "history": history or [],
                       "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{port}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    ms = round((time.time() - t0) * 1000)
    cards = [{"id": c.get("id"), "merchant": c.get("merchant"),
              "price": c.get("price")} for c in data.get("offers", [])[:6]]
    return {"ms": ms, "answer": (data.get("answer") or "")[:140].replace("\n", " "),
            "offers": cards}


def _trim(s, n=110):
    return str(s or "").replace("\n", " ")[:n]


# --- Ex-A-supp: taxonomy gaps not yet probed (fresh session each) ------------
TAXONOMY_GAPS = [
    ("discovery", "\u0641\u064a\u0646 \u0627\u0644\u0639\u0631\u0648\u0636 \u0627\u0644\u0645\u062a\u0627\u062d\u0629\u061f"),  # فين العروض المتاحة؟
    ("ambiguity", "\u0639\u0627\u064a\u0632 \u062d\u0627\u062c\u0629 \u062d\u0644\u0648\u0629"),
    ("ambiguity", "\u0639\u0627\u064a\u0632 \u062d\u0627\u062c\u0629 \u0631\u062e\u064a\u0635\u0629"),
    ("ambiguity", "\u0639\u0627\u064a\u0632 \u0639\u0631\u0636 \u0643\u0648\u064a\u0633"),
    ("ambiguity", "\u0639\u0627\u064a\u0632 \u062d\u0627\u062c\u0629 \u0645\u0646 100 \u0644\u0650150"),
    ("mixed_conditional", "\u0639\u0646\u062f\u0643\u0645 \u0628\u064a\u062a\u0632\u0627 \u0645\u0646 100 \u0644\u0650150\u061f \u0648\u0644\u0648 \u0645\u0641\u064a\u0634\u060c \u0634\u0648\u0641\u0644\u064a \u0634\u0627\u0648\u0631\u0645\u0627"),
    ("mixed_conditional", "\u0639\u0627\u064a\u0632 KFC \u0648\u0644\u0648 \u0645\u0641\u064a\u0634 \u0623\u064a \u0645\u0637\u0639\u0645 \u062a\u0627\u0646\u064a"),
    ("multi_step", "is that still available?"),
    ("multi_step", "\u0644\u0633\u0647 \u0645\u0648\u062c\u0648\u062f\u061f"),
    ("multi_step", "what about the second one?"),
    ("product", "\u0639\u0627\u064a\u0632 \u0645\u0648\u0628\u0627\u064a\u0644\u0627\u062a"),
    ("gk", "\u0645\u0627 \u0647\u064a Python\u061f"),
]


def run_taxonomy_gaps():
    print("=== EX-A-SUPP ===", flush=True)
    for cat, q in TAXONOMY_GAPS:
        sid = f"gap-{uuid.uuid4().hex[:8]}"
        row = {"cat": cat, "q": q}
        for label, port in (("rag", PORT_RAG), ("agent", PORT_AGENT)):
            try:
                row[label] = call(port, q, sid)
            except Exception as e:  # noqa: BLE001
                row[label] = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(row, ensure_ascii=False), flush=True)


# --- Ex-F: session state transitions -----------------------------------------
S1 = [
    ("t1", "\u0648\u0631\u064a\u0646\u064a \u0639\u0631\u0648\u0636 \u0628\u064a\u062a\u0632\u0627"),
    ("t2", "\u0623\u0631\u062e\u0635 \u0648\u0627\u062d\u062f \u0645\u0646\u0647\u0645"),
    ("t3", "\u0648\u062f\u0647 \u0628\u0643\u0627\u0645\u061f"),
    ("t4", "\u0648\u0627\u0644\u062a\u0627\u0646\u064a\u061f"),
    ("t5", "\u0641\u064a \u062d\u0627\u062c\u0629 \u0634\u0628\u0647\u0647\u061f"),
    ("t6", "\u0631\u062c\u0639\u0644\u064a \u0627\u0644\u0623\u0648\u0644"),
]

S2 = [
    ("t1", "\u0623\u0631\u062e\u0635 \u0648\u0627\u062d\u062f \u0645\u0646\u0647\u0645"),
    ("t2", "\u0627\u0644\u062a\u0627\u0646\u064a"),
]

S3 = [
    ("t1", "\u0639\u0631\u0648\u0636 \u0628\u064a\u062a\u0632\u0627"),
    ("t2", "\u0639\u0631\u0648\u0636 \u0643\u0634\u0631\u064a"),
    ("t3", "\u0641\u064a \u062d\u0627\u062c\u0629 \u0634\u0628\u0647\u0647\u061f"),
]

S5 = [
    ("t1", "\u0639\u0631\u0648\u0636 \u0627\u0643\u0644"),
    ("t2", "\u0627\u0644\u062a\u0627\u0646\u064a \u0628\u0643\u0627\u0645\u061f"),
]

FAKE_HISTORY = [{"role": "user", "content": "\u0639\u0631\u0648\u0636 \u0633\u062a\u0627\u0631\u0628\u0643\u0633"},
                {"role": "assistant", "content": "\u062e\u062f\u062a \u0639\u0631\u0648\u0636 \u0633\u062a\u0627\u0631\u0628\u0643\u0633"}]


def run_scenario(name, turns, history_per_turn=None):
    print(f"=== EX-F {name} ===", flush=True)
    for port_label, port in (("rag", PORT_RAG), ("agent", PORT_AGENT)):
        sid = f"{name.lower()}-{uuid.uuid4().hex[:6]}"
        for step, q in turns:
            hist = []
            if history_per_turn is not None and history_per_turn.get(step):
                hist = FAKE_HISTORY
            try:
                row = call(port, q, sid, history=hist)
                print(json.dumps({"engine": port_label, "step": step,
                                  "q": q, **row}, ensure_ascii=False), flush=True)
            except Exception as e:  # noqa: BLE001
                print(json.dumps({"engine": port_label, "step": step,
                                  "q": q, "error": f"{type(e).__name__}: {e}"},
                                 ensure_ascii=False), flush=True)
        # one extra probe: what does server memory now hold? via a neutral ping
        try:
            row = call(port, "\u0639\u0631\u0648\u0636 \u0643\u0634\u0631\u064a", sid)
            print(json.dumps({"engine": port_label, "step": "ping", "q": "\u0639\u0631\u0648\u0636 \u0643\u0634\u0631\u064a",
                              **row}, ensure_ascii=False), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"engine": port_label, "step": "ping",
                              "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    # healthcheck
    try:
        call(PORT_RAG, "\u0623\u0647\u0644\u0627", "sanity-rag")
        print("RAG OK", flush=True)
    except Exception as e:  # noqa: BLE001
        print("RAG DOWN:", e, flush=True)
    try:
        call(PORT_AGENT, "\u0623\u0647\u0644\u0627", "sanity-agent")
        print("AGENT OK", flush=True)
    except Exception as e:  # noqa: BLE001
        print("AGENT DOWN:", e, flush=True)

    run_taxonomy_gaps()
    run_scenario("S1", S1)
    run_scenario("S2", S2)
    run_scenario("S3", S3)
    run_scenario("S5", S5)
    # S4: fake injected history for the reference query, empty real memory
    print("=== EX-F S4 ===", flush=True)
    for port_label, port in (("rag", PORT_RAG), ("agent", PORT_AGENT)):
        sid = f"s4-{uuid.uuid4().hex[:6]}"
        try:
            row = call(port, "\u0623\u0631\u062e\u0635 \u0648\u0627\u062d\u062f \u0645\u0646\u0647\u0645",
                       sid, history=FAKE_HISTORY)
            print(json.dumps({"engine": port_label, "step": "fake_history_ref",
                              "q": "\u0623\u0631\u062e\u0635 \u0648\u0627\u062d\u062f \u0645\u0646\u0647\u0645",
                              **row}, ensure_ascii=False), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"engine": port_label, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False), flush=True)
    print("DONE", flush=True)