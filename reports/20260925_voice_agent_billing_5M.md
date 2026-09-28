# Voice Agent Billing at 5M Users/Mo — Huawei Self-Host vs Paid
Date: 2026-09-25 | Unit: USD, excl. VAT. Huawei GPU list price not public — pull exact figure from console calculator; proxies below.

## 0. Assumptions

- 5M users/mo = 166k/day. All become voice users (worst case; if only 10% use voice, divide by 10).
- Per user/mo: 1 session, 2 turns. STT input 20s. TTS output: short 150 chars / base 450 chars (2-3 Waffarha offers) / rich 900 chars.
- TTS volume: 0.75B / **2.25B** / 4.5B chars/mo. 1M chars ≈ 1,250 min ≈ 21 hrs.
- STT volume: 5M × 20s = 1.67M min = 27,778 hrs/mo.
- Realtime agent (holds session 60s/user): 5M min/mo. Async voice-note mode (burst TTS only): GPU held seconds, not minutes — 10-20x cheaper infra.
- Huawei Egypt region live; billing modes pay-per-use / yearly-monthly / spot (spot ≈ 50-70% off, no SLA).

## 1. Option A — Chatterbox self-host on Huawei VM (you operate, $0 license)

Stack: `Chatterbox-EG (oddadmix) TTS + Faster-Whisper/Cohere-ar STT + qwen2.5:3b LLM + LiveKit/Pipecat` on Huawei ECS + GACS GPU + OBS/EVS + EIP/bandwidth.

| Item | Spec | Proxy cost |
|---|---|---|
| GPU workers (TTS+STT) | T4/L4/A10 class GACS. Async mode: 2-4 GPUs handle peak (~20-30 conc.). Realtime mode (60s hold): 20-35 GPUs for ~350 conc. | ~$400-900/GPU/mo pay-per-use; ~$200-400 spot; yearly-monthly ~20-30% off. Async fleet **$1k-3k/mo**, realtime fleet **$10k-30k/mo** |
| CPU orchestration (LiveKit, API, RAG) | 2-4× C7 ECS 8vCPU/16GB + LB + EIP | ~$100-400/mo |
| Storage/bandwidth | OBS voice cache, EVS, 2-5TB egress EG | ~$100-500/mo |
| Licenses | Chatterbox MIT $0, Whisper $0, LiveKit OSS $0 | $0 |
| Ops | 0.25-0.5 DevOps + monitoring | headcount, not vendor bill |

**Total A: ~$1.5k-4k/mo async, ~$11k-31k/mo realtime at 5M users.** Marginal cost per extra 1M chars ≈ $0 (just power/compute you already rent). Cheapest at scale; you own watermark (PerTh), latency tuning, brand lexicon, no SLA.

## 2. Option B — Other open models self-hosted on cloud (you operate)

Same Huawei fleet, different VRAM/appetite. License $0 except XTTS-v2 (CPML — legal risk) and F5-Arabic forks (CC BY-NC — no commercial).

- Fish Speech S2 (24GB rec): needs A10/L4 24GB — same fleet as above, ~10-20% more $/stream than Chatterbox.
- Qwen3-TTS 0.6B/1.7B (8/16GB): fits T4, cheapest GPU-wise, but needs EG/SA fine-tune + diacritizer you build.
- Whisper-turbo INT8 STT: ~1.6GB, collapses onto same GPUs (100+ streams/L40S).

**Total B: ≈ A ±20%.** Pick on quality (Chatterbox-EG wins EG in your lab), not price.

## 3. Option C — Fully managed (vendor operates, pays per use)

### C1. Hyperscaler TTS/STT (pay per char/min, they run GPUs)

| Volume | Azure $16/1M (commit $7.50) | Google N2 $16 / Chirp $30 | AWS Neural $16 | Eleven v3 $100 / Flash $50 |
|---|---|---|---|---|
| 0.75B (short) | $12,000 ($5,625) | $12,000 / $22,500 | $12,000 | $75,000 / $37,500 |
| **2.25B (base)** | **$36,000 ($16,875)** | **$36,000 / $67,500** | **$36,000** | **$225,000 / $112,500** |
| 4.5B (rich) | $72,000 ($33,750) | $72,000 / $135,000 | $72,000 | $450,000 / $225,000 |

