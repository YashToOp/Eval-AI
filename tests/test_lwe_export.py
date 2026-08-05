"""LWE M3: evidence export and GAUNTLET integration (CAS §3.2, §5.2-§5.4).

The test that carries this milestone is the end-to-end one: a session written
in the tool reaches VALIDATED through the real `IntakeDesk`, with no change to
`evidence.py`. If the export had to relax a validator to fit, the design would
be wrong.

The second theme is that tier is *earned*. Every path that would let a
contributor claim more than their evidence supports is tested from the
claiming side.
"""

import json

import pytest

from ai_text_eval.gauntlet.evidence import (
    EvidenceKind,
    supported_tier as evidence_supported_tier,
    validate_package,
    verify_integrity,
)
from ai_text_eval.gauntlet.intake import IntakeDesk
from ai_text_eval.gauntlet.ledger import DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, State
from ai_text_eval.lwe.export import (
    ExportError,
    build_candidate,
    build_declaration,
    build_package,
    export,
    supported_tier,
)
from ai_text_eval.lwe.session import (
    Attestations,
    SessionIntent,
    SessionState,
    create_session,
    load_session,
)

TS = "2026-08-05T00:00:00Z"
WALL = "2026-08-05T10:00:00Z"

#: 100 words, so the sample satisfies the B100 bucket the metadata declares.
SAMPLE_TEXT = " ".join(["word"] * 100)


def attestations(**over):
    base = dict(consent_to_logging=True,
                environment_free_of_generative_tools=True,
                spot_verification_acknowledged=True,
                tools_used=["gauntlet-write"])
    base.update(over)
    return Attestations(**base)


def session(tmp_path, name="s1", text=SAMPLE_TEXT, att=None, close=True,
            contributor="wren"):
    s = create_session(tmp_path / name, session_id=name,
                       clock=lambda: 0, wall=lambda: WALL)
    s.open(SessionIntent(contributor=contributor, prompt="Describe a commute.",
                         intended_category="H-01", intended_length_bucket="B100",
                         language="en-native"),
           att or attestations())
    if text:
        s.insert(0, text)
    if close:
        s.close()
    return s


def metadata(**over):
    md = {
        "id": "H-01-B100-0001", "schema_version": "1", "corpus_version": "1.0.0",
        "split": "dev", "text": SAMPLE_TEXT, "category": "H-01", "track": "H",
        "domain": "casual", "format": "prose", "language": "en-native",
        "length_words": 100, "length_bucket": "B100", "label": "HUMAN",
        "ai_token_share": 0.0, "span_map": None, "source_type": "commissioned_T1",
        "provenance_tier": "T1", "provenance_ref": "provenance/h01/0001",
        "generator": None, "transforms": [], "topic_group_id": None,
        "difficulty": "D3", "rationale": "Commissioned under process logging.",
        "target_weakness": "contemporary informal register",
        "expected_confusions": "DF2", "noisy_label": False,
        "license": "internal", "pii_status": "clean", "created": "2026-08-05",
        "notes": "",
    }
    md.update(over)
    return md


def codes(report):
    return {f.code for f in report.findings}


# =====================================================================
# Tier is earned, not requested
# =====================================================================


def test_a_clean_logged_session_supports_t1(tmp_path):
    tier, reasons = supported_tier(session(tmp_path))
    assert tier == "T1"
    assert any("verified" in r for r in reasons)


def test_declining_the_environment_attestation_lands_at_t2(tmp_path):
    """Not a rejection: the named author's signed description of their own
    process is exactly what CAS §5.2 calls T2."""
    s = session(tmp_path, att=attestations(environment_free_of_generative_tools=False))
    tier, reasons = supported_tier(s)
    assert tier == "T2"
    assert any("did not attest" in r for r in reasons)


def test_an_unsealed_session_supports_nothing(tmp_path):
    tier, reasons = supported_tier(session(tmp_path, close=False))
    assert tier == ""
    assert any("never closed" in r for r in reasons)


def test_an_abandoned_session_supports_nothing(tmp_path):
    s = session(tmp_path, close=False)
    s.abandon("changed my mind")
    tier, reasons = supported_tier(s)
    assert tier == ""
    assert any("abandoned" in r for r in reasons)


