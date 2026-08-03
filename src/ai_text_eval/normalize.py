"""Unicode normalization defense.

Homoglyph substitution ("SilverSpeak"-class attacks) replaces Latin letters
with visually identical characters from other scripts — Cyrillic а for Latin
a, Greek ο for Latin o — and inserts zero-width characters. The text looks
unchanged to a reader while every affected token becomes out-of-vocabulary,
which destroys perplexity-based scoring and defeats lexical matching.

This is the cheapest evasion in the literature and the cheapest to defend
against, so normalization runs *before* scoring rather than being left to the
detectors. The report it returns is itself evidence: a document with 40
Cyrillic characters scattered through otherwise-English prose has been
tampered with, and that fact belongs in the output.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Confusables for ASCII letters and digits. Restricted to the characters that
# actually appear in homoglyph attacks (Cyrillic, Greek, fullwidth, and a few
# mathematical alphanumerics) rather than the full Unicode confusables table,
# so normalization stays predictable and auditable.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic -> Latin
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "І": "I",
    "Ј": "J", "Ԁ": "D", "Ԍ": "G", "Ѡ": "W",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "ѕ": "s", "і": "i", "ј": "j", "ԁ": "d", "һ": "h", "ν": "v",
    # Greek -> Latin
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "κ": "k", "α": "a", "ε": "e",
    # Armenian / Cherokee lookalikes seen in the wild
    "ѵ": "v", "Ꭺ": "A", "Ꮃ": "W", "Ꮪ": "S",
}

# Zero-width and invisible characters: pure payload, never meaningful in prose.
_INVISIBLE = {
    "​",  # zero-width space
    "‌",  # zero-width non-joiner
    "‍",  # zero-width joiner
    "⁠",  # word joiner
    "﻿",  # zero-width no-break space
    "­",  # soft hyphen
    "᠎",  # Mongolian vowel separator
}

# Unusual spaces that survive copy-paste and perturb tokenization.
_ODD_SPACES = {
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", "　": " ",
}

_LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass
class NormalizationReport:
    """What normalization had to repair, and how suspicious that is."""

    homoglyphs_replaced: int = 0
    invisibles_removed: int = 0
    odd_spaces_replaced: int = 0
    scripts_seen: set[str] = field(default_factory=set)
    n_chars: int = 0

    @property
    def total_repairs(self) -> int:
        return self.homoglyphs_replaced + self.invisibles_removed + self.odd_spaces_replaced

    @property
    def tamper_rate(self) -> float:
        """Repairs per character — the evidence of deliberate obfuscation."""
        return self.total_repairs / self.n_chars if self.n_chars else 0.0

    @property
    def is_suspicious(self) -> bool:
        """Whether the pattern looks like an attack rather than typography.

        Any invisible character in prose is a payload. Mixed-script homoglyphs
        are only innocent in genuinely multilingual text, so the rate matters:
        a handful of Cyrillic letters inside otherwise-Latin prose is the
        signature, not incidental.
        """
        if self.invisibles_removed > 0:
            return True
        return self.homoglyphs_replaced >= 3 and self.tamper_rate > 0.002

    def to_dict(self) -> dict:
        return {
            "homoglyphs_replaced": self.homoglyphs_replaced,
            "invisibles_removed": self.invisibles_removed,
            "odd_spaces_replaced": self.odd_spaces_replaced,
            "tamper_rate": round(self.tamper_rate, 6),
            "suspicious": self.is_suspicious,
            "scripts": sorted(self.scripts_seen),
        }


def _script_of(ch: str) -> str:
    """Coarse script label from the Unicode character name."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "UNKNOWN"
    for script in ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE",
                   "ARABIC", "HEBREW", "DEVANAGARI", "HAN", "HIRAGANA",
                   "KATAKANA", "HANGUL", "THAI"):
        if name.startswith(script):
            return script
    return "OTHER"


def normalize(text: str, nfkc: bool = True) -> tuple[str, NormalizationReport]:
    """Return (normalized_text, report).

    Applies NFKC, maps confusables to their Latin equivalents, strips
    invisible characters, and folds exotic spaces. Scoring should always run
    on the normalized text; the report should always travel with the verdict.
    """
    report = NormalizationReport(n_chars=len(text))
    out: list[str] = []
    for ch in text:
        if ch in _INVISIBLE:
            report.invisibles_removed += 1
            continue
        if ch in _ODD_SPACES:
            report.odd_spaces_replaced += 1
            out.append(_ODD_SPACES[ch])
            continue
        if ch in _CONFUSABLES:
            report.homoglyphs_replaced += 1
            report.scripts_seen.add(_script_of(ch))
            out.append(_CONFUSABLES[ch])
            continue
        if ch.isalpha() and not _LATIN_RE.match(ch):
            report.scripts_seen.add(_script_of(ch))
        out.append(ch)

    result = "".join(out)
    if nfkc:
        result = unicodedata.normalize("NFKC", result)
    return result, report
