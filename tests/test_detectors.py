"""Detector behavior: bounds, abstention, and directional sanity."""

import pytest

from ai_text_eval.detectors import available_detectors
from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS
from ai_text_eval.detectors.ensemble import EnsembleDetector, verdict_for
from ai_text_eval.detectors.phrases import PhraseDetector
from ai_text_eval.detectors.stylometry import StylometryDetector
from ai_text_eval.text_features import (
    coefficient_of_variation,
    mattr,
    sentence_lengths,
    sentences,
    words,
)

MARKER_HEAVY = (
    "In today's fast-paced digital landscape, it is important to note that "
    "artificial intelligence plays a crucial role in shaping our ever-evolving "
    "world. By leveraging cutting-edge frameworks, organizations can delve into "
    "a rich tapestry of insights and harness the power of data. Furthermore, "
    "this transformative technology underscores a pivotal shift. Moreover, "
    "it is worth noting that meticulous planning fosters seamless adoption. "
    "In conclusion, the importance of this paradigm cannot be overstated."
)

PLAIN_HUMAN_ISH = (
    "The bus was late again. I waited maybe twenty minutes, which is not the "
    "worst I have done, but it was raining and my umbrella has a bent spoke so "
    "half of me got soaked anyway. Two other people gave up and walked. When it "
    "finally came the driver apologized, said the bridge was closed, and let a "
    "man with no change ride for free. That was decent of him. I got to work "
    "eleven minutes late and nobody said anything about it, which tells you "
    "something about that job."
)

ALL_DETECTORS = list(available_detectors(include_model_based=False).items())


@pytest.mark.parametrize("name,det", ALL_DETECTORS)
def test_scores_are_bounded(name, det):
    for text in (MARKER_HEAVY, PLAIN_HUMAN_ISH, "Short.", "a", "?!?!"):
        r = det.score(text)
        assert 0.0 <= r.score <= 1.0, f"{name} produced {r.score}"


@pytest.mark.parametrize("name,det", ALL_DETECTORS)
def test_empty_text_abstains(name, det):
    r = det.score("")
    assert not r.reliable
    assert r.score == pytest.approx(0.5)


@pytest.mark.parametrize("name,det", ALL_DETECTORS)
def test_short_text_is_not_reliable(name, det):
    short = "This sentence is far too short to judge."
    assert len(words(short)) < MIN_RELIABLE_WORDS
    assert not det.score(short).reliable


@pytest.mark.parametrize("name,det", ALL_DETECTORS)
def test_whitespace_only_abstains(name, det):
    r = det.score("   \n\t  ")
    assert not r.reliable


def test_phrase_detector_separates_marker_density():
    det = PhraseDetector()
    assert det.score(MARKER_HEAVY).score > det.score(PLAIN_HUMAN_ISH).score


def test_phrase_detector_is_monotone_in_markers():
    det = PhraseDetector()
    base = "The report covers the results of the study. " * 12
    loaded = base + (
        "It is important to note that this underscores a pivotal, multifaceted "
        "paradigm. Furthermore, we delve into a rich tapestry of insights."
    )
    assert det.score(loaded).score > det.score(base).score


def test_phrase_detector_reports_hits():
    det = PhraseDetector()
    details = det.score(MARKER_HEAVY).details
    assert details["lexicon_hits"]
    assert details["weighted_rate_per_kw"] > 0


def test_stylometry_prefers_bursty_text():
    det = StylometryDetector()
    uniform = " ".join(
        ["The committee reviewed the quarterly figures with considerable care."] * 8
    )
    bursty = (
        "It failed. The committee had reviewed the quarterly figures with the "
        "kind of care that only appears when someone senior is watching, and "
        "still nobody caught the missing column until March. Nobody. "
        "That is the part I keep coming back to, months later, whenever "
        "somebody tells me the process works. It does not. Ask March."
    )
    assert det.score(uniform).score > det.score(bursty).score


def test_ensemble_requires_detectors():
    with pytest.raises(ValueError):
        EnsembleDetector({})


def test_ensemble_averages_within_bounds():
    dets = available_detectors(include_model_based=False)
    ens = EnsembleDetector(dets)
    r, sub = ens.score_verbose(MARKER_HEAVY)
    assert 0.0 <= r.score <= 1.0
    assert set(sub) == set(dets)
    assert min(s.score for s in sub.values()) - 1e-9 <= r.score
    assert r.score <= max(s.score for s in sub.values()) + 1e-9


def test_ensemble_abstains_on_short_text():
    ens = EnsembleDetector(available_detectors(include_model_based=False))
    r = ens.score("AI wrote this, maybe.")
    assert not r.reliable
    assert "insufficient evidence" in verdict_for(r.score, r.reliable)


def test_ensemble_calibration_fit_changes_output():
    dets = available_detectors(include_model_based=False)
    ens = EnsembleDetector(dets)
    texts = [MARKER_HEAVY, PLAIN_HUMAN_ISH] * 4
    labels = [1, 0] * 4
    before = ens.score(MARKER_HEAVY).score
    ens.fit(texts, labels)
    after = ens.score(MARKER_HEAVY).score
    assert ens.calibration_summary is not None
    assert after != pytest.approx(before)
    assert ens.score(MARKER_HEAVY).score > ens.score(PLAIN_HUMAN_ISH).score


def test_ensemble_fit_rejects_single_class():
    ens = EnsembleDetector(available_detectors(include_model_based=False))
    with pytest.raises(ValueError):
        ens.fit([MARKER_HEAVY, PLAIN_HUMAN_ISH], [1, 1])


def test_ensemble_fit_rejects_misaligned_labels():
    ens = EnsembleDetector(available_detectors(include_model_based=False))
    with pytest.raises(ValueError):
        ens.fit([MARKER_HEAVY], [1, 0])


def test_verdict_bands_cover_range():
    for score in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert verdict_for(score, True)
    assert "AI" in verdict_for(0.95, True)
    assert "human" in verdict_for(0.05, True)


def test_sentence_splitting_handles_abbreviations():
    text = "Dr. Smith went to Washington. He met Mrs. Lee at 3 p.m. It rained."
    assert len(sentences(text)) == 3


def test_sentence_splitting_handles_quotes_and_paragraphs():
    text = 'She said "no." Then she left.\n\nThe next day was worse.'
    assert len(sentences(text)) == 3


def test_burstiness_is_zero_for_uniform_lengths():
    text = "one two three four. five six seven eight. nine ten eleven twelve."
    assert coefficient_of_variation(
        [float(x) for x in sentence_lengths(text)]
    ) == pytest.approx(0.0)


def test_mattr_bounds_and_repetition():
    assert mattr([]) == 0.0
    assert mattr(["a"] * 100) == pytest.approx(1 / 50)
    varied = [f"w{i}" for i in range(100)]
    assert mattr(varied) == pytest.approx(1.0)


def test_score_many_matches_score():
    det = PhraseDetector()
    texts = [MARKER_HEAVY, PLAIN_HUMAN_ISH]
    batch = det.score_many(texts)
    assert [b.score for b in batch] == [det.score(t).score for t in texts]
