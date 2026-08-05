"""R-10 reviewer QA tests (CAS §6, BS §4.6, §9.1(c)).

Stage 6 is where human judgment enters a system built to keep it out, so the
tests that matter most are the ones about what judgment is *not* allowed to
do: form opinions about the text (P3), see the other reviewer's conclusions
first (§6.1), or produce an agreement number that reads as a pass when
nothing was measured (§6.3).
"""

import pytest

from ai_text_eval.gauntlet.ledger import Decision, DecisionLedger
from ai_text_eval.gauntlet.lifecycle import IdentifierRegistry, LifecycleError, State
from ai_text_eval.gauntlet.review import (
    KAPPA_THRESHOLD,
    MAX_REVISION_CYCLES,
    Adjudication,
    CalibrationExercise,
    Mandate,
    Recommendation,
    Review,
    ReviewDesk,
    ReviewError,
    ReviewRound,
    ReviewerRecord,
    Revision,
    RevisionLog,
    SeededDefect,
    agreement,
    agreement_gate,
    check_reviewer_eligibility,
    cohens_kappa,
    judgment_fields,
)

TS = "2026-08-05T00:00:00Z"
SAMPLE = "H-01-B100-0001"


def review(reviewer="rita", mandate=Mandate.PROVENANCE,
           recommendation=Recommendation.ACCEPT, sample=SAMPLE, **kw):
    return Review(sample=sample, reviewer=reviewer, mandate=mandate,
                  recommendation=recommendation, timestamp=TS, **kw)


def codes(report):
    return {f.code for f in report.findings}


def desk(tmp_path):
    return ReviewDesk(IdentifierRegistry(path=tmp_path / "ids.jsonl"),
                      DecisionLedger(path=tmp_path / "decisions.jsonl"))


def screened(d, sample=SAMPLE, text="some frozen text"):
    d.registry.open_idea(sample, "contributor", TS)
    d.registry.freeze(sample, text, "contributor", TS)
    d.registry.transition(sample, State.VALIDATED, "system", TS, reason="valid")
    d.registry.transition(sample, State.SCREENED, "system", TS, reason="screened")
    return sample


# =====================================================================
# Judgment fields come from governed data (BS §4.6, TD-G12)
# =====================================================================


def test_judgment_fields_are_read_from_the_registry_not_declared():
    fields = judgment_fields()
    assert "difficulty" in fields["categorical"]
    assert "pii_status" in fields["categorical"]
    assert "rationale" in fields["free_text"]


def test_free_text_judgment_fields_are_kept_separate_from_categorical():
    """Kappa is defined for categories, not for prose. Mixing them would
    produce a number for something that has none."""
    fields = judgment_fields()
    assert not set(fields["categorical"]) & set(fields["free_text"])
    assert "rationale" not in fields["categorical"]


# =====================================================================
# Two mandates (§6.1)
# =====================================================================


def test_test_material_requires_two_independent_reviews():
    round_ = ReviewRound(SAMPLE, "test")
    assert round_.dual
    assert round_.required_mandates == (Mandate.PROVENANCE, Mandate.CONTENT)


def test_hidden_material_requires_two_reviews():
    assert ReviewRound(SAMPLE, "hidden").dual


def test_dev_only_material_receives_one_combined_review():
    round_ = ReviewRound(SAMPLE, "dev")
    assert not round_.dual
    assert round_.required_mandates == (Mandate.COMBINED,)


def test_a_combined_review_is_refused_on_test_material():
    round_ = ReviewRound(SAMPLE, "test")
    with pytest.raises(ReviewError, match="requires"):
        round_.submit(review(mandate=Mandate.COMBINED))


def test_a_round_is_incomplete_until_every_mandate_is_in():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(mandate=Mandate.PROVENANCE))
    assert not round_.complete
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT))
    assert round_.complete


def test_a_second_review_under_the_same_mandate_is_refused():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(mandate=Mandate.PROVENANCE))
    with pytest.raises(ReviewError, match="not be independent"):
        round_.submit(review(reviewer="raj", mandate=Mandate.PROVENANCE))


