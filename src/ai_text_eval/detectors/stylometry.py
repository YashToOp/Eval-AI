"""Stylometric detector: statistical texture of the writing.

This is the family of signals GPTZero popularized ("burstiness") and that
classic authorship-attribution work has used for decades. Each feature is
converted to an AI-likelihood in [0, 1] via a logistic curve whose midpoint
and scale encode published/observed contrasts between LLM output and human
writing. The features are weak individually and deliberately transparent:
every raw value is returned in `details`.

Signals:
- **Burstiness** (coefficient of variation of sentence lengths). Humans mix
  short punchy sentences with long ones; LLM output is more uniform.
- **Sentence-length ceiling**: LLMs rarely produce very short (<4 word)
  sentences in expository prose.
- **Lexical diversity** (MATTR): instruction-tuned models recycle a
  comfortable mid-frequency vocabulary.
- **Word length / formality**: LLM register skews Latinate and polysyllabic.
- **Punctuation profile**: em-dash density (a famous post-2023 tell),
  semicolon rate, and the near-total absence of typos/irregular spacing.
"""

from __future__ import annotations

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import (
    coefficient_of_variation,
    logistic,
    mattr,
    mean,
    sentence_lengths,
    words,
)


def _feature_score(value: float, midpoint: float, scale: float, ai_is_high: bool) -> float:
    """Map a raw feature to [0,1] AI-likelihood with a logistic curve."""
    x = (value - midpoint) / scale
    if not ai_is_high:
        x = -x
    return logistic(x)


class StylometryDetector(Detector):
    name = "stylometry"

    # (midpoint, scale, ai_is_high) per feature — heuristic anchors chosen
    # from the demo corpora and the published literature; see README.
    CALIBRATION = {
        "burstiness": (0.42, 0.12, False),        # low sentence-length CV -> AI
        "short_sentence_rate": (0.08, 0.05, False),  # humans use short sentences
        "mattr": (0.72, 0.06, False),             # slightly lower diversity -> AI
        "mean_word_len": (4.55, 0.35, True),      # longer words -> AI
        "em_dash_per_kw": (2.0, 1.2, True),       # em-dash overuse -> AI
    }

    WEIGHTS = {
        "burstiness": 0.34,
        "short_sentence_rate": 0.16,
        "mattr": 0.14,
        "mean_word_len": 0.16,
        "em_dash_per_kw": 0.20,
    }

    def score(self, text: str) -> DetectorResult:
        toks = words(text)
        n_words = len(toks)
        if n_words == 0:
            return DetectorResult(score=0.5, reliable=False, details={"error": "empty text"})

        lengths = sentence_lengths(text)
        n_sents = len(lengths)
        per_kw = 1000.0 / n_words

        raw = {
            "burstiness": coefficient_of_variation([float(x) for x in lengths]),
            "short_sentence_rate": (
                sum(1 for x in lengths if x <= 4) / n_sents if n_sents else 0.0
            ),
            "mattr": mattr(toks),
            "mean_word_len": mean([float(len(t)) for t in toks]),
            "em_dash_per_kw": (text.count("—") + text.count(" -- ")) * per_kw,
        }

        # Burstiness/short-sentence stats need several sentences to exist.
        usable_weights = dict(self.WEIGHTS)
        if n_sents < 4:
            usable_weights["burstiness"] = 0.0
            usable_weights["short_sentence_rate"] = 0.0

        total_w = sum(usable_weights.values())
        score = sum(
            usable_weights[k] * _feature_score(raw[k], *self.CALIBRATION[k])
            for k in raw
        ) / total_w if total_w > 0 else 0.5

        reliable = n_words >= MIN_RELIABLE_WORDS and n_sents >= 4
        details = {**{k: round(v, 4) for k, v in raw.items()},
                   "n_words": n_words, "n_sentences": n_sents}
        return DetectorResult(score=score, reliable=reliable, details=details)
