"""Download a few real Arabic clips for EG/SA/MSA STT test. Uses streaming to avoid full download."""
import os
from datasets import load_dataset, Audio

OUT = os.path.join(os.path.dirname(__file__), "audio")
os.makedirs(OUT, exist_ok=True)

# 1) Common Voice Arabic (mostly MSA, some dialect) - take 3
print("loading common-voice ar streaming...")
try:
    ds = load_dataset("MohamedRashad/common-voice-18-arabic", split="train", streaming=True)
    ds = ds.cast_column("audio", Audio(sampling_rate=16000))
    n = 0
    for ex in ds:
        import soundfile as sf
        path = os.path.join(OUT, f"cv_ar_{n}.wav")
        sf.write(path, ex["audio"]["array"], 16000)
        print(f"saved {path} text={ex.get('sentence','')[:80]}")
        n += 1
        if n >= 3:
            break
except Exception as e:
    print("CV failed:", e)

# 2) MASC subset is large; skip auto-download, print hint
print("done. Put any EG/SA wavs you have into eval/voice_eval/audio/ as eg_*.wav / sa_*.wav")
print("files:", os.listdir(OUT))
