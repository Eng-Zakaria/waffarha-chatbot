"""
Pulls main.dim_payment_methods from ClickHouse and turns each usable row
into FAQ-shaped docs (source="faq") so it feeds the SAME code path as
data/faqs.json -- rag_engine.py, memory.py, and every direct-answer
shortcut already handle source in ("offer", "faq") everywhere, so treating
these as FAQs means ZERO changes to rag_engine.py/memory.py, same "reuse
the existing shape" approach as fetch_offers_clickhouse.py.

STATUS: filter confirmed against real checkout data -- 15 payment_ids
reviewed and approved (see _CONFIRMED_ACTIVE_IDS below). This now writes
data/faqs_payment_methods.json in the SAME raw record shape as
data/faqs.json (id/question_en/answer_en/category/question_ar/answer_ar/
category_ar) -- so build_index.py's load_faqs() can pick it up with one
small additive change (see the CHANGED comment added there) instead of
this script needing to know anything about doc/embedding shape itself.

Usage:
    python ingestion/sources/fetch_payment_methods_clickhouse.py --debug     # raw dump
    python ingestion/sources/fetch_payment_methods_clickhouse.py --preview   # filtered candidate list, no write
    python ingestion/sources/fetch_payment_methods_clickhouse.py --write     # writes data/faqs_payment_methods.json
"""
import argparse
import datetime
import json
import os
import re
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import config  # noqa: E402

_PAYMENT_COLUMNS = [
    "payment_id", "payment_name", "payment_name_en",
    "status", "mobile_status", "admin_status",
    "list_in_view", "sort",
    "description", "description_ar", "description_html_en", "description_html_ar",
    "subtitle_en", "subtitle_ar",
    "refund_message_en", "refund_message_ar", "refund_terms_en", "refund_terms_ar",
    "has_fees", "has_tax", "tax_percentage",
    "bill_payment_extra_fees", "bill_payment_extra_fees_type",
    "min", "max", "with_wallet", "type", "provider",
]

_QUERY = f"SELECT {', '.join(_PAYMENT_COLUMNS)} FROM main.dim_payment_methods"


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)
    if hasattr(v, "item"):
        return v.item()
    return v


def run_debug():
    client = config.get_clickhouse_client()
    print("Running query:\n", _QUERY, "\n")
    result = client.query(_QUERY)
    rows = [dict(r) for r in result.named_results()]
    print(f"Fetched {len(rows)} row(s) total (this table should be small -- one row per payment method).\n")

    # Distinct values for every status-like column, with counts, so we can
    # actually see which value(s) mean "enabled" instead of guessing.
    for col in ["status", "mobile_status", "admin_status", "list_in_view"]:
        counts = {}
        for r in rows:
            v = _clean(r.get(col))
            counts[v] = counts.get(v, 0) + 1
        print(f"Distinct '{col}' values: {counts}")

    print("\nAll rows (payment_id, payment_name_en, status, mobile_status, admin_status, list_in_view):")
    for r in rows:
        print(
            f"  id={_clean(r['payment_id'])!r:>6}  "
            f"name_en={_clean(r['payment_name_en'])!r:<25}  "
            f"status={_clean(r['status'])!r:<10}  "
            f"mobile_status={_clean(r['mobile_status'])!r:<10}  "
            f"admin_status={_clean(r['admin_status'])!r:<10}  "
            f"list_in_view={_clean(r['list_in_view'])!r}"
        )

    print("\nFull raw dict for the first 3 rows (to check description/refund field content + which is en vs ar):")
    for r in rows[:3]:
        print(json.dumps({k: _clean(v) for k, v in r.items()}, ensure_ascii=False, indent=2, default=str))

    print(
        "\n--> Share this output back. Specifically: which status/mobile_status/admin_status "
        "value(s) mean 'a customer can actually pay with this today', and whether the plain "
        "'description' column (no _en suffix) is English or Arabic text."
    )


