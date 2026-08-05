"""R-11 acceptance gate tests (CAS §12, §13).

§12 is conjunctive and says so: "there is no compensating excellence." The
tests that matter are the ones proving an unestablished criterion blocks
exactly like a failed one — because every artifact in this system arrives
late, and a gate that treated absence as consent would certify its own
ignorance.
"""

import pytest

from ai_text_eval.gauntlet.acceptance import (
    ACCEPTANCE_CRITERIA,
    ALL_CRITERIA,
    BY_CODE,
    REJECTION_CRITERIA,
    AcceptanceDesk,
    Confirmation,
    ConfirmationRegister,
    Dossier,
    Mechanization,
    Status,
    evaluate,
)
from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State

TS = "2026-08-05T00:00:00Z"
SAMPLE = "H-01-B100-0001"

CONFIRMED_CODES = ("A-8", "A-9", "A-10", "A-12", "A-13",
                   "X-7", "X-8", "X-9", "X-10", "X-11", "X-12")


class FakeVerdict:
    def __init__(self, value):
        self.value = value


class FakeScan:
    def __init__(self, value="clean"):
        self.verdict = FakeVerdict(value)


class FakeScreen:
    def __init__(self, report=None):
        self.report = report or Report()


class FakeAgreement:
    def __init__(self, passes=True):
        self.passes = passes


class FakeRound:
    def __init__(self, complete=True, outcome="accept", reviews=()):
        self.complete = complete
        self._outcome = outcome
        self.reviews = list(reviews)

    def outcome(self):
        return self._outcome


def clean_metadata(**over):
    md = {"pii_status": "clean", "license": "internal",
          "target_weakness": "lexical feature leakage"}
    md.update(over)
    return md


def clean_dossier(**over):
    base = dict(
        sample=SAMPLE, metadata=clean_metadata(), validation=Report(),
        evidence=Report(), firewall=Report(), derivation=Report(),
        duplicates=FakeScreen(), decontamination=FakeScan("clean"),
        review_round=FakeRound(), agreement={"difficulty": FakeAgreement()},
        declarations=[{"contributor": "alice", "model_involved": False}],
        text_verified=True, ledger=None,
        failure_mode_registry={"lexical feature leakage": "DF2"})
    base.update(over)
    return Dossier(**base)


def full_register(exclude=(), holds=True):
    """Confirmations for every judgment criterion, minus any excluded."""
    register = ConfirmationRegister()
    for code in CONFIRMED_CODES:
        if code in exclude:
            continue
        register.record(Confirmation(code, SAMPLE, "maria", "maintainer", TS,
                                     basis=f"checked {code} against the sample",
                                     holds=holds))
    return register


# =====================================================================
# The criterion registry
# =====================================================================


def test_all_thirteen_acceptance_criteria_are_present():
    assert [c.code for c in ACCEPTANCE_CRITERIA] == [f"A-{i}" for i in range(1, 14)]


def test_all_twelve_rejection_criteria_are_present():
    assert [c.code for c in REJECTION_CRITERIA] == [f"X-{i}" for i in range(1, 13)]


def test_every_criterion_declares_how_far_code_can_decide_it():
    assert all(isinstance(c.mechanization, Mechanization) for c in ALL_CRITERIA)


def test_judgment_criteria_carry_a_note_explaining_the_boundary():
    for code in ("A-9", "A-13", "X-7"):
        assert BY_CODE[code].note or BY_CODE[code].mechanization is Mechanization.CONFIRMATION


# =====================================================================
# Absence is not consent — the load-bearing property
# =====================================================================


def test_an_empty_dossier_confirms_nothing_and_accepts_nothing():
    """The single most important assertion in this module."""
    result = evaluate(Dossier(sample=SAMPLE))
    assert not result.accepted
    assert result.unconfirmed
    assert all(r.status is not Status.PASS for r in result.results)


def test_unconfirmed_is_reported_separately_from_failed():
    """"Nobody established this" is a different sentence from "this is false",
    and a release manager needs to know which one they are reading."""
    result = evaluate(Dossier(sample=SAMPLE))
    assert result.unconfirmed
    assert result.failed == []


def test_an_unconfirmed_criterion_blocks_exactly_like_a_failed_one():
    result = evaluate(clean_dossier(), full_register(exclude=("A-13",)))
    assert not result.accepted
    assert "A-13" in [r.code for r in result.unconfirmed]


