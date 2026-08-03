"""Benchmark metrics for detection evals — pure Python, deterministic.

These are the metrics the detection literature actually reports:

- **AUROC** — threshold-free ranking quality (computed via the
  Mann-Whitney U statistic with proper tie handling).
- **TPR @ fixed FPR** — the headline number in modern papers
  (Binoculars reports TPR at 0.01% FPR). In deployment the cost of a
  false accusation dwarfs the cost of a miss, so performance at *low*
  false-positive rates is what matters, not accuracy at 0.5.
- **Accuracy / precision / recall / F1** at a chosen threshold.
- **Brier score** and **ECE** — is the score a calibrated probability?
- **Bootstrap confidence intervals** — demo-sized corpora make point
  estimates almost meaningless; every AUROC we report carries a CI.

Labels: 1 = AI-generated (positive class), 0 = human-written.
Scores: higher = more AI-like.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


def _validate(labels: list[int], scores: list[float]) -> None:
    if len(labels) != len(scores):
        raise ValueError("labels and scores must have the same length")
    if not labels:
        raise ValueError("empty inputs")
    if any(l not in (0, 1) for l in labels):
        raise ValueError("labels must be 0 (human) or 1 (AI)")


def _average_ranks(values: list[float]) -> list[float]:
    """1-based ranks with ties assigned the average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def auroc(labels: list[int], scores: list[float]) -> float:
    """Area under the ROC curve via the rank-sum (Mann-Whitney) identity."""
    _validate(labels, scores)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("AUROC needs both classes present")
    ranks = _average_ranks(scores)
    rank_sum_pos = sum(r for r, l in zip(ranks, labels) if l == 1)
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def roc_points(labels: list[int], scores: list[float]) -> list[tuple[float, float, float]]:
    """ROC curve as (fpr, tpr, threshold), threshold descending.

    Classification rule: predict AI when score >= threshold.
    """
    _validate(labels, scores)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("ROC needs both classes present")
    pairs = sorted(zip(scores, labels), key=lambda p: -p[0])
    points: list[tuple[float, float, float]] = [(0.0, 0.0, float("inf"))]
    tp = fp = 0
    i = 0
    n = len(pairs)
    while i < n:
        threshold = pairs[i][0]
        while i < n and pairs[i][0] == threshold:
            if pairs[i][1] == 1:
                tp += 1
            else:
                fp += 1
            i += 1
        points.append((fp / n_neg, tp / n_pos, threshold))
    return points


def tpr_at_fpr(labels: list[int], scores: list[float], max_fpr: float) -> tuple[float, float]:
    """Best TPR achievable while keeping FPR <= max_fpr.

    Returns (tpr, threshold). With small corpora the achievable FPR grid is
    coarse (multiples of 1/n_neg); we report the best point on that grid.
    """
    best_tpr, best_thr = 0.0, float("inf")
    for fpr, tpr, thr in roc_points(labels, scores):
        if fpr <= max_fpr and tpr > best_tpr:
            best_tpr, best_thr = tpr, thr
    return best_tpr, best_thr


def confusion_at(labels: list[int], scores: list[float], threshold: float) -> dict[str, int]:
    _validate(labels, scores)
    tp = fp = tn = fn = 0
    for l, s in zip(labels, scores):
        pred = 1 if s >= threshold else 0
        if pred == 1 and l == 1:
            tp += 1
        elif pred == 1 and l == 0:
            fp += 1
        elif pred == 0 and l == 0:
            tn += 1
        else:
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def classification_metrics(labels: list[int], scores: list[float], threshold: float = 0.5) -> dict[str, float]:
    c = confusion_at(labels, scores, threshold)
    tp, fp, tn, fn = c["tp"], c["fp"], c["tn"], c["fn"]
    n = tp + fp + tn + fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "accuracy": (tp + tn) / n,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        **c,
    }


def brier_score(labels: list[int], scores: list[float]) -> float:
    _validate(labels, scores)
    return sum((s - l) ** 2 for l, s in zip(labels, scores)) / len(labels)


def expected_calibration_error(labels: list[int], scores: list[float], n_bins: int = 10) -> float:
    """ECE with equal-width bins over [0, 1]."""
    _validate(labels, scores)
    bins: list[list[tuple[int, float]]] = [[] for _ in range(n_bins)]
    for l, s in zip(labels, scores):
        idx = min(int(s * n_bins), n_bins - 1)
        bins[idx].append((l, s))
    n = len(labels)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_conf = sum(s for _, s in bucket) / len(bucket)
        avg_acc = sum(l for l, _ in bucket) / len(bucket)
        ece += len(bucket) / n * abs(avg_conf - avg_acc)
    return ece


def bootstrap_ci(
    labels: list[int],
    scores: list[float],
    statistic,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 1234,
) -> tuple[float, float]:
    """Percentile bootstrap CI for any (labels, scores) -> float statistic.

    Resamples are drawn per class (stratified) so every resample contains
    both classes; AUROC is undefined otherwise.
    """
    _validate(labels, scores)
    rng = random.Random(seed)
    pos_idx = [i for i, l in enumerate(labels) if l == 1]
    neg_idx = [i for i, l in enumerate(labels) if l == 0]
    if not pos_idx or not neg_idx:
        raise ValueError("bootstrap needs both classes present")
    stats: list[float] = []
    for _ in range(n_boot):
        sample = [rng.choice(pos_idx) for _ in pos_idx] + [rng.choice(neg_idx) for _ in neg_idx]
        stats.append(statistic([labels[i] for i in sample], [scores[i] for i in sample]))
    stats.sort()
    lo = stats[int((alpha / 2) * n_boot)]
    hi = stats[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return lo, hi


@dataclass
class BenchmarkResult:
    """Everything we report for one detector on one corpus."""

    detector: str
    n_human: int
    n_ai: int
    auroc: float
    auroc_ci: tuple[float, float]
    tpr_at_fpr_5: float
    tpr_at_fpr_1: float
    metrics_at_half: dict = field(default_factory=dict)
    brier: float = 0.0
    ece: float = 0.0

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "n_human": self.n_human,
            "n_ai": self.n_ai,
            "auroc": round(self.auroc, 4),
            "auroc_ci95": [round(self.auroc_ci[0], 4), round(self.auroc_ci[1], 4)],
            "tpr_at_5pct_fpr": round(self.tpr_at_fpr_5, 4),
            "tpr_at_1pct_fpr": round(self.tpr_at_fpr_1, 4),
            "at_threshold_0.5": {k: round(v, 4) if isinstance(v, float) else v
                                 for k, v in self.metrics_at_half.items()},
            "brier": round(self.brier, 4),
            "ece": round(self.ece, 4),
        }


def evaluate_scores(detector_name: str, labels: list[int], scores: list[float]) -> BenchmarkResult:
    """Compute the full metric suite for one detector's scores."""
    _validate(labels, scores)
    return BenchmarkResult(
        detector=detector_name,
        n_human=labels.count(0),
        n_ai=labels.count(1),
        auroc=auroc(labels, scores),
        auroc_ci=bootstrap_ci(labels, scores, auroc),
        tpr_at_fpr_5=tpr_at_fpr(labels, scores, 0.05)[0],
        tpr_at_fpr_1=tpr_at_fpr(labels, scores, 0.01)[0],
        metrics_at_half=classification_metrics(labels, scores, 0.5),
        brier=brier_score(labels, scores),
        ece=expected_calibration_error(labels, scores),
    )
