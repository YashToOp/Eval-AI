"""R-04 cross-field consistency tests (CAS §4.2, §4.4, §7.3, P10).

These lock the rules that were incomplete after milestone 1: span tiling,
generator-required for every non-HUMAN label, hybrid share range, Track V
transform+lineage, difficulty/panel binding, and both-ways relationship
resolution.
"""

import pytest

from ai_text_eval.gauntlet.loader import Corpus
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.validate import validate_relationships, validate_sample

GEN = {"family": "G1", "model_version": "v1", "provider": "p",
       "prompt_style": "PS1", "decoding": {}, "request_date": "2026-01-01",
       "config_ref": "generation_configs/x"}


def base(**overrides) -> dict:
    rec = {
        "id": "H-01-B100-0001", "schema_version": "1", "corpus_version": "1.0.0",
        "split": "test", "text": " ".join(["word"] * 100), "category": "H-01",
        "track": "H", "domain": "casual", "format": "prose", "language": "en-native",
        "length_words": 100, "length_bucket": "B100", "label": "HUMAN",
        "ai_token_share": 0.0, "span_map": None, "source_type": "archive_2017",
        "provenance_tier": "T0", "provenance_ref": "", "generator": None,
        "transforms": [], "topic_group_id": None, "difficulty": "D3",
        "rationale": "Pre-LLM archival forum post.",
        "target_weakness": "informal register baseline",
        "expected_confusions": "DF2", "noisy_label": False,
        "license": "internal", "pii_status": "clean", "created": "2026-08-05",
        "notes": "",
    }
    rec.update(overrides)
    return rec


def v2(**overrides) -> dict:
    rec = base(schema_version="2", lineage=[], difficulty_panel_version=None,
               difficulty_provisional=True)
    rec.update(overrides)
    return rec


def sample(rec) -> Sample:
    return Sample(raw=rec, source_file="test", source_line=1)


def codes(rec):
    return {f.code for f in validate_sample(sample(rec)).findings}


# -- span tiling (CAS §4.2) ----------------------------------------------

def collab(text="abcdefghij", spans=None, **kw) -> dict:
    return v2(id="X-06-B100-0001", category="X-06", track="X",
             label="COLLAB_MIXED", ai_token_share=0.5, provenance_tier="T1",
             generator=GEN, text=text, length_words=1,
             span_map=spans if spans is not None else [[0, 5, "human"], [5, 10, "ai"]],
             **kw)


def test_valid_tiling_passes():
    # text length 10, spans [0,5) human + [5,10) ai — a clean tiling.
    rec = collab()
    # length_words for the toy text is 1 (single token); fix it so the length
    # check does not mask the span check.
    rec["text"] = "abcdefghij"
    rec["length_words"] = 1
    found = codes(rec)
    assert "SPAN_OVERLAP" not in found
    assert "SPAN_GAP" not in found
    assert "SPAN_INCOMPLETE" not in found


def test_overlapping_spans_are_rejected():
    rec = collab(spans=[[0, 6, "human"], [4, 10, "ai"]])
    assert "SPAN_OVERLAP" in codes(rec)


def test_gap_between_spans_is_rejected():
    rec = collab(spans=[[0, 4, "human"], [6, 10, "ai"]])
    assert "SPAN_GAP" in codes(rec)


def test_spans_not_covering_whole_text_are_rejected():
    rec = collab(spans=[[0, 5, "human"]])  # text is 10 chars
    assert "SPAN_INCOMPLETE" in codes(rec)


def test_span_out_of_range_is_rejected():
    rec = collab(spans=[[0, 99, "human"]])
    assert "SPAN_OUT_OF_RANGE" in codes(rec)


def test_malformed_span_still_short_circuits_before_tiling():
    """A bad-origin entry reports BAD_SPAN_ENTRY, not a tiling error."""
    rec = collab(spans=[[0, 10, "robot"]])
    found = codes(rec)
    assert "BAD_SPAN_ENTRY" in found
    assert "SPAN_INCOMPLETE" not in found


# -- generator required for every non-HUMAN label (CAS §4.2/§4.4) --------

@pytest.mark.parametrize("label,share", [
    ("AI", 1.0), ("AI_HUMAN_EDITED", 0.7), ("HUMAN_AI_EDITED", 0.3),
])
def test_generator_required_for_non_human_labels(label, share):
    cat = {"AI": ("A-01", "A"), "AI_HUMAN_EDITED": ("X-01", "X"),
           "HUMAN_AI_EDITED": ("X-03", "X")}[label]
    rec = v2(id=f"{cat[0]}-B100-0001", category=cat[0], track=cat[1],
             label=label, ai_token_share=share, provenance_tier="T1",
             generator=None)
    if cat[1] == "X":
        rec["span_map"] = None
    assert "GENERATOR_REQUIRED" in codes(rec)


def test_human_ai_edited_now_requires_a_generator():
    """The milestone-1 carve-out that exempted HUMAN_AI_EDITED was wrong:
    the editing model still needs a recorded configuration."""
    rec = v2(id="X-03-B100-0001", category="X-03", track="X",
             label="HUMAN_AI_EDITED", ai_token_share=0.3, provenance_tier="T1",
             generator=None)
    assert "GENERATOR_REQUIRED" in codes(rec)


def test_generator_on_human_is_a_p2_alarm():
    assert "GENERATOR_ON_HUMAN" in codes(base(generator=GEN))


# -- hybrid share range (CAS §4.4) ---------------------------------------

@pytest.mark.parametrize("share", [0.0, 1.0])
def test_hybrid_share_must_be_strictly_between_0_and_1(share):
    rec = v2(id="X-01-B100-0001", category="X-01", track="X",
             label="AI_HUMAN_EDITED", ai_token_share=share, provenance_tier="T1",
             generator=GEN)
    assert "HYBRID_SHARE_RANGE" in codes(rec)


