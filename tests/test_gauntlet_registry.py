"""R-01 field registry tests. Sections cited per test.

The registry is what makes CAS §4.1 true: fields exist only via governed
data, vocabularies are closed, and version arithmetic decides which fields a
record may carry.
"""

import json

import pytest

from ai_text_eval.gauntlet.registry import FieldRegistry, load_field_registry
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.spec import FIELD_ORDER


def sample_v(version="1", **overrides) -> Sample:
    rec = {
        "id": "H-01-B100-0001", "schema_version": version, "corpus_version": "1.0.0",
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
    return Sample(raw=rec, source_file="test", source_line=1)


def codes(report):
    return {f.code for f in report.findings}


# -- loading -------------------------------------------------------------


def test_registry_loads_from_benchmark_dir():
    reg = load_field_registry()
    assert reg.registry_version == "2"
    assert reg.current_schema_version == "2"


def test_missing_registry_is_a_hard_error(tmp_path):
    """CAS 4.1: fields exist only via the registry; there is no code fallback."""
    with pytest.raises(FileNotFoundError, match="no code fallback"):
        load_field_registry(tmp_path)


def test_v1_field_order_matches_the_bs_schema():
    """BS 5.2 field order is preserved exactly for v1 records."""
    reg = load_field_registry()
    assert reg.field_order("1") == list(FIELD_ORDER)


def test_v2_order_is_v1_order_plus_appended_fields():
    """CAS 4.3: additive amendment; existing order never reshuffles."""
    reg = load_field_registry()
    v1, v2 = reg.field_order("1"), reg.field_order("2")
    assert v2[: len(v1)] == v1
    assert set(v2) - set(v1) == {"lineage", "difficulty_panel_version",
                                 "difficulty_provisional"}


def test_every_field_has_purpose_and_since():
    """CAS 4.1: every field has a stated purpose and a since version."""
    reg = load_field_registry()
    for name, meta in reg.fields.items():
        assert meta.get("purpose"), f"{name} lacks a purpose"
        assert meta.get("since") in ("1", "2"), f"{name} lacks a since version"


# -- version arithmetic --------------------------------------------------


def test_v2_fields_are_unknown_at_v1():
    reg = load_field_registry()
    assert "lineage" not in reg.known_at("1")
    assert "lineage" in reg.known_at("2")


def test_conformant_v1_record_passes():
    assert load_field_registry().validate_fields(sample_v("1")).ok


def test_conformant_v2_record_passes():
    s = sample_v("2", lineage=[], difficulty_provisional=True,
                 difficulty_panel_version=None)
    assert load_field_registry().validate_fields(s).ok


def test_unregistered_field_is_an_error():
    """CAS 4.1: records MUST NOT contain fields absent from the registry."""
    s = sample_v("1", invented_field="x")
    assert "UNREGISTERED_FIELD" in codes(load_field_registry().validate_fields(s))


def test_v2_field_in_v1_record_is_an_error():
    """A v1 record carrying a v2 field claims a schema it does not have."""
    s = sample_v("1", lineage=[])
    assert "FIELD_FROM_FUTURE_SCHEMA" in codes(
        load_field_registry().validate_fields(s))


def test_newer_schema_is_tolerated_with_a_warning():
    """CAS 4.1 forward compatibility: consumers must ignore fields added
    after their own version, and the version gap must be visible."""
    s = sample_v("99", some_future_field="whatever")
    report = load_field_registry().validate_fields(s)
    assert report.ok
    assert "NEWER_SCHEMA_TOLERATED" in codes(report)


def test_garbage_schema_version_is_an_error():
    s = sample_v("not-a-version")
    assert "BAD_SCHEMA_VERSION" in codes(load_field_registry().validate_fields(s))


def test_missing_required_field_is_reported_per_version():
    s = sample_v("1")
    del s.raw["rationale"]
    assert "MISSING_FIELD" in codes(load_field_registry().validate_fields(s))


def test_optional_fields_may_be_absent():
    s = sample_v("1")
    del s.raw["notes"]
    del s.raw["expected_confusions"]
    assert load_field_registry().validate_fields(s).ok


def test_deprecated_field_warns_but_does_not_fail():
    """CAS 4.1: fields are deprecated, never removed; old records stay valid."""
    reg = FieldRegistry(raw={
        "registry_version": "2", "current_schema_version": "2",
        "field_order": {"1": ["id", "schema_version", "old_field"]},
        "fields": {
            "id": {"purpose": "x", "since": "1", "required": True},
            "schema_version": {"purpose": "x", "since": "1", "required": True},
            "old_field": {"purpose": "x", "since": "1", "required": False,
                          "deprecated_since": "2"},
        },
    })
    s = Sample(raw={"id": "H-01-B100-0001", "schema_version": "2", "old_field": 1})
    report = reg.validate_fields(s)
    assert report.ok
    assert "DEPRECATED_FIELD" in codes(report)


def test_deprecated_field_is_not_required():
    reg = FieldRegistry(raw={
        "registry_version": "2", "current_schema_version": "2",
        "fields": {"gone": {"purpose": "x", "since": "1", "required": True,
                            "deprecated_since": "2"}},
    })
    assert "gone" in reg.required_at("1")
    assert "gone" not in reg.required_at("2")


# -- relationship vocabulary (P10) ----------------------------------------


def test_relationship_vocabulary_is_closed():
    reg = load_field_registry()
    assert set(reg.relationship_types) == {
        "derived_from", "supersedes", "tell_pair", "mimicry_pair"}
    assert reg.mutual_relationship_types == {"tell_pair", "mimicry_pair"}


def test_unknown_relation_is_rejected():
    s = sample_v("2", lineage=[{"relation": "vibes_with", "target": "H-01-B100-0002"}])
    assert "BAD_RELATION" in codes(load_field_registry().validate_fields(s))


def test_malformed_lineage_entry_is_rejected():
    s = sample_v("2", lineage=[{"relation": "derived_from"}])
    assert "BAD_LINEAGE_ENTRY" in codes(load_field_registry().validate_fields(s))
    s2 = sample_v("2", lineage="not-a-list")
    assert "BAD_LINEAGE_ENTRY" in codes(load_field_registry().validate_fields(s2))


def test_well_formed_lineage_passes():
    s = sample_v("2", lineage=[{"relation": "derived_from",
                                "target": "A-01-B100-0001"}])
    assert load_field_registry().validate_fields(s).ok


# -- roles ----------------------------------------------------------------


def test_role_vocabulary_covers_cas_section_11():
    roles = load_field_registry().roles
    for role in ("contributor", "generation_operator", "maintainer",
                 "reviewer", "adjudicator", "release_manager"):
        assert role in roles


def test_registry_file_is_valid_json_with_history():
    reg = load_field_registry()
    assert isinstance(reg.raw.get("history"), list)
    assert len(reg.raw["history"]) >= 2
