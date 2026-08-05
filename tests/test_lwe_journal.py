"""LWE M1: hash-chained journal tests.

The journal is the whole product — CAS §3.2 says a session log can prove what
an environment claim cannot, and these tests are what "prove" means. The
load-bearing ones are the tamper cases: a journal that cannot detect an edited,
truncated, or reordered log is not evidence of anything.
"""

import json

import pytest

from ai_text_eval.lwe.journal import (
    GENESIS,
    IDLE_GAP_MS,
    EventKind,
    Journal,
    JournalError,
    canonical_hash,
    text_checksum,
)


def clock_from(values):
    """A deterministic monotonic clock, so timings are asserted not observed."""
    it = iter(values)
    last = [0]

    def tick():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    return tick


def opened(tmp_path, name="journal.jsonl", clock=None, **payload):
    j = Journal(tmp_path / name, clock=clock or (lambda: 0))
    body = {"initial_text": "", "contributor": "wren"}
    body.update(payload)
    j.append(EventKind.SESSION_OPEN, body, wall="2026-08-05T10:00:00Z")
    return j


def write_and_seal(j, text="hello world"):
    j.append(EventKind.INSERT, {"pos": 0, "text": text})
    j.append(EventKind.SESSION_CLOSE,
             {"text": text, "sha256": text_checksum(text),
              "words": len(text.split())})
    return j


# =====================================================================
# Chain construction
# =====================================================================


def test_the_first_event_links_to_genesis(tmp_path):
    """A journal whose opening event was removed must not look like one that
    never had an opening event."""
    j = opened(tmp_path)
    assert j.events[0].prev == GENESIS


def test_each_event_links_to_its_predecessor(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "a"})
    j.append(EventKind.INSERT, {"pos": 1, "text": "b"})
    events = j.events
    assert events[1].prev == events[0].hash
    assert events[2].prev == events[1].hash


def test_sequence_numbers_are_dense_and_ordered(tmp_path):
    j = opened(tmp_path)
    for i in range(3):
        j.append(EventKind.INSERT, {"pos": i, "text": "x"})
    assert [e.seq for e in j.events] == [0, 1, 2, 3]


def test_the_first_event_must_be_session_open(tmp_path):
    """The consent and environment attestation live in event 0; a journal that
    starts elsewhere cannot carry a pre-writing attestation."""
    j = Journal(tmp_path / "j.jsonl", clock=lambda: 0)
    with pytest.raises(JournalError, match="first event must be session_open"):
        j.append(EventKind.INSERT, {"pos": 0, "text": "hi"})


def test_session_open_appears_only_once(tmp_path):
    j = opened(tmp_path)
    with pytest.raises(JournalError, match="event 0 and appears once"):
        j.append(EventKind.SESSION_OPEN, {"initial_text": ""})


def test_a_sealed_journal_refuses_further_events(tmp_path):
    j = write_and_seal(opened(tmp_path))
    with pytest.raises(JournalError, match="sealed"):
        j.append(EventKind.INSERT, {"pos": 0, "text": "more"})


def test_events_are_durable_before_they_are_acknowledged(tmp_path):
    """A crash must never leave the caller's document ahead of the log."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "written"})
    lines = (tmp_path / "journal.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["payload"]["text"] == "written"


def test_a_journal_reloads_from_disk(tmp_path):
    write_and_seal(opened(tmp_path))
    reloaded = Journal(tmp_path / "journal.jsonl")
    assert len(reloaded.events) == 3
    assert reloaded.verify().verified


# =====================================================================
# Replay
# =====================================================================


def test_replay_reconstructs_an_inserted_document(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "hello"})
    j.append(EventKind.INSERT, {"pos": 5, "text": " world"})
    assert j.replay() == "hello world"


def test_replay_applies_deletes(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "hello cruel world"})
    j.append(EventKind.DELETE, {"pos": 5, "length": 6})
    assert j.replay() == "hello world"


def test_replay_applies_pastes_like_inserts(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.PASTE, {"pos": 0, "text": "pasted text"})
    assert j.replay() == "pasted text"


def test_replay_handles_insertion_in_the_middle(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "ac"})
    j.append(EventKind.INSERT, {"pos": 1, "text": "b"})
    assert j.replay() == "abc"


def test_replay_can_stop_at_a_sequence_number(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "one"})
    j.append(EventKind.INSERT, {"pos": 3, "text": " two"})
    assert j.replay(upto=1) == "one"


def test_replay_rejects_an_edit_outside_the_document(tmp_path):
    """An impossible edit means the log does not describe a real session —
    a completeness failure, not something to clamp into range."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 40, "text": "x"})
    with pytest.raises(JournalError, match="outside a document"):
        j.replay()


