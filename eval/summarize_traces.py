"""Summarize turn_trace JSONL into an ASCII-safe table + verdicts per case."""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
recs = [json.loads(l) for l in open(src, encoding="utf-8")]
by_case = {}
for r in recs:
    by_case.setdefault((r["case"], r["turn"]), []).append(r)

def short_exit(e):
    if e.startswith("agent-fallback:"):
        return "agent-fallback"
    return e

lines = []
for (case, turn), rs in by_case.items():
    for r in sorted(rs, key=lambda x: x["engine"]):
        tools = ",".join(t["tool"] for t in r["tools"]) or "-"
        lines.append(
            "%-22s t%d %-7s exit=%-22s retr=%-5s cards=%d exp=%d null=%d lang=%s/%s llm=%d(%s) tools=%s intents=%s" % (
                case, turn, r["engine"], short_exit(r["exit_gate"]),
                r["retrieval_ran"], len(r["cards"]), r["expired_count"],
                r["null_expiry_count"], r["query_lang"], r["reply_lang"],
                r["llm_calls"][0]["n"], ",".join(r["llm_calls"][0]["phases"]),
                tools,
                {k: (v[:40] if isinstance(v, str) else v)
                 for k, v in r["intents"].items() if v}))

# HARD verdicts per task spec
HARD = []
def verdict(case, eng, ok, why):
    HARD.append((case, eng, "PASS" if ok else "FAIL", why))

for (case, turn), rs in by_case.items():
    for r in sorted(rs, key=lambda x: x["engine"]):
        e, q = r["engine"], r["query"]
        nc, ret, ans = len(r["cards"]), r["retrieval_ran"], r["answer_prefix"]
        clos = ("شكر" in ans or "welcome" in ans.lower() or "anything else" in ans.lower())
        if case.startswith("greet") or case in ("thanks_basha", "gibberish"):
            verdict(case, e, nc == 0 and not ret,
                    "cards=%d retr=%s exit=%s" % (nc, ret, short_exit(r["exit_gate"])))
        elif case == "offer_pizza":
            verdict(case, e, nc >= 1 and r["expired_count"] == 0,
                    "cards=%d expired=%d" % (nc, r["expired_count"]))
        elif case == "offer_under100":
            import re as _re
            def _num(p):
                m = _re.search(r"\d+(\.\d+)?", str(p))
                return float(m.group(0)) if m else None
            nums = [_num(c["price"]) for c in r["cards"]]
            verdict(case, e, (not clos) and nc > 0 and all(
                n is not None and n <= 100 for n in nums),
                "closing_like=%s cards=%d prices=%s" % (
                    clos, nc, [c["price"] for c in r["cards"]]))
        elif case.startswith("fp_"):
            oos = short_exit(r["exit_gate"]) in ("out-of-scope", "out-of-scope-guardrail")
            verdict(case, e, (not oos) and nc == 0 and (not clos),
                    "oos=%s cards=%d closing_like=%s exit=%s" % (
                        oos, nc, clos, short_exit(r["exit_gate"])))
        elif case == "loc_kfc_nasr":
            verdict(case, e, nc == 0, "cards=%d exit=%s" % (nc, short_exit(r["exit_gate"])))
        elif case == "personal_order":
            verdict(case, e, nc == 0, "cards=%d exit=%s" % (nc, short_exit(r["exit_gate"])))
        elif case.startswith("valid_"):
            honest = ("انتهى" in ans or "expired" in ans.lower())
            verdict(case, e, nc == 0 and honest,
                    "cards=%d honest_expired=%s exit=%s" % (nc, honest, short_exit(r["exit_gate"])))
        # language match every case
        want = "ar" if r["query_lang"] in ("ar",) or any(
            ord(c) > 127 for c in q) else "en"
        # Franco (latin-script Arabic) also expects Arabic reply
        verdict(case + ":lang", e, r["reply_lang"] == want,
                "want=%s got=%s" % (want, r["reply_lang"]))

with open(dst, "w", encoding="utf-8") as f:
    f.write("=== TRACE SUMMARY ===\n")
    f.write("\n".join(lines))
    f.write("\n\n=== HARD VERDICTS ===\n")
    for c, e, v, w in HARD:
        f.write("%-26s %-7s %s  %s\n" % (c, e, v, w))
print("wrote %s (%d records, %d verdicts)" % (dst, len(recs), len(HARD)))
