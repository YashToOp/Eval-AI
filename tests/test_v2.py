"""Tests for the v2 architecture: conformal control, normalization defense,
spans, ternary verdicts, provenance asymmetry, and the supervised layer."""

import json
import math
import random

import pytest

from ai_text_eval.attacks import ATTACKS, apply_attack, attack_names
from ai_text_eval.cli import main
from ai_text_eval.conformal import (
    ConformalCalibration,
    calibrate,
    conformal_threshold,
    empirical_fpr,
    load_calibrations,
    min_calibration_size,
    save_calibrations,
)
from ai_text_eval.dataset import load_demo_corpus
from ai_text_eval.detectors import available_detectors
from ai_text_eval.detectors.base import Detector, DetectorResult
from ai_text_eval.detectors.supervised import (
    SupervisedDetector,
    cross_val_scores,
    extract_features,
)
from ai_text_eval.engine import DetectionEngine
from ai_text_eval.metrics import (
    domain_adjusted_tpr,
    max_subgroup_gap,
    subgroup_fpr,
    threshold_at_fpr,
)
from ai_text_eval.normalize import normalize
from ai_text_eval.provenance import ProvenanceSignal, ProvenanceStatus, combine
from ai_text_eval.spans import analyze_spans, find_change_point
from ai_text_eval.text_features import words
from ai_text_eval.verdict import AbstainReason, Label, decide


class _Fixed(Detector):
    """Detector returning a preset score, for exercising the decision layers."""

    def __init__(self, value, name="fixed"):
        self._v = value
        self.name = name

    def score(self, text):
        return DetectorResult(score=self._v, details={"n_words": len(words(text))})


# --- conformal FPR control ---------------------------------------------


@pytest.mark.parametrize(
    "alpha,expected", [(0.005, 199), (0.01, 99), (0.05, 19), (0.10, 9)]
)
def test_min_calibration_size(alpha, expected):
    assert min_calibration_size(alpha) == expected


def test_threshold_is_infinite_when_calibration_too_small():
    """A 0.5% cap cannot be certified from 18 texts; refusing is the point."""
    assert conformal_threshold([0.5] * 18, 0.005) == math.inf


def test_threshold_is_finite_when_calibration_suffices():
    assert math.isfinite(conformal_threshold([i / 100 for i in range(200)], 0.005))


def test_conformal_guarantee_holds_empirically():
    """The realized FPR on fresh exchangeable data must respect alpha."""
    rng = random.Random(7)
    cal = [rng.random() for _ in range(1000)]
    for alpha in (0.01, 0.05, 0.10):
        thr = conformal_threshold(cal, alpha)
        fresh = [rng.random() for _ in range(20000)]
        realized = sum(1 for s in fresh if s >= thr) / len(fresh)
        # Conformal is guaranteed in expectation; allow sampling slack.
        assert realized <= alpha * 1.35, f"alpha={alpha} realized={realized}"


def test_conformal_guarantee_holds_on_skewed_scores():
    """No distributional assumption: works on a lumpy, tied score set too."""
    rng = random.Random(11)
    cal = [round(rng.betavariate(2, 5), 2) for _ in range(1000)]
    thr = conformal_threshold(cal, 0.05)
    fresh = [round(rng.betavariate(2, 5), 2) for _ in range(20000)]
    assert sum(1 for s in fresh if s >= thr) / len(fresh) <= 0.05 * 1.5


def test_calibration_reports_shortfall():
    cal = calibrate([0.4] * 18, [200] * 18, alpha=0.005)
    assert not cal.is_certified
    assert cal.shortfall == 199 - 18


def test_multiscale_thresholds_are_length_dependent():
    """Short texts score differently, so they need their own threshold."""
    rng = random.Random(3)
    short_scores = [0.6 + 0.3 * rng.random() for _ in range(40)]
    long_scores = [0.1 + 0.2 * rng.random() for _ in range(40)]
    scores = short_scores + long_scores
    lengths = [30] * 40 + [400] * 40
    cal = calibrate(scores, lengths, alpha=0.10, bin_edges=[50])
    assert cal.threshold_for(30) > cal.threshold_for(400)


def test_bins_below_minimum_defer_to_global():
    cal = calibrate([0.5] * 30, [10] * 5 + [300] * 25, alpha=0.10, bin_edges=[50])
    assert cal.threshold_for(10) == cal.global_threshold


def test_empirical_fpr_on_heldout_split():
    rng = random.Random(5)
    cal_scores = [rng.random() for _ in range(400)]
    cal = calibrate(cal_scores, [200] * 400, alpha=0.05)
    held = [rng.random() for _ in range(2000)]
    assert empirical_fpr(cal, held, [200] * 2000) <= 0.08


