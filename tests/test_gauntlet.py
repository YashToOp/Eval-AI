"""Tests for the GAUNTLET benchmark infrastructure.

Each test names the specification section it enforces. The validators are the
component most likely to be silently wrong — a validator that passes bad data
is worse than none, because it converts an unchecked corpus into a checked-
looking one — so the emphasis here is on proving they *reject*.
"""

import json

import pytest

from ai_text_eval.gauntlet import (
    BenchmarkRunner,
    Corpus,
    Sample,
    SplitDisciplineError,
    bucket_for,
    count_words,
    load_corpus,
    quantize,
    validate_manifest,
    validate_release,
    validate_sample,
    validate_splits,
    wilson_interval,
)
from ai_text_eval.gauntlet.loader import Manifest
from ai_text_eval.gauntlet.spec import (
    CELL_TARGETS,
    FIELD_ORDER,
    LENGTH_BUCKETS,
    POOLED_HUMAN_TEST_MINIMUM,
    load_categories,
    load_failure_modes,
)


def make_sample(**overrides) -> Sample:
    """A schema-complete HUMAN record; override fields to break it."""
    text = " ".join(["word"] * 100)  # 100 words -> B100
    rec = {
        "id": "H-01-B100-0001", "schema_version": "1", "corpus_version": "1.0.0",
        "split": "test", "text": text, "category": "H-01", "track": "H",
        "domain": "casual", "format": "prose", "language": "en-native",
        "length_words": 100, "length_bucket": "B100", "label": "HUMAN",
        "ai_token_share": 0.0, "span_map": None, "source_type": "archive_2017",
        "provenance_tier": "T0", "provenance_ref": "", "generator": None,
        "transforms": [], "topic_group_id": None, "difficulty": "D3",
        "rationale": "Pre-LLM archival forum post.",
        "target_weakness": "informal register baseline",
        "expected_confusions": "DF2,DF3", "noisy_label": False,
        "license": "internal", "pii_status": "clean", "created": "2026-08-05",
        "notes": "",
    }
    rec.update(overrides)
    return Sample(raw=rec, source_file="test", source_line=1)


def codes(report):
    return {f.code for f in report.findings}


# --- Section 2.5: length buckets ---------------------------------------


def test_word_count_is_whitespace_delimited_after_nfc():
    assert count_words("one two three") == 3
    assert count_words("  spaced   out  ") == 2
    # NFC normalization must not change the token count for clean text.
    assert count_words("café au lait") == count_words("café au lait")


@pytest.mark.parametrize("n,bucket", [
    (20, "B25"), (25, "B25"), (30, "B25"),
    (40, "B50"), (60, "B50"),
    (85, "B100"), (115, "B100"),
    (215, "B250"), (285, "B250"),
    (430, "B500"), (570, "B500"),
    (850, "B1000"), (5000, "B1000"),
])
def test_bucket_boundaries_match_the_spec(n, bucket):
    assert bucket_for(n) == bucket


@pytest.mark.parametrize("n", [0, 19, 31, 39, 61, 84, 116, 214, 286, 429, 571, 849])
def test_bucket_gaps_are_real_and_not_rounded_away(n):
    """Section 2.5 ranges are non-contiguous. A sample between buckets belongs
    to none, and widening the ranges to admit it would weaken the benchmark."""
    assert bucket_for(n) is None


def test_every_bucket_is_reachable():
    for bucket, (lo, _hi) in LENGTH_BUCKETS.items():
        assert bucket_for(lo) == bucket


# --- Section 4.7 / 5.2: per-sample validation --------------------------


def test_conformant_sample_passes():
    assert validate_sample(make_sample()).ok


def test_missing_required_field_is_an_error():
    s = make_sample()
    del s.raw["rationale"]
    assert "MISSING_FIELD" in codes(validate_sample(s))


def test_notes_is_the_only_optional_field():
    s = make_sample()
    del s.raw["notes"]
    assert validate_sample(s).ok


