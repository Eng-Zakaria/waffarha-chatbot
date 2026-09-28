# Arabic Voice Options Report — Egyptian / Saudi / MSA + Mixed AR-EN
Date: 2026-09-24 | Project: waffarha-chatbot | Scope: TTS + STT + Voice Agent

## 0. Executive summary

- **No single model wins all three dialects + mixed AR-EN.** You need a per-dialect pick.
- **Qwen vs Nabra is not apples-to-apples:**
  - `Qwen3-TTS` (Alibaba, Apache-2.0) has **no official Arabic support** (10 langs: zh/en/ja/ko/de/fr/ru/pt/es/it). Arabic works only via community fine-tunes (Egyptian `itshamdi404/Egy_Arabic_Qwen3-TTS`, Saudi `vadimbelsky/qwen3-TTS-KSA`). You self-host, you own data/latency, but you must build diacritization + eval yourself.
  - `Nabra` is ambiguous: **Nabarati Nabra v3 (Egypt, 15+ dialects, TTS/dubbing)** vs **Nabrah.ai (Riyadh, Saudi-dialect voice agents)**. Both are closed SaaS: immediate EG/SA voices, per-char/min pricing, no on-prem, no control over latency/residency.
  - Your current `qwen2.5:3b-instruct` is a **text LLM**, not a voice model. Do not confuse it with Qwen3-TTS or Qwen2-Audio.
- **Best commercial today for Waffarha (EG-first, SA-second, mixed queries like `عايز عروض بيتزا هت`, `3rod kentaki`):**
  - TTS: `Azure Neural (ar-EG Salma/Shakir + ar-SA Zariyah/Hamed)` for catalogue coverage + SLA, or `Hakim / SILMA / Munsit Faseeh / Hamsa` for dialect authenticity + native code-switch. `ElevenLabs` for best MSA naturalness, dialect via community voices only.
  - STT: `ElevenLabs Scribe v2` (best measured AR-EN code-switch: ~13% WER on hard EG set) vs `Deepgram Nova-3 Arabic ar-EG` (cheapest streaming + keyterms) vs `Cohere Transcribe Arabic` (best open-weights, Apache-2.0) vs `Munsit` (best dialect coverage, sovereign deploy).
  - Agent: cascade `Streaming STT -> fast LLM -> streaming TTS` on **LiveKit Agents** or **Pipecat**. Do not use end-to-end omni models (Moshi/Mini-Omni/Qwen-Omni/GLM-4-Voice) for EG/SA production — EN/ZH-only research demos.
- **Blockers are not only hardware.** Ranked: (1) dialectal + noisy + code-switched data, (2) eval (no vendor WER transfers to your phone audio), (3) telco (EG NTRA / SA CITC, WhatsApp is async, not realtime), (4) cost (LLM dominates per-minute), (5) hardware (only matters if self-hosting).
- **Can open-source reach Munsit-or-better?** Yes for a narrow domain (food/deals, 2 dialects, 1-2 brand voices) in 2-4 months with 50-200h curated audio + fine-tune + eval. No for general 25-dialect coverage (needs 10k-30k hrs). Recommended: start SaaS, collect calls, fine-tune open models, swap in.

## 1. What you asked to cover

Waffarha traffic is Egyptian Arabic + MSA + English brand names, with Saudi expansion. Observed patterns in your own history: `عايز عروض بيتزا`, `3rod kentaki 2026`, `ski egypt`, `واتس اوفر زير` (Arabizi/Franco + code-switch). STT must output Arabic script + preserve Latin brands; TTS must read `الـoffer ده valid لحد Friday؟` without foreign accent.

## 2. TTS — full option list

### 2.1 Commercial hyperscalers

