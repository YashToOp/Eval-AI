"""Append-only decision ledger (R-03).

CAS §14.2: every privileged action lands in an append-only decision record —
action, scope, actors by role, reasons, evidence references — and the record
survives as long as the corpus does. §11.7 adds that in the small-team era one
person may hold several roles but MUST NOT act in two conflicting roles on the
same sample, and that every same-person dual-role action is noted for later
audit.

This module is the *record*, not the review process (that is R-10, Phase C).
Its two jobs:

  1. Persist privileged actions append-only, faithfully, forever (P5). The
     ledger never refuses to record; refusing would defeat the audit surface.
  2. Detect the §11.7 / §6.6 conflict-of-interest pairs across recorded
     actions and surface them as findings. Enforcement — blocking a sample —
     is the acceptance gate's job (R-11), not the ledger's; the ledger's duty
     is that the conflict is visible.

Actions are recorded even when they conflict, because "note every same-person
dual-role action" (§11.7) requires exactly that. Callers wanting hard
enforcement pass `strict=True`, which records first and then raises.

Out of scope for R-03, noted rather than silently omitted: the §11.5
release-cycle constraints ("release managers do not create or review in the
same cycle they assign") need release-cycle modelling that arrives with the
release builder (R-15). Only the same-sample pairs of §11.7 and §6.6 are
enforced here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.spec import CORPUS_DIR

#: Privileged actions, from the CAS §14.1 authority matrix. Closed vocabulary.
PRIVILEGED_ACTIONS = frozenset({
    "create_sample",
    "generation_operate",
    "review_provenance",
    "review_content",
    "adjudicate",
    "confirm_acceptance",
    # §14.1 lists no screening authority, but §14.2 requires every privileged
    # action to land in the decision record and a Stage 5 hold is exactly
    # that: a consequential, contestable decision about a sample. Added as an
    # explicit project decision (TD-D18) rather than recording screening holds
    # as `modify_metadata_pre_acceptance`, which would file them as edits to
    # metadata that screening never touches.
    "screen",
    "modify_metadata_pre_acceptance",
    "errata_modify_metadata",
    "change_label",
    "deprecate",
    "redact",
    "assign_split",
    "create_release",
})

#: Roles that count as having produced or operated the sample (§6.6).
PRODUCER_ROLES = frozenset({"contributor", "generation_operator"})

#: Role pairs the same person MUST NOT both fill on one sample.
#: - producer × reviewer / adjudicator: §6.6 (the producer must not review it;
#:   adjudication is a review role).
#: - reviewer × adjudicator: §11.7.
#: - producer × release_manager: §11.7 ("contributor and split assigner for
#:   cells containing their material") and §11.5.
FORBIDDEN_ROLE_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"contributor", "reviewer"}),
    frozenset({"generation_operator", "reviewer"}),
    frozenset({"contributor", "adjudicator"}),
    frozenset({"generation_operator", "adjudicator"}),
    frozenset({"reviewer", "adjudicator"}),
    frozenset({"contributor", "release_manager"}),
    frozenset({"generation_operator", "release_manager"}),
})


class LedgerConflictError(RuntimeError):
    """Raised in strict mode when a recorded action creates a §11.7 conflict."""


@dataclass
class Decision:
    action: str
    actor_person: str
    actor_role: str
    timestamp: str
    sample: str | None = None
    scope: str | None = None
    reason: str = ""
    evidence_refs: list[str] | None = None

    def to_event(self) -> dict:
        return {
            "action": self.action,
            "actor_person": self.actor_person,
            "actor_role": self.actor_role,
            "timestamp": self.timestamp,
            "sample": self.sample,
            "scope": self.scope,
            "reason": self.reason,
            "evidence_refs": self.evidence_refs or [],
        }


class DecisionLedger:
    """Append-only ledger of privileged actions (CAS §14.2)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else (CORPUS_DIR / "registry" / "decisions.jsonl")
        self._events: list[dict] = []
        if self.path.is_file():
            self._replay()

    def _replay(self) -> None:
        with self.path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    self._events.append(json.loads(line))
                except json.JSONDecodeError as err:
                    raise ValueError(
                        f"{self.path}:{line_no}: corrupt decision line: {err}") from err

    # -- recording -------------------------------------------------------

    def record(self, decision: Decision, strict: bool = False) -> Report:
        """Append a privileged action and return any conflicts it creates.

        The action is always recorded (P5). Conflicts against prior actions on
        the same sample are returned as findings; in strict mode the method
        records first, then raises.
        """
        if decision.action not in PRIVILEGED_ACTIONS:
            raise ValueError(
                f"unknown privileged action {decision.action!r}; the authority "
                f"matrix (§14.1) is a closed vocabulary")

        # Compute conflicts against the *prior* history before appending, so a
        # self-comparison of the new action against itself is impossible.
        conflicts = self._conflicts_for(decision)

        event = decision.to_event()
        self._events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

        report = Report(checked=1)
        for other_role, other_person in conflicts:
            report.error(
                "CAS 11.7", "ROLE_CONFLICT",
                f"{decision.actor_person!r} acts as {decision.actor_role} on "
                f"{decision.sample}, having already acted as {other_role} there; "
                "the same person MUST NOT fill both roles on one sample",
                sample_id=decision.sample)
        if strict and not report.ok:
            raise LedgerConflictError(str(report.errors[0]))
        return report

    def _conflicts_for(self, decision: Decision) -> list[tuple[str, str]]:
        """Prior (role, person) actions on the same sample that conflict with
        this actor filling this role."""
        if decision.sample is None:
            return []
        out: list[tuple[str, str]] = []
        for ev in self._events:
            if ev.get("sample") != decision.sample:
                continue
            if ev.get("actor_person") != decision.actor_person:
                continue
            prior_role = ev.get("actor_role")
            if prior_role == decision.actor_role:
                continue
            if frozenset({prior_role, decision.actor_role}) in FORBIDDEN_ROLE_PAIRS:
                out.append((prior_role, ev.get("actor_person")))
        return out

    # -- queries ---------------------------------------------------------

    def for_sample(self, sample: str) -> list[dict]:
        return [e for e in self._events if e.get("sample") == sample]

    def by_actor(self, person: str) -> list[dict]:
        return [e for e in self._events if e.get("actor_person") == person]

    def all_events(self) -> list[dict]:
        return list(self._events)

    def audit_conflicts(self) -> Report:
        """Replay the whole ledger and report every §11.7 conflict in it.

        Used by governance audit: a conflict recorded in strict-off mode still
        exists and must be findable after the fact."""
        report = Report(checked=len(self._events))
        seen: dict[str, dict[str, set[str]]] = {}  # sample -> person -> roles
        for ev in self._events:
            sample = ev.get("sample")
            if sample is None:
                continue
            person = ev.get("actor_person")
            role = ev.get("actor_role")
            roles = seen.setdefault(sample, {}).setdefault(person, set())
            for prior in roles:
                if frozenset({prior, role}) in FORBIDDEN_ROLE_PAIRS:
                    report.error(
                        "CAS 11.7", "ROLE_CONFLICT",
                        f"{person!r} filled both {prior} and {role} on {sample}",
                        sample_id=sample)
            roles.add(role)
        return report
