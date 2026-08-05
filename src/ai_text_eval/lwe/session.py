"""Writing session lifecycle (LWE M2).

A session wraps a journal with the state machine that makes its evidence
meaningful. The journal proves *what happened*; the session enforces *when it
could happen* — specifically that consent and the environment attestation are
recorded before any text exists, because CAS §3.2 says the logging arrangement
"MUST be in place before writing begins; retroactive logging does not exist".

**Why a CREATED state exists.** It would be simpler to open a journal and
start writing. The extra state is what forces the attestation into event 0: a
session cannot accept content until it has been opened, and opening is the act
that writes the attestation. An attestation appended later is provably later —
its sequence number says so — and the export refuses it.

**Declining is not failing.** A contributor who will not attest a tool-free
environment still gets a session, still writes, and still contributes; their
evidence simply supports a lower tier (M3). The strategy calls this "burden
proportional to ambition", and implementing it as a refusal would lose
contributions the corpus can legitimately use in DEV.

**Abandoned sessions are retained.** CAS P5: nothing is deleted. An abandoned
session exports nothing, but its existence is a fact and it stays on disk.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ai_text_eval.lwe.journal import (
    EventKind,
    Journal,
    JournalError,
    Verification,
    text_checksum,
)

#: The three things a contributor is asked, in the order they are asked.
#: Consent is separate from the environment attestation on purpose: agreeing
#: to be logged and asserting a tool-free environment are different promises,
#: and bundling them would obscure which one a contributor actually made.
CONSENT_KEY = "consent_to_logging"
ENVIRONMENT_KEY = "environment_free_of_generative_tools"
VERIFICATION_KEY = "spot_verification_acknowledged"

ATTESTATION_KEYS = (CONSENT_KEY, ENVIRONMENT_KEY, VERIFICATION_KEY)

SESSION_MANIFEST = "session.json"
JOURNAL_FILE = "journal.jsonl"
TEXT_FILE = "text.txt"


class SessionError(RuntimeError):
    """Raised when an operation is illegal in the session's current state."""


class SessionState(str, Enum):
    CREATED = "created"
    OPEN = "open"
    CLOSED = "closed"
    EXPORTED = "exported"
    ABANDONED = "abandoned"


#: Only OPEN accepts content. Stated as data so the rule is checkable.
CONTENT_STATES = frozenset({SessionState.OPEN})


@dataclass
class Attestations:
    """What the contributor affirmed, and nothing more.

    Every field defaults to False. A missing answer is a "no", never an
    assumed "yes" — the whole point of the record is that an unmade promise is
    distinguishable from a made one.
    """

    consent_to_logging: bool = False
    environment_free_of_generative_tools: bool = False
    spot_verification_acknowledged: bool = False
    tools_used: list[str] = field(default_factory=list)
    declared_model_involvement: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {CONSENT_KEY: self.consent_to_logging,
                ENVIRONMENT_KEY: self.environment_free_of_generative_tools,
                VERIFICATION_KEY: self.spot_verification_acknowledged,
                "tools_used": list(self.tools_used),
                "declared_model_involvement": self.declared_model_involvement,
                "note": self.note}

    @classmethod
    def from_dict(cls, raw: dict) -> "Attestations":
        return cls(
            consent_to_logging=bool(raw.get(CONSENT_KEY, False)),
            environment_free_of_generative_tools=bool(raw.get(ENVIRONMENT_KEY, False)),
            spot_verification_acknowledged=bool(raw.get(VERIFICATION_KEY, False)),
            tools_used=list(raw.get("tools_used", [])),
            declared_model_involvement=bool(raw.get("declared_model_involvement", False)),
            note=str(raw.get("note", "")))


@dataclass
class SessionIntent:
    """What the contributor was asked to write.

    Recorded as *intent*, never asserted as metadata. CAS §3.4 makes the
    instruction define the category, and an instruction is not mechanically
    classifiable — so the cell here is a hint for the reviewer, not a claim.
    """

    contributor: str = ""
    prompt: str = ""
    intended_category: str = ""
    intended_length_bucket: str = ""
    language: str = ""

    def to_dict(self) -> dict:
        return {"contributor": self.contributor, "prompt": self.prompt,
                "intended_category": self.intended_category,
                "intended_length_bucket": self.intended_length_bucket,
                "language": self.language}

    @classmethod
    def from_dict(cls, raw: dict) -> "SessionIntent":
        return cls(contributor=str(raw.get("contributor", "")),
                   prompt=str(raw.get("prompt", "")),
                   intended_category=str(raw.get("intended_category", "")),
                   intended_length_bucket=str(raw.get("intended_length_bucket", "")),
                   language=str(raw.get("language", "")))


