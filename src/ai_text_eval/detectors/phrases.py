"""Lexical-forensics detector: the "AI-isms".

Corpus studies after ChatGPT's release measured large frequency spikes for
specific words and constructions in LLM output relative to pre-2023 human
baselines ("delve", "tapestry", "underscores", "plays a crucial role",
"it's important to note", the not-only-but-also frame, triadic lists).
This detector counts a weighted lexicon of those markers per 1000 words,
plus a few structural patterns, and squashes the weighted rate to [0, 1].

It is an *evidence accumulator*: one marker means nothing; a pile of them
in a short span is a strong signal. It is also trivially evadable by
paraphrase — which is exactly why the evasion benchmark exists.
"""

from __future__ import annotations

import math
import re

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import sentences, words

# phrase -> weight. Weights reflect how discriminative the marker is
# (how much more frequent in LLM output than in human baselines).
LEXICON: dict[str, float] = {
    # single words with measured post-2023 frequency spikes
    "delve": 3.0, "delves": 3.0, "delving": 3.0,
    "tapestry": 3.0,
    "underscore": 2.0, "underscores": 2.5, "underscoring": 2.5,
    "testament to": 2.5,
    "multifaceted": 2.0,
    "intricate": 1.5, "intricacies": 2.0,
    "pivotal": 1.5,
    "meticulous": 1.5, "meticulously": 2.0,
    "seamless": 1.5, "seamlessly": 2.0,
    "leverage": 1.0, "leveraging": 1.5,
    "foster": 1.0, "fostering": 1.5,
    "garner": 1.5, "garnered": 1.5,
    "boast": 1.5, "boasts": 2.0, "boasting": 1.5,
    "vibrant": 1.5,
    "bustling": 2.0,
    "realm": 1.5, "realms": 1.5,
    "landscape": 1.0, "landscapes": 1.0,   # metaphorical use dominates in LLM text
    "beacon": 2.0,
    "crucible": 2.0,
    "paradigm": 1.5,
    "holistic": 1.5,
    "robust": 1.0,
    "showcase": 1.5, "showcasing": 2.0, "showcases": 1.5,
    "elevate": 1.5, "elevates": 1.5,
    "embark": 2.0, "embarking": 2.0,
    "unleash": 2.0, "unlock": 1.0, "unlocking": 1.5,
    "invaluable": 1.5,
    "commendable": 2.0,
    "noteworthy": 1.5,
    "unwavering": 2.0,
    "ever-evolving": 2.5, "ever-changing": 2.0,
    "game-changer": 2.0, "game changer": 2.0,
    "transformative": 1.5,
    "revolutionize": 2.0, "revolutionizing": 2.0,
    "synergy": 1.5,
    "empower": 1.5, "empowering": 1.5, "empowers": 1.5,
    "resonate": 1.5, "resonates": 1.5,
    "captivating": 1.5,
    "enduring": 1.0,
    "profound": 1.0,
    "myriad": 1.5,
    "plethora": 2.0,
    "nuanced": 1.5, "nuances": 1.5,
    # discourse-marker phrases
    "it's important to note": 3.0, "it is important to note": 3.0,
    "it's worth noting": 3.0, "it is worth noting": 3.0,
    "it should be noted": 2.5,
    "in today's fast-paced world": 3.0, "in today's digital age": 3.0,
    "in the ever-evolving": 3.0,
    "in the realm of": 2.5,
    "at the heart of": 1.5,
    "when it comes to": 1.0,
    "plays a crucial role": 3.0, "plays a vital role": 3.0,
    "plays a pivotal role": 3.0, "play a crucial role": 3.0,
    "a wide range of": 1.5, "a wide array of": 2.0, "a rich tapestry": 3.0,
    "serves as a": 1.5,
    "stands as a": 2.0,
    "look no further": 2.5,
    "in conclusion": 2.0,
    "in summary": 1.5,
    "furthermore": 1.5,
    "moreover": 1.5,
    "additionally": 1.0,
    "overall": 0.5,
    "ultimately": 1.0,
    "navigating the": 2.0,
    "the world of": 1.0,
    "dive into": 1.5, "dive deeper": 2.0, "deep dive": 1.5,
    "shed light on": 2.0, "sheds light on": 2.0,
    "paving the way": 2.5,
    "pushing the boundaries": 2.5,
    "a double-edged sword": 2.5,
    "cannot be overstated": 3.0,
    "continues to evolve": 2.5,
    "remains to be seen": 2.0,
    "as we navigate": 2.5,
    "treasure trove": 2.5,
    "whether you're a": 2.5, "whether you are a": 2.5,
    "here are some": 1.0,
    "let's explore": 2.0, "let's dive": 2.0,
    "key takeaways": 2.0,
    "actionable insights": 2.5,
    "best practices": 1.0,
    "harness the power": 3.0, "harnessing the power": 3.0,
}

