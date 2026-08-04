"""R-05 evidence package and chain-of-custody tests (CAS §5)."""

import json

import pytest

from ai_text_eval.gauntlet.evidence import (
    ARCHIVE_CUTOFF,
    TIER_ORDER,
    EvidenceItem,
    EvidenceKind,
    EvidencePackage,
    InadmissibleKind,
    checksum_package,
    derived_tier,
    file_checksum,
    load_package,
    supported_tier,
    validate_package,
    verify_integrity,
)


def item(kind, **attrs) -> EvidenceItem:
    return EvidenceItem(kind=kind if isinstance(kind, str) else kind.value,
                        path=attrs.pop("path", "artifact.bin"),
                        checksum=attrs.pop("checksum", "sha256:" + "0" * 64),
                        recorded_at="2026-08-05", attributes=attrs)


def archive_item(**over):
    attrs = {"capture_date": "2017-04-02", "archive": "example-archive",
             "independent": True, "integrity_record": "warc-digest"}
    attrs.update(over)
    return item(EvidenceKind.ARCHIVE_RECORD, **attrs)


def process_item(**over):
    attrs = {"logging_started_before_writing": True, "complete_session": True,
             "generative_tools_attested_absent": True}
    attrs.update(over)
    return item(EvidenceKind.PROCESS_CAPTURE, **attrs)


def generation_item(**over):
    attrs = {"model_family": "G1", "model_version": "v-exact", "provider": "p",
             "prompt": "…", "decoding": {"temperature": 1.0},
             "request_date": "2026-07-01", "raw_response": "…"}
    attrs.update(over)
    return item(EvidenceKind.GENERATION_SESSION, **attrs)


def chain_item(**over):
    attrs = {"states": ["base", "round1"], "instructions": "fix grammar only"}
    attrs.update(over)
    return item(EvidenceKind.INTERMEDIATE_CHAIN, **attrs)


def attestation_item(**over):
    attrs = {"author_identified": "A. Writer", "signature": "sig",
             "contemporaneous": True, "spot_verification_acknowledged": True,
             "tools_described": "plain text editor"}
    attrs.update(over)
    return item(EvidenceKind.ATTESTATION, **attrs)


def pkg(tier, items, sample_id="H-01-B100-0001", **kw) -> EvidencePackage:
    return EvidencePackage(sample_id=sample_id, tier=tier, items=items, **kw)


def codes(report):
    return {f.code for f in report.findings}


# -- tier support (§5.2) -------------------------------------------------


def test_t0_package_with_valid_archive_record_passes():
    assert validate_package(pkg("T0", [archive_item()])).ok


def test_t1_human_with_process_capture_passes():
    assert validate_package(pkg("T1", [process_item()]), label="HUMAN").ok


def test_t1_ai_with_generation_session_passes():
    assert validate_package(pkg("T1", [generation_item()]), label="AI").ok


def test_t1_hybrid_with_chain_passes():
    assert validate_package(pkg("T1", [chain_item()]), label="AI_HUMAN_EDITED").ok


def test_t2_with_complete_attestation_passes():
    assert validate_package(pkg("T2", [attestation_item()])).ok


def test_empty_package_cannot_support_a_tier():
    assert "EVIDENCE_PACKAGE_EMPTY" in codes(validate_package(pkg("T1", [])))


def test_tier_claimed_without_supporting_evidence_kind():
    """Tier is earned by evidence, not asserted."""
    report = validate_package(pkg("T0", [attestation_item()]))
    assert "TIER_OVERCLAIM" in codes(report)


def test_t1_claimed_with_only_attestation_is_an_overclaim():
    report = validate_package(pkg("T1", [attestation_item()]), label="HUMAN")
    assert "TIER_OVERCLAIM" in codes(report)


def test_supported_tier_reports_strongest_available():
    assert supported_tier(pkg("T0", [archive_item()])) == "T0"
    assert supported_tier(pkg("T1", [process_item()])) == "T1"
    assert supported_tier(pkg("T2", [attestation_item()])) == "T2"
    assert supported_tier(pkg("T3", [])) is None


def test_t1_label_requires_the_matching_evidence_kind():
    """§5.2: process capture for human, session transcripts for generation,
    intermediate chains for hybrids — they are not interchangeable."""
    report = validate_package(pkg("T1", [process_item()]), label="AI")
    assert "T1_WRONG_EVIDENCE_KIND" in codes(report)


def test_t3_is_flagged_as_inferred():
    report = validate_package(pkg("T3", []))
    assert "TIER_HEURISTIC" in {f.code for f in report.warnings}
    assert report.ok  # a warning, not an error: T3 is legal in DEV


def test_unknown_tier_is_rejected():
    assert "EVIDENCE_BAD_TIER" in codes(validate_package(pkg("T9", [])))


