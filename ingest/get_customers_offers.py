"""
get_customer_offers.py

Fetch a customer's own coupons/offers from ClickHouse by user_id.

Install dependency:
    pip install clickhouse-connect

Usage:
    python get_customer_offers.py --user-id 12345
    python get_customer_offers.py --user-id 12345 --source coupons_new
    python get_customer_offers.py --user-id 12345 --limit 20 --status 1
"""

import argparse
import json
import sys
from datetime import datetime, date
from decimal import Decimal

import clickhouse_connect

# ---------------------------------------------------------------------------
# Connection config — replace with your real values, or better, load from
# environment variables / a secrets manager instead of hardcoding.
# ---------------------------------------------------------------------------

CH_CONFIG = {
    "host": "localhost",      # e.g. "your-clickhouse-host.cloud"
    "port": 8443,             # 8443 for https, 8123 for http (adjust as needed)
    "username": "default",
    "password": "",           # do not hardcode in production
    "database": "main",
    "secure": True,           # set False if not using TLS
}


def get_client():
    """Create a ClickHouse client connection."""
    return clickhouse_connect.get_client(**CH_CONFIG)


def fetch_offers_fct_coupons(client, user_id: int, limit: int = 50, status: int | None = None):
    """
    Query the newer fact table (fct_coupons) joined with dim_offers / dim_partners.
    """
    query = """
        SELECT
            c.coupon_id,
            c.voucher_sn,
            c.created_at,
            c.active_at,
            c.expire_at,
            c.coupon_status,
            c.total_price,
            c.discount,
            o.offer_brief_en,
            o.offer_brief_ar,
            p.part_name_en
        FROM main.fct_coupons AS c
        LEFT JOIN main.dim_offers AS o ON c.offer_id = o.offer_id
        LEFT JOIN main.dim_partners AS p ON c.partner_id = p.part_id
        WHERE c.user_id = {user_id:Int32}
        {status_clause}
        ORDER BY c.created_at DESC
        LIMIT {limit:UInt32}
    """
    params = {"user_id": user_id, "limit": limit}
    status_clause = ""
    if status is not None:
        status_clause = "AND c.coupon_status = {status:Int32}"
        params["status"] = status

    query = query.format(status_clause=status_clause)
    result = client.query(query, parameters=params)
    return result.result_rows, result.column_names


def fetch_offers_coupons_new(client, user_id: int, limit: int = 50, status: int | None = None):
    """
    Query the legacy table (coupons_new) — uses users_id instead of user_id,
    and no direct partner join available in this schema.
    """
    query = """
        SELECT
            c.coupon_id,
            c.voucher_sn,
            c.created,
            c.active_date_time,
            c.expire_date,
            c.coupon_status,
            c.total_price,
            c.discount,
            o.offer_brief_en,
            o.offer_brief_ar
        FROM main.coupons_new AS c
        LEFT JOIN main.dim_offers AS o ON c.offer_id = o.offer_id
        WHERE c.users_id = {user_id:Int32}
        {status_clause}
        ORDER BY c.created DESC
        LIMIT {limit:UInt32}
    """
    params = {"user_id": user_id, "limit": limit}
    status_clause = ""
    if status is not None:
        status_clause = "AND c.coupon_status = {status:Int32}"
        params["status"] = status

    query = query.format(status_clause=status_clause)
    result = client.query(query, parameters=params)
    return result.result_rows, result.column_names


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def rows_to_dicts(rows, columns):
    return [dict(zip(columns, row)) for row in rows]


def main():
    parser = argparse.ArgumentParser(description="Fetch a customer's coupons/offers by user_id.")
    parser.add_argument("--user-id", type=int, required=True, help="Customer's user_id")
    parser.add_argument(
        "--source",
        choices=["fct_coupons", "coupons_new"],
        default="fct_coupons",
        help="Which table to query (default: fct_coupons, the newer fact table)",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max rows to return")
    parser.add_argument("--status", type=int, default=None, help="Filter by coupon_status code")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    try:
        client = get_client()
    except Exception as e:
        print(f"Failed to connect to ClickHouse: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.source == "fct_coupons":
            rows, columns = fetch_offers_fct_coupons(client, args.user_id, args.limit, args.status)
        else:
            rows, columns = fetch_offers_coupons_new(client, args.user_id, args.limit, args.status)
    except Exception as e:
        print(f"Query failed: {e}", file=sys.stderr)
        sys.exit(1)

    data = rows_to_dicts(rows, columns)

    if not data:
        print(f"No offers found for user_id={args.user_id} in {args.source}.")
        return

    indent = 2 if args.pretty else None
    print(json.dumps(data, default=_json_default, indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()