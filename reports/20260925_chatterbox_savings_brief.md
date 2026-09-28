# Chatterbox Savings vs Paid TTS 
Scope: TTS only, Arabic EG/SA/MSA.

## 1. What "Chatterbox" costs

**A. Self-hosted (the saving): MIT license, $0 software.**
- Weights: `ResembleAI/chatterbox` + EG fine-tune `oddadmix/chatterbox-egyptian-v0` (lab winner).
- Requirements: 6GB VRAM min (RTX 3060 12GB), RTX 3090/4090 recommended; 4GB works but slow (your GTX 1650 Ti test). CPU works, not for prod.
- Real cost = infra only: existing GPU → ~$5-30/mo electricity; cloud spot G4/G5 → $0.30-0.50/hr; dedicated L40S/A10 → $300-800/mo or $3-6k capex. $0/char marginal.
- Caveats: PerTh watermark on all outputs, no SLA, you own diacritization/normalization, Franco `3→ع` mapping, brand lexicon (`وفرها` not "wafferha"), and eval.

**B. Hosted Chatterbox (if you don't self-host):**
- `Chatterbox AI` hosted: Free 50k chars/mo (400ms), Pro $19/mo for 10M chars ($1.90/1M effective, 200ms). Enterprise custom <120ms.
- `Resemble AI` platform: Flex $0 pay-as-you-go (credits never expire) → Team $350/mo ($280 annual) → Business $1,000/mo ($800 annual) → Enterprise custom (80% volume discounts, SSO, on-prem). Note: Resemble no longer publishes per-sec TTS rates (old $0.0005/sec unverifiable Jul-2026) ask sales. Third-party `Replicate chatterbox-pro` = pay-per-second alternative.

## 2. Paid vendor plans (per 1M chars unless noted)

| Vendor | Plans | Effective $/1M | Free tier |
|---|---|---|---|
| **ElevenLabs** | Free 10k credits; Starter $6/30k; Creator $22/121k ($11 first mo); Pro $99/600k; Scale $299/1.8M; Business $990/6M; Enterprise custom. Annual = pay 10/12 (~17% off). API PAYG: $0.10/1k (v2/v3) = **$100/1M**, $0.05/1k (Flash/Turbo) = **$50/1M**. Overage $0.12-0.30/1k. Credits shared across TTS/STT/dubbing. | $165-200 in-quota; $50-100 PAYG | 10k/mo, non-commercial |
| **Azure Speech** | F0 free → PAYG Neural $16, HD $22 (was $30), Custom $24 ($48 HD). Commitment (pay minimum whether used or not): $960/80M = $12, $3,840/400M = $9.60, $15,000/2B = $7.50. | $16 PAYG → $7.50 top commit | 500k/mo, never expires |
| **Google Cloud** | Standard/WaveNet $4; Neural2 $16; Chirp3-HD $30; Studio $160; Instant Custom $60. Gemini-TTS token billing separate. | $4-160 | Standard 4M, WaveNet/Neural2 1M |
| **AWS Polly** | Standard $4; Neural $16; Generative $30; Long-form $100. | $4-100 | 5M/1M for 12mo |
| **Murf** | Free limited → Creator $29/mo ($19 annual) → Business $99/mo ($66 annual) → Enterprise custom. API from ~$0.01/1k = **~$10/1M**. Quotas minute-based, studio editor included. MSA only, no EG/SA split. | ~$10 API + seat fee | Limited free |
| **Fish Audio** | Free 8k credits (~7 min) → API **$15/1M** bytes (~chars). Subscriptions ~$15-66/mo tiers. | $15 | 7 min/mo |
| **SILMA v2** | From **$0.025/min** ≈ **~$28/1M** (at ~54k chars/hr). KSA Najdi + MSA, native AR-EN, on-prem option. | ~$28 | Verify trial |
| **Nabrah.ai (SA)** | TTS SAR 0.24-0.31/1k = **~$64-83/1M**; STT SAR 0.06-0.078/min; Agent SAR 0.60-0.78/min. 20k free credits. | ~$64-83 | 20k credits |

PlayHT excluded (shut Dec-2025 after Meta acquisition).

## 3. Average paid price (like-for-like neural)

Comparable neural tier (Azure 16 + Google N2 16 + Polly N 16 + Eleven Flash 50 + Eleven v2/v3 100 + Fish 15 + Murf API 10 + SILMA 28 + Nabrah 73 + Chirp HD 30) / 10 = **~$35/1M mean, ~$22/1M median**. Rule: 1M chars ≈ 1,250 min ≈ 21 hrs speech (at ~800 chars/min).

## 4. Savings math

Formula: `monthly saving = (paid $/1M × volume_M) − chatterbox_infra`.

| Volume | Paid cost at mean $35/1M | ElevenLabs $100/1M | Azure $16/1M | Chatterbox self-host (own GPU) | Saving vs mean |
|---|---|---|---|---|---|
| 1M/mo | $35 | $100 | $16 | ~$5-30 | **~$5-30 net (≈50-100% off license)** |
| 10M/mo | $350 | $1,000 | $160 | ~$5-30 | **~$320-345 (∼95%)** |
| 50M/mo | $1,750 | $5,000 | $800 | ~$5-30 (or $300-800 cloud GPU) | **~$950-1,745** |
| Hosted Pro alternative | — | — | — | $19 for 10M ($1.90/1M) | 95% off mean without GPU ops |

Break-even on a $400/mo cloud GPU: ~11M chars vs mean, ~4M vs ElevenLabs, ~25M vs Azure PAYG, ~52M vs Azure $7.50 commit. With a GPU you already own: positive from month 1.

Worked example from reviews: 450k-char audiobook = $67.50 ElevenLabs vs $2.30 electricity self-host.

## 5. When Chatterbox does / doesn't save

- **Saves most:** high volume + EG dialect (Chatterbox-EG is your only ★5 EG in lab tests; Azure/others need lexicon work) + data-residency/on-prem needs + 24/7 agent minutes.
- **Doesn't save:** <500k chars/mo (Azure/Google free tiers cover you for $0 with zero ops), need for SLA/support/phone numbers (keep 1 paid fallback), SA-only Najdi authenticity (SILMA still leads), or no GPU/ops capacity (use $19 hosted Pro instead).
- **Recommendation for Waffarha:** self-host Chatterbox-EG as primary EG TTS (sunk GPU = near-$0), keep Azure PAYG ($16/1M, 500k free) as fallback + SA cover, re-evaluate at >25M chars/mo whether Azure commit ($7.50) beats cloud-GPU cost. Confirm Resemble/Chatterbox-Pro hosted rates in writing before budgeting hosted path — published pages conflict as of Sep-2026.
