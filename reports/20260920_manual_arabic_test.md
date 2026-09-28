# Manual Arabic Test Report — localhost:8000 (RagEngine cascade)

Date: 2026-09-20 · Tester: opencode harness (Python) · Endpoint: `POST http://localhost:8000/api/chat`
Method: fresh session per batch, `session_id` fixed per conversation to exercise server-side session memory. Answers validated against `data/faqs.json`, `data/offers_raw.json` (ground truth), and the live-validity cut (2026-09-20).

## 0. Environment facts (matter when reading results)

- `INCLUDE_EXPIRED_OFFERS=true` in `.env` → the built index contains **9036/9048** expired offers.
- Only **12 active offers expire ≥ 2026-09-20** (6 unique: Espresso/Epress coffee, Espressolab coffee, Hamam Cleopatra Spa, Maro Sushi).
- `MEMORY_BACKEND=local`, `VECTOR_STORE_BACKEND=qdrant`, `REFERENCE_DATE` unset.
- Existing known-issue from README applies: no runtime expiry predicate; offers from 2013 are returned as "ساري حتى …".

## 1. FAQ — Arabic (12 questions) · verdict by question

| # | Question (ar) | Verdict | Notes |
|---|---------------|---------|-------|
| 1 | ما هي وفرها وازاي بتقدم خصومات كبيرة؟ | ✅ correct | Matches faq_12_about |
| 2 | إزاي أنشئ حساب جديد؟ | ✅ correct | Arabic direct answer |
| 3 | إزاي أشتري من أبلكيشن وفرها؟ | ✅ correct | |
| 4 | إزاي أستخدم الكوبون بعد الشراء؟ | ✅ correct | |
| 5 | إزاي أعمل استرجاع للكوبون؟ | ⚠️ mixed | Correct AR + full EN FAQ appended (bilingual duplication) |
| 6 | ايه طرق الدفع المتاحة؟ | ✅ correct | |
| 7 | إزاي أدفع فواتيري؟ | ❌ **fabricated** | LLM invented a fake 6-step flow ("الإشعارات"، "احفظ نسخة من الإيصال") that does NOT match FAQ; source FAQ retrieved but ignored. ~22s gen |
| 8 | إزاي أعرف حالة الفاتورة؟ | ✅ correct | |
| 9 | إزاي أشتري بطاقة هدايا؟ | ❌ wrong | Answered with an offer (Dixipay VISA card, exp 2013) instead of the gift-voucher FAQ; FAQ was in sources but not used |
| 10 | ايه سياسة الكاش باك؟ | ⚠️ correct text + **irrelevant offer card** (Dixipay 362) | card unrelated to cashback |
| 11 | وفرها بتجمع معلومات إيه عني؟ | ⚠️ correct text + **irrelevant offer card** (362) | same leak |
| 12 | إزاي أضيف/أحذف كارت بنكي؟ | ❌ **wrong + offer dump** | Direct answer failed; bot replied "أفضل العروض… حالياً" and dumped 2 offers; FAQ in sources but ignored |

Summary: 7 correct, 3 wrong, 2 correct-text-but-tainted. Two distinct bugs: (a) FAQ direct-answer gate intermittently fails on Arabic → LLM free-generates or dumps offers; (b) one specific fictional answer for bill payment.

## 2. About Waffarha (1 extra, English batch) · ✅ correct in EN

## 3. Offers — Arabic (12 questions) · relevance check