def test_id_must_agree_with_category_bucket_and_track():
    assert "ID_CATEGORY_MISMATCH" in codes(validate_sample(
        make_sample(id="H-02-B100-0001")))
    assert "ID_BUCKET_MISMATCH" in codes(validate_sample(
        make_sample(id="H-01-B250-0001")))


def test_malformed_id_is_rejected():
    assert "BAD_ID_FORMAT" in codes(validate_sample(make_sample(id="whatever-1")))


def test_unknown_category_is_rejected():
    assert "UNKNOWN_CATEGORY" in codes(validate_sample(
        make_sample(id="H-99-B100-0001", category="H-99")))


def test_track_must_match_the_category_registry():
    assert "TRACK_CATEGORY_MISMATCH" in codes(validate_sample(make_sample(track="A")))


def test_category_with_fixed_label_rejects_a_different_label():
    """H-01 is a human-only category (Section 3.1)."""
    assert "LABEL_CATEGORY_MISMATCH" in codes(validate_sample(
        make_sample(label="AI", ai_token_share=1.0)))


def test_length_words_must_match_the_harness_counter():
    assert "LENGTH_MISMATCH" in codes(validate_sample(make_sample(length_words=42)))


def test_bucket_must_match_the_word_count():
    s = make_sample(text=" ".join(["w"] * 250), length_words=250)
    assert "BUCKET_MISMATCH" in codes(validate_sample(s))


def test_sample_between_buckets_is_reported():
    s = make_sample(text=" ".join(["w"] * 35), length_words=35)
    assert "NO_BUCKET" in codes(validate_sample(s))


@pytest.mark.parametrize("label,share", [("HUMAN", 0.5), ("AI", 0.7)])
def test_label_and_ai_token_share_must_cohere(label, share):
    """Section 4.1 fixes the share for the two pure labels."""
    s = make_sample(id="X-01-B100-0001", category="X-01", track="X",
                    label=label, ai_token_share=share, provenance_tier="T1")
    assert "SHARE_LABEL_CONFLICT" in codes(validate_sample(s))


def test_ai_token_share_must_be_in_range():
    s = make_sample(id="X-01-B100-0001", category="X-01", track="X",
                    label="AI_HUMAN_EDITED", ai_token_share=1.4,
                    provenance_tier="T1")
    assert "SHARE_RANGE" in codes(validate_sample(s))


def test_collab_mixed_requires_a_span_map():
    s = make_sample(id="X-06-B100-0001", category="X-06", track="X",
                    label="COLLAB_MIXED", ai_token_share=0.5,
                    provenance_tier="T1", span_map=None)
    assert "SPAN_MAP_REQUIRED" in codes(validate_sample(s))


def test_malformed_span_entry_is_rejected():
    s = make_sample(id="X-06-B100-0001", category="X-06", track="X",
                    label="COLLAB_MIXED", ai_token_share=0.5,
                    provenance_tier="T1", span_map=[[0, 10, "robot"]])
    assert "BAD_SPAN_ENTRY" in codes(validate_sample(s))


def test_human_sample_may_not_carry_a_generator_record():
    assert "GENERATOR_ON_HUMAN" in codes(validate_sample(
        make_sample(generator={"family": "G1"})))


def test_generator_record_must_be_complete():
    s = make_sample(id="A-01-B100-0001", category="A-01", track="A",
                    label="AI", ai_token_share=1.0, provenance_tier="T1",
                    generator={"family": "G1"})
    assert "GENERATOR_INCOMPLETE" in codes(validate_sample(s))


def test_expected_confusions_must_be_df_codes():
    assert "BAD_DF_CODE" in codes(validate_sample(
        make_sample(expected_confusions="DF2,XX9")))


@pytest.mark.parametrize("field,value,code", [
    ("split", "prod", "BAD_SPLIT"),
    ("label", "MACHINE", "BAD_LABEL"),
    ("provenance_tier", "T9", "BAD_TIER"),
    ("difficulty", "D9", "BAD_DIFFICULTY"),
    ("pii_status", "maybe", "BAD_PII_STATUS"),
])
def test_enumerations_are_enforced(field, value, code):
    assert code in codes(validate_sample(make_sample(**{field: value})))


