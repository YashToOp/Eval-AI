"""Evidence export — the GAUNTLET boundary (LWE M3).

The only module in `lwe` that imports GAUNTLET. Everything below it is a
standalone writing application; this turns a finished session into a
`Candidate` that `IntakeDesk.submit()` accepts, and then gets out of the way.

**Tier is earned, not requested.** The export does not ask what tier the
contributor wants. It looks at what was actually captured and emits the
evidence that supports:

  verified journal + pre-writing environment attestation  →  process_capture (T1)
  verified journal, attestation declined or late          →  attestation (T2)
  journal fails verification, or session never sealed     →  nothing

This is the strategy's "burden proportional to ambition" as a function rather
than a policy: a contributor who declines the attestation is not rejected,
they are recorded at the tier their evidence supports, and the split system
enforces the consequence downstream.

**The export never improves on the record.** It writes
`generative_tools_attested_absent: true` only when the contributor affirmed it
in event 0 — before any text existed. If the contributor declares model
involvement, the export still runs and the Generation Firewall rejects the
candidate at intake. That is the system working, and pre-empting it here would
hide an honest declaration behind a tool's refusal.

**Nothing downstream is bypassed.** No lifecycle state is set, no ledger entry
is written, no split is suggested. The export produces a candidate; intake,
screening, review, and acceptance do what they already do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ai_text_eval.gauntlet.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePackage,
    file_checksum,
)
from ai_text_eval.gauntlet.intake import Candidate, Declaration
from ai_text_eval.lwe.session import (
    JOURNAL_FILE,
    SESSION_MANIFEST,
    TEXT_FILE,
    SessionState,
    WritingSession,
)

EVIDENCE_MANIFEST = "evidence.json"


class ExportError(RuntimeError):
    """Raised when a session cannot produce evidence at all."""


@dataclass
class ExportResult:
    """What an export produced, and why it produced that and not more."""

    session_id: str
    tier: str                      # "T1", "T2", or "" when nothing is supported
    package: EvidencePackage | None
    declaration: Declaration | None
    text: str
    reasons: list[str]             # why this tier and not a higher one

    @property
    def ok(self) -> bool:
        return self.package is not None

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "tier": self.tier,
                "supported": self.ok, "reasons": list(self.reasons),
                "text_chars": len(self.text)}


def supported_tier(session: WritingSession) -> tuple[str, list[str]]:
    """The tier this session's evidence supports, and the reasons.

    Reasons are returned whatever the outcome, including for a T1 pass, so a
    reviewer reading the export can see which checks were consulted rather
    than inferring it from silence.
    """
    reasons: list[str] = []
    verification = session.verify()

    if session.state is SessionState.ABANDONED:
        reasons.append("the session was abandoned and never sealed; an "
                       "abandoned session is evidence of nothing (CAS P5 keeps "
                       "the record, it does not make it admissible)")
        return "", reasons

    if not verification.sealed:
        reasons.append("the session was never closed, so there is no sealed "
                       "text for the log to be complete with respect to")
        return "", reasons

    if not verification.chain_intact:
        reasons.append("the journal's hash chain is broken, so the log has "
                       "been altered since it was written")
        return "", reasons

    if not verification.replay_matches:
        reasons.append("replaying the journal does not reproduce the sealed "
                       "text, so the log does not fully describe how the text "
                       "was produced (CAS §5.4: partial records are "
                       "inadmissible)")
        return "", reasons

    if not verification.opened_empty:
        reasons.append("the session did not open over an empty document; "
                       "CAS §3.2 says retroactive logging does not exist")
        return "", reasons

    reasons.append("journal verified: chain intact, replay matches the sealed "
                   "text, session opened empty")

    attested = session.attestations
    if not attested.environment_free_of_generative_tools:
        reasons.append("the contributor did not attest a generative-tool-free "
                       "environment, which CAS §3.2 requires for T1 "
                       "commissioned writing; the named author's signed "
                       "description of their own process supports T2")
        return "T2", reasons

    if session.attestation_event_seq != 0:
        reasons.append("the environment attestation is not event 0, so it did "
                       "not precede the writing")
        return "T2", reasons

    reasons.append("environment attested free of generative writing tools, "
                   "recorded before writing began (event 0)")
    return "T1", reasons


def build_package(session: WritingSession, *, recorded_at: str = "") -> tuple[
        EvidencePackage | None, str, list[str]]:
    """Assemble the evidence package for a finished session."""
    tier, reasons = supported_tier(session)
    if not tier:
        return None, tier, reasons

    journal_path = session.root / JOURNAL_FILE
    checksum = (file_checksum(journal_path) if journal_path.is_file() else "")
    facts = session.facts()
    attested = session.attestations

    if tier == "T1":
        item = EvidenceItem(
            kind=EvidenceKind.PROCESS_CAPTURE.value,
            path=JOURNAL_FILE,
            checksum=checksum,
            recorded_at=recorded_at,
            attributes={
                # The three CAS attributes `validate_package` enforces. Each is
                # true because something was checked, not because it was asked.
                "logging_started_before_writing": True,
                "complete_session": True,
                "generative_tools_attested_absent": True,
                # Process facts, carried so a reviewer does not have to open
                # the journal to see them. Counts and durations only.
                "capture_method": "keystroke and edit-event capture "
                                  "(gauntlet-write)",
                "session_id": session.session_id,
                "contributor": session.intent.contributor,
                "facts": facts,
                "tools_used": list(attested.tools_used),
            })
    else:
        item = EvidenceItem(
            kind=EvidenceKind.ATTESTATION.value,
            path=JOURNAL_FILE,
            checksum=checksum,
            recorded_at=recorded_at,
            attributes={
                # The CAS §5.3 attestation fields.
                "author_identified": session.intent.contributor,
                "signature": f"session:{session.session_id}",
                "contemporaneous": True,
                "spot_verification_acknowledged":
                    attested.spot_verification_acknowledged,
                "tools_described": ", ".join(attested.tools_used) or "unspecified",
                "session_id": session.session_id,
                "facts": facts,
            })

    package = EvidencePackage(sample_id=session.session_id, tier=tier,
                              items=[item], root=session.root)
    return package, tier, reasons


def build_declaration(session: WritingSession) -> Declaration:
    """The contributor's declaration, reflecting what they actually said.

    Never synthesised. A contributor who declared model involvement gets a
    declaration that says so, and the Generation Firewall does its job.
    """
    attested = session.attestations
    return Declaration(
        contributor=session.intent.contributor,
        model_involved=attested.declared_model_involvement,
        detail=attested.note,
        tools_used=list(attested.tools_used))


def build_candidate(session: WritingSession, identifier: str,
                    metadata: dict, *, recorded_at: str = "") -> Candidate:
    """Turn a finished session into something `IntakeDesk.submit()` accepts.

    `metadata` is supplied by the caller because the LWE does not own it: the
    category follows from the instruction (CAS §3.4), the split is assigned at
    Stage 8 by a release manager (§4.2), and neither is the writing tool's to
    assert.
    """
    package, tier, reasons = build_package(session, recorded_at=recorded_at)
    if package is None:
        raise ExportError(
            f"session {session.session_id} supports no evidence tier: "
            + "; ".join(reasons))
    return Candidate(
        identifier=identifier,
        text=session.text,
        metadata=dict(metadata),
        evidence=package,
        declaration=build_declaration(session),
        target_label=str(metadata.get("label", "")),
        evidence_root=session.root)


def export(session: WritingSession, *, recorded_at: str = "") -> ExportResult:
    """Write the export artifacts and return what they support.

    Idempotent: exporting twice rewrites the same manifest from the same
    journal rather than producing a second, divergent record.
    """
    if session.state is SessionState.CREATED:
        raise ExportError("a session that never opened has nothing to export")

    package, tier, reasons = build_package(session, recorded_at=recorded_at)
    text = session.text

    (session.root / TEXT_FILE).write_text(text, encoding="utf-8")

    if package is not None:
        (session.root / EVIDENCE_MANIFEST).write_text(
            json.dumps({"sample_id": package.sample_id, "tier": package.tier,
                        "items": [_item_dict(i) for i in package.items]},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        if session.state is SessionState.CLOSED:
            session.state = SessionState.EXPORTED

    # Written last, and by this function rather than `session.save()`, because
    # the export manifest is the session manifest *plus* the tier and its
    # reasons. Letting save() run afterwards would silently drop them.
    manifest = dict(session.to_dict(), supported_tier=tier,
                    tier_reasons=reasons)
    (session.root / SESSION_MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    return ExportResult(session_id=session.session_id, tier=tier,
                        package=package,
                        declaration=build_declaration(session) if package else None,
                        text=text, reasons=reasons)


def _item_dict(item: EvidenceItem) -> dict:
    return {"kind": item.kind, "path": item.path, "checksum": item.checksum,
            "recorded_at": item.recorded_at, "attributes": item.attributes}
