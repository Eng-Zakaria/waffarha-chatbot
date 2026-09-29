# LLM comparison: local vs Gemini vs pollinations vs assistant

Date: 2026-09-29 · Branch: `exp/free-cloud-llm` · Index: `BAAI/bge-m3` + FAISS

## What was compared

Same 6 queries (4 EN, 2 AR — offer search, FAQ, refusal, dialectal
follow-up) through `RagEngine.answer()` over the **same index**, three
generation backends, plus a fourth column — my own answers (this assistant,
acting as the LLM), written from the **same retrieved sources** and the FAQ
ground truth in `data/faqs.json`:

| Backend | Model | Key/signup? | Median latency* |
|---|---|---|---|
| local (Ollama) | `qwen2.5:3b-instruct` | no (runs on this machine) | ~0.05 s shortcut / ~12 s generated |
| gemini | `gemini-2.5-flash` | free key (AI Studio) | ~0.3 s shortcut / ~3 s generated |
| pollinations | `openai` (alias) | **none at all** | ~0.04 s shortcut / ~9–28 s generated |
| assistant (me) | Muse Spark | n/a (reference) | n/a |

\* Shortcut = deterministic path (faceted/FAQ-direct/refusal), no LLM call.
Raw evidence: `eval/local_vs_cloud_20260929.json`; my answers:
`eval/assistant_reference_20260929.json`.

## Per-query findings

**Q1 — "What breakfast offers do you have under 200 EGP?" (all generated).**
All three backends returned the *same two offer cards* (Sira 85 EGP,
Espressolab 85 EGP) — only the intro line differed in tone
("We have some great deals…" / "Certainly!" / "Here are a few options…").
My reference adds the caveat the bots omit: neither card is explicitly a
*breakfast* offer (coffee/pastries), so the answer is budget-correct but
category-loose. Latency: local 14.1 s, gemini 3.0 s, pollinations 9.1 s.

**Q2 — "Show me KFC offers" (deterministic refusal).** Identical across all
three + my reference: no KFC in the catalog. Correct — refusal is the right
behavior here, and it needs no LLM.

**Q3/Q4 — coupon-use + payment-methods FAQs (deterministic direct answers).**
Byte-identical across backends. Two warts, both backend-independent:
(1) the answer echoes the *question* as its first line ("How do I use my
purchased coupon?\nAfter payment…") — a rendering wart worth stripping;
(2) my reference is the same content minus that echo.

**Q5 — "عندك عروض بيتزا إيه؟" (deterministic refusal, score 0.000).**
All backends refused — but probing the index shows **115 pizza docs exist**
(Pizza Time, Chicken Planet, Chicking, Spaghi…). This is a **retrieval gap**,
not an empty catalog: Egyptian-dialect "عندك … إيه؟" scores 0.000 against
real pizza offers. My reference refuses too (zero context = refuse), but the
action item is retrieval-side: dialectal offer-query coverage for common
categories. Repro probe: scan `data/index/BAAI__bge-m3/faiss/docs.pkl` for
`pizza`/`بيتزا`.

**Q6 — "إزاي أستلم الأوردر بتاعي؟" (all generated, weak context).**
Retrieval returned off-topic payment FAQs only. Behaviors diverged sharply:
- **local qwen-3b (9.4 s): hallucinated.** Invented specifics ungrounded in
  context — named the "فرصة" payment method unprompted, gave generic steps
  as fact, asked a clarifying question. Fluent Egyptian-flavored Arabic,
  factually unreliable.
- **gemini (first run): mojibake.** Returned `Ù„ÙŠØ³` — latin-1-decoded
  Arabic. Root cause: `requests` guessing encoding on a charset-less
  response; **fixed** in `core/llm_providers.py` (`_parse_json_body` /
  `_decode_line` decode `resp.content` as UTF-8; regression tests included).
  Post-fix rerun: clean MSA abstention directing to support. Correct.
- **pollinations (27.9 s): honest abstention** in good MSA, same verdict as
  fixed gemini. Slowest, but the most truthful cloud answer on this query.
- **assistant (reference):** grounded answer from `faq_4` delivery steps +
  `faq_2` coupon location — hotline, coupon numbers, address, cash delivery
  fee. What the bots *should* have synthesized from ground truth.

## Aggregate local-vs-assistant differences

1. **Grounding discipline.** On weak context (Q6) I abstain-or-stay-grounded;
   local qwen-3b confabulates specifics. This is the single biggest quality
   gap: a 3B local model fills gaps with fluent fiction.
2. **Dialect.** Local qwen speaks Egyptian-flavored Arabic (matches the
   audience); gemini/pollinations default to MSA; my reference uses Egyptian
   dialect deliberately. For this product, dialect match matters.
3. **Formatting.** Card rendering is deterministic (identical across
   backends); only intro lines vary. FAQ answers echo the question
   (backend-independent wart).
4. **Latency.** Local generation ~3–5× slower than Gemini on CPU (14 s vs
   3 s on Q1); pollinations is slowest and flakiest (9–28 s + observed
   500/402 outages from this network).
5. **Refusals.** Knowledge-cutoff refusals (Q2, Q5) never reach any LLM —
   identical everywhere by construction. Good design; Q5's trigger is a
   retrieval miss, not a model decision.

## No-signup options (asked separately — verdicts)

- **pollinations.ai anonymous tier: works, no key, no account** — wired as
  `LLM_PROVIDER=pollinations` on this branch and used in this run. Caveats
  (all observed first-hand): flaky 500/402s, only the `openai` alias works
  anonymously (old `mistral`/`qwen`/`deepseek`/`llama` aliases retired),
  separate `system` role rejected (provider folds it into the user turn),
  slow. Fine for zero-friction smoke tests; not for real evals.
- **Gemini free tier: needs only the free AI Studio key** (already in this
  repo's `.env`), far more reliable. Recommended for the actual comparison
  work (`eval/compare_local_vs_cloud.py`).
- **Z AI (Zhipu GLM): not wired** — its API needs a registered key (free
  account; new accounts typically include free credits — confirm current
  terms on their site). The provider interface makes it ~30 lines to add
  once you have a key; say the word and I'll add it the same way.

## Bugs / follow-ups filed by this run

1. ~~Cloud Arabic mojibake~~ — **fixed** (`_parse_json_body`, `+3` tests).
2. Q5 pizza retrieval gap (115 docs, score 0.000) — retrieval-side, open.
3. FAQ answers echo the question (Q3/Q4) — rendering wart, open.
4. Qdrant embedded folder lock when a second process opens the index —
   worked around with `--backend faiss`; worth a `--backend` note in server
   docs, open.