| Question | Verdict | Notes (offer ids → merchant, expiry) |
|----------|---------|--------------------------------------|
| عايز عروض مطاعم | ✅ relevant | كشري/سيلنترو/بافالو برجر/كوفتا |
| في عروض بيتزا؟ | ⚠️ mostly | بيتزا هت/ستيشن/روما present but top result is Syrian food; 2 games offers (تشكي تشيز, Rush Hub) leaked in |
| عايز عرض قهوة | ⚠️ partial | **top card is كشري (wrong)**; then سيركل كيه/TBS (right) |
| في عروض سوبر ماركت؟ | ❌ false-negative + **memory-bleed cards** | Text: "مفيش عروض من سوبر ماركت" but returned the PREVIOUS turn's coffee offers as cards |
| عروض صيدلية | ❌ false-negative + cards | same contradiction (13 pharmacy offers exist in corpus) |
| عروض سينما | ✅ relevant | dream park, رينيسانس ticket present |
| في عروض وجبات سريعة | ❌ false-negative + cards | "مفيش" although 194 pizza + McDonald's offers exist; previous entertainment offers shown |
| عروض حلويات وجاتوه | ✅ relevant | 5/6 sweet offers |
| عايز عروض ملابس | ✅ relevant | but **all offered 2016–2017 expired** |
| عروض فنادق وشهر عسل | ❌ NOT relevant | returned breakfast/coffee offers; 240 hotel offers exist in corpus but none surfaced |
| ايه العروض المتاحة دلوقتي؟ | ⚠️ mixed | varied, but several offers from **2013–2018** served as "متاحة" |
| عايز عروض من سبينيس | ✅ relevant | single expired (2019) Spinneys coupon; labeled category "الجمال والعناية" (wrong) |

Key: category relevance mostly OK (food→food, cinema→entertainment) — the bot does return offers by category, not just merchant. But top-card noise, false negatives on supermarket/pharmacy/fastfood/hotel, and heavy expired leakage dominate.

## 4. Payment methods — Arabic (6 + FAQ payload)

| Question | Verdict | Notes |
|----------|---------|-------|
| ايه طرق الدفع المتاحة؟ | ✅ correct AR | full list |
| هل في دفع كاش عند الاستلام؟ | ❌ wrong | answered with random offers (Dixipay card, car washes); never addressed "كاش" |
| في تقسيط؟ إزاي أقسط؟ | ⚠️ correct facts but **English response to Arabic question**; single unrelated offer card |
| إزاي أدفع بفودافون كاش؟ | ⚠️ correct but EN + duplicated | Arabic FAQ exists, bot answered English version twice |
| إزاي أدفع بفوري؟ | ❌ **hallucination** | Bot: "لا يوجد خيار دفع يدعى فوري" — false; top source IS the فوري FAQ ("إزاي أدفع بـ فــورى؟") |
| هل بتدعم Apple Pay? | ✅ correct AR | |
| لو سمحت عايز أعرف طريقة الدفع | ⚠️ ok but fabricated OPay walkthrough detail | "كما ذكرت في الجدول السابق" – no table existed | 

Fawry = direct contradiction with the bot's own retrieved source; installment/Vodafone answered in English despite Arabic FAQ availability. Bilingual content duplication ("refund" shows AR+EN blocks).

## 5. Irrelevant / off-topic — Arabic (5) · ❌ all fail

| Question | Bot behavior |
|----------|-------------|
| ايه لون السماء؟ | dumped VR-sky + lenses offers (embedding drift), no refusal |
| مين فاز بكأس العالم 2010؟ | dumped World Cup 2022 / random offers; no refusal |
| احكيلي قصة عن التنين | dumped Pharaonic village offers |
| ازاي أعمل الكشرى في البيت؟ | dumped carpet-cleaning + koshary offers, no recipe refusal |
| اكتبلي قصيدة شعر | dumped hair-beauty offers |

The "low-signal backstop" (clarify instead of silent dump) does **not** trigger on these Arabic inputs — every off-topic question produces a confident, unrelated offer dump.

## 6. Polite / Rude — Arabic

| Input | Bot behavior | Verdict |
|-------|-------------|---------|
| من فضلك ممكن تعملي عروض مطاعم؟ | "مفيش عندي معلومات عن ده حاليًا" + hair offers | ❌ refused even a valid request (greeting prefix hurt intent) |
| شكرا جزيلا، انت حلوة جدا | "عافاك! لو محتاج أي حاجة تانية" — good | ✅ |
| انت غبية وبطيئة | replied with confidence "نحنا سعداء بعرض…" + food/car offers; **ignores the rudeness** | ❌ |
| رد على بسرعة يا غبي | same confident offer-dump | ❌ |
| أنت لا تفهمين شيئا | same | ❌ |
| اسكت واجب فقط بالعروض | "مفيش عندنا عروض من كلام حاليًا" | ❌ nonsense |

