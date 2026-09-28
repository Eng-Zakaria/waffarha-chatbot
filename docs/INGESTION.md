# Ingestion: The Single ClickHouse-Based Pipeline

This is the canonical description of how Waffarha chatbot indexes stay fresh.
Since **Phase 4** there is exactly **one** source of truth — ClickHouse — and
exactly **one** entry point — `ingestion/refresh.py`. The legacy mobile-API
scrape (`ingestion/sources/fetch_offers.py`) and the remote→local replication
tool (`ingestion/sync_clickhouse.py`, `ingestion/clickhouse_sync/`) were
**removed**.

## Pipeline overview

```
ClickHouse                    data/ (raw JSON, the "stage")            data/index/<model>/<backend>/
──────────────────────►  ───────────────────────────────►  ──────────────────────────────────────►
main.dim_offers         fetch_offers_clickhouse  → offers_raw.json         build_index(_incremental).py
main.dim_partners       fetch_partners_clickhouse → partners/partners.json  → docs.pkl / store files
main.dim_type_price     fetch_type_price          → type_prices.json          + index_manifest.json
main.dim_purchasing_status fetch_purchasing_status → faqs_purchasing_status.json
main.dim_payment_methods  fetch_payment_methods   → faqs_payment_methods.json
(static sources)                                                    faqs.json
```

Each fetch script is a thin `SELECT` that writes one file under
`config.INDEX_DIR` (`data/`). The **full builder**
(`ingestion/loaders/build_index.py`) embeds everything; the **incremental
updater** (`ingestion/loaders/build_index_incremental.py`) re-embeds only docs
whose stable content hash changed, reusing an existing store.

## The one command

```bash
# Fetch every source from ClickHouse, then (re)build the index
python ingestion/refresh.py

# Same, but skip the ClickHouse fetch and rebuild from existing data/
python ingestion/refresh.py --skip-fetch

# Dry-run: print the lineage/freshness report (source files + hashes, target
# index dir, manifest status, stale detection); writes/changes nothing
python ingestion/refresh.py --plan
```

`refresh.py` runs the steps in order and **stops on the first failure**, so a
broken fetch never produces a half-fresh index. Every successful run appends a
lineage entry to `data/refresh_log.json` (rolling, capped at 50 entries) —
source-file hashes, build name, duration, and where the manifest was written.

### Step selection

```bash
python ingestion/refresh.py --steps sources faqs type-prices partners build
python ingestion/refresh.py --skip-fetch --steps build            # default with --skip-fetch
python ingestion/refresh.py --steps incremental                   # incremental update only
```

Available steps: `sources` (offers → `offers_raw.json`), `partners`
(→ `partners/partners.json`), `faqs` (payment-methods + purchasing-status
FAQs), `type-prices`, `build` (full rebuild), `incremental` (diff update).
`type-prices`/`partners` are kept as separate steps so you can skip them when
they haven't changed.

### Other flags

| Flag | Meaning |
|------|---------|
| `--backend` | vector backend (default `config.VECTOR_STORE_BACKEND`) |
| `--embedding-model` | embedding model (default `config.EMBEDDING_MODEL`) |
| `--batch-size` | rows per fetch page (default 10000) |
| `--force-full` | rebuild even if fingerprint/corpus hashes are unchanged |
| `--max-rows`, `--out-dir` | sample-build / test flags (only useful offline) |
| `--log` | refresh-log path (default `data/refresh_log.json`) |

## The index manifest

Every built index directory contains `index_manifest.json`, written by both
builders from the shared module `ingestion/loaders/index_manifest.py`
(schema `1`):

```jsonc
{
  "schema_version": 1,
  "embedding_model": "BAAI/bge-m3",        // canonical key
  "backend": "qdrant",
  "created_at": "...", "updated_at": "...",
  "built_by": "build_index.py",
  "build_config": {
    "include_expired_offers": false,        // IMPORTANT: must be false in production
    "offer_active_values": ["active"]       // OFFER_ACTIVE_VALUES at build time
  },
  "source": {
    "files": {                              // lineage: per source file
      "faqs.json": { "bytes": 1234, "sha256": "…" },
      "offers_raw.json": { "bytes": 5678, "sha256": "…" }
      // absent files are omitted
    },
    "corpus_hash": "sha256 over sorted 'stable_id|doc_hash' pairs"
  },
  "stats": { "total_docs": 9142, "faq_count": 152, "offer_count": 8990 },
  "documents": {
    "offer:123:en": { "hash": "…", "metadata": {…}, "index_position": 0 },
    "faq:42:ar":   { "hash": "…", "metadata": {…}, "index_position": 1 }
  }
}
```

Semantics;

- `embedding_model` / `backend` are **canonical** — the manifest is the record
  of what actually sits in that directory. `RagEngine` loads the index, compares
  these against what it was asked for, and **logs a warning** (never raises) on
  mismatch.
- `corpus_hash` changes **iff** any document changed (content or identity) — the
  incremental updater uses per-document hashes for partial rebuilds and refreshes
  `corpus_hash` afterwards.
- `build_config.include_expired_offers` is captured at build time so you can tell
  at a glance whether a stale-looking corpus is an index problem or a build-flags
  problem.

## Cadence / freshness policy

| Source | When to refresh | Notes |
|--------|-----------------|-------|
| `dim_offers` → `offers_raw.json` | **Daily** (or on each deploy) | Active-only SELECT; includes `INCLUDE_EXPIRED_OFFERS` filtering at build time |
| `dim_partners` → `partners.json` | **Daily** | Cheap; pair with offers |
| `dim_type_price` | **Daily** | Pricing tiers; stale tiers show in catalog answers |
| `dim_purchasing_status`, `dim_payment_methods` | On change | Only used to build those two FAQ files |
| `faqs.json` (static) | On edit | Content-edited directly; fingerprint catches it |

Production builds **must** leave `INCLUDE_EXPIRED_OFFERS=false` (default off,
configured in `core/config.py`). The current local `.env` sets it to `true`;
the manifest's `build_config` records which value a given index was built with.

Suggested production cron:

```bash
# 03:00 daily — fetch + full/hybrid rebuild
0 3 * * *  cd /srv/waffarha-chatbot && PYTHONIOENCODING=utf-8 python ingestion/refresh.py >> data/refresh.log 2>&1
```

For a fleet with live traffic, schedule the fetch-only steps
(`--steps sources faqs type-prices`) through the day and run `--skip-fetch`
builds in off-peak hours.

## Diagnostics

- `ingestion/refresh.py --plan` — lineage report: which source files the corpus
  would use, their hashes, whether they changed since the last run, the target
  index dir, and current manifest stats (or "no manifest").
- `data/refresh_log.json` — append-only run history (capped at 50): start/end
  times, per-step durations, source-file fingerprints, built-by, manifest path.
- `ingestion/sources/get_customers_offers.py` — **diagnostic only** (not part of
  the pipeline): query the live per-user coupon data for debugging "my coupons"
  type questions.

## Retired (Phase 4)

- `ingestion/sources/fetch_offers.py` — mobile-API scrape. Its only consumers of
  `WAFFARHA_SECURITY_KEY`, `OFFERS_API_URL`, `OFFERS_API_BASE_BODY`,
  `CATEGORY_IDS` were removed from `core/config.py`.
- `ingestion/sync_clickhouse.py` / `ingestion/clickhouse_sync/` — remote→local
  replication (was already 100% dead code). Local development points
  `CLICKHOUSE_HOST` at a local instance, or imports data with ClickHouse's
  `remoteSecure()` (see `docs/CLICKHOUSE_LOCAL_SETUP.md`).