# -- inadmissible evidence (§5.4) ----------------------------------------


@pytest.mark.parametrize("kind", [k.value for k in InadmissibleKind])
def test_inadmissible_evidence_is_rejected(kind):
    report = validate_package(pkg("T1", [process_item(), item(kind)]),
                              label="HUMAN")
    assert "INADMISSIBLE_EVIDENCE" in codes(report)


def test_detector_output_as_evidence_is_rejected():
    """P3: using a detector to 'verify' human text makes the benchmark
    circular; circularity does not become acceptable by pointing in a
    convenient direction."""
    report = validate_package(
        pkg("T1", [process_item(), item(InadmissibleKind.DETECTOR_OUTPUT)]),
        label="HUMAN")
    assert "INADMISSIBLE_EVIDENCE" in codes(report)


def test_unknown_evidence_kind_is_rejected():
    report = validate_package(pkg("T1", [process_item(), item("vibes")]),
                              label="HUMAN")
    assert "UNKNOWN_EVIDENCE_KIND" in codes(report)


def test_evidence_without_a_checksum_is_rejected():
    report = validate_package(pkg("T1", [process_item(checksum="")]),
                              label="HUMAN")
    assert "EVIDENCE_NOT_CHECKSUMMED" in codes(report)


# -- T0 archive detail (§5.2, §5.3) --------------------------------------


def test_archive_captured_after_the_cutoff_is_rejected():
    report = validate_package(pkg("T0", [archive_item(capture_date="2021-06-01")]))
    assert "ARCHIVE_TOO_RECENT" in codes(report)


def test_archive_captured_exactly_at_the_cutoff_is_rejected():
    """The bound is strict: capture must predate 2020."""
    report = validate_package(
        pkg("T0", [archive_item(capture_date=ARCHIVE_CUTOFF.isoformat())]))
    assert "ARCHIVE_TOO_RECENT" in codes(report)


def test_archive_without_capture_date_is_rejected():
    report = validate_package(pkg("T0", [archive_item(capture_date=None)]))
    assert "ARCHIVE_NO_CAPTURE_DATE" in codes(report)


def test_archive_with_malformed_date_is_rejected():
    report = validate_package(pkg("T0", [archive_item(capture_date="last tuesday")]))
    assert "ARCHIVE_BAD_CAPTURE_DATE" in codes(report)


def test_archive_controlled_by_contributor_is_rejected():
    report = validate_package(pkg("T0", [archive_item(independent=False)]))
    assert "ARCHIVE_NOT_INDEPENDENT" in codes(report)


def test_archive_without_integrity_record_is_rejected():
    report = validate_package(pkg("T0", [archive_item(integrity_record=None)]))
    assert "ARCHIVE_NO_INTEGRITY_RECORD" in codes(report)


# -- T1 process detail (§3.2, §3.3, §3.4, §5.4) --------------------------


def test_retroactive_logging_is_rejected():
    """§3.2: the logging arrangement MUST be in place before writing begins."""
    report = validate_package(
        pkg("T1", [process_item(logging_started_before_writing=False)]),
        label="HUMAN")
    assert "LOGGING_NOT_PRE_ARRANGED" in codes(report)


def test_partial_session_record_is_rejected():
    report = validate_package(
        pkg("T1", [process_item(complete_session=False)]), label="HUMAN")
    assert "PARTIAL_SESSION_RECORD" in codes(report)


def test_unattested_environment_is_rejected():
    report = validate_package(
        pkg("T1", [process_item(generative_tools_attested_absent=False)]),
        label="HUMAN")
    assert "ENVIRONMENT_NOT_ATTESTED" in codes(report)


@pytest.mark.parametrize("missing", [
    "model_family", "model_version", "provider", "prompt", "decoding",
    "request_date", "raw_response",
])
def test_incomplete_generation_record_is_rejected(missing):
    report = validate_package(pkg("T1", [generation_item(**{missing: None})]),
                              label="AI")
    assert "GENERATION_RECORD_INCOMPLETE" in codes(report)


def test_hybrid_chain_needs_base_plus_a_round():
    report = validate_package(pkg("T1", [chain_item(states=["only-base"])]),
                              label="AI_HUMAN_EDITED")
    assert "CHAIN_TOO_SHORT" in codes(report)


def test_hybrid_chain_needs_recorded_instructions():
    """§3.4: the instruction defines the category."""
    report = validate_package(pkg("T1", [chain_item(instructions=None)]),
                              label="HUMAN_AI_EDITED")
    assert "CHAIN_NO_INSTRUCTIONS" in codes(report)


# -- T2 attestation detail (§5.3, §5.4) ----------------------------------


