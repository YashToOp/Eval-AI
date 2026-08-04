"""R-06 mechanical derivation tests (CAS §3.4, §3.5, §4.2, §6.1).

The derivations here replace human estimation, so the tests check arithmetic
against hand-computed values rather than against the implementation.
"""

import pytest

from ai_text_eval.gauntlet.derive import (
    AI,
    HUMAN,
    SHARE_TOLERANCE,
    DerivationError,
    DiffChain,
    EditRound,
    ai_token_share,
    attribute,
    chain_from_evidence,
    derive_label,
    replay,
    span_map,
    tokenize,
    verify_label,
    verify_share,
    verify_span_map,
)
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.validate import _check_span_tiling


def codes(report):
    return {f.code for f in report.findings}


# -- tokenization --------------------------------------------------------


def test_tokenize_matches_whitespace_counting():
    tokens = tokenize("  alpha beta   gamma ")
    assert [t.text for t in tokens] == ["alpha", "beta", "gamma"]
    assert tokens[0].start == 2 and tokens[0].end == 7


def test_tokenize_empty_text():
    assert tokenize("   ") == []


# -- attribution ---------------------------------------------------------


def test_unedited_base_keeps_its_origin():
    chain = DiffChain(base_text="one two three", base_origin=AI)
    assert [a.origin for a in attribute(chain)] == [AI, AI, AI]


def test_inserted_tokens_take_the_editor_origin():
    chain = DiffChain(base_text="one two", base_origin=AI,
                      rounds=[EditRound(editor=HUMAN, text="one two three")])
    assert [a.origin for a in attribute(chain)] == [AI, AI, HUMAN]


def test_untouched_tokens_keep_their_original_origin():
    """A later round does not relabel tokens it left alone."""
    chain = DiffChain(base_text="alpha beta gamma", base_origin=HUMAN,
                      rounds=[EditRound(editor=AI, text="alpha beta delta")])
    origins = [a.origin for a in attribute(chain)]
    assert origins == [HUMAN, HUMAN, AI]


def test_replaced_tokens_take_the_editor_origin():
    chain = DiffChain(base_text="the quick fox", base_origin=AI,
                      rounds=[EditRound(editor=HUMAN, text="the slow fox")])
    assert [a.origin for a in attribute(chain)] == [AI, HUMAN, AI]


def test_deleted_tokens_disappear():
    chain = DiffChain(base_text="one two three four", base_origin=AI,
                      rounds=[EditRound(editor=HUMAN, text="one four")])
    attributed = attribute(chain)
    assert [a.token.text for a in attributed] == ["one", "four"]
    assert [a.origin for a in attributed] == [AI, AI]


def test_multi_round_attribution_is_last_writer_wins():
    chain = DiffChain(
        base_text="a b c", base_origin=HUMAN,
        rounds=[EditRound(editor=AI, text="a X c"),
                EditRound(editor=HUMAN, text="a X Y")])
    assert [a.origin for a in attribute(chain)] == [HUMAN, AI, HUMAN]


def test_malformed_origin_raises():
    with pytest.raises(DerivationError, match="base_origin"):
        attribute(DiffChain(base_text="x", base_origin="robot"))


def test_malformed_editor_raises():
    chain = DiffChain(base_text="x", base_origin=HUMAN,
                      rounds=[EditRound(editor="alien", text="y")])
    with pytest.raises(DerivationError, match="editor"):
        attribute(chain)


# -- model-origin share (§4.2) -------------------------------------------


def test_share_is_one_for_pure_ai():
    assert ai_token_share(DiffChain("one two three", AI)) == 1.0


def test_share_is_zero_for_pure_human():
    assert ai_token_share(DiffChain("one two three", HUMAN)) == 0.0


def test_share_is_hand_checkable():
    """4 final tokens, 1 written by the model -> exactly 0.25."""
    chain = DiffChain(base_text="alpha beta gamma delta", base_origin=HUMAN,
                      rounds=[EditRound(editor=AI, text="alpha beta gamma ZETA")])
    assert ai_token_share(chain) == pytest.approx(0.25)


