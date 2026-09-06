"""
Investigates main.dim_partners before deciding whether/how to fold merchant
info (address, phone, website, social links, working hours, lat/lng) into
the RAG index.

Why this matters: fetch_offers_clickhouse.py already joins dim_partners for
part_name_en/ar, but discards everything else on that table. Questions like
"where is this branch?", "what time do they open?", "is there a phone
number?" currently have nothing in the corpus to answer them from -- IF the
relevant columns are actually populated for real, currently-listed partners.
This script is investigation only (no write), same as
fetch_type_price_clickhouse.py --debug: figure out coverage and status
semantics on real data before writing any ingest/merge logic.

What it checks:
  - distinct `status` values (need to know which partners are "live" the
    same way dim_payment_methods needed status/mobile_status/admin_status
    sorted out before trusting them)
  - for each candidate column (address, phone, website, socials, work
    hours, lat/lng), what fraction of ACTIVE partners actually have a
    non-empty value -- a field that's 90% empty isn't worth embedding,
    one that's 90% populated is
  - a handful of full sample rows for currently-listed partners (joined
    to dim_offers so we're looking at partners real offers point to, not
    dead/test rows sitting unused in dim_partners)

Usage:
    python ingest/fetch_partners_clickhouse.py --debug

Requires: pip install clickhouse-connect
Requires: CLICKHOUSE_PASSWORD (and friends) in .env -- see config.py.
"""
import argparse
import datetime
import json
import os
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import config  # noqa: E402

_PARTNER_COLUMNS = [
    "part_id", "part_name_en", "part_name_ar", "status",
    "part_address_en", "part_address_ar",
    "part_address_en2", "part_address_ar2",
    "part_tel", "part_tel2", "part_tel3", "part_tel4",
    "part_website",
    "part_facebook", "part_facebook2", "twitter", "youtube", "instagram",
    "part_glat", "part_glng",
    "work_time_en", "work_time_ar",
    "location_en", "location_ar",
    "part_logo", "cover_photo",
]

# Only partners that at least one currently-listed offer actually points to
# -- dim_partners likely has dead/test rows the same way dim_payment_methods
# did, and there's no point profiling field coverage on rows nothing links
# to. Distinct on part_id since one partner can have many offers.
_QUERY = f"""
SELECT DISTINCT {', '.join(f'p.{c}' for c in _PARTNER_COLUMNS)}
FROM main.dim_partners AS p
INNER JOIN main.dim_offers AS o ON o.part_id = p.part_id
WHERE o.deleted_at IS NULL
"""


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)
    if hasattr(v, "item"):  # numpy scalar
        return v.item()
    return v


def _is_present(v) -> bool:
    v = _clean(v)
    if v is None:
        return False
    s = str(v).strip()
    return s != "" and s.lower() not in ("0", "null", "none")


def run_debug():
    client = config.get_clickhouse_client()
    print("Running query:\n", _QUERY, "\n")
    rows = [dict(r) for r in client.query(_QUERY).named_results()]
    print(f"Fetched {len(rows)} distinct partner(s) linked to a non-deleted offer.\n")

    if not rows:
        print("--> No rows returned. Check the join / CLICKHOUSE_DATABASE / permissions.")
        return

    # Distinct status values, same check as dim_payment_methods -- need to
    # know if 'status' means anything here before trusting rows on it.
    counts = {}
    for r in rows:
        v = _clean(r.get("status"))
        counts[v] = counts.get(v, 0) + 1
    print(f"Distinct 'status' values: {counts}\n")

    # Field coverage: of these partners, what fraction actually has each
    # candidate field populated? Decides whether a field is worth embedding
    # at all vs. mostly-empty noise.
    check_cols = [
        "part_address_en", "part_address_ar", "part_address_en2", "part_address_ar2",
        "part_tel", "part_tel2", "part_website",
        "part_facebook", "instagram", "twitter", "youtube",
        "part_glat", "part_glng",
        "work_time_en", "work_time_ar",
        "location_en", "location_ar",
    ]
    n = len(rows)
    print("Field coverage across these partners:")
    for col in check_cols:
        n_present = sum(1 for r in rows if _is_present(r.get(col)))
        pct = 100 * n_present / n if n else 0
        print(f"  {col:<18} {n_present:>4} / {n}  ({pct:5.1f}%)")

    print("\nFull raw dict for 3 sample partners:")
    for r in rows[:3]:
        print(json.dumps({k: _clean(v) for k, v in r.items()}, ensure_ascii=False, indent=2, default=str))

    print(
        "\n--> Which status value(s) mean 'a live, listed partner'? Which of the "
        "fields above are populated often enough to be worth embedding (address, "
        "phone, hours, website/socials, lat/lng)? Once that's confirmed I'll write "
        "the merge logic -- most likely folding a 'Location' / 'Contact' / 'Hours' "
        "block into fetch_offers_clickhouse.py's existing partner join, same "
        "additive pattern as the type_price tiers."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Investigate dim_partners field coverage -- no write.")
    args = parser.parse_args()

    if args.debug:
        run_debug()
    else:
        parser.error("pass --debug (investigation only -- no --write yet, see module docstring)")


if __name__ == "__main__":
    main()
