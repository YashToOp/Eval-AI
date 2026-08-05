"""The hash-chained session journal (LWE M1).

This module is the whole product. The writing surface is packaging; the
journal is the thing CAS §3.2 means when it says a session log can *prove*
what a tool-free environment claim cannot otherwise establish.

**Three properties, mechanically checkable.** A journal supports T1 evidence
only if all three hold:

  1. *Chain intact* — every event carries the hash of its predecessor, so
     editing, removing, or reordering any event invalidates every hash after
     it. Tamper-evidence, not secrecy.
  2. *Replay matches* — applying every content event in sequence reconstructs
     exactly the text sealed at close. This is what turns CAS §5.4's
     `complete_session` from a contributor's assertion into a property. A log
     missing the paste that produced half the document cannot replay to the
     document.
  3. *Opened empty* — event 0 is a `session_open` and the document began with
     no content, so "logging started before writing" (CAS §3.2: "retroactive
     logging does not exist") is structural rather than promised.

**What the journal deliberately does not store.** A `delete` records position
and length, never the removed text. Forward replay does not need it, and a
contributor who types something private and deletes it should not discover it
preserved forever in an evidence archive. This is the largest privacy exposure
the tool could have had, and it costs nothing to avoid.

**What the journal deliberately does not compute.** Counts and durations only.
No judgment about whether a session "looks" genuine — see the module-level
prohibition in `docs/LWE_DESIGN.md` §1.1. A classifier over typing dynamics is
the same circularity CAS §5.4 forbids over text, one level up.

The module imports nothing from GAUNTLET, so the writing environment runs as
an independent application.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

#: Chain root. The first event's `prev` is this, so a journal that has had its
#: opening event removed cannot be mistaken for one that never had it.
GENESIS = "sha256:" + "0" * 64

#: Gap after which a contributor is treated as idle rather than writing, for
#: the active/idle split in the fact summary. A reporting convention, not a
#: judgment: both numbers are reported and neither is thresholded.
IDLE_GAP_MS = 30_000


class JournalError(RuntimeError):
    """Raised when an operation would break the journal's invariants."""


class EventKind(str, Enum):
    SESSION_OPEN = "session_open"
    INSERT = "insert"
    DELETE = "delete"
    PASTE = "paste"
    FOCUS_OUT = "focus_out"
    FOCUS_IN = "focus_in"
    NOTE = "note"
    SESSION_CLOSE = "session_close"


#: Events that change the document. Replay applies exactly these.
CONTENT_KINDS = frozenset({EventKind.INSERT, EventKind.PASTE, EventKind.DELETE})


