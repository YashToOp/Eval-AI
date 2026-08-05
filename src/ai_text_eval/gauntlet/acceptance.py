"""The acceptance gate — Stage 7 (R-11, CAS §12, §13, §2).

§12 is conjunctive and says so: "A sample enters the corpus only if every
criterion below holds... there is no compensating excellence." §13 is
disqualifying regardless of merit. Together they are the last gate before a
sample becomes part of the corpus inventory.

**Three statuses, because two would lie.** A criterion is PASS, FAIL, or
UNCONFIRMED. The third exists because most of §12 is only partly mechanizable:
"the rationale is substantive", "the sample is famous or widely memorized",
"rights permit corpus distribution" — no function decides these, and several
others are blocked on governance decisions that have not been made (share caps
have no numbers, the rights vocabulary is not enumerated). A gate with only
PASS and FAIL would have to guess, and every guess would be in the permissive
direction, because that is the direction that lets work continue. UNCONFIRMED
blocks exactly like FAIL and reads differently in the report, which is the
point: "nobody has established this" is a different sentence from "this is
false", and a release manager needs to know which one they are looking at.

**Mechanization is classified per criterion, and the classification is
engineering judgment.** MECHANICAL criteria are computed from artifacts.
CONFIRMATION criteria are judgment and need a recorded human confirmation with
a basis. ASSISTED criteria are computed as far as the artifacts allow and then
still need a confirmation for the remainder. Which criterion falls where is a
statement about what code can decide, not about what the specification
requires — the specification requires all of them equally.

**A missing artifact is never a pass.** The dossier's fields are optional
because in practice they arrive at different stages, but an absent duplicate
screen makes A-6 UNCONFIRMED rather than clean. This is the same rule R-09
applies to absent reference corpora and R-10 applies to an undefined kappa,
and it is the rule that stops a gate from certifying its own ignorance.

**Confirmations require a basis.** A confirmation whose basis is empty is not
recorded as a confirmation, because "a maintainer ticked a box" is not what
§12 means by a criterion holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State

#: The state a candidate must be in to face the gate (§2: Stage 7 follows 6).
REQUIRED_STATE = State.REVIEWED


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCONFIRMED = "unconfirmed"


class Mechanization(str, Enum):
    MECHANICAL = "mechanical"
    ASSISTED = "assisted"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True)
class Criterion:
    """One §12 or §13 criterion and what it takes to decide it."""

    code: str
    summary: str
    mechanization: Mechanization
    note: str = ""

    @property
    def is_rejection(self) -> bool:
        return self.code.startswith("X-")


#: §12 acceptance criteria. `mechanization` records what code can decide.
ACCEPTANCE_CRITERIA: tuple[Criterion, ...] = (
    Criterion("A-1", "provenance tier admissible and fully supported by the "
              "evidence package", Mechanization.MECHANICAL),
    Criterion("A-2", "label mechanically derived and independently recomputed "
              "in provenance review", Mechanization.MECHANICAL),
    Criterion("A-3", "class-specific artifacts present and verified",
              Mechanization.MECHANICAL),
    Criterion("A-4", "metadata record passes validation including §4.4",
              Mechanization.MECHANICAL),
    Criterion("A-5", "exact word count falls inside the declared bucket",
              Mechanization.MECHANICAL),
    Criterion("A-6", "all six duplicate screens pass, or every flag is covered "
              "by a verified declared relationship", Mechanization.MECHANICAL),
    Criterion("A-7", "decontamination screening passes",
              Mechanization.MECHANICAL),
    Criterion("A-8", "PII status established and scrubbing documented in "
              "evidence", Mechanization.ASSISTED,
              "the status field is mechanical; that scrubbing is adequately "
              "documented is judgment"),
    Criterion("A-9", "rights recorded and permit corpus distribution",
              Mechanization.ASSISTED,
              "non-emptiness is mechanical; whether a value permits "
              "distribution needs the rights vocabulary (TD-G06)"),
    Criterion("A-10", "rationale substantive and specific; target weakness maps "
              "to the failure-mode registry", Mechanization.ASSISTED,
              "the registry mapping is mechanical; 'substantive and specific' "
              "is judgment"),
    Criterion("A-11", "two independent reviews complete, no unresolved "
              "objections, agreement holds, no COI breach",
              Mechanization.MECHANICAL),
    Criterion("A-12", "contributor declarations on file and no open provenance "
              "challenge", Mechanization.ASSISTED,
              "declarations are mechanical; there is no challenge register "
              "(TD-D20)"),
    Criterion("A-13", "author-share and session-share caps respected after "
              "inclusion", Mechanization.ASSISTED,
              "blocked on TD-G04: the caps have no numeric values"),
)

#: §13 rejection criteria. A rejection criterion PASSES when it does not apply.
REJECTION_CRITERIA: tuple[Criterion, ...] = (
    Criterion("X-1", "generative model involvement in HUMAN-class text",
              Mechanization.MECHANICAL),
    Criterion("X-2", "provenance not establishable at any admissible tier, or "
              "evidence incomplete after one revision cycle",
              Mechanization.MECHANICAL),
    Criterion("X-3", "label justification relies on stylistic judgment or on "
              "detector or model output", Mechanization.MECHANICAL),
    Criterion("X-4", "found hybrid, reconstructed process, or "
              "contributor-estimated origin shares", Mechanization.MECHANICAL),
    Criterion("X-5", "the text changed after freeze", Mechanization.MECHANICAL),
    Criterion("X-6", "undeclared similarity the contributor cannot resolve",
              Mechanization.MECHANICAL),
    Criterion("X-7", "famous, widely memorized, or previously published text "
              "presented as newly authored", Mechanization.CONFIRMATION,
              "a decontamination hit is evidence for this but not the whole "
              "rule; recognition is judgment"),
    Criterion("X-8", "PII cannot be reduced to clean, scrubbed or synthetic",
              Mechanization.ASSISTED),
    Criterion("X-9", "rights unclear, disputed, or incompatible with "
              "distribution", Mechanization.ASSISTED, "see A-9 / TD-G06"),
    Criterion("X-10", "content exclusions (harm, legal exposure)",
              Mechanization.CONFIRMATION),
    Criterion("X-11", "the sample's only virtue is defeating one detector "
              "implementation's bug", Mechanization.CONFIRMATION),
    Criterion("X-12", "share cap breach or conflict-of-interest violation",
              Mechanization.ASSISTED,
              "the COI half is mechanical from the ledger; the cap half is "
              "blocked on TD-G04"),
)

ALL_CRITERIA: tuple[Criterion, ...] = ACCEPTANCE_CRITERIA + REJECTION_CRITERIA
BY_CODE: dict[str, Criterion] = {c.code: c for c in ALL_CRITERIA}


# =====================================================================
# Confirmations
# =====================================================================

@dataclass(frozen=True)
class Confirmation:
    """A recorded human confirmation for a criterion code cannot decide."""

    criterion: str
    sample: str
    confirmed_by: str
    actor_role: str
    timestamp: str
    basis: str
    holds: bool = True

    @property
    def valid(self) -> bool:
        """A confirmation with no basis is not a confirmation.

        §12 asks whether a criterion holds, not whether someone said so.
        """
        return bool(self.basis.strip()) and self.criterion in BY_CODE

    def to_dict(self) -> dict:
        return {"criterion": self.criterion, "sample": self.sample,
                "confirmed_by": self.confirmed_by, "actor_role": self.actor_role,
                "timestamp": self.timestamp, "basis": self.basis,
                "holds": self.holds}


class ConfirmationRegister:
    """Recorded confirmations, keyed by (sample, criterion)."""

    def __init__(self):
        self._entries: dict[tuple[str, str], Confirmation] = {}
        self._rejected: list[Confirmation] = []

    def record(self, confirmation: Confirmation) -> Report:
        r = Report(checked=1)
        if confirmation.criterion not in BY_CODE:
            r.error("CAS 12", "UNKNOWN_CRITERION",
                    f"{confirmation.criterion!r} is not a §12 or §13 criterion",
                    confirmation.sample)
            self._rejected.append(confirmation)
            return r
        if not confirmation.basis.strip():
            r.error("CAS 12", "CONFIRMATION_WITHOUT_BASIS",
                    f"confirmation of {confirmation.criterion} records no basis; "
                    "§12 asks whether the criterion holds, not whether someone "
                    "said so", confirmation.sample)
            self._rejected.append(confirmation)
            return r
        self._entries[(confirmation.sample, confirmation.criterion)] = confirmation
        return r

    def get(self, sample: str, criterion: str) -> Confirmation | None:
        return self._entries.get((sample, criterion))

    def for_sample(self, sample: str) -> list[Confirmation]:
        return [c for (s, _), c in sorted(self._entries.items()) if s == sample]


# =====================================================================
# The dossier
# =====================================================================

@dataclass
class Dossier:
    """Everything the gate reads about one sample.

    Every field is optional because artifacts arrive at different stages —
    but absent is UNCONFIRMED, never PASS.
    """

    sample: str
    metadata: dict | None = None
    #: Reports produced by earlier stages.
    validation: Report | None = None
    evidence: Report | None = None
    firewall: Report | None = None
    derivation: Report | None = None
    #: Screen results (R-08 / R-09).
    duplicates: object | None = None
    decontamination: object | None = None
    #: Review artifacts (R-10).
    review_round: object | None = None
    agreement: dict | None = None
    #: Intake artifacts.
    declarations: list | None = None
    text_verified: bool | None = None
    #: Cross-cutting.
    ledger: DecisionLedger | None = None
    failure_mode_registry: dict | None = None


@dataclass
class CriterionResult:
    code: str
    status: Status
    message: str
    mechanization: Mechanization

    @property
    def blocks(self) -> bool:
        return self.status is not Status.PASS


@dataclass
class AcceptanceResult:
    sample: str
    results: list[CriterionResult] = field(default_factory=list)
    report: Report = field(default_factory=Report)

    def by_code(self) -> dict[str, CriterionResult]:
        return {r.code: r for r in self.results}

    @property
    def failed(self) -> list[CriterionResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def unconfirmed(self) -> list[CriterionResult]:
        return [r for r in self.results if r.status is Status.UNCONFIRMED]

    @property
    def accepted(self) -> bool:
        """§12 is conjunctive: "there is no compensating excellence"."""
        return not self.failed and not self.unconfirmed

    def to_dict(self) -> dict:
        return {"sample": self.sample, "accepted": self.accepted,
                "failed": [r.code for r in self.failed],
                "unconfirmed": [r.code for r in self.unconfirmed],
                "criteria": [{"code": r.code, "status": r.status.value,
                              "message": r.message} for r in self.results]}


# =====================================================================
# Evaluation
# =====================================================================

def _report_has(report: Report | None, *codes: str) -> bool:
    if report is None:
        return False
    present = {f.code for f in report.findings}
    return bool(present & set(codes))


def _from_report(report: Report | None, missing: str) -> tuple[Status, str]:
    """PASS if the report is clean, FAIL if it errored, UNCONFIRMED if absent."""
    if report is None:
        return Status.UNCONFIRMED, missing
    if report.errors:
        return Status.FAIL, "; ".join(sorted({f.code for f in report.errors}))
    return Status.PASS, "clean"


def _confirmed(register: ConfirmationRegister, sample: str, code: str,
               mechanical: tuple[Status, str] | None = None) -> tuple[Status, str]:
    """Fold a recorded confirmation into a criterion's status.

    A mechanical FAIL is never rescued by a confirmation — §13 rejections are
    disqualifying "regardless of other merits", and a human saying otherwise
    about a computed fact is not evidence.
    """
    if mechanical and mechanical[0] is Status.FAIL:
        return mechanical
    entry = register.get(sample, code)
    if entry is None:
        # Keep the mechanical explanation. *Why* a criterion could not be
        # decided — a governance gap, a missing artifact — is the part a
        # release manager acts on; "no recorded confirmation" alone says only
        # that nobody signed, not what they would have had to establish.
        if mechanical:
            return Status.UNCONFIRMED, f"{mechanical[1]}; no recorded confirmation"
        return Status.UNCONFIRMED, "no recorded confirmation"
    if not entry.holds:
        return Status.FAIL, f"confirmed as not holding: {entry.basis}"
    return Status.PASS, f"confirmed by {entry.confirmed_by}: {entry.basis}"


def evaluate(dossier: Dossier,
             confirmations: ConfirmationRegister | None = None) -> AcceptanceResult:
    """Evaluate every §12 and §13 criterion for one sample."""
    register = confirmations or ConfirmationRegister()
    sample = dossier.sample
    md = dossier.metadata or {}
    out: list[CriterionResult] = []

    def add(code: str, status: Status, message: str) -> None:
        out.append(CriterionResult(code, status, message, BY_CODE[code].mechanization))

    # -- §12 -------------------------------------------------------------

    add("A-1", *_from_report(dossier.evidence, "no evidence report on file"))
    add("A-2", *_from_report(dossier.derivation,
                             "derivations were not independently recomputed"))

    if dossier.evidence is None or dossier.metadata is None:
        add("A-3", Status.UNCONFIRMED, "evidence report or metadata absent")
    elif _report_has(dossier.evidence, "T1_WRONG_EVIDENCE_KIND",
                     "GENERATION_RECORD_INCOMPLETE", "TRANSFORM_RECORD_MISSING"):
        add("A-3", Status.FAIL, "class-specific artifacts missing or unverified")
    else:
        add("A-3", Status.PASS, "class-specific artifacts present")

    add("A-4", *_from_report(dossier.validation, "no validation report on file"))

    if dossier.validation is None:
        add("A-5", Status.UNCONFIRMED, "no validation report on file")
    elif _report_has(dossier.validation, "LENGTH_MISMATCH", "BUCKET_MISMATCH"):
        add("A-5", Status.FAIL, "word count outside the declared bucket")
    else:
        add("A-5", Status.PASS, "word count inside the declared bucket")

    dup = dossier.duplicates
    if dup is None:
        add("A-6", Status.UNCONFIRMED, "the duplicate screen has not been run")
    elif getattr(dup, "report", None) is not None and dup.report.errors:
        add("A-6", Status.FAIL,
            "; ".join(sorted({f.code for f in dup.report.errors})))
    else:
        add("A-6", Status.PASS, "all six screens clear or covered by declaration")

    decon = dossier.decontamination
    verdict = getattr(getattr(decon, "verdict", None), "value", None)
    if decon is None:
        add("A-7", Status.UNCONFIRMED, "decontamination has not been run")
    elif verdict == "contaminated":
        add("A-7", Status.FAIL, "contaminated against a reference corpus")
    elif verdict != "clean":
        add("A-7", Status.UNCONFIRMED,
            "the scan was incomplete, so cleanliness is unknown rather than "
            "established (TD-X01)")
    else:
        add("A-7", Status.PASS, "clean against every required source")

    # A field this code never received is unknown; a field it received empty
    # is a stated absence. Collapsing the two would report "no licence
    # recorded" about a dossier that simply arrived without metadata.
    have_metadata = dossier.metadata is not None

    pii = md.get("pii_status")
    if not have_metadata:
        pii_mechanical = (Status.UNCONFIRMED, "no metadata on file")
    else:
        pii_mechanical = ((Status.PASS, f"pii_status={pii!r}") if pii
                          else (Status.UNCONFIRMED, "pii_status is not set"))
    add("A-8", *_confirmed(register, sample, "A-8", pii_mechanical))

    rights = md.get("license")
    if not have_metadata:
        rights_mechanical = (Status.UNCONFIRMED, "no metadata on file")
    elif rights:
        rights_mechanical = (Status.UNCONFIRMED,
                             f"license={rights!r} recorded, but whether it "
                             "permits distribution needs the rights vocabulary "
                             "(TD-G06)")
    else:
        rights_mechanical = (Status.FAIL, "no license recorded")
    add("A-9", *_confirmed(register, sample, "A-9", rights_mechanical))

    weakness = (md.get("target_weakness") or "").strip()
    registry = dossier.failure_mode_registry
    if not have_metadata:
        a10 = (Status.UNCONFIRMED, "no metadata on file")
    elif not weakness:
        a10 = (Status.FAIL, "target_weakness is empty")
    elif registry is None:
        a10 = (Status.UNCONFIRMED, "no failure-mode registry supplied")
    elif weakness not in registry and not any(
            weakness in str(v) for v in registry.values()):
        a10 = (Status.UNCONFIRMED,
               "target_weakness does not map to a failure-mode entry; §12 "
               "allows a registered proposal for a new entry, which is a "
               "recorded decision rather than a computed one")
    else:
        a10 = (Status.UNCONFIRMED, "mapping holds; 'substantive and specific' "
                                   "is a judgment that needs confirming")
    add("A-10", *_confirmed(register, sample, "A-10", a10))

    round_ = dossier.review_round
    if round_ is None:
        add("A-11", Status.UNCONFIRMED, "no review round on file")
    elif not getattr(round_, "complete", False):
        add("A-11", Status.FAIL, "the review round is incomplete")
    elif getattr(round_, "outcome", lambda: None)() is None:
        add("A-11", Status.FAIL, "an unresolved reviewer disagreement remains")
    elif dossier.agreement is None:
        add("A-11", Status.UNCONFIRMED,
            "reviews are complete but batch agreement was not measured")
    elif not all(getattr(a, "passes", False) for a in dossier.agreement.values()):
        unmet = sorted(name for name, a in dossier.agreement.items()
                       if not getattr(a, "passes", False))
        add("A-11", Status.FAIL, f"agreement not met for {unmet}")
    else:
        add("A-11", Status.PASS, "reviews complete, agreement holds")

    if dossier.declarations is None:
        decl_mechanical = (Status.UNCONFIRMED, "no declaration record supplied")
    elif dossier.declarations:
        decl_mechanical = (Status.UNCONFIRMED,
                           "declarations on file; an open provenance challenge "
                           "cannot be checked without a challenge register "
                           "(TD-D20)")
    else:
        decl_mechanical = (Status.FAIL, "no contributor declarations on file")
    add("A-12", *_confirmed(register, sample, "A-12", decl_mechanical))

    caps_mechanical = (Status.UNCONFIRMED,
                       "share caps have no numeric values (TD-G04), so "
                       "compliance cannot be computed")
    add("A-13", *_confirmed(register, sample, "A-13", caps_mechanical))

    # -- §13: a rejection criterion PASSES when it does not apply ---------

    if dossier.firewall is None:
        add("X-1", Status.UNCONFIRMED, "the firewall has not been run")
    elif dossier.firewall.errors:
        add("X-1", Status.FAIL, "generative model involvement in HUMAN text")
    else:
        add("X-1", Status.PASS, "does not apply")

    if dossier.evidence is None:
        add("X-2", Status.UNCONFIRMED, "no evidence report on file")
    elif _report_has(dossier.evidence, "TIER_UNSUPPORTED", "TIER_OVERCLAIM",
                     "EVIDENCE_PACKAGE_EMPTY"):
        add("X-2", Status.FAIL, "provenance not establishable at a usable tier")
    else:
        add("X-2", Status.PASS, "does not apply")

    stylistic = _report_has(dossier.evidence, "INADMISSIBLE_EVIDENCE")
    appearance = any(getattr(r, "appearance_opinion", "")
                     for r in getattr(round_, "reviews", []) or [])
    if dossier.evidence is None and round_ is None:
        add("X-3", Status.UNCONFIRMED, "no evidence report or review on file")
    elif stylistic or appearance:
        add("X-3", Status.FAIL,
            "the justification rests on stylistic judgment or detector output (P3)")
    else:
        add("X-3", Status.PASS, "does not apply")

    if dossier.derivation is None:
        add("X-4", Status.UNCONFIRMED, "derivations were not recomputed")
    elif _report_has(dossier.derivation, "SHARE_NOT_DERIVED", "SPAN_MAP_NOT_DERIVED",
                     "LABEL_NOT_DERIVED", "CHAIN_REPLAY_MISMATCH"):
        add("X-4", Status.FAIL, "estimated shares or a reconstructed process")
    else:
        add("X-4", Status.PASS, "does not apply")

    if dossier.text_verified is None:
        add("X-5", Status.UNCONFIRMED, "the frozen text was not re-verified")
    elif not dossier.text_verified:
        add("X-5", Status.FAIL, "the text changed after freeze")
    else:
        add("X-5", Status.PASS, "does not apply")

    if dup is None:
        add("X-6", Status.UNCONFIRMED, "the duplicate screen has not been run")
    elif _report_has(getattr(dup, "report", None), "NEAR_DUPLICATE_UNDECLARED",
                     "EXACT_DUPLICATE", "HIDDEN_SIMILARITY_TO_PUBLIC"):
        add("X-6", Status.FAIL, "undeclared similarity remains unresolved")
    else:
        add("X-6", Status.PASS, "does not apply")

    add("X-7", *_confirmed(register, sample, "X-7"))

    if not have_metadata:
        x8 = (Status.UNCONFIRMED, "no metadata on file")
    elif md.get("pii_status") in {"dirty", "unknown", None}:
        x8 = (Status.FAIL, f"pii_status={md.get('pii_status')!r}")
    else:
        x8 = (Status.PASS, "does not apply")
    add("X-8", *_confirmed(register, sample, "X-8", x8))
    add("X-9", *_confirmed(register, sample, "X-9",
                           (Status.UNCONFIRMED, "see A-9 / TD-G06")))
    add("X-10", *_confirmed(register, sample, "X-10"))
    add("X-11", *_confirmed(register, sample, "X-11"))

    coi = None
    if dossier.ledger is not None:
        conflicts = [f for f in dossier.ledger.audit_conflicts().findings
                     if f.sample_id == sample]
        coi = ((Status.FAIL, "a §11.7 role conflict is recorded against this "
                             "sample") if conflicts else None)
    x12 = coi or (Status.UNCONFIRMED,
                  "the COI half is clear; the share-cap half is blocked on "
                  "TD-G04")
    if dossier.ledger is None:
        x12 = (Status.UNCONFIRMED, "no ledger supplied, so COI was not checked")
    add("X-12", *_confirmed(register, sample, "X-12", x12))

    # -- assemble ---------------------------------------------------------

    result = AcceptanceResult(sample=sample, results=out)
    r = result.report
    r.checked = len(out)
    for entry in out:
        if entry.status is Status.FAIL:
            r.error("CAS 12" if not BY_CODE[entry.code].is_rejection else "CAS 13",
                    f"{entry.code}_FAILED", entry.message, sample)
        elif entry.status is Status.UNCONFIRMED:
            r.error("CAS 12", f"{entry.code}_UNCONFIRMED",
                    f"{entry.message}; §12 is conjunctive with no compensating "
                    "excellence, so an unestablished criterion blocks exactly "
                    "like a failed one", sample)
    return result


# =====================================================================
# The desk (Stage 7)
# =====================================================================

class AcceptanceDesk:
    """Runs CAS §2 Stage 7 for one candidate (REVIEWED → ACCEPTED)."""

    def __init__(self, registry: IdentifierRegistry, ledger: DecisionLedger,
                 confirmations: ConfirmationRegister | None = None):
        self.registry = registry
        self.ledger = ledger
        self.confirmations = confirmations or ConfirmationRegister()

    def confirm(self, confirmation: Confirmation) -> Report:
        """Record one criterion confirmation (§12, §14.2)."""
        report = self.confirmations.record(confirmation)
        if report.ok:
            report.extend(self.ledger.record(Decision(
                action="confirm_acceptance", actor_person=confirmation.confirmed_by,
                actor_role=confirmation.actor_role, timestamp=confirmation.timestamp,
                sample=confirmation.sample,
                reason=f"{confirmation.criterion} "
                       f"{'holds' if confirmation.holds else 'does not hold'}: "
                       f"{confirmation.basis}")))
        return report

    def decide(self, dossier: Dossier, *, maintainer: str, timestamp: str,
               actor_role: str = "maintainer") -> AcceptanceResult:
        """Evaluate the gate and move the sample.

        Acceptance transitions to ACCEPTED. Anything else leaves the sample
        REVIEWED: §12 failures are not automatically terminal, because §6.5
        allows a metadata revision to cure several of them, and rejection here
        burns the identifier forever (§9.5). Only §13 criteria that have
        actually been *established* as applying are terminal.
        """
        state = self.registry.state_of(dossier.sample)
        if state is not REQUIRED_STATE:
            raise LifecycleError(
                f"{dossier.sample} is {state.value if state else 'unregistered'}; "
                f"Stage 7 acceptance requires {REQUIRED_STATE.value}")

        result = evaluate(dossier, self.confirmations)

        if result.accepted:
            self.registry.transition(dossier.sample, State.ACCEPTED, actor_role,
                                     timestamp, reason="§12 acceptance gate passed")
            self.ledger.record(Decision(
                action="confirm_acceptance", actor_person=maintainer,
                actor_role=actor_role, timestamp=timestamp, sample=dossier.sample,
                reason="every §12 criterion holds and no §13 criterion applies"))
            return result

        established_rejections = [r.code for r in result.failed
                                  if BY_CODE[r.code].is_rejection]
        if established_rejections:
            self.registry.reject(
                dossier.sample, actor_role, timestamp,
                reason=f"§13 {', '.join(established_rejections)}")
        return result