class WritingSession:
    """One writing session: a directory, a journal, and a state machine."""

    def __init__(self, root: Path | str, *, session_id: str = "",
                 clock=None, wall=None):
        self.root = Path(root)
        self.session_id = session_id or self.root.name
        self._clock = clock
        # Wall-clock is a contributor assertion (threat T6). Injected so it is
        # explicit and so tests are deterministic.
        self._wall = wall or (lambda: "")
        self.journal = Journal(self.root / JOURNAL_FILE, clock=clock)
        self.state = SessionState.CREATED
        self.intent = SessionIntent()
        self.attestations = Attestations()
        self.token = secrets.token_urlsafe(24)
        self._text = ""
        self._load_manifest()
        self._restore_from_journal()

    # -- persistence -----------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / SESSION_MANIFEST

    def _load_manifest(self) -> None:
        if not self.manifest_path.is_file():
            return
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.state = SessionState(raw.get("state", SessionState.CREATED.value))
        self.intent = SessionIntent.from_dict(raw.get("intent", {}))
        self.attestations = Attestations.from_dict(raw.get("attestations", {}))

    def _restore_from_journal(self) -> None:
        """Rebuild in-memory state after a crash.

        The journal is the source of truth, not the manifest: a manifest can
        be stale by one event, a flushed journal cannot.
        """
        events = self.journal.events
        if not events:
            return
        opening = events[0].payload
        if self.state is SessionState.CREATED:
            self.state = SessionState.OPEN
        self.intent = SessionIntent.from_dict(opening.get("intent", {}))
        self.attestations = Attestations.from_dict(opening.get("attestations", {}))
        try:
            self._text = self.journal.replay()
        except JournalError:
            self._text = ""
        if self.journal.is_sealed and self.state is SessionState.OPEN:
            self.state = SessionState.CLOSED

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    # -- lifecycle -------------------------------------------------------

    def open(self, intent: SessionIntent, attestations: Attestations) -> None:
        """Record consent and attestations, then begin logging.

        This is the only place the attestation can be recorded, and it becomes
        event 0. That is the mechanism behind CAS §3.2's requirement that the
        arrangement precede the writing.
        """
        if self.state is not SessionState.CREATED:
            raise SessionError(
                f"session is {self.state.value}; only a created session can be "
                "opened, because opening is what puts the attestation before "
                "the writing")
        if not attestations.consent_to_logging:
            raise SessionError(
                "logging cannot begin without consent to logging; a session "
                "that records a contributor who did not agree is not evidence, "
                "it is surveillance")
        if not intent.contributor.strip():
            raise SessionError(
                "a session needs a named contributor: T1 and T2 both rest on "
                "an identified author (CAS §5.2)")

        self.intent = intent
        self.attestations = attestations
        self.journal.append(
            EventKind.SESSION_OPEN,
            {"initial_text": "",
             "lwe_version": _version(),
             "session_id": self.session_id,
             "intent": intent.to_dict(),
             "attestations": attestations.to_dict()},
            wall=self._wall())
        self.state = SessionState.OPEN
        self.save()

    def _require_open(self, action: str) -> None:
        if self.state not in CONTENT_STATES:
            raise SessionError(
                f"cannot {action}: session is {self.state.value} and only an "
                "open session accepts content")

    # -- content ---------------------------------------------------------

    def insert(self, pos: int, text: str) -> None:
        self._require_open("insert")
        self._apply(EventKind.INSERT, {"pos": int(pos), "text": str(text)})

    def paste(self, pos: int, text: str) -> None:
        """Record a paste.

        Never blocked. Blocking would push a determined contributor to
        retyping, which the tool cannot see at all — trading a recorded fact
        for an invisible one. Whether a paste disqualifies T1 is a governance
        question (LWE_DESIGN §14.1); this records it and takes no position.
        """
        self._require_open("paste")
        self._apply(EventKind.PASTE, {"pos": int(pos), "text": str(text)})

    def delete(self, pos: int, length: int) -> None:
        """Record a deletion by position and length only — never its content."""
        self._require_open("delete")
        self._apply(EventKind.DELETE, {"pos": int(pos), "length": int(length)})

    def focus_out(self) -> None:
        self._require_open("record focus loss")
        self._apply(EventKind.FOCUS_OUT, {})

    def focus_in(self) -> None:
        self._require_open("record focus return")
        self._apply(EventKind.FOCUS_IN, {})

    def note(self, text: str) -> None:
        self._require_open("add a note")
        self._apply(EventKind.NOTE, {"text": str(text)})

    def _apply(self, kind: EventKind, payload: dict) -> None:
        """Append, then advance the in-memory document.

        Order matters: the journal write is durable before the document moves,
        so a crash can leave the log ahead of the document but never behind
        it. A log ahead is recoverable; a document ahead is unexplained text.
        """
        before = self._text
        self.journal.append(kind, payload, wall=self._wall())
        try:
            self._text = self.journal.replay()
        except JournalError as err:
            self._text = before
            raise SessionError(f"edit does not apply to the document: {err}") from err

    @property
    def text(self) -> str:
        return self._text

    # -- closing ---------------------------------------------------------

    def close(self) -> Verification:
        """Seal the session and verify it. Returns the verification result.

        A close that does not verify still closes — the session is over either
        way — but the failure is recorded rather than hidden, and export will
        refuse T1 on it.
        """
        if self.state is not SessionState.OPEN:
            raise SessionError(f"session is {self.state.value}; nothing to close")
        text = self._text
        self.journal.append(
            EventKind.SESSION_CLOSE,
            {"text": text, "sha256": text_checksum(text),
             "words": len(text.split()), "chars": len(text)},
            wall=self._wall())
        self.state = SessionState.CLOSED
        (self.root / TEXT_FILE).write_text(text, encoding="utf-8")
        self.save()
        return self.journal.verify()

    def abandon(self, reason: str = "") -> None:
        """End a session without sealing it. Retained under P5, exports nothing."""
        if self.state in (SessionState.CLOSED, SessionState.EXPORTED):
            raise SessionError(
                f"session is {self.state.value}; a completed session cannot be "
                "abandoned after the fact")
        if self.state is SessionState.OPEN:
            self.note(f"abandoned: {reason}" if reason else "abandoned")
        self.state = SessionState.ABANDONED
        self.save()

    # -- reporting -------------------------------------------------------

    def verify(self) -> Verification:
        return self.journal.verify()

    def facts(self) -> dict:
        return self.journal.facts()

    @property
    def attestation_event_seq(self) -> int | None:
        """Where the attestation was recorded. Should be 0; anything else is
        an attestation that arrived after writing began."""
        events = self.journal.events
        if not events or events[0].kind != EventKind.SESSION_OPEN.value:
            return None
        return events[0].seq

    def to_dict(self) -> dict:
        verification = self.verify()
        return {
            "lwe_version": _version(),
            "session_id": self.session_id,
            "state": self.state.value,
            "intent": self.intent.to_dict(),
            "attestations": dict(self.attestations.to_dict(),
                                 recorded_at_event=self.attestation_event_seq),
            "verification": verification.to_dict(),
            "facts": self.facts(),
            "text_sha256": text_checksum(self._text),
            "journal_sha256": (self.journal.checksum()
                               if (self.root / JOURNAL_FILE).is_file() else ""),
        }


def _version() -> str:
    from ai_text_eval.lwe import LWE_VERSION
    return LWE_VERSION


def create_session(root: Path | str, session_id: str = "", *,
                   clock=None, wall=None) -> WritingSession:
    """Create a fresh session directory. Refuses to reuse an existing one."""
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise SessionError(
            f"{root} already contains a session; sessions are append-only and "
            "are never reused (CAS P5)")
    root.mkdir(parents=True, exist_ok=True)
    return WritingSession(root, session_id=session_id, clock=clock, wall=wall)


def load_session(root: Path | str, *, clock=None, wall=None) -> WritingSession:
    """Reopen an existing session, restoring state from the journal."""
    root = Path(root)
    if not (root / JOURNAL_FILE).is_file() and not (root / SESSION_MANIFEST).is_file():
        raise SessionError(f"{root} does not contain a session")
    return WritingSession(root, clock=clock, wall=wall)
