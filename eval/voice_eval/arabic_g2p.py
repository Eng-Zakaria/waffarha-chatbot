#!/usr/bin/env python3
"""
Arabic (MSA) G2P front-end for the Kokoro / StyleTTS2 pipeline
==============================================================
Shared by prepare_dataset.py and prepare_training.py so the text→phoneme
mapping is byte-for-byte identical for training transcripts, OOD texts, and
(by extension) inference. Keeping this in one place is what guarantees the
model is trained and queried with the same symbol set.

Per-sentence pipeline:
    1. normalize_text   drop Latin-script loanwords (logged) + tidy whitespace
    2. diacritize       restore MSA short vowels with camel-tools (optional)
    3. espeak-ng G2P    misaki EspeakG2P(language="ar")
    4. clean_phonemes   strip espeak syllable-boundary '.', apply vocab fixups

Why each step exists:
  - espeak-ng's Arabic G2P guesses short vowels for unvowelized text, badly.
    A real MSA diacritizer in front of it removes most of that guessing.
  - espeak emits '.' as a *syllable boundary* mid-word (e.g. ʔarrˈa.ʤul), but
    Kokoro treats '.' as a pause token, so those dots inject phantom pauses.
  - A handful of espeak phonemes fall outside Kokoro's 178-token vocab. ʕ (ع)
    and ħ (ح) get dedicated embedding slots (EXTRA_SYMBOLS) so the MSA contrasts
    ع/ء and ح/ه survive; the rest collapse to their nearest in-vocab symbol.
"""

import re

# ── Out-of-vocab phonemes remapped to an in-vocab symbol ─────────────────────
# espeak emits a few Arabic phonemes that are not in Kokoro's 178-token vocab.
# These get folded into their nearest in-vocab neighbour. NOTE: ʕ and ħ are
# intentionally absent here — they are preserved via EXTRA_SYMBOLS below.
# Emphatics arrive as base + a pharyngealization/dental marker; stripping the
# marker collapses them onto the plain base consonant (sˤ→s, tˤ→t, …).
ARABIC_PHONEME_FIXUPS = {
    "̪": "",  # ◌̪ combining bridge below (dental) → strip
    "ˤ": "",  # ˤ pharyngealization marker → strip
    "[": "",  # espeak untranslatable-token bracket → strip
    "]": "",  # espeak untranslatable-token bracket → strip
    "{": "",  # espeak parenthetical/markup bracket → strip
    "}": "",  # espeak parenthetical/markup bracket → strip
}

# ── Out-of-vocab phonemes KEPT via free Kokoro embedding slots ───────────────
# Kokoro's 178-token table has 63 unused (gap) indices. We park the two most
# important MSA pharyngeals on stable gap slots so they get their own embedding
# (untrained in Kokoro → learned during fine-tuning) instead of being merged
# into ʔ / h. prepare_training.py `patch-styletts2` writes these into the
# symbols list and `verify` treats them as known. Indices 7 & 8 are confirmed
# free in Kokoro-82M config.json.
EXTRA_SYMBOLS = {
    "ʕ": 7,  # ع voiced pharyngeal fricative (distinct from ء/ʔ)
    "ħ": 8,  # ح voiceless pharyngeal fricative (distinct from ه/h)
}

# espeak emits '.' as a syllable boundary mid-word; strip only those (a phoneme
# char on both sides) and keep a sentence-final '.' for prosody.
_SYLLABLE_DOT = re.compile(r"(?<=\S)\.(?=\S)")
# Runs of Latin letters = loanwords espeak would mangle; dropped + counted.
_LATIN_RUN = re.compile(r"[A-Za-z]+")
# Bracketed citation/footnote markers in encyclopedic text — e.g. [123],
# [132][133], [فر]. A narrator does not voice these, so dropping them keeps the
# phonemes aligned with the audio. (Square brackets only; parentheses are kept.)
_CITATION = re.compile(r"\[[^\]]*\]")
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> tuple[str, int]:
    """Drop citation markers + Latin-script loanwords and tidy whitespace.

    Returns (cleaned_text, n_latin_runs_dropped).
    """
    text = _CITATION.sub(" ", text)
    latin = _LATIN_RUN.findall(text)
    if latin:
        text = _LATIN_RUN.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text, len(latin)


def clean_phonemes(ph: str) -> str:
    """Strip espeak syllable-boundary dots and remap out-of-vocab phonemes."""
    ph = _SYLLABLE_DOT.sub("", ph)
    for old, new in ARABIC_PHONEME_FIXUPS.items():
        ph = ph.replace(old, new)
    return ph


class ArabicG2P:
    """Text → IPA phonemes for MSA, with optional camel-tools diacritization.

    Stateful counters (latin_dropped, diac_fallbacks) accumulate across calls
    so the caller can print a single summary at the end.
    """

    def __init__(self, diacritize: bool = True):
        from misaki import espeak

        self._g2p = espeak.EspeakG2P(language="ar")
        self._diac_enabled = diacritize
        self._mle = None
        self.latin_dropped = 0
        self.diac_fallbacks = 0
        self.diac_available = False
        if diacritize:
            self._init_diacritizer()

    def _init_diacritizer(self):
        try:
            from camel_tools.disambig.mle import MLEDisambiguator

            self._mle = MLEDisambiguator.pretrained()
            self.diac_available = True
            print("Diacritizer: camel-tools MLE (calima-msa-r13) loaded.")
        except Exception as e:
            print(f"WARNING: camel-tools diacritizer unavailable ({e}).")
            print("  Falling back to RAW espeak (it will guess short vowels).")
            print("  To enable: pip install camel-tools && "
                  "camel_data -i disambig-mle-calima-msa-r13")
            self._mle = None

    def diacritize(self, text: str) -> str:
        """Restore MSA short vowels. Returns text unchanged if unavailable."""
        if self._mle is None:
            return text
        from camel_tools.tokenizers.word import simple_word_tokenize

        try:
            tokens = simple_word_tokenize(text)
            if not tokens:
                return text
            out = []
            for d in self._mle.disambiguate(tokens):
                if d.analyses:
                    out.append(d.analyses[0].analysis.get("diac", d.word))
                else:
                    out.append(d.word)
            return " ".join(out)
        except Exception:
            self.diac_fallbacks += 1
            return text

    def process(self, raw_text: str) -> tuple[str, str]:
        """Full pipeline. Returns (display_text, phonemes).

        display_text is the Latin-stripped (but NOT diacritized) text, suitable
        for metadata.csv; phonemes are derived from the diacritized form.
        """
        text, n_latin = normalize_text(raw_text)
        self.latin_dropped += n_latin
        diac = self.diacritize(text) if self._diac_enabled else text
        ph, _ = self._g2p(diac)
        return text, clean_phonemes(ph)

    def __call__(self, raw_text: str) -> str:
        """Convenience: return just the phonemes."""
        return self.process(raw_text)[1]

    def summary(self) -> str:
        bits = [f"Latin runs dropped: {self.latin_dropped:,}"]
        if self._diac_enabled:
            bits.append(
                "diacritizer: "
                + ("on" if self.diac_available else "UNAVAILABLE (raw espeak)")
            )
            if self.diac_fallbacks:
                bits.append(f"diac fallbacks: {self.diac_fallbacks:,}")
        else:
            bits.append("diacritizer: off")
        return " | ".join(bits)