def test_anonymous_attestation_is_rejected():
    report = validate_package(pkg("T2", [attestation_item(author_identified=None)]))
    assert "ATTESTATION_ANONYMOUS" in codes(report)


def test_unsigned_attestation_is_rejected():
    report = validate_package(pkg("T2", [attestation_item(signature=None)]))
    assert "ATTESTATION_UNSIGNED" in codes(report)


def test_reconstructed_attestation_is_rejected():
    """§5.4: process descriptions reconstructed after the fact are
    inadmissible."""
    report = validate_package(pkg("T2", [attestation_item(contemporaneous=False)]))
    assert "ATTESTATION_RECONSTRUCTED" in codes(report)


def test_attestation_without_verification_right_is_rejected():
    report = validate_package(
        pkg("T2", [attestation_item(spot_verification_acknowledged=False)]))
    assert "ATTESTATION_NO_VERIFICATION_RIGHT" in codes(report)


def test_attestation_without_tool_description_is_rejected():
    report = validate_package(pkg("T2", [attestation_item(tools_described=None)]))
    assert "ATTESTATION_NO_TOOL_DESCRIPTION" in codes(report)


# -- derivation ceiling (§5.5) -------------------------------------------


@pytest.mark.parametrize("base", TIER_ORDER)
def test_derived_tier_never_exceeds_the_base(base):
    assert derived_tier(base, transform_recorded=True) == base


def test_derivation_without_a_transform_record_degrades_to_t3():
    assert derived_tier("T0", transform_recorded=False) == "T3"


def test_derived_tier_rejects_unknown_base():
    with pytest.raises(ValueError, match="unknown base tier"):
        derived_tier("T9", transform_recorded=True)


# -- integrity (§5.5, P4) ------------------------------------------------


def test_integrity_passes_for_unaltered_files(tmp_path):
    artefact = tmp_path / "session.log"
    artefact.write_text("keystrokes", encoding="utf-8")
    p = pkg("T1", [process_item(path="session.log",
                                checksum=file_checksum(artefact))], root=tmp_path)
    assert verify_integrity(p).ok


def test_integrity_detects_an_altered_artefact(tmp_path):
    artefact = tmp_path / "session.log"
    artefact.write_text("keystrokes", encoding="utf-8")
    p = pkg("T1", [process_item(path="session.log",
                                checksum=file_checksum(artefact))], root=tmp_path)
    artefact.write_text("tampered", encoding="utf-8")
    assert "EVIDENCE_CHECKSUM_MISMATCH" in codes(verify_integrity(p))


def test_integrity_detects_a_missing_artefact(tmp_path):
    p = pkg("T1", [process_item(path="gone.log")], root=tmp_path)
    assert "EVIDENCE_FILE_MISSING" in codes(verify_integrity(p))


def test_integrity_without_a_root_is_reported_not_assumed(tmp_path):
    report = verify_integrity(pkg("T1", [process_item()]))
    assert "INTEGRITY_NOT_CHECKED" in {f.code for f in report.warnings}


def test_checksum_package_fills_missing_checksums(tmp_path):
    artefact = tmp_path / "a.bin"
    artefact.write_bytes(b"data")
    p = pkg("T1", [process_item(path="a.bin", checksum="")], root=tmp_path)
    checksum_package(p, stamped_at="2026-08-05")
    assert p.items[0].checksum == file_checksum(artefact)
    assert p.intake_checksummed_at == "2026-08-05"


def test_checksum_package_never_overwrites_an_existing_checksum(tmp_path):
    """Re-blessing an altered artefact is the failure custody prevents."""
    artefact = tmp_path / "a.bin"
    artefact.write_bytes(b"original")
    original = file_checksum(artefact)
    p = pkg("T1", [process_item(path="a.bin", checksum=original)], root=tmp_path)
    artefact.write_bytes(b"altered")
    checksum_package(p)
    assert p.items[0].checksum == original
    assert "EVIDENCE_CHECKSUM_MISMATCH" in codes(verify_integrity(p))


def test_checksum_package_requires_a_root():
    with pytest.raises(ValueError, match="needs a package root"):
        checksum_package(pkg("T1", [process_item()]))


# -- package IO ----------------------------------------------------------


def test_package_round_trips_through_json(tmp_path):
    p = pkg("T1", [process_item()], base_package_ref="provenance/base")
    (tmp_path / "evidence.json").write_text(json.dumps(p.to_dict()), encoding="utf-8")
    back = load_package(tmp_path)
    assert back.tier == "T1"
    assert back.sample_id == p.sample_id
    assert back.base_package_ref == "provenance/base"
    assert back.kinds() == {"process_capture"}


def test_missing_manifest_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not enter validation"):
        load_package(tmp_path)


def test_corrupt_manifest_is_reported(tmp_path):
    (tmp_path / "evidence.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_package(tmp_path)