No rudeness/politeness handling; rude inputs are treated as normal offer queries.

## 7. Multi-turn conversations — Arabic (4 convs) · ❌ follow-up anchoring broken

- **food**: restaurants ✅ → "اللي عليه خصم كبير؟" → answered **"مفيش عروض من كبير"** (treated the adjective `كبير` as a category) → pizza follow-up returns new offers not constrained to the shown list + games noise → "أحسن سعر فيهم؟" → **drifts to random 2013 offers** (lazy-boy chair, HAYA bags, jewelry) instead of comparing the shown items → "ممكن الدليفري؟" → random food offers, delivery question never answered.
- **offers**: "عايز عروض اليوم" → **"مفيش عروض من اليوم"** + previous-turn cards → "الترفيه" ok → "أرخص من 100" ok (free dental, expired 2017) → "آخد العرض ده إزاي؟" → anchored to wrong offer (جرافيتي), did not explain purchase.
- **payflow**: "إزاي أدفع لطلباتي؟" → **"حصلت مشكلة في الوصول لبيانات حسابك دلوقتي"** (identity-refusal misread as error) → "الفرق بين فوري وفودافون كاش؟" → "مفيش عندي معلومات" (both FAQs exist) → "إزاي أسترجعلهم؟" → random trampoline offer.
- **gift**: "جيفت كارد" → fashion deals, not gift vouchers → "أهديها لبنت اخويا" → random gift offers → "وإزاي تدفع فيها؟" → random offers, payment question unanswered.

Demonstrates: no reliable conversation grounding. Server-side `recent_offers` lead / cards leak between turns even across sessions at times; factual follow-ups and pronouns are dropped.

## 8. Cross-cutting bug list (for tech lead priority)

1. **Expired-offer leakage end-to-end** (worst systemic): 9036/9048 offers expired; `INCLUDE_EXPIRED_OFFERS=true` + no runtime expiry predicate → answers promote 2013–2025 offers as "ساري حتى …" (inc. 2013 Dixipay offer that keeps appearing via embedding noise). README/open reports already flag this.
2. **FAQ direct-answer gate intermittently fails on Arabic** → LLM fallback free-generates: fabricated bill-payment steps; "فوري غير موجود" contradicting top source; "لا يوجد تقسيط/كاش" style answers; saved-card query → offer dump.
3. **No-match handling self-contradiction**: text says "مفيش عروض من X" while the same response ships the previous turn's unrelated offer cards (memory/`recent_offers` resurgence into `_offer_cards`).
4. **Follow-up anchoring broken** in Arabic: adjective/aspect words (`كبير`, `اليوم`, `التاني`, "فيهم") mis-parsed as categories; comparatives ("أحسن سعر فيهم") drift to random old offers.
5. **Off-topic / rude / greet+request inputs** are not short-circuited → confident random offer dumps (backstop ineffective for these Arabic forms).
6. **Language consistency**: installments & Vodafone Cash answered in English to Arabic Q; bilingual FAQ concatenation (refund, purchase).
7. **Category label mismatches**: Spinneys coupon → "الجمال والعناية"; some cards `category` missing in JSON payload.
8. **Latency**: many turns 4–20s (LLM fallback); occasional 32s; first batch cold start ok.

## 9. Firewall note

- Answer cards frequently include **offers from 2013–2018** that a user can no longer buy — highest-impact trust issue likely to reach the tech lead first; FAQ/identity/payment flow accuracy issues second; follow-up & off-topic handling third.

Full raw per-turn JSON: `C:\Users\devza\AppData\Local\Temp\opencode\wf_faq_ar.json`, `wf_offers_ar.json`, `wf_pay_ar.json`, `wf_social_ar.json`, `wf_conversations.txt`.