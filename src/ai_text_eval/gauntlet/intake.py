"""Candidate intake and the Generation Firewall (R-07, CAS §2, §3.1, §11).

Intake is the one stage where a P2 violation can still be stopped. After it,
nothing can undo one: CAS X-1 states the rejection "is not curable by editing
or re-review", and P2 says there is "no quorum of approvals that converts
model text into human ground truth". So the firewall is placed here, ahead of
validation and review, and it is structural rather than procedural.

**The firewall rule.** A candidate whose target label is HUMAN is rejected if
model involvement appears anywhere in its declared history or its evidence
package. Three independent detectors, because a contributor may be honest,
mistaken, or neither:

  1. Declared involvement — the contributor's own declaration (§3.1 item 3).
  2. Evidence contradiction — a generation session in a package claiming
     HUMAN, which is model involvement whatever the declaration says.
  3. Metadata contradiction — a generator record on a HUMAN-labelled record
     (already a validator P2 alarm; repeated here because intake must not
     depend on a later stage running).

Crucially the firewall reads *records*, never the text. Judging whether prose
"looks generated" is inadmissible (P3), and a firewall that did so would
convert the corpus into a detector's opinion of itself.

**What this module does not do.** It does not review (Phase C), assign splits
(Phase D), or screen for duplicates (R-08 — wired in by the caller when
available). It orchestrates: freeze, firewall, evidence check, metadata
validation, and the lifecycle/ledger records that make each step auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ai_text_eval.gauntlet.evidence import (
    EvidenceKind,
    EvidencePackage,
    validate_package,
    verify_integrity,
)
from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, State
from ai_text_eval.gauntlet.registry import FieldRegistry, load_field_registry
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.validate import validate_sample

#: Evidence kinds that constitute model involvement in a text's history.
MODEL_INVOLVEMENT_KINDS = frozenset({EvidenceKind.GENERATION_SESSION.value})

#: The label reserved for text with no generative model involvement at any
#: stage (CAS §4.1). The firewall protects this label and only this label.
PROTECTED_LABEL = "HUMAN"


class FirewallBreach(RuntimeError):
    """Raised when a candidate would enter the HUMAN class with model
    involvement in its history (P2 / X-1)."""


@dataclass
class Declaration:
    """A contributor's mandatory declaration (CAS §3.1 item 3, §11.2).

    "The model only helped a little" is a declaration, not an exemption, so
    `model_involved` is a plain boolean and any detail lives beside it.
    """

    contributor: str
    model_involved: bool
    detail: str = ""
    tools_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"contributor": self.contributor,
                "model_involved": self.model_involved,
                "detail": self.detail, "tools_used": list(self.tools_used)}


@dataclass
class Candidate:
    """A submission package: text, metadata, evidence, declaration (§11.1)."""

    identifier: str
    text: str
    metadata: dict
    evidence: EvidencePackage
    declaration: Declaration
    target_label: str = ""
    evidence_root: Path | None = None

    @property
    def label(self) -> str:
        return self.target_label or str(self.metadata.get("label", ""))


@dataclass
class IntakeResult:
    """Outcome of one intake attempt."""

    identifier: str
    accepted_as_candidate: bool
    report: Report
    firewall_triggered: bool = False
    state: State | None = None
    checksum: str = ""

    @property
    def ok(self) -> bool:
        return self.accepted_as_candidate


# -- the firewall --------------------------------------------------------

def check_generation_firewall(candidate: Candidate) -> Report:
    """P2 / X-1: model-involved text MUST NOT enter the HUMAN class.

    Returns a report; an error means the candidate is rejected outright and
    no later stage may overturn it.
    """
    r = Report(checked=1)
    sid = candidate.identifier

    if candidate.label != PROTECTED_LABEL:
        return r  # the firewall guards the HUMAN class only

    # 1. Declared involvement (§3.1 item 3).
    if candidate.declaration.model_involved:
        r.error("CAS X-1", "FIREWALL_DECLARED_MODEL_INVOLVEMENT",
                "the contributor declared model involvement and the target "
                "label is HUMAN; this rejection is automatic and is not "
                "curable by editing or re-review (P2)", sid)

    # 2. Evidence contradiction — involvement the declaration did not mention.
    involved_kinds = candidate.evidence.kinds() & MODEL_INVOLVEMENT_KINDS
    if involved_kinds:
        r.error("CAS X-1", "FIREWALL_EVIDENCE_SHOWS_GENERATION",
                f"the evidence package contains {sorted(involved_kinds)} but the "
                "target label is HUMAN; generation evidence is model "
                "involvement whatever the declaration says", sid)
        if not candidate.declaration.model_involved:
            # §11.6: undeclared involvement is the one contributor act that
            # attacks P2 directly.
            r.error("CAS 11.6", "UNDECLARED_MODEL_INVOLVEMENT",
                    f"model involvement found in evidence but not declared by "
                    f"{candidate.declaration.contributor!r}; this quarantines all "
                    "of that contributor's material pending re-audit", sid)

    # 3. Metadata contradiction (§4.2: generator with a HUMAN label is a P2
    #    alarm). Repeated here so intake does not depend on a later stage.
    if candidate.metadata.get("generator") is not None:
        r.error("CAS X-1", "FIREWALL_GENERATOR_RECORD_ON_HUMAN",
                "a generator record is present on a HUMAN-labelled candidate", sid)

    return r


def quarantine_scope(ledger: DecisionLedger, contributor: str) -> list[str]:
    """Identifiers touched by a contributor, for the §11.6 quarantine.

    Discovery of undeclared model involvement quarantines *all* of that
    contributor's material pending re-audit, so the scope must be queryable.
    """
    return sorted({
        ev["sample"] for ev in ledger.by_actor(contributor)
        if ev.get("sample")
    })


# -- intake orchestration ------------------------------------------------

class IntakeDesk:
    """Runs Stages 1-4 of the lifecycle for one candidate (CAS §2).

    Ordering is deliberate and enforced: idea → freeze → firewall → evidence
    → metadata. The firewall runs before any expensive or judgment-bearing
    step, because §3.7's reasoning applies with more force here — a candidate
    that cannot lawfully enter the HUMAN class must not consume review effort,
    and must not be sitting in a VALIDATED state where a later approval could
    appear to bless it.
    """

    def __init__(self, registry: IdentifierRegistry, ledger: DecisionLedger,
                 field_registry: FieldRegistry | None = None):
        self.registry = registry
        self.ledger = ledger
        self.field_registry = field_registry or load_field_registry()

    def submit(self, candidate: Candidate, actor_role: str, timestamp: str,
               strict_firewall: bool = False) -> IntakeResult:
        """Submit one candidate. Returns an IntakeResult; never raises on a
        firewall breach unless `strict_firewall` is set.

        On any failure the identifier is left REJECTED with the reason
        recorded — rejection is terminal for that identifier (§6.4) and the
        reason is retained permanently (P5).
        """
        report = Report()
        ident = candidate.identifier

        # Stage 1: register the intent (idea), then Stage 2: freeze.
        if not self.registry.exists(ident):
            self.registry.open_idea(ident, actor_role, timestamp)
        checksum = self.registry.freeze(ident, candidate.text, actor_role, timestamp)

        self.ledger.record(Decision(
            action="create_sample", actor_person=candidate.declaration.contributor,
            actor_role=actor_role, timestamp=timestamp, sample=ident,
            reason="candidate submitted",
            evidence_refs=[str(candidate.metadata.get("provenance_ref", ""))],
        ))

        # The firewall, before anything else can form an opinion.
        firewall = check_generation_firewall(candidate)
        report.extend(firewall)
        if not firewall.ok:
            self._reject(ident, actor_role, timestamp,
                         reason="X-1 generation firewall")
            result = IntakeResult(ident, False, report, firewall_triggered=True,
                                  state=State.REJECTED, checksum=checksum)
            if strict_firewall:
                raise FirewallBreach(str(firewall.errors[0]))
            return result

        # Evidence package must be complete and support its tier (§3.1 item 2).
        report.extend(validate_package(
            candidate.evidence,
            label=candidate.label or None,
            claimed_tier=candidate.evidence.tier,
        ))
        report.extend(verify_integrity(candidate.evidence, candidate.evidence_root))

        # Metadata conformance (Stage 4). A candidate MUST pass validation
        # before any human reviews it, so reviewer attention goes to judgment.
        sample = Sample(raw=dict(candidate.metadata), source_file="<intake>")
        report.extend(validate_sample(sample, registry=self.field_registry))

        if not report.ok:
            self._reject(ident, actor_role, timestamp,
                         reason="failed intake validation")
            return IntakeResult(ident, False, report, state=State.REJECTED,
                                checksum=checksum)

        self.registry.transition(ident, State.VALIDATED, actor_role, timestamp,
                                 reason="intake validation passed")
        return IntakeResult(ident, True, report, state=State.VALIDATED,
                            checksum=checksum)

    def _reject(self, identifier: str, actor_role: str, timestamp: str,
                reason: str) -> None:
        self.registry.reject(identifier, actor_role, timestamp, reason=reason)
        self.ledger.record(Decision(
            action="modify_metadata_pre_acceptance", actor_person="system",
            actor_role="system", timestamp=timestamp, sample=identifier,
            reason=f"rejected at intake: {reason}",
        ))