def test_one_person_filling_both_mandates_is_not_two_independent_reviews():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", mandate=Mandate.PROVENANCE))
    report = round_.submit(review(reviewer="rita", mandate=Mandate.CONTENT))
    assert "SAME_REVIEWER_BOTH_MANDATES" in codes(report)


# =====================================================================
# Independence is enforced by withholding (§6.1)
# =====================================================================


def test_a_reviewer_sees_nothing_before_the_round_is_complete():
    """§6.1: "neither reviewer sees the other's conclusions before submitting
    their own." Withholding is the enforcement; instructing would not be."""
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", mandate=Mandate.PROVENANCE))
    assert round_.conclusions_for("raj") == []


def test_conclusions_are_available_once_the_round_is_complete():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", mandate=Mandate.PROVENANCE))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT))
    assert [r.reviewer for r in round_.conclusions_for("raj")] == ["rita"]


def test_a_reviewer_is_never_shown_their_own_review_as_anothers():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", mandate=Mandate.PROVENANCE))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT))
    assert all(r.reviewer != "rita" for r in round_.conclusions_for("rita"))


# =====================================================================
# P3: appearance opinions are inadmissible (§6.1)
# =====================================================================


def test_an_appearance_opinion_in_a_provenance_review_is_a_defect():
    """§6.1: such opinions are inadmissible (P3) and "their appearance in a
    review is itself a review defect"."""
    round_ = ReviewRound(SAMPLE, "test")
    report = round_.submit(review(
        mandate=Mandate.PROVENANCE,
        appearance_opinion="the prose reads like a model wrote it"))
    assert "APPEARANCE_OPINION_IN_REVIEW" in {f.code for f in report.errors}


def test_the_schema_has_a_place_for_the_opinion_so_it_can_be_detected():
    """A schema with nowhere to record it would push the same opinion into
    free-text notes, where nothing can see it."""
    assert hasattr(review(), "appearance_opinion")


def test_a_provenance_review_without_an_opinion_is_clean():
    round_ = ReviewRound(SAMPLE, "test")
    assert round_.submit(review(mandate=Mandate.PROVENANCE)).ok


# =====================================================================
# Disagreement and adjudication (§6.2)
# =====================================================================


def test_differing_recommendations_are_a_disagreement():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", recommendation=Recommendation.ACCEPT))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         recommendation=Recommendation.REJECT))
    assert "recommendation" in round_.disagreements()


def test_differing_judgment_values_are_a_disagreement():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", judgments={"difficulty": "D3"}))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         judgments={"difficulty": "D4"}))
    assert round_.disagreements() == ["difficulty"]


def test_agreeing_reviews_produce_no_disagreement():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", judgments={"difficulty": "D3"}))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         judgments={"difficulty": "D3"}))
    assert round_.disagreements() == []


def test_disagreement_with_the_contributors_metadata_also_adjudicates():
    """§6.2 routes both cases: reviewers disagreeing with each other, or
    either disagreeing with the contributor."""
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", judgments={"difficulty": "D3"}))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         judgments={"difficulty": "D3"}))
    assert not round_.needs_adjudication()
    assert round_.needs_adjudication({"difficulty": "D5"})


def test_an_unresolved_disagreement_has_no_outcome():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", recommendation=Recommendation.ACCEPT))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         recommendation=Recommendation.REJECT))
    assert round_.outcome() is None


def test_adjudication_resolves_the_outcome():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita", recommendation=Recommendation.ACCEPT))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT,
                         recommendation=Recommendation.REJECT))
    round_.adjudicate(Adjudication(SAMPLE, "senior", Recommendation.ACCEPT,
                                   "evidence is complete and the category fits",
                                   TS))
    assert round_.outcome() is Recommendation.ACCEPT


def test_the_adjudicator_must_be_a_third_person():
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita"))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT))
    report = round_.adjudicate(Adjudication(SAMPLE, "rita", Recommendation.ACCEPT,
                                            "because", TS))
    assert "ADJUDICATOR_ALREADY_REVIEWED" in codes(report)


