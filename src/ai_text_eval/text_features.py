"""Dependency-free text primitives shared by the detectors.

These are deliberately simple, deterministic implementations: an eval
framework must be reproducible, so we avoid tokenizers whose behavior
changes across library versions.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# A word starts with a letter and may contain internal apostrophes (straight
# or typographic). Requiring a leading letter keeps a bare quotation mark
# from being counted as a token.
_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’][A-Za-zÀ-ɏ]+)*")

# Only abbreviations that are essentially never sentence-final belong here.
# Ordinary words that merely look like abbreviations ("no", "co", "est",
# "vs", "inc") must not be listed: "The answer was no. Then we left." would
# lose a real boundary, silently corrupting the burstiness feature that the
# stylometry detector leans on hardest.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "rev", "gen", "col", "lt", "sgt",
    "capt", "sr", "jr", "mt", "e.g", "i.e",
}

# Split at whitespace that follows sentence-ending punctuation (optionally
# plus one closing quote/bracket) and precedes something that can open a
# sentence. Both lookbehinds consume nothing, so closing quotes stay attached
# to the sentence they end. `(?<!\.\.)` keeps a mid-sentence ellipsis
# ("we finished ... eventually") from reading as a boundary.
_SENT_BOUNDARY_RE = re.compile(
    r"(?:(?<=[.!?])|(?<=[.!?][\"'”’)\]]))"
    r"(?<!\.\.)"
    r"\s+"
    r"(?=[A-Z0-9\"'“‘(\[])"
)

# Lines that open a new block regardless of the previous line's punctuation:
# markdown bullets, numbered items, headings, blockquotes. Without this a
# bulleted answer collapses into one enormous "sentence" and the length
# statistics stop meaning anything — which matters, because bulleted output
# is among the most characteristic LLM formats.
_BLOCK_START_RE = re.compile(r"^\s*(?:[-*+•]\s|\d+[.)]\s|#{1,6}\s|>\s)")

# A fragment consisting only of initials ("J. R. R.") is a name that got
# split, so it absorbs whatever follows. Requiring the *whole* fragment to be
# initials is what keeps "We tried plan A. It failed." as two sentences: there
# the fragment is "We tried plan A.", which is not just initials.
_ALL_INITIALS_RE = re.compile(r"^(?:[A-Z]\.\s*)+$")


def words(text: str) -> list[str]:
    """Lowercased word tokens, with typographic apostrophes normalized.

    Normalizing U+2019 to ASCII is corpus hygiene: LLM output tends to emit
    curly apostrophes and scraped human text tends to emit straight ones, so
    without this the tokenizer hands the detectors a typography signal that
    would masquerade as an authorship signal.
    """
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _blocks(text: str) -> list[str]:
    """Group lines into blocks, joining soft-wrapped lines.

    A bare newline is not a sentence boundary — plain-text corpora are full
    of hard-wrapped prose — so consecutive lines are joined with a space.
    Blank lines and list/heading markers do start a new block.
    """
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if _BLOCK_START_RE.match(raw_line):
            # Drop the marker itself: an enumerator's period is not a
            # sentence boundary, and markers contribute no word tokens.
            line = _BLOCK_START_RE.sub("", line, count=1).strip() or line
            if current:
                blocks.append(" ".join(current))
                current = []
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    return blocks


def sentences(text: str) -> list[str]:
    """Split text into sentences.

    Regex-based, with abbreviation, ellipsis, and list-item handling — good
    enough for feature extraction, and fully deterministic.
    """
    text = text.strip()
    if not text:
        return []
    sents: list[str] = []
    for block in _blocks(text):
        pieces = [p.strip() for p in _SENT_BOUNDARY_RE.split(block) if p.strip()]
        merged: list[str] = []
        for piece in pieces:
            if merged:
                prev = merged[-1]
                last_word = prev.rstrip(".!?\"'”’)]").rsplit(" ", 1)[-1].lower()
                is_abbrev = last_word in _ABBREVIATIONS
                is_initial_chain = _ALL_INITIALS_RE.match(prev) is not None
                if is_abbrev or is_initial_chain:
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

    Stable across text lengths *above* the window size, which is why it is
    preferred to plain type-token ratio. At or below the window it falls back
    to plain TTR and is length-confounded like any TTR, so expect drift on
    texts shorter than `window` tokens and compare documents of broadly
    similar length.
    """
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    total = 0.0
    count = 0
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
