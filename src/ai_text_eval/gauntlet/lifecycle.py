"""Identifier registry and lifecycle state machine (R-02).

CAS §2 defines the state machine every sample moves through; §9.5 makes
identifiers permanent and unique across *all* history including rejections
and redactions; Stage 2 freezes the text at CANDIDATE with a checksum, after
which X-5 makes any change an automatic rejection.

Two invariants are enforced structurally here:

  1. **No stage is skipped.** `LEGAL_TRANSITIONS` is the whole transition
     relation; anything outside it raises `LifecycleError`.
  2. **Identifiers are never reused.** An identifier that has ever been
     registered — even one now REJECTED or REDACTED — cannot be registered
     again. The ledger sees history, not just the live corpus, which is the
     gap the plain DUPLICATE_ID check could not close.

Persistence is an append-only JSONL event log; the current state is a replay
of that log, never an in-place mutation, so the audit trail is the source of
truth rather than a side effect of it (P4, P5).

Timestamps are recorded facts supplied by the acting process, not read from a
clock inside the registry — CAS §4.2 treats them as evidence, and passing
them in keeps replay deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ai_text_eval.gauntlet.spec import CORPUS_DIR


class State(str, Enum):
    IDEA = "idea"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    SCREENED = "screened"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    ASSIGNED = "assigned"
    RELEASED = "released"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"   # terminal exception (§6.4)
    REDACTED = "redacted"   # terminal exception (§9.4)


#: The complete transition relation (CAS §2). Rejection is reachable from any
#: pre-acceptance state (§6.4); once ACCEPTED the exits are deprecation and
#: redaction only (§9.3, §9.4). DEPRECATED remains executable and may still be
#: redacted for post-hoc PII/legal necessity.
LEGAL_TRANSITIONS: dict[State, frozenset[State]] = {
    State.IDEA: frozenset({State.CANDIDATE, State.REJECTED}),
    State.CANDIDATE: frozenset({State.VALIDATED, State.REJECTED}),
    State.VALIDATED: frozenset({State.SCREENED, State.REJECTED}),
    State.SCREENED: frozenset({State.REVIEWED, State.REJECTED}),
    State.REVIEWED: frozenset({State.ACCEPTED, State.REJECTED}),
    State.ACCEPTED: frozenset({State.ASSIGNED}),
    State.ASSIGNED: frozenset({State.RELEASED}),
    State.RELEASED: frozenset({State.DEPRECATED, State.REDACTED}),
    State.DEPRECATED: frozenset({State.REDACTED}),
    State.REJECTED: frozenset(),
    State.REDACTED: frozenset(),
}

TERMINAL_STATES = frozenset({State.REJECTED, State.REDACTED})

#: The transition into which a sample is frozen (Stage 2). The text checksum
#: is recorded here and immutable thereafter.
FREEZE_TRANSITION = (State.IDEA, State.CANDIDATE)


class LifecycleError(RuntimeError):
    """An illegal transition, a reused identifier, or a post-freeze edit."""


def text_checksum(text: str) -> str:
    """SHA-256 of the exact text bytes (UTF-8). The frozen fingerprint."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class IdentifierRecord:
    """The current lifecycle facts for one identifier, built by replay."""

    identifier: str
    state: State
    checksum: str | None = None          # set at freeze
    lineage: list[dict] = field(default_factory=list)
    terminal_reason: str | None = None
    history: list[dict] = field(default_factory=list)

    @property
    def is_frozen(self) -> bool:
        return self.checksum is not None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def verify_text(self, text: str) -> bool:
        """Whether `text` matches the frozen checksum (X-5)."""
        if self.checksum is None:
            raise LifecycleError(
                f"{self.identifier} is not frozen; there is no text to verify")
        return text_checksum(text) == self.checksum