def test_replay_rejects_an_oversized_delete(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "abc"})
    j.append(EventKind.DELETE, {"pos": 0, "length": 99})
    with pytest.raises(JournalError, match="outside a document"):
        j.replay()


def test_non_content_events_do_not_affect_the_document(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "text"})
    j.append(EventKind.FOCUS_OUT, {})
    j.append(EventKind.FOCUS_IN, {})
    j.append(EventKind.NOTE, {"text": "took a break"})
    assert j.replay() == "text"


# =====================================================================
# Verification — the three properties
# =====================================================================


def test_a_clean_session_verifies(tmp_path):
    verification = write_and_seal(opened(tmp_path)).verify()
    assert verification.verified
    assert verification.problems == []


def test_verification_reports_the_three_properties_separately(tmp_path):
    """They fail for different reasons and a reviewer needs to know which."""
    v = write_and_seal(opened(tmp_path)).verify().to_dict()
    assert {"chain_intact", "replay_matches", "opened_empty", "sealed"} <= set(v)


def test_an_unsealed_journal_is_not_verified(tmp_path):
    """Absence of a checked failure is not a pass."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "unfinished"})
    verification = j.verify()
    assert not verification.verified
    assert not verification.sealed
    assert any("session_close" in p for p in verification.problems)


def test_an_empty_journal_is_not_verified(tmp_path):
    assert not Journal(tmp_path / "empty.jsonl").verify().verified


def test_opening_with_text_already_present_is_retroactive_logging(tmp_path):
    """CAS §3.2: "retroactive logging does not exist"."""
    j = Journal(tmp_path / "j.jsonl", clock=lambda: 0)
    j.append(EventKind.SESSION_OPEN, {"initial_text": "already written"})
    j.append(EventKind.SESSION_CLOSE,
             {"text": "already written", "sha256": text_checksum("already written")})
    verification = j.verify()
    assert not verification.opened_empty
    assert not verification.verified
    assert any("retroactive" in p for p in verification.problems)


def test_verify_never_raises_on_a_broken_journal(tmp_path):
    """A broken log is a finding to report, not an exception to swallow."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 99, "text": "impossible"})
    verification = j.verify()           # does not raise
    assert not verification.verified
    assert verification.problems


# =====================================================================
# Tamper detection — the reason the journal exists
# =====================================================================


def rewrite(path, transform):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = transform(rows)
    path.write_text("".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))


def test_editing_an_event_breaks_the_chain(tmp_path):
    """The attack this defends against: relabelling a paste as typing, so the
    fact summary stops showing that the document arrived in one block."""
    j = opened(tmp_path)
    j.append(EventKind.PASTE, {"pos": 0, "text": "hello world"})
    text = j.replay()
    j.append(EventKind.SESSION_CLOSE, {"text": text, "sha256": text_checksum(text)})
    path = tmp_path / "journal.jsonl"
    assert Journal(path).verify().verified          # clean before tampering

    def swap_paste_for_typing(rows):
        assert rows[1]["kind"] == "paste"
        rows[1]["kind"] = "insert"
        return rows

    rewrite(path, swap_paste_for_typing)
    verification = Journal(path).verify()
    assert not verification.chain_intact
    assert any("does not match its hash" in p for p in verification.problems)


