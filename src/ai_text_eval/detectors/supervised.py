"""Supervised detector — the primary layer in v2.

The 2025-26 evidence is decisive: at the low false-positive rates an eval
engine actually needs, supervised and adversarially-trained classifiers
dominate zero-shot statistical methods. The independent Booth/BFI audit found
only a supervised commercial system met a strict FPR <= 0.5% cap, and the
RAID-based COLING 2025 shared task was won by a supervised entrant at 99.3%
clean / 97.7% adversarial TPR at 5% FPR. So v2 re-architects around a
supervised primary with the zero-shot detectors demoted to corroboration and
out-of-distribution tripwires.

**What this class is and is not.** It is a regularized logistic regression
over interpretable features, trained in pure Python, with standardization and
an explicit train/test discipline. It is a *reference implementation of the
architectural slot*, not a competitor to a system trained on continuously
refreshed billion-token corpora with adversarial augmentation. Expect it to
behave like a decent baseline and to lose badly to any real supervised
detector. `TransformerClassifier` below is the interface for plugging one in.

The features deliberately include the zero-shot detector scores. That is the
recommended composition: the supervised layer learns how much to trust each
statistical signal on *your* data instead of using the hand-set weights that
v1 shipped with.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import (
    coefficient_of_variation,
    logistic,
    mattr,
    mean,
    sentence_lengths,
    sentences,
    stdev,
    words,
)


def extract_features(text: str, sub_scores: dict[str, float] | None = None) -> dict[str, float]:
    """Interpretable feature vector. Deterministic and dependency-free."""
    toks = words(text)
    n_words = len(toks)
    if n_words == 0:
        return {}

    lengths = [float(x) for x in sentence_lengths(text)]
    n_sents = max(1, len(lengths))
    per_kw = 1000.0 / n_words
    word_lens = [float(len(t)) for t in toks]

    feats = {
        "burstiness": coefficient_of_variation(lengths),
        "mean_sentence_len": mean(lengths),
        "sd_sentence_len": stdev(lengths),
        "short_sentence_rate": sum(1 for x in lengths if x <= 4) / n_sents,
        "long_sentence_rate": sum(1 for x in lengths if x >= 30) / n_sents,
        "mattr": mattr(toks),
        "mean_word_len": mean(word_lens),
        "sd_word_len": stdev(word_lens),
        "long_word_rate": sum(1 for w in toks if len(w) >= 8) / n_words,
        "em_dash_per_kw": (text.count("—") + text.count(" -- ")) * per_kw,
        "semicolon_per_kw": text.count(";") * per_kw,
        "colon_per_kw": text.count(":") * per_kw,
        "comma_per_kw": text.count(",") * per_kw,
        "question_per_kw": text.count("?") * per_kw,
        "exclaim_per_kw": text.count("!") * per_kw,
        "quote_per_kw": (text.count('"') + text.count("“")) * per_kw,
        "paren_per_kw": text.count("(") * per_kw,
        "digit_rate": sum(1 for c in text if c.isdigit()) / max(1, len(text)),
        "uppercase_rate": sum(1 for c in text if c.isupper()) / max(1, len(text)),
        "contraction_per_kw": sum(1 for t in toks if "'" in t) * per_kw,
        "first_person_per_kw": sum(1 for t in toks if t in {"i", "i'm", "i've", "my", "me"}) * per_kw,
        "hedge_per_kw": sum(
            1 for t in toks if t in {"maybe", "probably", "roughly", "sort", "kind", "seems", "guess"}
        ) * per_kw,
        "log_n_words": math.log1p(n_words),
    }
    if sub_scores:
        for name, value in sub_scores.items():
            feats[f"detector_{name}"] = value
    return feats


@dataclass
class _Standardizer:
    means: dict[str, float] = field(default_factory=dict)
    sds: dict[str, float] = field(default_factory=dict)

    def fit(self, rows: list[dict[str, float]], keys: list[str]) -> None:
        for k in keys:
            vals = [r.get(k, 0.0) for r in rows]
            m = mean(vals)
            s = stdev(vals)
            self.means[k] = m
            # A zero-variance feature carries no information; mapping it to 0
            # keeps it from dividing by zero and from acquiring a weight.
            self.sds[k] = s if s > 1e-9 else 1.0

    def transform(self, row: dict[str, float], keys: list[str]) -> list[float]:
        return [(row.get(k, 0.0) - self.means[k]) / self.sds[k] for k in keys]


class SupervisedDetector(Detector):
    """L2-regularized logistic regression over interpretable features."""

    name = "supervised"

    def __init__(self, corroborators: dict[str, Detector] | None = None, l2: float = 0.05):
        self.corroborators = corroborators or {}
        self.l2 = l2
        self._keys: list[str] = []
        self._w: list[float] = []
        self._b: float = 0.0
        self._scaler = _Standardizer()
        self._fitted = False
        self._n_train = 0

    # -- training --------------------------------------------------------

    def _row(self, text: str) -> dict[str, float]:
        sub = {n: d.score(text).score for n, d in self.corroborators.items()}
        return extract_features(text, sub)

    def fit(
        self,
        texts: list[str],
        labels: list[int],
        iters: int = 3000,
        lr: float = 0.25,
    ) -> None:
        """Train on labeled texts (1 = AI, 0 = human).

        Whatever you evaluate on must not appear here. `cross_val_scores`
        exists precisely so the benchmark does not report training accuracy.
        """
        if len(texts) != len(labels):
            raise ValueError("texts and labels must align")
        if len(set(labels)) < 2:
            raise ValueError("need both classes to train")

        rows = [self._row(t) for t in texts]
        rows = [r for r in rows if r]
        if len(rows) != len(texts):
            raise ValueError("every training text must be non-empty")

        self._keys = sorted({k for r in rows for k in r})
        self._scaler = _Standardizer()
        self._scaler.fit(rows, self._keys)
        xs = [self._scaler.transform(r, self._keys) for r in rows]

        self._w = [0.0] * len(self._keys)
        self._b = 0.0
        n = len(xs)
        for _ in range(iters):
            grad_w = [self.l2 * wi for wi in self._w]
            grad_b = 0.0
            for x, y in zip(xs, labels):
                err = logistic(sum(wi * xi for wi, xi in zip(self._w, x)) + self._b) - y
                for j, xj in enumerate(x):
                    grad_w[j] += err * xj / n
                grad_b += err / n
            self._w = [wi - lr * g for wi, g in zip(self._w, grad_w)]
            self._b -= lr * grad_b
        self._fitted = True
        self._n_train = n

    # -- inference -------------------------------------------------------

    def score(self, text: str) -> DetectorResult:
        toks = words(text)
        n_words = len(toks)
        if n_words == 0:
            return DetectorResult(score=0.5, reliable=False, details={"error": "empty text"})
        if not self._fitted:
            # Untrained is not "0.5 with confidence"; it is no signal at all.
            return DetectorResult(
                score=0.5,
                reliable=False,
                details={"error": "SupervisedDetector is not fitted", "n_words": n_words},
            )
        row = self._row(text)
        x = self._scaler.transform(row, self._keys)
        score = logistic(sum(wi * xi for wi, xi in zip(self._w, x)) + self._b)
        return DetectorResult(
            score=score,
            reliable=n_words >= MIN_RELIABLE_WORDS,
            details={
                "n_words": n_words,
                "n_sentences": len(sentences(text)),
                "n_train": self._n_train,
                "top_features": self.top_features(3),
            },
        )

    def top_features(self, k: int = 8) -> list[tuple[str, float]]:
        if not self._fitted:
            return []
        pairs = sorted(zip(self._keys, self._w), key=lambda p: -abs(p[1]))
        return [(name, round(w, 4)) for name, w in pairs[:k]]

    @property
    def is_fitted(self) -> bool:
        return self._fitted


def cross_val_scores(
    make_detector,
    texts: list[str],
    labels: list[int],
    n_folds: int = 5,
    seed: int = 0,
) -> list[float]:
    """Out-of-fold scores, so a trained detector can be honestly benchmarked.

    Returns one score per input text, each produced by a model that never saw
    that text. Reporting in-sample scores for a trained detector is the single
    easiest way to publish a number that means nothing — it is why v1 flagged
    its own hand-set constants as in-sample, and a trainable layer makes the
    hazard much worse.
    """
    if len(texts) != len(labels):
        raise ValueError("texts and labels must align")
    n = len(texts)
    if n_folds < 2 or n_folds > n:
        raise ValueError(f"n_folds must be in [2, {n}]")

    # Stratified, deterministic fold assignment: each class is dealt round
    # robin so every fold keeps both classes.
    import random

    rng = random.Random(seed)
    by_class: dict[int, list[int]] = {0: [], 1: []}
    for i, y in enumerate(labels):
        by_class[y].append(i)
    fold_of = [0] * n
    for cls, idxs in by_class.items():
        rng.shuffle(idxs)
        for pos, i in enumerate(idxs):
            fold_of[i] = pos % n_folds

    out = [0.5] * n
    for fold in range(n_folds):
        train_idx = [i for i in range(n) if fold_of[i] != fold]
        test_idx = [i for i in range(n) if fold_of[i] == fold]
        if not test_idx or len({labels[i] for i in train_idx}) < 2:
            continue
        det = make_detector()
        det.fit([texts[i] for i in train_idx], [labels[i] for i in train_idx])
        for i in test_idx:
            out[i] = det.score(texts[i]).score
    return out


class TransformerClassifier(Detector):
    """Interface for a real supervised detector (DeBERTa/RoBERTa fine-tune, API).

    Not implemented here — it needs weights this repo does not ship. Subclass
    and implement `score` to slot a production classifier into the primary
    layer; the ensemble, conformal calibration, and verdict machinery all work
    unchanged, because they only require a score in [0, 1].
    """

    name = "transformer"

    def score(self, text: str) -> DetectorResult:  # pragma: no cover - interface
        raise NotImplementedError(
            "TransformerClassifier is an integration point, not an implementation. "
            "Subclass it and implement score() with your own model or API."
        )
