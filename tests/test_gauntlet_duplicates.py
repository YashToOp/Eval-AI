"""R-08 duplicate detection tests (CAS §8, P10, X-6)."""

import pytest

from ai_text_eval.gauntlet.duplicates import (
    DEFAULT_SEMANTIC_THRESHOLD,
    EXPLANATORY_RELATIONS,
    CorpusEntry,
    DuplicateClass,
    DuplicateScreen,
    LexicalSemanticBackend,
    ScreeningConfig,
    jaccard,
    near_similarity,
    normalized_checksum,
    raw_checksum,
    structural_fingerprint,
    template_similarity,
)

BASE = ("The cache invalidation issue was not in the eviction logic at all. "
        "I spent most of yesterday looking there before realising the "
        "timestamps were rounded earlier in the pipeline. That explains why "
        "it only appeared under heavier load.")


def entry(ident, text=BASE, **kw):
    return CorpusEntry(identifier=ident, text=text, **kw)


def codes(result):
    return {f.code for f in result.report.findings}


def screen(history=(), **cfg):
    return DuplicateScreen(history, config=ScreeningConfig(**cfg))


# -- fingerprints (§8.1) -------------------------------------------------


def test_raw_and_normalized_checksums_differ_on_invisible_characters():
    """§8.1 needs both forms: a zero-width variant is a different sample by
    design, and collapsing it would erase Track V."""
    a = "hello world"
    b = "hello​world"
    assert raw_checksum(a) != raw_checksum(b)


def test_normalization_collapses_case_and_whitespace():
    assert normalized_checksum("Hello   World") == normalized_checksum("hello world")


def test_jaccard_edges():
    assert jaccard(set(), set()) == 1.0
    assert jaccard({1}, set()) == 0.0
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)


# -- 8.1 exact -----------------------------------------------------------


def test_exact_duplicate_is_rejected():
    result = screen([entry("H-01-B100-0001")]).screen("H-01-B100-0002", BASE)
    assert "EXACT_DUPLICATE" in codes(result)
    assert not result.ok


def test_exact_duplicate_after_normalization_is_caught():
    result = screen([entry("H-01-B100-0001")]).screen(
        "H-01-B100-0002", BASE.upper())
    assert "EXACT_DUPLICATE" in codes(result)


def test_cross_split_collision_is_investigated():
    result = screen([entry("H-01-B100-0001", split="hidden")]).screen(
        "H-01-B100-0002", BASE, split="dev")
    assert "CROSS_SPLIT_COLLISION" in codes(result)


def test_a_candidate_does_not_match_itself():
    result = screen([entry("H-01-B100-0001")]).screen("H-01-B100-0001", BASE)
    assert result.ok


# -- 8.2 near ------------------------------------------------------------


def test_near_duplicate_without_declaration_is_rejected():
    """X-6: undeclared similarity the contributor cannot resolve."""
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("H-01-B100-0001")]).screen("H-01-B100-0002", tweaked)
    assert "NEAR_DUPLICATE_UNDECLARED" in codes(result)
    assert not result.ok


def test_near_duplicate_with_a_declared_relation_stands():
    """§8.2: with declaration verified, both samples stand and the link is
    recorded."""
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("A-01-B100-0001")]).screen(
        "V-05-B100-0001", tweaked,
        lineage=[{"relation": "derived_from", "target": "A-01-B100-0001"}])
    assert "NEAR_DUPLICATE_DECLARED" in {f.code for f in result.report.warnings}
    assert result.ok


def test_undeclared_relation_type_does_not_excuse_similarity():
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("A-01-B100-0001")]).screen(
        "V-05-B100-0001", tweaked,
        lineage=[{"relation": "vibes_with", "target": "A-01-B100-0001"}])
    assert "NEAR_DUPLICATE_UNDECLARED" in codes(result)


def test_unrelated_text_does_not_flag():
    other = ("Quarterly revenue rose eight percent, driven by the subscription "
             "segment. Hardware sales fell three percent after the delayed "
             "launch. Headcount grew from two hundred to two hundred thirty.")
    result = screen([entry("H-01-B100-0001")]).screen("H-01-B100-0002", other)
    assert result.ok


def test_thresholds_are_per_register():
    """§8.2: issue-tracker text is naturally more self-similar than fiction,
    so a global threshold would be wrong in both directions."""
    cfg = ScreeningConfig()
    assert cfg.near_threshold_for("legal") > cfg.near_threshold_for("creative")
    assert cfg.near_threshold_for(None) == cfg.near_thresholds["__default__"]
    assert cfg.near_threshold_for("unknown-register") == cfg.near_thresholds["__default__"]


