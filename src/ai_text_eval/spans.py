"""Span-level analysis: where in a document the AI signal lives.

Document-level binary classification is the wrong output shape for real
documents, which are increasingly hybrid — a human draft polished by a model,
or a generated draft edited by a person. The field moved to sentence and
boundary detection (SeqXGPT, TM-TREK, change-point methods) and to explicit
"degree of AI involvement" estimation.

Two things make this tractable without a sequence-labeling model:

1. **Overlapping windows, not sentences.** Scoring a single sentence is
   exactly the operation this framework refuses to do, because 15 words carry
   no evidence. So we score windows of `window_words` and let each sentence
   inherit the average of the windows covering it. Evidence per decision stays
   above the reliability floor even though the output is per-sentence.
2. **Change-point detection.** The most common hybrid document has one seam:
   human intro then generated body, or generated draft with a human ending.
   A max-t scan over split points finds the seam and reports how much the mean
   score actually shifts across it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ai_text_eval.detectors.base import Detector
from ai_text_eval.text_features import sentences, words

# Windows must clear the evidence floor; the whole point is to avoid making
# per-sentence judgements from per-sentence evidence.
DEFAULT_WINDOW_WORDS = 120
DEFAULT_STRIDE_WORDS = 60


@dataclass
class SentenceSpan:
    index: int
    text: str
    n_words: int
    score: float
    n_windows: int  # how many windows contributed evidence

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "n_words": self.n_words,
            "score": round(self.score, 4),
            "n_windows": self.n_windows,
        }


@dataclass
class ChangePoint:
    """Best single seam in the document, by mean-score shift."""

    sentence_index: int
    delta: float           # mean(after) - mean(before); sign gives direction
    mean_before: float
    mean_after: float
    is_significant: bool

    def to_dict(self) -> dict:
        return {
            "sentence_index": self.sentence_index,
            "delta": round(self.delta, 4),
            "mean_before": round(self.mean_before, 4),
            "mean_after": round(self.mean_after, 4),
            "significant": self.is_significant,
        }


@dataclass
class SpanAnalysis:
    spans: list[SentenceSpan] = field(default_factory=list)
    change_point: ChangePoint | None = None
    threshold: float = 0.5
    window_words: int = DEFAULT_WINDOW_WORDS
    reliable: bool = True

    @property
    def total_words(self) -> int:
        return sum(s.n_words for s in self.spans)

    @property
    def resolution_words(self) -> int:
        """Finest seam this analysis can localize — one window width.

        Sentences within a window of a boundary inherit evidence from both
        sides, so a seam is only ever located to within about this many words,
        and any segment shorter than this cannot be resolved at all.
        """
        return self.window_words

    @property
    def ai_word_fraction(self) -> float:
        """Share of the document's words in spans scoring above threshold.

        Word-weighted, not sentence-weighted: a document of one long generated
        paragraph and six short human asides is mostly generated, and counting
        sentences would say the opposite.

        **Biased toward the majority class.** Because windows straddle the
        seam, text within `resolution_words` of a boundary is pulled toward
        the other side's score, so the fraction overstates whichever class
        dominates. Shrinking the window does not fix this: below the evidence
        floor the detector's scores drift upward, so narrower windows trade
        smearing for false positives and the estimate gets worse, not better.
        Read this as an approximate proportion, never as a word count.
        """
        total = self.total_words
        if not total:
            return 0.0
        ai_words = sum(s.n_words for s in self.spans if s.score >= self.threshold)
        return ai_words / total

    def to_dict(self, include_spans: bool = True) -> dict:
        out = {
            "ai_word_fraction": round(self.ai_word_fraction, 4),
            "n_sentences": len(self.spans),
            "total_words": self.total_words,
            "window_words": self.window_words,
            "threshold": self.threshold,
            "reliable": self.reliable,
            "change_point": self.change_point.to_dict() if self.change_point else None,
        }
        if include_spans:
            out["spans"] = [s.to_dict() for s in self.spans]
        return out


def _windows(sents: list[str], window_words: int, stride_words: int) -> list[tuple[int, int]]:
    """Sentence index ranges [start, end) whose word count covers the window."""
    lengths = [len(words(s)) for s in sents]
    n = len(sents)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        total = 0
        end = start
        while end < n and total < window_words:
            total += lengths[end]
            end += 1
        spans.append((start, end))
        if end >= n:
            break
        # Advance by roughly `stride_words` worth of sentences.
        advanced = 0
        new_start = start
        while new_start < n - 1 and advanced < stride_words:
            advanced += lengths[new_start]
            new_start += 1
        if new_start == start:
            new_start += 1
        start = new_start
    return spans


def find_change_point(scores: list[float], min_segment: int = 2,
                      min_delta: float = 0.25) -> ChangePoint | None:
    """Scan for the split that best separates the score sequence.

    Ranks candidate splits by the standard two-sample statistic

        |mean_after - mean_before| * sqrt(n_before * n_after / n)

    rather than by the raw mean difference. The raw difference is maximized
    by lopping off one extreme sentence — a 3-vs-15 split beats the real
    seam simply because a 3-sentence segment has a more extreme mean — so
    the unweighted version reliably reports a boundary near whichever end of
    the document is most extreme instead of where the writing actually
    changes hands.
    """
    n = len(scores)
    if n < 2 * min_segment:
        return None
    best: ChangePoint | None = None
    best_stat = -1.0
    for cut in range(min_segment, n - min_segment + 1):
        before = scores[:cut]
        after = scores[cut:]
        mb = sum(before) / len(before)
        ma = sum(after) / len(after)
        delta = ma - mb
        stat = abs(delta) * math.sqrt(len(before) * len(after) / n)
        if stat > best_stat:
            best_stat = stat
            best = ChangePoint(
                sentence_index=cut,
                delta=delta,
                mean_before=mb,
                mean_after=ma,
                is_significant=abs(delta) >= min_delta,
            )
    return best


def analyze_spans(
    detector: Detector,
    text: str,
    threshold: float = 0.5,
    window_words: int = DEFAULT_WINDOW_WORDS,
    stride_words: int = DEFAULT_STRIDE_WORDS,
) -> SpanAnalysis:
    """Score a document window-by-window and attribute scores to sentences."""
    sents = sentences(text)
    if not sents:
        return SpanAnalysis(threshold=threshold, window_words=window_words, reliable=False)

    ranges = _windows(sents, window_words, stride_words)
    window_texts = [" ".join(sents[a:b]) for a, b in ranges]
    window_results = detector.score_many(window_texts)

    totals = [0.0] * len(sents)
    counts = [0] * len(sents)
    for (a, b), result in zip(ranges, window_results):
        for i in range(a, b):
            totals[i] += result.score
            counts[i] += 1

    spans = [
        SentenceSpan(
            index=i,
            text=s,
            n_words=len(words(s)),
            score=(totals[i] / counts[i]) if counts[i] else 0.5,
            n_windows=counts[i],
        )
        for i, s in enumerate(sents)
    ]

    total_words = sum(s.n_words for s in spans)
    analysis = SpanAnalysis(
        spans=spans,
        threshold=threshold,
        window_words=window_words,
        # Span structure is only evidence when windows can actually resolve
        # it. One window gives every sentence the same document score; a
        # document barely longer than one window gives heavily overlapping
        # windows that smear a real seam across the whole text, which reads
        # as "100% AI" for a document that is half human.
        reliable=len(ranges) >= 3 and total_words >= 2 * window_words,
    )
    if analysis.reliable:
        analysis.change_point = find_change_point([s.score for s in spans])
    return analysis
