# Local ClickHouse Setup & Testing Guide

This guide covers how to set up a local ClickHouse instance, sync data from the remote production ClickHouse, and test the query layer directly.

## 📋 Overview

The Waffarha chatbot uses a **hybrid architecture**:
- **RAG/Embeddings** (FAISS/Chroma): For FAQ, semantic search, fuzzy matching
- **ClickHouse (Live)**: For catalog queries (merchant, price, ranking, location, tags), personal queries

The local ClickHouse setup lets you:
1. Develop/test without hitting production
2. Modify data for testing edge cases
3. Test the catalog query layer directly
4. Compare RAG vs ClickHouse approaches

---

## 🚀 Quick Start

### 1. Start Local ClickHouse

```bash
# Start local ClickHouse container
docker compose -f docker-compose.local-clickhouse.yml up -d

# Verify it's running
docker compose -f docker-compose.local-clickhouse.yml logs -f clickhouse-local
```

The local instance runs on:
- **HTTP**: `http://localhost:8123`
- **Native**: `localhost:9000`
- **User**: `default` (no password)
- **Database**: `main`

### 2. Sync Data from Remote to Local

```bash
# Sync all tables (default)
python ingest/sync_clickhouse.py

# Sync specific tables only
python ingest/sync_clickhouse.py --tables dim_offers,dim_partners

# Dry run (see what would be synced)
python ingest/sync_clickhouse.py --dry-run
```

This will:
- Connect to remote ClickHouse (using `.env` credentials)
- Connect to local ClickHouse (localhost:8123)
- Create tables locally with same schema
- Copy all active data (filtered for active offers/partners)

### 3. Test Catalog Queries Directly

```bash
# Single query
python test_catalog_clickhouse.py "KFC offers"
python test_catalog_clickhouse.py "عروض كنتاكي"
python test_catalog_clickhouse.py "under 200 EGP"
python test_catalog_clickhouse.py "cheapest offer"

# Interactive mode
python test_catalog_clickhouse.py --interactive

# Run predefined test suite
python test_catalog_clickhouse.py --run-tests

# Test direct SQL
python test_catalog_clickhouse.py --direct-sql
```

### 4. Compare RAG vs ClickHouse

```bash
# Compare both approaches on eval queries
python compare_rag_vs_clickhouse.py --queries queries.json --output comparison_results.json

# Limit to first 20 queries
python compare_rag_vs_clickhouse.py --limit 20
```

### 5. Run Full Integration Test

```bash
# Does sync + catalog tests + RAG tests
python test_clickhouse_integration.py
```

---

## 📁 Files Created

| File | Purpose |
|------|---------|
| `docker-compose.local-clickhouse.yml` | Local ClickHouse container config |
| `clickhouse-config.xml` | ClickHouse server configuration |
| `ingest/sync_clickhouse.py` | Sync remote → local ClickHouse |
| `ingest/build_index_incremental.py` | Incremental index builder |
| `test_catalog_clickhouse.py` | Direct ClickHouse catalog query tester |
| `compare_rag_vs_clickhouse.py` | RAG vs ClickHouse comparison |
| `test_clickhouse_integration.py` | End-to-end integration test |

---

## 🔧 Detailed Usage

### sync_clickhouse.py

```bash
# Full sync (all tables)
python ingest/sync_clickhouse.py

# Custom local connection
python ingest/sync_clickhouse.py --local-host localhost --local-port 8123 --local-user default --local-password ""

# Specific tables
python ingest/sync_clickhouse.py --tables dim_offers,dim_partners,fct_coupons

# Larger batch size for faster transfer
python ingest/sync_clickhouse.py --batch-size 50000
```

**Tables synced (in order):**
1. `dim_partners` - Merchant/partner info
2. `dim_offers` - Offers (active only, non-deleted)
3. `dim_type_price` - Pricing tiers
4. `dim_purchasing_status` - Order statuses
5. `dim_payment_methods` - Payment methods
6. `fct_coupons` - User coupons (for personal queries)

### test_catalog_clickhouse.py

