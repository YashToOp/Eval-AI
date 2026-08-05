"""LWE M2: session lifecycle tests (CAS §3.2, §5.2, P5).

The state machine exists for one reason: to force consent and the environment
attestation into event 0, so that CAS §3.2's "the logging arrangement MUST be
in place before writing begins" is structural rather than promised. The tests
that matter are the ones proving an attestation cannot arrive late.
"""

import pytest

from ai_text_eval.lwe.journal import EventKind, Journal
from ai_text_eval.lwe.session import (
    Attestations,
    SessionError,
    SessionIntent,
    SessionState,
    WritingSession,
    create_session,
    load_session,
)

WALL = "2026-08-05T10:00:00Z"


def intent(**over):
    base = dict(contributor="wren", prompt="Describe a recent commute.",
                intended_category="H-01", intended_length_bucket="B250",
                language="en-native")
    base.update(over)
    return SessionIntent(**base)


def attestations(**over):
    base = dict(consent_to_logging=True,
                environment_free_of_generative_tools=True,
                spot_verification_acknowledged=True,
                tools_used=["gauntlet-write"])
    base.update(over)
    return Attestations(**base)


def new_session(tmp_path, name="s1", **over):
    return create_session(tmp_path / name, session_id=name,
                          clock=lambda: 0, wall=lambda: WALL, **over)


def opened_session(tmp_path, name="s1", att=None):
    s = new_session(tmp_path, name)
    s.open(intent(), att or attestations())
    return s


def written(tmp_path, text="the bus was late again", name="s1", att=None):
    s = opened_session(tmp_path, name, att)
    s.insert(0, text)
    return s


# =====================================================================
# Creation
# =====================================================================


def test_a_new_session_starts_in_created(tmp_path):
    assert new_session(tmp_path).state is SessionState.CREATED


def test_a_created_session_accepts_no_content(tmp_path):
    """The whole reason CREATED exists: content before attestation would be
    content the attestation does not cover."""
    s = new_session(tmp_path)
    with pytest.raises(SessionError, match="only an open session"):
        s.insert(0, "sneaking in early")


def test_creating_over_an_existing_session_is_refused(tmp_path):
    """Sessions are append-only and never reused (CAS P5)."""
    opened_session(tmp_path)
    with pytest.raises(SessionError, match="never reused"):
        create_session(tmp_path / "s1")


# =====================================================================
# Opening: the attestation lands in event 0
# =====================================================================


def test_opening_writes_the_attestation_as_event_zero(tmp_path):
    """CAS §3.2: the arrangement must precede the writing. Sequence number 0
    is how that is proved rather than asserted."""
    s = opened_session(tmp_path)
    assert s.attestation_event_seq == 0
    opening = s.journal.events[0]
    assert opening.kind == EventKind.SESSION_OPEN.value
    assert opening.payload["attestations"]["environment_free_of_generative_tools"] is True


def test_the_opening_event_records_an_empty_document(tmp_path):
    """The claim "logging preceded writing" is only checkable if the opening
    event says the document was empty at that moment."""
    s = opened_session(tmp_path)
    assert s.journal.events[0].payload["initial_text"] == ""
    s.insert(0, "written after the session opened")
    assert s.close().opened_empty is True


def test_opening_records_the_intent_without_asserting_it(tmp_path):
    """CAS §3.4: the instruction defines the category, and an instruction is
    not mechanically classifiable — so this is a hint for the reviewer."""
    s = opened_session(tmp_path)
    recorded = s.journal.events[0].payload["intent"]
    assert recorded["intended_category"] == "H-01"
    assert recorded["prompt"] == "Describe a recent commute."


def test_a_session_cannot_be_opened_twice(tmp_path):
    s = opened_session(tmp_path)
    with pytest.raises(SessionError, match="only a created session"):
        s.open(intent(), attestations())


def test_logging_without_consent_is_refused(tmp_path):
    """A record of someone who did not agree is not evidence, it is
    surveillance."""
    s = new_session(tmp_path)
    with pytest.raises(SessionError, match="surveillance"):
        s.open(intent(), attestations(consent_to_logging=False))