def test_a_tampered_journal_supports_nothing(tmp_path):
    s = session(tmp_path)
    path = s.root / "journal.jsonl"
    path.write_text(path.read_text().replace("word", "WORD", 1))
    tier, reasons = supported_tier(load_session(s.root))
    assert tier == ""
    assert any("hash chain is broken" in r for r in reasons)


def test_reasons_are_given_even_when_the_tier_passes(tmp_path):
    """A reviewer should see which checks were consulted, not infer it from
    silence."""
    _, reasons = supported_tier(session(tmp_path))
    assert len(reasons) >= 2
    assert any("event 0" in r for r in reasons)


# =====================================================================
# The package satisfies GAUNTLET's own validators, unmodified
# =====================================================================


def test_a_t1_package_carries_the_three_cas_attributes(tmp_path):
    package, tier, _ = build_package(session(tmp_path), recorded_at=TS)
    attrs = package.items[0].attributes
    assert tier == "T1"
    assert attrs["logging_started_before_writing"] is True
    assert attrs["complete_session"] is True
    assert attrs["generative_tools_attested_absent"] is True


def test_a_t1_package_validates_against_gauntlet_unmodified(tmp_path):
    """The strongest evidence that the design fits: `evidence.py` needed no
    change to accept what the writing tool produces."""
    package, _, _ = build_package(session(tmp_path), recorded_at=TS)
    report = validate_package(package, label="HUMAN", claimed_tier="T1")
    assert report.ok, sorted(codes(report))


def test_the_package_is_a_process_capture_which_is_what_human_t1_requires(tmp_path):
    """CAS §5.2 / T1_KIND_FOR_LABEL: process capture for human writing."""
    package, _, _ = build_package(session(tmp_path), recorded_at=TS)
    assert package.items[0].kind == EvidenceKind.PROCESS_CAPTURE.value
    assert evidence_supported_tier(package) == "T1"


def test_a_t2_package_validates_as_an_attestation(tmp_path):
    s = session(tmp_path, att=attestations(environment_free_of_generative_tools=False))
    package, tier, _ = build_package(s, recorded_at=TS)
    assert tier == "T2"
    assert package.items[0].kind == EvidenceKind.ATTESTATION.value
    assert validate_package(package, label="HUMAN", claimed_tier="T2").ok


def test_the_journal_checksum_detects_post_export_tampering(tmp_path):
    """`verify_integrity` re-reads the file; editing it after export is caught
    without any LWE-specific machinery."""
    s = session(tmp_path)
    package, _, _ = build_package(s, recorded_at=TS)
    assert verify_integrity(package, s.root).ok
    path = s.root / "journal.jsonl"
    path.write_text(path.read_text() + "\n")
    assert not verify_integrity(package, s.root).ok


def test_the_package_never_claims_an_attestation_that_was_not_made(tmp_path):
    """The export writes `generative_tools_attested_absent` only when the
    contributor affirmed it in event 0."""
    s = session(tmp_path, att=attestations(environment_free_of_generative_tools=False))
    package, _, _ = build_package(s, recorded_at=TS)
    assert "generative_tools_attested_absent" not in package.items[0].attributes


# =====================================================================
# Declarations reflect what the contributor said
# =====================================================================


def test_the_declaration_carries_the_contributor_and_tools(tmp_path):
    declaration = build_declaration(session(tmp_path))
    assert declaration.contributor == "wren"
    assert declaration.model_involved is False
    assert declaration.tools_used == ["gauntlet-write"]


def test_a_declared_model_involvement_is_passed_through_not_suppressed(tmp_path):
    """The export must not pre-empt the Generation Firewall by refusing.
    Recording an honest declaration that then fails is the system working."""
    s = session(tmp_path, att=attestations(declared_model_involvement=True))
    assert build_declaration(s).model_involved is True


# =====================================================================
# End to end through the real intake desk
# =====================================================================


def desk(tmp_path):
    return IntakeDesk(IdentifierRegistry(path=tmp_path / "ids.jsonl"),
                      DecisionLedger(path=tmp_path / "decisions.jsonl"))


def test_a_written_session_reaches_validated_through_intake(tmp_path):
    """The milestone's exit criterion, and the whole point of the boundary."""
    s = session(tmp_path / "sessions")
    candidate = build_candidate(s, "H-01-B100-0001", metadata(), recorded_at=TS)
    result = desk(tmp_path).submit(candidate, "contributor", TS)
    assert result.ok, sorted(codes(result.report))
    assert result.state is State.VALIDATED