def test_a_missing_duplicate_screen_is_unconfirmed_not_clean():
    result = evaluate(clean_dossier(duplicates=None), full_register())
    assert result.by_code()["A-6"].status is Status.UNCONFIRMED


def test_a_missing_decontamination_scan_is_unconfirmed_not_clean():
    result = evaluate(clean_dossier(decontamination=None), full_register())
    assert result.by_code()["A-7"].status is Status.UNCONFIRMED


def test_an_incomplete_decontamination_scan_is_unconfirmed_not_passed():
    """R-09's rule carried through to the gate: incomplete is unknown."""
    result = evaluate(clean_dossier(decontamination=FakeScan("incomplete")),
                      full_register())
    entry = result.by_code()["A-7"]
    assert entry.status is Status.UNCONFIRMED
    assert "unknown rather than established" in entry.message


def test_a_missing_firewall_run_is_unconfirmed_not_a_pass():
    result = evaluate(clean_dossier(firewall=None), full_register())
    assert result.by_code()["X-1"].status is Status.UNCONFIRMED


def test_unverified_frozen_text_is_unconfirmed():
    result = evaluate(clean_dossier(text_verified=None), full_register())
    assert result.by_code()["X-5"].status is Status.UNCONFIRMED


# =====================================================================
# Conjunctive: no compensating excellence (§12)
# =====================================================================


def test_a_fully_evidenced_and_confirmed_sample_is_accepted():
    result = evaluate(clean_dossier(), full_register())
    assert result.accepted, [r.code for r in result.results if r.blocks]


def test_one_failure_blocks_regardless_of_everything_else():
    failed = Report()
    failed.error("CAS 4.4", "LENGTH_MISMATCH", "wrong bucket", SAMPLE)
    result = evaluate(clean_dossier(validation=failed), full_register())
    assert not result.accepted
    assert "A-5" in [r.code for r in result.failed]


def test_confirmations_cannot_rescue_a_mechanical_failure():
    """§13 rejections are disqualifying "regardless of other merits"; a human
    asserting otherwise about a computed fact is not evidence."""
    register = full_register()
    register.record(Confirmation("X-8", SAMPLE, "maria", "maintainer", TS,
                                 basis="I looked and it is fine", holds=True))
    result = evaluate(clean_dossier(metadata=clean_metadata(pii_status="dirty")),
                      register)
    assert result.by_code()["X-8"].status is Status.FAIL


def test_a_confirmation_recorded_as_not_holding_fails_the_criterion():
    register = full_register(exclude=("X-10",))
    register.record(Confirmation("X-10", SAMPLE, "maria", "maintainer", TS,
                                 basis="contains harassment of a named person",
                                 holds=False))
    result = evaluate(clean_dossier(), register)
    assert result.by_code()["X-10"].status is Status.FAIL


# =====================================================================
# Confirmations require a basis (§12)
# =====================================================================


def test_a_confirmation_without_a_basis_is_not_recorded():
    register = ConfirmationRegister()
    report = register.record(Confirmation("A-10", SAMPLE, "maria", "maintainer",
                                          TS, basis="   "))
    assert "CONFIRMATION_WITHOUT_BASIS" in {f.code for f in report.errors}
    assert register.get(SAMPLE, "A-10") is None


def test_a_confirmation_for_an_unknown_criterion_is_refused():
    register = ConfirmationRegister()
    report = register.record(Confirmation("A-99", SAMPLE, "maria", "maintainer",
                                          TS, basis="whatever"))
    assert "UNKNOWN_CRITERION" in {f.code for f in report.errors}


def test_a_valid_confirmation_is_retrievable():
    register = ConfirmationRegister()
    register.record(Confirmation("A-10", SAMPLE, "maria", "maintainer", TS,
                                 basis="rationale names the specific construction"))
    assert register.get(SAMPLE, "A-10").confirmed_by == "maria"
    assert len(register.for_sample(SAMPLE)) == 1


# =====================================================================
# Individual criteria that carry real logic
# =====================================================================


def test_a6_fails_on_a_duplicate_screen_error():
    report = Report()
    report.error("CAS 8.2", "NEAR_DUPLICATE_UNDECLARED", "too similar", SAMPLE)
    result = evaluate(clean_dossier(duplicates=FakeScreen(report)), full_register())
    assert result.by_code()["A-6"].status is Status.FAIL
    assert result.by_code()["X-6"].status is Status.FAIL


