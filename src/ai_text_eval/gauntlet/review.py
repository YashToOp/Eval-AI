"""Reviewer QA — Stage 6 (R-10, CAS §6, BS §4.6, §9.1(c)).

Stage 6 is where human judgment enters a system that has spent five stages
keeping it out. The design problem is therefore not "how do reviewers record
opinions" but "which opinions are admissible, and how is their agreement
measured honestly".

**Two mandates, and they are not interchangeable** (§6.1). Provenance review
audits the evidence: tiers, completeness, checksums, coherent dates, and
mechanical derivations recomputed and matching. Content review checks
everything else. TEST/HIDDEN-eligible material gets both, independently;
DEV-only material gets one combined review.

**The provenance reviewer must not form an opinion about the text.** §6.1 is
explicit that judging whether prose "seems" consistent with its label is
inadmissible under P3, and that such an opinion appearing in a review "is
itself a review defect". So `Review.appearance_opinion` exists precisely so
that recording one is *detectable* — a schema with nowhere to put it would
push the same opinion into free-text notes where nothing can see it.

**Independence is enforced by withholding, not by asking** (§6.1: "neither
reviewer sees the other's conclusions before submitting their own"). A round
reveals nothing until it is complete. A system that merely instructed
reviewers not to peek would be recording a promise, not a property.

**Agreement is measured, and an undefined kappa is not a passing kappa.**
Cohen's kappa is undefined when both reviewers used exactly one category for
everything — the classic paradox, where perfect observed agreement yields
0/0. Returning 1.0 there would report the *least* informative batch as the
best one. `agreement()` returns `None` with a stated reason, and the gate
treats it as unmeasured rather than passed.

**What this module does not do.** It does not decide acceptance: §12/§13 are
the acceptance criteria and belong to R-11. Stage 6's output is a
recommendation, a revision request, or a rejection with recorded reasons.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State
from ai_text_eval.gauntlet.registry import FieldRegistry, load_field_registry

#: Splits whose candidates require two independent reviews (§6.1). DEV-only
#: material receives one combined review.
DUAL_REVIEW_SPLITS = frozenset({"test", "hidden"})

#: BS §4.6 and CAS §6.3: "Cohen's kappa >= 0.8".
KAPPA_THRESHOLD = 0.8

#: §6.4: "when a requested metadata revision cycle fails twice".
MAX_REVISION_CYCLES = 2

#: The state a candidate must be in to be reviewed (§2: Stage 6 follows 5).
REQUIRED_STATE = State.SCREENED

#: Ledger actions for each mandate (§14.1).
LEDGER_ACTION = {
    "provenance": "review_provenance",
    "content": "review_content",
    "combined": "review_content",
}


class Mandate(str, Enum):
    PROVENANCE = "provenance"
    CONTENT = "content"
    COMBINED = "combined"


class Recommendation(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"   # metadata only; text is frozen (§6.5)
    REJECT = "reject"


class ReviewError(RuntimeError):
    """Raised when a review cannot be accepted into a round at all."""


# =====================================================================
# Judgment fields (BS §4.6, CAS §6.3)
# =====================================================================

def judgment_fields(registry: FieldRegistry | None = None) -> dict[str, list[str]]:
    """The judgment-field mapping, read from the registry rather than declared.

    Neither specification enumerates these by name — BS §4.6 names them by
    description ("register tags, difficulty, PII checks, quality screening").
    The mapping is therefore governed data carrying an unratified
    interpretation (TD-G12), not a constant in this file, so that changing it
    is a registry decision with a recorded basis.
    """
    reg = registry or load_field_registry()
    block = reg.raw.get("judgment_fields", {})
    return {"categorical": list(block.get("categorical", [])),
            "free_text": list(block.get("free_text", []))}


# =====================================================================
# Reviews and rounds
# =====================================================================

@dataclass
class Review:
    """One reviewer's independent conclusion on one sample."""

    sample: str
    reviewer: str
    mandate: Mandate
    recommendation: Recommendation
    timestamp: str
    #: Judgment-field values this reviewer assigned. Kappa is computed across
    #: a batch from these.
    judgments: dict[str, object] = field(default_factory=dict)
    notes: str = ""
    #: §6.1/P3: an opinion about whether the text "seems" consistent with its
    #: label. Present in the schema so that recording one is detectable — see
    #: the module docstring.
    appearance_opinion: str = ""
    #: Findings the reviewer raised (used for calibration scoring).
    raised_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"sample": self.sample, "reviewer": self.reviewer,
                "mandate": self.mandate.value,
                "recommendation": self.recommendation.value,
                "timestamp": self.timestamp, "judgments": dict(self.judgments),
                "notes": self.notes,
                "appearance_opinion": self.appearance_opinion,
                "raised_codes": list(self.raised_codes)}