def test_an_unnamed_contributor_is_refused(tmp_path):
    """T1 and T2 both rest on an identified author (CAS §5.2)."""
    s = new_session(tmp_path)
    with pytest.raises(SessionError, match="named contributor"):
        s.open(intent(contributor="   "), attestations())


def test_declining_the_environment_attestation_still_opens_a_session(tmp_path):
    """"Burden proportional to ambition": declining lowers the tier the
    evidence supports, it does not refuse the contribution."""
    s = opened_session(tmp_path,
                       att=attestations(environment_free_of_generative_tools=False))
    assert s.state is SessionState.OPEN
    assert s.attestations.environment_free_of_generative_tools is False


def test_the_three_attestations_are_recorded_separately(tmp_path):
    """Agreeing to be logged and asserting a tool-free environment are
    different promises; bundling them would obscure which was made."""
    s = opened_session(tmp_path,
                       att=attestations(spot_verification_acknowledged=False))
    recorded = s.to_dict()["attestations"]
    assert recorded["consent_to_logging"] is True
    assert recorded["environment_free_of_generative_tools"] is True
    assert recorded["spot_verification_acknowledged"] is False


def test_a_missing_attestation_defaults_to_no(tmp_path):
    """An unmade promise must be distinguishable from a made one."""
    blank = Attestations(consent_to_logging=True)
    s = opened_session(tmp_path, att=blank)
    assert s.attestations.environment_free_of_generative_tools is False
    assert s.attestations.spot_verification_acknowledged is False


# =====================================================================
# Writing
# =====================================================================


def test_inserting_advances_the_document(tmp_path):
    s = written(tmp_path, "hello")
    s.insert(5, " world")
    assert s.text == "hello world"


def test_deleting_removes_from_the_document(tmp_path):
    s = written(tmp_path, "hello cruel world")
    s.delete(5, 6)
    assert s.text == "hello world"


def test_a_paste_is_recorded_and_never_blocked(tmp_path):
    """Blocking would push a determined contributor to retyping, which the
    tool cannot see at all — trading a recorded fact for an invisible one."""
    s = opened_session(tmp_path)
    s.paste(0, "text from somewhere else")
    assert s.text == "text from somewhere else"
    assert s.facts()["paste_count"] == 1
    assert s.facts()["pasted_chars"] == len("text from somewhere else")


def test_focus_events_are_recorded(tmp_path):
    s = written(tmp_path)
    s.focus_out()
    s.focus_in()
    assert s.facts()["focus_out_count"] == 1
    assert s.text == "the bus was late again"


def test_an_impossible_edit_leaves_the_document_unchanged(tmp_path):
    s = written(tmp_path, "short")
    with pytest.raises(SessionError, match="does not apply"):
        s.delete(0, 999)
    assert s.text == "short"


# =====================================================================
# Closing
# =====================================================================


def test_closing_seals_and_verifies(tmp_path):
    verification = written(tmp_path).close()
    assert verification.verified
    assert verification.problems == []


def test_closing_writes_the_text_file(tmp_path):
    s = written(tmp_path, "the finished sample")
    s.close()
    assert (s.root / "text.txt").read_text() == "the finished sample"


def test_a_closed_session_accepts_no_more_content(tmp_path):
    s = written(tmp_path)
    s.close()
    with pytest.raises(SessionError, match="only an open session"):
        s.insert(0, "afterthought")


def test_closing_twice_is_refused(tmp_path):
    s = written(tmp_path)
    s.close()
    with pytest.raises(SessionError, match="nothing to close"):
        s.close()


def test_closing_an_unopened_session_is_refused(tmp_path):
    with pytest.raises(SessionError, match="nothing to close"):
        new_session(tmp_path).close()


def test_a_session_that_fails_verification_still_closes(tmp_path):
    """The session is over either way; the failure is recorded, not hidden."""
    s = written(tmp_path)
    s.close()
    path = s.root / "journal.jsonl"
    path.write_text(path.read_text().replace("the bus", "a train"))
    assert not load_session(s.root).verify().verified


# =====================================================================
# Abandonment (CAS P5: nothing is deleted)
# =====================================================================


