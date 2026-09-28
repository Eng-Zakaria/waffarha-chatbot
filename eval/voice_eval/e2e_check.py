"""Full voice loop with strict UTF-8 end to end (no PowerShell decoding).
chat (:8001) -> answer.txt -> TTS (:8001 voice) -> loop.wav -> STT -> verdict.
Usage: python e2e_check.py [query] [session]
"""
import json
import sys
import urllib.request

CHAT = "http://127.0.0.1:8001/api/chat"
TTS = "http://127.0.0.1:8001/api/voice/tts"
STT = "http://127.0.0.1:8000/api/voice/stt"


def post_json(url, payload, timeout=660):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def post_file(url, path, timeout=660):
    import uuid
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"loop.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


query = sys.argv[1] if len(sys.argv) > 1 else "ايه عروض رمضان طيب؟"
session = sys.argv[2] if len(sys.argv) > 2 else "e2e-utf8-1"

st, raw = post_json(CHAT, {"query": query, "session_id": session})
chat = json.loads(raw.decode("utf-8"))
answer = chat["answer"]
print(f"answer chars: {len(answer)}", flush=True)
with open("outputs/e2e_answer.txt", "w", encoding="utf-8") as f:
    f.write(answer)

st, raw = post_json(TTS, {"text": answer})
if st != 200:
    print("TTS FAILED:", raw.decode("utf-8")[:300], flush=True)
    sys.exit(1)
with open("outputs/e2e_loop.wav", "wb") as f:
    f.write(raw)
print(f"tts bytes: {len(raw)}", flush=True)

st, raw = post_file(STT, "outputs/e2e_loop.wav")
print("stt:", json.loads(raw.decode("utf-8"))["text"], flush=True)
print("E2E DONE", flush=True)