class IdentifierRegistry:
    """Append-only ledger of identifier lifecycle events."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (CORPUS_DIR / "registry" / "identifiers.jsonl")
        self._records: dict[str, IdentifierRecord] = {}
        if self.path.is_file():
            self._replay()

    # -- replay ----------------------------------------------------------

    def _replay(self) -> None:
        with self.path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as err:
                    raise LifecycleError(
                        f"{self.path}:{line_no}: corrupt ledger line: {err}") from err
                self._apply(event, persist=False)

    # -- event application -----------------------------------------------

    def _apply(self, event: dict, persist: bool) -> None:
        ident = event["identifier"]
        to_state = State(event["to_state"])
        kind = event.get("event")

        if kind == "open":
            if ident in self._records:
                raise LifecycleError(
                    f"identifier {ident} already exists (§9.5: identifiers are "
                    "unique across all history and never reused)")
            self._records[ident] = IdentifierRecord(identifier=ident, state=to_state)
        else:
            rec = self._records.get(ident)
            if rec is None:
                raise LifecycleError(f"transition on unknown identifier {ident}")
            frm = rec.state
            if to_state not in LEGAL_TRANSITIONS.get(frm, frozenset()):
                raise LifecycleError(
                    f"illegal transition {frm.value} -> {to_state.value} for "
                    f"{ident}; no stage may be skipped (CAS §2)")
            if event.get("checksum") is not None:
                if rec.checksum is not None and rec.checksum != event["checksum"]:
                    raise LifecycleError(
                        f"{ident} is already frozen; text cannot change (X-5)")
                rec.checksum = event["checksum"]
            rec.state = to_state
            if to_state in TERMINAL_STATES:
                rec.terminal_reason = event.get("reason")

        rec = self._records[ident]
        for link in event.get("lineage", []) or []:
            rec.lineage.append(link)
        rec.history.append(event)

        if persist:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _event(self, **fields) -> None:
        self._apply(fields, persist=True)

    # -- public API ------------------------------------------------------

    def open_idea(self, identifier: str, actor_role: str, timestamp: str,
                  cell: dict | None = None, target_weakness: str | None = None) -> None:
        """Stage 1: register a new identifier in the IDEA state (§2)."""
        self._event(event="open", identifier=identifier, from_state=None,
                    to_state=State.IDEA.value, actor_role=actor_role,
                    timestamp=timestamp, cell=cell, target_weakness=target_weakness)

    def freeze(self, identifier: str, text: str, actor_role: str,
               timestamp: str, lineage: list[dict] | None = None) -> str:
        """Stage 2: IDEA -> CANDIDATE, recording the text checksum (§2).

        Returns the frozen checksum. After this the text MUST NOT change.
        """
        checksum = text_checksum(text)
        self._event(event="transition", identifier=identifier,
                    from_state=State.IDEA.value, to_state=State.CANDIDATE.value,
                    checksum=checksum, actor_role=actor_role, timestamp=timestamp,
                    lineage=lineage or [])
        return checksum

    def transition(self, identifier: str, to_state: State | str, actor_role: str,
                   timestamp: str, reason: str = "") -> None:
        """A legal lifecycle transition for VALIDATED..RELEASED and exceptions."""
        to = State(to_state) if not isinstance(to_state, State) else to_state
        rec = self.require(identifier)
        self._event(event="transition", identifier=identifier,
                    from_state=rec.state.value, to_state=to.value,
                    actor_role=actor_role, timestamp=timestamp, reason=reason)

    def reject(self, identifier: str, actor_role: str, timestamp: str,
               reason: str) -> None:
        """Terminal rejection (§6.4). Reason is retained permanently (P5)."""
        self.transition(identifier, State.REJECTED, actor_role, timestamp, reason)

    def supersede(self, old_identifier: str, new_identifier: str, text: str,
                  actor_role: str, timestamp: str) -> str:
        """Stage 2 edit path: reject the old id as 'superseded', freeze the new
        one with a supersedes lineage link (§2, §6.5)."""
        self.reject(old_identifier, actor_role, timestamp, reason="superseded")
        self.open_idea(new_identifier, actor_role, timestamp)
        return self.freeze(new_identifier, text, actor_role, timestamp,
                           lineage=[{"relation": "supersedes", "target": old_identifier}])

    # -- queries ---------------------------------------------------------

    def exists(self, identifier: str) -> bool:
        return identifier in self._records

    def get(self, identifier: str) -> IdentifierRecord | None:
        return self._records.get(identifier)

    def require(self, identifier: str) -> IdentifierRecord:
        rec = self._records.get(identifier)
        if rec is None:
            raise LifecycleError(f"unknown identifier {identifier}")
        return rec

    def state_of(self, identifier: str) -> State | None:
        rec = self._records.get(identifier)
        return rec.state if rec else None

    def all_identifiers(self) -> list[str]:
        return list(self._records)

    def in_state(self, state: State | str) -> list[str]:
        s = State(state) if not isinstance(state, State) else state
        return [i for i, r in self._records.items() if r.state is s]
