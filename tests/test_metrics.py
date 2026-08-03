"""Metric correctness — checked against values computable by hand."""

import math

import pytest

from ai_text_eval.metrics import (
    auroc,
    bootstrap_ci,
    brier_score,
    classification_metrics,
    expected_calibration_error,
    roc_points,
    tpr_at_fpr,
)


def test_auroc_perfect_separation():
    labels = [0, 0, 0, 1, 1, 1]
    scores = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    assert auroc(labels, scores) == pytest.approx(1.0)


def test_auroc_perfect_inversion():
    labels = [0, 0, 0, 1, 1, 1]
    scores = [0.9, 0.8, 0.7, 0.3, 0.2, 0.1]
    assert auroc(labels, scores) == pytest.approx(0.0)


def test_auroc_all_ties_is_half():
    labels = [0, 1, 0, 1]
    scores = [0.5, 0.5, 0.5, 0.5]
    assert auroc(labels, scores) == pytest.approx(0.5)


def test_auroc_known_value_with_ties():
    # 2 positives, 2 negatives. Pairs (pos, neg): (0.6,0.6) tie -> 0.5,
    # (0.6,0.1) win -> 1, (0.9,0.6) win -> 1, (0.9,0.1) win -> 1.
    # AUROC = (0.5 + 1 + 1 + 1) / 4 = 0.875
    labels = [1, 1, 0, 0]
    scores = [0.6, 0.9, 0.6, 0.1]
    assert auroc(labels, scores) == pytest.approx(0.875)


def test_auroc_requires_both_classes():
    with pytest.raises(ValueError):
        auroc([1, 1, 1], [0.1, 0.2, 0.3])


def test_auroc_rejects_length_mismatch():
    with pytest.raises(ValueError):
        auroc([1, 0], [0.5])


def test_roc_points_monotone_and_bounded():
    labels = [0, 1, 0, 1, 1, 0]
    scores = [0.2, 0.9, 0.4, 0.6, 0.55, 0.1]
    pts = roc_points(labels, scores)
    assert pts[0] == (0.0, 0.0, float("inf"))
    assert pts[-1][0] == pytest.approx(1.0)
    assert pts[-1][1] == pytest.approx(1.0)
    fprs = [p[0] for p in pts]
    tprs = [p[1] for p in pts]
    assert fprs == sorted(fprs)
    assert tprs == sorted(tprs)


def test_tpr_at_fpr_respects_budget():
    # 4 negatives -> FPR grid is 0, .25, .5, .75, 1
    labels = [1, 1, 1, 1, 0, 0, 0, 0]
    scores = [0.9, 0.8, 0.7, 0.3, 0.6, 0.4, 0.2, 0.1]
    tpr, thr = tpr_at_fpr(labels, scores, 0.0)
    # At zero false positives we can only accept scores above 0.6 -> 3 of 4.
    assert tpr == pytest.approx(0.75)
    assert thr == pytest.approx(0.7)


def test_tpr_at_fpr_zero_when_unreachable():
    # Every positive scores below every negative; no FPR-0 threshold catches any.
    labels = [1, 1, 0, 0]
    scores = [0.1, 0.2, 0.8, 0.9]
    tpr, _ = tpr_at_fpr(labels, scores, 0.0)
    assert tpr == pytest.approx(0.0)


def test_classification_metrics_hand_computed():
    labels = [1, 1, 0, 0]
    scores = [0.9, 0.4, 0.6, 0.1]
    m = classification_metrics(labels, scores, threshold=0.5)
    assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (1, 1, 1, 1)
    assert m["accuracy"] == pytest.approx(0.5)
    assert m["precision"] == pytest.approx(0.5)
    assert m["recall"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(0.5)
    assert m["fpr"] == pytest.approx(0.5)


def test_threshold_is_inclusive():
    labels = [1]
    scores = [0.5]
    assert classification_metrics(labels, scores, 0.5)["tp"] == 1


def test_brier_score_hand_computed():
    labels = [1, 0]
    scores = [0.75, 0.25]
    assert brier_score(labels, scores) == pytest.approx((0.0625 + 0.0625) / 2)


def test_ece_zero_for_perfectly_calibrated():
    # 10 items in the 0.9-1.0 bin, 9 of them positive, all scored 0.9.
    labels = [1] * 9 + [0]
    scores = [0.9] * 10
    assert expected_calibration_error(labels, scores) == pytest.approx(0.0, abs=1e-9)


def test_ece_detects_overconfidence():
    labels = [1, 0, 1, 0]
    scores = [0.99, 0.99, 0.99, 0.99]
    # confidence 0.99, accuracy 0.5 -> ECE ~ 0.49
    assert expected_calibration_error(labels, scores) == pytest.approx(0.49, abs=1e-6)


def test_ece_zero_when_confident_and_correct():
    # score 1.0 on a positive and 0.0 on a negative is perfect calibration.
    assert expected_calibration_error([1, 0], [1.0, 0.0]) == pytest.approx(0.0)


def test_ece_handles_score_of_one_without_index_error():
    # Guards the bin index off-by-one at the top edge: score 1.0 must land in
    # the last bin, and being confidently wrong there must cost the full gap.
    assert expected_calibration_error([0], [1.0]) == pytest.approx(1.0)
    assert expected_calibration_error([1], [0.0]) == pytest.approx(1.0)


def test_bootstrap_ci_brackets_point_estimate_and_is_deterministic():
    labels = [0] * 20 + [1] * 20
    scores = [0.2 + 0.01 * i for i in range(20)] + [0.6 + 0.01 * i for i in range(20)]
    point = auroc(labels, scores)
    lo, hi = bootstrap_ci(labels, scores, auroc, n_boot=300, seed=7)
    assert lo <= point <= hi
    assert (lo, hi) == bootstrap_ci(labels, scores, auroc, n_boot=300, seed=7)


def test_bootstrap_ci_wider_for_smaller_sample():
    # Overlapping class distributions — a perfectly separated corpus gives a
    # degenerate zero-width interval at every sample size.
    big_labels = [0] * 40 + [1] * 40
    big_scores = [0.30 + 0.010 * i for i in range(40)] + [0.35 + 0.010 * i for i in range(40)]
    small_labels = big_labels[::8]
    small_scores = big_scores[::8]
    assert small_labels.count(0) == 5 and small_labels.count(1) == 5
    big_lo, big_hi = bootstrap_ci(big_labels, big_scores, auroc, n_boot=300, seed=1)
    small_lo, small_hi = bootstrap_ci(small_labels, small_scores, auroc, n_boot=300, seed=1)
    assert (small_hi - small_lo) >= (big_hi - big_lo)


def test_metrics_reject_bad_labels():
    with pytest.raises(ValueError):
        auroc([0, 2], [0.1, 0.2])
    with pytest.raises(ValueError):
        brier_score([], [])


def test_auroc_matches_bruteforce_pair_counting():
    labels = [1, 0, 1, 0, 1, 1, 0, 0, 1, 0]
    scores = [0.5, 0.5, 0.9, 0.1, 0.3, 0.7, 0.7, 0.2, 0.4, 0.4]
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in pos
        for n in neg
    )
    assert auroc(labels, scores) == pytest.approx(wins / (len(pos) * len(neg)))


def test_auroc_is_finite_on_extreme_scores():
    labels = [1, 0]
    scores = [1.0, 0.0]
    assert math.isfinite(auroc(labels, scores))