def test_an_adjudication_without_reasoning_is_rejected():
    """§6.2: "The adjudicator's decision and reasoning are recorded." Without
    reasoning, recurring ambiguity cannot be spotted and fixed in the spec."""
    round_ = ReviewRound(SAMPLE, "test")
    round_.submit(review(reviewer="rita"))
    round_.submit(review(reviewer="raj", mandate=Mandate.CONTENT))
    report = round_.adjudicate(Adjudication(SAMPLE, "senior",
                                            Recommendation.ACCEPT, "   ", TS))
    assert "ADJUDICATION_REASONING_MISSING" in codes(report)


# =====================================================================
# Agreement measurement (§6.3) — the arithmetic
# =====================================================================


def test_kappa_of_perfect_agreement_over_two_categories_is_one():
    pairs = [("D3", "D3"), ("D4", "D4"), ("D3", "D3"), ("D4", "D4")]
    kappa, po, reason = cohens_kappa(pairs)
    assert kappa == pytest.approx(1.0)
    assert po == 1.0
    assert reason == ""


def test_kappa_matches_a_hand_computed_value():
    """2x2, n=10, agreeing on 8. Both raters call it D3 six times and D4 four
    times, so po = 0.8, pe = 0.6*0.6 + 0.4*0.4 = 0.52, and
    kappa = (0.8 - 0.52) / (1 - 0.52) = 7/12."""
    pairs = ([("D3", "D3")] * 5 + [("D4", "D4")] * 3
             + [("D3", "D4")] * 1 + [("D4", "D3")] * 1)
    kappa, po, _ = cohens_kappa(pairs)
    assert po == pytest.approx(0.8)
    assert kappa == pytest.approx(7 / 12)


def test_kappa_uses_both_raters_marginals_not_one():
    """Unequal marginals: A calls it D3 eight times, B four times, and they
    agree on four. po = 0.4; pe = 0.8*0.4 + 0.2*0.6 = 0.44;
    kappa = (0.4 - 0.44) / 0.56, i.e. slightly negative. Using one rater's
    marginal for both would give a different, wrong answer."""
    pairs = ([("D3", "D3")] * 4 + [("D3", "D4")] * 4 + [("D4", "D4")] * 2)
    kappa, po, _ = cohens_kappa(pairs)
    assert po == pytest.approx(0.6)
    assert kappa == pytest.approx((0.6 - 0.44) / 0.56)


