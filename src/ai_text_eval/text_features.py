"""Dependency-free text primitives shared by the detectors.

These are deliberately simple, deterministic implementations: an eval
framework must be reproducible, so we avoid tokenizers whose behavior
changes across library versions.
"""

from __future__ import annotations

import math
import re

_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ']+")

# Common abbreviations that end with a period but do not end a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g",
    "i.e", "fig", "no", "vol", "dept", "est", "approx", "inc", "ltd", "co",
    "u.s", "u.k",
}

_SENT_BOUNDARY_RE = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+")


def words(text: str) -> list[str]:
    """Lowercased alphabetic word tokens."""
    return [w.lower() for w in _WORD_RE.findall(text)]


def sentences(text: str) -> list[str]:
    """Split text into sentences.

    Regex-based with an abbreviation guard — good enough for feature
    extraction, and fully deterministic.
    """
    # Normalize whitespace but keep paragraph breaks as sentence boundaries.
    text = text.strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    sents: list[str] = []
    for para in paragraphs:
        para = " ".join(para.split())
        if not para:
            continue
        pieces = _SENT_BOUNDARY_RE.split(para)
        # Re-join splits caused by abbreviations ("Dr. Smith said...").
        merged: list[str] = []
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if merged:
                prev = merged[-1]
                last_word = prev.rstrip(".!?\"'”’)]").rsplit(" ", 1)[-1].lower()
                if last_word in _ABBREVIATIONS or (len(last_word) == 1 and prev.endswith(".")):
                    merged[-1] = prev + " " + piece
                    continue
            merged.append(piece)
        sents.extend(merged)
    return sents


def sentence_lengths(text: str) -> list[int]:
    return [len(words(s)) for s in sentences(text) if words(s)]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def coefficient_of_variation(values: list[float]) -> float:
    """std / mean — the classic "burstiness" statistic for sentence lengths."""
    m = mean(values)
    if m == 0:
        return 0.0
    return stdev(values) / m


def mattr(tokens: list[str], window: int = 50) -> float:
    """Moving-Average Type-Token Ratio (Covington & McFall, 2010).

    Unlike plain TTR, MATTR is stable across text lengths, which matters
    because our corpora mix short and long documents.
    """
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    total = 0.0
    count = 0
    # Sliding window with an incremental multiset for O(n) behavior.
    from collections import Counter

    counter = Counter(tokens[:window])
    total += len(counter) / window
    count += 1
    for i in range(window, len(tokens)):
        out_tok = tokens[i - window]
        counter[out_tok] -= 1
        if counter[out_tok] == 0:
            del counter[out_tok]
        counter[tokens[i]] += 1
        total += len(counter) / window
        count += 1
    return total / count


def logistic(x: float) -> float:
    """Numerically-stable sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)
