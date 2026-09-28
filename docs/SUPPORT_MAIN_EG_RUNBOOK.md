# main_eg live-DB runbook (customer-service phase)

Date: 2026-09-27. Status: code-ready, awaiting live `main_eg` credentials.

## 1. What changed today (no live DB needed)

- `core/config.py`: added `ch_table(name)` + `SUPPORT_QUERIES_ENABLED` + `SUPPORT_ORDER_LIMIT`.
  All SQL now resolves `CLICKHOUSE_DATABASE` dynamically. Tomorrow is a pure `.env` switch.
- `catalog/catalog_queries.py`, `personal/personal_queries.py`,
  `ingestion/sources/*`: replaced every hardcoded `main.` with the configured DB.
- New `support/support_queries.py` (`SupportQueryService` + `is_support_query`):
  user-scoped lookups across `fct_coupons`, `fct_bp_orders`, `fct_medical_orders`,
  `fct_trip_orders`, `fct_gift_vouchers`, `gift_refunds`, `trip_refunds`,
  plus voucher-sn / order-id targeted lookup. All parameterized, all `user_id`-scoped.
- New agent tool `agent/tools/support_tools.py` (`support_lookup`), registered in
  `core/app.py`, advertised in `agent/planner.py` (`support` intent).
- `core/rag_engine.py`: support branch runs before personal for
  refund/order-id/voucher queries; generic `my coupons` still uses personal.
- `.env.example`: `CLICKHOUSE_DATABASE=main_eg`, `SUPPORT_QUERIES_ENABLED=true`.
- Tests: `tests/unit/test_support_queries.py` (8 tests, fake client, no DB).

Verified: `test_support_queries` + `test_agent_cascade_tools` + `test_identity` +
`test_session_manager` = 55 passed. Full suite has 4 pre-existing failures in
`test_agent_engine` / `test_faceted_routing` / `test_faq_routing` / `test_intent_router`
(files not touched by this change) + Qdrant lock errors when a server holds the index.

## 2. Tomorrow: live access checklist (15 min)

1. Copy `.env.example` -> `.env`, set:
   `CLICKHOUSE_HOST/PORT/SECURE/USERNAME/PASSWORD`, `CLICKHOUSE_DATABASE=main_eg`,
   `PERSONAL_QUERIES_ENABLED=true`, `IDENTITY_BACKEND=auth` (+ `AUTH_SIGNING_SECRET`),
   `CATALOG_QUERIES_ENABLED=true`, `SUPPORT_QUERIES_ENABLED=true`.
2. Read-only DB user. Verify: `python ingestion/sources/fetch_offers_clickhouse.py --debug`
   then `--steps sources --debug` equivalents for partners/type-price.
3. Smoke-test per-user paths with a real user id (via `get_customers_offers.py --user-id X`
   and direct `SupportQueryService` calls for bp/medical/trip/gift + refunds).
4. Rebuild index: `python ingestion/refresh.py` (or `update_index.ps1`).
5. Run `py -m pytest tests/unit/test_support_queries.py -q` + manual bilingual checks:
   offers, voucher status, refund status, failed-order reason, app/policy fallback.
6. Watch logs for `support query failed` / `personal query failed` / `catalog query failed`.

## 3. Intent -> table map (for CS triage)

- Offers/prices/merchants: `dim_offers` + `dim_partners` + `dim_type_price` (catalog).
  Live main_eg (2026-09-28): 8998 offers, 4505 active, **658 live** (expire future).
  Titles live in `offer_brief_*` — `mobile_offer_title_*` is NULL for ~all live rows.
  Geography: `place_id` -> `dim_place` (41 places incl Online Store / All Egypt).
- My vouchers: `fct_coupons` (5.1M rows; voucher_sn like `66983-74108`,
  status_v2 Used/Refund/Expired/Paid/Canceled). coupon_status codes: 3=Used,
  6=Refund, 1=Paid-or-Expired (split by dates), 10=Expired, 5=Canceled.
  Codes 11/13 (In Refund Process / Paymob) have zero rows — no in-progress signal.
