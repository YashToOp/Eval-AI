"""Conformal false-positive control — the core of v2.

The 2025-26 literature is unanimous on one operational point: an eval engine
must be threshold-set by a **false-positive policy cap**, not by accuracy.
Accuracy at 0.5 is meaningless when the cost of a false accusation dwarfs the
cost of a miss, and the documented harms (non-native speakers, neurodivergent
writers, AAVE, formulaic genres) are all false positives.

Split conformal prediction gives a *distribution-free, finite-sample*
guarantee: calibrate on human-written text, and the resulting threshold
satisfies

    P(score(new human text) >= tau) <= alpha

for any exchangeable data and any underlying detector, with no assumption
about score distributions. Multiscale Conformal Prediction (Zhu et al., 2025)
extends this with length-dependent thresholds, which is what makes short-text
handling principled instead of a hand-set word count.

The property that matters most here is the one that *refuses*: if the
calibration set is too small to certify the requested alpha, the threshold is
+inf and the engine flags nothing. You cannot certify a 0.5% false-positive
rate with 18 calibration texts, and the honest response is to say so rather
than to print a number the data cannot support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Policy cap recommended by the strongest independent audit (Jabarian & Imas,
# Booth/BFI WP 2025-116): the only detector meeting FPR <= 0.5% was judged
# fit for decision-relevant use.
DEFAULT_FPR_CAP = 0.005


def min_calibration_size(alpha: float) -> int:
    """Smallest human calibration set that can certify an FPR cap of `alpha`.

    Split conformal takes the ceil((n+1)(1-alpha))-th order statistic. That
    index exceeds n — i.e. no finite threshold exists — unless n + 1 >= 1/alpha.
    So a 0.5% cap needs 199 human texts, a 1% cap needs 99, and a 5% cap
    needs 19. Below that the guarantee is unavailable at any threshold.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    return max(1, math.ceil(1.0 / alpha) - 1)