```bash
# Test specific queries
python test_catalog_clickhouse.py "KFC offers" --lang en
python test_catalog_clickhouse.py "عروض كنتاكي" --lang ar
python test_catalog_clickhouse.py "cheapest offer" --lang en
python test_catalog_clickhouse.py "ارخص عرض" --lang ar

# Interactive mode (recommended for exploration)
python test_catalog_clickhouse.py --interactive

# In interactive mode:
#   > KFC offers
#   > lang ar
#   > عروض كنتاكي
#   > session my_session_123
#   > compare KFC and McDonald's
#   > quit
```

**Supported query types:**
| Type | Example (EN) | Example (AR) |
|------|--------------|--------------|
| Merchant | `KFC offers` | `عروض كنتاكي` |
| Price ceiling | `under 200 EGP` | `تحت 200 جنيه` |
| Price floor | `over 500` | `فوق 500` |
| Price range | `between 100 and 300` | `من 100 لحد 300` |
| Cheapest | `cheapest offer` | `ارخص عرض` |
| Most expensive | `most expensive offer` | `أغلى عرض` |
| Highest discount | `highest discount` | `أعلى خصم` |
| Location | `where is KFC` | `أين كنتاكي` |
| Tags | `Ramadan offers` | `عروض رمضان` |
| Comparison | `compare KFC and McDonald's` | `قارن بين كنتاكي وماكدونالدز` |
| Multi-merchant | `KFC and McDonald's offers` | `عروض كنتاكي وماكدونالدز` |
| Merchant + price | `KFC price` | `كنتاكي بكام` |

### build_index_incremental.py

```bash
# Incremental update (only new/modified docs)
python ingest/build_index_incremental.py --backend faiss

# Force full rebuild
python ingest/build_index_incremental.py --backend faiss --force-full

# Different embedding model
python ingest/build_index_incremental.py --backend faiss --embedding-model intfloat/multilingual-e5-small
```

This maintains a manifest (`index_manifest.json`) tracking document hashes to detect changes.

### compare_rag_vs_clickhouse.py

```bash
# Full comparison on eval queries
python compare_rag_vs_clickhouse.py --queries queries.json --output results.json

# Quick test (first 10 queries)
python compare_rag_vs_clickhouse.py --limit 10
```

Outputs detailed comparison including:
- Latency comparison
- Answer quality indicators
- Expected ID match rates
- Per-category breakdown

---

## 🧪 Testing Workflow

### For Catalog Query Development

1. **Start local ClickHouse**
   ```bash
   docker compose -f docker-compose.local-clickhouse.yml up -d
   ```

2. **Sync latest data**
   ```bash
   python ingest/sync_clickhouse.py
   ```

3. **Test query interactively**
   ```bash
   python test_catalog_clickhouse.py --interactive
   ```

4. **Debug intent detection**
   ```bash
   python test_catalog_clickhouse.py "your query" --verbose
   ```

5. **Check raw SQL if needed**
   ```bash
   python test_catalog_clickhouse.py --direct-sql
   ```

### For RAG vs ClickHouse Evaluation

1. **Run comparison**
   ```bash
   python compare_rag_vs_clickhouse.py --queries queries.json --output comparison.json
   ```

2. **Analyze results**
   ```bash
   # View summary
   cat comparison.json | jq '.summary'
   
   # View per-query details
   cat comparison.json | jq '.results[] | {query, category, rag: .rag.latency_s, ch: .clickhouse.latency_s}'
   ```

### For Incremental Index Updates

After syncing new data from ClickHouse:

```bash
# Regenerate offers_raw.json from local ClickHouse
python ingest/fetch_offers_clickhouse.py

# Incremental index update
python ingest/build_index_incremental.py --backend faiss
```

---

## 🔍 Debugging Tips

### Check Local ClickHouse Data

```bash
# Connect to local ClickHouse
docker exec -it clickhouse-local clickhouse-client

# Or via HTTP
curl "http://localhost:8123/?query=SELECT+count()%3DFROM+main.dim_offers"
```

