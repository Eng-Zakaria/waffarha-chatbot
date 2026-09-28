"""Disposable Chatterbox synthesis worker. One process per request: if the
T3 sampler emits an out-of-range token (CUDA assert), only this worker dies
and the lab stays healthy for the next request. Args: ckpt_dir text out_wav.
Prints a JSON result line on stdout."""
import json
import os
import re
import sys
import time

ckpt, text, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import soundfile as sf
import torch

from lab import _cb_load

t0 = time.time()
dev = "cuda" if torch.cuda.is_available() else "cpu"
m = _cb_load(ckpt, dev)

parts = [p.strip() for p in re.split(r"(?<=[.!?؟:؛\n])\s*", text) if p.strip()]
chunks = []
for p in parts:
    # Hard-split overlong pieces at word boundaries: the sampler loops
    # (token repetition -> forced EOS / OOB crash) on long formal inputs.
    while len(p) > 150:
        cut = p.rfind(" ", 0, 150)
        cut = cut if cut > 60 else 150
        chunks.append(p[:cut].strip())
        p = p[cut:].strip()
    if p:
        chunks.append(p)
# Drop chunks with no speakable characters (emoji/punct-only remnants);
# an empty token list makes the sampler hallucinate its fallback sentence.
chunks = [c for c in chunks if re.search(r"[\w\u0600-\u06FF]", c)]
if not chunks:
    print(json.dumps({"error": "no speakable content"}))
    sys.exit(2)

wavs = []
for ch in chunks:
    w = m.generate(text=ch, language_id="ar", temperature=0.7,
                   cfg_weight=0.5, exaggeration=0.5)
    wavs.append(w.squeeze(0).cpu().numpy())
    del w

import numpy as np
sf.write(out, np.concatenate(wavs), m.sr)
print(json.dumps({"latency_s": round(time.time() - t0, 2), "chunks": len(chunks)}))