# NEW: first-pass guess at "customer can actually pay with this today", built
# from eyeballing the real --debug output (confirmed: status/mobile_status/
# admin_status disagree with each other on several rows, e.g. aman/Contact
# have admin_status=disactive despite status+mobile_status=active, and
# Qahera Cash/klivvr/Halan have the reverse). Requiring ALL THREE to be
# 'active' is the conservative choice -- it will under-include (miss some
# real live methods where one field lagged an update) rather than
# over-include (tell a customer they can use something they can't), which is
# the safer failure direction for customer-facing payment info. list_in_view
# is intentionally NOT required here, since it disagreed with an otherwise-
# fully-active row (Vodafone Cash Wallet, id 109) -- shown separately in the
# preview instead so you can judge it yourself.
#
# THIS IS A GUESS TO REVIEW, NOT A FINAL ANSWER -- see run_preview().
def _looks_active(r: dict) -> bool:
    return (
        _clean(r.get("status")) == "active"
        and _clean(r.get("mobile_status")) == "active"
        and _clean(r.get("admin_status")) == "active"
    )


def run_preview():
    client = config.get_clickhouse_client()
    result = client.query(_QUERY)
    rows = [dict(r) for r in result.named_results()]

    candidates = [r for r in rows if _looks_active(r)]
    print(f"{len(candidates)} of {len(rows)} rows pass status==mobile_status==admin_status=='active':\n")

    for r in candidates:
        name = (_clean(r["payment_name_en"]) or "").replace("\n", " ").strip()
        has_desc = bool(_clean(r.get("description")) or _clean(r.get("description_ar")))
        has_refund = bool(
            _clean(r.get("refund_message_en")) or _clean(r.get("refund_terms_en"))
            or _clean(r.get("refund_message_ar")) or _clean(r.get("refund_terms_ar"))
        )
        print(
            f"  id={_clean(r['payment_id']):>5}  name_en={name!r:<30}  "
            f"list_in_view={_clean(r.get('list_in_view'))}  "
            f"has_description={has_desc}  has_refund_content={has_refund}"
        )

    print(
        "\n--> Does this list match what customers can actually pay with today? "
        "Anything missing (a real method excluded by the strict filter) or "
        "anything that shouldn't be here (e.g. an internal/test entry that "
        "happens to have all three fields 'active')? Reply with corrections "
        "-- an explicit include/exclude list by id is safest given how "
        "inconsistent this table is -- and I'll finalize the FAQ mapping "
        "against the confirmed set, not this guess."
    )


# CONFIRMED against real checkout behavior (see conversation review) --
# these 15 are the payment methods customers can actually use today.
# Everything else in dim_payment_methods is dead/test/internal, regardless
# of what its status columns say. If this list ever needs to change,
# change it HERE explicitly rather than reverting to the status-column
# filter -- that filter is what _looks_active()/run_preview() exists to
# sanity-check, not something to trust blindly for a full write.
_CONFIRMED_ACTIVE_IDS = [4, 48, 50, 61, 66, 68, 75, 79, 95, 107, 109, 112, 117, 119, 131]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(text) -> str:
    """Strips embedded HTML (refund_terms/description fields contain literal
    '<br />' etc. -- confirmed in the --debug output) and collapses
    \\r\\n/\\r/\\n/repeated-space runs into single spaces, so embedded text
    and anything the LLM might copy into a reply is plain, clean prose."""
    if not text:
        return ""
    t = _HTML_TAG_RE.sub(" ", str(text))
    t = _WHITESPACE_RE.sub(" ", t)
    return t.strip()


def _pick(row: dict, base: str, lang: str) -> str:
    return _clean_text(row.get(f"{base}_{lang}"))


