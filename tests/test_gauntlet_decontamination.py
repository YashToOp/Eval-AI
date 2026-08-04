"""R-09 decontamination screening tests (CAS §3.7, BS §4.9, §5.4, §9.1(d)).

The load-bearing tests here are the ones about *absence*: a scan that could
not consult its sources must never look like a scan that found nothing. Every
other property in this module is arithmetic; that one is the integrity claim.
"""

import pytest

from ai_text_eval.gauntlet.decontamination import (
    CONTIGUOUS_CHAR_LIMIT,
    NGRAM_N,
    REQUIRED_REFERENCES,
    DecontaminationScreen,
    DevSplitIndex,
    NgramIndex,
    ReferenceKind,
    ScanSummary,
    ScreenConfig,
    Stage,
    Verdict,
    dev_split_source,
    ngrams,
    release_gate,
    words_with_offsets,
)

TS = "2026-08-05T00:00:00Z"

#: 40 distinct words: long enough for 13-gram windows and for a 50+ character
#: contiguous run. Written for this test file, not drawn from any corpus.
SHARED = ("the quarterly maintenance window moved to a saturday because the "
          "storage migration needed a full weekend of headroom and nobody "
          "wanted to explain another partial rollback to the platform review "
          "board on monday morning again")

OTHER = ("bees navigate by polarized light and a remembered vector from the "
         "hive entrance which is why an overcast afternoon shortens their "
         "foraging range far more than a cold one does in early spring "
         "across most temperate regions")

UNRELATED = ("the ferry timetable changes twice a year and the printed version "
             "in the harbour office has been wrong since the pier repairs "
             "started so everyone now checks the handwritten card taped beside "
             "the ticket window instead")


def detection_corpus(*texts, name="HC3", version="v1"):
    return NgramIndex(name, ReferenceKind.DETECTION_CORPUS, version).add_texts(texts)


def all_detection_sources(*texts):
    """One source per BS §4.9(a) name, so a scan can be *complete*."""
    return [detection_corpus(*texts, name=ref.name)
            for ref in REQUIRED_REFERENCES
            if ref.kind is ReferenceKind.DETECTION_CORPUS]


def complete_screen(*texts, dev=(), **cfg):
    """A screen with every non-optional BS §4.9 source registered."""
    sources = all_detection_sources(*texts)
    sources.append(dev_split_source(dev))
    return DecontaminationScreen(sources, config=ScreenConfig(**cfg))


def codes(report):
    return {f.code for f in report.findings}


# =====================================================================
# Shingling (BS §4.9: "13-gram containment checks")
# =====================================================================


def test_ngram_window_is_thirteen():
    assert NGRAM_N == 13


def test_ngram_count_is_words_minus_window_plus_one():
    words = words_with_offsets(SHARED)
    assert len(ngrams(SHARED)) == len(words) - NGRAM_N + 1


def test_text_shorter_than_the_window_yields_no_ngrams():
    assert ngrams("only a handful of words here") == []


def test_ngrams_are_case_folded_so_recasing_does_not_evade_the_screen():
    assert ngrams(SHARED.upper()) == ngrams(SHARED)


def test_word_offsets_index_the_original_text():
    words = words_with_offsets("  alpha beta")
    assert (words[0].start, words[0].end) == (2, 7)
    assert words[0].key == "alpha"


def test_offsets_survive_case_folding():
    """Matching is folded; character measurement is not, so the 50-character
    rule is measured against the text as written."""
    words = words_with_offsets("ALPHA beta")
    assert words[0].key == "alpha"
    assert (words[0].start, words[0].end) == (0, 5)


# =====================================================================
# Sources
# =====================================================================


def test_index_contains_its_own_shingles_and_not_others():
    index = detection_corpus(SHARED)
    assert index.contains(ngrams(SHARED)[0])
    assert not index.contains(ngrams(OTHER)[0])


def test_empty_index_contains_nothing():
    assert not NgramIndex("HC3", ReferenceKind.DETECTION_CORPUS).contains("anything")


def test_index_holds_only_what_it_is_given():
    """The container fabricates nothing: no text in, no shingles out."""
    assert NgramIndex("HC3", ReferenceKind.DETECTION_CORPUS).shingles == 0


