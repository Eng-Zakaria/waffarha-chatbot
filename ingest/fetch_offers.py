"""
Pulls all offers from the Waffarha mobile API across every category id and
both languages, paginating each (category, lang) pair until an empty page
is returned. Saves everything to data/offers_raw.json.

Usage:
    # First, sanity-check the response shape (writes data/sample_response.json, prints keys):
    python ingest/fetch_offers.py --debug

    # Then pull everything:
    python ingest/fetch_offers.py
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402


def fetch_page(section_id: int, page: int, lang: str) -> dict:
    body = {
        **config.OFFERS_API_BASE_BODY,
        "limit": config.PAGE_LIMIT,
        "page": page,
        "section_id": section_id,
        "lang": lang,
        "security_key": get_security_key(),
    }
    max_retries = getattr(config, "MAX_RETRIES", 3)
    backoff = getattr(config, "RETRY_BACKOFF", 2)
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(config.OFFERS_API_URL, json=body, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < max_retries:
                sleep_sec = backoff ** (attempt - 1)
                time.sleep(sleep_sec)
    raise last_err


def extract_offer_list(raw_json: dict) -> list:
    """Find the list of offers inside the response, trying known keys,
    then falling back to the first list-of-dicts value found."""
    for key in config.OFFERS_LIST_CANDIDATES:
        val = raw_json.get(key)
        if isinstance(val, list):
            return val
        # sometimes nested one level deeper, e.g. {"data": {"offers": [...]}}
        if isinstance(val, dict):
            for subkey in config.OFFERS_LIST_CANDIDATES:
                subval = val.get(subkey)
                if isinstance(subval, list):
                    return subval
    # fallback: first list of dicts anywhere in the top-level response
    for val in raw_json.values():
        if isinstance(val, list) and (len(val) == 0 or isinstance(val[0], dict)):
            return val
    return []


def run_debug():
    section_id = config.CATEGORY_IDS[0]
    print(f"Fetching one debug page for section_id={section_id}, lang=en ...")
    raw = fetch_page(section_id, 1, "en")
    out_path = os.path.join(config.INDEX_DIR, "sample_response.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"Saved raw response to {out_path}")
    print("\nTop-level keys:", list(raw.keys()))
    offers = extract_offer_list(raw)
    print(f"Auto-detected {len(offers)} offer(s) in this page.")
    if offers:
        print("\nKeys of first offer object:")
        print(list(offers[0].keys()))
        print("\nSample offer:")
        print(json.dumps(offers[0], ensure_ascii=False, indent=2))
        print(
            "\n--> Compare these keys against OFFER_FIELD_CANDIDATES in config.py "
            "and add any missing key names to the candidate lists there."
        )
    else:
        print(
            "--> Could not auto-detect the offers list. Open data/sample_response.json "
            "and check OFFERS_LIST_CANDIDATES in config.py."
        )


def run_full_fetch():
    all_offers = {}  # keyed by (offer_id or fallback index) to dedupe across langs/pages
    total_requests = 0

    for lang in config.LANGS:
        for section_id in config.CATEGORY_IDS:
            page = 1
            while page <= config.MAX_PAGES_PER_SECTION:
                try:
                    raw = fetch_page(section_id, page, lang)
                except requests.RequestException as e:
                    print(f"  [section_id={section_id} lang={lang} page={page}] request failed: {e}")
                    break

                total_requests += 1
                offers = extract_offer_list(raw)

                if not offers:
                    break

                for offer in offers:
                    offer_id = None
                    for k in config.OFFER_FIELD_CANDIDATES["id"]:
                        if k in offer:
                            offer_id = offer[k]
                            break
                    dedup_key = f"{offer_id}_{lang}" if offer_id is not None else f"{section_id}_{page}_{lang}_{len(all_offers)}"
                    offer["_section_id"] = section_id
                    offer["_lang"] = lang
                    all_offers[dedup_key] = offer

                print(f"  section_id={section_id} lang={lang} page={page}: +{len(offers)} offers (total so far: {len(all_offers)})")

                if len(offers) < config.PAGE_LIMIT:
                    # short page => last page
                    break

                page += 1
                time.sleep(config.SLEEP_BETWEEN_REQUESTS)

    out_path = os.path.join(config.INDEX_DIR, "offers_raw.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(list(all_offers.values()), f, ensure_ascii=False, indent=2)

    print(f"\nDone. {total_requests} requests made. {len(all_offers)} unique offers saved to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Fetch a single page and dump its raw shape")
    args = parser.parse_args()

    if args.debug:
        run_debug()
    else:
        run_full_fetch()