- Bill problems: `fct_bp_orders` (1.3M: paid/failed/pending/cancelled/refunded/collected).
  **No `total` column** — use `requested_amount`/`amount`. Top failure reasons are
  provider messages (biller unavailable, amount validation) — quotable in replies.
  Top services: We ADSL, Etisalat, Vodafone Recharge, Orange.
- Medical: `fct_medical_orders` (1680 rows; labs dominate: Mokhtabar, Alborg;
  Cairo/Giza/Alex). Statuses initiated/dispensed/pending-payment/refunded/...
- Trips: `fct_trip_orders` (1782; **pending = unpaid**, 1627/1627 unpaid — never
  call it a failure). Providers Gobus/Bluebus/Otobeas; 13 refund reasons with
  deduction flags; trip_refunds statuses pending/approved/rejected.
- Gifts: `fct_gift_vouchers.status` is clean strings (Used/Refunded/Paid/Cancelled).
  Raw-table codes: orders 1/2=paid, 3=awaiting payment, 4=cancelled, 5=paid;
  vouchers 1=valid, 2=used, 3=refunded, 5=cancelled; refunds 1=completed,
  3=cancelled, 5=pending. WARNING: status-3 (unpaid) orders already have vouchers
  issued — never tell the user an unpaid order's voucher is valid.
- Policy text: `dim_offers.offer_fineprint_*` (HTML, strip tags) +
  `dim_payment_methods.refund_message_*` (22 active methods: Fawry, wallets,
  Bank Cards/Installment, vodafone cash, aman, valU...).
- Dev testing: `STATIC_TEST_USER_ID=55` has **zero rows** in main_eg. Use a real
  uid with cross-vertical data, e.g. `1695845` (641 coupons + bp + gift refunds).

## 4. Personalization + wait/escalate rules (measured 2026-09-28)

- DB size: ~13.7M rows across the 19 core tables (byte counts not granted to
  the read user; counts are the sizing story). Biggest: coupons 11M,
  bill orders 2.6M.
- Troubleshooting ("my last voucher isn't working"): `last_coupon_full`
  joins coupon -> offer (`receiving_method`, `not_refundable`) -> partner
  (phone/address) and diagnoses Used / Refunded / Cancelled / Expired / Valid.
- Refund timelines with wait-vs-escalate (thresholds from live quantiles):
  gift refunds complete median 4h / p90 15h -> `GIFT_REFUND_WAIT_DAYS=2`;
  medical paid->refunded median 2h / p90 4.5d -> `MEDICAL_REFUND_WAIT_DAYS=5`
  (currently advisory); trip pendings in the data are 118-420 days old ->
  `TRIP_REFUND_ESCALATE_DAYS=30`, anything older escalates with its reference.
  No invented ETAs: wait messages cite "normal processing window" + a check-back
  date; stuck cases get an explicit human-escalation message with reference id.
- Location: `dim_place` (41 places, EN+AR verified in code) + `dim_location`
  (278 neighborhoods, understanding-only — no FK to offers). Live catalog is
  ~all Cairo, so non-Cairo answers honestly redirect to live cities
  (`live_places()`). Merchant+city queries keep the merchant with a city filter.
- Time: `ending_soon` (7d, live-tested: 7 rows) / `starting_soon`
  (usually ~1 row — honest empty otherwise).
- Anti-hallucination fixes proven live: over-fetch before the live-expiry
  filter (top-discount rows are 2013 relics that used to blank whole pages),
  ال-tolerant Arabic city matching, pending≠failed, unpaid-gift-voucher trap.

## 5. Safety rules (do not relax)

- `user_id` comes only from `core/identity.py`. Voucher/order ids from text are
  filters inside that scope, never replacements.
- No user input interpolated into SQL (named parameters only).
- No auth -> deterministic "please log in" answer, never another user's rows.
- One vertical failing must not fail the whole turn (see `lookup_order` per-vertical try/except).
