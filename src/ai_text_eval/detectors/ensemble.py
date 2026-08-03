"""Ensemble detector: combines the individual detectors into one verdict.

Two combination modes:

- **weighted average** (default): transparent fixed weights. Model-based
  detectors get more weight because they carry more signal.
- **calibrated logistic** (optional): fit a tiny pure-Python logistic
  regression on a labeled corpus via `fit()`, which both reweights the
  detectors and calibrates the output toward a probability.

The ensemble also owns the *abstention policy*: below MIN_RELIABLE_WORDS
the verdict is "insufficient evidence" no matter what the score says.
This is not a cosmetic disclaimer — at sentence length, every published
detector's accuracy collapses toward coin-flipping, and false accusations
are the costly error.
"""

from __future__ import annotations

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import logistic, words

DEFAULT_WEIGHTS = {
    "binoculars": 3.0,
    "perplexity": 2.5,
    "stylometry": 1.0,
    "phrases": 1.0,
}

VERDICT_BANDS = [
    (0.80, "likely AI-generated"),
    (0.60, "leaning AI-generated"),
    (0.40, "uncertain"),
    (0.20, "leaning human-written"),
    (0.00, "likely human-written"),
]


def verdict_for(score: float, reliable: bool) -> str:
    if not reliable:
        return "insufficient evidence (text too short for any detector to be reliable)"
    for threshold, label in VERDICT_BANDS:
        if score >= threshold:
            return label
    return "likely human-written"


class _LogisticModel:
    """Minimal logistic regression (batch gradient descent, L2)."""

    def __init__(self, n_features: int):
        self.w = [0.0] * n_features
        self.b = 0.0

    def predict(self, x: list[float]) -> float:
        return logistic(sum(wi * xi for wi, xi in zip(self.w, x)) + self.b)

    def fit(
        self,
        xs: list[list[float]],
        ys: list[int],
        lr: float = 0.5,
        iters: int = 800,
        l2: float = 1e-2,
    ) -> None:
        n = len(xs)
        if n == 0:
            raise ValueError("no training data")
        for _ in range(iters):
            grad_w = [l2 * wi for wi in self.w]
            grad_b = 0.0
            for x, y in zip(xs, ys):
                err = self.predict(x) - y
                for j, xj in enumerate(x):
                    grad_w[j] += err * xj / n
                grad_b += err / n
            self.w = [wi - lr * gi for wi, gi in zip(self.w, grad_w)]
            self.b -= lr * grad_b


class EnsembleDetector(Detector):
    name = "ensemble"

    def __init__(self, detectors: dict[str, Detector], weights: dict[str, float] | None = None):
        if not detectors:
            raise ValueError("EnsembleDetector needs at least one detector")
        self.detectors = detectors
        # `weights or DEFAULT_WEIGHTS` would silently swap in the defaults for
        # an explicitly-passed empty dict, i.e. exactly when the caller asked
        # for no preset weighting at all.
        source = DEFAULT_WEIGHTS if weights is None else weights
        self.weights = {n: source.get(n, 1.0) for n in detectors}
        self._logistic: _LogisticModel | None = None
        self._feature_order: list[str] = sorted(detectors)

    # -- combination -------------------------------------------------

    def _combine(self, sub: dict[str, DetectorResult]) -> float:
        if self._logistic is not None:
            x = [sub[n].score for n in self._feature_order]
            return self._logistic.predict(x)
        total_w = sum(self.weights[n] for n in sub)
        if total_w == 0:
            return 0.5
        return sum(self.weights[n] * r.score for n, r in sub.items()) / total_w

    def combine_results(self, sub: dict[str, DetectorResult], n_words: int) -> DetectorResult:
        """Combine already-computed sub-detector results.

        Exposed so callers that need both the ensemble score and the
        individual scores — the benchmark loop does — can score each text
        once instead of once per detector plus once more for the ensemble.
        """
        combined = self._combine(sub)
        reliable = n_words >= MIN_RELIABLE_WORDS and any(r.reliable for r in sub.values())
        details = {
            "detectors": {n: {"score": round(r.score, 4), "reliable": r.reliable}
                          for n, r in sub.items()},
            "combination": "logistic" if self._logistic else "weighted_average",
            "n_words": n_words,
        }
        return DetectorResult(score=combined, reliable=reliable, details=details)

    def score(self, text: str) -> DetectorResult:
        return self.score_verbose(text)[0]

    def score_verbose(self, text: str) -> tuple[DetectorResult, dict[str, DetectorResult]]:
        """Like score(), but also return the per-detector results."""
        sub = {name: det.score(text) for name, det in self.detectors.items()}
        return self.combine_results(sub, len(words(text))), sub

    # -- calibration -------------------------------------------------

    def fit(self, texts: list[str], labels: list[int]) -> None:
        """Fit the logistic combiner on labeled texts (1 = AI, 0 = human)."""
        if len(texts) != len(labels):
            raise ValueError("texts and labels must align")
        if len(set(labels)) < 2:
            raise ValueError("need both classes to calibrate")
        xs = []
        for t in texts:
            sub = {name: det.score(t) for name, det in self.detectors.items()}
            xs.append([sub[n].score for n in self._feature_order])
        model = _LogisticModel(len(self._feature_order))
        model.fit(xs, list(labels))
        self._logistic = model

    @property
    def calibration_summary(self) -> dict | None:
        if self._logistic is None:
            return None
        return {
            "features": self._feature_order,
            "weights": [round(w, 4) for w in self._logistic.w],
            "bias": round(self._logistic.b, 4),
        }