def test_a7_fails_on_contamination():
    result = evaluate(clean_dossier(decontamination=FakeScan("contaminated")),
                      full_register())
    assert result.by_code()["A-7"].status is Status.FAIL


def test_a11_fails_on_an_incomplete_review_round():
    result = evaluate(clean_dossier(review_round=FakeRound(complete=False)),
                      full_register())
    assert result.by_code()["A-11"].status is Status.FAIL


def test_a11_fails_on_an_unresolved_disagreement():
    result = evaluate(clean_dossier(review_round=FakeRound(outcome=None)),
                      full_register())
    assert "unresolved" in result.by_code()["A-11"].message


def test_a11_is_unconfirmed_when_agreement_was_never_measured():
    result = evaluate(clean_dossier(agreement=None), full_register())
    assert result.by_code()["A-11"].status is Status.UNCONFIRMED


def test_a11_fails_when_agreement_is_below_threshold():
    result = evaluate(clean_dossier(agreement={"difficulty": FakeAgreement(False)}),
                      full_register())
    assert result.by_code()["A-11"].status is Status.FAIL


def test_a9_cannot_pass_mechanically_while_the_rights_vocabulary_is_open():
    """TD-G06: "unknown" is not a value, but the closed set is not enumerated,
    so a recorded licence string cannot be checked against anything."""
    result = evaluate(clean_dossier(), full_register(exclude=("A-9",)))
    entry = result.by_code()["A-9"]
    assert entry.status is Status.UNCONFIRMED
    assert "TD-G06" in entry.message


def test_a9_fails_outright_when_no_licence_is_recorded():
    result = evaluate(clean_dossier(metadata=clean_metadata(license="")),
                      full_register())
    assert result.by_code()["A-9"].status is Status.FAIL


def test_a13_cannot_be_computed_while_the_caps_have_no_numbers():
    result = evaluate(clean_dossier(), full_register(exclude=("A-13",)))
    assert "TD-G04" in result.by_code()["A-13"].message


def test_a10_flags_a_target_weakness_that_maps_to_nothing():
    result = evaluate(
        clean_dossier(metadata=clean_metadata(target_weakness="something new")),
        full_register(exclude=("A-10",)))
    assert result.by_code()["A-10"].status is Status.UNCONFIRMED
    assert "registered proposal" in result.by_code()["A-10"].message


def test_a10_fails_on_an_empty_target_weakness():
    result = evaluate(clean_dossier(metadata=clean_metadata(target_weakness="")),
                      full_register())
    assert result.by_code()["A-10"].status is Status.FAIL


def test_x1_fails_on_a_firewall_error():
    firewall = Report()
    firewall.error("CAS X-1", "FIREWALL_DECLARED_MODEL_INVOLVEMENT", "declared",
                   SAMPLE)
    result = evaluate(clean_dossier(firewall=firewall), full_register())
    assert result.by_code()["X-1"].status is Status.FAIL


def test_x3_fails_when_a_review_recorded_an_appearance_opinion():
    class FakeReview:
        appearance_opinion = "reads like a model"

    result = evaluate(
        clean_dossier(review_round=FakeRound(reviews=[FakeReview()])),
        full_register())
    assert result.by_code()["X-3"].status is Status.FAIL


def test_x3_fails_on_inadmissible_evidence():
    evidence = Report()
    evidence.error("CAS 5.4", "INADMISSIBLE_EVIDENCE", "recollection", SAMPLE)
    result = evaluate(clean_dossier(evidence=evidence), full_register())
    assert result.by_code()["X-3"].status is Status.FAIL


def test_x4_fails_on_a_hand_entered_share():
    derivation = Report()
    derivation.error("CAS 4.2", "SHARE_NOT_DERIVED", "estimated", SAMPLE)
    result = evaluate(clean_dossier(derivation=derivation), full_register())
    assert result.by_code()["X-4"].status is Status.FAIL


def test_x5_fails_when_the_text_changed_after_freeze():
    result = evaluate(clean_dossier(text_verified=False), full_register())
    assert result.by_code()["X-5"].status is Status.FAIL


