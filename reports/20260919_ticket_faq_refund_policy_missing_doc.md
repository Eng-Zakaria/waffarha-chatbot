# Ticket — missing FAQ doc for the refund-policy topic

Ticket id: **FIX2-2026-09 "refund-policy topic routes, but its target doc does not exist"**

Status: OPEN. The doc-existence gap is unfixable without content work in the
index corpus (or a rule remap); it is tracked here so it is not mistaken for
"FIX 2 completed".

## Summary

The FAQ router (`core/rag_engine.py::_route_faq_topic` / `_FAQ_TOPIC_RULES`)
now routes "what is your return policy?"-family input to the `refund_policy`
topic. Routing is deterministic and correct.

But the corpus the router answers *from* contains **no `faq_refund_policy`
doc**: `_faq_topic_answer` returns `None` for that topic, so the turn falls
into the retrieval-blocked / no-retrieval fallback. The **customer-visible
answer is byte-for-byte the pre-fix fallback**:

```json
{"answer":"I couldn't find matching offers right now. Try a different merchant or category.","type":"text","offers":[],"sources":[],"suggestions":[]}
```

FIX 2's routing half is done; its answer half is not. This ticket owns the
answer half.

## Evidence

- Corpus: `data/index/BAAI__bge-m3/qdrant/docs.pkl` — 9142 docs
  (`{'text','metadata'}`, 9048 offers + 94 faq/help).
- Unique faq/help doc ids present: 47
  (`faq_1..faq_13_privacy`, `payment_*_info/_refund`, `purchasing_status_1..12`).
- `_FAQ_TOPIC_RULES` has **49 rules**; doc-existence check (`faq_rules_check4.py`):
  **48/49 target ids exist; the single missing target is `refund_policy -> faq_refund_policy`**
  (`related=False`, i.e. hard-mapped to a doc id that is not in the corpus).
- Routing check (in-repo `tests/unit/test_faq_routing.py::test_refund_policy_singular_routing` PASSED):
  the singular "return policy" phrasings resolve to topic `refund_policy`.

## How to interpret (observed vs inferred)

- OBSERVED: routing resolves to `refund_policy`; the doc id `faq_refund_policy`
  is absent from the corpus; the live answer is the fallback string above.
- The doc-content itself cannot be verified: it does not exist to verify.

## Options

1. **Add `faq_refund_policy` to the corpus** (correct fix) — write the refund /
   return-policy answer as an FAQ doc and rebuild the index. Requires product
   (policy) sign-off on the answer text.
2. **Remap the rule target** to an existing doc-id. No suitable existing doc was
   found in the 47 present ids (scanned; payment refunds under
   `payment_*_refund` are a different topic than a policy/how-to refund answer).

Recommended: option 1. Until resolved, the failure mode is a *safe* generic
negative (no wrong answer, no fabricated policy) — but the user does not get the
policy they asked for.

Not addressed by this ticket: nothing. It is scoped to the missing doc only.
Routing, freshness, and evidence changes are tracked in the Phase-5/FIX reports.