# --- Sections 2.3 / 4.2: split and provenance discipline ---------------


def test_t3_is_inadmissible_in_test():
    c = Corpus(samples=[make_sample(provenance_tier="T3")])
    assert "INADMISSIBLE_TIER" in codes(validate_splits(c))


def test_t3_is_allowed_in_dev():
    c = Corpus(samples=[make_sample(split="dev", provenance_tier="T3",
                                    noisy_label=True)])
    assert "INADMISSIBLE_TIER" not in codes(validate_splits(c))


def test_noisy_label_outside_dev_is_rejected():
    c = Corpus(samples=[make_sample(noisy_label=True)])
    assert "NOISY_LABEL_OUTSIDE_DEV" in codes(validate_splits(c))


def test_noisy_label_requires_t3():
    c = Corpus(samples=[make_sample(split="dev", noisy_label=True,
                                    provenance_tier="T0")])
    assert "NOISY_LABEL_TIER_CONFLICT" in codes(validate_splits(c))


def test_fairness_gated_categories_require_t1_or_t2():
    c = Corpus(samples=[make_sample(id="H-14-B100-0001", category="H-14",
                                    provenance_tier="T0")])
    assert "FAIRNESS_TIER" in codes(validate_splits(c))


def test_hybrid_categories_require_t1():
    c = Corpus(samples=[make_sample(id="X-01-B100-0001", category="X-01",
                                    track="X", label="AI_HUMAN_EDITED",
                                    ai_token_share=0.4, provenance_tier="T2")])
    assert "HYBRID_TIER" in codes(validate_splits(c))


def test_duplicate_ids_are_rejected():
    c = Corpus(samples=[make_sample(), make_sample()])
    assert "DUPLICATE_ID" in codes(validate_splits(c))


# --- Section 9.4: split discipline at the API --------------------------


def test_dev_cannot_be_used_for_reporting():
    c = Corpus(samples=[make_sample(split="dev")])
    with pytest.raises(SplitDisciplineError, match="2.3"):
        c.for_reporting("dev")


def test_thresholds_cannot_be_selected_on_test_or_hidden():
    c = Corpus(samples=[make_sample()])
    for split in ("test", "hidden"):
        with pytest.raises(SplitDisciplineError, match="9.4"):
            c.for_threshold_selection(split)


def test_dev_is_available_for_threshold_selection():
    c = Corpus(samples=[make_sample(split="dev")])
    assert len(c.for_threshold_selection("dev")) == 1


# --- Section 5.4 / 2.4: manifest ---------------------------------------


def test_manifest_missing_keys_are_reported():
    assert "MANIFEST_MISSING_KEY" in codes(validate_manifest(Manifest(raw={})))


def test_checksum_mismatch_is_detected(tmp_path):
    f = tmp_path / "samples.jsonl"
    f.write_text("hello", encoding="utf-8")
    m = Manifest(raw={"corpus_version": "1.0.0",
                      "checksums": {"samples.jsonl": "0" * 64}},
                 path=tmp_path / "manifest.json")
    assert "CHECKSUM_MISMATCH" in codes(validate_manifest(m, tmp_path))


def test_missing_checksummed_file_is_detected(tmp_path):
    m = Manifest(raw={"corpus_version": "1.0.0",
                      "checksums": {"gone.jsonl": "0" * 64}},
                 path=tmp_path / "manifest.json")
    assert "CHECKSUM_FILE_MISSING" in codes(validate_manifest(m, tmp_path))


def test_unpassed_decontamination_blocks_release():
    m = Manifest(raw={"corpus_version": "1.0.0",
                      "decontamination": {"status": "not_run"}})
    assert "DECONTAMINATION_NOT_PASSED" in codes(validate_manifest(m))