def test_an_abandoned_session_is_retained_on_disk(tmp_path):
    s = written(tmp_path)
    s.abandon("changed my mind")
    assert s.state is SessionState.ABANDONED
    assert (s.root / "journal.jsonl").is_file()


def test_abandonment_records_its_reason_in_the_journal(tmp_path):
    s = written(tmp_path)
    s.abandon("interrupted")
    notes = [e for e in s.journal.events if e.kind == EventKind.NOTE.value]
    assert "interrupted" in notes[-1].payload["text"]


def test_a_created_session_can_be_abandoned_before_it_opens(tmp_path):
    s = new_session(tmp_path)
    s.abandon()
    assert s.state is SessionState.ABANDONED


def test_a_closed_session_cannot_be_abandoned_afterwards(tmp_path):
    s = written(tmp_path)
    s.close()
    with pytest.raises(SessionError, match="cannot be abandoned"):
        s.abandon()


def test_an_abandoned_session_is_not_verified(tmp_path):
    s = written(tmp_path)
    s.abandon()
    assert not s.verify().verified   # never sealed


# =====================================================================
# Crash recovery — the journal is the source of truth
# =====================================================================


def test_a_session_resumes_from_its_journal(tmp_path):
    s = written(tmp_path, "first half ")
    s.insert(len(s.text), "second half")
    resumed = load_session(s.root)
    assert resumed.state is SessionState.OPEN
    assert resumed.text == "first half second half"


def test_resuming_restores_the_intent_and_attestations(tmp_path):
    s = written(tmp_path)
    resumed = load_session(s.root)
    assert resumed.intent.contributor == "wren"
    assert resumed.attestations.environment_free_of_generative_tools is True


def test_resuming_a_closed_session_sees_it_as_closed(tmp_path):
    s = written(tmp_path)
    s.close()
    assert load_session(s.root).state is SessionState.CLOSED


def test_a_stale_manifest_does_not_override_the_journal(tmp_path):
    """A manifest can be one event behind a crash; a flushed journal cannot."""
    s = written(tmp_path, "recorded in the journal")
    s.manifest_path.write_text('{"state": "created", "intent": {}, '
                               '"attestations": {}}')
    resumed = load_session(s.root)
    assert resumed.state is SessionState.OPEN
    assert resumed.text == "recorded in the journal"


def test_loading_a_directory_without_a_session_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SessionError, match="does not contain a session"):
        load_session(tmp_path / "empty")


def test_writing_continues_after_a_resume(tmp_path):
    s = written(tmp_path, "before ")
    resumed = load_session(s.root, clock=lambda: 0, wall=lambda: WALL)
    resumed.insert(len(resumed.text), "after")
    assert resumed.close().verified
    assert resumed.text == "before after"


# =====================================================================
# The manifest
# =====================================================================


def test_the_manifest_reports_verification_facts_and_attestations(tmp_path):
    s = written(tmp_path)
    s.close()
    payload = s.to_dict()
    assert payload["verification"]["verified"] is True
    assert payload["attestations"]["recorded_at_event"] == 0
    assert payload["facts"]["final_words"] == 5
    assert payload["text_sha256"].startswith("sha256:")
    assert payload["journal_sha256"].startswith("sha256:")


def test_the_manifest_survives_a_round_trip(tmp_path):
    s = written(tmp_path)
    s.close()
    assert load_session(s.root).to_dict()["state"] == "closed"


def test_the_manifest_carries_no_judgment_about_the_session(tmp_path):
    """Same rule as the journal's facts: counts and verification, never a
    verdict about whether the writing 'looks' genuine."""
    s = written(tmp_path)
    s.close()
    blob = str(s.to_dict()).lower()
    for word in ("suspicious", "likely human", "authenticity", "confidence",
                 "probability", "risk score"):
        assert word not in blob


# =====================================================================
# Session tokens (local endpoint guard)
# =====================================================================


def test_each_session_gets_a_distinct_token(tmp_path):
    a = new_session(tmp_path, "a")
    b = new_session(tmp_path, "b")
    assert a.token != b.token
    assert len(a.token) >= 20