def canonical_hash(prev: str, seq: int, t: int, wall: str, kind: str,
                   payload: dict) -> str:
    """The chain hash for one event.

    Canonical JSON with sorted keys and no insignificant whitespace, so the
    hash of a re-serialised event is stable across writers and Python
    versions. `prev` is folded in first, which is what makes the chain a
    chain rather than a set of independent digests.
    """
    body = json.dumps(
        {"seq": seq, "t": t, "wall": wall, "kind": kind, "payload": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
    return "sha256:" + digest


@dataclass(frozen=True)
class Event:
    seq: int
    t: int          # ms since session open, monotonic — trustworthy ordering
    wall: str       # contributor wall-clock — an assertion, not a fact
    kind: str
    payload: dict
    prev: str
    hash: str

    @property
    def event_kind(self) -> EventKind | None:
        try:
            return EventKind(self.kind)
        except ValueError:
            return None

    def to_dict(self) -> dict:
        return {"seq": self.seq, "t": self.t, "wall": self.wall,
                "kind": self.kind, "payload": self.payload,
                "prev": self.prev, "hash": self.hash}

    def recompute(self) -> str:
        return canonical_hash(self.prev, self.seq, self.t, self.wall,
                              self.kind, self.payload)


@dataclass
class Verification:
    """The result of checking a journal. Three properties, reported apart.

    They are kept separate rather than collapsed into a boolean because they
    fail for different reasons and a reviewer needs to know which: a broken
    chain is tampering, a replay mismatch is an incomplete log, and a
    non-empty open is retroactive logging.
    """

    chain_intact: bool = False
    replay_matches: bool = False
    opened_empty: bool = False
    sealed: bool = False
    problems: list[str] = field(default_factory=list)
    replayed_text: str = ""

    @property
    def verified(self) -> bool:
        """True only when every property holds. Absence of a checked failure
        is not the same as a pass, so an unsealed journal is never verified."""
        return (self.chain_intact and self.replay_matches
                and self.opened_empty and self.sealed)

    def to_dict(self) -> dict:
        return {"chain_intact": self.chain_intact,
                "replay_matches": self.replay_matches,
                "opened_empty": self.opened_empty,
                "sealed": self.sealed,
                "verified": self.verified,
                "problems": list(self.problems)}


class Journal:
    """An append-only, hash-chained event log backed by a JSONL file.

    Every append is flushed before it is acknowledged, so a crash can lose at
    most the event that had not yet been written — never leave the caller's
    document ahead of the log.
    """

    def __init__(self, path: Path | str, *, clock=None):
        self.path = Path(path)
        self._events: list[Event] = []
        # Injected so tests are deterministic and so the monotonic basis is
        # explicit rather than an ambient global.
        self._clock = clock or (lambda: int(time.monotonic() * 1000))
        self._origin: int | None = None
        if self.path.is_file():
            self._load()

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as err:
                    raise JournalError(
                        f"{self.path}:{line_no}: corrupt journal line: {err}"
                    ) from err
                self._events.append(Event(
                    seq=raw["seq"], t=raw["t"], wall=raw["wall"],
                    kind=raw["kind"], payload=raw["payload"],
                    prev=raw["prev"], hash=raw["hash"]))

    def _write(self, event: Event) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False,
                                sort_keys=True) + "\n")
            fh.flush()

    # -- appending -------------------------------------------------------

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    @property
    def head(self) -> str:
        return self._events[-1].hash if self._events else GENESIS

    @property
    def next_seq(self) -> int:
        return len(self._events)

    @property
    def is_sealed(self) -> bool:
        return bool(self._events) and self._events[-1].kind == EventKind.SESSION_CLOSE.value

    def append(self, kind: EventKind | str, payload: dict | None = None, *,
               wall: str = "", t: int | None = None) -> Event:
        """Append one event and return it.

        Refuses to append after `session_close`: a sealed journal is sealed.
        """
        kind_value = kind.value if isinstance(kind, EventKind) else str(kind)
        if self.is_sealed:
            raise JournalError(
                "the journal is sealed; a session_close event ends the log and "
                "later events would make the seal meaningless")
        if self._events and kind_value == EventKind.SESSION_OPEN.value:
            raise JournalError("session_open must be event 0 and appears once")
        if not self._events and kind_value != EventKind.SESSION_OPEN.value:
            raise JournalError(
                "the first event must be session_open, which carries the "
                "pre-writing consent and environment attestation")

        now = self._clock()
        if self._origin is None:
            self._origin = now if not self._events else now
        elapsed = 0 if not self._events else max(0, now - self._origin)
        if t is not None:
            elapsed = t

        seq = self.next_seq
        prev = self.head
        payload = dict(payload or {})
        event = Event(seq=seq, t=elapsed, wall=wall, kind=kind_value,
                      payload=payload, prev=prev,
                      hash=canonical_hash(prev, seq, elapsed, wall,
                                          kind_value, payload))
        self._write(event)          # durable before acknowledged
        self._events.append(event)
        return event

    # -- replay ----------------------------------------------------------

    def replay(self, upto: int | None = None) -> str:
        """Reconstruct the document from its content events.

        Positions are validated: an out-of-range edit means the log does not
        describe a possible editing session, which is a completeness failure
        rather than something to clamp into range.
        """
        text = ""
        for event in self._events:
            if upto is not None and event.seq > upto:
                break
            kind = event.event_kind
            if kind not in CONTENT_KINDS:
                continue
            payload = event.payload
            pos = int(payload.get("pos", 0))
            if kind in (EventKind.INSERT, EventKind.PASTE):
                inserted = str(payload.get("text", ""))
                if pos < 0 or pos > len(text):
                    raise JournalError(
                        f"event {event.seq}: insert at {pos} outside a document "
                        f"of length {len(text)}")
                text = text[:pos] + inserted + text[pos:]
            else:
                length = int(payload.get("length", 0))
                if pos < 0 or length < 0 or pos + length > len(text):
                    raise JournalError(
                        f"event {event.seq}: delete of {length} at {pos} outside "
                        f"a document of length {len(text)}")
                text = text[:pos] + text[pos + length:]
        return text

    # -- verification ----------------------------------------------------

    def verify(self) -> Verification:
        """Check the three properties T1 evidence depends on.

        Never raises for a bad journal: a broken log is a finding to report,
        not an exception to swallow. Reports each property separately.
        """
        result = Verification()

        if not self._events:
            result.problems.append("the journal is empty")
            return result

        # 1. chain
        prev = GENESIS
        chain_ok = True
        for index, event in enumerate(self._events):
            if event.seq != index:
                result.problems.append(
                    f"event at position {index} claims seq {event.seq}; "
                    "sequence numbers are dense and ordered")
                chain_ok = False
            if event.prev != prev:
                result.problems.append(
                    f"event {event.seq}: prev hash does not match the previous "
                    "event; the chain is broken here")
                chain_ok = False
            if event.hash != event.recompute():
                result.problems.append(
                    f"event {event.seq}: content does not match its hash; the "
                    "event was altered after it was written")
                chain_ok = False
            prev = event.hash
        result.chain_intact = chain_ok

        # 2. opened empty (CAS §3.2: retroactive logging does not exist)
        first = self._events[0]
        if first.kind != EventKind.SESSION_OPEN.value:
            result.problems.append(
                "the first event is not session_open, so the log cannot show "
                "that logging preceded writing")
        elif first.payload.get("initial_text", "") != "":
            result.problems.append(
                "the session opened with text already present; logging that "
                "begins mid-document is retroactive logging")
        else:
            result.opened_empty = True

        # 3. replay against the sealed text
        result.sealed = self.is_sealed
        try:
            result.replayed_text = self.replay()
        except JournalError as err:
            result.problems.append(str(err))
            return result

        if not result.sealed:
            result.problems.append(
                "the journal has no session_close, so there is no sealed text "
                "to replay against")
            return result

        sealed = self._events[-1].payload
        declared = str(sealed.get("text", ""))
        if declared == result.replayed_text:
            result.replay_matches = True
        else:
            result.problems.append(
                f"replay produced {len(result.replayed_text)} characters but "
                f"the sealed text is {len(declared)}; the log does not fully "
                "describe how this text was produced")

        declared_hash = str(sealed.get("sha256", ""))
        if declared_hash and declared_hash != text_checksum(declared):
            result.problems.append(
                "the sealed checksum does not match the sealed text")
            result.replay_matches = False

        return result

    # -- facts (counts and durations only — never a judgment) ------------

    def facts(self) -> dict:
        """Process facts for the reviewer view.

        Counts and durations. No score, no flag, no threshold. See
        `docs/LWE_DESIGN.md` §1.1: a classifier over process is the same
        circularity CAS §5.4 forbids over text.
        """
        counts: dict[str, int] = {}
        pasted_chars = 0
        inserted_chars = 0
        deleted_chars = 0
        idle_ms = 0
        previous_t = 0

        for event in self._events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
            gap = event.t - previous_t
            if gap >= IDLE_GAP_MS:
                idle_ms += gap
            previous_t = event.t
            kind = event.event_kind
            if kind is EventKind.PASTE:
                pasted_chars += len(str(event.payload.get("text", "")))
            elif kind is EventKind.INSERT:
                inserted_chars += len(str(event.payload.get("text", "")))
            elif kind is EventKind.DELETE:
                deleted_chars += int(event.payload.get("length", 0))

        duration = self._events[-1].t if self._events else 0
        final_text = self._events[-1].payload.get("text", "") if self.is_sealed else ""
        return {
            "events_total": len(self._events),
            "events": counts,
            "duration_ms": duration,
            "idle_ms": idle_ms,
            "active_ms": max(0, duration - idle_ms),
            "inserted_chars": inserted_chars,
            "pasted_chars": pasted_chars,
            "deleted_chars": deleted_chars,
            "paste_count": counts.get(EventKind.PASTE.value, 0),
            "focus_out_count": counts.get(EventKind.FOCUS_OUT.value, 0),
            "final_chars": len(final_text),
            "final_words": len(str(final_text).split()),
        }

    def checksum(self) -> str:
        """SHA-256 of the journal file as written."""
        return "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest()


def text_checksum(text: str) -> str:
    """SHA-256 of a document's UTF-8 bytes."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
