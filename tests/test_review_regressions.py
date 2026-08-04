"""Regression tests for defects found by adversarial review.

Each test names the failure it locks out. These are the cases where the
framework previously produced a confident number that was wrong — the worst
failure mode for an eval, because nothing looks broken.
"""

import json

import pytest

from ai_text_eval.cli import main
from ai_text_eval.dataset import load_labeled_jsonl, load_pairs_jsonl
from ai_text_eval.detectors import detector_names
from ai_text_eval.detectors.base import DetectorResult
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.detectors.phrases import PhraseDetector
from ai_text_eval.detectors.stylometry import StylometryDetector
from ai_text_eval.evasion import EvasionPair, EvasionReport, PairOutcome, evaluate_evasion
from ai_text_eval.metrics import (
    auroc,
    bootstrap_ci,
    evaluate_scores,
    expected_calibration_error,
    fpr_resolution,
)
from ai_text_eval.report import render_benchmark, render_evasion, render_score
from ai_text_eval.text_features import mattr, sentences, words

FILLER = "Plain filler sentence here. " * 14


# --- sentence splitting -------------------------------------------------


def test_ordinary_word_no_is_not_an_abbreviation():
    """"The answer was no." ended a sentence; the splitter used to swallow it."""
    assert len(sentences("The answer was no. Then we went home. It rained.")) == 3


@pytest.mark.parametrize("word", ["no", "co", "est", "vs", "inc", "st"])
def test_sentence_final_ordinary_words_split(word):
    text = f"The label said {word}. Then we checked again. It was fine."
    assert len(sentences(text)) == 3


def test_single_letter_ending_does_not_swallow_next_sentence():
    assert len(sentences("We tried plan A. It failed. Then plan B. It failed too.")) == 4


def test_initial_chains_still_join():
    assert len(sentences("J. R. R. Tolkien wrote The Hobbit. It sold well.")) == 2


def test_titles_still_join():
    assert len(sentences("Dr. Smith went to town. He met Mrs. Lee. It rained.")) == 3


def test_mid_sentence_ellipsis_is_not_a_boundary():
    assert len(sentences("We finished this work ... at least in the long run.")) == 1


def test_bulleted_list_is_not_one_giant_sentence():
    """LLM output is bullet-heavy; collapsing it destroyed the length stats."""
    text = "- **Speed**: fast\n- **Cost**: low\n- **Size**: small"
    assert len(sentences(text)) == 3


def test_numbered_list_marker_is_not_a_sentence():
    assert len(sentences("1. First item\n2. Second item\n3. Third item")) == 3


def test_hard_wrapped_prose_is_not_over_split():
    """Plain-text corpora wrap mid-sentence; a newline alone is not a boundary."""
    text = "Hard wrapped prose runs\nacross several lines\nwithout ending. Then it ends."
    assert len(sentences(text)) == 2


def test_closing_quote_stays_with_its_sentence():
    assert sentences('She said "no." Then she left.')[0].endswith('"')


# --- tokenization -------------------------------------------------------


def test_typographic_and_ascii_apostrophes_tokenize_identically():
    """Otherwise typography masquerades as an authorship signal."""
    assert words("don’t stop") == words("don't stop") == ["don't", "stop"]


def test_bare_quote_is_not_a_word_token():
    assert words("She wrote ' the quiet part ' out loud") == [
        "she", "wrote", "the", "quiet", "part", "out", "loud"
    ]


def test_mattr_docstring_matches_behavior_below_window():
    """Below the window MATTR is plain TTR; the docstring must not overclaim."""
    assert mattr(["a", "b", "c"]) == pytest.approx(1.0)
    assert "TTR" in mattr.__doc__


# --- phrase detector ----------------------------------------------------


def test_multiword_phrases_match_on_word_boundaries():
    """'here are some' must not fire inside 'Where are some of the...'."""
    det = PhraseDetector()
    r = det.score("Where are some of the best places to watch birds? " + FILLER)
    assert "here are some" not in r.details["lexicon_hits"]


def test_no_marker_text_scores_near_the_floor():
    det = PhraseDetector()
    r = det.score("Where are some of the best places to watch birds? " + FILLER)
    assert r.score < 0.35, "marker-free human prose must stay near the floor"


def test_nested_phrases_are_charged_once():
    """'in the realm of' must not also bill for the 'realm' inside it."""
    det = PhraseDetector()
    hits = det.score(
        "In the realm of scheduling, a rich tapestry of constraints applies. " + FILLER
    ).details["lexicon_hits"]
    assert "realm" not in hits
    assert "tapestry" not in hits
    assert hits["in the realm of"] == 1
    assert hits["a rich tapestry"] == 1