def test_share_of_empty_text_is_zero():
    assert ai_token_share(DiffChain("", HUMAN)) == 0.0


def test_verify_share_accepts_the_derived_value():
    chain = DiffChain("a b c d", HUMAN, [EditRound(AI, "a b c Z")])
    assert verify_share(chain, 0.25).ok


def test_verify_share_rejects_a_hand_entered_value():
    """§4.2: hand-entered shares are non-conformant."""
    chain = DiffChain("a b c d", HUMAN, [EditRound(AI, "a b c Z")])
    assert "SHARE_NOT_DERIVED" in codes(verify_share(chain, 0.5))


def test_verify_share_reports_an_absent_value():
    assert "SHARE_ABSENT" in codes(verify_share(DiffChain("a", HUMAN), None))


def test_verify_share_tolerance_absorbs_float_noise_only():
    chain = DiffChain("a b c d", HUMAN, [EditRound(AI, "a b c Z")])
    assert verify_share(chain, 0.25 + SHARE_TOLERANCE / 2).ok
    assert not verify_share(chain, 0.25 + 1e-6).ok


def test_verify_share_reports_a_malformed_chain():
    assert "CHAIN_MALFORMED" in codes(
        verify_share(DiffChain("x", "robot"), 0.5))


# -- span maps (§3.5, §4.2) ----------------------------------------------


def test_span_map_of_uniform_text_is_one_span():
    chain = DiffChain("alpha beta gamma", AI)
    spans = span_map(chain)
    assert spans == [[0, len("alpha beta gamma"), AI]]


def test_span_map_splits_at_origin_changes():
    chain = DiffChain(base_text="alpha beta", base_origin=HUMAN,
                      rounds=[EditRound(AI, "alpha beta gamma")])
    spans = span_map(chain)
    assert [s[2] for s in spans] == [HUMAN, AI]


def test_derived_spans_tile_the_text():
    """The derived map must satisfy the §4.2 tiling rule the validator
    enforces — checked against the validator itself, not a reimplementation."""
    chain = DiffChain(base_text="one two three four five", base_origin=HUMAN,
                      rounds=[EditRound(AI, "one two THREE FOUR five")])
    spans = span_map(chain)
    sample = Sample(raw={"text": chain.final_text, "span_map": spans})
    assert _check_span_tiling(sample, "test").ok


def test_derived_spans_start_at_zero_and_end_at_text_length():
    chain = DiffChain(base_text="  leading space here", base_origin=HUMAN,
                      rounds=[EditRound(AI, "  leading space THERE")])
    spans = span_map(chain)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(chain.final_text)


def test_span_map_of_empty_text_is_empty():
    assert span_map(DiffChain("", HUMAN)) == []


def test_verify_span_map_accepts_the_derived_map():
    chain = DiffChain("alpha beta", HUMAN, [EditRound(AI, "alpha beta gamma")])
    assert verify_span_map(chain, span_map(chain)).ok


def test_verify_span_map_rejects_a_hand_written_map():
    """§3.5: post-hoc span annotation is prohibited."""
    chain = DiffChain("alpha beta", HUMAN, [EditRound(AI, "alpha beta gamma")])
    assert "SPAN_MAP_NOT_DERIVED" in codes(
        verify_span_map(chain, [[0, 16, HUMAN]]))


def test_verify_span_map_reports_an_absent_map():
    assert "SPAN_MAP_ABSENT" in codes(verify_span_map(DiffChain("a", HUMAN), None))


# -- label derivation (§4.1, §4.2) ---------------------------------------