def test_x12_reports_a_recorded_role_conflict(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    ledger.record(Decision(action="create_sample", actor_person="alice",
                           actor_role="contributor", timestamp=TS, sample=SAMPLE))
    ledger.record(Decision(action="review_content", actor_person="alice",
                           actor_role="reviewer", timestamp=TS, sample=SAMPLE))
    result = evaluate(clean_dossier(ledger=ledger), full_register())
    assert result.by_code()["X-12"].status is Status.FAIL


# =====================================================================
# The desk (Stage 7)
# =====================================================================


def accepted_desk(tmp_path):
    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    for state in (State.VALIDATED, State.SCREENED, State.REVIEWED):
        if state is State.VALIDATED:
            registry.open_idea(SAMPLE, "contributor", TS)
            registry.freeze(SAMPLE, "frozen text", "contributor", TS)
        registry.transition(SAMPLE, state, "system", TS, reason="ok")
    return AcceptanceDesk(registry, ledger, full_register())


def test_acceptance_requires_the_reviewed_state(tmp_path):
    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    registry.open_idea(SAMPLE, "contributor", TS)
    registry.freeze(SAMPLE, "frozen text", "contributor", TS)
    desk = AcceptanceDesk(registry, ledger)
    with pytest.raises(LifecycleError, match="Stage 7"):
        desk.decide(clean_dossier(), maintainer="maria", timestamp=TS)


def test_a_passing_gate_transitions_to_accepted(tmp_path):
    desk = accepted_desk(tmp_path)
    result = desk.decide(clean_dossier(), maintainer="maria", timestamp=TS)
    assert result.accepted
    assert desk.registry.state_of(SAMPLE) is State.ACCEPTED


def test_acceptance_is_recorded_in_the_ledger(tmp_path):
    desk = accepted_desk(tmp_path)
    desk.decide(clean_dossier(), maintainer="maria", timestamp=TS)
    actions = [e["action"] for e in desk.ledger.for_sample(SAMPLE)]
    assert "confirm_acceptance" in actions


def test_an_unconfirmed_criterion_leaves_the_sample_reviewed(tmp_path):
    """Not terminal: §6.5 lets a metadata revision cure several §12 failures,
    and rejection would burn the identifier forever (§9.5)."""
    desk = AcceptanceDesk(
        IdentifierRegistry(path=tmp_path / "ids.jsonl"),
        DecisionLedger(path=tmp_path / "decisions.jsonl"),
        full_register(exclude=("A-13",)))
    for state in (State.VALIDATED, State.SCREENED, State.REVIEWED):
        if state is State.VALIDATED:
            desk.registry.open_idea(SAMPLE, "contributor", TS)
            desk.registry.freeze(SAMPLE, "frozen text", "contributor", TS)
        desk.registry.transition(SAMPLE, state, "system", TS, reason="ok")
    result = desk.decide(clean_dossier(), maintainer="maria", timestamp=TS)
    assert not result.accepted
    assert desk.registry.state_of(SAMPLE) is State.REVIEWED


def test_an_established_rejection_criterion_is_terminal(tmp_path):
    desk = accepted_desk(tmp_path)
    result = desk.decide(clean_dossier(text_verified=False),
                         maintainer="maria", timestamp=TS)
    assert "X-5" in [r.code for r in result.failed]
    assert desk.registry.state_of(SAMPLE) is State.REJECTED


def test_recording_a_confirmation_lands_in_the_ledger(tmp_path):
    desk = accepted_desk(tmp_path)
    desk.confirm(Confirmation("A-10", SAMPLE, "maria", "maintainer", TS,
                              basis="the rationale names the exact construction"))
    entry = [e for e in desk.ledger.for_sample(SAMPLE)
             if "A-10" in (e.get("reason") or "")][0]
    assert "names the exact construction" in entry["reason"]


def test_a_baseless_confirmation_is_not_written_to_the_ledger(tmp_path):
    desk = accepted_desk(tmp_path)
    desk.confirm(Confirmation("A-10", SAMPLE, "maria", "maintainer", TS, basis=""))
    assert not [e for e in desk.ledger.for_sample(SAMPLE)
                if "A-10" in (e.get("reason") or "")]


def test_the_result_serializes_for_the_release_record():
    result = evaluate(clean_dossier(), full_register(exclude=("A-13",)))
    payload = result.to_dict()
    assert payload["accepted"] is False
    assert "A-13" in payload["unconfirmed"]
    assert len(payload["criteria"]) == len(ALL_CRITERIA)