def test_dev_index_excludes_a_samples_own_contribution():
    """Without this a DEV sample matches the DEV split at 100% because it *is*
    the DEV split, and every sample would report as contaminated by itself."""
    index = dev_split_source([("H-01-B100-0001", SHARED)])
    gram = ngrams(SHARED)[0]
    assert index.contains(gram)
    assert not index.contains_excluding(gram, "H-01-B100-0001")
    assert index.contains_excluding(gram, "H-01-B100-0002")


def test_scanning_a_dev_sample_against_the_dev_split_does_not_match_itself():
    screen = complete_screen(OTHER, dev=[("H-01-B100-0001", SHARED)])
    scan = screen.scan("H-01-B100-0001", SHARED, split="dev")
    dev = [s for s in scan.sources if s.kind == ReferenceKind.DEV_SPLIT.value][0]
    assert dev.matched_ngrams == 0


def test_dev_index_reports_shared_material_from_another_sample():
    screen = complete_screen(OTHER, dev=[("H-01-B100-0001", SHARED)])
    scan = screen.scan("T-01-B100-0002", SHARED, split="test")
    dev = [s for s in scan.sources if s.kind == ReferenceKind.DEV_SPLIT.value][0]
    assert dev.matched_ngrams == len(ngrams(SHARED))


# =====================================================================
# The integrity rule: an unrun scan is not a passed scan
# =====================================================================


def test_a_scan_with_no_sources_is_incomplete_not_clean():
    """The single most important assertion in this module."""
    scan = DecontaminationScreen([]).scan("H-01-B100-0001", SHARED, split="test")
    assert scan.verdict is Verdict.INCOMPLETE
    assert scan.verdict is not Verdict.CLEAN
    assert not scan.passed


def test_incomplete_is_not_contaminated_either():
    """Unknown is its own state; overstating it would be as wrong as
    understating it."""
    scan = DecontaminationScreen([]).scan("H-01-B100-0001", SHARED, split="test")
    assert not scan.contaminated


def test_missing_sources_are_named_not_merely_counted():
    scan = DecontaminationScreen([]).scan("H-01-B100-0001", SHARED)
    assert "HC3" in scan.missing_sources
    assert "DEV split" in scan.missing_sources


def test_the_optional_pretraining_index_does_not_make_a_scan_incomplete():
    """§4.9(b) says "where available" — the only source class the
    specification itself qualifies."""
    screen = complete_screen(OTHER, dev=[])
    assert "pretraining index" not in screen.missing_sources()
    assert "pretraining index" in screen.optional_missing()
    scan = screen.scan("H-01-B100-0001", SHARED, split="test")
    assert scan.complete
    assert "OPTIONAL_SOURCE_ABSENT" in codes(scan.report)


def test_a_complete_scan_finding_nothing_is_clean():
    scan = complete_screen(OTHER, dev=[]).scan("H-01-B100-0001", SHARED,
                                               split="test")
    assert scan.verdict is Verdict.CLEAN
    assert scan.passed


def test_a_hit_is_conclusive_even_when_the_scan_is_incomplete():
    """Absence of evidence is inconclusive; presence of evidence is not."""
    screen = DecontaminationScreen([detection_corpus(SHARED)])
    scan = screen.scan("H-01-B100-0001", SHARED, split="test")
    assert scan.missing_sources          # incomplete
    assert scan.verdict is Verdict.CONTAMINATED


def test_a_sample_too_short_to_screen_is_incomplete_not_clean():
    scan = complete_screen(OTHER, dev=[]).scan("H-01-B050-0001",
                                               "far too short to shingle",
                                               split="test")
    assert "TOO_SHORT_TO_SCREEN" in codes(scan.report)
    assert scan.verdict is Verdict.INCOMPLETE


# =====================================================================
# Stage severity (CAS §3.7 candidacy vs BS §9.1(d) release)
# =====================================================================


def test_incompleteness_only_warns_at_candidacy():
    """Erroring here would make the corpus unbuildable while TD-X01 is
    unresolved: no candidate could ever be submitted."""
    scan = DecontaminationScreen([]).scan("H-01-B100-0001", SHARED,
                                          stage=Stage.CANDIDACY)
    assert "DECONTAMINATION_INCOMPLETE" in codes(scan.report)
    assert scan.report.ok  # warnings only


