"""
Checks whether main.dim_type_price actually represents multi-tier pricing
worth capturing in the RAG index, before building any ingest logic for it.

Current offer docs (see build_index.py's load_offers(), fed by either
fetch_offers.py or fetch_offers_clickhouse.py) only ever show ONE price per
offer, taken straight from dim_offers.actual_value/offer_value. If a
meaningful share of offers actually have several purchasable price options
(e.g. "Single" vs "Family" vs "VIP" seating) sitting in dim_type_price, the
current text ("Price: 150 EGP") is silently wrong/incomplete for those
offers -- a customer asking "how much for two people" would get no tier
info at all. If it turns out to be rare, or the extra rows are stale/
inactive/duplicate rather than real distinct options, it's not worth the
added complexity.

DECISION (confirmed from --debug output): worth ingesting. 909 of 1090
offers with any dim_type_price rows have genuinely different named/priced
options (dental packages, room-reservation durations, meal combos), not
duplicates. 'status' is confirmed active/superseded, not a language or
type flag -- e.g. offer_id=8122 shows the SAME tier name at two different
prices over time (520/650 EGP vs 545/700 EGP), one row status=0 and the
other status=1; offer_id=7929 shows an old un-scoped tier (status=0) later
replaced by city-scoped versions (status=1). So status=1 is "currently
purchasable", status=0 is stale/superseded -- filtering to status=1 gives
the live tier set.

Usage:
    python ingest/fetch_type_price_clickhouse.py --debug   # investigate only, no write (see above)
    python ingest/fetch_type_price_clickhouse.py --write   # writes data/type_prices.json (status=1 only)
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

_COLUMNS = [
    "type_price_id", "offer_id", "type_price_name", "type_price_name_ar",
    "type_price_price", "type_price_discount", "type_price_price_before_discount",
    "status", "type_price_commission", "notes", "start_date", "expire_date",
]

_QUERY = f"SELECT {', '.join(_COLUMNS)} FROM main.dim_type_price"


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
    rows = [dict(r) for r in client.query(_QUERY).named_results()]
    print(f"Fetched {len(rows)} row(s) total from dim_type_price.\n")

    if not rows:
        print("--> Table is empty. Nothing to do here -- current single-price offer docs are already complete.")
        return

    # How many DISTINCT offers actually have rows here, and of those, how
    # many have MORE THAN ONE row (i.e. genuine multi-tier candidates)?
    per_offer = {}
    for r in rows:
        oid = _clean(r.get("offer_id"))
        per_offer.setdefault(oid, []).append(r)

    n_offers = len(per_offer)
    multi = {oid: rs for oid, rs in per_offer.items() if len(rs) > 1}
    print(f"{n_offers} distinct offer_id(s) appear in dim_type_price.")
    print(f"{len(multi)} of those have MORE THAN ONE row (genuine multi-tier candidates).\n")

    # Distinct status values, same as the payment-methods check -- need to
    # know if "extra rows" might just be old/inactive tiers, not live options.
    counts = {}
    for r in rows:
        v = _clean(r.get("status"))
        counts[v] = counts.get(v, 0) + 1
    print(f"Distinct 'status' values across all rows: {counts}\n")

    # Show a handful of real multi-tier offers in full, so we can see
    # whether the extra rows are meaningfully different options (different
    # name/price -- e.g. "Single"/"Family") or near-duplicates/noise.
    sample_oids = list(multi.keys())[:5]
    print(f"Sample of up to 5 multi-tier offers (offer_id -> its dim_type_price rows):\n")
    for oid in sample_oids:
        print(f"offer_id={oid}:")
        for r in multi[oid]:
            print("  " + json.dumps({k: _clean(v) for k, v in r.items()}, ensure_ascii=False, default=str))
        print()

    print(
        "--> Look at the sample above: are the multiple rows per offer genuinely different "
        "purchasable options (different name + price), or duplicates/stale rows sharing a "
        "status value we should filter on? Share this output back and I'll decide whether "
        "dim_type_price is worth ingesting, and if so, how to fold it into the offer docs."
    )


# Confirmed via --debug: status=1 rows are the currently-purchasable tiers;
# status=0 rows are stale/superseded versions of the same tier (see module
# docstring). This is a constant, not a CLI flag -- it reflects a decision
# already made from real data, not something to leave open per-run.
_ACTIVE_STATUS = 1


def fetch_active_tiers() -> dict:
    """Returns {str(offer_id): [tier_dict, ...]} for every offer with at
    least one currently-active (status=1) pricing tier. Keyed as a string
    because that's what json.dump()/json.load() round-trip as dict keys
    regardless of the source type -- build_index.py's load_offers() looks
    offers up the same way (str(offer_id)) so the two sides always agree."""
    client = config.get_clickhouse_client()
    rows = [dict(r) for r in client.query(f"{_QUERY} WHERE status = {_ACTIVE_STATUS}").named_results()]

    by_offer = {}
    for r in rows:
        oid = _clean(r.get("offer_id"))
        if oid is None:
            continue
        by_offer.setdefault(str(oid), []).append({
            "type_price_id": _clean(r.get("type_price_id")),
            "name_en": _clean(r.get("type_price_name")),
            "name_ar": _clean(r.get("type_price_name_ar")),
            "price": _clean(r.get("type_price_price")),
            "price_before_discount": _clean(r.get("type_price_price_before_discount")),
            "discount": _clean(r.get("type_price_discount")),
            # NEW: keep the tier-level start/expiry dates. Previously the
            # snapshot dropped these, so build_index.py only ever saw the
            # offer-level dim_offers.offer_expire_date -- but that summary
            # field can lag the tier dates the site actually shows (e.g.
            # offer 8291: dim_offers says 2026-08-31 while the purchasable
            # tiers run until 2026-09-30). Consumers now compute the offer's
            # effective expiry as the later of the two.
            "start_date": _clean(r.get("start_date")),
            "expire_date": _clean(r.get("expire_date")),
        })
    return by_offer


def run_write():
    by_offer = fetch_active_tiers()

    out_path = os.path.join(config.INDEX_DIR, "type_prices.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(by_offer, f, ensure_ascii=False, indent=2)

    n_offers = len(by_offer)
    n_tiers = sum(len(v) for v in by_offer.values())
    print(f"\nDone. {n_offers} offer(s) with active pricing tier(s) -> {n_tiers} tier record(s) saved to {out_path}")
    print("Next: python ingest/build_index.py --backend faiss   (load_offers() now also reads this file)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Investigate only -- no write (see module docstring).")
    parser.add_argument("--write", action="store_true", help="Write data/type_prices.json from status=1 rows.")
    args = parser.parse_args()

    if args.write:
        run_write()
    elif args.debug:
        run_debug()
    else:
        parser.error("pass --debug or --write")


if __name__ == "__main__":
    main()