| Vendor | Dialects | Mixed AR-EN | Cloning / Streaming | Pricing (2026 publ.) | Quality verdict |
|---|---|---|---|---|---|
| **Azure Speech Neural** | 20 ar locales incl. named **ar-EG (Salma/Shakir)** + **ar-SA (Zariyah/Hamed)**. Only vendor with both. HD (DragonHD) for select voices. | SSML `<lang>` tags, Dec-2024 diacritizer upgrade | Custom Neural Voice + Personal Voice, realtime SDK | Neural $15-16/1M chars (0.5M free/mo), HD $22-24/1M, Custom $24/1M + $52/compute-hr train | Best catalogue + enterprise SLA. EG/SA intelligible, slightly MSA-leaning prosody. Pick if you need compliance + support. |
| **Google Cloud TTS** | Legacy/WaveNet/Chirp3-HD = **ar-XA MSA only**. Only EG path is **Gemini-TTS ar-EG GA + ar-001 Preview** | SSML only | Instant Custom Voice | Standard/WaveNet $4/1M, Neural2 $16/1M, Chirp3-HD $30/1M, Studio $160/1M. Gemini token: $0.5-1 input + $10-20/1M audio tokens | MSA good, EG only via Gemini. Not first choice for EG/SA today. |
| **Amazon Polly** | `arb Zeina` (MSA) + `ar-AE Hala/Zayd` (Gulf). **No ar-EG, no ar-SA** | None | Brand Voice (enterprise) | Standard $4/1M, Neural $16/1M, Generative $30/1M, Long-form $100/1M | Exclude for EG/SA. |

### 2.2 Arabic-first SaaS (best fit)

