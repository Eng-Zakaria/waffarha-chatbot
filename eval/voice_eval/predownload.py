"""Pre-download all voice-lab weights to eval/voice_eval/weights (regular files, no symlinks)."""
import os
from huggingface_hub import snapshot_download

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")

jobs = [
    ("Qwen/Qwen3-ASR-0.6B", "qwen3-asr-06"),
    ("Qwen/Qwen3-TTS-Tokenizer-12Hz", "qwen3-tts-tok"),
    ("Qwen/Qwen3-TTS-12Hz-0.6B-Base", "qwen3-tts-06"),
    ("oddadmix/chatterbox-egyptian-v0", "chatterbox-eg"),
    ("oddadmix/lahgtna-chatterbox-v1", "lahgtna"),
    ("oddadmix/Nabra-82M-v0.1", "nabra-82m"),
    ("oddadmix/whisper-small-arabic-dialectal", "whisper-ar-dialect"),
    ("MAdel121/whisper-medium-egy", "whisper-medium-egy"),
]
for repo, slug in jobs:
    try:
        print(f"DOWNLOADING {repo} ...", flush=True)
        p = snapshot_download(repo_id=repo, local_dir=os.path.join(BASE, slug),
                              local_dir_use_symlinks=False)
        print(f"OK {repo}", flush=True)
    except Exception as e:
        print(f"FAIL {repo}: {type(e).__name__}: {e}", flush=True)
print("PREDOWNLOAD DONE")