# --- Section 9.1: release acceptance -----------------------------------


def test_empty_corpus_is_not_releasable():
    report = validate_release(Corpus(samples=[]))
    assert not report.ok
    assert "CELL_UNDERPOPULATED" in codes(report)
    assert "POOLED_HUMAN_TEST_TOO_SMALL" in codes(report)


def test_release_validator_reports_unchecked_criteria_rather_than_skipping():
    """9.1(c) kappa and 9.1(f) canary cannot be checked from files alone. A
    green report must never imply a check that did not happen."""
    report = validate_release(Corpus(samples=[]))
    assert "NOT_MECHANICALLY_CHECKED" in {f.code for f in report.warnings}


def test_pooled_human_minimum_matches_the_spec():
    assert POOLED_HUMAN_TEST_MINIMUM["v1.0"] == 3000
    assert POOLED_HUMAN_TEST_MINIMUM["v2.0"] == 10000
    assert CELL_TARGETS["v1.0"]["test"] == 10
    assert CELL_TARGETS["v2.0"]["test"] == 50


def test_unknown_phase_is_rejected():
    with pytest.raises(ValueError, match="unknown phase"):
        validate_release(Corpus(samples=[]), phase="v9")


# --- benchmark definition data -----------------------------------------


def test_category_registry_covers_every_track():
    cats = load_categories()
    tracks = {c["track"] for c in cats.values()}
    assert tracks == {"H", "A", "X", "V", "F", "E", "U"}
    assert len(cats) == 99


def test_x12_uses_the_spec_default_policy():
    """Section 3.3: default HUMAN_AI_EDITED with transform=MT; no new policy."""
    assert load_categories()["X-12"]["expected_label"] == "HUMAN_AI_EDITED"


def test_failure_mode_map_is_transcribed_not_invented():
    """Section 6.2 leaves 33 categories uncovered. The map must reflect the
    spec as written; inventing entries to satisfy 9.1(h) would hide a real
    gap in the specification."""
    cats = set(load_categories())
    covered = set()
    for fm in load_failure_modes().values():
        covered |= set(fm.get("categories", []))
    assert len(cats - covered) == 33


# --- Section 9.3: runner ------------------------------------------------


class _StubDetector:
    """Scores by label so cell arithmetic is checkable without a real model."""

    def __init__(self, ai_score=0.9, human_score=0.1):
        self.ai, self.human = ai_score, human_score

    def score(self, text):
        from ai_text_eval.detectors.base import DetectorResult
        return DetectorResult(score=self.ai if "GENERATED" in text else self.human)


def _corpus_for_runner(n=12):
    samples = []
    for i in range(n):
        samples.append(make_sample(
            id=f"H-01-B100-{i:04d}", text=" ".join(["word"] * 100)))
    for i in range(n):
        samples.append(make_sample(
            id=f"A-01-B100-{i:04d}", category="A-01", track="A", label="AI",
            ai_token_share=1.0, provenance_tier="T1",
            text="GENERATED " + " ".join(["word"] * 99),
            generator={"family": "G1", "model_version": "v", "provider": "p",
                       "prompt_style": "PS1", "decoding": {}, "request_date": "2026-01-01",
                       "config_ref": "x"}))
    return Corpus(samples=samples,
                  manifest=Manifest(raw={"corpus_version": "1.0.0"}))


def test_runner_produces_per_cell_results():
    r = BenchmarkRunner(_corpus_for_runner(), _StubDetector()).run("T1", "test")
    assert {c.cell[0] for c in r.cells} == {"H-01", "A-01"}
    payload = r.to_dict()
    assert payload["cells"], "the per-cell table always ships (Section 9.3)"


def test_runner_headline_is_worst_cell_not_mean():
    """P4: averages hide exactly the failures the corpus exists to find."""
    r = BenchmarkRunner(_corpus_for_runner(), _StubDetector()).run("T1", "test")
    fpr = 0.01
    worst = r.worst_cell(fpr)
    macro = r.macro_mean(fpr)
    if worst is not None and macro is not None:
        assert worst.tpr_at_fpr[fpr] <= macro + 1e-9


