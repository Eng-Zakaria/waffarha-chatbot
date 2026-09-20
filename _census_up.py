import pathlib
L = pathlib.Path(r"agent/tools/catalog_tools.py").read_text(encoding="utf-8").splitlines()
print("rows:", len(L))
print("=== A: the two gate-call seats (one per envelope class) ===")
for i, s in enumerate(L, 1):
    if "_fresh_only(" in s and i < 200:
        print(f"{i:>3}: {s.strip()[:96]}")
print("=== B: the provenance binds inside run (rows 33-200) ===")
for i, s in enumerate(L, 1):
    t = s.strip()
    if t.startswith("provenance = "):
        print(f"{i:>3}: {t[:96]}")
print("=== C: the note envelope rows 197-199 ===")
for i in range(196, 200):
    s = L[i - 1].strip()
    if s:
        print(f"{i:>3}: {s[:96]}")