def test_incompleteness_errors_at_release():
    scan = DecontaminationScreen([]).scan("H-01-B100-0001", SHARED,
                                          split="test", stage=Stage.RELEASE)
    assert not scan.report.ok
    assert "DECONTAMINATION_INCOMPLETE" in {f.code for f in scan.report.errors}


def test_stage_changes_severity_but_never_the_verdict():
    kwargs = dict(split="test")
    a = DecontaminationScreen([]).scan("X", SHARED, stage=Stage.CANDIDACY, **kwargs)
    b = DecontaminationScreen([]).scan("X", SHARED, stage=Stage.RELEASE, **kwargs)
    assert a.verdict is b.verdict is Verdict.INCOMPLETE


# =====================================================================
# The one numeric rule §4.9 states (BS §4.9, 50 contiguous characters)
# =====================================================================


def test_contiguous_limit_is_fifty_characters():
    assert CONTIGUOUS_CHAR_LIMIT == 50


def test_test_sample_overlapping_a_public_corpus_is_contaminated():
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="test")
    assert scan.verdict is Verdict.CONTAMINATED
    assert "PUBLIC_CORPUS_OVERLAP" in {f.code for f in scan.report.errors}


def test_hidden_is_protected_the_same_as_test():
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="hidden")
    assert scan.verdict is Verdict.CONTAMINATED


def test_the_screen_reports_the_overlap_and_does_not_choose_the_remedy():
    """§4.9 offers two remedies — replace, or move to DEV. Picking one would
    be repairing rather than reporting."""
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="test")
    message = [f.message for f in scan.report.errors
               if f.code == "PUBLIC_CORPUS_OVERLAP"][0]
    assert "replaced or moved to DEV" in message
    assert scan.split == "test"  # nothing was moved


def test_overlap_inside_dev_is_the_specified_end_state_not_a_defect():
    """BS §2.3 assumes DEV is contaminated, and §4.9 makes DEV the destination
    for contaminated TEST/HIDDEN material."""
    scan = complete_screen(SHARED, dev=[]).scan("H-01-B100-0001", SHARED,
                                                split="dev")
    assert scan.verdict is Verdict.CLEAN
    assert "PUBLIC_CORPUS_OVERLAP_IN_DEV" in codes(scan.report)
    assert scan.report.ok


def test_overlap_on_an_unassigned_candidate_constrains_its_split():
    """Split is assigned at Stage 8 (§4.2), so a candidate usually has none
    yet; the overlap must survive as an input to that decision."""
    scan = complete_screen(SHARED, dev=[]).scan("H-01-B100-0001", SHARED)
    assert scan.verdict is Verdict.CLEAN
    assert scan.dev_only
    assert "PUBLIC_CORPUS_OVERLAP_UNASSIGNED" in codes(scan.report)


def test_a_clean_sample_is_not_marked_dev_only():
    scan = complete_screen(OTHER, dev=[]).scan("H-01-B100-0001", SHARED)
    assert not scan.dev_only


def test_test_sample_overlapping_the_dev_split_leaks_its_own_answer():
    screen = complete_screen(OTHER, dev=[("H-01-B100-0001", SHARED)])
    scan = screen.scan("T-01-B100-0002", SHARED, split="test")
    assert "DEV_SPLIT_OVERLAP" in {f.code for f in scan.report.errors}
    assert scan.verdict is Verdict.CONTAMINATED


def test_short_shared_run_below_the_limit_does_not_trigger_the_rule():
    """A shared 13-gram whose span is under 50 characters is measured and
    reported, but §4.9's rule does not fire."""
    short = "a b c d e f g h i j k l m"          # 13 one-character words
    padded = f"{short} zzz yyy xxx www vvv uuu"
    screen = complete_screen(short, dev=[])
    scan = screen.scan("H-01-B100-0001", padded, split="test")
    hit = [s for s in scan.sources if s.source == "HC3"][0]
    assert hit.matched_ngrams == 1
    assert hit.longest_contiguous_chars < CONTIGUOUS_CHAR_LIMIT
    assert scan.verdict is Verdict.CLEAN


def test_contiguous_characters_are_measured_from_the_run_of_matching_ngrams():
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="test")
    hit = [s for s in scan.sources if s.source == "HC3"][0]
    assert hit.matched_ngrams == len(ngrams(SHARED))
    assert hit.longest_contiguous_chars == len(SHARED)
    assert hit.containment == pytest.approx(1.0)


