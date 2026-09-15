"""
Fetches main.dim_purchasing_status and (with --write) turns it into
data/faqs_purchasing_status.json -- same additive, no-code-elsewhere-touched
pattern as fetch_payment_methods_clickhouse.py's faqs_payment_methods.json
and fetch_type_price_clickhouse.py's type_prices.json. build_index.py picks
this file up automatically if present (see the NEW block added there).

--debug output settled the open questions from the original investigation:

  - pur_status_name_ar IS populated for every row -- except id 13
    ("Don't Generate - Paymob Refund"), where name_ar is a verbatim copy of
    name_en rather than an actual translation. That's a tell for an
    internal/admin-only record, not a translation gap to work around.
  - pur_status_status does NOT cleanly mean "customer-facing" the way
    dim_payment_methods' status/mobile_status/admin_status columns did:
    id 11 "In Refund Process" is status=0 yet reads like a real
    customer-facing state, while id 13 above is status=1 despite being
    internal-only. So --write filters on name shape instead of the status
    column -- see _is_customer_facing.
  - id 4 "Canceled test" is explicitly a test row (name says so) and is
    dropped regardless of its status value.
  - id 9 "V Pending" is kept -- it has a real, non-fallback Arabic name --
    but its exact customer meaning is genuinely unclear from this table
    alone. See the caveat on _ANSWER_EN["V Pending"] below. Worth
    confirming with whoever owns the Fawry/verification flow before this
    ships to customers.

Usage:
    python ingestion/sources/fetch_purchasing_status_clickhouse.py --debug   # investigate only
    python ingestion/sources/fetch_purchasing_status_clickhouse.py --write   # writes data/faqs_purchasing_status.json

Requires: pip install clickhouse-connect
Requires: CLICKHOUSE_PASSWORD (and friends) in .env -- see config.py.
"""
import argparse
import json
import os
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import config  # noqa: E402

_COLUMNS = ["pur_status_id", "pur_status_name", "pur_status_status", "pur_status_name_ar"]
_QUERY = f"SELECT {', '.join(_COLUMNS)} FROM main.dim_purchasing_status"

_TEST_MARKERS = ("test",)


def _is_customer_facing(row: dict) -> bool:
    """True unless the row's *name* marks it as internal/test -- not its
    status flag (see module docstring for why status isn't reliable here)."""
    name = (row.get("pur_status_name") or "").strip()
    name_ar = (row.get("pur_status_name_ar") or "").strip()
    if not name:
        return False
    if any(marker in name.lower() for marker in _TEST_MARKERS):
        return False
    # name_ar identical to name_en (not just missing) is the id-13 tell:
    # nobody translated it because it was never meant to be customer-facing.
    if name_ar and name_ar == name:
        return False
    return True


# Deliberately generic and factual -- NOT claiming specific timelines,
# refund amounts, or policy details this lookup table doesn't actually
# contain. Keyed by pur_status_name so it's obvious in a diff which status
# a caveat below belongs to. New status values that show up later and
# aren't in this dict are skipped in _build_faq_records rather than
# guessed at -- see the "no answer copy written yet" branch.
_ANSWER_EN = {
    "Paid": "Your payment for this order was received and confirmed.",
    "In Process": "Your order has been confirmed and is currently being processed.",
    "Used": "This coupon has already been redeemed.",
    "Canceled": "This order or coupon has been canceled.",
    "Refund": "This order has been refunded.",
    "Pending": "Your order is awaiting payment confirmation.",
    "Waiting": "Your order is on hold, awaiting a further step before it can proceed.",
    # CAVEAT: distinct from plain "Pending" in the data, but the lookup
    # table doesn't say how -- likely a Fawry/payment-verification
    # sub-state. Confirm the exact customer-facing meaning before this
    # ships.
    "V Pending": "Your order is in a pending verification state.",
    "Expired": "This coupon's validity period has passed and it can no longer be used.",
    "In Refund Process": "Your refund has been initiated and is currently being processed.",
    "Fawry Pending": "Your payment via Fawry has not yet been confirmed.",
}