def _build_faq_records(row: dict) -> list:
    """One row -> up to 2 FAQ records (info always, refund only if content
    exists), each already in data/faqs.json's raw shape. If only one
    language actually has content, both language slots fall back to it
    (flagged via a printed warning) rather than shipping an empty answer --
    imperfect, but better than a blank/crashing FAQ entry; worth fixing by
    hand in the source data if it comes up often."""
    pid = _clean(row["payment_id"])
    name_en = (_clean(row.get("payment_name_en")) or "").replace("\n", " ").strip()
    name_ar = (_clean(row.get("payment_name")) or name_en).replace("\n", " ").strip()

    records = []

    # NOTE: unlike every other field here, English description has NO _en
    # suffix -- confirmed from --debug output: the column is literally
    # "description" (English text), paired with "description_ar" (Arabic).
    # _pick()'s "{base}_{lang}" pattern doesn't apply to this one field.
    desc_en = _clean_text(row.get("description"))
    desc_ar = _clean_text(row.get("description_ar"))
    if not desc_en and not desc_ar:
        print(f"  [skip] id={pid} name={name_en!r}: no description in either language, skipping info doc")
    else:
        if not desc_en:
            print(f"  [warn] id={pid} name={name_en!r}: no English description, reusing Arabic for both langs")
        if not desc_ar:
            print(f"  [warn] id={pid} name={name_en!r}: no Arabic description, reusing English for both langs")
        records.append({
            "id": f"payment_{pid}_info",
            "category": "payment",
            "category_ar": "Ø§Ù„Ø¯ÙØ¹",
            "question_en": f"How does paying with {name_en} work?",
            "answer_en": desc_en or desc_ar,
            "question_ar": f"Ø¥Ø²Ø§ÙŠ Ø£Ø¯ÙØ¹ Ø¨Ù€ {name_ar}ØŸ",
            "answer_ar": desc_ar or desc_en,
        })

    refund_en = " ".join(x for x in [_pick(row, "refund_message", "en"), _pick(row, "refund_terms", "en")] if x)
    refund_ar = " ".join(x for x in [_pick(row, "refund_message", "ar"), _pick(row, "refund_terms", "ar")] if x)
    if refund_en or refund_ar:
        if not refund_en:
            print(f"  [warn] id={pid} name={name_en!r}: no English refund text, reusing Arabic for both langs")
        if not refund_ar:
            print(f"  [warn] id={pid} name={name_en!r}: no Arabic refund text, reusing English for both langs")
        records.append({
            "id": f"payment_{pid}_refund",
            "category": "payment",
            "category_ar": "Ø§Ù„Ø¯ÙØ¹",
            "question_en": f"What is the refund policy if I paid with {name_en}?",
            "answer_en": refund_en or refund_ar,
            "question_ar": f"Ø¥ÙŠÙ‡ Ø³ÙŠØ§Ø³Ø© Ø§Ù„Ø§Ø³ØªØ±Ø¬Ø§Ø¹ Ù„Ùˆ Ø¯ÙØ¹Øª Ø¨Ù€ {name_ar}ØŸ",
            "answer_ar": refund_ar or refund_en,
        })

    return records


def run_write():
    client = config.get_clickhouse_client()
    result = client.query(f"{_QUERY} WHERE payment_id IN ({','.join(str(i) for i in _CONFIRMED_ACTIVE_IDS)})")
    rows = [dict(r) for r in result.named_results()]
    found_ids = {_clean(r["payment_id"]) for r in rows}
    missing = set(_CONFIRMED_ACTIVE_IDS) - found_ids
    if missing:
        print(f"WARNING: {missing} from _CONFIRMED_ACTIVE_IDS not found in dim_payment_methods -- "
              f"table may have changed since confirmation. Continuing with what was found.")

    all_records = []
    for row in rows:
        all_records.extend(_build_faq_records(row))

    out_path = os.path.join(config.INDEX_DIR, "faqs_payment_methods.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    n_info = sum(1 for r in all_records if r["id"].endswith("_info"))
    n_refund = sum(1 for r in all_records if r["id"].endswith("_refund"))
    print(f"\nDone. {len(rows)} payment method(s) -> {n_info} info + {n_refund} refund FAQ record(s) "
          f"= {len(all_records)} total, saved to {out_path}")
    print("Next: python ingestion/loaders/build_index.py --backend faiss   (load_faqs() now also reads this file)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Raw dump of every row + distinct status values.")
    parser.add_argument("--preview", action="store_true",
                         help="Apply the best-guess active filter and show which payment methods "
                              "would be included, for review before any FAQ docs get written.")
    parser.add_argument("--write", action="store_true",
                         help="Write data/faqs_payment_methods.json from the confirmed id list "
                              "(_CONFIRMED_ACTIVE_IDS below).")
    args = parser.parse_args()

    if args.write:
        run_write()
    elif args.preview:
        run_preview()
    elif args.debug:
        run_debug()
    else:
        parser.error("pass --debug, --preview, or --write")


if __name__ == "__main__":
    main()