def test_estimated_char_measure_is_labelled_as_an_estimate():
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="test")
    assert all(not s.exact_char_measure for s in scan.sources)


def test_a_source_may_measure_the_overlap_exactly():
    class ExactSource:
        name, kind, version = "HC3", ReferenceKind.DETECTION_CORPUS.value, "v1"

        def contains(self, ngram):
            return False

        def max_contiguous_chars(self, text):
            return 999

    scan = DecontaminationScreen([ExactSource()]).scan("T-01-B100-0001", SHARED,
                                                       split="test")
    hit = scan.sources[0]
    assert hit.exact_char_measure
    assert hit.longest_contiguous_chars == 999
    assert scan.verdict is Verdict.CONTAMINATED


# =====================================================================
# The threshold §4.9 does not state (TD-G10)
# =====================================================================


def test_no_containment_ratio_threshold_is_invented():
    assert ScreenConfig().containment_review_threshold is None


def test_the_unset_threshold_is_reported_rather_than_passed_silently():
    scan = complete_screen(OTHER, dev=[]).scan("H-01-B100-0001", SHARED)
    assert "CONTAINMENT_THRESHOLD_UNSET" in codes(scan.report)


def test_full_containment_alone_does_not_decide_anything():
    """Every 13-gram shared, but no 50-character contiguous run: §4.9's stated
    rule does not fire, and no invented ratio rule fires either."""
    words = "a b c d e f g h i j k l m"  # exactly one 13-gram, 25 characters
    screen = complete_screen(words, dev=[])
    scan = screen.scan("H-01-B100-0001", words, split="test")
    hit = [s for s in scan.sources if s.source == "HC3"][0]
    assert hit.containment == pytest.approx(1.0)
    assert hit.longest_contiguous_chars < CONTIGUOUS_CHAR_LIMIT
    assert scan.verdict is Verdict.CLEAN


def test_a_configured_threshold_engages_when_governance_sets_one():
    screen = complete_screen(SHARED, dev=[], containment_review_threshold=0.5)
    scan = screen.scan("H-01-B100-0001", SHARED, split="dev")
    assert "CONTAINMENT_ABOVE_REVIEW_THRESHOLD" in codes(scan.report)
    assert "CONTAINMENT_THRESHOLD_UNSET" not in codes(scan.report)


# =====================================================================
# Corpus scan and the manifest block (BS §5.4)
# =====================================================================


def test_scan_corpus_screens_every_sample():
    summary = complete_screen(UNRELATED, dev=[]).scan_corpus([
        ("H-01-B100-0001", SHARED, "dev"),
        ("T-01-B100-0002", OTHER, "test"),
    ])
    assert len(summary.scans) == 2
    assert summary.by_verdict()["clean"] == 2


def test_manifest_block_reports_incomplete_rather_than_passed():
    summary = DecontaminationScreen([]).scan_corpus([("H-01-B100-0001", SHARED, "dev")])
    block = summary.to_manifest(TS)
    assert block["status"] == "incomplete"
    assert block["sources_consulted"] == []
    assert "HC3" in block["sources_missing"]


def test_manifest_block_never_claims_a_boolean_pass():
    """A manifest reading `"passed": true` after consulting nothing is the
    misrepresentation this module exists to prevent."""
    block = DecontaminationScreen([]).scan_corpus(
        [("H-01-B100-0001", SHARED, "dev")]).to_manifest(TS)
    assert block["status"] in {"passed", "incomplete", "contaminated"}
    assert "passed" not in {k for k, v in block.items() if isinstance(v, bool)}


def test_manifest_block_records_the_parameters_the_scan_ran_under():
    block = complete_screen(OTHER, dev=[]).scan_corpus(
        [("H-01-B100-0001", SHARED, "dev")]).to_manifest(TS)
    assert block["ngram_n"] == NGRAM_N
    assert block["contiguous_char_limit"] == CONTIGUOUS_CHAR_LIMIT
    assert block["containment_review_threshold"] is None
    assert block["scanned_at"] == TS