| Vendor | Dialects | Mixed AR-EN | Latency / Deploy | Pricing | Quality verdict |
|---|---|---|---|---|---|
| **Hakim (tryhakim.ai)** | 15 varieties: MSA, Masri/EG, Khaleeji, Najdi, Levantine, Maghrebi + | STT Arab v2 + TTS Saree v1.3 claim native CS | 113ms p95 TTS, 90ms STT (vendor). 4 regions. Cloud only | 5k free chars, then tiered (verify sales) | Broadest EG+SA matrix. Startup claims — require blind MOS before commit. |
| **SILMA TTS v2** | `msa` + `ksa Najdi`, 8 voices. No EG model | **Native Najdi/MSA/EN one pipeline** + `<STAG_*>` tags | ~170ms TTFT, WS/SSE/REST, **on-prem avail.** | From **$0.025/min** (~$28/1M chars equiv.) | Best proven KSA + CS. Pick for Saudi agent. No EG. |
| **Nabrah.ai (SA)** | Saudi-first, 3 voices (Daniah/Norah/Abdullah) + 5s clone | Via Agents/Studio API | No SLA publ., cloud only | TTS SAR 0.24-0.31/1k chars (~$64-83/1M), STT SAR 0.06-0.078/min, Agent SAR 0.60-0.78/min | Good for SA-only. No EG. Expensive per-char vs Azure. |
| **Nabarati Nabra v3 (EG)** | 15+ dialects incl. EG + Gulf + Levant + Maghreb | Dialect converter + emotion/speed | Cloud, no API docs found | Free trial, no public pricing | Samples-only evidence. Verify API + EG MOS. |
| **Munsit Faseeh (CNTXT, UAE)** | 25+ dialects auto | Claims CS handling | Cloud/VPC/**on-prem/on-device** | Cloud ~$0.50/1k chars, from $8/mo credits, enterprise custom | Sovereign pick. STT-side stronger evidence than TTS. |
| **Hamsa** | 12+ dialects (`egy` Mariam/Samir, `ksa` Hiba/Fahd) + LiveKit plugin | Claimed | LiveKit-native | Verify sales | Shortlist for LiveKit EG/SA TTS. Claims unverified — pilot it. |

### 2.3 Generalist creator APIs

| Vendor | Arabic | Pricing | Verdict |
|---|---|---|---|
| **ElevenLabs (Multilingual v2 / Turbo v2.5 / Flash v2.5 / v3)** | Single `ARA`. Dialects via community voices (EG: Fathy/Haytham/Hoda; SA: Raed; Gulf: Fares). v3 74 langs. Flash 75ms / Turbo ~300ms / v3 ~280ms | 1 char=1 credit (v2/v3), 0.5 (Turbo/Flash). **$100/1M (v2/v3), $50/1M (Turbo/Flash)** | Best MSA naturalness. EG authenticity = voice-dependent. Flash skips normalization (numbers/dates worse). |
| **Fish Audio (hosted Fish Speech)** | 40+ langs incl. ar Tier-2 | From $9/mo; ~$15/1M | Low-cost ElevenLabs alt if EG passes listening test. |
| **Murf** | MSA studio only, no EG/SA tags | $19-66/mo + $0.10-0.35/min overage | Studio tool, not realtime agent. Exclude for agent. |
| **PlayHT** | — | — | **Dead. Acquired by Meta Jul-2025, shut Dec-2025. Do not select.** |

### 2.4 Open-source TTS (self-host)

| Model | Arabic? | License | HW inference | Verdict |
|---|---|---|---|---|
| **Qwen3-TTS 0.6B / 1.7B** | **No official.** Community EG (`itshamdi404/Egy_Arabic_Qwen3-TTS`) + KSA (`vadimbelsky/qwen3-TTS-KSA`) forks only | Apache-2.0 | 0.6B ~8GB VRAM pract., 1.7B ~16GB VRAM. TTFB ~100ms, RTF 0.23-0.55 | Viable on-prem base if you fine-tune + evaluate per dialect. Needs diacritizer + normalization you build. |
| **Fish Speech v1.4 / S2 Pro** | Yes Tier-2 (20k hrs ar of 720k) | Apache-ish (verify per release) | v1.4 8GB min / 16-24GB rec. S2 Pro **24GB rec** (11GB FP / 6.5GB INT8) | Best open Arabic-ready base today. 10-30s zero-shot clone, ~100ms TTFA. |
| **XTTS-v2 (Coqui 470M)** | Yes generic (MSA/Gulf-leaning, needs EG/SA FT) | **CPML — not OSI open, commercial restrictions; Coqui shut 2024** | 4-8GB typical, 8-16GB rec. <200ms streaming | Good quality but license risk. Avoid for commercial unless counsel clears. |
| **F5-TTS + Arabic forks** | MSA-only forks, needs full tashkeel | MIT base, **Arabic forks CC BY-NC (no commercial)** | 8-12GB | Exclude for prod. |
| **Bark / Kokoro / ChatTTS / CosyVoice / MMS-TTS / YourTTS** | Bark weak ar; others **no Arabic** | Mixed (ChatTTS model CC BY-NC) | 2-12GB / CPU | Exclude for Arabic. Kokoro is CPU-cheap but EN-only. |

**TTS quality ranking for your case:** EG: Hakim/Hamsa/Munsit ≈ Azure ar-EG > ElevenLabs EG community voice > Fish Speech FT > Qwen3-TTS EG fork > MSA-only models. SA: SILMA ksa ≈ Munsit/Hakim > Azure ar-SA > xAI ar-SA > others. Mixed AR-EN: SILMA/Hakim (native) > Azure/Google with SSML tagging > ElevenLabs v2 (normalization) > open models (need fixed phonemizer, cf. SawtArabi Interspeech 2025).

## 3. STT — full option list

Leaderboard anchor: Open Universal Arabic ASR (SADA + MASC + MGB-2 + CV + Casablanca). Best open 2026-07 `Cohere Transcribe Arabic` 25.87% avg WER vs Whisper large-v3 36.86%. MSA 5-16%, EG/Gulf conversational 20-50%, Maghrebi 40-60%. NADI 2025 blind: zero-shot Whisper 93.9%, fine-tuned winner (Munsit/CNTXT-style) 35.7% (EG 20.9%). Code-switch hard sets: ElevenLabs Scribe v2 ~13% EG, Azure ~43%.

### 3.1 Open-source STT

| Model | Size / HW | Streaming | Verdict |
|---|---|---|---|
| **Faster-Whisper large-v3-turbo INT8** | 809M, ~1.6GB VRAM, any 6GB+ GPU. 4-6x faster than large-v3. CPU i7 INT8 ~1m42s vs 7min stock | Via VAD + chunking (0.5-2s partials), no native stream | Cheap baseline. MSA-leaning, normalizes dialect to MSA, CS unpredictable. Add brand keyterms + normalizer. |
| **Whisper large-v3 FP16** | 1.55B, ~6.2GB VRAM (3GB INT8) | Same as above | +0.5-2pts over turbo on Tier-2 langs. 100+ concurrent turbo streams per L40S 48GB. |
| **Cohere Transcribe Arabic (07-2026)** | 2B, Apache-2.0, vLLM, RTFx 525 claimed, 8-12GB pract. | Batch API, no native diarization | **Current open SOTA + Latin-preserving CS.** First swap-in after baseline. |
| **NeMo FastConformer Arabic** | ~115M, 0.5-1GB VRAM, tiny + fast, Riva deploy | CTC streaming-capable | Good low-latency MSA, needs EG FT. |
| **wav2vec2 XLSR ar FTs / MMS-1B / Qwen2-Audio-7B / SeamlessM4T-v2 / Distil-Whisper** | 0.3-7B | Mostly none | Exclude as primary: brittle on phone noise + CS (MMS ~100% zero-shot on dialectal sets), Qwen2-Audio hallucinates, Distil EN-only. |

### 3.2 Commercial STT

| Provider | ar-EG/ar-SA | CS | Streaming / Diar. | Pricing | Verdict |
|---|---|---|---|---|---|
| **Deepgram Nova-3 Arabic** | 17 codes incl. ar-EG/SA/AE/QA | Monolingual ar (10-lang live CS excludes ar) but ~40% lower conv. WER claimed | <300ms partials, keyterms (100), diar. add-on, self-host avail. | ~$0.0043-0.0048 batch, $0.0077 stream | Cheapest hosted streaming. Good week-1 baseline. |
| **ElevenLabs Scribe v2** | 99 langs incl. ar | **Best measured CS** | Batch + realtime, 32-spk diar., word ts | ~$0.22 batch / $0.28-0.48 realtime ($0.40/hr list) | Pick if CS accuracy > cost. |
| **Google Chirp 3** | ar-EG + 15 ar variants (Preview) | Auto-LID, SA < EG | Stream/batch, diar. batch-only | $0.016/min std, $0.003 dynamic batch | OK fallback, verify EG GA status. |
| **Azure Speech** | Full ar-EG/SA/DZ/IQ/JO/KW/LB/MA table | Continuous LID | Realtime + batch, diar. +$0.30/hr | $1/hr realtime, $0.36/hr batch, 5h free | Enterprise pick, weak on hardest CS in Perle test. |
| **Soniox / Speechmatics** | 60+ incl. ar; Speechmatics claims Gulf/EG/Levantine | Soniox any-to-any, Speechmatics native bilingual CS | Token-level stream, diar. included (Soniox $0.10-0.12/hr flat) | Soniox $0.10/hr async, $0.12/hr stream; Speechmatics from $0.129/hr | Strong CS + price. Pilot Soniox. |
| **Munsit (UAE)** | 25+ dialects auto, Gulf-first + EG | Claims CS | Cloud/VPC/on-prem/on-device + realtime | Free credits, from $8/mo, enterprise custom | Sovereign + dialect pick. Vendor #1 claim anchored to HF leaderboard — replicate on your audio. |
| **AWS Transcribe / AssemblyAI** | ar-SA (MSA-ish), Assembly U-2 99 langs | No explicit ar CS mode | Standard | $0.006-0.024/min (AWS pages inconsistent), Assembly $0.15-0.45/hr | Fallbacks only. |

**STT quality ranking for your case:** Hard EG+brands+noise: Scribe v2 ≈ Cohere-ar ≈ Munsit > Deepgram Nova-3 ar-EG > Whisper-turbo > Azure/Google generic > wav2vec2/MMS/Qwen2-Audio. Always measure on 200 own clips — clean-set WER gaps of 2pts become 40pts on hardest quartile.

**Arabizi note:** No STT emits `3` for ع. `3rod kentaki` -> STT gives `عروض كنتاكي / Kentucky`. You need post-STT normalizer: lower, unidiacritize, alef/ya/tamarbuta fold, Franco map (3->ع, 7->ح, 2->أ), brand alias dict (`kentaki/kentaky/كنتاكي/دجاج كنتاكي`, `pizza hut/بيتزا هت`), then bilingual-embedding retrieval. Supply keyterms/adaptation lists (`بيتزا هت، كنتاكي، كومبو، وجبة`) to Deepgram/Google/Azure.

## 4. Voice agent (the actual product)

Pattern: `VAD -> streaming STT -> endpointing -> fast LLM (your RAG) -> streaming TTS`, orchestrated by LiveKit Agents or Pipecat, SIP to local carrier, WhatsApp as separate async path.

| Stack | Arabic verdict |
|---|---|
| **LiveKit Agents + Hamsa/Munsit/Soniox STT + Hamsa/Munsit/SILMA/xAI TTS** | **Recommended default.** `ara` supported, xAI has `ar-EG/ar-SA/ar-AE` tags, Cartesia/Rime/Fish `ar` generic. Multilingual turn-detector, SIP/WEBRTC at scale. Note: Deepgram Aura-2 TTS has **no Arabic — must swap TTS**. |
| **Pipecat (Daily)** | Co-recommended if you need custom VAD/LLM/tooling. Same provider swap-ins (Soniox publishes Pipecat packages). |
| **Vapi / Retell / Bland / ElevenLabs ConvAI / PlayAI** | All passthrough to STT/TTS you configure. Retell best barge-in, Vapi most flexible BYOK, Bland most closed (worst for dialect control). $0.06-0.33/min realistic all-in. None has native EG turn-taking. Usable to prototype, not to differentiate. |
| **OpenAI Realtime API** | Arabic buggy (transcribed as English in 2025 reports), no EG/SA split, EN-tuned voices. Not primary. |
| **Qwen-Omni / Moshi / Mini-Omni / LLaMA-Omni / Freeze-Omni / GLM-4-Voice** | EN/ZH research demos. No verified EG/SA. **Do not build prod on these.** |

Latency budget for <800ms perceived: VAD 50-100ms + STT partial 100-200ms + endpointing 200-300ms + LLM TTFT 300-500ms + TTS first-audio 200-400ms = 1-2s untuned. Sub-800ms needs all-streaming + co-located region + small LLM + filler (`لحظة واحدة…`) that is itself interruptible. Barge-in = state machine (keep listening while speaking, 200-300ms energy confirm, halt TTS + flush buffer, preserve context). Monitor false-barge-in >8% and premature-cutoff >5%.

Telephony Egypt/Saudi: no clean Twilio/Telnyx local EG termination at scale (NTRA regulates VoIP; SA needs CITC trunking). Use LiveKit/Retell SIP <-> local carrier (WE/Orange/Etisalat EG; STC/Mobily/Zain SA) or aggregator (Maqsam). +$0.015-0.03/min + $2/mo/number. WhatsApp Business API = **async file exchange (OGG in, MP3 out), seconds of latency, no barge-in** — build as separate voice-note mode (batch STT -> LLM -> TTS).

## 5. Hardware requirements for local deployment

| Component | Config | VRAM / RAM | Notes |
|---|---|---|---|
| Whisper turbo INT8 (Faster-Whisper) | 1 stream | ~1.6GB VRAM, any 6GB GPU (RTX 3060 OK) | 100+ streams per L40S 48GB. CPU-only possible via whisper.cpp (~1-1.6GB RAM INT8) but not for concurrency. |
| Whisper large-v3 FP16 | 1 stream | ~6.2GB VRAM | Batch on RTX 3060 laptop: turbo 18.7s vs large-v3 43s on ref set. |
| Cohere Transcribe Arabic | vLLM server | 8-12GB pract. | Verify on your GPU; vendor claims consumer-HW capable. |
| NeMo FastConformer | Riva server | 0.5-1GB | Cheapest streaming STT self-host. |
| Qwen3-TTS 0.6B | FP16 + FlashAttn-2, CUDA | ~8GB pract. (2GB weights) | ROCm via community fork only. |
| Qwen3-TTS 1.7B | Same | ~16GB pract. | Needs RTX 4090 / L4 / A10 minimum. |
| Fish Speech S2 Pro | FP8/INT8 | 24GB rec (11GB FP / 6.5GB INT8), H200 RTF 0.195 | 1x A10G/L4 per realtime session class. |
| XTTS-v2 | FP16 | 4-8GB typical, 8-16GB rec. | — |
| Full agent worker (STT+LLM 3B+TTS) | Docker Compose | 1x A10 24GB or L4 24GB per 20-50 concurrent streams (depends on batching) | LLM dominates: qwen2.5:3b needs ~6-8GB; 7B omni needs 16-24GB. |
| CPU-only MSA | MMS-TTS / whisper.cpp | <2GB | Robotic TTS, weak STT — prototype only. |

Rule: self-host STT+TTS for 50 concurrent calls ~= 1-2x L40S/A10 24-48GB (~$300-800/mo cloud GPU or $3-6k capex). Managed STT+TTS avoids this entirely.

## 6. Scale, blockers, limits — and fixes

| # | Blocker | Severity | Limit you will hit | Fix |
|---|---|---|---|---|
| 1 | **Dialectal + noisy + CS data** | Highest | MSA broadcast data does not transfer (negation/verb forms differ). No open set covers EG/SA call audio. | Collect 30-60 min/dialect pilot from day 1 (consent + PII strip). Grow to 50-200h for domain FT. Include 8kHz phone codec + street/cafe noise + `Pizza Hut/Kentucky` switches. |
| 2 | **Eval** | Highest | Vendor WER/MOS do not transfer. Arabic orthography variance (انتو/انتوا) inflates WER; single-ref WER 76% -> 53% with 5 refs. | Build own harness: fixed normalizer, report WER + CER + MR-WER + BERTScore; 200-clip STT set + blind MOS for TTS + barge-in A/B (2k calls). Gate vendor choice on this. |
| 3 | **Telco + WhatsApp** | High | EG NTRA / SA CITC blocks, no local numbers via Twilio, WhatsApp != realtime | SIP via local aggregator; WhatsApp as async mode. Budget telephony extra. AEC mandatory (agent trips on own TTS over phone delay). |
| 4 | **Cost** | High | LLM dominates: $0.05/min orchestration is noise; LLM $0.003-0.345/min swings total $0.06-0.33/min. 50k min/mo: Retell $3-7.7k vs Vapi $9-16k (vendor math) | Start small LLM (GPT-4o-mini/Gemini Flash/Haiku) + concise EG/SA prompt, no markdown. Self-host LLM only after PMF. |
| 5 | **Hardware** | Medium | Only if self-hosting | Use managed STT/TTS first; self-host after 10k+ min/mo proves unit economics. 24GB VRAM covers one full worker. |
| 6 | **Compliance** | High for banking/gov/health | UAE PDPL + SA PDPL (enforced Sep-2024) disqualify cloud-only globals | Require VPC/on-prem option (Munsit/SILMA/Soniox on-prem, Azure EU) for regulated verticals. |
| 7 | **Diacritization / normalization** | Medium | Unvowelized Arabic collapses TTS (SawtArabi: all baselines need vowelization + fixed espeak-ng phonemizer) | Add tashkeel model + number/date normalizer before TTS; fixed phonemizer for open models. |

Not only hardware. Data + eval + telco hurt more than GPUs.

## 7. Can we build on open-source to beat Munsit?

- **Narrow domain (Waffarha deals, EG+SA, 1-2 brand voices): yes, 2-4 months.** Path: Fish Speech or Qwen3-TTS EG/KSA fork + Cohere-ar/Faster-Whisper + NileTTS 38h EG + SawtArabi 4h CS for eval + 50-200h own calls + LoRA/adapter FT + diacritizer + brand vocab. Outcome: latency/control win, per-min cost -> GPU-only, MOS can match on your prompts.
- **Broad 25-dialect generalist: no.** Needs 10k-30k hrs (Munsit cites 30k) + per-dialect prosody + sovereign certs. Do not attempt.
- **Practical plan:** Phase 1 SaaS (Azure + Deepgram/Scribe, LiveKit), Phase 2 collect + eval harness, Phase 3 FT open models per dialect, swap in where MOS/WER wins. This is how you get `Munsit-like for Waffarha, better on your own queries`.

## 8. Recommended build for Waffarha

1. Week 1-2: LiveKit Agents + Deepgram Nova-3 `ar-EG` (keyterms: brands) vs Scribe v2 on 200 own clips; TTS: Azure ar-EG vs Hakim/Hamsa vs ElevenLabs EG voice. Blind MOS + WER.
2. Month 1: ship cascade with winner; WhatsApp voice-note mode (async) separate from live-call pipeline; SIP via local aggregator.
3. Month 2-4: collect calls, build eval set, FT Fish/Qwen-TTS per dialect + Cohere-ar adapter; keep SaaS fallback.
4. Gate scale on: false-barge-in <8%, cutoff <5%, task-completion lift, per-min cost.

## 9. Cost sketch (verify at contract time)

- SaaS TTS: Azure $15/1M chars (~$0.9/hr speech) vs ElevenLabs $50-100/1M vs SILMA $0.025/min ($1.50/hr) vs Nabrah ~$64-83/1M.
- SaaS STT: Soniox $0.10-0.12/hr vs Deepgram ~$0.26-0.46/hr vs Azure $1/hr vs Scribe ~$0.22-0.48/hr.
- Agent all-in hosted: $0.06-0.33/min (+ telephony $0.015-0.03/min).
- Self-host: 1x L40S/A10 ~$300-800/mo cloud or $3-6k capex, then ~$0 marginal per min.

*All pricing/latency vendor-published 2025-2026, moves quarterly. Accuracy claims anchored to HF Open Universal Arabic ASR Leaderboard + Perle 2026 hard sets + vendor docs; replicate on your audio before committing.*

---

## 10. Addendum 2026-09-24 — real-test corrections (voice lab, GTX 1650 Ti 4GB, 41 TTS clips + notes.jsonl)

Hands-on testing overturned parts of the desk research above. Findings, each tied to evidence:

1. **Chatterbox-Egyptian (`oddadmix/chatterbox-egyptian-v0`) was missing from this report and is the winner.** ★5 "perfect egyptian arabic" on `انا عايز بيتزا...`; good MSA; SA intelligible with weak Saudi accent ("a bit bad, expected"); reads Arabizi-script Arabic well; English sentence "unexpectedly perfect"; Franco bad-but-improvable via normalization. Only true EG voice found. Slower than Nabra-82M, acceptable. **EG TTS ranking is now: Chatterbox-EG > Azure ar-EG**, reversing §2's order.
2. **Nabra-82M was also missing (report only named the commercial Nabras).** Fast, MSA ★5, EG mediocre (★3 on pizza/EG sentences), breaks on Latin-script English inside Arabic (★2 KFC) but reads Arabizi Arabic (★5). Verdict: fast MSA fallback, not the EG voice. "Qwen vs Nabra similar" matches smoke tests (both MSA-leaning, both garble EG words).
3. **Qwen numbers in §2.4/§5 are wrong for small GPUs.** 0.6B runs on 4GB (not "~8GB pract."), but slowly: Qwen3-ASR 98s cold / 25s warm per clip on 1650 Ti. Vendor "100ms TTFB" does not transfer. **Qwen3-ASR is also missing from the STT table (§3.1)** — add it: open, `ar`-generic with no dialect split, SA near-perfect in smoke test, EG mediocre.
4. **Lahgtna is missing and must be marked unreliable.** Dialect mixing ("mixing dialects together"), "funny" outputs, hallucinations per tester. Exclude from prod shortlist.
5. **Azure confirmed fast + multi-voice but demoted on accuracy.** "No linking, strict MSA not dialect", brand-name failure (`وفرها` → "wafferha" — unacceptable for Waffarha), code-switch ★1 ("unbelievable" on KFC sentence). Any Azure choice requires a brand pronunciation lexicon.
6. **Franco: extend the §3 Arabizi note to TTS input.** Chatterbox + letter normalization (3→ع, 7→ح, 2→أ, 5→خ…) is the path; `fr1/fr2` presets added to `eval/voice_eval/corpus_eg_sa.json`.
7. **Still unverified:** Hakim, SILMA, Munsit, Hamsa, ElevenLabs, Scribe, Deepgram, Cohere. Their rankings above stay provisional until blind-tested against Chatterbox on your clips.
8. **STT gap:** all 20 notes are TTS-side. STT ranking (Qwen3-ASR vs dialect-Whisper vs Faster-Whisper) needs microphone recordings, not TTS roundtrips. `whisper-medium-egy` weights were still downloading at test time.

## 11. Addendum 2026-09-27 — voice agent built + clean UTF-8 loop scores

`core/voice.py` bridges the chatbot (:8000/:8001) to the voice lab (:8002);
Chatterbox-EG via disposable per-request worker processes (a T3 sampler
assert kills only the worker, never the lab). Widget has mic input + spoken
replies. `tests/test_voice.py`: 9 passed.

- **Test-harness artifact found and fixed:** PowerShell `Invoke-RestMethod`
  decodes charset-less JSON as Latin-1, so every chatbot answer piped to TTS
  that way arrived as mojibake (incl. U+0080-U+009F "controls"). All earlier
  long-answer quality judgments from that path are void; short literal texts
  were unaffected. Browser `fetch` is UTF-8-clean, so real users never hit this.
- **Clean loop scores (whisper/fw-small roundtrip, CER is the signal for
  dialectal Arabic since WER inflates on orthography):** short conversational
  EG (`انا رايح الشغل...`) CER **0.17** (good); long formal offer answer
  (526 chars) CER **0.64**, WER 0.87 (poor — stutter loops, syllable
  repetition, occasional English hallucinations on tiny fragments).
- **Loop triggers:** single 400+-char generations (repetition guard fires but
  too late; worst case OOB assert), formal MSA register, digit/symbol runs
  (now verbalized: `35%` → `خمسة وثلاثين بالمئة`), C1 controls (now stripped),
  and over-fragmented chunks (9-25 char labels hallucinate). Mitigations:
  ≤150-char sentence chunks, number verbalizer, control-strip, worker isolation.
- **Product rule from this:** speak short replies/leads only
  (`VOICE_TTS_MAX_CHARS=350`, widget slices 350); full offer cards stay on
  screen. Long formal dumps are slow (multiple minutes on GTX 1650 Ti) and
  loop-prone — poor voice UX regardless of engine.
- **Open:** Nabra-82M (MSA-native, deterministic) as long/formal fallback —
  untested on this content; blind commercial comparison vs Chatterbox still due.
