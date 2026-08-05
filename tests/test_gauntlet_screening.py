"""Stage 5 screening orchestration tests (TD-D18, CAS §2 Stage 5, §8, §3.7).

The distinction these tests exist to protect is held-vs-rejected. Rejection
burns an identifier forever (§9.5); §8.2 and §8.4 both describe material that
comes back explained. Getting that wrong destroys candidates the
specification expects to recover.
"""

import pytest

from ai_text_eval.gauntlet.decontamination import (
    DecontaminationScreen,
    NgramIndex,
    ReferenceKind,
    Stage,
    Verdict,
    dev_split_source,
)
from ai_text_eval.gauntlet.duplicates import CorpusEntry, DuplicateScreen
from ai_text_eval.gauntlet.ledger import PRIVILEGED_ACTIONS, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State
from ai_text_eval.gauntlet.screening import (
    TERMINAL_CODES,
    Disposition,
    ScreeningDesk,
)

TS = "2026-08-05T00:00:00Z"

#: 69 words. Long enough that a one-word paraphrase lands above the default
#: casual-register near-duplicate threshold (0.82) rather than under it —
#: shingle similarity is length-sensitive, and a short passage with one word
#: changed is genuinely *not* a near duplicate by that measure.
TEXT = ("the quarterly maintenance window moved to a saturday because the "
        "storage migration needed a full weekend of headroom and nobody wanted "
        "to explain another partial rollback to the platform review board on "
        "monday morning again so we booked the extra day early and told the on "
        "call rotation to expect a quiet shift with the usual caveat that quiet "
        "is never a promise anyone can actually make about storage")

OTHER = ("bees navigate by polarized light and a remembered vector from the "
         "hive entrance which is why an overcast afternoon shortens their "
         "foraging range far more than a cold one does in early spring across "
         "most temperate regions and that is before anyone accounts for the "
         "way a hedge line changes the local wind enough to matter over the "
         "course of a single working day in the field")

#: The same passage with one word changed: shingle similarity 0.857, above the
#: 0.82 casual threshold and well below exact identity.
NEAR = TEXT.replace("saturday", "sunday")

META = {"category": "H-01", "length_bucket": "B100", "domain": "casual"}


def detection_sources(*texts):
    """Enough named sources for a decontamination scan to be *complete*."""
    from ai_text_eval.gauntlet.decontamination import REQUIRED_REFERENCES
    sources = [NgramIndex(ref.name, ReferenceKind.DETECTION_CORPUS, "v1").add_texts(texts)
               for ref in REQUIRED_REFERENCES
               if ref.kind is ReferenceKind.DETECTION_CORPUS]
    sources.append(dev_split_source([]))
    return sources


def desk(tmp_path, history=(), corpora=(OTHER,), decon=True, dup=True):
    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    return ScreeningDesk(
        registry, ledger,
        duplicates=DuplicateScreen(history) if dup else None,
        decontamination=(DecontaminationScreen(detection_sources(*corpora))
                         if decon else None))


def validated(d, identifier="H-01-B100-0001", text=TEXT):
    """Walk a candidate to VALIDATED the legitimate way."""
    d.registry.open_idea(identifier, "contributor", TS)
    d.registry.freeze(identifier, text, "contributor", TS)
    d.registry.transition(identifier, State.VALIDATED, "system", TS,
                          reason="intake validation passed")
    return identifier


def codes(report):
    return {f.code for f in report.findings}


# =====================================================================
# The lifecycle contract (CAS §2)
# =====================================================================


