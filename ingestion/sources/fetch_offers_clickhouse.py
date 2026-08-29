"""
Pulls active offers from ClickHouse (main.dim_offers joined to
main.dim_partners) and writes data/offers_raw.json in EXACTLY the shape
fetch_offers.py already produces from the mobile API -- so build_index.py's
load_offers() / pick_field() / OFFER_FIELD_CANDIDATES machinery needs ZERO
changes to consume it. This is a drop-in alternative offer source, not a
new pipeline.

Why this works with no changes downstream:
    config.OFFER_FIELD_CANDIDATES already expects keys like "actual_value",
    "offer_value", "offer_discount", "offer_expire_date" -- because those
    are literally the ClickHouse column names dim_offers already uses.
    (The mobile API apparently returns the same underlying field names.)
    The one thing the JSON-API pipeline does that ClickHouse doesn't do for
    you automatically is split into one record PER LANGUAGE (fetch_offers.py
    calls the API once per lang in config.LANGS and tags each result
    "_lang"). dim_offers instead has both _en/_ar columns on the SAME row,
    so this script does that split itself: each ClickHouse row becomes TWO
    synthetic offer dicts (one "en", one "ar"), each populated with ONLY
    that language's columns under the SAME generic key names load_offers()
    already looks for (e.g. "mobile_offer_title_en" only appears in the en
    dict, "mobile_offer_title_ar" only in the ar dict) -- so pick_field()'s
    candidate-list fallback picks the right one automatically, same as it
    does today for real API responses.

Usage:
    # Sanity-check the query/column mapping against real data first:
    python ingest/fetch_offers_clickhouse.py --debug

    # Then pull everything and (over)write data/offers_raw.json:
    python ingest/fetch_offers_clickhouse.py

    # Merge into a separate file instead of overwriting the API's output,
    # e.g. if you want both sources side by side while validating this one:
    python ingest/fetch_offers_clickhouse.py --output offers_raw_ch.json

Requires: pip install clickhouse-connect
Requires: CLICKHOUSE_PASSWORD (and friends) in .env -- see config.py.
"""
import argparse
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

# Only the columns load_offers()/pick_field() (via OFFER_FIELD_CANDIDATES)
# or this script's own mapping actually use. Selecting explicitly rather
# than `SELECT *` -- dim_offers has 140+ columns and most aren't relevant
# here; an explicit list also means a future schema change fails loudly
# (missing column error) rather than silently pulling in unused bytes.
_OFFER_COLUMNS = [
    "offer_id", "part_id", "section_id",
    "mobile_offer_title_en", "mobile_offer_title_ar",
    "offer_brief_en", "offer_brief_ar",
    "offer_desc_en", "offer_desc_ar",
    "actual_value", "offer_value", "offer_discount",
    "offer_expire_date", "offer_status", "deleted_at",
    "offer_sold_coponos", "offer_no_coponos",
    "special_display",
    "rate", "rate_count",
    # NEW: offer fine-print/terms from dim_offers
    "offer_fineprint_en", "offer_fineprint_ar",
    "waffarha_advice_en", "waffarha_advice_ar",
]

# NEW: partner contact/location columns, confirmed worth ingesting via
# fetch_partners_clickhouse.py --debug against 2507 currently-listed
# partners (each linked to at least one non-deleted offer):
#   part_tel        86.9% populated
#   part_facebook   88.7%
#   part_address_en/ar   33.1% (HTML-wrapped, e.g. "<ul>\r\n\t<li>...</li></ul>")
#   work_time_en/ar 31.6% / 31.4%
#   part_website    13.5%  -- low, but cheap to include when present
#   instagram       24.1%
#   part_tel2       23.0%
# Deliberately EXCLUDED (confirmed ~0% populated in the same debug run):
# part_glat/part_glng, twitter, youtube, location_en/ar, part_address_en2/ar2.
# `status` split 2503 active / 4 inactive across those partners -- gate on
# status == 'active' in _row_to_lang_offer, same binary pattern already used
# for offer_status elsewhere in this pipeline.
_PARTNER_COLUMNS = {
    "status": "partner_status",
    "part_address_en": "part_address_en",
    "part_address_ar": "part_address_ar",
    "part_tel": "part_tel",
    "part_tel2": "part_tel2",
    "part_website": "part_website",
    "part_facebook": "part_facebook",
    "instagram": "part_instagram",
    "work_time_en": "work_time_en",
    "work_time_ar": "work_time_ar",
}

