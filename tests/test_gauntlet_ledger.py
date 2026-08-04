"""R-03 decision ledger tests (CAS §14.2, §11.7, §6.6)."""

import json

import pytest

from ai_text_eval.gauntlet.ledger import (
    FORBIDDEN_ROLE_PAIRS,
    PRIVILEGED_ACTIONS,
    Decision,
    DecisionLedger,
    LedgerConflictError,
)

TS = "2026-08-05T00:00:00Z"


def ledger(tmp_path) -> DecisionLedger:
    return DecisionLedger(path=tmp_path / "decisions.jsonl")


def act(action, person, role, sample="H-01-B100-0001", **kw):
    return Decision(action=action, actor_person=person, actor_role=role,
                    timestamp=TS, sample=sample, **kw)


# -- recording -----------------------------------------------------------


def test_records_a_privileged_action(tmp_path):
    lg = ledger(tmp_path)
    report = lg.record(act("create_sample", "alice", "contributor"))
    assert report.ok
    assert len(lg.for_sample("H-01-B100-0001")) == 1


def test_unknown_action_is_rejected(tmp_path):
    lg = ledger(tmp_path)
    with pytest.raises(ValueError, match="closed vocabulary"):
        lg.record(act("vibe_check", "alice", "contributor"))


def test_action_vocabulary_covers_the_authority_matrix():
    for action in ("create_sample", "confirm_acceptance", "change_label",
                   "assign_split", "create_release", "deprecate", "redact"):
        assert action in PRIVILEGED_ACTIONS


def test_evidence_refs_and_reason_are_retained(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("change_label", "carol", "release_manager",
                  reason="upheld provenance challenge",
                  evidence_refs=["provenance/x/challenge-001"]))
    ev = lg.for_sample("H-01-B100-0001")[0]
    assert ev["reason"] == "upheld provenance challenge"
    assert ev["evidence_refs"] == ["provenance/x/challenge-001"]


# -- conflict detection (§11.7, §6.6) ------------------------------------


def test_producer_cannot_review_their_own_sample(tmp_path):
    """§6.6: the producer of a sample MUST NOT review it."""
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    report = lg.record(act("review_provenance", "alice", "reviewer"))
    assert "ROLE_CONFLICT" in {f.code for f in report.errors}


def test_reviewer_cannot_adjudicate_the_same_sample(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("review_content", "bob", "reviewer"))
    report = lg.record(act("adjudicate", "bob", "adjudicator"))
    assert not report.ok


def test_producer_cannot_assign_splits_for_their_material(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    report = lg.record(act("assign_split", "alice", "release_manager"))
    assert "ROLE_CONFLICT" in {f.code for f in report.errors}


def test_generation_operator_counts_as_producer(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("generation_operate", "dave", "generation_operator"))
    report = lg.record(act("review_content", "dave", "reviewer"))
    assert not report.ok


def test_different_people_in_the_two_roles_is_fine(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    report = lg.record(act("review_provenance", "bob", "reviewer"))
    assert report.ok


def test_same_person_same_role_is_not_a_conflict(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("review_provenance", "bob", "reviewer"))
    report = lg.record(act("review_content", "bob", "reviewer"))
    assert report.ok


def test_conflict_only_within_the_same_sample(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor", sample="H-01-B100-0001"))
    report = lg.record(act("review_provenance", "alice", "reviewer",
                           sample="H-02-B100-0002"))
    assert report.ok


def test_conflicting_action_is_still_recorded(tmp_path):
    """§11.7: every same-person dual-role action is noted; the ledger records
    faithfully even while flagging the conflict."""
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    lg.record(act("review_provenance", "alice", "reviewer"))
    assert len(lg.for_sample("H-01-B100-0001")) == 2


def test_strict_mode_records_then_raises(tmp_path):
    path = tmp_path / "decisions.jsonl"
    lg = DecisionLedger(path=path)
    lg.record(act("create_sample", "alice", "contributor"))
    with pytest.raises(LedgerConflictError):
        lg.record(act("review_provenance", "alice", "reviewer"), strict=True)
    # The action must still be on disk despite the raise (P5).
    lg2 = DecisionLedger(path=path)
    assert len(lg2.for_sample("H-01-B100-0001")) == 2


def test_forbidden_pairs_are_all_symmetric_frozensets():
    for pair in FORBIDDEN_ROLE_PAIRS:
        assert isinstance(pair, frozenset) and len(pair) == 2


# -- audit ---------------------------------------------------------------


def test_audit_finds_conflicts_recorded_in_non_strict_mode(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    lg.record(act("adjudicate", "alice", "adjudicator"))
    audit = lg.audit_conflicts()
    assert not audit.ok
    assert "ROLE_CONFLICT" in {f.code for f in audit.errors}


def test_audit_is_clean_when_roles_are_separated(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor"))
    lg.record(act("review_provenance", "bob", "reviewer"))
    lg.record(act("review_content", "carol", "reviewer"))
    assert lg.audit_conflicts().ok


# -- persistence ---------------------------------------------------------


def test_ledger_is_append_only_and_replays(tmp_path):
    path = tmp_path / "decisions.jsonl"
    lg = DecisionLedger(path=path)
    lg.record(act("create_sample", "alice", "contributor"))
    before = path.read_text()
    lg.record(act("confirm_acceptance", "mel", "maintainer"))
    after = path.read_text()
    assert after.startswith(before)

    lg2 = DecisionLedger(path=path)
    assert len(lg2.all_events()) == 2


def test_by_actor_query(tmp_path):
    lg = ledger(tmp_path)
    lg.record(act("create_sample", "alice", "contributor", sample="H-01-B100-0001"))
    lg.record(act("create_sample", "alice", "contributor", sample="H-01-B100-0002"))
    lg.record(act("confirm_acceptance", "mel", "maintainer", sample="H-01-B100-0001"))
    assert len(lg.by_actor("alice")) == 2


def test_corrupt_decision_line_is_reported(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text('{"action":"create_sample"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt decision line"):
        DecisionLedger(path=path)


def test_non_sample_scoped_actions_do_not_conflict(tmp_path):
    """A release-wide action has no sample and cannot form a same-sample pair."""
    lg = ledger(tmp_path)
    lg.record(Decision(action="create_release", actor_person="rm1",
                       actor_role="release_manager", timestamp=TS,
                       sample=None, scope="release-1.0.0"))
    report = lg.record(Decision(action="create_release", actor_person="rm1",
                                actor_role="release_manager", timestamp=TS,
                                sample=None, scope="release-1.0.0"))
    assert report.ok