def test_a_clean_candidate_advances_to_screened(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.disposition is Disposition.ADVANCED
    assert d.registry.state_of(ident) is State.SCREENED


def test_screening_requires_the_validated_state(tmp_path):
    """§2: no stage is skipped."""
    d = desk(tmp_path)
    d.registry.open_idea("H-01-B100-0001", "contributor", TS)
    d.registry.freeze("H-01-B100-0001", TEXT, "contributor", TS)
    with pytest.raises(LifecycleError, match="Stage 5"):
        d.screen("H-01-B100-0001", TEXT, META, timestamp=TS)


def test_screening_an_unregistered_identifier_raises(tmp_path):
    with pytest.raises(LifecycleError, match="unregistered"):
        desk(tmp_path).screen("H-01-B100-0009", TEXT, META, timestamp=TS)


def test_a_screened_candidate_is_not_rescreened(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    with pytest.raises(LifecycleError):
        d.screen(ident, TEXT, META, timestamp=TS)


# =====================================================================
# Held is not rejected (§8.2, §8.4 vs §8.1)
# =====================================================================


def test_undeclared_near_duplicate_holds_rather_than_rejects(tmp_path):
    """§8.2: "the candidate holds pending explanation". Rejection is terminal
    (§6.4) and would burn an identifier the spec expects back."""
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", NEAR)
    outcome = d.screen(ident, NEAR, META, timestamp=TS)
    assert outcome.disposition is Disposition.HELD
    assert "NEAR_DUPLICATE_UNDECLARED" in outcome.hold_codes


def test_a_held_candidate_stays_validated_and_is_not_terminal(tmp_path):
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", NEAR)
    d.screen(ident, NEAR, META, timestamp=TS)
    assert d.registry.state_of(ident) is State.VALIDATED
    assert not d.registry.get(ident).is_terminal


def test_a_held_candidate_advances_once_the_relationship_is_declared(tmp_path):
    """§8.2: "With declaration verified, both samples stand." The hold has to
    be recoverable or the disposition is rejection wearing another name."""
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", NEAR)
    assert d.screen(ident, NEAR, META, timestamp=TS).held

    explained = dict(META, lineage=[{"relation": "derived_from",
                                     "target": "H-01-B100-0001"}])
    outcome = d.screen(ident, NEAR, explained, timestamp=TS)
    assert outcome.disposition is Disposition.ADVANCED
    assert d.registry.state_of(ident) is State.SCREENED


def test_exact_duplication_is_the_only_terminal_screening_outcome(tmp_path):
    """§8.1 is the one class the specification resolves with "the newcomer is
    rejected"."""
    assert TERMINAL_CODES == {"EXACT_DUPLICATE"}
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", TEXT)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.disposition is Disposition.REJECTED
    assert d.registry.state_of(ident) is State.REJECTED


def test_a_rejected_identifier_cannot_be_reused(tmp_path):
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", TEXT)
    d.screen(ident, TEXT, META, timestamp=TS)
    with pytest.raises(LifecycleError):
        d.registry.open_idea(ident, "contributor", TS)


# =====================================================================
# Warnings advance; errors hold
# =====================================================================


def test_semantic_overlap_in_a_cell_advances_to_reach_the_reviewers(tmp_path):
    """§8.3: the diversity judgment is "recorded in the review", so a flagged
    candidate must reach review rather than stop before it."""
    class AlwaysSimilar:
        name = "stub"

        def similarity(self, a, b):
            return 1.0

    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    history = [CorpusEntry("H-01-B100-0001", OTHER, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = ScreeningDesk(registry, ledger,
                      duplicates=DuplicateScreen(history, semantic=AlwaysSimilar()),
                      decontamination=DecontaminationScreen(detection_sources(OTHER)))
    ident = validated(d, "H-01-B100-0002", TEXT)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert "SEMANTIC_OVERLAP_IN_CELL" in codes(outcome.report)
    assert outcome.disposition is Disposition.ADVANCED


def test_an_incomplete_decontamination_scan_does_not_hold_at_candidacy(tmp_path):
    """CAS §3.7 screens to save review effort; BS §9.1(d) is the gate. Holding
    here would make the corpus unbuildable while TD-X01 is open (TD-A04)."""
    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    d = ScreeningDesk(registry, ledger, duplicates=DuplicateScreen([]),
                      decontamination=DecontaminationScreen([]))
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.decontamination.verdict is Verdict.INCOMPLETE
    assert "DECONTAMINATION_INCOMPLETE" in codes(outcome.report)
    assert outcome.disposition is Disposition.ADVANCED


def test_the_same_incomplete_scan_holds_at_release_stage(tmp_path):
    registry = IdentifierRegistry(path=tmp_path / "ids.jsonl")
    ledger = DecisionLedger(path=tmp_path / "decisions.jsonl")
    d = ScreeningDesk(registry, ledger, duplicates=DuplicateScreen([]),
                      decontamination=DecontaminationScreen([]))
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS, stage=Stage.RELEASE)
    assert outcome.disposition is Disposition.HELD


def test_contaminated_candidate_holds_for_the_remedy_decision(tmp_path):
    """BS §4.9 offers two remedies — replace, or move to DEV. Both are human
    decisions, so the candidate waits rather than being rejected."""
    d = desk(tmp_path, corpora=(TEXT,))
    ident = validated(d)
    outcome = d.screen(ident, TEXT, dict(META, split="test"), timestamp=TS)
    assert outcome.decontamination.verdict is Verdict.CONTAMINATED
    assert outcome.disposition is Disposition.HELD
    assert d.registry.state_of(ident) is State.VALIDATED


# =====================================================================
# A screen that did not run does not pass
# =====================================================================


def test_a_desk_without_a_duplicate_screen_holds_everything(tmp_path):
    d = desk(tmp_path, dup=False)
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.disposition is Disposition.HELD
    assert "DUPLICATE_SCREEN_NOT_CONFIGURED" in outcome.hold_codes


def test_a_desk_without_a_decontamination_screen_holds_everything(tmp_path):
    d = desk(tmp_path, decon=False)
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.disposition is Disposition.HELD
    assert "DECONTAMINATION_SCREEN_NOT_CONFIGURED" in outcome.hold_codes


def test_an_unconfigured_desk_never_marks_anything_screened(tmp_path):
    d = desk(tmp_path, dup=False, decon=False)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    assert d.registry.state_of(ident) is not State.SCREENED


# =====================================================================
# The live candidate pool (§8)
# =====================================================================


def test_an_advanced_candidate_joins_the_history(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    assert [e.identifier for e in d.duplicates.history] == [ident]


def test_two_identical_candidates_in_one_batch_do_not_both_pass(tmp_path):
    """The failure this exists to prevent: each screened against a history
    that had not yet heard of the other."""
    d = desk(tmp_path)
    first = validated(d, "H-01-B100-0001", TEXT)
    second = validated(d, "H-01-B100-0002", TEXT)
    assert d.screen(first, TEXT, META, timestamp=TS).advanced
    assert d.screen(second, TEXT, META, timestamp=TS).rejected


def test_a_held_candidate_does_not_join_the_history(tmp_path):
    """Otherwise a held candidate would start blocking its own re-screen."""
    d = desk(tmp_path, dup=False)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    d2 = desk(tmp_path.joinpath("second"))
    assert d2.duplicates.history == []


def test_a_rejected_candidate_does_not_join_the_history(tmp_path):
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", TEXT)
    d.screen(ident, TEXT, META, timestamp=TS)
    assert [e.identifier for e in d.duplicates.history] == ["H-01-B100-0001"]


# =====================================================================
# The decision record (§14.2)
# =====================================================================


def test_screen_is_a_recognised_privileged_action():
    assert "screen" in PRIVILEGED_ACTIONS


def test_every_disposition_lands_in_the_ledger(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    events = d.ledger.for_sample(ident)
    assert [e["action"] for e in events] == ["screen"]
    assert "advanced" in events[0]["reason"]


def test_a_hold_records_its_reasons_in_the_ledger(tmp_path):
    """P5: a hold is contestable, so what caused it survives."""
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", NEAR)
    d.screen(ident, NEAR, META, timestamp=TS)
    reason = d.ledger.for_sample(ident)[0]["reason"]
    assert "held" in reason
    assert "NEAR_DUPLICATE_UNDECLARED" in reason


def test_a_rejection_records_its_grounds(tmp_path):
    history = [CorpusEntry("H-01-B100-0001", TEXT, category="H-01",
                           length_bucket="B100", domain="casual")]
    d = desk(tmp_path, history=history)
    ident = validated(d, "H-01-B100-0002", TEXT)
    d.screen(ident, TEXT, META, timestamp=TS)
    assert "8.1" in d.ledger.for_sample(ident)[0]["reason"]
    assert "exact" in d.registry.get(ident).terminal_reason


def test_the_ledger_survives_a_reload(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    d.screen(ident, TEXT, META, timestamp=TS)
    reloaded = DecisionLedger(path=tmp_path / "decisions.jsonl")
    assert [e["action"] for e in reloaded.for_sample(ident)] == ["screen"]


# =====================================================================
# Reporting, not repairing
# =====================================================================


def test_the_desk_never_edits_the_metadata_it_screens(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    metadata = dict(META)
    d.screen(ident, TEXT, metadata, timestamp=TS)
    assert metadata == META


def test_both_screen_results_are_returned_for_the_reviewers(tmp_path):
    d = desk(tmp_path)
    ident = validated(d)
    outcome = d.screen(ident, TEXT, META, timestamp=TS)
    assert outcome.duplicates is not None
    assert outcome.decontamination is not None
    assert outcome.decontamination.verdict is Verdict.CLEAN