_QUERY = f"""
SELECT {', '.join(f'o.{c}' for c in _OFFER_COLUMNS)},
       p.part_name_en, p.part_name_ar,
       {', '.join(f'p.{col} AS {alias}' for col, alias in _PARTNER_COLUMNS.items())}
FROM main.dim_offers AS o
LEFT JOIN main.dim_partners AS p ON o.part_id = p.part_id
WHERE o.deleted_at IS NULL
"""
# NOTE: deliberately NOT filtering `offer_status = 'active'` here -- the
# JSON-API pipeline doesn't pre-filter at fetch time either; load_offers()
# applies config.OFFER_ACTIVE_VALUES itself, so filtering happens in exactly
# one place regardless of which source fed it. If you want to shrink the
# pull (e.g. this table gets huge), add the status filter here too, but keep
# it in sync with config.OFFER_ACTIVE_VALUES or the two will drift.


def _clean(v):
    """ClickHouse client returns None for SQL NULL already. This normalizes
    two things json.dump() can't handle on its own:
      - numpy scalar types (int64, float32, ...) -> plain Python numbers
      - datetime/date objects (clickhouse-connect returns real datetime
        objects for DateTime columns like offer_expire_date, not strings) ->
        "YYYY-MM-DD HH:MM:SS", matching the exact format build_index.py's
        load_offers() already assumes the raw expiry string is in (see its
        comment: "raw expiry is 'YYYY-MM-DD HH:MM:SS' ... truncated once
        here" -- str(datetime(...)) produces exactly that format, so no
        changes are needed on the build_index.py side).
    """
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)
    if hasattr(v, "item"):  # numpy scalar (int64, float32, etc.)
        return v.item()
    return v


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(text) -> str:
    """Strips embedded HTML (part_address_en/ar come back HTML-wrapped, e.g.
    "<ul>\\r\\n\\t<li>26 Adly St., - Downtown</li>\\n</ul>", confirmed in
    fetch_partners_clickhouse.py --debug output), decodes HTML entities
    (e.g. a lone "<p>&nbsp;</p>" tail left "&nbsp;" behind when only tags
    were stripped -- caught in the previous session's testing), and
    collapses \\r\\n/\\r/\\n/repeated-space runs into single spaces. Same
    cleanup fetch_payment_methods_clickhouse.py already does for
    description/refund text."""
    if not text:
        return ""
    t = _HTML_TAG_RE.sub(" ", str(text))
    t = html.unescape(t)
    t = _WHITESPACE_RE.sub(" ", t)
    return t.strip()


def _row_to_lang_offer(row: dict, lang: str) -> dict:
    """Builds ONE synthetic offer dict for ONE language from a joined
    ClickHouse row, containing only the fields fetch_offers.py's real API
    responses would contain for that language -- see module docstring."""
    suffix = f"_{lang}"
    offer = {
        "offer_id": _clean(row["offer_id"]),
        "actual_value": _clean(row["actual_value"]),
        "offer_value": _clean(row["offer_value"]),
        "offer_discount": _clean(row["offer_discount"]),
        "offer_expire_date": _clean(row["offer_expire_date"]),
        "offer_status": _clean(row["offer_status"]),
        # NEW vs. the JSON pipeline: these are clean integers straight from
        # the warehouse, not a "18086 sold coupons" string to regex-parse
        # (see build_index.py's _extract_sold_count) -- feeding a plain
        # digit string through unchanged still works with that function
        # as-is, no changes needed there.
        "sold_coupons_count": _clean(row["offer_sold_coponos"]),
        "offer_special_display": _clean(row["special_display"]),
        "_section_id": _clean(row["section_id"]),
        "_lang": lang,
    }

    title = _clean(row.get(f"mobile_offer_title{suffix}"))
    if not title:
        # mobile_offer_title_* is Nullable in the schema; offer_brief_* is
        # NOT nullable, so it's the safer fallback rather than skipping the
        # offer outright (load_offers() already drops title-less offers via
        # skipped_no_title -- better that filter runs on real absence of
        # both fields, not just the first one).
        title = _clean(row.get(f"offer_brief{suffix}"))
    offer[f"mobile_offer_title{suffix}"] = title

    desc = _clean(row.get(f"offer_desc{suffix}")) or _clean(row.get(f"offer_brief{suffix}"))
    # config.OFFER_FIELD_CANDIDATES["description"] looks for a lang-neutral
    # "offer_brief" key (the mobile API returns one already-language-scoped
    # field per response) -- so unlike title/price fields above, this one
    # DOES need renaming rather than a straight column copy.
    offer["offer_brief"] = desc

    merchant_name = _clean(row.get(f"part_name{suffix}"))
    if merchant_name:
        partners = {"part_name": merchant_name}

        # NEW: contact/location fields, only surfaced for a currently-active
        # partner (status confirmed 2503 active / 4 inactive of the 2507
        # partners linked to a non-deleted offer -- an inactive partner's
        # stale phone/address isn't worth showing a customer). Lang-scoped
        # fields (address, hours) pull the suffix matching this offer dict's
        # language; lang-neutral fields (phone, facebook, website,
        # instagram) are the same for both "en" and "ar" copies.
        if _clean(row.get("partner_status")) == "active":
            address = _clean_text(row.get(f"part_address{suffix}"))
            if address:
                partners["address"] = address
            hours = _clean_text(row.get(f"work_time{suffix}"))
            if hours:
                partners["hours"] = hours
            tel = _clean(row.get("part_tel"))
            if tel:
                partners["phone"] = str(tel)
            tel2 = _clean(row.get("part_tel2"))
            if tel2:
                partners["phone2"] = str(tel2)
            website = _clean(row.get("part_website"))
            if website:
                partners["website"] = str(website)
            facebook = _clean(row.get("part_facebook"))
            if facebook:
                partners["facebook"] = str(facebook)
            instagram = _clean(row.get("part_instagram"))
            if instagram:
                partners["instagram"] = str(instagram)

        offer["partners"] = partners

    return offer


