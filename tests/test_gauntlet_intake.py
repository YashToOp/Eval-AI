"""R-07 intake and Generation Firewall tests (CAS §2, §3.1, X-1, P2, §11.6).

The firewall tests are the most important in the repository: a P2 breach
cannot be cured after the fact, so the only place it can be stopped is here.
"""

import pytest

from ai_text_eval.gauntlet.evidence import EvidenceItem, EvidenceKind, EvidencePackage
from ai_text_eval.gauntlet.intake import (
    PROTECTED_LABEL,
    Candidate,
    Declaration,
    FirewallBreach,
    IntakeDesk,
    check_generation_firewall,
    quarantine_scope,
)
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, State

TS = "2026-08-05T00:00:00Z"
TEXT = " ".join(["word"] * 100)


def process_item():
    return EvidenceItem(
        kind=EvidenceKind.PROCESS_CAPTURE.value, path="session.log",
        checksum="sha256:" + "0" * 64, recorded_at=TS,
        attributes={"logging_started_before_writing": True,
                    "complete_session": True,
                    "generative_tools_attested_absent": True})


def generation_item():
    return EvidenceItem(
        kind=EvidenceKind.GENERATION_SESSION.value, path="session.json",
        checksum="sha256:" + "1" * 64, recorded_at=TS,
        attributes={"model_family": "G1", "model_version": "v", "provider": "p",
                    "prompt": "…", "decoding": {}, "request_date": "2026-07-01",
                    "raw_response": "…"})


def human_metadata(**over):
    md = {
        "id": "H-01-B100-0001", "schema_version": "1", "corpus_version": "1.0.0",
        "split": "dev", "text": TEXT, "category": "H-01", "track": "H",
        "domain": "casual", "format": "prose", "language": "en-native",
        "length_words": 100, "length_bucket": "B100", "label": "HUMAN",
        "ai_token_share": 0.0, "span_map": None, "source_type": "commissioned_T1",
        "provenance_tier": "T1", "provenance_ref": "provenance/h01/0001",
        "generator": None, "transforms": [], "topic_group_id": None,
        "difficulty": "D3", "rationale": "Commissioned under logging.",
        "target_weakness": "informal register baseline",
        "expected_confusions": "DF2", "noisy_label": False,
        "license": "internal", "pii_status": "clean", "created": "2026-08-05",
        "notes": "",
    }
    md.update(over)
    return md


def candidate(label="HUMAN", model_involved=False, items=None,
              metadata=None, identifier="H-01-B100-0001", tier="T1",
              contributor="alice", text=TEXT) -> Candidate:
    return Candidate(
        identifier=identifier, text=text,
        metadata=metadata if metadata is not None else human_metadata(),
        evidence=EvidencePackage(
            sample_id=identifier, tier=tier,
            items=items if items is not None else [process_item()]),
        declaration=Declaration(contributor=contributor,
                                model_involved=model_involved),
        target_label=label)


def desk(tmp_path):
    return IntakeDesk(
        IdentifierRegistry(path=tmp_path / "ids.jsonl"),
        DecisionLedger(path=tmp_path / "decisions.jsonl"))


def codes(report):
    return {f.code for f in report.findings}


# =====================================================================
# The Generation Firewall (P2, X-1)
# =====================================================================


def test_clean_human_candidate_passes_the_firewall():
    assert check_generation_firewall(candidate()).ok


def test_declared_model_involvement_blocks_the_human_class():
    """P2: no pipeline, review, approval, or delay launders model text into
    the HUMAN class."""
    report = check_generation_firewall(candidate(model_involved=True))
    assert "FIREWALL_DECLARED_MODEL_INVOLVEMENT" in codes(report)


def test_generation_evidence_blocks_the_human_class():
    """Evidence outranks the declaration: a generation session is model
    involvement whatever the contributor wrote."""
    report = check_generation_firewall(
        candidate(items=[process_item(), generation_item()]))
    assert "FIREWALL_EVIDENCE_SHOWS_GENERATION" in codes(report)


def test_undeclared_involvement_is_flagged_separately():
    """§11.6: undeclared involvement is the one act that attacks P2 directly."""
    report = check_generation_firewall(
        candidate(model_involved=False, items=[generation_item()]))
    assert "UNDECLARED_MODEL_INVOLVEMENT" in codes(report)


def test_declared_involvement_is_not_double_penalised():
    """Declaring honestly still blocks HUMAN, but is not also an §11.6
    violation — the contributor did their duty."""
    report = check_generation_firewall(
        candidate(model_involved=True, items=[generation_item()]))
    assert "UNDECLARED_MODEL_INVOLVEMENT" not in codes(report)
    assert not report.ok


def test_generator_record_on_a_human_candidate_blocks_it():
    report = check_generation_firewall(
        candidate(metadata=human_metadata(generator={"family": "G1"})))
    assert "FIREWALL_GENERATOR_RECORD_ON_HUMAN" in codes(report)


def test_firewall_guards_only_the_human_class():
    """An AI candidate with generation evidence is correct, not a breach."""
    md = human_metadata(id="A-01-B100-0001", category="A-01", track="A",
                        label="AI", ai_token_share=1.0)
    report = check_generation_firewall(
        candidate(label="AI", identifier="A-01-B100-0001", metadata=md,
                  items=[generation_item()], model_involved=True))
    assert report.ok


def test_protected_label_is_human_only():
    assert PROTECTED_LABEL == "HUMAN"


def test_firewall_never_inspects_the_text():
    """P3: judging whether prose 'looks generated' is inadmissible. Two
    candidates with wildly different text and identical records must get
    identical firewall verdicts."""
    a = candidate(text="the bus was late again and I waited " * 5)
    b = candidate(text="It is important to note that this underscores " * 5)
    assert check_generation_firewall(a).ok == check_generation_firewall(b).ok