def test_truncating_the_log_is_caught_twice(tmp_path):
    """Dropping an event breaks the chain *and* makes replay diverge — the
    two checks are independent on purpose."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "first part "})
    j.append(EventKind.PASTE, {"pos": 11, "text": "pasted part"})
    text = j.replay()
    j.append(EventKind.SESSION_CLOSE, {"text": text, "sha256": text_checksum(text)})
    path = tmp_path / "journal.jsonl"

    rewrite(path, lambda rows: [r for r in rows if r["kind"] != "paste"])
    verification = Journal(path).verify()
    assert not verification.chain_intact
    assert not verification.replay_matches


def test_reordering_events_breaks_the_chain(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "a"})
    j.append(EventKind.INSERT, {"pos": 1, "text": "b"})
    text = j.replay()
    j.append(EventKind.SESSION_CLOSE, {"text": text, "sha256": text_checksum(text)})
    path = tmp_path / "journal.jsonl"

    def swap(rows):
        rows[1], rows[2] = rows[2], rows[1]
        return rows

    rewrite(path, swap)
    assert not Journal(path).verify().chain_intact


def test_appending_a_forged_event_after_the_seal_is_caught(tmp_path):
    write_and_seal(opened(tmp_path), "short")
    path = tmp_path / "journal.jsonl"

    def append_forgery(rows):
        forged = dict(rows[-1])
        forged["seq"] = len(rows)
        forged["kind"] = "insert"
        forged["payload"] = {"pos": 0, "text": "sneaked in"}
        rows.append(forged)
        return rows

    rewrite(path, append_forgery)
    assert not Journal(path).verify().chain_intact


def test_rewriting_the_sealed_text_is_caught(tmp_path):
    """Swapping the finished text for different prose while keeping the log."""
    write_and_seal(opened(tmp_path), "the text that was written")
    path = tmp_path / "journal.jsonl"

    def swap_text(rows):
        rows[-1]["payload"]["text"] = "completely different prose"
        return rows

    rewrite(path, swap_text)
    verification = Journal(path).verify()
    assert not verification.verified
    assert not verification.chain_intact


def test_a_consistently_rehashed_edit_still_fails_on_replay(tmp_path):
    """The strongest realistic attack: recompute every hash after the edit so
    the chain is internally consistent again. Replay is the second wall."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "written by hand"})
    text = j.replay()
    j.append(EventKind.SESSION_CLOSE, {"text": text, "sha256": text_checksum(text)})
    path = tmp_path / "journal.jsonl"

    def drop_and_rechain(rows):
        kept = [r for r in rows if r["payload"].get("text") != "written by hand"]
        prev = GENESIS
        for index, row in enumerate(kept):
            row["seq"] = index
            row["prev"] = prev
            row["hash"] = canonical_hash(prev, row["seq"], row["t"], row["wall"],
                                         row["kind"], row["payload"])
            prev = row["hash"]
        return kept

    rewrite(path, drop_and_rechain)
    verification = Journal(path).verify()
    assert verification.chain_intact          # the forgery is self-consistent
    assert not verification.replay_matches    # but the text cannot be produced
    assert not verification.verified


def test_a_mismatched_sealed_checksum_is_caught(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "abc"})
    j.append(EventKind.SESSION_CLOSE,
             {"text": "abc", "sha256": text_checksum("something else")})
    verification = j.verify()
    assert not verification.replay_matches
    assert any("checksum" in p for p in verification.problems)


# =====================================================================
# Privacy: what the journal refuses to store
# =====================================================================