def test_runner_refuses_dev_as_a_reporting_split():
    c = _corpus_for_runner()
    for s in c.samples:
        s.raw["split"] = "dev"
    with pytest.raises(SplitDisciplineError):
        BenchmarkRunner(c, _StubDetector()).run("T1", "dev")


def test_runner_quantizes_hidden_output():
    """Section 8.6: HIDDEN is reported at coarse granularity."""
    c = _corpus_for_runner()
    for s in c.samples:
        s.raw["split"] = "hidden"
    r = BenchmarkRunner(c, _StubDetector()).run("T1", "hidden")
    assert r.quantized is True


@pytest.mark.parametrize("task", ["T3", "T4"])
def test_unimplemented_tasks_refuse_rather_than_guess(task):
    with pytest.raises(NotImplementedError, match="manufacture evidence"):
        BenchmarkRunner(_corpus_for_runner(), _StubDetector()).run(task, "test")


def test_unknown_task_is_rejected():
    with pytest.raises(ValueError, match="unknown task"):
        BenchmarkRunner(_corpus_for_runner(), _StubDetector()).run("T9", "test")


def test_t1_excludes_hybrids_by_definition():
    c = _corpus_for_runner()
    c.samples.append(make_sample(
        id="X-01-B100-9999", category="X-01", track="X",
        label="AI_HUMAN_EDITED", ai_token_share=0.4, provenance_tier="T1"))
    r = BenchmarkRunner(c, _StubDetector()).run("T1", "test")
    assert not any(c_.cell[0] == "X-01" and c_.n for c_ in r.cells)


def test_cell_reports_its_fpr_resolution():
    """A cell of 12 negatives cannot express FPR=0.005."""
    r = BenchmarkRunner(_corpus_for_runner(), _StubDetector()).run("T1", "test")
    cell = next(c for c in r.cells if c.n_negative)
    assert cell.fpr_resolution == pytest.approx(1 / cell.n_negative)
    assert not cell.is_measurable_at(0.005)


# --- statistics ---------------------------------------------------------


def test_wilson_interval_stays_in_bounds():
    for k, n in ((0, 10), (10, 10), (1, 3), (0, 0)):
        lo, hi = wilson_interval(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_interval_is_wide_at_small_n():
    narrow = wilson_interval(50, 100)
    wide = wilson_interval(5, 10)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_quantize_matches_the_reporting_granularity():
    assert quantize(0.8731) == pytest.approx(0.875)
    assert quantize(0.8749) == pytest.approx(0.875)


# --- serialization ------------------------------------------------------


def test_field_order_is_preserved_on_round_trip():
    s = make_sample()
    assert list(s.to_ordered_dict()) == [k for k in FIELD_ORDER if k in s.raw]


def test_unknown_fields_survive_round_trip():
    """A newer schema must not lose data passing through older tooling."""
    s = make_sample(future_field="keep me")
    assert s.to_ordered_dict()["future_field"] == "keep me"


def test_text_is_never_normalized_on_load(tmp_path):
    """Section 5.2: storage is byte-exact. Normalizing here would defeat
    Track V's encoding attacks before a detector ever sees them."""
    tricky = "café ​ zero-width"
    f = tmp_path / "track_h.jsonl"
    f.write_text(json.dumps({"id": "H-01-B100-0001", "text": tricky}) + "\n",
                 encoding="utf-8")
    (tmp_path / "manifest.json").write_text('{"corpus_version":"1.0.0"}', encoding="utf-8")
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "track_h.jsonl").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    corpus = load_corpus(tmp_path)
    assert corpus.samples[0].text == tricky


def test_shipped_corpus_skeleton_loads():
    """The empty corpus is a valid state; only the release validator judges it."""
    corpus = load_corpus(require_manifest=False)
    assert corpus.manifest is not None
    assert len(corpus) == 0