def test_uncalibrated_thresholds_are_declared_in_the_report():
    """A pass under provisional thresholds must not read as a calibrated
    result (TD-G05)."""
    result = screen([]).screen("H-01-B100-0001", BASE)
    assert "THRESHOLDS_NOT_CALIBRATED" in {f.code for f in result.report.warnings}
    assert result.thresholds_calibrated is False


def test_calibrated_config_suppresses_the_warning():
    s = DuplicateScreen([], config=ScreeningConfig(calibrated=True))
    result = s.screen("H-01-B100-0001", BASE)
    assert "THRESHOLDS_NOT_CALIBRATED" not in {f.code for f in result.report.findings}


def test_near_similarity_is_symmetric_and_bounded():
    a, b = BASE, BASE.replace("cache", "buffer")
    assert near_similarity(a, b) == pytest.approx(near_similarity(b, a))
    assert 0.0 <= near_similarity(a, b) <= 1.0
    assert near_similarity(a, a) == 1.0


# -- 8.3 semantic --------------------------------------------------------


#: Restates BASE's situation with the same content words in a different
#: order and phrasing — the lexical backend's notion of "same content,
#: different words". A real embedding model would flag looser rewordings.
SAME_SITUATION = ("Eviction logic was not where the cache invalidation issue "
                  "lived. Yesterday I spent most of the time looking there, "
                  "before realising timestamps get rounded earlier in the "
                  "pipeline; heavier load is why it only appeared then.")


def test_semantic_overlap_in_the_same_cell_is_flagged_for_review():
    """§8.3: a cell that secretly restates one situation is flagged.

    The threshold is passed explicitly. The shipped default is documented as
    uncalibrated (TD-G05), so a test must not encode it as a fact — doing so
    would turn an admitted unknown into a green check.
    """
    result = screen([entry("H-01-B100-0001", category="H-01",
                           length_bucket="B100")],
                    semantic_threshold=0.85).screen(
        "H-01-B100-0002", SAME_SITUATION, category="H-01", length_bucket="B100")
    assert "SEMANTIC_OVERLAP_IN_CELL" in {f.code for f in result.report.warnings}


def test_semantic_overlap_across_a_topic_group_is_the_paired_design():
    """§8.3: the same topic realized as human, AI and hybrid is how content
    confounds are controlled — not duplication.

    Same threshold as the test above, so the exemption is genuinely exercised:
    at the default the pair would score below threshold and the test would
    pass without the topic-group rule ever running.
    """
    result = screen([entry("A-01-B100-0001", category="A-01",
                           length_bucket="B100", topic_group_id="tg-1")],
                    semantic_threshold=0.85).screen(
        "H-01-B100-0002", SAME_SITUATION, category="H-01", length_bucket="B100",
        topic_group_id="tg-1")
    assert "SEMANTIC_OVERLAP_IN_CELL" not in {f.code for f in result.report.findings}


def test_the_lexical_stand_in_misses_a_restatement_at_the_default_threshold():
    """An honest record of the stand-in's weakness, not an aspiration.

    BASE and SAME_SITUATION describe one situation in one register; a reviewer
    would call them a restatement. The bundled content-word backend scores
    them below the default threshold, so the shipped screen does *not* flag
    them. Closing this gap needs a real embedding model (TD-X06), not a
    lower threshold — lowering it would flag independent same-topic text too.
    """
    similarity = LexicalSemanticBackend().similarity(BASE, SAME_SITUATION)
    assert similarity < DEFAULT_SEMANTIC_THRESHOLD
    result = screen([entry("H-01-B100-0001", category="H-01",
                           length_bucket="B100")]).screen(
        "H-01-B100-0002", SAME_SITUATION, category="H-01", length_bucket="B100")
    assert "SEMANTIC_OVERLAP_IN_CELL" not in {f.code for f in result.report.findings}


def test_semantic_flags_never_block():
    """§8.3: meaning-level measures flag rather than decide."""
    reworded = ("The problem with cache invalidation turned out not to be "
                "eviction logic. Timestamps were rounded earlier in the "
                "pipeline, which is why heavier load exposed it.")
    result = screen([entry("H-01-B100-0001", category="H-01",
                           length_bucket="B100")]).screen(
        "H-99-B100-0002", reworded, category="H-02", length_bucket="B100")
    assert not [m for m in result.blocking
                if m.duplicate_class is DuplicateClass.SEMANTIC]


def test_semantic_backend_is_named_so_it_is_not_mistaken_for_embeddings():
    result = screen([]).screen("H-01-B100-0001", BASE)
    assert result.semantic_backend == "lexical"