def test_a_delete_never_stores_the_removed_text(tmp_path):
    """The largest privacy exposure the tool could have had. A contributor who
    types something private and deletes it must not find it preserved in an
    evidence archive forever."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "my private phone number"})
    j.append(EventKind.DELETE, {"pos": 3, "length": 20})
    stored = (tmp_path / "journal.jsonl").read_text()
    delete_event = [e for e in j.events if e.kind == "delete"][0]
    assert set(delete_event.payload) == {"pos", "length"}
    assert "private phone number" in stored   # from the insert, not the delete
    assert stored.count("private phone number") == 1


def test_replay_needs_no_deleted_text(tmp_path):
    """Position and length are sufficient, which is why omitting the text is
    free rather than a trade-off."""
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "keep DROP keep"})
    j.append(EventKind.DELETE, {"pos": 5, "length": 5})
    assert j.replay() == "keep keep"


# =====================================================================
# Facts: counts and durations, never a judgment
# =====================================================================


def test_facts_count_events_by_kind(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "one"})
    j.append(EventKind.INSERT, {"pos": 3, "text": " two"})
    j.append(EventKind.PASTE, {"pos": 7, "text": " three"})
    facts = j.facts()
    assert facts["events"]["insert"] == 2
    assert facts["paste_count"] == 1
    assert facts["events_total"] == 4


def test_facts_measure_pasted_and_typed_characters_apart(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.INSERT, {"pos": 0, "text": "typed"})
    j.append(EventKind.PASTE, {"pos": 5, "text": "pasted!!"})
    facts = j.facts()
    assert facts["inserted_chars"] == 5
    assert facts["pasted_chars"] == 8


def test_facts_split_active_from_idle_time(tmp_path):
    j = Journal(tmp_path / "j.jsonl", clock=clock_from([0]))
    j.append(EventKind.SESSION_OPEN, {"initial_text": ""})
    j.append(EventKind.INSERT, {"pos": 0, "text": "a"}, t=1_000)
    j.append(EventKind.INSERT, {"pos": 1, "text": "b"}, t=1_000 + IDLE_GAP_MS + 5_000)
    facts = j.facts()
    assert facts["idle_ms"] == IDLE_GAP_MS + 5_000
    assert facts["active_ms"] == 1_000
    assert facts["duration_ms"] == 1_000 + IDLE_GAP_MS + 5_000


def test_facts_report_focus_loss_as_a_count(tmp_path):
    j = opened(tmp_path)
    j.append(EventKind.FOCUS_OUT, {})
    j.append(EventKind.FOCUS_IN, {})
    j.append(EventKind.FOCUS_OUT, {})
    assert j.facts()["focus_out_count"] == 2


def test_facts_contain_no_score_flag_or_verdict(tmp_path):
    """docs/LWE_DESIGN.md §1.1: a classifier over process is the same
    circularity CAS §5.4 forbids over text, one level up. The fact summary is
    counts and durations, and this test is what keeps it that way."""
    j = write_and_seal(opened(tmp_path))
    facts = j.facts()
    forbidden = ("score", "suspicious", "likely", "confidence", "probability",
                 "authentic", "genuine", "verdict", "risk", "flag", "rating")
    for key, value in facts.items():
        assert not any(word in key.lower() for word in forbidden), key
        assert isinstance(value, (int, dict)), f"{key} is not a count or duration"


def test_facts_of_a_sealed_session_report_the_final_size(tmp_path):
    j = write_and_seal(opened(tmp_path), "one two three")
    facts = j.facts()
    assert facts["final_words"] == 3
    assert facts["final_chars"] == len("one two three")


# =====================================================================
# Checksums
# =====================================================================


def test_the_journal_checksums_its_own_file(tmp_path):
    j = write_and_seal(opened(tmp_path))
    assert j.checksum().startswith("sha256:")
    assert j.checksum() == Journal(tmp_path / "journal.jsonl").checksum()


def test_the_journal_checksum_changes_when_the_file_changes(tmp_path):
    j = write_and_seal(opened(tmp_path))
    before = j.checksum()
    path = tmp_path / "journal.jsonl"
    path.write_text(path.read_text().replace("hello", "HELLO"))
    assert Journal(path).checksum() != before


def test_text_checksum_is_stable_and_content_addressed():
    assert text_checksum("abc") == text_checksum("abc")
    assert text_checksum("abc") != text_checksum("abd")