# Structural patterns (regexes) with weights.
STRUCTURAL: list[tuple[str, re.Pattern, float]] = [
    ("not_only_but_also", re.compile(r"\bnot only\b.{1,120}?\bbut also\b", re.I | re.S), 2.0),
    ("from_x_to_y_frame", re.compile(r"\bfrom \w+(?: \w+)? to \w+(?: \w+)?, ", re.I), 1.0),
    ("bullet_with_bold_lead", re.compile(r"^\s*[-*•]\s*\*\*[^*]+\*\*[:,]", re.M), 1.5),
    ("numbered_bold_lead", re.compile(r"^\s*\d+\.\s*\*\*[^*]+\*\*[:,]", re.M), 1.5),
    ("colon_triad_header", re.compile(r"^#{1,4}\s", re.M), 0.5),
]

_TRIAD_RE = re.compile(r"\b\w[\w'’-]*, \w[\w'’-]*(?: \w[\w'’-]*)?, and \w[\w'’-]*", re.I)


def _lexicon_hits(text_lower: str) -> dict[str, int]:
    hits: dict[str, int] = {}
    for phrase in LEXICON:
        if " " in phrase or "-" in phrase:
            n = text_lower.count(phrase)
        else:
            n = len(re.findall(r"\b" + re.escape(phrase) + r"\b", text_lower))
        if n:
            hits[phrase] = n
    return hits


class PhraseDetector(Detector):
    name = "phrases"

    # Rate (weighted hits per 1000 words) at which the score reaches ~0.63.
    RATE_SCALE = 18.0

    def score(self, text: str) -> DetectorResult:
        toks = words(text)
        n_words = len(toks)
        if n_words == 0:
            return DetectorResult(score=0.5, reliable=False, details={"error": "empty text"})

        text_lower = text.lower()
        hits = _lexicon_hits(text_lower)
        weighted = sum(LEXICON[p] * n for p, n in hits.items())

        structural_hits: dict[str, int] = {}
        for label, pattern, weight in STRUCTURAL:
            n = len(pattern.findall(text))
            if n:
                structural_hits[label] = n
                weighted += weight * n

        # Triadic lists ("clear, concise, and compelling") — count per sentence
        # so list-heavy prose is flagged, but a single triad is not.
        n_sents = max(1, len(sentences(text)))
        triads = len(_TRIAD_RE.findall(text))
        triad_rate = triads / n_sents
        if triad_rate > 0.25:
            weighted += 2.0 * triads

        rate = weighted * 1000.0 / n_words
        # 0 hits -> 0.5 would overstate ignorance: absence of markers in a
        # long text is mild evidence of human authorship, so map rate 0 to
        # a floor below 0.5 and saturate toward 1.0 as the rate grows.
        floor = 0.30
        score = floor + (1.0 - floor) * (1.0 - math.exp(-rate / self.RATE_SCALE))

        details = {
            "weighted_rate_per_kw": round(rate, 3),
            "lexicon_hits": dict(sorted(hits.items(), key=lambda kv: -LEXICON[kv[0]] * kv[1])[:12]),
            "structural_hits": structural_hits,
            "triads": triads,
            "n_words": n_words,
        }
        return DetectorResult(
            score=score,
            reliable=n_words >= MIN_RELIABLE_WORDS,
            details=details,
        )
