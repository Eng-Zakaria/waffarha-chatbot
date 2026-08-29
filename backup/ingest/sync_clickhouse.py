"""
Sync data from remote ClickHouse to local ClickHouse (running on Docker).

This script:
1. Connects to remote ClickHouse (configured in .env)
2. Connects to local ClickHouse (running in Docker on localhost:8123)
3. Syncs the relevant tables for the chatbot: dim_offers, dim_partners, dim_type_price,
   dim_purchasing_status, dim_payment_methods, fct_coupons

Usage:
    python ingest/sync_clickhouse.py [--tables TABLE1,TABLE2] [--local-port 8123]

Requires:
    - Remote ClickHouse credentials in .env (CLICKHOUSE_HOST, CLICKHOUSE_PORT, etc.)
    - Local ClickHouse running on Docker (default: localhost:8123, default user, no password)
"""
import argparse
import sys
import os
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import clickhouse_connect


# Tables to sync (in dependency order - dim tables first, then fact tables)
SYNC_TABLES = [
    "dim_partners",
    "dim_offers",
    "dim_type_price",
    "dim_purchasing_status",
    "dim_payment_methods",
    "fct_coupons",
]

# Columns to exclude from sync (system columns, etc.)
EXCLUDE_COLUMNS = {
    "dim_offers": ["deleted_at"],  # We'll handle this in WHERE clause
}


def get_remote_client():
    """Get connected client to remote ClickHouse."""
    return config.get_clickhouse_client()


def get_local_client(host: str = "localhost", port: int = 8123,
                     username: str = "default", password: str = "",
                     database: str = "main", secure: bool = False):
    """Get connected client to local ClickHouse."""
    return clickhouse_connect.get_client(
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
        secure=secure,
    )


def get_table_schema(client, table: str) -> List[Dict]:
    """Get column names and types for a table."""
    query = f"DESCRIBE TABLE {config.CLICKHOUSE_DATABASE}.{table}"
    rows = client.query(query).named_results()
    return rows


def table_exists(client, table: str) -> bool:
    """Check if a table exists in the database."""
    query = f"""
    SELECT name FROM system.tables
    WHERE database = '{config.CLICKHOUSE_DATABASE}' AND name = '{table}'
    """
    result = client.query(query).named_results()
    return len(result) > 0