def test_intake_freezes_exactly_the_text_that_was_written(tmp_path):
    s = session(tmp_path / "sessions")
    d = desk(tmp_path)
    candidate = build_candidate(s, "H-01-B100-0001", metadata(), recorded_at=TS)
    d.submit(candidate, "contributor", TS)
    assert d.registry.get("H-01-B100-0001").verify_text(s.text)


def test_a_declared_model_involvement_is_rejected_by_the_firewall(tmp_path):
    """End to end: the LWE hands over an honest declaration and X-1 does the
    rest. No LWE-side refusal, no LWE-side exemption."""
    s = session(tmp_path / "sessions",
                att=attestations(declared_model_involvement=True))
    candidate = build_candidate(s, "H-01-B100-0001", metadata(), recorded_at=TS)
    result = desk(tmp_path).submit(candidate, "contributor", TS)
    assert result.firewall_triggered
    assert "FIREWALL_DECLARED_MODEL_INVOLVEMENT" in codes(result.report)


def test_a_t2_session_claiming_t1_metadata_is_rejected(tmp_path):
    """A contributor who declined the attestation cannot reach T1 by editing
    the metadata: the evidence, not the claim, decides."""
    s = session(tmp_path / "sessions",
                att=attestations(environment_free_of_generative_tools=False))
    candidate = build_candidate(s, "H-01-B100-0001",
                                metadata(provenance_tier="T1"), recorded_at=TS)
    candidate.evidence.tier = "T1"
    result = desk(tmp_path).submit(candidate, "contributor", TS)
    assert not result.ok
    assert "TIER_OVERCLAIM" in codes(result.report) or \
           "T1_WRONG_EVIDENCE_KIND" in codes(result.report)


def test_a_session_with_no_supported_tier_refuses_to_build_a_candidate(tmp_path):
    s = session(tmp_path / "sessions", close=False)
    with pytest.raises(ExportError, match="supports no evidence tier"):
        build_candidate(s, "H-01-B100-0001", metadata())


# =====================================================================
# Export artifacts
# =====================================================================


def test_export_writes_the_three_artifacts(tmp_path):
    s = session(tmp_path)
    export(s, recorded_at=TS)
    for name in ("journal.jsonl", "text.txt", "session.json", "evidence.json"):
        assert (s.root / name).is_file(), name


def test_the_session_manifest_reports_the_tier_and_its_reasons(tmp_path):
    s = session(tmp_path)
    export(s, recorded_at=TS)
    manifest = json.loads((s.root / "session.json").read_text())
    assert manifest["supported_tier"] == "T1"
    assert manifest["tier_reasons"]
    assert manifest["verification"]["verified"] is True


def test_export_moves_a_closed_session_to_exported(tmp_path):
    s = session(tmp_path)
    export(s, recorded_at=TS)
    assert s.state is SessionState.EXPORTED
    assert load_session(s.root).state is SessionState.EXPORTED


def test_export_is_idempotent(tmp_path):
    s = session(tmp_path)
    first = export(s, recorded_at=TS)
    manifest_before = (s.root / "evidence.json").read_text()
    second = export(s, recorded_at=TS)
    assert first.tier == second.tier == "T1"
    assert (s.root / "evidence.json").read_text() == manifest_before


def test_exporting_a_session_that_never_opened_is_refused(tmp_path):
    s = create_session(tmp_path / "unopened", session_id="unopened")
    with pytest.raises(ExportError, match="never opened"):
        export(s)


def test_a_failed_export_still_writes_the_record_of_why(tmp_path):
    """A session that supports nothing is still a fact; the manifest says so
    rather than the export silently producing nothing."""
    s = session(tmp_path, close=False)
    result = export(s, recorded_at=TS)
    assert not result.ok
    manifest = json.loads((s.root / "session.json").read_text())
    assert manifest["supported_tier"] == ""
    assert manifest["tier_reasons"]
    assert not (s.root / "evidence.json").is_file()


def test_the_export_result_serializes_for_a_reviewer(tmp_path):
    payload = export(session(tmp_path), recorded_at=TS).to_dict()
    assert payload["tier"] == "T1"
    assert payload["supported"] is True
    assert payload["reasons"]
