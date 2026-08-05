"""Stage 5 screening orchestration (TD-D18, CAS §2 Stage 5, §8, §3.7).

CAS §2 places duplicate detection and decontamination together at Stage 5,
the VALIDATED → SCREENED transition. R-08 and R-09 built the two screens;
this composes them and owns the lifecycle consequence.

**Held is not rejected, and the difference is the whole design.** §8.2 says an
undeclared near-duplicate "holds pending explanation", and §8.4 returns a
templated batch to its contributor. Rejection in this system is terminal
(§6.4) and burns the identifier forever (§9.5), so routing a hold through
rejection would destroy candidates the specification expects to come back
explained. Three dispositions, therefore:

  ADVANCED   → SCREENED. Warnings ride along to the reviewers.
  HELD       stays VALIDATED. Reasons recorded; re-screenable once the
             contributor declares the relationship or the release manager
             resolves the contamination.
  REJECTED   terminal, and only for §8.1 exact duplication, the one class the
             specification itself resolves with "the newcomer is rejected".

**Warnings advance; errors hold.** A semantic overlap inside a cell (§8.3) is
explicitly a reviewer's diversity judgment "recorded in the review", so it
must reach review rather than stop before it. An undeclared near-duplicate
cannot: no reviewer judgment is being asked for, an explanation is.

**A screen that did not run does not pass.** A desk configured without one of
its two screens holds every candidate. This mirrors R-09's rule for absent
reference corpora: the alternative is a corpus whose samples are marked
SCREENED because nothing looked at them.

**Accepted candidates join the history.** §8 screens against "the live
candidate pool", so an advanced candidate is added to the duplicate screen's
history here. Leaving that to the caller would let two identical candidates
both pass by being screened against a history that never learned about the
first one.

**The `screen` action.** §14.2 requires every privileged action to land in the
append-only decision record, and a hold is consequential enough to need one.
§14.1's matrix had no screening entry, so `screen` was added to the ledger's
closed action vocabulary as an explicit project decision (TD-D18) rather than
by overloading `modify_metadata_pre_acceptance`, which would have recorded a
screening hold as a metadata edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ai_text_eval.gauntlet.decontamination import (
    DecontaminationScreen,
    SampleScan,
    Stage,
)
from ai_text_eval.gauntlet.duplicates import CorpusEntry, DuplicateScreen, ScreenResult
from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State

#: The one screening outcome CAS resolves with terminal rejection: §8.1,
#: "the newcomer is rejected". Everything else that fails is a hold, because
#: §8.2 and §8.4 both describe material that comes back explained.
TERMINAL_CODES = frozenset({"EXACT_DUPLICATE"})

#: The state a candidate must be in to be screened (§2: no stage is skipped).
REQUIRED_STATE = State.VALIDATED


class Disposition(str, Enum):
    ADVANCED = "advanced"
    HELD = "held"
    REJECTED = "rejected"


@dataclass
class ScreeningOutcome:
    """The result of one Stage 5 pass."""

    identifier: str
    disposition: Disposition
    state: State
    report: Report = field(default_factory=Report)
    duplicates: ScreenResult | None = None
    decontamination: SampleScan | None = None
    hold_codes: list[str] = field(default_factory=list)

    @property
    def advanced(self) -> bool:
        return self.disposition is Disposition.ADVANCED

    @property
    def held(self) -> bool:
        return self.disposition is Disposition.HELD

    @property
    def rejected(self) -> bool:
        return self.disposition is Disposition.REJECTED

    @property
    def ok(self) -> bool:
        return self.advanced


class ScreeningDesk:
    """Runs CAS §2 Stage 5 for one candidate.

    Both screens are injected rather than constructed: their configuration is
    a governance artifact (thresholds per register, which reference corpora
    are attached), not something a desk should decide for itself.
    """

    def __init__(self, registry: IdentifierRegistry, ledger: DecisionLedger,
                 duplicates: DuplicateScreen | None = None,
                 decontamination: DecontaminationScreen | None = None):
        self.registry = registry
        self.ledger = ledger
        self.duplicates = duplicates
        self.decontamination = decontamination

    def screen(self, identifier: str, text: str, metadata: dict | None = None, *,
               timestamp: str, actor_person: str = "system",
               actor_role: str = "system", author: str | None = None,
               session: str | None = None,
               stage: Stage = Stage.CANDIDACY) -> ScreeningOutcome:
        """Screen one VALIDATED candidate and record what followed.

        Raises `LifecycleError` if the candidate is not VALIDATED: Stage 5
        follows Stage 4, and a candidate that skipped validation would consume
        screening effort on a record that may not even be well-formed.
        """
        md = dict(metadata or {})
        state = self.registry.state_of(identifier)
        if state is not REQUIRED_STATE:
            raise LifecycleError(
                f"{identifier} is {state.value if state else 'unregistered'}; "
                f"Stage 5 screening requires {REQUIRED_STATE.value} (§2: no "
                "stage is skipped)")

        report = Report(checked=1)
        dup_result: ScreenResult | None = None
        decon_result: SampleScan | None = None

        # -- 8.1-8.6 duplicate detection ---------------------------------
        if self.duplicates is None:
            report.error("CAS 2", "DUPLICATE_SCREEN_NOT_CONFIGURED",
                         "Stage 5 ran without a duplicate screen; a candidate "
                         "MUST NOT be marked SCREENED because nothing looked at "
                         "it", identifier)
        else:
            dup_result = self.duplicates.screen(
                identifier, text,
                domain=md.get("domain"), category=md.get("category"),
                length_bucket=md.get("length_bucket"), split=md.get("split"),
                lineage=md.get("lineage"), topic_group_id=md.get("topic_group_id"),
                author=author, session=session)
            report.extend(dup_result.report)

        # -- 3.7 / BS 4.9 decontamination --------------------------------
        if self.decontamination is None:
            report.error("CAS 3.7", "DECONTAMINATION_SCREEN_NOT_CONFIGURED",
                         "Stage 5 ran without a decontamination screen; §3.7 "
                         "screens every candidate before acceptance", identifier)
        else:
            decon_result = self.decontamination.scan(
                identifier, text, split=md.get("split"), stage=stage)
            report.extend(decon_result.report)

        # -- disposition --------------------------------------------------
        error_codes = [f.code for f in report.errors]
        terminal = [c for c in error_codes if c in TERMINAL_CODES]

        if terminal:
            disposition = Disposition.REJECTED
            reason = f"§8.1 exact duplication ({', '.join(sorted(set(terminal)))})"
            self.registry.reject(identifier, actor_role, timestamp, reason=reason)
            final_state = State.REJECTED
        elif error_codes:
            disposition = Disposition.HELD
            reason = ("held at Stage 5 pending explanation: "
                      f"{', '.join(sorted(set(error_codes)))}")
            final_state = REQUIRED_STATE  # unchanged; the hold is not a state
        else:
            disposition = Disposition.ADVANCED
            reason = "Stage 5 screening passed"
            self.registry.transition(identifier, State.SCREENED, actor_role,
                                     timestamp, reason=reason)
            final_state = State.SCREENED
            self._remember(identifier, text, md, author, session)

        self.ledger.record(Decision(
            action="screen", actor_person=actor_person, actor_role=actor_role,
            timestamp=timestamp, sample=identifier,
            reason=f"{disposition.value}: {reason}",
        ))

        return ScreeningOutcome(
            identifier=identifier, disposition=disposition, state=final_state,
            report=report, duplicates=dup_result, decontamination=decon_result,
            hold_codes=sorted(set(error_codes)))

    def _remember(self, identifier: str, text: str, md: dict,
                  author: str | None, session: str | None) -> None:
        """Add an advanced candidate to the live candidate pool (§8).

        Without this, two identical candidates submitted in one batch would
        both pass, each screened against a history that had not yet heard of
        the other.
        """
        if self.duplicates is None:
            return
        self.duplicates.history.append(CorpusEntry(
            identifier=identifier, text=text, split=md.get("split"),
            domain=md.get("domain"), author=author, session=session,
            category=md.get("category"), length_bucket=md.get("length_bucket"),
            topic_group_id=md.get("topic_group_id"), state=State.SCREENED.value))
