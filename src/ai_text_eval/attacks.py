"""Adversarial transforms for robustness evaluation.

RAID ships eleven adversarial attacks alongside its corpus for a reason: a
detector's clean-split number tells you almost nothing about how it behaves
against someone actively trying to evade it, and the gap between the two is
where every deployed system fails. These transforms exist to *measure* that
gap and to verify that `normalize.py` closes it.

Scope is deliberate. Implemented here are the mechanical, semantics-preserving
perturbations that need no model: homoglyphs, invisible characters, whitespace,
punctuation and casing. The attacks that actually matter most — recursive
paraphrase (DIPPER), RL-optimized rewriting (StealthRL, AuthorMist), and
commercial humanizers — require a generator, so this module does not fake
them. Supply real paraphrase pairs to `evaluate_evasion` instead; a mechanical
stand-in would make a detector look robust against an attack class it has
never actually faced.

Every transform is deterministic given a seed, so robustness numbers are
reproducible.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

# Latin -> visually identical non-Latin. The inverse of normalize.py's map,
# restricted to characters that render near-identically in common fonts.
_HOMOGLYPHS: dict[str, str] = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "s": "ѕ", "x": "х",
    "y": "у", "i": "і", "j": "ј", "d": "ԁ", "h": "һ",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "I": "І", "J": "Ј",
    "K": "К", "M": "М", "O": "О", "P": "Р", "S": "Ѕ", "T": "Т", "X": "Х",
    "Y": "У",
}

_ZERO_WIDTH = ["​", "‌", "‍", "⁠"]


@dataclass
class Attack:
    name: str
    description: str
    fn: object  # Callable[[str, random.Random], str]

    def apply(self, text: str, seed: int = 0) -> str:
        return self.fn(text, random.Random(seed))  # type: ignore[operator]


def _homoglyph(text: str, rng: random.Random, rate: float = 0.06) -> str:
    """Swap a fraction of Latin letters for confusable non-Latin ones."""
    out = []
    for ch in text:
        if ch in _HOMOGLYPHS and rng.random() < rate:
            out.append(_HOMOGLYPHS[ch])
        else:
            out.append(ch)
    return "".join(out)


def _zero_width(text: str, rng: random.Random, rate: float = 0.04) -> str:
    """Insert invisible characters between words."""
    parts = text.split(" ")
    buf: list[str] = []
    for i, part in enumerate(parts):
        buf.append(part)
        if i < len(parts) - 1:
            buf.append(rng.choice(_ZERO_WIDTH) + " " if rng.random() < rate else " ")
    return "".join(buf)


def _whitespace(text: str, rng: random.Random, rate: float = 0.05) -> str:
    """Replace ordinary spaces with exotic but visually similar ones."""
    exotic = [" ", " ", " ", " "]
    return "".join(
        rng.choice(exotic) if (ch == " " and rng.random() < rate) else ch for ch in text
    )


def _punctuation(text: str, rng: random.Random, rate: float = 0.35) -> str:
    """Swap straight quotes/apostrophes and hyphens for typographic variants."""
    out = []
    for ch in text:
        if rng.random() < rate:
            if ch == "'":
                out.append("’")
                continue
            if ch == '"':
                out.append("“")
                continue
            if ch == "-":
                out.append("‑")  # non-breaking hyphen
                continue
        out.append(ch)
    return "".join(out)


def _casing(text: str, rng: random.Random, rate: float = 0.02) -> str:
    """Randomly flip the case of individual letters."""
    return "".join(
        (ch.upper() if ch.islower() else ch.lower()) if (ch.isalpha() and rng.random() < rate) else ch
        for ch in text
    )


def _article_drop(text: str, rng: random.Random, rate: float = 0.30) -> str:
    """Delete articles — a crude register shift that survives reading."""
    def repl(m: re.Match) -> str:
        return "" if rng.random() < rate else m.group(0)

    out = re.sub(r"\b(the|a|an)\s+", repl, text, flags=re.I)
    return re.sub(r"\s{2,}", " ", out)


ATTACKS: dict[str, Attack] = {
    "homoglyph": Attack(
        "homoglyph",
        "Replace Latin letters with confusable Cyrillic/Greek characters",
        _homoglyph,
    ),
    "zero_width": Attack(
        "zero_width", "Insert zero-width characters between words", _zero_width
    ),
    "whitespace": Attack(
        "whitespace", "Substitute exotic Unicode spaces for ordinary ones", _whitespace
    ),
    "punctuation": Attack(
        "punctuation", "Swap straight quotes and hyphens for typographic variants", _punctuation
    ),
    "casing": Attack("casing", "Randomly flip letter case", _casing),
    "article_drop": Attack("article_drop", "Delete articles to shift register", _article_drop),
}


def apply_attack(name: str, text: str, seed: int = 0) -> str:
    if name not in ATTACKS:
        raise ValueError(f"unknown attack {name!r}; known: {sorted(ATTACKS)}")
    return ATTACKS[name].apply(text, seed)


def attack_names() -> list[str]:
    return sorted(ATTACKS)