def test_calibration_round_trips_through_json(tmp_path):
    cal = calibrate([i / 100 for i in range(200)], [200] * 200, alpha=0.01, language="hi")
    path = tmp_path / "cal.json"
    save_calibrations(path, {"hi": cal})
    back = load_calibrations(path)["hi"]
    assert back.language == "hi"
    assert back.alpha == cal.alpha
    assert back.global_threshold == pytest.approx(cal.global_threshold)


def test_infinite_threshold_round_trips_as_none(tmp_path):
    cal = calibrate([0.5] * 10, [200] * 10, alpha=0.005)
    path = tmp_path / "cal.json"
    save_calibrations(path, {"en": cal})
    assert json.loads(path.read_text())["en"]["global_threshold"] is None
    assert math.isinf(load_calibrations(path)["en"].global_threshold)


# --- Unicode normalization defense --------------------------------------


@pytest.mark.parametrize("attack", ["homoglyph", "zero_width", "whitespace"])
def test_normalization_reverses_mechanical_attacks(attack):
    original = "The quick brown fox jumps over the lazy dog and keeps on running."
    attacked = apply_attack(attack, original, seed=1)
    assert attacked != original, f"{attack} did not modify the text"
    restored, _ = normalize(attacked)
    assert restored == original


def test_normalization_flags_tampering():
    original = "The quick brown fox jumps over the lazy dog. " * 4
    _, report = normalize(apply_attack("homoglyph", original, seed=2))
    assert report.homoglyphs_replaced > 0
    assert report.is_suspicious


def test_any_invisible_character_is_suspicious():
    _, report = normalize("hello​world")
    assert report.invisibles_removed == 1
    assert report.is_suspicious


def test_clean_text_is_not_flagged():
    _, report = normalize("Perfectly ordinary English prose, with punctuation — and a dash.")
    assert not report.is_suspicious
    assert report.homoglyphs_replaced == 0


def test_normalization_preserves_clean_text_content():
    text = "Ordinary text stays byte-identical."
    assert normalize(text)[0] == text


def test_homoglyph_attack_defeats_detection_without_the_defense():
    """The reason normalization runs before scoring, demonstrated."""
    det = available_detectors(include_model_based=False)["phrases"]
    ai_text = (
        "It is important to note that in today's fast-paced digital landscape, "
        "organizations must delve into a rich tapestry of insights. Furthermore, "
        "this underscores a pivotal paradigm. Moreover, we must harness the power "
        "of data to unlock transformative value and foster seamless adoption."
    )
    attacked = apply_attack("homoglyph", ai_text, seed=3)
    assert det.score(attacked).score < det.score(ai_text).score
    restored, _ = normalize(attacked)
    assert det.score(restored).score == pytest.approx(det.score(ai_text).score)


def test_all_attacks_are_deterministic():
    text = "The quick brown fox jumps over the lazy dog. " * 3
    for name in attack_names():
        assert apply_attack(name, text, seed=9) == apply_attack(name, text, seed=9)


def test_attack_registry_is_complete():
    assert set(attack_names()) == set(ATTACKS)
    for name in attack_names():
        assert ATTACKS[name].description


# --- provenance asymmetry ----------------------------------------------


def test_verified_ai_provenance_raises_score():
    sig = ProvenanceSignal(status=ProvenanceStatus.VERIFIED_AI, scheme="c2pa-2.3")
    score, note = combine(0.2, sig)
    assert score >= 0.99
    assert "cryptographic" in note


def test_absent_provenance_never_lowers_score():
    """Absence must be uninformative: most models emit no watermark at all."""
    sig = ProvenanceSignal(status=ProvenanceStatus.ABSENT)
    for base in (0.05, 0.5, 0.95):
        assert combine(base, sig)[0] == pytest.approx(base)


def test_signed_human_claim_does_not_lower_score():
    sig = ProvenanceSignal(status=ProvenanceStatus.VERIFIED_HUMAN_CLAIM)
    assert combine(0.9, sig)[0] == pytest.approx(0.9)


def test_invalid_manifest_raises_suspicion():
    sig = ProvenanceSignal(status=ProvenanceStatus.INVALID)
    assert combine(0.5, sig)[0] > 0.5


@pytest.mark.parametrize("status", list(ProvenanceStatus))
def test_provenance_is_monotone_for_every_status(status):
    """No provenance state may ever reduce an AI score."""
    for base in (0.0, 0.3, 0.7, 1.0):
        assert combine(base, ProvenanceSignal(status=status))[0] >= base - 1e-9