@dataclass
class Adjudication:
    """A third senior reviewer's decision on a disagreement (§6.2)."""

    sample: str
    adjudicator: str
    recommendation: Recommendation
    reasoning: str
    timestamp: str
    judgments: dict[str, object] = field(default_factory=dict)


class ReviewRound:
    """The independent reviews of one sample (§6.1, §6.2).

    Conclusions are withheld until the round is complete, so independence is a
    property of the object rather than an instruction to its users.
    """

    def __init__(self, sample: str, split: str | None,
                 registry: FieldRegistry | None = None):
        self.sample = sample
        self.split = (split or "").casefold() or None
        self.reviews: list[Review] = []
        self.adjudication: Adjudication | None = None
        self._fields = judgment_fields(registry)

    # -- shape of the round ---------------------------------------------

    @property
    def dual(self) -> bool:
        """§6.1: TEST/HIDDEN eligibility requires two independent reviews."""
        return self.split in DUAL_REVIEW_SPLITS

    @property
    def required_mandates(self) -> tuple[Mandate, ...]:
        if self.dual:
            return (Mandate.PROVENANCE, Mandate.CONTENT)
        return (Mandate.COMBINED,)

    @property
    def complete(self) -> bool:
        have = {r.mandate for r in self.reviews}
        return all(m in have for m in self.required_mandates)

    # -- submission ------------------------------------------------------

    def submit(self, review: Review) -> Report:
        """Record one review. Returns findings about the review itself."""
        r = Report(checked=1)
        if review.sample != self.sample:
            raise ReviewError(
                f"review is for {review.sample!r}, round is for {self.sample!r}")
        if review.mandate not in self.required_mandates:
            raise ReviewError(
                f"{self.split or 'unassigned'} material requires "
                f"{[m.value for m in self.required_mandates]}; got "
                f"{review.mandate.value}")
        if any(x.mandate is review.mandate for x in self.reviews):
            raise ReviewError(
                f"{review.mandate.value} review already submitted for "
                f"{self.sample}; a second one would not be independent")
        if any(x.reviewer == review.reviewer for x in self.reviews):
            r.error("CAS 6.1", "SAME_REVIEWER_BOTH_MANDATES",
                    f"{review.reviewer!r} already reviewed {self.sample} under "
                    "another mandate; two reviews by one person are not two "
                    "independent reviews", self.sample)

        # §6.1 / P3: appearance opinions are inadmissible, and their presence
        # in a review is itself a review defect.
        if review.appearance_opinion:
            severity = (r.error if review.mandate is Mandate.PROVENANCE else r.warn)
            severity("CAS 6.1", "APPEARANCE_OPINION_IN_REVIEW",
                     f"{review.reviewer!r} recorded an opinion about whether the "
                     "text seems consistent with its label; such opinions are "
                     "inadmissible (P3) and their appearance in a review is "
                     "itself a review defect", self.sample)

        unknown = set(review.judgments) - set(self._fields["categorical"]) \
            - set(self._fields["free_text"])
        if unknown:
            r.warn("CAS 6.3", "UNKNOWN_JUDGMENT_FIELD",
                   f"review records judgments on {sorted(unknown)}, which the "
                   "registry does not list as judgment fields (TD-G12)",
                   self.sample)

        self.reviews.append(review)
        return r

    # -- independence ----------------------------------------------------

    def conclusions_for(self, reviewer: str) -> list[Review]:
        """What `reviewer` may see. Nothing, until the round is complete.

        §6.1: "neither reviewer sees the other's conclusions before submitting
        their own." Withholding is the enforcement; asking would not be.
        """
        if not self.complete:
            return []
        return [r for r in self.reviews if r.reviewer != reviewer]

    # -- disagreement (§6.2) ---------------------------------------------

    def disagreements(self) -> list[str]:
        """Judgment fields, or the recommendation, the reviewers differ on."""
        if len(self.reviews) < 2:
            return []
        out: list[str] = []
        first, second = self.reviews[0], self.reviews[1]
        if first.recommendation is not second.recommendation:
            out.append("recommendation")
        for name in self._fields["categorical"] + self._fields["free_text"]:
            if name in first.judgments and name in second.judgments:
                if first.judgments[name] != second.judgments[name]:
                    out.append(name)
        return out

    def needs_adjudication(self, contributor_metadata: dict | None = None) -> bool:
        """§6.2: reviewers disagreeing with each other *or* with the
        contributor's metadata both route to adjudication."""
        if self.disagreements():
            return True
        if contributor_metadata:
            for review in self.reviews:
                for name, value in review.judgments.items():
                    if name in contributor_metadata and contributor_metadata[name] != value:
                        return True
        return False

    def adjudicate(self, adjudication: Adjudication) -> Report:
        r = Report(checked=1)
        if adjudication.adjudicator in {x.reviewer for x in self.reviews}:
            r.error("CAS 6.2", "ADJUDICATOR_ALREADY_REVIEWED",
                    f"{adjudication.adjudicator!r} already reviewed "
                    f"{self.sample}; §6.2 requires a *third* senior reviewer",
                    self.sample)
        if not adjudication.reasoning.strip():
            r.error("CAS 6.2", "ADJUDICATION_REASONING_MISSING",
                    "the adjudicator's decision and reasoning are both "
                    "recorded; a decision without reasoning cannot be reviewed "
                    "for recurring ambiguity (§6.2)", self.sample)
        self.adjudication = adjudication
        return r

    # -- outcome ---------------------------------------------------------

    def outcome(self) -> Recommendation | None:
        """The round's conclusion, or None while it is unresolved."""
        if not self.complete:
            return None
        if self.adjudication is not None:
            return self.adjudication.recommendation
        if self.disagreements():
            return None   # unresolved until adjudicated
        return self.reviews[0].recommendation


