"""Dump selected acceptance-trace records for the verification report (ASCII-safe)."""
import json

recs = [json.loads(l) for l in open("eval/acceptance_traces.jsonl", encoding="utf-8")]
idx = {(r["case"], r["turn"], r["engine"]): r for r in recs}
out = []


def show(case, turn, engine):
    r = idx[(case, turn, engine)]
    out.append("### %s t%d %s exit=%s" % (case, turn, engine, r["exit_gate"][:70]))
    out.append("answer_prefix: %s" % r["answer_prefix"][:400])
    for c in r["cards"]:
        out.append("card: id=%s price=%s expiry=%s expired=%s title=%.60s" % (
            c.get("id"), c.get("price"), c.get("expiry"),
            c.get("expired_vs_now"), c.get("title") or ""))
    for e in r.get("events", []):
        if e["kind"] in ("gate", "agent-plan", "agent-tool") and (
                e["decisive"] or e["name"] in ("retrieve",)):
            d = e["detail"]
            if isinstance(d, dict):
                d = {k: (str(v)[:100]) for k, v in d.items()}
            out.append("ev: %s %s dec=%s %s" % (e["kind"], e["name"], e["decisive"], d))
    out.append("llm: %s" % r["llm_calls"])
    out.append("intents: %s" % {k: (v[:80] if isinstance(v, str) else v)
                                for k, v in r["intents"].items() if v})
    out.append("")


for args in [("greet_hala_long", 0, "cascade"),
             ("offer_pizza", 0, "cascade"),
             ("followup_compare", 0, "cascade"),
             ("followup_compare", 1, "cascade"),
             ("followup_compare", 1, "agent"),
             ("meal_pizza_breakfast", 0, "cascade"),
             ("meal_tamara_iftar", 0, "cascade"),
             ("meal_sohour", 0, "cascade"),
             ("meal_ramadan_breakfast", 0, "cascade"),
             ("loc_kfc_nasr", 0, "cascade"),
             ("loc_kfc_nasr", 0, "agent"),
             ("personal_order", 0, "cascade"),
             ("personal_order", 0, "agent"),
             ("fp_gym", 0, "agent"),
             ("fp_valid", 0, "agent"),
             ("fp_cart", 0, "cascade"),
             ("offer_under100", 0, "cascade")]:
    show(*args)

with open("reports/record_details.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote reports/record_details.txt")