def test_manifest_block_reports_contamination_over_incompleteness():
    summary = DecontaminationScreen([detection_corpus(SHARED)]).scan_corpus(
        [("T-01-B100-0001", SHARED, "test")])
    block = summary.to_manifest(TS)
    assert block["status"] == "contaminated"
    assert block["contaminated"] == ["T-01-B100-0001"]


def test_manifest_block_lists_the_sources_actually_consulted():
    block = complete_screen(OTHER, dev=[]).scan_corpus(
        [("H-01-B100-0001", SHARED, "dev")]).to_manifest(TS)
    names = {s["name"] for s in block["sources_consulted"]}
    assert "HC3" in names and "DEV split" in names
    assert all("version" in s for s in block["sources_consulted"])


# =====================================================================
# Release gate (BS §9.1(d))
# =====================================================================


def test_release_gate_passes_a_complete_clean_scan():
    summary = complete_screen(OTHER, dev=[]).scan_corpus(
        [("H-01-B100-0001", SHARED, "dev")], stage=Stage.RELEASE)
    assert release_gate(summary).ok


def test_release_gate_blocks_an_empty_scan():
    """A summary over zero samples satisfies "nothing contaminated" vacuously."""
    report = release_gate(ScanSummary(stage=Stage.RELEASE))
    assert "DECONTAMINATION_NOT_RUN" in {f.code for f in report.errors}


def test_release_gate_blocks_an_incomplete_scan():
    summary = DecontaminationScreen([]).scan_corpus(
        [("H-01-B100-0001", SHARED, "dev")], stage=Stage.RELEASE)
    report = release_gate(summary)
    assert not report.ok
    assert "DECONTAMINATION_INCOMPLETE" in {f.code for f in report.errors}


def test_release_gate_blocks_a_contaminated_sample():
    summary = complete_screen(SHARED, dev=[]).scan_corpus(
        [("T-01-B100-0001", SHARED, "test")], stage=Stage.RELEASE)
    report = release_gate(summary)
    assert "CONTAMINATED_SAMPLE_IN_RELEASE" in {f.code for f in report.errors}


def test_release_gate_blocks_an_unscreenable_sample():
    summary = complete_screen(OTHER, dev=[]).scan_corpus(
        [("H-01-B050-0001", "too short to shingle here", "dev")],
        stage=Stage.RELEASE)
    report = release_gate(summary)
    assert "SAMPLE_NOT_FULLY_SCREENED" in {f.code for f in report.errors}


def test_release_gate_names_every_offending_sample():
    summary = complete_screen(SHARED, dev=[]).scan_corpus([
        ("T-01-B100-0001", SHARED, "test"),
        ("T-01-B100-0002", SHARED, "hidden"),
        ("H-01-B100-0003", OTHER, "dev"),
    ], stage=Stage.RELEASE)
    offenders = {f.sample_id for f in release_gate(summary).errors}
    assert offenders == {"T-01-B100-0001", "T-01-B100-0002"}


# =====================================================================
# Reporting, not repairing
# =====================================================================


def test_the_screen_never_mutates_the_text_it_screens():
    text = SHARED
    complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", text, split="test")
    assert text == SHARED


def test_the_screen_never_reassigns_a_split():
    scan = complete_screen(SHARED, dev=[]).scan("T-01-B100-0001", SHARED,
                                                split="test")
    assert scan.split == "test"
    assert scan.to_dict()["split"] == "test"


def test_required_reference_list_matches_the_sources_bs_4_9_names():
    names = {r.name for r in REQUIRED_REFERENCES}
    assert {"HC3", "GPT-2 output corpus", "M4", "RAID", "MGTBench"} <= names
    assert "DEV split" in names


def test_registering_extra_sources_is_admissible():
    """§4.9(a) names its corpora by example, so the list is a floor."""
    screen = complete_screen(OTHER, dev=[])
    screen.sources.append(detection_corpus(OTHER, name="SomeNewCorpus"))
    assert screen.missing_sources() == []
    assert len(screen.sources_consulted()) == len(screen.sources)


def test_dev_index_counts_are_derived_from_what_was_added():
    index = DevSplitIndex()
    index.add_sample("H-01-B100-0001", SHARED)
    index.add_sample("H-01-B100-0002", OTHER)
    assert index.samples == 2
    assert index.shingles == len(set(ngrams(SHARED)) | set(ngrams(OTHER)))