# =====================================================================
# Agreement measurement (§6.3, BS §4.6)
# =====================================================================

@dataclass
class AgreementResult:
    """Cohen's kappa for one judgment field over one batch."""

    field_name: str
    n: int
    observed_agreement: float
    kappa: float | None
    undefined_reason: str = ""

    @property
    def measured(self) -> bool:
        return self.kappa is not None

    @property
    def passes(self) -> bool:
        """Unmeasured is not passing. An undefined kappa says the batch was
        uninformative, not that it was good."""
        return self.kappa is not None and self.kappa >= KAPPA_THRESHOLD

    def to_dict(self) -> dict:
        return {"field": self.field_name, "n": self.n,
                "observed_agreement": round(self.observed_agreement, 6),
                "kappa": None if self.kappa is None else round(self.kappa, 6),
                "undefined_reason": self.undefined_reason,
                "passes": self.passes}


def cohens_kappa(pairs: list[tuple[object, object]]) -> tuple[float | None, float, str]:
    """Cohen's kappa for two raters over categorical judgments.

    Returns `(kappa, observed_agreement, undefined_reason)`. Kappa is `None`
    when it is genuinely undefined — chance agreement of exactly 1.0, which
    happens when both raters used a single identical category throughout.
    Reporting 1.0 there would rank the least informative possible batch as the
    best one; reporting 0.0 would call perfect agreement worthless. Neither is
    true, so neither is returned.
    """
    n = len(pairs)
    if n == 0:
        return None, 0.0, "no dual-annotated items"

    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n

    first = Counter(a for a, _ in pairs)
    second = Counter(b for _, b in pairs)
    categories = set(first) | set(second)
    pe = sum((first[c] / n) * (second[c] / n) for c in categories)

    if pe >= 1.0:
        return None, po, ("chance agreement is 1.0: both reviewers used a "
                          "single identical category for every item, so kappa "
                          "is 0/0 and the batch carries no information about "
                          "agreement")
    return (po - pe) / (1 - pe), po, ""


def agreement(pairs_by_field: dict[str, list[tuple[object, object]]]) -> dict[str, AgreementResult]:
    """Kappa per judgment field for one batch (§6.3)."""
    out: dict[str, AgreementResult] = {}
    for name, pairs in pairs_by_field.items():
        kappa, po, reason = cohens_kappa(pairs)
        out[name] = AgreementResult(field_name=name, n=len(pairs),
                                    observed_agreement=po, kappa=kappa,
                                    undefined_reason=reason)
    return out


