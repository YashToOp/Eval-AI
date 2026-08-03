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

import math
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
        # Clamp both ends: a score of exactly 1.0 would index past the last
        # bin, and a negative score would wrap around to it via Python's
        # negative indexing, quietly averaging a confident-human item in with
        # the confident-AI ones.
        idx = min(max(int(s * n_bins), 0), n_bins - 1)
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
    # Symmetric percentile indices. Using int((1 - alpha/2) * n_boot) for the
    # upper bound leaves one fewer order statistic above the interval than
    # below it, tilting every reported CI upward.
    lo_idx = int(math.floor((alpha / 2) * n_boot))
    hi_idx = int(math.ceil((1 - alpha / 2) * n_boot)) - 1
    lo_idx = min(max(lo_idx, 0), n_boot - 1)
    hi_idx = min(max(hi_idx, 0), n_boot - 1)
    return stats[lo_idx], stats[hi_idx]


def threshold_at_fpr(labels: list[int], scores: list[float], target_fpr: float) -> float:
    """Lowest threshold whose empirical FPR stays within `target_fpr`.

    This is the *empirical* counterpart to the conformal threshold. Use
    conformal.calibrate for anything with consequences: this one has no
    finite-sample guarantee and is optimistic on the data it was chosen on.
    """
    _validate(labels, scores)
    human = sorted((s for s, l in zip(scores, labels) if l == 0), reverse=True)
    if not human:
        raise ValueError("need human texts to set an FPR threshold")
    allowed = int(len(human) * target_fpr)
    if allowed >= len(human):
        return float("-inf")
    # Anything strictly above the allowed-th highest human score is safe.
    return math.nextafter(human[allowed], math.inf)


def domain_adjusted_tpr(
    labels: list[int],
    scores: list[float],
    domains: list[str],
    target_fpr: float = 0.05,
) -> tuple[float, dict[str, float]]:
    """RAID's headline metric: TPR at a fixed FPR, macro-averaged over domains.

    Averaging over domains rather than over documents stops a corpus that is
    80% news from reporting a detector as strong when it only works on news.
    The threshold is global (set once on all human text) so the domains are
    compared at a single operating point.
    """
    _validate(labels, scores)
    if len(domains) != len(labels):
        raise ValueError("domains must align with labels")
    thr = threshold_at_fpr(labels, scores, target_fpr)

    per_domain: dict[str, list[bool]] = {}
    for label, score, domain in zip(labels, scores, domains):
        if label == 1:
            per_domain.setdefault(domain, []).append(score >= thr)
    if not per_domain:
        raise ValueError("no AI texts to compute TPR over")
    tprs = {d: sum(hits) / len(hits) for d, hits in per_domain.items()}
    return sum(tprs.values()) / len(tprs), tprs


@dataclass
class SubgroupFPR:
    """False-positive rate for one subgroup of human writers."""

    group: str
    n: int
    n_flagged: int

    @property
    def fpr(self) -> float:
        return self.n_flagged / self.n if self.n else 0.0

    def wilson_interval(self, z: float = 1.96) -> tuple[float, float]:
        """Wilson score interval — correct at the small n and near-zero rates
        that subgroup analysis always involves, where a normal approximation
        would produce negative lower bounds."""
        n = self.n
        if n == 0:
            return (0.0, 1.0)
        p = self.fpr
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return (max(0.0, center - margin), min(1.0, center + margin))

    def to_dict(self) -> dict:
        lo, hi = self.wilson_interval()
        return {
            "group": self.group,
            "n": self.n,
            "n_flagged": self.n_flagged,
            "fpr": round(self.fpr, 4),
            "fpr_ci95": [round(lo, 4), round(hi, 4)],
        }


def subgroup_fpr(
    labels: list[int],
    scores: list[float],
    groups: list[str],
    threshold: float,
) -> list[SubgroupFPR]:
    """Per-subgroup false-positive rates among human-written texts.

    The documented harms of this technology are all false positives
    concentrated in identifiable groups — non-native English writers,
    neurodivergent writers, AAVE speakers, formulaic genres. An aggregate FPR
    that meets a policy cap while one subgroup sits far above it is a failing
    detector, and only this breakdown shows it.
    """
    _validate(labels, scores)
    if len(groups) != len(labels):
        raise ValueError("groups must align with labels")
    buckets: dict[str, list[float]] = {}
    for label, score, group in zip(labels, scores, groups):
        if label == 0:
            buckets.setdefault(group, []).append(score)
    return [
        SubgroupFPR(group=g, n=len(ss), n_flagged=sum(1 for s in ss if s >= threshold))
        for g, ss in sorted(buckets.items())
    ]


def max_subgroup_gap(results: list[SubgroupFPR]) -> float:
    """Largest FPR difference between any two subgroups."""
    if len(results) < 2:
        return 0.0
    rates = [r.fpr for r in results]
    return max(rates) - min(rates)


def fpr_resolution(labels: list[int]) -> float:
    """Smallest non-zero FPR the corpus can express: 1 / n_negatives.

    An FPR budget below this rounds down to "zero false positives allowed",
    so two different budgets can silently return the identical number. With
    18 human texts the resolution is 0.056, which means a reported
    "TPR @ 1% FPR" is not a measurement of 1% FPR at all.
    """
    n_neg = len(labels) - sum(labels)
    return 1.0 / n_neg if n_neg else float("inf")


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
    threshold: float = 0.5
    metrics_at_threshold: dict = field(default_factory=dict)
    brier: float = 0.0
    ece: float = 0.0
    tpr_at_fpr_5_ci: tuple[float, float] = (0.0, 0.0)
    fpr_resolution: float = 0.0

    @property
    def tpr_5_is_measurable(self) -> bool:
        return self.fpr_resolution <= 0.05

    @property
    def tpr_1_is_measurable(self) -> bool:
        return self.fpr_resolution <= 0.01

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "n_human": self.n_human,
            "n_ai": self.n_ai,
            "auroc": round(self.auroc, 4),
            "auroc_ci95": [round(self.auroc_ci[0], 4), round(self.auroc_ci[1], 4)],
            "tpr_at_5pct_fpr": round(self.tpr_at_fpr_5, 4),
            "tpr_at_5pct_fpr_ci95": [round(self.tpr_at_fpr_5_ci[0], 4),
                                     round(self.tpr_at_fpr_5_ci[1], 4)],
            "tpr_at_1pct_fpr": round(self.tpr_at_fpr_1, 4),
            "fpr_resolution": round(self.fpr_resolution, 4),
            "tpr_at_5pct_fpr_measurable": self.tpr_5_is_measurable,
            "tpr_at_1pct_fpr_measurable": self.tpr_1_is_measurable,
            f"at_threshold_{self.threshold}": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.metrics_at_threshold.items()
            },
            "brier": round(self.brier, 4),
            "ece": round(self.ece, 4),
        }


def evaluate_scores(
    detector_name: str,
    labels: list[int],
    scores: list[float],
    threshold: float = 0.5,
) -> BenchmarkResult:
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
        tpr_at_fpr_5_ci=bootstrap_ci(
            labels, scores, lambda l, s: tpr_at_fpr(l, s, 0.05)[0]
        ),
        fpr_resolution=fpr_resolution(labels),
        threshold=threshold,
        metrics_at_threshold=classification_metrics(labels, scores, threshold),
        brier=brier_score(labels, scores),
        ece=expected_calibration_error(labels, scores),
    )