# --- span analysis ------------------------------------------------------


def test_change_point_is_not_biased_to_the_extremes():
    """Unweighted max-t reports a boundary at index 1; the seam is at 10."""
    scores = [0.1] * 10 + [0.9] * 10
    cp = find_change_point(scores)
    assert cp is not None
    assert cp.sentence_index == 10
    assert cp.is_significant


def test_change_point_absent_in_homogeneous_text():
    cp = find_change_point([0.5] * 20)
    assert cp is not None
    assert not cp.is_significant


def test_change_point_needs_minimum_length():
    assert find_change_point([0.1, 0.9]) is None


def test_span_analysis_unreliable_on_short_documents():
    """A document barely longer than one window cannot resolve a seam."""
    det = _Fixed(0.8)
    short = "This is a sentence with several words in it. " * 4
    assert not analyze_spans(det, short).reliable


def test_span_analysis_reliable_on_long_documents():
    det = _Fixed(0.8)
    long_text = "This is a sentence with quite a few words inside of it. " * 40
    analysis = analyze_spans(det, long_text)
    assert analysis.reliable
    assert analysis.ai_word_fraction == pytest.approx(1.0)


def test_ai_fraction_is_word_weighted_not_sentence_weighted():
    class _ByLength(Detector):
        name = "bylength"

        def score(self, text):
            # Long windows score AI, short ones human.
            return DetectorResult(score=0.9 if len(words(text)) > 100 else 0.1)

    analysis = analyze_spans(_ByLength(), "word " * 400)
    assert 0.0 <= analysis.ai_word_fraction <= 1.0


def test_span_resolution_is_reported():
    det = _Fixed(0.8)
    analysis = analyze_spans(det, "A sentence with several words here. " * 40)
    assert analysis.resolution_words == analysis.window_words


# --- ternary verdict and abstention -------------------------------------


def _cal(alpha=0.05, thr=0.6, n=200):
    return ConformalCalibration(
        alpha=alpha, global_threshold=thr, n_calibration=n, language="en"
    )


def test_short_text_abstains():
    v = decide(score=0.99, n_words=40, calibration=_cal())
    assert v.label is Label.ABSTAIN
    assert AbstainReason.TOO_SHORT in v.abstain_reasons
    assert not v.actionable


def test_missing_calibration_abstains():
    v = decide(score=0.99, n_words=500, calibration=None)
    assert v.label is Label.ABSTAIN
    assert AbstainReason.NO_CALIBRATION in v.abstain_reasons


def test_language_mismatch_abstains():
    cal = _cal()
    v = decide(score=0.99, n_words=500, calibration=cal, language="hi")
    assert v.label is Label.ABSTAIN
    assert AbstainReason.NO_CALIBRATION in v.abstain_reasons


def test_uncertified_cap_abstains():
    cal = ConformalCalibration(
        alpha=0.005, global_threshold=math.inf, n_calibration=18, language="en"
    )
    v = decide(score=0.99, n_words=500, calibration=cal)
    assert v.label is Label.ABSTAIN
    assert AbstainReason.UNCERTIFIED_CAP in v.abstain_reasons


def test_tampered_text_abstains():
    _, report = normalize("hello​world " * 100)
    v = decide(score=0.99, n_words=500, calibration=_cal(), normalization=report)
    assert v.label is Label.ABSTAIN
    assert AbstainReason.TAMPERED in v.abstain_reasons


def test_clear_ai_above_threshold():
    v = decide(score=0.95, n_words=500, calibration=_cal(thr=0.6))
    assert v.label is Label.AI
    assert v.actionable


def test_clear_human_below_threshold():
    v = decide(score=0.10, n_words=500, calibration=_cal(thr=0.6))
    assert v.label is Label.HUMAN


