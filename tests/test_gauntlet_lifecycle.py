"""R-02 identifier registry and lifecycle tests (CAS §2, §9.5, X-5)."""

import json

import pytest

from ai_text_eval.gauntlet.lifecycle import (
    FREEZE_TRANSITION,
    LEGAL_TRANSITIONS,
    IdentifierRegistry,
    LifecycleError,
    State,
    text_checksum,
)

TS = "2026-08-05T00:00:00Z"


def reg(tmp_path) -> IdentifierRegistry:
    return IdentifierRegistry(path=tmp_path / "identifiers.jsonl")


def carry_to_candidate(r, ident="H-01-B100-0001", text="hello world"):
    r.open_idea(ident, "contributor", TS)
    r.freeze(ident, text, "contributor", TS)
    return ident


# -- state machine -------------------------------------------------------


def test_full_happy_path(tmp_path):
    r = reg(tmp_path)
    i = carry_to_candidate(r)
    for state in (State.VALIDATED, State.SCREENED, State.REVIEWED,
                  State.ACCEPTED, State.ASSIGNED, State.RELEASED):
        r.transition(i, state, "maintainer", TS)
    assert r.state_of(i) is State.RELEASED


def test_no_stage_may_be_skipped(tmp_path):
    """CAS §2: skipping VALIDATED..REVIEWED straight to ACCEPTED is illegal."""
    r = reg(tmp_path)
    i = carry_to_candidate(r)
    with pytest.raises(LifecycleError, match="no stage may be skipped"):
        r.transition(i, State.ACCEPTED, "maintainer", TS)


def test_cannot_transition_out_of_a_terminal_state(tmp_path):
    r = reg(tmp_path)
    i = carry_to_candidate(r)
    r.reject(i, "reviewer", TS, reason="failed X-2")
    with pytest.raises(LifecycleError, match="illegal transition"):
        r.transition(i, State.VALIDATED, "maintainer", TS)


def test_rejection_is_reachable_from_every_pre_acceptance_state(tmp_path):
    for target in (State.IDEA, State.CANDIDATE, State.VALIDATED,
                   State.SCREENED, State.REVIEWED):
        assert State.REJECTED in LEGAL_TRANSITIONS[target]


def test_accepted_cannot_be_rejected(tmp_path):
    """Post-acceptance the exits are deprecation and redaction, not rejection."""
    r = reg(tmp_path)
    i = carry_to_candidate(r)
    for s in (State.VALIDATED, State.SCREENED, State.REVIEWED, State.ACCEPTED):
        r.transition(i, s, "maintainer", TS)
    with pytest.raises(LifecycleError):
        r.reject(i, "maintainer", TS, reason="too late")


def test_released_can_be_deprecated_and_redacted(tmp_path):
    assert State.DEPRECATED in LEGAL_TRANSITIONS[State.RELEASED]
    assert State.REDACTED in LEGAL_TRANSITIONS[State.RELEASED]


def test_deprecated_may_still_be_redacted(tmp_path):
    """A deprecated sample stays in distribution and can need PII redaction."""
    assert State.REDACTED in LEGAL_TRANSITIONS[State.DEPRECATED]


def test_freeze_transition_constant_matches_the_table():
    frm, to = FREEZE_TRANSITION
    assert to in LEGAL_TRANSITIONS[frm]


# -- identifier uniqueness (§9.5) ----------------------------------------


def test_identifier_cannot_be_registered_twice(tmp_path):
    r = reg(tmp_path)
    r.open_idea("H-01-B100-0001", "contributor", TS)
    with pytest.raises(LifecycleError, match="§9.5"):
        r.open_idea("H-01-B100-0001", "contributor", TS)


def test_rejected_identifier_is_never_reused(tmp_path):
    """§9.5: unique across all history including rejections."""
    r = reg(tmp_path)
    i = carry_to_candidate(r)
    r.reject(i, "reviewer", TS, reason="X-1")
    with pytest.raises(LifecycleError, match="§9.5"):
        r.open_idea(i, "contributor", TS)