def fetch_all_offers() -> list:
    client = config.get_clickhouse_client()
    result = client.query(_QUERY)
    rows = result.named_results()  # list of dict-like rows

    offers = []
    for row in rows:
        row = dict(row)
        for lang in config.LANGS:
            offers.append(_row_to_lang_offer(row, lang))
    return offers


def run_debug():
    client = config.get_clickhouse_client()
    print("Running query:\n", _QUERY)
    result = client.query(_QUERY + " LIMIT 3")
    rows = list(result.named_results())
    print(f"\nFetched {len(rows)} sample row(s).")
    if not rows:
        print("--> No rows returned. Check CLICKHOUSE_DATABASE / table names / permissions.")
        return
    print("\nRaw ClickHouse row (first result):")
    print(json.dumps({k: _clean(v) for k, v in dict(rows[0]).items()}, ensure_ascii=False, indent=2, default=str))

    print("\nMapped 'en' offer dict (what load_offers() will actually see):")
    print(json.dumps(_row_to_lang_offer(dict(rows[0]), "en"), ensure_ascii=False, indent=2, default=str))
    print("\nMapped 'ar' offer dict:")
    print(json.dumps(_row_to_lang_offer(dict(rows[0]), "ar"), ensure_ascii=False, indent=2, default=str))
    print(
        "\n--> Compare the mapped dicts against config.OFFER_FIELD_CANDIDATES. "
        "If a field you expect is missing/empty, check the column name in "
        "_OFFER_COLUMNS and _row_to_lang_offer against your actual schema. "
        "Also check the 'partners' dict above: address/hours should read as "
        "clean plain text (no leftover <tags> or &entities;), and phone/"
        "facebook/website/instagram should only appear when partner_status "
        "== 'active'."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Fetch 3 rows and print the raw + mapped shape")
    parser.add_argument("--output", default="offers_raw.json",
                         help="Filename under config.INDEX_DIR to write. Defaults to offers_raw.json "
                              "(same file fetch_offers.py writes -- overwrites it). Pass a different "
                              "name to keep this source separate while validating it.")
    args = parser.parse_args()

    if args.debug:
        run_debug()
        return

    print("Fetching offers from ClickHouse ...")
    offers = fetch_all_offers()

    out_path = os.path.join(config.INDEX_DIR, args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(offers, f, ensure_ascii=False, indent=2)

    n_rows = len(offers) // len(config.LANGS)
    print(f"\nDone. {n_rows} offer row(s) x {len(config.LANGS)} lang(s) = {len(offers)} offer dicts saved to {out_path}")
    print("Next: python ingest/build_index.py --backend faiss   (or your usual backend/embedding-model flags)")


if __name__ == "__main__":
    main()