def test_kappa_of_chance_level_agreement_is_about_zero():
    pairs = [("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]
    kappa, _, _ = cohens_kappa(pairs)
    assert kappa == pytest.approx(0.0)


def test_kappa_is_undefined_when_both_reviewers_used_one_category():
    """The classic paradox: perfect observed agreement, 0/0 kappa. Returning
    1.0 would rank the least informative possible batch as the best one."""
    kappa, po, reason = cohens_kappa([("D3", "D3")] * 20)
    assert kappa is None
    assert po == 1.0
    assert "0/0" in reason


def test_kappa_of_an_empty_batch_is_undefined_not_perfect():
    kappa, po, reason = cohens_kappa([])
    assert kappa is None
    assert "no dual-annotated items" in reason


def test_systematic_disagreement_gives_negative_kappa():
    pairs = [("a", "b"), ("b", "a")] * 5
    kappa, _, _ = cohens_kappa(pairs)
    assert kappa < 0


# =====================================================================
# Agreement gating (§6.3) — unmeasured is not passing
# =====================================================================


def test_an_undefined_kappa_does_not_pass():
    """The single most important assertion in this module."""
    result = agreement({"difficulty": [("D3", "D3")] * 20})["difficulty"]
    assert result.kappa is None
    assert not result.passes
    assert not result.measured


def test_the_gate_calls_an_undefined_kappa_unmeasured_not_failed():
    report = agreement_gate(agreement({"difficulty": [("D3", "D3")] * 20}))
    message = [f.message for f in report.errors if f.code == "KAPPA_UNDEFINED"][0]
    assert "unmeasured agreement, not passing agreement" in message


def test_kappa_below_threshold_blocks_the_batch():
    pairs = [("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]
    report = agreement_gate(agreement({"difficulty": pairs}))
    assert "KAPPA_BELOW_THRESHOLD" in {f.code for f in report.errors}


def test_a_field_never_dual_annotated_is_reported_as_such(tmp_path):
    """Silence about a judgment field is not agreement about it."""
    report = agreement_gate({})
    assert "FIELD_NOT_DUAL_ANNOTATED" in {f.code for f in report.errors}
    missing = {f.location for f in report.errors
               if f.code == "FIELD_NOT_DUAL_ANNOTATED"}
    assert "difficulty" in missing and "pii_status" in missing


def test_the_ungoverned_batch_size_is_reported_every_time():
    """No minimum n is specified anywhere, so a pass over three items is
    reported at face value — with the caveat attached (TD-G13)."""
    report = agreement_gate(agreement({"difficulty": [("a", "a"), ("b", "b")]}))
    assert "KAPPA_BATCH_SIZE_NOT_GOVERNED" in {f.code for f in report.warnings}


def test_item_counts_accompany_every_result():
    results = agreement({"difficulty": [("a", "a"), ("b", "b"), ("a", "b")]})
    assert results["difficulty"].n == 3
    assert results["difficulty"].to_dict()["n"] == 3


def test_the_threshold_is_the_one_both_specifications_state():
    assert KAPPA_THRESHOLD == 0.8


def test_a_passing_batch_clears_the_gate_apart_from_the_caveat():
    fields = judgment_fields()["categorical"]
    strong = [("a", "a")] * 9 + [("b", "b")] * 9 + [("a", "b")]
    report = agreement_gate(agreement({name: strong for name in fields}))
    assert report.ok
    assert {f.code for f in report.warnings} == {"KAPPA_BATCH_SIZE_NOT_GOVERNED"}


# =====================================================================
# Reviewer integrity (§6.6)
# =====================================================================


def test_the_producer_of_a_sample_may_not_review_it(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    ledger.record(Decision(action="create_sample", actor_person="alice",
                           actor_role="contributor", timestamp=TS, sample=SAMPLE))
    report = check_reviewer_eligibility(ledger, SAMPLE, "alice",
                                        declared_interests={})
    assert "REVIEWER_PRODUCED_THE_SAMPLE" in codes(report)


def test_the_generation_operator_may_not_review_it(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    ledger.record(Decision(action="generation_operate", actor_person="bob",
                           actor_role="generation_operator", timestamp=TS,
                           sample=SAMPLE))
    report = check_reviewer_eligibility(ledger, SAMPLE, "bob",
                                        declared_interests={})
    assert "REVIEWER_PRODUCED_THE_SAMPLE" in codes(report)


def test_an_uninvolved_reviewer_is_eligible(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    ledger.record(Decision(action="create_sample", actor_person="alice",
                           actor_role="contributor", timestamp=TS, sample=SAMPLE))
    assert check_reviewer_eligibility(ledger, SAMPLE, "carol",
                                      declared_interests={}).ok


def test_a_declared_detector_interest_disqualifies(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    report = check_reviewer_eligibility(
        ledger, SAMPLE, "dana",
        declared_interests={"dana": {"detector-x"}},
        detectors_under_evaluation={"detector-x"})
    assert "REVIEWER_HAS_DECLARED_DETECTOR_INTEREST" in codes(report)


def test_an_interest_in_a_detector_not_under_evaluation_is_not_a_conflict(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    assert check_reviewer_eligibility(
        ledger, SAMPLE, "dana",
        declared_interests={"dana": {"detector-y"}},
        detectors_under_evaluation={"detector-x"}).ok


def test_a_missing_interest_register_is_reported_not_assumed_clean(tmp_path):
    """No register supplied means the conflict arm did not run. Silence there
    is not a clean bill of health."""
    ledger = DecisionLedger(path=tmp_path / "d.jsonl")
    report = check_reviewer_eligibility(ledger, SAMPLE, "dana")
    assert "DECLARED_INTERESTS_NOT_SUPPLIED" in codes(report)


# =====================================================================
# Calibration exercises (§6.6)
# =====================================================================


def test_calibration_scores_caught_and_missed_defects():
    exercise = CalibrationExercise("q3", "1", [
        SeededDefect("S-1", "LENGTH_MISMATCH"),
        SeededDefect("S-2", "TIER_OVERCLAIM"),
    ])
    result = exercise.score("rita", {"S-1": ["LENGTH_MISMATCH"], "S-2": []})
    assert [d.code for d in result.caught] == ["LENGTH_MISMATCH"]
    assert [d.code for d in result.missed] == ["TIER_OVERCLAIM"]
    assert result.catch_rate == pytest.approx(0.5)


def test_a_catch_rate_over_zero_seeded_defects_is_undefined():
    """Not 1.0: an exercise that seeded nothing measured nothing."""
    assert CalibrationExercise("empty", "1", []).score("rita", {}).catch_rate is None


def test_a_reviewer_record_tracks_exercises_with_misses():
    exercise = CalibrationExercise("q3", "1", [SeededDefect("S-1", "X")])
    record = ReviewerRecord("rita")
    record.add(exercise.score("rita", {"S-1": ["X"]}))
    record.add(exercise.score("rita", {"S-1": []}))
    assert record.exercises_taken == 2
    assert record.exercises_with_misses == 1


def test_retraining_cannot_be_applied_without_a_governed_threshold():
    """§6.6 says "repeatedly" and gives no number, so none is invented."""
    record = ReviewerRecord("rita")
    record.add(CalibrationExercise("q3", "1", [SeededDefect("S-1", "X")])
               .score("rita", {"S-1": []}))
    assert "RETRAINING_THRESHOLD_UNSET" in codes(record.retraining_report())


def test_a_supplied_threshold_engages():
    record = ReviewerRecord("rita")
    for _ in range(3):
        record.add(CalibrationExercise("q3", "1", [SeededDefect("S-1", "X")])
                   .score("rita", {"S-1": []}))
    report = record.retraining_report(threshold=2)
    assert "REVIEWER_REQUIRES_RETRAINING" in {f.code for f in report.errors}


# =====================================================================
# Revision tracking (§6.5, §6.4)
# =====================================================================


def test_a_metadata_revision_records_all_five_required_facts():
    log = RevisionLog()
    log.record(Revision(SAMPLE, "difficulty", "D3", "D4", "panel re-estimate",
                        "maintainer", TS))
    entry = log.for_sample(SAMPLE)[0].to_dict()
    assert set(entry) >= {"field", "old_value", "new_value", "reason", "actor_role"}


def test_text_never_revises():
    """§6.5: corrected text is a new candidate with a supersedes link."""
    log = RevisionLog()
    report = log.record(Revision(SAMPLE, "text", "old", "new", "typo",
                                 "contributor", TS))
    assert "TEXT_REVISION_ATTEMPTED" in codes(report)
    assert log.for_sample(SAMPLE) == []   # not recorded as a revision at all


def test_a_revision_without_a_reason_is_a_defect():
    log = RevisionLog()
    report = log.record(Revision(SAMPLE, "difficulty", "D3", "D4", "  ",
                                 "maintainer", TS))
    assert "REVISION_REASON_MISSING" in codes(report)


def test_two_failed_cycles_exhaust_the_allowance():
    log = RevisionLog()
    assert log.open_cycle(SAMPLE) == 1
    assert not log.exhausted(SAMPLE)
    assert log.open_cycle(SAMPLE) == 2
    assert log.exhausted(SAMPLE)
    assert MAX_REVISION_CYCLES == 2


# =====================================================================
# The desk (Stage 6)
# =====================================================================


def test_review_requires_the_screened_state(tmp_path):
    d = desk(tmp_path)
    d.registry.open_idea(SAMPLE, "contributor", TS)
    d.registry.freeze(SAMPLE, "text", "contributor", TS)
    with pytest.raises(LifecycleError, match="Stage 6"):
        d.open_round(SAMPLE, "test")


def test_an_accepted_round_advances_to_reviewed(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    d.submit(round_, review(reviewer="raj", mandate=Mandate.CONTENT),
             declared_interests={})
    outcome = d.conclude(round_, timestamp=TS)
    assert outcome.recommendation is Recommendation.ACCEPT
    assert d.registry.state_of(SAMPLE) is State.REVIEWED


def test_an_incomplete_round_does_not_conclude(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    outcome = d.conclude(round_, timestamp=TS)
    assert "REVIEW_ROUND_INCOMPLETE" in codes(outcome.report)
    assert d.registry.state_of(SAMPLE) is State.SCREENED


def test_an_unresolved_disagreement_does_not_conclude(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    d.submit(round_, review(reviewer="raj", mandate=Mandate.CONTENT,
                            recommendation=Recommendation.REJECT),
             declared_interests={})
    outcome = d.conclude(round_, timestamp=TS)
    assert "UNRESOLVED_DISAGREEMENT" in codes(outcome.report)
    assert d.registry.state_of(SAMPLE) is State.SCREENED


def test_adjudication_lets_the_round_conclude(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    d.submit(round_, review(reviewer="raj", mandate=Mandate.CONTENT,
                            recommendation=Recommendation.REJECT),
             declared_interests={})
    d.adjudicate(round_, Adjudication(SAMPLE, "senior", Recommendation.ACCEPT,
                                      "evidence complete, category fits", TS))
    outcome = d.conclude(round_, timestamp=TS)
    assert outcome.adjudicated
    assert d.registry.state_of(SAMPLE) is State.REVIEWED


def test_a_rejection_is_terminal(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "dev")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.COMBINED,
                            recommendation=Recommendation.REJECT),
             declared_interests={})
    d.conclude(round_, timestamp=TS)
    assert d.registry.state_of(SAMPLE) is State.REJECTED


def test_a_revision_request_holds_the_candidate_at_screened(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "dev")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.COMBINED,
                            recommendation=Recommendation.REVISE),
             declared_interests={})
    outcome = d.conclude(round_, timestamp=TS)
    assert outcome.state is State.SCREENED
    assert d.revisions.cycles_for(SAMPLE) == 1


def test_a_second_failed_revision_cycle_rejects(tmp_path):
    """§6.4: rejection follows when a revision cycle fails twice."""
    d = desk(tmp_path)
    screened(d)
    for _ in range(2):
        round_ = d.open_round(SAMPLE, "dev")
        d.submit(round_, review(reviewer="rita", mandate=Mandate.COMBINED,
                                recommendation=Recommendation.REVISE),
                 declared_interests={})
        outcome = d.conclude(round_, timestamp=TS)
    assert "REVISION_CYCLES_EXHAUSTED" in codes(outcome.report)
    assert d.registry.state_of(SAMPLE) is State.REJECTED


def test_an_ineligible_reviewers_review_is_not_accepted(tmp_path):
    """The review is refused outright, not recorded and flagged: §6.6 says
    the producer MUST NOT review, so there is nothing to file."""
    d = desk(tmp_path)
    screened(d)
    d.ledger.record(Decision(action="create_sample", actor_person="alice",
                             actor_role="contributor", timestamp=TS, sample=SAMPLE))
    round_ = d.open_round(SAMPLE, "test")
    report = d.submit(round_, review(reviewer="alice", mandate=Mandate.PROVENANCE),
                      declared_interests={})
    assert "REVIEWER_PRODUCED_THE_SAMPLE" in codes(report)
    assert round_.reviews == []


def test_each_review_lands_in_the_decision_ledger(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    d.submit(round_, review(reviewer="raj", mandate=Mandate.CONTENT),
             declared_interests={})
    actions = [e["action"] for e in d.ledger.for_sample(SAMPLE)]
    assert actions == ["review_provenance", "review_content"]


def test_an_adjudication_lands_in_the_ledger_with_its_reasoning(tmp_path):
    d = desk(tmp_path)
    screened(d)
    round_ = d.open_round(SAMPLE, "test")
    d.submit(round_, review(reviewer="rita", mandate=Mandate.PROVENANCE),
             declared_interests={})
    d.submit(round_, review(reviewer="raj", mandate=Mandate.CONTENT,
                            recommendation=Recommendation.REJECT),
             declared_interests={})
    d.adjudicate(round_, Adjudication(SAMPLE, "senior", Recommendation.ACCEPT,
                                      "the evidence package is complete", TS))
    entry = [e for e in d.ledger.for_sample(SAMPLE) if e["action"] == "adjudicate"][0]
    assert "the evidence package is complete" in entry["reason"]