# -- freeze and X-5 ------------------------------------------------------


def test_freeze_records_the_checksum(tmp_path):
    r = reg(tmp_path)
    i = carry_to_candidate(r, text="the exact bytes")
    assert r.get(i).is_frozen
    assert r.get(i).checksum == text_checksum("the exact bytes")


def test_frozen_text_verifies(tmp_path):
    r = reg(tmp_path)
    i = carry_to_candidate(r, text="original")
    assert r.get(i).verify_text("original") is True
    assert r.get(i).verify_text("tampered") is False


def test_verifying_unfrozen_identifier_raises(tmp_path):
    r = reg(tmp_path)
    r.open_idea("H-01-B100-0001", "contributor", TS)
    with pytest.raises(LifecycleError, match="not frozen"):
        r.get("H-01-B100-0001").verify_text("anything")


def test_supersede_rejects_old_and_freezes_new(tmp_path):
    r = reg(tmp_path)
    old = carry_to_candidate(r, "H-01-B100-0001", text="v1 text")
    r.supersede(old, "H-01-B100-0002", "v2 text", "contributor", TS)
    assert r.state_of(old) is State.REJECTED
    assert r.get(old).terminal_reason == "superseded"
    new = r.get("H-01-B100-0002")
    assert new.state is State.CANDIDATE
    assert new.verify_text("v2 text")
    assert new.lineage == [{"relation": "supersedes", "target": old}]


# -- persistence and replay ----------------------------------------------


def test_ledger_is_append_only_and_replays(tmp_path):
    path = tmp_path / "identifiers.jsonl"
    r1 = IdentifierRegistry(path=path)
    i = carry_to_candidate(r1)
    r1.transition(i, State.VALIDATED, "maintainer", TS)

    # A fresh registry over the same file reconstructs identical state.
    r2 = IdentifierRegistry(path=path)
    assert r2.state_of(i) is State.VALIDATED
    assert r2.get(i).checksum == r1.get(i).checksum


def test_replay_preserves_full_history(tmp_path):
    path = tmp_path / "identifiers.jsonl"
    r = IdentifierRegistry(path=path)
    i = carry_to_candidate(r)
    r.transition(i, State.VALIDATED, "maintainer", TS)
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert [e["event"] for e in lines] == ["open", "transition", "transition"]
    assert lines[0]["to_state"] == "idea"


def test_events_are_never_rewritten(tmp_path):
    """P5: the log only grows; a transition appends, it does not edit."""
    path = tmp_path / "identifiers.jsonl"
    r = IdentifierRegistry(path=path)
    i = carry_to_candidate(r)
    before = path.read_text()
    r.transition(i, State.VALIDATED, "maintainer", TS)
    after = path.read_text()
    assert after.startswith(before)  # strictly appended
    assert len(after) > len(before)


def test_corrupt_ledger_line_is_reported(tmp_path):
    path = tmp_path / "identifiers.jsonl"
    path.write_text('{"event":"open","identifier":"H-01-B100-0001","to_state":"idea"}\n'
                    'not json\n', encoding="utf-8")
    with pytest.raises(LifecycleError, match="corrupt ledger line"):
        IdentifierRegistry(path=path)


# -- queries -------------------------------------------------------------


def test_query_helpers(tmp_path):
    r = reg(tmp_path)
    a = carry_to_candidate(r, "H-01-B100-0001")
    r.open_idea("H-02-B100-0001", "contributor", TS)
    assert r.exists(a)
    assert not r.exists("nope")
    assert set(r.all_identifiers()) == {"H-01-B100-0001", "H-02-B100-0001"}
    assert r.in_state(State.CANDIDATE) == ["H-01-B100-0001"]
    assert r.in_state(State.IDEA) == ["H-02-B100-0001"]


def test_transition_on_unknown_identifier_raises(tmp_path):
    r = reg(tmp_path)
    with pytest.raises(LifecycleError, match="unknown identifier"):
        r.transition("H-99-B100-0001", State.VALIDATED, "maintainer", TS)