def test_custom_semantic_backend_attaches_without_caller_changes():
    class AlwaysSimilar:
        name = "stub"

        def similarity(self, a, b):
            return 1.0

    s = DuplicateScreen([entry("H-01-B100-0001", category="H-01",
                               length_bucket="B100")],
                        semantic=AlwaysSimilar())
    result = s.screen("H-01-B100-0002", "completely unrelated words here",
                      category="H-01", length_bucket="B100")
    assert result.semantic_backend == "stub"
    assert "SEMANTIC_OVERLAP_IN_CELL" in {f.code for f in result.report.warnings}


def test_lexical_backend_ignores_stopwords():
    b = LexicalSemanticBackend()
    assert b.similarity("the and of it", "the and of it") == 0.0  # all stopwords


# -- 8.4 template --------------------------------------------------------


def test_structural_fingerprint_ignores_content():
    a = "Alpha beta gamma delta. Epsilon zeta eta theta."
    b = "Sigma tau upsilon phi. Chi psi omega kappa."
    assert structural_fingerprint(a)[:2] == structural_fingerprint(b)[:2]


def test_template_convergence_is_flagged():
    """§8.4: same opening construction, same section rhythm, slots varied —
    the characteristic failure of batch generation."""
    a = "In this section we cover alpha beta. Next we examine gamma delta epsilon."
    b = "In this section we cover sigma tau. Next we examine phi chi psi."
    assert template_similarity(a, b) >= 0.85
    result = screen([entry("A-01-B100-0001", text=a)]).screen("A-01-B100-0002", b)
    assert "TEMPLATE_CONVERGENCE" in {f.code for f in result.report.warnings}


def test_shared_structure_alone_is_not_enough_to_flag():
    """Two texts that merely have the same sentence count and length classes,
    with different openings, are not a template — otherwise every pair of
    two-sentence samples would flag."""
    a = "Alpha beta gamma delta epsilon. Zeta eta theta iota kappa."
    b = "Lambda mu nu xi omicron. Pi rho sigma tau upsilon."
    assert template_similarity(a, b) < 0.85


def test_structurally_different_text_does_not_flag_as_template():
    a = "One. Two. Three. Four. Five. Six."
    b = ("A single long sentence that runs on for quite a while without any "
         "internal punctuation to break it into smaller units at all.")
    assert template_similarity(a, b) < 0.85


def test_identical_structure_scores_one():
    a = "Alpha beta gamma. Delta epsilon zeta."
    assert template_similarity(a, a) == 1.0


# -- 8.5 style -----------------------------------------------------------


def test_author_share_cap_is_enforced():
    history = [entry(f"H-01-B100-000{i}", author="alice", category="H-01",
                     length_bucket="B100", text=f"unrelated text number {i} " * 10)
               for i in range(1, 4)]
    s = DuplicateScreen(history, config=ScreeningConfig(
        max_author_share_per_cell=0.5, calibrated=True))
    result = s.screen("H-01-B100-0009", "another distinct text entirely " * 10,
                      category="H-01", length_bucket="B100", author="alice")
    assert "AUTHOR_SHARE_CAP_EXCEEDED" in codes(result)


def test_author_share_under_the_cap_passes():
    history = [entry(f"H-01-B100-000{i}", author=f"writer{i}", category="H-01",
                     length_bucket="B100", text=f"unrelated text number {i} " * 10)
               for i in range(1, 5)]
    s = DuplicateScreen(history, config=ScreeningConfig(
        max_author_share_per_cell=0.5, calibrated=True))
    result = s.screen("H-01-B100-0009", "another distinct text entirely " * 10,
                      category="H-01", length_bucket="B100", author="writer1")
    assert "AUTHOR_SHARE_CAP_EXCEEDED" not in codes(result)


def test_session_share_cap_is_enforced():
    history = [entry(f"A-01-B100-000{i}", session="run-7", category="A-01",
                     length_bucket="B100", text=f"generated text number {i} " * 10)
               for i in range(1, 4)]
    s = DuplicateScreen(history, config=ScreeningConfig(
        max_session_share_per_cell=0.4, calibrated=True))
    result = s.screen("A-01-B100-0009", "different generated text here " * 10,
                      category="A-01", length_bucket="B100", session="run-7")
    assert "SESSION_SHARE_CAP_EXCEEDED" in codes(result)


def test_unset_share_caps_are_reported_not_silently_skipped():
    """§8.5 requires caps in the coverage plan (TD-G04); their absence must be
    visible rather than passing as compliance."""
    result = screen([]).screen("H-01-B100-0001", BASE, category="H-01",
                               length_bucket="B100", author="alice")
    assert "SHARE_CAPS_UNSET" in {f.code for f in result.report.warnings}