@pytest.mark.parametrize("base,editors,expected", [
    (AI, [], "AI"),
    (HUMAN, [], "HUMAN"),
    (AI, [AI], "AI"),
    (HUMAN, [HUMAN], "HUMAN"),
    (AI, [HUMAN], "AI_HUMAN_EDITED"),
    (HUMAN, [AI], "HUMAN_AI_EDITED"),
    (HUMAN, [AI, HUMAN], "COLLAB_MIXED"),
    (AI, [HUMAN, AI], "COLLAB_MIXED"),
])
def test_label_derivation_table(base, editors, expected):
    rounds = [EditRound(editor=e, text=f"text round {i}")
              for i, e in enumerate(editors)]
    assert derive_label(DiffChain("text base", base, rounds)) == expected


def test_verify_label_accepts_the_derived_label():
    chain = DiffChain("a b", AI, [EditRound(HUMAN, "a c")])
    assert verify_label(chain, "AI_HUMAN_EDITED").ok


def test_verify_label_rejects_a_chosen_label():
    """P1: labels are assigned mechanically, never chosen."""
    chain = DiffChain("a b", AI, [EditRound(HUMAN, "a c")])
    assert "LABEL_NOT_DERIVED" in codes(verify_label(chain, "HUMAN"))


def test_label_derivation_does_not_guess_the_category():
    """§3.4: the instruction defines the category, and an instruction is not
    mechanically classifiable — derive_label returns a label, not a cell."""
    chain = DiffChain("a b", HUMAN, [EditRound(AI, "a c", instruction="fix grammar")])
    assert derive_label(chain) == "HUMAN_AI_EDITED"  # not X-04


# -- replay (§6.1) -------------------------------------------------------


def test_replay_matches_the_sample_text():
    chain = DiffChain("base text", HUMAN, [EditRound(AI, "final text")])
    assert replay(chain, "final text").ok


def test_replay_detects_a_chain_from_a_different_sample():
    chain = DiffChain("base text", HUMAN, [EditRound(AI, "final text")])
    assert "CHAIN_REPLAY_MISMATCH" in codes(replay(chain, "some other text"))


def test_replay_of_an_unedited_chain_is_the_base():
    assert replay(DiffChain("just the base", HUMAN), "just the base").ok


# -- evidence integration ------------------------------------------------


def test_chain_from_evidence_builds_a_usable_chain():
    chain = chain_from_evidence({
        "base_origin": AI,
        "states": ["one two", "one two three"],
        "editors": [HUMAN],
        "instructions": ["add a word"],
    })
    assert chain.base_origin == AI
    assert len(chain.rounds) == 1
    assert chain.final_text == "one two three"
    assert derive_label(chain) == "AI_HUMAN_EDITED"


def test_chain_from_evidence_accepts_a_single_instruction_string():
    chain = chain_from_evidence({
        "base_origin": HUMAN, "states": ["a", "b", "c"],
        "editors": [AI, AI], "instructions": "polish",
    })
    assert [r.instruction for r in chain.rounds] == ["polish", "polish"]


def test_chain_from_evidence_requires_an_editor_per_round():
    with pytest.raises(DerivationError, match="every round must record who edited"):
        chain_from_evidence({"base_origin": HUMAN,
                             "states": ["a", "b", "c"], "editors": [AI]})


def test_chain_from_evidence_requires_a_base_state():
    with pytest.raises(DerivationError, match="at least the base state"):
        chain_from_evidence({"base_origin": HUMAN, "states": []})


def test_end_to_end_derivation_is_self_consistent():
    """Share, spans, and label all derive from one attribution pass, so they
    cannot disagree with each other."""
    chain = chain_from_evidence({
        "base_origin": HUMAN,
        "states": ["alpha beta gamma delta", "alpha beta GAMMA delta"],
        "editors": [AI], "instructions": ["swap one word"],
    })
    share = ai_token_share(chain)
    spans = span_map(chain)
    assert share == pytest.approx(0.25)
    assert verify_share(chain, share).ok
    assert verify_span_map(chain, spans).ok
    assert verify_label(chain, derive_label(chain)).ok
    sample = Sample(raw={"text": chain.final_text, "span_map": spans})
    assert _check_span_tiling(sample, "test").ok