STT on top (27,778 hrs): Soniox $0.12/hr = **$3.3k**; Scribe $0.40 = $11.1k; Deepgram stream $0.46 = $12.8k; Azure $1.00 = $27.8k. LLM extra: $0.003-0.02/min small model = $15k-100k at 5M min.

**Total C1 base case: ~$55k-75k/mo (Azure/Google + Soniox + small LLM), up to ~$250k+ on ElevenLabs v3.**

### C2. Creator / Arabic-first SaaS (per char/min + seat)

- Fish Audio $15/1M: 2.25B = $33,750. Murf API ~$10/1M + $19-99 seats: 2.25B = ~$22,500. SILMA ~$28/1M equiv: 2.25B = ~$63,000. Nabrah ~$73/1M: 2.25B = ~$164,000. Chatterbox hosted Pro $1.90/1M: 2.25B = ~$4,275 (225 × $19) — cheapest managed, no GPU ops.
- Mean paid ≈ $35/1M → base 2.25B = **$78,750/mo** TTS alone. Median $22/1M → $49,500.

### C3. Agent platforms all-in ($/min covers STT+LLM+TTS+orchestration, telephony extra)

5M min/mo: Retell $0.06 = **$300k**; mid $0.15 = **$750k**; Vapi/Bland high $0.33 = **$1.65M**. + telephony/SIP $0.015-0.03/min = $75k-150k + numbers. WhatsApp voice-notes = async (can't use realtime per-min pricing; bill as C1 bursts instead).

## 4. Headline comparison at base case (5M users, 450 chars each, 20s STT each)

| Path | Who runs GPUs | TTS 2.25B | STT 27.8k hrs | Infra/platforms | **Total/mo** |
|---|---|---|---|---|---|
| A Chatterbox × Huawei async | you | $0 | $0 | $1.5k-4k | **$1.5k-4k** |
| A realtime (60s hold) | you | $0 | $0 | $11k-31k | **$11k-31k** |
| B other open self-host | you | $0 | $0 | same ±20% | **≈ A** |
| C1 Azure + Soniox + LLM | vendor | $36,000 | $3,300 | $15k-100k LLM | **~$55k-140k** |
| C1 Azure commit | vendor | $16,875 | $3,300 | same LLM | **~$35k-120k** |
| C2 mean $35/1M | vendor | $78,750 | incl. or +STT | seats | **~$80k-110k** |
| C2 Eleven v3 | vendor | $225,000 | +$11k | seats | **~$240k+** |
| C3 agent $0.06-0.33/min | vendor | incl. | incl. | +$75k telco | **$375k-1.8M** |

Savings A vs mean: **~$75k/mo at base case (~95%+ off license)**. A pays back a $400/GPU fleet after ~11M chars/mo vs mean, ~4M vs ElevenLabs.

## 5. Recommendation for 5M

1. Ship async voice-reply + voice-note mode on **A (Huawei + Chatterbox-EG)** — $1.5k-4k/mo, EG quality win, data stays in Huawei EG region.
2. Keep **Azure PAYG ($16/1M, 500k free) + Soniox** as fallback for SA overflow and SLA cover — $0 idle, absorbs spikes without sizing fleet for peak.
3. Do not put 5M realtime minutes on C3 before PMF — $375k+/mo. Prove retention on async first; move realtime to A (own fleet) where marginal minute ≈ $0.002-0.006 vs $0.06-0.33 managed (10-50x gap).
4. Confirm Huawei GACS T4/L4 monthly + spot + egress in console (prices vary by AZ, not published statically); re-run §4 with your exact per-GPU figure and measured chars/user from week-1 logs. If voice adoption is 10% not 100%, divide all totals by 10.
