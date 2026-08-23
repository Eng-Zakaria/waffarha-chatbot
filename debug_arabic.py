import sys
query = "بكام عرض كنتاكي"
print("Has Arabic chars:", any(0x0600 <= ord(ch) <= 0x06FF for ch in query))
print("Query length:", len(query))
words = query.split()
print("Word count:", len(words))
for w in words:
    sys.stdout.buffer.write(f"  Word: {repr(w)}, len={len(w)}\n".encode('utf-8'))

# Check if _looks_like_gibberish would catch it
import re
letters_only = re.sub(r"[^a-zA-Z\s]", "", query)
print("Letters only:", repr(letters_only))
words_eng = [w for w in letters_only.split() if w]
print("English words:", words_eng)