# =====================================================================
# Intake orchestration (CAS §2 Stages 1-4)
# =====================================================================


def test_successful_intake_reaches_validated(tmp_path):
    d = desk(tmp_path)
    result = d.submit(candidate(), "contributor", TS)
    assert result.ok
    assert result.state is State.VALIDATED
    assert d.registry.state_of("H-01-B100-0001") is State.VALIDATED


def test_intake_freezes_the_text(tmp_path):
    d = desk(tmp_path)
    result = d.submit(candidate(), "contributor", TS)
    record = d.registry.get("H-01-B100-0001")
    assert record.is_frozen
    assert record.verify_text(TEXT)
    assert result.checksum == record.checksum


def test_firewall_breach_rejects_terminally(tmp_path):
    """X-1: the rejection is automatic and not curable by re-review."""
    d = desk(tmp_path)
    result = d.submit(candidate(model_involved=True), "contributor", TS)
    assert not result.ok
    assert result.firewall_triggered
    assert d.registry.state_of("H-01-B100-0001") is State.REJECTED


def test_rejected_identifier_cannot_be_resubmitted(tmp_path):
    """§9.5 + §6.4: rejection is terminal for that identifier."""
    from ai_text_eval.gauntlet.lifecycle import LifecycleError
    d = desk(tmp_path)
    d.submit(candidate(model_involved=True), "contributor", TS)
    with pytest.raises(LifecycleError):
        d.registry.open_idea("H-01-B100-0001", "contributor", TS)


def test_firewall_runs_before_evidence_and_metadata_checks(tmp_path):
    """A breached candidate must never reach a VALIDATED-looking state where a
    later approval could appear to bless it."""
    d = desk(tmp_path)
    bad = candidate(model_involved=True,
                    metadata=human_metadata(rationale=""))  # also invalid
    result = d.submit(bad, "contributor", TS)
    assert result.firewall_triggered
    assert d.registry.state_of("H-01-B100-0001") is State.REJECTED
    # The firewall verdict is present; downstream validation did not run.
    assert "FIREWALL_DECLARED_MODEL_INVOLVEMENT" in codes(result.report)
    assert "EMPTY_REQUIRED_TEXT" not in codes(result.report)


def test_strict_firewall_raises(tmp_path):
    d = desk(tmp_path)
    with pytest.raises(FirewallBreach):
        d.submit(candidate(model_involved=True), "contributor", TS,
                 strict_firewall=True)


def test_incomplete_evidence_rejects(tmp_path):
    """§3.1 item 2: candidates without complete evidence do not proceed."""
    d = desk(tmp_path)
    result = d.submit(candidate(items=[]), "contributor", TS)
    assert not result.ok
    assert "EVIDENCE_PACKAGE_EMPTY" in codes(result.report)
    assert d.registry.state_of("H-01-B100-0001") is State.REJECTED


def test_tier_overclaim_rejects(tmp_path):
    d = desk(tmp_path)
    attestation = EvidenceItem(
        kind=EvidenceKind.ATTESTATION.value, path="a.txt",
        checksum="sha256:" + "2" * 64,
        attributes={"author_identified": "A", "signature": "s",
                    "contemporaneous": True,
                    "spot_verification_acknowledged": True,
                    "tools_described": "editor"})
    result = d.submit(candidate(items=[attestation], tier="T1"),
                      "contributor", TS)
    assert "TIER_OVERCLAIM" in codes(result.report)
    assert not result.ok


def test_invalid_metadata_rejects(tmp_path):
    d = desk(tmp_path)
    result = d.submit(candidate(metadata=human_metadata(length_words=999)),
                      "contributor", TS)
    assert not result.ok
    assert "LENGTH_MISMATCH" in codes(result.report)


def test_intake_records_a_ledger_decision(tmp_path):
    d = desk(tmp_path)
    d.submit(candidate(), "contributor", TS)
    events = d.ledger.for_sample("H-01-B100-0001")
    assert any(e["action"] == "create_sample" for e in events)


def test_rejection_is_recorded_in_the_ledger(tmp_path):
    """P5: rejection history is how the project learns and how repeat
    submission of bad material is detected."""
    d = desk(tmp_path)
    d.submit(candidate(model_involved=True), "contributor", TS)
    events = d.ledger.for_sample("H-01-B100-0001")
    assert any("rejected at intake" in (e.get("reason") or "") for e in events)
    assert d.registry.get("H-01-B100-0001").terminal_reason == "X-1 generation firewall"


# =====================================================================
# Quarantine scope (§11.6)
# =====================================================================


def test_quarantine_scope_lists_a_contributors_material(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    for sample in ("H-01-B100-0001", "H-01-B100-0002"):
        ledger.record(Decision(action="create_sample", actor_person="alice",
                               actor_role="contributor", timestamp=TS,
                               sample=sample))
    ledger.record(Decision(action="create_sample", actor_person="bob",
                           actor_role="contributor", timestamp=TS,
                           sample="H-02-B100-0001"))
    assert quarantine_scope(ledger, "alice") == ["H-01-B100-0001", "H-01-B100-0002"]
    assert quarantine_scope(ledger, "bob") == ["H-02-B100-0001"]


def test_quarantine_scope_is_empty_for_an_unknown_contributor(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    assert quarantine_scope(ledger, "nobody") == []


def test_declaration_serializes():
    dec = Declaration(contributor="alice", model_involved=True,
                      detail="grammar suggestions", tools_used=["some-editor"])
    assert dec.to_dict()["model_involved"] is True
    assert dec.to_dict()["tools_used"] == ["some-editor"]