def agreement_gate(results: dict[str, AgreementResult],
                   registry: FieldRegistry | None = None) -> Report:
    """§6.3: a batch below threshold is re-reviewed after a calibration
    session, and "does not ship on schedule pressure".

    Also reports what was *not* measured. A batch whose kappa is undefined,
    or whose judgment fields were never dual-annotated, has not demonstrated
    agreement — which is a different statement from failing to reach 0.8, and
    the gate says which one it is.
    """
    r = Report(checked=len(results))
    expected = judgment_fields(registry)["categorical"]

    for name in expected:
        if name not in results:
            r.error("CAS 6.3", "FIELD_NOT_DUAL_ANNOTATED",
                    f"{name!r} is a judgment field but carries no "
                    "dual-annotated items in this batch; §6.3 requires kappa "
                    "per field per batch", location=name)

    for name, result in sorted(results.items()):
        if result.kappa is None:
            r.error("CAS 6.3", "KAPPA_UNDEFINED",
                    f"{name!r}: {result.undefined_reason}; an undefined kappa "
                    "is unmeasured agreement, not passing agreement",
                    location=name)
        elif result.kappa < KAPPA_THRESHOLD:
            r.error("CAS 6.3", "KAPPA_BELOW_THRESHOLD",
                    f"{name!r}: kappa {result.kappa:.3f} < {KAPPA_THRESHOLD} "
                    f"over {result.n} items; the batch is re-reviewed after a "
                    "calibration session and does not ship on schedule "
                    "pressure", location=name)

    r.warn("CAS 6.3", "KAPPA_BATCH_SIZE_NOT_GOVERNED",
           "neither specification sets a minimum batch size for kappa, so a "
           "pass over very few items is reported at face value; item counts "
           "accompany every result (TD-G13)")
    return r


# =====================================================================
# Reviewer integrity (§6.6)
# =====================================================================

#: Roles whose holder produced the sample and therefore MUST NOT review it.
PRODUCER_ROLES = frozenset({"contributor", "generation_operator"})


def check_reviewer_eligibility(ledger: DecisionLedger, sample: str, reviewer: str,
                               declared_interests: dict[str, set[str]] | None = None,
                               detectors_under_evaluation: set[str] | None = None) -> Report:
    """§6.6: producers, session operators, and conflicted parties MUST NOT
    review.

    Run *before* recording a review. The ledger catches the first two after
    the fact (§11.7), but a review that should never have happened is better
    prevented than audited.
    """
    r = Report(checked=1)

    for event in ledger.for_sample(sample):
        if event.get("actor_person") != reviewer:
            continue
        if event.get("actor_role") in PRODUCER_ROLES:
            r.error("CAS 6.6", "REVIEWER_PRODUCED_THE_SAMPLE",
                    f"{reviewer!r} acted as {event['actor_role']} on {sample} "
                    "and MUST NOT review it", sample)
            break

    interests = (declared_interests or {}).get(reviewer, set())
    under_evaluation = detectors_under_evaluation or set()
    conflicted = set(interests) & set(under_evaluation)
    if conflicted:
        r.error("CAS 6.6", "REVIEWER_HAS_DECLARED_DETECTOR_INTEREST",
                f"{reviewer!r} has a declared interest in "
                f"{sorted(conflicted)}, which is under evaluation on the "
                "affected cells", sample)

    if declared_interests is None:
        r.warn("CAS 6.6", "DECLARED_INTERESTS_NOT_SUPPLIED",
               "no declared-interest register was supplied, so the detector "
               "conflict-of-interest arm of §6.6 was not checked; absence of "
               "a finding here is not absence of a conflict (TD-D19)", sample)

    return r


@dataclass(frozen=True)
class SeededDefect:
    """A known defect planted in a calibration exercise (§6.6)."""

    sample: str
    code: str
    description: str = ""


@dataclass
class CalibrationExercise:
    """A periodic exercise containing seeded known-defect candidates (§6.6)."""

    name: str
    version: str
    seeded: list[SeededDefect] = field(default_factory=list)

    def score(self, reviewer: str, raised: dict[str, list[str]]) -> "CalibrationResult":
        """Score one reviewer's findings against the seeded defects.

        `raised` maps sample identifier to the codes that reviewer raised.
        """
        caught, missed = [], []
        for defect in self.seeded:
            if defect.code in raised.get(defect.sample, []):
                caught.append(defect)
            else:
                missed.append(defect)
        return CalibrationResult(exercise=self.name, version=self.version,
                                 reviewer=reviewer, caught=caught, missed=missed)