_ANSWER_AR = {
    "Paid": "ØªÙ… Ø§Ø³ØªÙ„Ø§Ù… ÙˆØªØ£ÙƒÙŠØ¯ Ø¯ÙØ¹ØªÙƒ Ù„Ù‡Ø°Ø§ Ø§Ù„Ø·Ù„Ø¨.",
    "In Process": "ØªÙ… ØªØ£ÙƒÙŠØ¯ Ø·Ù„Ø¨Ùƒ ÙˆØ¬Ø§Ø±Ù ØªÙ†ÙÙŠØ°Ù‡ Ø­Ø§Ù„ÙŠÙ‹Ø§.",
    "Used": "ØªÙ… Ø§Ø³ØªØ®Ø¯Ø§Ù… Ù‡Ø°Ø§ Ø§Ù„ÙƒÙˆØ¨ÙˆÙ† Ø¨Ø§Ù„ÙØ¹Ù„.",
    "Canceled": "ØªÙ… Ø¥Ù„ØºØ§Ø¡ Ù‡Ø°Ø§ Ø§Ù„Ø·Ù„Ø¨ Ø£Ùˆ Ø§Ù„ÙƒÙˆØ¨ÙˆÙ†.",
    "Refund": "ØªÙ… Ø§Ø³ØªØ±Ø¯Ø§Ø¯ Ù‚ÙŠÙ…Ø© Ù‡Ø°Ø§ Ø§Ù„Ø·Ù„Ø¨.",
    "Pending": "Ø·Ù„Ø¨Ùƒ ÙÙŠ Ø§Ù†ØªØ¸Ø§Ø± ØªØ£ÙƒÙŠØ¯ Ø§Ù„Ø¯ÙØ¹.",
    "Waiting": "Ø·Ù„Ø¨Ùƒ ÙÙŠ ÙˆØ¶Ø¹ Ø§Ù„Ø§Ù†ØªØ¸Ø§Ø± Ù‚Ø¨Ù„ Ø§Ø³ØªÙƒÙ…Ø§Ù„ Ø§Ù„Ø®Ø·ÙˆØ§Øª Ø§Ù„ØªØ§Ù„ÙŠØ©.",
    "V Pending": "Ø·Ù„Ø¨Ùƒ ÙÙŠ Ù…Ø±Ø­Ù„Ø© Ø§Ù†ØªØ¸Ø§Ø± Ø§Ù„ØªØ­Ù‚Ù‚.",
    "Expired": "Ø§Ù†ØªÙ‡Øª ØµÙ„Ø§Ø­ÙŠØ© Ù‡Ø°Ø§ Ø§Ù„ÙƒÙˆØ¨ÙˆÙ† ÙˆÙ„Ù… ÙŠØ¹Ø¯ ØµØ§Ù„Ø­Ù‹Ø§ Ù„Ù„Ø§Ø³ØªØ®Ø¯Ø§Ù….",
    "In Refund Process": "ØªÙ… Ø¨Ø¯Ø¡ Ø¹Ù…Ù„ÙŠØ© Ø§Ø³ØªØ±Ø¯Ø§Ø¯ Ù‚ÙŠÙ…Ø© Ø·Ù„Ø¨Ùƒ ÙˆØ¬Ø§Ø±Ù ØªÙ†ÙÙŠØ°Ù‡Ø§.",
    "Fawry Pending": "Ù„Ù… ÙŠØªÙ… ØªØ£ÙƒÙŠØ¯ Ø§Ù„Ø¯ÙØ¹ Ø¹Ø¨Ø± ÙÙˆØ±ÙŠ Ø­ØªÙ‰ Ø§Ù„Ø¢Ù†.",
}

_CATEGORY_EN = "purchase_status"
_CATEGORY_AR = "Ø­Ø§Ù„Ø© Ø§Ù„Ø·Ù„Ø¨"


def _build_faq_records(rows: list):
    records = []
    skipped = []
    for row in rows:
        name = (row.get("pur_status_name") or "").strip()
        if not _is_customer_facing(row):
            skipped.append(f"{name!r} (id={row.get('pur_status_id')}, internal/test)")
            continue
        if name not in _ANSWER_EN:
            skipped.append(f"{name!r} (id={row.get('pur_status_id')}, no answer copy written yet)")
            continue
        records.append({
            "id": f"purchasing_status_{row['pur_status_id']}",
            "question_en": f'What does the order status "{name}" mean?',
            "answer_en": _ANSWER_EN[name],
            "category": _CATEGORY_EN,
            "question_ar": f'Ù…Ø§Ø°Ø§ ØªØ¹Ù†ÙŠ Ø­Ø§Ù„Ø© Ø§Ù„Ø·Ù„Ø¨ "{row.get("pur_status_name_ar") or name}"ØŸ',
            "answer_ar": _ANSWER_AR.get(name, _ANSWER_EN[name]),
            "category_ar": _CATEGORY_AR,
        })
    return records, skipped


def run_debug():
    client = config.get_clickhouse_client()
    print("Running query:\n", _QUERY, "\n")
    rows = [dict(r) for r in client.query(_QUERY).named_results()]
    print(f"Fetched {len(rows)} row(s) total from dim_purchasing_status.\n")

    if not rows:
        print("--> Table is empty. Nothing to do here.")
        return

    print("All rows:")
    for r in rows:
        print(
            f"  id={r.get('pur_status_id')!r:>5}  "
            f"name={r.get('pur_status_name')!r:<25}  "
            f"name_ar={r.get('pur_status_name_ar')!r:<25}  "
            f"status={r.get('pur_status_status')!r}"
        )

    print("\nFull raw dicts:")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))

    records, skipped = _build_faq_records(rows)
    print(f"\n--> --write would produce {len(records)} FAQ record(s).")
    if skipped:
        print(f"--> --write would skip {len(skipped)} row(s):")
        for s in skipped:
            print(f"      {s}")


def run_write():
    client = config.get_clickhouse_client()
    rows = [dict(r) for r in client.query(_QUERY).named_results()]
    records, skipped = _build_faq_records(rows)

    if not records:
        print("--> No customer-facing rows produced any FAQ records. Nothing written.")
        return

    out_path = os.path.join(config.INDEX_DIR, "faqs_purchasing_status.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(records)} FAQ record(s) to {out_path}")
    if skipped:
        print(f"Skipped {len(skipped)} row(s):")
        for s in skipped:
            print(f"  {s}")
    print("\nRun ingestion/loaders/build_index.py to fold these into the index (see the NEW block in load_faqs()).")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--debug", action="store_true", help="Investigate only -- no write.")
    group.add_argument("--write", action="store_true", help="Write data/faqs_purchasing_status.json.")
    args = parser.parse_args()
    if args.debug:
        run_debug()
    else:
        run_write()


if __name__ == "__main__":
    main()