def test_hybrid_share_in_range_passes():
    rec = v2(id="X-01-B100-0001", category="X-01", track="X",
             label="AI_HUMAN_EDITED", ai_token_share=0.4, provenance_tier="T1",
             generator=GEN)
    assert "HYBRID_SHARE_RANGE" not in codes(rec)


# -- Track V transform + lineage (CAS §4.4, P10) -------------------------

def track_v(**kw) -> dict:
    rec = v2(id="V-05-B100-0001", category="V-05", track="V", label="AI",
             ai_token_share=1.0, provenance_tier="T1", generator=GEN,
             transforms=[{"name": "paraphrase", "date": "2026-07"}],
             lineage=[{"relation": "derived_from", "target": "A-01-B100-0001"}])
    rec.update(kw)
    return rec


def test_track_v_requires_a_transform_record():
    assert "V_TRANSFORM_REQUIRED" in codes(track_v(transforms=[]))


def test_track_v_requires_a_derived_from_link():
    assert "V_LINEAGE_REQUIRED" in codes(track_v(lineage=[]))


def test_well_formed_track_v_passes_the_v_rules():
    found = codes(track_v())
    assert "V_TRANSFORM_REQUIRED" not in found
    assert "V_LINEAGE_REQUIRED" not in found


def test_v1_track_v_is_not_held_to_the_lineage_rule():
    """Lineage is a v2 field; a legacy v1 V-record predates it and cannot
    carry it, so V_LINEAGE_REQUIRED must not fire against v1."""
    rec = base(id="V-05-B100-0001", category="V-05", track="V", label="AI",
               ai_token_share=1.0, provenance_tier="T1", generator=GEN,
               transforms=[{"name": "paraphrase"}])
    assert "V_LINEAGE_REQUIRED" not in codes(rec)


# -- difficulty / panel binding (CAS §7.3) -------------------------------

def test_empirical_difficulty_must_name_a_panel():
    rec = v2(difficulty_provisional=False, difficulty_panel_version=None)
    assert "DIFFICULTY_WITHOUT_PANEL" in codes(rec)


def test_empirical_difficulty_with_panel_passes():
    rec = v2(difficulty_provisional=False, difficulty_panel_version="panel-2026-08")
    assert "DIFFICULTY_WITHOUT_PANEL" not in codes(rec)


def test_provisional_difficulty_needs_no_panel():
    rec = v2(difficulty_provisional=True, difficulty_panel_version=None)
    assert "DIFFICULTY_WITHOUT_PANEL" not in codes(rec)


# -- relationships resolve both ways (CAS §4.4, P10) ---------------------

def test_derived_from_target_must_exist():
    s = sample(v2(id="V-05-B100-0001", category="V-05", track="V", label="AI",
                  ai_token_share=1.0, provenance_tier="T1", generator=GEN,
                  transforms=[{"name": "x"}],
                  lineage=[{"relation": "derived_from", "target": "A-99-B100-9999"}]))
    report = validate_relationships(Corpus(samples=[s]))
    assert "LINEAGE_TARGET_MISSING" in {f.code for f in report.errors}


def test_derived_from_resolves_when_base_present():
    derived = sample(v2(id="V-05-B100-0001", category="V-05", track="V", label="AI",
                        ai_token_share=1.0, provenance_tier="T1", generator=GEN,
                        transforms=[{"name": "x"}],
                        lineage=[{"relation": "derived_from", "target": "A-01-B100-0001"}]))
    base_s = sample(v2(id="A-01-B100-0001", category="A-01", track="A", label="AI",
                       ai_token_share=1.0, provenance_tier="T1", generator=GEN))
    report = validate_relationships(Corpus(samples=[derived, base_s]))
    assert report.ok


def test_mutual_relationship_must_be_declared_both_ways():
    a = sample(v2(id="V-11-B100-0001", category="V-11", track="V", label="HUMAN",
                  ai_token_share=0.0, provenance_tier="T1", generator=None,
                  transforms=[{"name": "tell_injection"}],
                  lineage=[{"relation": "derived_from", "target": "H-19-B100-0001"},
                           {"relation": "tell_pair", "target": "V-10-B100-0001"}]))
    b = sample(v2(id="V-10-B100-0001", category="V-10", track="V", label="AI",
                  ai_token_share=1.0, provenance_tier="T1", generator=GEN,
                  transforms=[{"name": "tell_suppression"}],
                  lineage=[{"relation": "derived_from", "target": "A-01-B100-0001"}]))
    report = validate_relationships(Corpus(samples=[a, b]))
    assert "RELATIONSHIP_NOT_MUTUAL" in {f.code for f in report.errors}


def test_mutual_relationship_declared_both_ways_passes():
    a = sample(v2(id="V-11-B100-0001", category="V-11", track="V", label="HUMAN",
                  ai_token_share=0.0, provenance_tier="T1", generator=None,
                  transforms=[{"name": "tell_injection"}],
                  lineage=[{"relation": "tell_pair", "target": "V-10-B100-0001"}]))
    b = sample(v2(id="V-10-B100-0001", category="V-10", track="V", label="AI",
                  ai_token_share=1.0, provenance_tier="T1", generator=GEN,
                  transforms=[{"name": "tell_suppression"}],
                  lineage=[{"relation": "tell_pair", "target": "V-11-B100-0001"}]))
    report = validate_relationships(Corpus(samples=[a, b]))
    assert "RELATIONSHIP_NOT_MUTUAL" not in {f.code for f in report.errors}