def test_triad_contribution_has_no_cliff():
    """One extra triad used to swing the score across most of its range.

    Sentence count is held fixed so the only thing varying is the quantity
    being measured.
    """
    det = PhraseDetector()
    triad = "Alpha, beta, and gamma matter."
    plain = "Alpha and beta really matter."
    scores = [
        det.score(" ".join([triad] * k + [plain] * (12 - k))).score for k in range(13)
    ]
    jumps = [abs(b - a) for a, b in zip(scores, scores[1:])]
    assert max(jumps) < 0.25, f"discontinuous triad contribution: {scores}"
    assert scores == sorted(scores), "triad contribution must be monotone"


def test_triads_still_discriminate():
    det = PhraseDetector()
    dense = "Alpha, beta, and gamma matter. Delta, epsilon, and zeta matter. " * 6
    sparse = "Alpha and beta matter. Delta and epsilon matter. " * 6
    assert det.score(dense).score > det.score(sparse).score


@pytest.mark.parametrize("n_words,n_sents", [(30, 2), (45, 3), (90, 5), (200, 10)])
def test_content_free_text_scores_at_the_floor(n_words, n_sents):
    """Softplus never reaches zero, so charging it directly billed every text
    for triads it did not contain — and the per-1000-words normalization
    turned that phantom into a large rate on short input. Nonsense with no
    markers and no triads must sit exactly at the floor, at every length."""
    det = PhraseDetector()
    sentence = " ".join(["zzz"] * (n_words // n_sents)) + "."
    text = " ".join([sentence] * n_sents)
    r = det.score(text)
    assert r.details["lexicon_hits"] == {}
    assert r.details["triads"] == 0
    assert r.details["weighted_rate_per_kw"] == pytest.approx(0.0)
    assert r.score == pytest.approx(0.30)


def test_marker_free_score_is_length_independent():
    """The phantom rate made the score a function of word and sentence count
    alone: two unrelated 31-word/2-sentence passages scored byte-identically."""
    det = PhraseDetector()
    short = "The optimization reduces unnecessary allocations while maintaining behavior. " \
            "Preliminary testing indicates measurable improvements across workloads."
    long = short * 4
    assert det.score(short).score == pytest.approx(det.score(long).score)


# --- metrics ------------------------------------------------------------


def test_bootstrap_interval_is_symmetric_in_order_statistics():
    """int((1-a/2)*n) left one fewer sample above the interval than below."""
    labels = [0] * 15 + [1] * 15
    scores = [0.30 + 0.02 * i for i in range(15)] + [0.34 + 0.02 * i for i in range(15)]
    n_boot = 1000
    lo, hi = bootstrap_ci(labels, scores, auroc, n_boot=n_boot, seed=1234)

    stats = []
    import random as _random
    rng = _random.Random(1234)
    pos = [i for i, l in enumerate(labels) if l == 1]
    neg = [i for i, l in enumerate(labels) if l == 0]
    for _ in range(n_boot):
        sample = [rng.choice(pos) for _ in pos] + [rng.choice(neg) for _ in neg]
        stats.append(auroc([labels[i] for i in sample], [scores[i] for i in sample]))
    stats.sort()
    n_below = sum(1 for s in stats if s < lo)
    n_above = sum(1 for s in stats if s > hi)
    assert abs(n_below - n_above) <= max(2, int(0.01 * n_boot))


def test_ece_negative_score_does_not_wrap_into_the_top_bin():
    """bins[int(-0.9 * 10)] is bins[-9]; Python wrapped it into a high bin,
    quietly pooling a confidently-human item with the confidently-AI ones."""
    labels = [1] + [0] * 9
    scores = [-0.9] + [0.95] * 9
    # Correct: -0.9 clamps into bin 0 alone (|conf - acc| = 1.9, weight 0.1),
    # the nine 0.95s occupy the top bin (|0.95 - 0| = 0.95, weight 0.9).
    expected = 0.1 * abs(-0.9 - 1.0) + 0.9 * abs(0.95 - 0.0)
    assert expected_calibration_error(labels, scores) == pytest.approx(expected)
    # If it had wrapped, everything would pool into one bin at ~0.665.
    assert expected_calibration_error(labels, scores) != pytest.approx(0.665, abs=1e-3)


def test_ece_score_of_one_lands_in_the_last_bin():
    assert expected_calibration_error([0], [1.0]) == pytest.approx(1.0)


def test_fpr_resolution_reports_corpus_granularity():
    assert fpr_resolution([0] * 18 + [1] * 18) == pytest.approx(1 / 18)


def test_unmeasurable_fpr_budget_is_flagged_not_silently_reported():
    """TPR@1%FPR is meaningless with 18 negatives; the table must say so."""
    labels = [0] * 18 + [1] * 18
    scores = [0.2 + 0.01 * i for i in range(18)] + [0.5 + 0.01 * i for i in range(18)]
    r = evaluate_scores("d", labels, scores)
    assert not r.tpr_1_is_measurable
    assert not r.tpr_5_is_measurable
    assert r.tpr_at_fpr_1 == r.tpr_at_fpr_5
    out = render_benchmark([r])
    assert "*" in out
    assert "zero false positives allowed" in out
    assert r.to_dict()["tpr_at_1pct_fpr_measurable"] is False


def test_measurable_budget_is_not_flagged():
    labels = [0] * 200 + [1] * 200
    scores = [0.001 * i for i in range(200)] + [0.5 + 0.002 * i for i in range(200)]
    r = evaluate_scores("d", labels, scores)
    assert r.tpr_5_is_measurable
    assert "*" not in render_benchmark([r])


def test_tpr_carries_a_confidence_interval():
    labels = [0] * 18 + [1] * 18
    scores = [0.2 + 0.01 * i for i in range(18)] + [0.5 + 0.01 * i for i in range(18)]
    r = evaluate_scores("d", labels, scores)
    lo, hi = r.tpr_at_fpr_5_ci
    assert lo <= r.tpr_at_fpr_5 <= hi


def test_evaluate_scores_honors_a_custom_threshold():
    labels = [0, 0, 1, 1]
    scores = [0.1, 0.6, 0.7, 0.95]
    strict = evaluate_scores("d", labels, scores, threshold=0.9)
    loose = evaluate_scores("d", labels, scores, threshold=0.5)
    assert strict.metrics_at_threshold["tp"] == 1
    assert loose.metrics_at_threshold["tp"] == 2
    assert "at_threshold_0.9" in strict.to_dict()


# --- evasion accounting -------------------------------------------------


def test_undefined_evasion_rate_is_not_reported_as_zero():
    """0/0 rendered as 0.0% ranked a detector that never fires as the best."""
    useless = EvasionReport(detector="useless", threshold=0.5)
    useless.outcomes = [PairOutcome(0.10, 0.10)] * 10
    assert useless.evasion_rate is None
    out = render_evasion([useless])
    assert "n/a" in out and "0.0%" not in out


def test_evadable_detector_still_reports_a_rate():
    evadable = EvasionReport(detector="evadable", threshold=0.5)
    evadable.outcomes = [PairOutcome(0.95, 0.30)] * 8 + [PairOutcome(0.95, 0.95)] * 2
    assert evadable.evasion_rate == pytest.approx(0.8)


def test_render_evasion_survives_a_zero_pair_report():
    assert "n/a" in render_evasion([EvasionReport(detector="empty", threshold=0.5)])


# --- ensemble -----------------------------------------------------------


class _Stub:
    def __init__(self, name, value):
        self.name = name
        self._v = value
        self.calls = 0

    def score(self, text):
        self.calls += 1
        return DetectorResult(score=self._v, details={"n_words": len(words(text))})

    def score_many(self, texts):
        return [self.score(t) for t in texts]


def test_explicit_empty_weights_is_not_replaced_by_defaults():
    stubs = {"stylometry": _Stub("stylometry", 0.9), "phrases": _Stub("phrases", 0.1)}
    ens = EnsembleDetector(stubs, weights={})
    assert ens.weights == {"stylometry": 1.0, "phrases": 1.0}


def test_score_and_score_verbose_agree_on_details():
    stubs = {"stylometry": _Stub("stylometry", 0.9), "phrases": _Stub("phrases", 0.1)}
    ens = EnsembleDetector(stubs)
    text = "word " * 60
    a = ens.score(text)
    b, _ = ens.score_verbose(text)
    assert a.details.keys() == b.details.keys()
    assert a.score == pytest.approx(b.score)


def test_combine_results_avoids_rescoring():
    stubs = {"stylometry": _Stub("stylometry", 0.9), "phrases": _Stub("phrases", 0.1)}
    ens = EnsembleDetector(stubs)
    text = "word " * 60
    sub = {n: d.score(text) for n, d in stubs.items()}
    before = {n: d.calls for n, d in stubs.items()}
    ens.combine_results(sub, len(words(text)))
    assert {n: d.calls for n, d in stubs.items()} == before


# --- dataset loading ----------------------------------------------------


def test_demo_data_lives_inside_the_package():
    """Repo-relative paths break the moment the package is pip-installed."""
    import ai_text_eval.dataset as ds

    assert ds.DATA_DIR.is_dir()
    assert ds.DATA_DIR.parent.name == "ai_text_eval"
    assert (ds.DATA_DIR / "demo_human.jsonl").exists()


def test_fractional_label_is_rejected_not_truncated(tmp_path):
    """int(0.7) == 0 silently recorded an AI-leaning example as human."""
    f = tmp_path / "soft.jsonl"
    f.write_text('{"text": "x", "label": 0.7}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="integer"):
        load_labeled_jsonl(f)


def test_boolean_label_is_rejected(tmp_path):
    f = tmp_path / "b.jsonl"
    f.write_text('{"text": "x", "label": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="integer"):
        load_labeled_jsonl(f)


def test_pairs_loader_wraps_json_errors_with_location(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text('{"original": "a", bad json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r":1:.*invalid JSON"):
        load_pairs_jsonl(f)


# --- CLI ----------------------------------------------------------------


def test_benchmark_threshold_reaches_the_metrics_table(capsys):
    main(["benchmark", "--fast", "--threshold", "0.5"])
    loose = capsys.readouterr().out
    main(["benchmark", "--fast", "--threshold", "0.95"])
    strict = capsys.readouterr().out
    assert loose != strict, "--threshold was ignored by the metrics table"
    assert "F1@0.95" in strict


def test_benchmark_json_carries_the_caveats(tmp_path, capsys):
    main(["benchmark", "--fast", "--out", str(tmp_path)])
    capsys.readouterr()
    payload = json.loads((tmp_path / "benchmark.json").read_text())
    assert payload["caveats"], "a bare JSON of AUROCs gets quoted without its confounds"
    assert any("confounded" in c for c in payload["caveats"])
    assert payload["threshold"] == 0.5


def test_benchmark_prints_caveats_next_to_the_numbers(capsys):
    main(["benchmark", "--fast"])
    out = capsys.readouterr().out
    assert "NOT detector performance" in out
    assert "in-sample" in out


def test_missing_file_is_an_error_message_not_a_traceback(capsys):
    assert main(["score", "--fast", "--file", "/nonexistent/nope.txt"]) == 2
    assert "error:" in capsys.readouterr().err


def test_malformed_corpus_is_an_error_message_not_a_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    ok = tmp_path / "ok.jsonl"
    ok.write_text('{"text": "x", "label": 1}\n', encoding="utf-8")
    assert main(["benchmark", "--fast", "--human", str(bad), "--ai", str(ok)]) == 2
    assert "error:" in capsys.readouterr().err


def test_single_class_corpus_is_rejected_clearly(tmp_path, capsys):
    h = tmp_path / "h.jsonl"
    h.write_text('{"text": "a", "label": 0}\n', encoding="utf-8")
    a = tmp_path / "a.jsonl"
    a.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="one class"):
        main(["benchmark", "--fast", "--human", str(h), "--ai", str(a)])


def test_empty_pairs_file_warns_instead_of_silently_skipping(tmp_path, capsys):
    h = tmp_path / "h.jsonl"
    h.write_text('{"text": "a b c", "label": 0}\n', encoding="utf-8")
    a = tmp_path / "a.jsonl"
    a.write_text('{"text": "d e f", "label": 1}\n', encoding="utf-8")
    p = tmp_path / "p.jsonl"
    p.write_text("", encoding="utf-8")
    main(["benchmark", "--fast", "--human", str(h), "--ai", str(a), "--pairs", str(p)])
    assert "no pairs" in capsys.readouterr().out


def test_detectors_listing_does_not_construct_models():
    """Listing names must not download ~1GB of weights."""
    assert detector_names(include_model_based=False) == ["stylometry", "phrases"]


def test_detectors_fast_does_not_claim_models_are_missing(capsys):
    main(["detectors", "--fast"])
    out = capsys.readouterr().out
    assert "unavailable" not in out
    assert "--fast" in out


# --- reporting ----------------------------------------------------------


def test_unreliable_reason_names_the_real_cause():
    """A 70-word bulleted doc is not "too short"; it has too few sentences."""
    det = StylometryDetector()
    text = "- " + "\n- ".join(["alpha beta gamma delta epsilon zeta eta theta"] * 2)
    r = det.score(text)
    ens_like = {"stylometry": r}
    out = render_score(DetectorResult(score=0.5, reliable=False, details=r.details), ens_like)
    assert "words" in out or "sentences" in out


def test_short_text_reason_mentions_words():
    det = StylometryDetector()
    r = det.score("Only a handful of words here.")
    out = render_score(DetectorResult(score=0.5, reliable=False, details=r.details), {"stylometry": r})
    assert "words" in out