### Verify Sync Worked

```bash
# Check table counts
python -c "
import clickhouse_connect
client = clickhouse_connect.get_client(host='localhost', port=8123, username='default', password='', database='main')
for t in ['dim_offers', 'dim_partners', 'dim_type_price', 'fct_coupons']:
    try:
        cnt = client.query(f'SELECT COUNT() FROM main.{t}').named_results()[0]['count']
        print(f'{t}: {cnt:,}')
    except Exception as e:
        print(f'{t}: ERROR - {e}')
"
```

### Compare Remote vs Local

```bash
# Run same query on both
python -c "
import config
remote = config.get_clickhouse_client()
local = __import__('clickhouse_connect').get_client(host='localhost', port=8123, username='default', password='', database='main')

query = 'SELECT COUNT() FROM main.dim_offers WHERE deleted_at IS NULL AND offer_status = \"active\"'
print('Remote:', remote.query(query).named_results()[0]['count'])
print('Local: ', local.query(query).named_results()[0]['count'])
"
```

---

## 🐛 Common Issues

### "Connection refused" to local ClickHouse
```bash
# Check container is running
docker ps | grep clickhouse-local

# Check logs
docker logs clickhouse-local

# Restart
docker compose -f docker-compose.local-clickhouse.yml restart
```

### "Table doesn't exist" after sync
```bash
# Re-run sync (it creates tables automatically)
python ingest/sync_clickhouse.py
```

### Sync is slow
```bash
# Increase batch size
python ingest/sync_clickhouse.py --batch-size 50000

# Or sync only specific tables
python ingest/sync_clickhouse.py --tables dim_offers,dim_partners
```

### Out of memory during sync
```bash
# Reduce batch size
python ingest/sync_clickhouse.py --batch-size 5000
```

---

## 📊 Architecture Notes

### Why Local ClickHouse?

| Aspect | Remote (Production) | Local (Development) |
|--------|---------------------|---------------------|
| **Access** | Read-only | Read/Write |
| **Data Freshness** | Real-time | Synced on demand |
| **Testing** | Risky | Safe |
| **Modifications** | Not allowed | Full control |
| **Performance** | Production SLAs | Limited by hardware |

### Query Routing Logic

In `rag_engine.py`, queries are routed:

```
User Query
    │
    ├─► Personal? (my coupons, my orders) ──► ClickHouse (fct_coupons)
    │
    ├─► Catalog? (offers, prices, merchants) ──► ClickHouse (dim_offers, dim_partners)
    │
    ├─► FAQ? (how to, what is, policies) ──► RAG/Embeddings (FAISS)
    │
    └─► Other ──► RAG/Embeddings (fallback)
```

The `is_catalog_query()` function in `ingest/catalog_queries.py` determines if a query should hit ClickHouse directly.

---

## 🔄 CI/CD Integration

Add to your pipeline:

```yaml
# .github/workflows/test-catalog.yml
- name: Start local ClickHouse
  run: docker compose -f docker-compose.local-clickhouse.yml up -d

- name: Wait for ClickHouse
  run: sleep 10

- name: Sync test data (subset)
  run: python ingest/sync_clickhouse.py --tables dim_offers,dim_partners --batch-size 1000

- name: Run catalog tests
  run: python test_catalog_clickhouse.py --run-tests

- name: Compare RAG vs ClickHouse
  run: python compare_rag_vs_clickhouse.py --limit 20 --output comparison.json
```

---

## 📚 Related Documentation

- [DEVELOPMENT.md](DEVELOPMENT.md) - Development workflow
- [ARCHITECTURE.md](ARCHITECTURE.md) - RAG pipeline architecture
- [EVALUATION.md](EVALUATION.md) - Evaluation harness
- [DOCKER.md](DOCKER.md) - Docker deployment guide

---

## 🆘 Support

| Issue | Contact |
|-------|---------|
| ClickHouse sync | Data Engineering |
| Catalog queries | Backend Team |
| RAG vs ClickHouse routing | ML Team |
| Docker/local setup | Platform Team |