def create_table_local(local_client, table: str, schema: List[Dict]):
    """Create table in local ClickHouse matching remote schema."""
    columns = []
    for col in schema:
        col_name = col['name']
        col_type = col['type']
        # Skip excluded columns
        if table in EXCLUDE_COLUMNS and col_name in EXCLUDE_COLUMNS[table]:
            continue
        columns.append(f"`{col_name}` {col_type}")

    # Build CREATE TABLE statement
    # Use MergeTree engine with appropriate ORDER BY
    order_by = "offer_id" if table == "dim_offers" else \
               "part_id" if table == "dim_partners" else \
               "offer_id" if table == "dim_type_price" else \
               "coupon_id" if table == "fct_coupons" else \
               tuple(col['name'] for col in schema if col['name'].endswith('_id'))[0] if any(c['name'].endswith('_id') for c in schema) else "tuple()"

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {config.CLICKHOUSE_DATABASE}.{table} (
        {', '.join(columns)}
    ) ENGINE = MergeTree()
    ORDER BY {order_by}
    """

    print(f"  Creating table {table}...")
    local_client.command(create_sql)


def get_row_count(client, table: str, where: str = "") -> int:
    """Get row count for a table."""
    query = f"SELECT COUNT(*) as cnt FROM {config.CLICKHOUSE_DATABASE}.{table}"
    if where:
        query += f" WHERE {where}"
    result = client.query(query).named_results()
    return result[0]['cnt']


def sync_table(remote_client, local_client, table: str, batch_size: int = 10000,
               where_remote: str = "", where_local: str = "") -> Dict:
    """Sync a single table from remote to local."""

    print(f"\n=== Syncing {table} ===")

    # Get schema from remote
    schema = get_table_schema(remote_client, table)
    print(f"  Remote schema: {len(schema)} columns")

    # Create table locally if needed
    if not table_exists(local_client, table):
        create_table_local(local_client, table, schema)
        print(f"  Created table {table} locally")
    else:
        print(f"  Table {table} already exists locally")

    # Build column list (exclude system columns)
    columns = [col['name'] for col in schema
               if not (table in EXCLUDE_COLUMNS and col['name'] in EXCLUDE_COLUMNS[table])]
    col_list = ", ".join(f"`{c}`" for c in columns)

    # Get remote count
    remote_count = get_row_count(remote_client, table, where_remote)
    print(f"  Remote rows to sync: {remote_count:,}")

    if remote_count == 0:
        print(f"  No rows to sync for {table}")
        return {"table": table, "synced": 0, "remote_count": 0}

    # Get local count before sync
    local_count_before = get_row_count(local_client, table, where_local)
    print(f"  Local rows before sync: {local_count_before:,}")

    # Clear local table (full refresh for simplicity - could be made incremental)
    print(f"  Truncating local table...")
    local_client.command(f"TRUNCATE TABLE {config.CLICKHOUSE_DATABASE}.{table}")

    # Fetch and insert in batches
    offset = 0
    total_synced = 0

    while offset < remote_count:
        query = f"""
        SELECT {col_list}
        FROM {config.CLICKHOUSE_DATABASE}.{table}
        {f'WHERE {where_remote}' if where_remote else ''}
        LIMIT {batch_size} OFFSET {offset}
        """

        print(f"  Fetching batch {offset//batch_size + 1} (offset {offset})...")
        result = remote_client.query(query)
        rows = result.named_results()

        if not rows:
            break

        # Insert into local
        data = [tuple(row[col] for col in columns) for row in rows]
        local_client.insert(
            f"{config.CLICKHOUSE_DATABASE}.{table}",
            data,
            column_names=columns
        )

        total_synced += len(rows)
        offset += batch_size
        print(f"  Inserted {len(rows)} rows (total: {total_synced:,})")

    # Verify
    local_count_after = get_row_count(local_client, table, where_local)
    print(f"  Local rows after sync: {local_count_after:,}")

    return {
        "table": table,
        "synced": total_synced,
        "remote_count": remote_count,
        "local_before": local_count_before,
        "local_after": local_count_after
    }


def main():
    parser = argparse.ArgumentParser(description="Sync ClickHouse data from remote to local")
    parser.add_argument("--tables", default=",".join(SYNC_TABLES),
                        help=f"Comma-separated list of tables to sync. Default: {','.join(SYNC_TABLES)}")
    parser.add_argument("--local-host", default="localhost", help="Local ClickHouse host")
    parser.add_argument("--local-port", type=int, default=8123, help="Local ClickHouse HTTP port")
    parser.add_argument("--local-user", default="default", help="Local ClickHouse username")
    parser.add_argument("--local-password", default="", help="Local ClickHouse password")
    parser.add_argument("--local-database", default="main", help="Local ClickHouse database")
    parser.add_argument("--local-secure", action="store_true", help="Use HTTPS for local ClickHouse")
    parser.add_argument("--batch-size", type=int, default=10000, help="Batch size for data transfer")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without doing it")

    args = parser.parse_args()

    tables = [t.strip() for t in args.tables.split(",")]

    print("=" * 60)
    print("ClickHouse Remote -> Local Sync")
    print("=" * 60)
    print(f"Remote: {config.CLICKHOUSE_HOST}:{config.CLICKHOUSE_PORT} ({config.CLICKHOUSE_DATABASE})")
    print(f"Local:  {args.local_host}:{args.local_port} ({args.local_database})")
    print(f"Tables: {', '.join(tables)}")
    print(f"Batch size: {args.batch_size}")
    print()

    if args.dry_run:
        print("DRY RUN - no data will be transferred")
        return

    # Connect to remote
    print("Connecting to remote ClickHouse...")
    try:
        remote_client = get_remote_client()
        print("  Connected successfully")
    except Exception as e:
        print(f"  Failed to connect to remote: {e}")
        sys.exit(1)

    # Connect to local
    print(f"Connecting to local ClickHouse ({args.local_host}:{args.local_port})...")
    try:
        local_client = get_local_client(
            host=args.local_host,
            port=args.local_port,
            username=args.local_user,
            password=args.local_password,
            database=args.local_database,
            secure=args.local_secure
        )
        print("  Connected successfully")
    except Exception as e:
        print(f"  Failed to connect to local: {e}")
        print("\nMake sure local ClickHouse is running:")
        print("  docker run -d -p 8123:8123 -p 9000:9000 --name clickhouse-local clickhouse/clickhouse-server:latest")
        sys.exit(1)

    # Ensure database exists locally
    try:
        local_client.command(f"CREATE DATABASE IF NOT EXISTS {args.local_database}")
        print(f"  Database '{args.local_database}' ready")
    except Exception as e:
        print(f"  Warning: Could not create database: {e}")

    # Sync each table
    results = []
    for table in tables:
        try:
            # Special handling for dim_offers - only sync active/non-deleted
            where_remote = ""
            if table == "dim_offers":
                where_remote = "deleted_at IS NULL AND offer_status = 'active'"
            elif table == "dim_partners":
                where_remote = "status = 'active'"

            result = sync_table(remote_client, local_client, table,
                              batch_size=args.batch_size,
                              where_remote=where_remote)
            results.append(result)
        except Exception as e:
            print(f"  ERROR syncing {table}: {e}")
            import traceback
            traceback.print_exc()
            results.append({"table": table, "error": str(e)})

    # Summary
    print("\n" + "=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    for r in results:
        if "error" in r:
            print(f"  {r['table']}: FAILED - {r['error']}")
        else:
            print(f"  {r['table']}: {r['synced']:,} rows synced (remote: {r['remote_count']:,})")

    total_synced = sum(r.get('synced', 0) for r in results if 'synced' in r)
    print(f"\nTotal rows synced: {total_synced:,}")

    # Verify key tables have data
    print("\nVerifying key tables...")
    for table in ["dim_offers", "dim_partners", "fct_coupons"]:
        if table in tables:
            try:
                cnt = get_row_count(local_client, table)
                print(f"  {table}: {cnt:,} rows locally")
            except Exception as e:
                print(f"  {table}: ERROR - {e}")


if __name__ == "__main__":
    main()