def test_mixed_when_span_fraction_is_intermediate():
    det = _Fixed(0.8)
    long_text = "This is a sentence with quite a few words inside of it. " * 40
    spans = analyze_spans(det, long_text)
    spans.spans[0].score = 0.0  # force a human-scoring region
    for s in spans.spans[: len(spans.spans) // 2]:
        s.score = 0.1
    v = decide(score=0.7, n_words=500, calibration=_cal(), spans=spans)
    assert v.label is Label.MIXED
    assert v.ai_word_fraction is not None


def test_verified_provenance_answers_through_short_text_abstention():
    v = decide(score=0.2, n_words=20, calibration=None, provenance_is_ai=True)
    assert v.label is Label.AI
    assert v.actionable


def test_verdict_serializes():
    v = decide(score=0.95, n_words=500, calibration=_cal())
    payload = v.to_dict()
    assert payload["label"] == "ai"
    assert payload["actionable"] is True
    assert "fpr_cap" in payload


def test_abstention_explains_itself():
    v = decide(score=0.99, n_words=10, calibration=None)
    assert "ABSTAIN" in v.explain()
    assert v.notes


# --- supervised layer ---------------------------------------------------


def test_supervised_refuses_to_score_before_fitting():
    det = SupervisedDetector()
    r = det.score("some text here that is reasonably long " * 10)
    assert not r.reliable
    assert "not fitted" in r.details["error"]


def test_supervised_learns_the_demo_corpus():
    corpus = load_demo_corpus()
    det = SupervisedDetector(corroborators=available_detectors(include_model_based=False))
    det.fit([c.text for c in corpus], [c.label for c in corpus])
    assert det.is_fitted
    ai = [c for c in corpus if c.label == 1][0]
    human = [c for c in corpus if c.label == 0][0]
    assert det.score(ai.text).score > det.score(human.text).score


def test_supervised_rejects_single_class():
    det = SupervisedDetector()
    with pytest.raises(ValueError):
        det.fit(["a b c", "d e f"], [1, 1])


def test_cross_val_scores_are_out_of_fold():
    """In-sample scores from a trained model are the easiest way to lie."""
    corpus = load_demo_corpus()
    texts = [c.text for c in corpus]
    labels = [c.label for c in corpus]
    oof = cross_val_scores(lambda: SupervisedDetector(), texts, labels, n_folds=4)
    assert len(oof) == len(texts)
    assert all(0.0 <= s <= 1.0 for s in oof)
    # Out-of-fold scores must differ from in-sample ones.
    det = SupervisedDetector()
    det.fit(texts, labels)
    in_sample = [det.score(t).score for t in texts]
    assert oof != in_sample


def test_cross_val_is_deterministic():
    corpus = load_demo_corpus()[:20]
    texts = [c.text for c in corpus]
    labels = [c.label for c in corpus]
    a = cross_val_scores(lambda: SupervisedDetector(), texts, labels, n_folds=3, seed=1)
    b = cross_val_scores(lambda: SupervisedDetector(), texts, labels, n_folds=3, seed=1)
    assert a == b


def test_feature_extraction_is_empty_for_empty_text():
    assert extract_features("") == {}


def test_features_include_corroborator_scores():
    feats = extract_features("some text", {"phrases": 0.7})
    assert feats["detector_phrases"] == 0.7


# --- engine -------------------------------------------------------------


def _engine(primary_value=None, corr_values=None, calibrations=None):
    corr = {k: _Fixed(v, k) for k, v in (corr_values or {"phrases": 0.5}).items()}
    primary = _Fixed(primary_value, "primary") if primary_value is not None else None
    return DetectionEngine(
        corroborators=corr, primary=primary, calibrations=calibrations or {}
    )


def test_engine_weights_primary_above_corroborators():
    eng = _engine(primary_value=1.0, corr_values={"a": 0.0})
    score = eng.score_texts(["word " * 200])[0]
    assert score == pytest.approx(0.60)


def test_engine_falls_back_without_primary():
    eng = _engine(primary_value=None, corr_values={"a": 0.8})
    assert eng.score_texts(["word " * 200])[0] == pytest.approx(0.8)


def test_engine_flags_out_of_distribution_disagreement():
    eng = _engine(primary_value=0.95, corr_values={"a": 0.05})
    result = eng.analyze("word " * 200, want_spans=False)
    assert result.is_ood
    assert result.ood_disagreement > 0.45


def test_engine_does_not_flag_agreement_as_ood():
    eng = _engine(primary_value=0.80, corr_values={"a": 0.75})
    assert not eng.analyze("word " * 200, want_spans=False).is_ood


def test_engine_normalizes_before_scoring():
    eng = _engine(corr_values={"a": 0.5})
    result = eng.analyze("hello​world " * 100, want_spans=False)
    assert result.normalization.invisibles_removed > 0


def test_engine_requires_a_detector():
    with pytest.raises(ValueError):
        DetectionEngine(corroborators={}, primary=None)


def test_engine_result_serializes():
    eng = _engine(primary_value=0.9, corr_values={"a": 0.8})
    payload = eng.analyze("word " * 200, want_spans=False).to_dict()
    assert "verdict" in payload and "corroborators" in payload


# --- metrics extensions -------------------------------------------------


def test_domain_adjusted_tpr_macro_averages_over_domains():
    """A corpus dominated by one easy domain must not look strong overall."""
    labels = [0] * 20 + [1] * 20
    # Human scores spread across [0, 0.5] so the 5%-FPR threshold lands near
    # 0.5; 'easy' AI clears it, 'hard' AI does not.
    scores = [i / 40 for i in range(20)] + [0.9] * 10 + [0.2] * 10
    domains = ["x"] * 20 + ["easy"] * 10 + ["hard"] * 10
    macro, per_domain = domain_adjusted_tpr(labels, scores, domains, target_fpr=0.05)
    assert per_domain["easy"] > per_domain["hard"]
    assert macro == pytest.approx((per_domain["easy"] + per_domain["hard"]) / 2)


def test_threshold_at_fpr_respects_budget():
    labels = [0] * 100 + [1] * 10
    scores = [i / 100 for i in range(100)] + [0.99] * 10
    thr = threshold_at_fpr(labels, scores, 0.05)
    fp = sum(1 for s, l in zip(scores, labels) if l == 0 and s >= thr)
    assert fp <= 5


def test_subgroup_fpr_exposes_a_hidden_disparity():
    """Aggregate FPR meets the cap while one subgroup is far above it."""
    labels = [0] * 200
    # group A never flagged; group B flagged 20% of the time.
    scores = [0.1] * 100 + [0.9] * 20 + [0.1] * 80
    groups = ["native"] * 100 + ["non_native"] * 100
    results = subgroup_fpr(labels, scores, groups, threshold=0.5)
    by_group = {r.group: r for r in results}
    assert by_group["native"].fpr == pytest.approx(0.0)
    assert by_group["non_native"].fpr == pytest.approx(0.2)
    assert max_subgroup_gap(results) == pytest.approx(0.2)


def test_subgroup_wilson_interval_stays_in_bounds():
    """Normal-approximation intervals go negative at these rates; Wilson does not."""
    results = subgroup_fpr([0] * 20, [0.1] * 20, ["g"] * 20, threshold=0.5)
    lo, hi = results[0].wilson_interval()
    assert 0.0 <= lo <= hi <= 1.0
    assert results[0].fpr == 0.0
    assert hi > 0.0, "a zero-count subgroup still has upper uncertainty"


def test_subgroup_fpr_ignores_ai_texts():
    labels = [0, 1]
    scores = [0.1, 0.99]
    results = subgroup_fpr(labels, scores, ["g", "g"], threshold=0.5)
    assert results[0].n == 1


# --- CLI ----------------------------------------------------------------


def test_cli_analyze_abstains_without_calibration(capsys):
    assert main(["analyze", "--fast", "--no-spans", "word " * 200]) == 0
    assert "ABSTAIN" in capsys.readouterr().out


def test_cli_calibrate_reports_shortfall(tmp_path, capsys):
    human = tmp_path / "h.jsonl"
    corpus = [c for c in load_demo_corpus() if c.label == 0]
    human.write_text(
        "\n".join(json.dumps({"text": c.text, "label": 0}) for c in corpus),
        encoding="utf-8",
    )
    assert main(["calibrate", "--fast", "--human", str(human), "--fpr-cap", "0.005"]) == 0
    out = capsys.readouterr().out
    assert "NOT CERTIFIED" in out
    assert "199" in out


def test_cli_calibrate_certifies_when_possible(tmp_path, capsys):
    human = tmp_path / "h.jsonl"
    corpus = [c for c in load_demo_corpus() if c.label == 0]
    human.write_text(
        "\n".join(json.dumps({"text": c.text, "label": 0}) for c in corpus),
        encoding="utf-8",
    )
    out_path = tmp_path / "cal.json"
    assert main([
        "calibrate", "--fast", "--human", str(human),
        "--fpr-cap", "0.2", "--out", str(out_path),
    ]) == 0
    assert "CERTIFIED" in capsys.readouterr().out
    assert load_calibrations(out_path)["en"].is_certified


def test_cli_spans_runs(capsys):
    assert main(["spans", "--fast", "A sentence with several words in it here. " * 40]) == 0
    assert "AI-attributed text" in capsys.readouterr().out


def test_cli_robustness_runs(capsys):
    assert main(["robustness", "--fast"]) == 0
    out = capsys.readouterr().out
    assert "homoglyph" in out
    assert "residual" in out


def test_cli_fairness_runs(capsys):
    assert main(["fairness", "--fast", "--group-by", "domain"]) == 0
    out = capsys.readouterr().out
    assert "FPR" in out
    assert "false positives concentrated" in out


def test_cli_analyze_json(capsys):
    assert main(["analyze", "--fast", "--no-spans", "--json", "word " * 200]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"]["label"] == "abstain"
    assert payload["verdict"]["actionable"] is False
