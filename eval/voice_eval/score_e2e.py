"""Score e2e_loop.wav: transcribe with fw-small, WER/CER vs answer."""
import json
import urllib.request
import uuid

import requests
with open("outputs/e2e_loop.wav", "rb") as f:
    r = requests.post("http://127.0.0.1:8002/api/stt",
                      data={"model": "fw-small"},
                      files={"file": ("l.wav", f, "audio/wav")}, timeout=300)
    r.raise_for_status()
    hyp = r.json()["text"]
with open("outputs/e2e_hyp.txt", "w", encoding="utf-8") as f:
    f.write(hyp)
ref = open("outputs/e2e_answer.txt", encoding="utf-8").read()
from jiwer import wer, cer
print("WER:", round(wer(ref, hyp), 3), "CER:", round(cer(ref, hyp), 3), flush=True)
print("ref words:", len(ref.split()), "hyp words:", len(hyp.split()), flush=True)