def conformal_threshold(calibration_scores: list[float], alpha: float) -> float:
    """One-sided split-conformal threshold at level `alpha`.

    Returns the smallest tau such that at most an alpha fraction of exchangeable
    human texts score >= tau, or +inf when the calibration set is too small to
    make that promise.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    n = len(calibration_scores)
    if n == 0:
        return math.inf
    k = math.ceil((n + 1) * (1.0 - alpha))
    if k > n:
        # The requested cap is finer than n+1 samples can resolve. Returning a
        # finite threshold here would be the exact failure this module exists
        # to prevent: a guarantee the data does not support.
        return math.inf
    return sorted(calibration_scores)[k - 1]


@dataclass
class ConformalCalibration:
    """A fitted FPR-capped decision rule.

    `bins` maps a length bucket to its own threshold (multiscale conformal);
    `global_threshold` is the fallback for lengths with too little calibration
    data. Both may be +inf, which means "never flag".
    """

    alpha: float
    global_threshold: float
    n_calibration: int
    bin_edges: list[int] = field(default_factory=list)
    bin_thresholds: dict[int, float] = field(default_factory=dict)
    bin_counts: dict[int, int] = field(default_factory=dict)
    language: str = "en"

    @property
    def is_certified(self) -> bool:
        """Whether any finite threshold can honor the requested cap."""
        return math.isfinite(self.global_threshold)

    @property
    def shortfall(self) -> int:
        """How many more human calibration texts the requested cap needs."""
        return max(0, min_calibration_size(self.alpha) - self.n_calibration)

    def _bin_index(self, n_words: int) -> int:
        idx = 0
        for edge in self.bin_edges:
            if n_words >= edge:
                idx += 1
            else:
                break
        return idx

    def threshold_for(self, n_words: int) -> float:
        """Length-appropriate threshold, falling back to the global one."""
        idx = self._bin_index(n_words)
        thr = self.bin_thresholds.get(idx)
        if thr is None or not math.isfinite(thr):
            return self.global_threshold
        return thr

    def flags(self, score: float, n_words: int) -> bool:
        """Whether this score clears the FPR-capped bar for a text this long."""
        return score >= self.threshold_for(n_words)

    def to_dict(self) -> dict:
        return {
            "alpha": self.alpha,
            "language": self.language,
            "n_calibration": self.n_calibration,
            "certified": self.is_certified,
            "shortfall": self.shortfall,
            "min_calibration_size": min_calibration_size(self.alpha),
            "global_threshold": (
                None if math.isinf(self.global_threshold) else round(self.global_threshold, 6)
            ),
            "bin_edges": self.bin_edges,
            "bin_thresholds": {
                str(k): (None if math.isinf(v) else round(v, 6))
                for k, v in sorted(self.bin_thresholds.items())
            },
            "bin_counts": {str(k): v for k, v in sorted(self.bin_counts.items())},
        }


# Length buckets in words. Short text is where detectors fail hardest and
# where the documented false positives concentrate, so the low buckets are
# narrow.
DEFAULT_BIN_EDGES = [50, 100, 250, 500]

# Below this many calibration texts a bucket cannot support its own threshold
# and defers to the global one.
MIN_BIN_CALIBRATION = 20


def calibrate(
    human_scores: list[float],
    human_lengths: list[int] | None = None,
    alpha: float = DEFAULT_FPR_CAP,
    bin_edges: list[int] | None = None,
    language: str = "en",
) -> ConformalCalibration:
    """Fit a conformal decision rule on human-written calibration text.

    Only human texts are used: the guarantee is about the rate at which human
    writing gets flagged, which is the quantity with real-world victims.
    """
    if not human_scores:
        raise ValueError("conformal calibration needs human-text scores")
    if human_lengths is not None and len(human_lengths) != len(human_scores):
        raise ValueError("human_scores and human_lengths must align")

    edges = DEFAULT_BIN_EDGES if bin_edges is None else sorted(bin_edges)
    cal = ConformalCalibration(
        alpha=alpha,
        global_threshold=conformal_threshold(human_scores, alpha),
        n_calibration=len(human_scores),
        bin_edges=edges,
        language=language,
    )

    if human_lengths is not None:
        buckets: dict[int, list[float]] = {}
        for score, length in zip(human_scores, human_lengths):
            buckets.setdefault(cal._bin_index(length), []).append(score)
        for idx, scores in buckets.items():
            cal.bin_counts[idx] = len(scores)
            cal.bin_thresholds[idx] = (
                conformal_threshold(scores, alpha)
                if len(scores) >= MIN_BIN_CALIBRATION
                else math.inf
            )
    return cal


def from_dict(payload: dict) -> ConformalCalibration:
    """Rebuild a calibration from its serialized form."""
    def _thr(v):
        return math.inf if v is None else float(v)

    return ConformalCalibration(
        alpha=float(payload["alpha"]),
        global_threshold=_thr(payload.get("global_threshold")),
        n_calibration=int(payload.get("n_calibration", 0)),
        bin_edges=[int(e) for e in payload.get("bin_edges", [])],
        bin_thresholds={int(k): _thr(v) for k, v in payload.get("bin_thresholds", {}).items()},
        bin_counts={int(k): int(v) for k, v in payload.get("bin_counts", {}).items()},
        language=payload.get("language", "en"),
    )


def save_calibrations(path, calibrations: dict[str, ConformalCalibration]) -> None:
    """Persist per-language calibrations to JSON."""
    import json
    from pathlib import Path

    Path(path).write_text(
        json.dumps({lang: c.to_dict() for lang, c in calibrations.items()}, indent=2),
        encoding="utf-8",
    )


def load_calibrations(path) -> dict[str, ConformalCalibration]:
    """Load per-language calibrations from JSON.

    Thresholds are per-language by design: perplexity and stylometric score
    distributions differ sharply across languages, so an English threshold
    applied to Hindi or Hinglish text has no guarantee attached to it at all.
    """
    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {lang: from_dict(data) for lang, data in payload.items()}


def empirical_fpr(calibration: ConformalCalibration,
                  human_scores: list[float],
                  human_lengths: list[int]) -> float:
    """Realized false-positive rate of a calibration on held-out human text.

    Use this on a *separate* split. Measured on the calibration set itself it
    reproduces alpha by construction and proves nothing.
    """
    if not human_scores:
        return 0.0
    flagged = sum(
        1 for s, n in zip(human_scores, human_lengths) if calibration.flags(s, n)
    )
    return flagged / len(human_scores)