@dataclass
class CalibrationResult:
    exercise: str
    version: str
    reviewer: str
    caught: list[SeededDefect] = field(default_factory=list)
    missed: list[SeededDefect] = field(default_factory=list)

    @property
    def seeded_total(self) -> int:
        return len(self.caught) + len(self.missed)

    @property
    def catch_rate(self) -> float | None:
        """None when nothing was seeded — a rate over zero defects is not 1.0."""
        if not self.seeded_total:
            return None
        return len(self.caught) / self.seeded_total


@dataclass
class ReviewerRecord:
    """Reviewer performance, part of the audit trail (§6.6)."""

    reviewer: str
    results: list[CalibrationResult] = field(default_factory=list)

    def add(self, result: CalibrationResult) -> None:
        self.results.append(result)

    @property
    def exercises_taken(self) -> int:
        return len(self.results)

    @property
    def exercises_with_misses(self) -> int:
        return sum(1 for r in self.results if r.missed)

    def retraining_report(self, threshold: int | None = None) -> Report:
        """§6.6: "a reviewer who passes seeded defects repeatedly is retrained".

        "Repeatedly" carries no number, so none is invented: without a
        governed threshold this reports the record and says the rule cannot be
        applied (TD-G14).
        """
        r = Report(checked=self.exercises_taken)
        if threshold is None:
            r.warn("CAS 6.6", "RETRAINING_THRESHOLD_UNSET",
                   f"{self.reviewer!r} missed seeded defects in "
                   f"{self.exercises_with_misses} of {self.exercises_taken} "
                   "exercises; §6.6 requires retraining after 'repeatedly' but "
                   "sets no number, so the rule cannot be applied mechanically "
                   "(TD-G14)", location=self.reviewer)
        elif self.exercises_with_misses >= threshold:
            r.error("CAS 6.6", "REVIEWER_REQUIRES_RETRAINING",
                    f"{self.reviewer!r} missed seeded defects in "
                    f"{self.exercises_with_misses} exercises (threshold "
                    f"{threshold}); retrain before reviewing further",
                    location=self.reviewer)
        return r


# =====================================================================
# Metadata revision tracking (§6.5, §6.4)
# =====================================================================

@dataclass(frozen=True)
class Revision:
    """One pre-acceptance metadata change (§6.5)."""

    sample: str
    field_name: str
    old_value: object
    new_value: object
    reason: str
    actor_role: str
    timestamp: str

    def to_dict(self) -> dict:
        return {"sample": self.sample, "field": self.field_name,
                "old_value": self.old_value, "new_value": self.new_value,
                "reason": self.reason, "actor_role": self.actor_role,
                "timestamp": self.timestamp}


class RevisionLog:
    """Pre-acceptance metadata revisions, and the §6.4 two-cycle limit."""

    def __init__(self):
        self._revisions: list[Revision] = []
        self._cycles: Counter = Counter()

    def record(self, revision: Revision) -> Report:
        r = Report(checked=1)
        if revision.field_name == "text":
            r.error("CAS 6.5", "TEXT_REVISION_ATTEMPTED",
                    "text never revises (Stage 2 freeze); corrected text is a "
                    "new candidate carrying a supersedes link to the rejected "
                    "one", revision.sample)
            return r   # not recorded: the operation is not a revision at all
        if not revision.reason.strip():
            r.error("CAS 6.5", "REVISION_REASON_MISSING",
                    "revisions log field, old value, new value, reason and "
                    "actor role; a reason is not optional", revision.sample)
        self._revisions.append(revision)
        return r

    def open_cycle(self, sample: str) -> int:
        """Begin a revision cycle; returns the cycle number now in progress."""
        self._cycles[sample] += 1
        return self._cycles[sample]

    def cycles_for(self, sample: str) -> int:
        return self._cycles[sample]

    def exhausted(self, sample: str) -> bool:
        """§6.4: rejection follows when a revision cycle fails twice."""
        return self._cycles[sample] >= MAX_REVISION_CYCLES

    def for_sample(self, sample: str) -> list[Revision]:
        return [x for x in self._revisions if x.sample == sample]

    def all(self) -> list[Revision]:
        return list(self._revisions)


# =====================================================================
# The desk (Stage 6)
# =====================================================================

