"""Repro battery for user-reported issues (branch: exp/free-cloud-llm).

Posts a fixed set of offer / FAQ / personal queries to a running server
and saves {query, answer, seconds} + card counts for analysis.

Usage:
    python eval/repro_battery.py --base http://127.0.0.1:8010 --out eval/repro_20260929.json
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUERIES = [
    # -- offers: user says only one offer comes back --
    ("offer", "pizza offers"),
    ("offer", "What breakfast offers do you have under 200 EGP?"),
    ("offer", "coffee offers"),
    ("offer", "Show me Burger King offers"),
    ("offer", "offers under 100 EGP"),
    # -- FAQs: user says most are not answered --
    ("faq", "How do I register / create an account?"),
    ("faq", "How do I purchase from the Waffarha app?"),
    ("faq", "How do I use my purchased coupon?"),
    ("faq", "What payment methods are available on the app?"),
    ("faq", "How do I pay my bills through the app?"),
    ("faq", "How do I check my bill status?"),
    ("faq", "How do I refund a coupon?"),
    ("faq", "What is the coupon refund policy?"),
    ("faq", "How do I add or remove a saved bank card?"),
    ("faq", "How do I buy a gift voucher?"),
    ("faq", "What is the cashback policy?"),
    # -- personal: user says own data is not retrieved --
    ("personal", "show my coupons"),
    ("personal", "show my orders"),
    ("personal", "where is my order"),
    ("personal", "I want a refund"),
]


def post(base: str, query: str, timeout: int = 300) -> dict:
    started = time.perf_counter()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/chat",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        return {"answer": body.get("answer", ""),
                "seconds": round(time.perf_counter() - started, 2)}
    except Exception as e:  # noqa: BLE001
        return {"answer": "", "error": f"{type(e).__name__}: {str(e)[:200]}",
                "seconds": round(time.perf_counter() - started, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--out", default="eval/repro_battery_out.json")
    args = ap.parse_args()
    rows = []
    for kind, q in QUERIES:
        res = post(args.base, q)
        res.update({"kind": kind, "query": q})
        rows.append(res)
        status = "ERR " if res.get("error") else f"{len(res['answer'])}ch"
        print(f"[{kind}] {status} {res['seconds']}s -- {q[:60]}", flush=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(rows)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
