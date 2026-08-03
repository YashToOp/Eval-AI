"""Evasion (paraphrase-attack) evaluation.

The single most important robustness result in this literature: running
AI text through a paraphraser (DIPPER, or a human "rephrase this so it
doesn't look like AI") collapses most detectors. A serious eval therefore
measures not just clean AUROC but *score retention under attack*:

    for each (original AI text, rephrased AI text) pair:
        delta = score(original) - score(rephrased)

Both texts are AI-generated (a paraphrase of AI text is still AI text —
that's the ground truth), so any score drop is pure detector failure.
We report the mean drop per detector and the *evasion rate*: how often a
text that was flagged at threshold t escapes below t after rephrasing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_text_eval.dataset import EvasionPair
from ai_text_eval.detectors.base import Detector


@dataclass
class PairOutcome:
    score_original: float
    score_rephrased: float

    @property
    def delta(self) -> float:
        return self.score_original - self.score_rephrased


@dataclass
class EvasionReport:
    detector: str
    threshold: float
    outcomes: list[PairOutcome] = field(default_factory=list)

    @property
    def n_pairs(self) -> int:
        return len(self.outcomes)

    @property
    def mean_delta(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(o.delta for o in self.outcomes) / len(self.outcomes)

    @property
    def n_flagged_originals(self) -> int:
        return sum(1 for o in self.outcomes if o.score_original >= self.threshold)

    @property
    def n_evaded(self) -> int:
        """Originals that were flagged whose rephrasing escaped the flag."""
        return sum(
            1
            for o in self.outcomes
            if o.score_original >= self.threshold and o.score_rephrased < self.threshold
        )

    @property
    def evasion_rate(self) -> float | None:
        """Share of flagged originals that escaped after rephrasing.

        None, not 0.0, when the detector flagged nothing: the rate is
        undefined, and 0.0 is the *best* value on a "higher = weaker" scale,
        so returning it would rank a detector that never fires above a strong
        but evadable one.
        """
        flagged = self.n_flagged_originals
        return self.n_evaded / flagged if flagged else None

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "threshold": self.threshold,
            "n_pairs": self.n_pairs,
            "mean_score_original": round(
                sum(o.score_original for o in self.outcomes) / self.n_pairs, 4
            ) if self.n_pairs else None,
            "mean_score_rephrased": round(
                sum(o.score_rephrased for o in self.outcomes) / self.n_pairs, 4
            ) if self.n_pairs else None,
            "mean_delta": round(self.mean_delta, 4),
            "flagged_originals": self.n_flagged_originals,
            "evaded": self.n_evaded,
            "evasion_rate": (
                round(self.evasion_rate, 4) if self.evasion_rate is not None else None
            ),
        }


def evaluate_evasion(
    detector: Detector,
    pairs: list[EvasionPair],
    threshold: float = 0.5,
) -> EvasionReport:
    report = EvasionReport(detector=detector.name, threshold=threshold)
    originals = detector.score_many([p.original for p in pairs])
    rephrased = detector.score_many([p.rephrased for p in pairs])
    for o, r in zip(originals, rephrased):
        report.outcomes.append(PairOutcome(o.score, r.score))
    return report