@dataclass
class ReviewOutcome:
    sample: str
    recommendation: Recommendation | None
    state: State
    report: Report = field(default_factory=Report)
    adjudicated: bool = False

    @property
    def advanced(self) -> bool:
        return self.state is State.REVIEWED


class ReviewDesk:
    """Runs CAS §2 Stage 6 for one candidate (SCREENED → REVIEWED)."""

    def __init__(self, registry: IdentifierRegistry, ledger: DecisionLedger,
                 revisions: RevisionLog | None = None,
                 field_registry: FieldRegistry | None = None):
        self.registry = registry
        self.ledger = ledger
        self.revisions = revisions or RevisionLog()
        self.field_registry = field_registry or load_field_registry()

    def open_round(self, sample: str, split: str | None) -> ReviewRound:
        state = self.registry.state_of(sample)
        if state is not REQUIRED_STATE:
            raise LifecycleError(
                f"{sample} is {state.value if state else 'unregistered'}; "
                f"Stage 6 review requires {REQUIRED_STATE.value}")
        return ReviewRound(sample, split, registry=self.field_registry)

    def submit(self, round_: ReviewRound, review: Review, *,
               declared_interests: dict[str, set[str]] | None = None,
               detectors_under_evaluation: set[str] | None = None) -> Report:
        """Check eligibility, accept the review, and record it (§14.2)."""
        report = check_reviewer_eligibility(
            self.ledger, round_.sample, review.reviewer,
            declared_interests, detectors_under_evaluation)
        if report.errors:
            return report   # ineligible: the review is not accepted at all

        report.extend(round_.submit(review))
        report.extend(self.ledger.record(Decision(
            action=LEDGER_ACTION[review.mandate.value],
            actor_person=review.reviewer, actor_role="reviewer",
            timestamp=review.timestamp, sample=round_.sample,
            reason=f"{review.mandate.value} review: {review.recommendation.value}",
        )))
        return report

    def adjudicate(self, round_: ReviewRound, adjudication: Adjudication) -> Report:
        report = round_.adjudicate(adjudication)
        report.extend(self.ledger.record(Decision(
            action="adjudicate", actor_person=adjudication.adjudicator,
            actor_role="adjudicator", timestamp=adjudication.timestamp,
            sample=round_.sample,
            reason=f"{adjudication.recommendation.value}: {adjudication.reasoning}",
        )))
        return report

    def conclude(self, round_: ReviewRound, *, timestamp: str,
                 actor_role: str = "maintainer") -> ReviewOutcome:
        """Close the round and move the candidate accordingly."""
        report = Report(checked=1)
        sample = round_.sample

        if not round_.complete:
            report.error("CAS 6.1", "REVIEW_ROUND_INCOMPLETE",
                         f"{[m.value for m in round_.required_mandates]} required; "
                         f"have {[r.mandate.value for r in round_.reviews]}", sample)
            return ReviewOutcome(sample, None, REQUIRED_STATE, report)

        recommendation = round_.outcome()
        if recommendation is None:
            report.error("CAS 6.2", "UNRESOLVED_DISAGREEMENT",
                         f"reviewers disagree on {round_.disagreements()}; a "
                         "third senior reviewer adjudicates before the round "
                         "concludes", sample)
            return ReviewOutcome(sample, None, REQUIRED_STATE, report)

        if recommendation is Recommendation.ACCEPT:
            self.registry.transition(sample, State.REVIEWED, actor_role, timestamp,
                                     reason="Stage 6 review recommends acceptance")
            state = State.REVIEWED
        elif recommendation is Recommendation.REVISE:
            cycle = self.revisions.open_cycle(sample)
            if self.revisions.exhausted(sample):
                report.error("CAS 6.4", "REVISION_CYCLES_EXHAUSTED",
                             f"revision cycle {cycle} of {MAX_REVISION_CYCLES}; "
                             "§6.4 rejects a candidate whose revision cycle "
                             "fails twice", sample)
                self.registry.reject(sample, actor_role, timestamp,
                                     reason="§6.4 revision cycles exhausted")
                state = State.REJECTED
            else:
                state = REQUIRED_STATE   # stays; metadata revision requested
        else:
            self.registry.reject(sample, actor_role, timestamp,
                                 reason="§6.4 review rejection")
            state = State.REJECTED

        return ReviewOutcome(sample, recommendation, state, report,
                             adjudicated=round_.adjudication is not None)