def test_share_caps_are_scoped_to_the_cell():
    """A different bucket is a different cell, so its samples do not count."""
    history = [entry(f"H-01-B250-000{i}", author="alice", category="H-01",
                     length_bucket="B250", text=f"other bucket text {i} " * 10)
               for i in range(1, 5)]
    s = DuplicateScreen(history, config=ScreeningConfig(
        max_author_share_per_cell=0.5, calibrated=True))
    result = s.screen("H-01-B100-0009", "a distinct text " * 10,
                      category="H-01", length_bucket="B100", author="alice")
    assert "AUTHOR_SHARE_CAP_EXCEEDED" not in codes(result)


def test_share_cap_is_not_enforced_where_it_cannot_be_satisfied():
    """The first sample of a cell is unavoidably 100% of it. Enforcing a 50%
    cap there would reject every cell's first sample and make the corpus
    impossible to populate, so the cap reports as not-yet-enforceable."""
    s = DuplicateScreen([], config=ScreeningConfig(
        max_author_share_per_cell=0.5, calibrated=True))
    result = s.screen("H-01-B100-0001", BASE, category="H-01",
                      length_bucket="B100", author="alice")
    assert "SHARE_CAP_NOT_YET_ENFORCEABLE" in {f.code for f in result.report.warnings}
    assert "AUTHOR_SHARE_CAP_EXCEEDED" not in codes(result)


def test_share_cap_engages_once_the_cell_is_large_enough():
    """With a 50% cap the rule becomes satisfiable at two samples, and a cell
    of two by one author then genuinely violates it."""
    history = [entry("H-01-B100-0001", author="alice", category="H-01",
                     length_bucket="B100", text="first distinct text here " * 10)]
    s = DuplicateScreen(history, config=ScreeningConfig(
        max_author_share_per_cell=0.5, calibrated=True))
    result = s.screen("H-01-B100-0002", "second distinct text here " * 10,
                      category="H-01", length_bucket="B100", author="alice")
    assert "AUTHOR_SHARE_CAP_EXCEEDED" in codes(result)


# -- 8.6 cross-release ---------------------------------------------------


def test_similarity_to_dev_blocks_hidden_entry():
    """§8.6: memorization of public material must never pay inside HIDDEN."""
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("H-01-B100-0001", split="dev")]).screen(
        "H-01-B100-0002", tweaked, split="hidden")
    assert "HIDDEN_SIMILARITY_TO_PUBLIC" in codes(result)
    assert not result.ok


def test_similarity_to_test_blocks_hidden_entry():
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("H-01-B100-0001", split="test")]).screen(
        "H-01-B100-0002", tweaked, split="hidden")
    assert "HIDDEN_SIMILARITY_TO_PUBLIC" in codes(result)


def test_similarity_between_dev_and_test_does_not_block_hidden_rule():
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("H-01-B100-0001", split="dev")]).screen(
        "H-01-B100-0002", tweaked, split="test")
    assert "HIDDEN_SIMILARITY_TO_PUBLIC" not in codes(result)


def test_redacted_tombstones_still_screen_by_checksum():
    """§8.6 / §9.4: the text is gone but fingerprints remain, and history does
    not reset."""
    tomb = CorpusEntry(identifier="H-01-B100-0001", text=None,
                       state="redacted",
                       raw_checksum=raw_checksum(BASE),
                       normalized_checksum=normalized_checksum(BASE))
    result = screen([tomb]).screen("H-01-B100-0002", BASE)
    assert "EXACT_DUPLICATE" in codes(result)


def test_screening_covers_deprecated_history():
    result = screen([entry("H-01-B100-0001", state="deprecated")]).screen(
        "H-01-B100-0002", BASE)
    assert "EXACT_DUPLICATE" in codes(result)


# -- result surface ------------------------------------------------------


def test_blocking_excludes_explained_and_advisory_classes():
    tweaked = BASE.replace("heavier load", "higher load")
    result = screen([entry("A-01-B100-0001")]).screen(
        "V-05-B100-0001", tweaked,
        lineage=[{"relation": "derived_from", "target": "A-01-B100-0001"}])
    assert result.ok
    assert result.of_class(DuplicateClass.NEAR)


def test_explanatory_relations_match_the_registry_vocabulary():
    from ai_text_eval.gauntlet.registry import load_field_registry
    assert EXPLANATORY_RELATIONS == frozenset(load_field_registry().relationship_types)


def test_empty_history_screens_clean():
    result = screen([]).screen("H-01-B100-0001", BASE)
    assert result.ok
    assert result.matches == []
