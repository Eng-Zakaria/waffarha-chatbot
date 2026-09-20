"""Compare last-fetched offers_raw.json snapshot against current ClickHouse
main.dim_offers data. Focus on expired items rate.

Run:  python tests/repro/check_clickhouse_freshness.py
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from core import config  # noqa: E402

NOW = datetime.datetime.now()


def ch_query(sql: str):
    client = config.get_clickhouse_client()
    return [dict(r) for r in client.query(sql).named_results()]


def main():
    print("=" * 78)
    print("CLICKHOUSE DATA FRESHNESS CHECK")
    print(f"  Local time: {NOW.isoformat()}")
    print(f"  ClickHouse host: {config.CLICKHOUSE_HOST}")
    print("=" * 78)

    # --- 1. Current ClickHouse counts ---
    print("\n--- CURRENT CLICKHOUSE STATE ---")

    rows = ch_query("SELECT count() AS total FROM main.dim_offers WHERE deleted_at IS NULL")
    ch_total = rows[0]["total"]
    print(f"  dim_offers (deleted_at IS NULL): {ch_total}")

    rows = ch_query("""
        SELECT count() AS total FROM main.dim_offers
        WHERE deleted_at IS NULL AND offer_status = 'active'
    """)
    ch_active_status = rows[0]["total"]
    print(f"  dim_offers (active status):      {ch_active_status}")

    rows = ch_query(f"""
        SELECT count() AS total FROM main.dim_offers
        WHERE deleted_at IS NULL AND offer_expire_date < '{NOW.strftime('%Y-%m-%d')}'
    """)
    ch_expired = rows[0]["total"]
    print(f"  dim_offers (expire_date < now):  {ch_expired}")

    rows = ch_query(f"""
        SELECT count() AS total FROM main.dim_offers
        WHERE deleted_at IS NULL AND offer_expire_date >= '{NOW.strftime('%Y-%m-%d')}'
    """)
    ch_not_expired = rows[0]["total"]
    print(f"  dim_offers (expire_date >= now): {ch_not_expired}")

    rows = ch_query("SELECT count() AS total FROM main.dim_offers WHERE deleted_at IS NOT NULL")
    ch_deleted = rows[0]["total"]
    print(f"  dim_offers (deleted):            {ch_deleted}")

    # Expired by offer_status
    rows = ch_query(f"""
        SELECT offer_status, count() AS cnt
        FROM main.dim_offers
        WHERE deleted_at IS NULL AND offer_expire_date < '{NOW.strftime('%Y-%m-%d')}'
        GROUP BY offer_status
        ORDER BY cnt DESC
    """)
    print("\n  Expired offers broken down by status:")
    for r in rows:
        print(f"    status={r['offer_status']}: {r['cnt']}")

    # --- 2. Local offers_raw.json snapshot ---
    print("\n--- LOCAL offers_raw.json SNAPSHOT ---")
    raw_path = os.path.join(config.INDEX_DIR, "offers_raw.json")
    if not os.path.exists(raw_path):
        print(f"  NOT FOUND: {raw_path}")
        return 1

    with open(raw_path, "r", encoding="utf-8") as f:
        offers = json.load(f)

    local_total = len(offers)
    local_offer_ids = set()
    local_active = 0
    local_expired = 0
    local_not_expired = 0
    local_no_expire = 0

    for o in offers:
        oid = o.get("offer_id")
        if oid is not None:
            local_offer_ids.add(oid)
        status = (o.get("offer_status") or "").strip().lower()
        expire = o.get("offer_expire_date")
        if status in ("active", "1", "true"):
            local_active += 1
        if expire is not None:
            try:
                exp_dt = datetime.datetime.strptime(str(expire)[:10], "%Y-%m-%d")
                if exp_dt < NOW:
                    local_expired += 1
                else:
                    local_not_expired += 1
            except (ValueError, TypeError):
                local_no_expire += 1
        else:
            local_no_expire += 1

    local_distinct_ids = len(local_offer_ids)
    print(f"  Total offer dicts (rows x langs): {local_total}")
    print(f"  Distinct offer_id values:         {local_distinct_ids}")
    print(f"  Status='active':                  {local_active}")
    print(f"  Expired (expire < now):           {local_expired}")
    print(f"  Not expired (expire >= now):      {local_not_expired}")
    print(f"  No parseable expire_date:         {local_no_expire}")

    # --- 3. Freshness comparison ---
    print("\n--- FRESHNESS COMPARISON ---")
    ratio_expired = ch_expired / ch_total * 100 if ch_total else 0
    print(f"  ClickHouse: {ch_expired}/{ch_total} offers expired = {ratio_expired:.1f}%")

    print(f"  Local snapshot: {local_expired}/{local_distinct_ids} distinct offers expired")

    # Check how many CH active offers are in local snapshot
    rows = ch_query("""
        SELECT DISTINCT offer_id FROM main.dim_offers
        WHERE deleted_at IS NULL AND offer_status = 'active'
    """)
    ch_active_ids = {r["offer_id"] for r in rows}
    missing_from_local = ch_active_ids - local_offer_ids
    extra_in_local = local_offer_ids - ch_active_ids

    print(f"\n  CH active offers: {len(ch_active_ids)}")
    print(f"  Local distinct IDs: {len(local_offer_ids)}")
    print(f"  Active in CH but MISSING from local snapshot: {len(missing_from_local)}")
    if missing_from_local and len(missing_from_local) <= 20:
        for oid in sorted(missing_from_local):
            print(f"    - offer_id={oid}")
    print(f"  In local snapshot but NOT active in CH (stale): {len(extra_in_local)}")

    # --- 4. Recent offers ---
    print("\n--- RECENT OFFERS (last 30 days) ---")
    rows = ch_query(f"""
        SELECT count() AS cnt FROM main.dim_offers
        WHERE deleted_at IS NULL
        AND offer_expire_date >= '{NOW.strftime('%Y-%m-%d')}'
        AND offer_expire_date < '{(NOW + datetime.timedelta(days=30)).strftime('%Y-%m-%d')}'
    """)
    ch_expiring_30d = rows[0]["cnt"]
    print(f"  Offers expiring within 30 days: {ch_expiring_30d}")

    rows = ch_query(f"""
        SELECT count() AS cnt FROM main.dim_offers
        WHERE deleted_at IS NULL
        AND offer_expire_date >= '{(NOW + datetime.timedelta(days=30)).strftime('%Y-%m-%d')}'
    """)
    ch_alive_30d = rows[0]["cnt"]
    print(f"  Offers alive > 30 days:         {ch_alive_30d}")

    # --- 5. Partner counts ---
    print("\n--- PARTNER COUNTS ---")
    rows = ch_query("""
        SELECT count() AS total FROM main.dim_partners AS p
        INNER JOIN main.dim_offers AS o ON o.part_id = p.part_id
        WHERE o.deleted_at IS NULL
    """)
    ch_partners = rows[0]["total"]
    print(f"  Partners linked to non-deleted offers: {ch_partners}")

    # Check local partners snapshot
    partners_path = os.path.join(config.INDEX_DIR, "partners", "partners.json")
    if os.path.exists(partners_path):
        with open(partners_path, "r", encoding="utf-8") as f:
            local_partners = json.load(f)
        print(f"  Local partners snapshot: {len(local_partners)} partners")
    else:
        print(f"  Local partners snapshot: NOT FOUND")

    # --- Summary ---
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    stale_pct = len(extra_in_local) / local_distinct_ids * 100 if local_distinct_ids else 0
    missing_pct = len(missing_from_local) / len(ch_active_ids) * 100 if ch_active_ids else 0
    print(f"  Expired offers in ClickHouse:    {ch_expired}/{ch_total} ({ratio_expired:.1f}%)")
    print(f"  Stale (in local but not active): {len(extra_in_local)} ({stale_pct:.1f}%)")
    print(f"  Missing from local:              {len(missing_from_local)} ({missing_pct:.1f}%)")
    print(f"  Expiring within 30 days:         {ch_expiring_30d}")

    if ratio_expired > 20:
        print(f"\n  ⚠ WARNING: {ratio_expired:.1f}% of offers are expired — index may be polluted")
    if stale_pct > 5:
        print(f"  ⚠ WARNING: {stale_pct:.1f}% of local offers are stale — refresh needed")
    if missing_pct > 5:
        print(f"  ⚠ WARNING: {missing_pct:.1f}% of active offers missing from local — refresh needed")

    print(f"\n  Last refresh: check data/refresh_log.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
