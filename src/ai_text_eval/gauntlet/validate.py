"""Validators for corpus integrity and release acceptance.

Four layers, each citing the section it enforces:

  validate_sample    Section 4.7 + 5.2 — per-record schema and cross-field
                     consistency.
  validate_manifest  Section 5.4 — manifest completeness and checksums.
  validate_splits    Sections 2.3, 4.2, 4.9 — split discipline, provenance
                     admissibility, id uniqueness.
  validate_release   Section 9.1 — corpus release acceptance.

Design rule: validators *report*, they never repair. A validator that quietly
fixed a bucket boundary or filled in a missing tier would be changing ground
truth to make a gate pass, which Section 1.1 and the project's standing rules
forbid. Every finding carries a section reference so a reader can check the
call against the specification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ai_text_eval.gauntlet.findings import Finding, Report, Severity
from ai_text_eval.gauntlet.loader import Corpus, Manifest
from ai_text_eval.gauntlet.registry import FieldRegistry, load_field_registry
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.spec import (
    ADMISSIBLE_TIERS_TEST_HIDDEN,
    AI_INVOLVED_LABELS,
    CELL_TARGETS,
    DETECTOR_FAMILIES,
    DIFFICULTIES,
    FAIRNESS_GATED_CATEGORIES,
    FAIRNESS_GATED_TIERS,
    ID_PATTERN,
    LABELS,
    METADATA_SCHEMA_VERSION,
    PII_STATUSES,
    POOLED_HUMAN_TEST_MINIMUM,
    PROVENANCE_TIERS,
    SPAN_MAP_REQUIRED_CATEGORIES,
    SPAN_MAP_REQUIRED_LABELS,
    SPLITS,
    TRACKS,
    bucket_for,
    count_words,
    load_categories,
    load_failure_modes,
)


# Finding, Report, Severity live in findings.py (shared with the registry and
# lifecycle modules); re-exported here for backwards compatibility.
__all_findings__ = (Finding, Report, Severity)


# -- Section 4.7 / 5.2: per-sample metadata ------------------------------

def validate_sample(sample: Sample, categories: dict | None = None,
                    registry: FieldRegistry | None = None) -> Report:
    """Schema completeness and cross-field consistency for one record.

    Field presence, unregistered-field, and schema-version checks are
    delegated to the field registry (R-01) so there is a single source of
    truth for which fields exist; this function owns the cross-field semantic
    rules of CAS §4.4.
    """
    r = Report(checked=1)
    cats = categories if categories is not None else load_categories()
    reg = registry if registry is not None else load_field_registry()
    sid = sample.id or f"{sample.source_file}:{sample.source_line}"
    schema = str(sample.get("schema_version") or METADATA_SCHEMA_VERSION)
    schema_v = reg.parse_version(schema)

    # Field-level checks (presence, unregistered fields, version arithmetic,
    # lineage vocabulary) come from the registry — R-01.
    r.extend(reg.validate_fields(sample))

    # Enumerations.
    if sample.split is not None and sample.split not in SPLITS:
        r.error("2.3", "BAD_SPLIT", f"split {sample.split!r} not in {SPLITS}", sid)
    if sample.track is not None and sample.track not in TRACKS:
        r.error("2.1", "BAD_TRACK", f"track {sample.track!r} not in {TRACKS}", sid)
    if sample.label is not None and sample.label not in LABELS:
        r.error("4.1", "BAD_LABEL", f"label {sample.label!r} not in {LABELS}", sid)
    if sample.provenance_tier is not None and sample.provenance_tier not in PROVENANCE_TIERS:
        r.error("4.2", "BAD_TIER", f"provenance_tier {sample.provenance_tier!r}", sid)
    if sample.difficulty is not None and sample.difficulty not in DIFFICULTIES:
        r.error("4.8", "BAD_DIFFICULTY", f"difficulty {sample.difficulty!r}", sid)
    if sample.get("pii_status") is not None and sample.get("pii_status") not in PII_STATUSES:
        r.error("4.10", "BAD_PII_STATUS", f"pii_status {sample.get('pii_status')!r}", sid)

    # id format and agreement with its own fields (Section 5.2).
    if sample.id is not None:
        m = ID_PATTERN.match(sample.id)
        if not m:
            r.error("5.2", "BAD_ID_FORMAT",
                    "id must match CATEGORY-BUCKET-NNNN, e.g. V-05-B250-0031", sid)
        else:
            if sample.category is not None and m.group("category") != sample.category:
                r.error("5.2", "ID_CATEGORY_MISMATCH",
                        f"id encodes category {m.group('category')} but field says "
                        f"{sample.category}", sid)
            if sample.length_bucket is not None and m.group("bucket") != sample.length_bucket:
                r.error("5.2", "ID_BUCKET_MISMATCH",
                        f"id encodes bucket {m.group('bucket')} but field says "
                        f"{sample.length_bucket}", sid)
            if sample.track is not None and m.group("track") != sample.track:
                r.error("5.2", "ID_TRACK_MISMATCH",
                        f"id encodes track {m.group('track')} but field says "
                        f"{sample.track}", sid)

    # Category must exist in the registry, and track must agree with it.
    if sample.category is not None:
        if sample.category not in cats:
            r.error("3", "UNKNOWN_CATEGORY",
                    f"category {sample.category!r} not in the registry", sid)
        else:
            expected_track = cats[sample.category]["track"]
            if sample.track is not None and sample.track != expected_track:
                r.error("3", "TRACK_CATEGORY_MISMATCH",
                        f"category {sample.category} belongs to track "
                        f"{expected_track}, not {sample.track}", sid)
            expected_label = cats[sample.category].get("expected_label")
            if expected_label and sample.label and sample.label != expected_label:
                r.error("3", "LABEL_CATEGORY_MISMATCH",
                        f"category {sample.category} expects label "
                        f"{expected_label}, got {sample.label}", sid)

    # Length: the stored count must match the harness counter, and the bucket
    # must match the count. Section 2.5 makes bucket membership ground truth.
    if sample.text is not None:
        actual = count_words(sample.text)
        if sample.length_words is not None and sample.length_words != actual:
            r.error("2.5", "LENGTH_MISMATCH",
                    f"length_words={sample.length_words} but harness counter "
                    f"says {actual}", sid)
        derived = bucket_for(actual)
        if derived is None:
            r.error("2.5", "NO_BUCKET",
                    f"{actual} words falls between buckets; Section 2.5 ranges "
                    "are non-contiguous and the sample belongs to none", sid)
        elif sample.length_bucket is not None and derived != sample.length_bucket:
            r.error("2.5", "BUCKET_MISMATCH",
                    f"{actual} words is {derived}, not {sample.length_bucket}", sid)

    # Label / numeric-field coherence (Section 4.1).
    share = sample.ai_token_share
    if share is not None and not 0.0 <= share <= 1.0:
        r.error("4.1", "SHARE_RANGE", f"ai_token_share {share} outside [0,1]", sid)
    if sample.label == "HUMAN" and share not in (None, 0.0):
        r.error("4.1", "SHARE_LABEL_CONFLICT",
                f"label HUMAN requires ai_token_share 0.0, got {share}", sid)
    if sample.label == "AI" and share not in (None, 1.0):
        r.error("4.1", "SHARE_LABEL_CONFLICT",
                f"label AI requires ai_token_share 1.0, got {share}", sid)

    needs_spans = (
        sample.label in SPAN_MAP_REQUIRED_LABELS
        or sample.category in SPAN_MAP_REQUIRED_CATEGORIES
    )
    if needs_spans and not sample.span_map:
        r.error("4.1", "SPAN_MAP_REQUIRED",
                "span_map is required for COLLAB_MIXED and splice categories", sid)
    if sample.span_map:
        shape_ok = True
        for entry in sample.span_map:
            if (not isinstance(entry, (list, tuple)) or len(entry) != 3
                    or not isinstance(entry[0], int) or not isinstance(entry[1], int)
                    or entry[2] not in ("human", "ai")):
                r.error("4.1", "BAD_SPAN_ENTRY",
                        f"span entry {entry!r} must be [start,end,'human'|'ai']", sid)
                shape_ok = False
                break
        # CAS §4.2: spans must tile the text without overlap. Only checkable
        # once the entries are well-formed and the text is present.
        if shape_ok and sample.text is not None:
            r.extend(_check_span_tiling(sample, sid))

    # Generator record (CAS §4.2, §4.4). CANONICAL ruling TD-A02 (ratified
    # 2026-08-05): required for every non-HUMAN label — a model was involved in
    # the base, the edit, or the mix — and forbidden for HUMAN (its presence
    # there is a P2 alarm). The earlier carve-out for HUMAN_AI_EDITED was a
    # defect: the editing model still needs a record. Do not reopen without a
    # CAS §14.3 amendment.
    if sample.label is not None and sample.label != "HUMAN":
        if sample.generator is None:
            r.error("4.4", "GENERATOR_REQUIRED",
                    f"label {sample.label} requires a generator record; a model "
                    "was involved and its configuration must be recorded", sid)
    if sample.label == "HUMAN" and sample.generator is not None:
        r.error("4.2", "GENERATOR_ON_HUMAN",
                "generator must be absent for HUMAN samples (P2 alarm)", sid)
    if sample.generator is not None:
        for key in ("family", "model_version", "provider", "prompt_style",
                    "decoding", "request_date", "config_ref"):
            if key not in sample.generator:
                r.error("4.4", "GENERATOR_INCOMPLETE",
                        f"generator record missing {key!r}", sid)

    # Hybrid labels imply a model-origin share strictly between 0 and 1
    # (CAS §4.4). The pure-label shares are checked above.
    if sample.label in ("AI_HUMAN_EDITED", "HUMAN_AI_EDITED", "COLLAB_MIXED"):
        if share is not None and not 0.0 < share < 1.0:
            r.error("4.4", "HYBRID_SHARE_RANGE",
                    f"hybrid label {sample.label} requires 0 < ai_token_share < 1, "
                    f"got {share}", sid)

    # Track V: a non-empty transform record with a lineage link to a base
    # (CAS §4.4). The lineage requirement applies once the schema supports the
    # field (v2+); v1 predates lineage and cannot carry it.
    if sample.track == "V":
        if not sample.transforms:
            r.error("4.4", "V_TRANSFORM_REQUIRED",
                    "Track V requires a non-empty transform record", sid)
        if schema_v >= 2:
            lineage = sample.get("lineage") or []
            has_base = any(isinstance(e, dict) and e.get("relation") == "derived_from"
                           for e in lineage)
            if not has_base:
                r.error("4.4", "V_LINEAGE_REQUIRED",
                        "Track V requires a derived_from lineage link to a base "
                        "(P10)", sid)

    # Difficulty / panel binding (CAS §4.2, §7.3), schema v2+. An empirical
    # difficulty must name the panel version that produced it; a hand-set
    # value with no panel is difficulty invented.
    if schema_v >= 2 and "difficulty_provisional" in sample.raw:
        provisional = bool(sample.get("difficulty_provisional"))
        panel = sample.get("difficulty_panel_version")
        if not provisional and not panel:
            r.error("7.3", "DIFFICULTY_WITHOUT_PANEL",
                    "empirical difficulty (difficulty_provisional=false) must "
                    "name the reference panel version that produced it", sid)

    # Free-text fields that Section 4.7 makes mandatory.
    for name, section in (("rationale", "4.7"), ("target_weakness", "4.7")):
        v = sample.get(name)
        if isinstance(v, str) and not v.strip():
            r.error(section, "EMPTY_REQUIRED_TEXT", f"{name} present but empty", sid)

    ec = sample.get("expected_confusions")
    if isinstance(ec, str) and ec.strip():
        for code in (c.strip() for c in ec.split(",")):
            if code and code not in DETECTOR_FAMILIES:
                r.error("6.1", "BAD_DF_CODE",
                        f"expected_confusions {code!r} not a DF code", sid)

    return r


def _check_span_tiling(sample: Sample, sid: str) -> Report:
    """CAS §4.2: spans must tile the text without overlap.

    Tiling means the spans, in order, start at 0, meet end-to-start with no
    gap and no overlap, and the last span ends exactly at the text length.
    Origin of a span is a production fact (§3.5); this only checks geometry.
    """
    r = Report()
    spans = sorted(sample.span_map, key=lambda e: e[0])
    text_len = len(sample.text)
    cursor = 0
    for start, end, _origin in spans:
        if start < 0 or end > text_len or start >= end:
            r.error("4.2", "SPAN_OUT_OF_RANGE",
                    f"span [{start},{end}] is not a valid range within a text of "
                    f"length {text_len}", sid)
            return r
        if start < cursor:
            r.error("4.2", "SPAN_OVERLAP",
                    f"span [{start},{end}] overlaps the previous span ending at "
                    f"{cursor}", sid)
            return r
        if start > cursor:
            r.error("4.2", "SPAN_GAP",
                    f"gap between {cursor} and {start}; spans must tile the text "
                    "without gaps", sid)
            return r
        cursor = end
    if cursor != text_len:
        r.error("4.2", "SPAN_INCOMPLETE",
                f"spans cover [0,{cursor}] but the text is {text_len} characters; "
                "the span map must tile the whole text", sid)
    return r


# -- Section 4.4: cross-sample relationship resolution -------------------

def validate_relationships(corpus: Corpus,
                           registry: FieldRegistry | None = None) -> Report:
    """CAS §4.4: every declared relationship resolves both ways (P10).

    Cross-sample by nature, so this is separate from validate_sample. A
    derived_from or supersedes target must exist; a mutual relationship
    (tell_pair, mimicry_pair) must be declared reciprocally by its target.
    """
    reg = registry if registry is not None else load_field_registry()
    r = Report(checked=len(corpus))
    mutual = reg.mutual_relationship_types
    known = reg.relationship_types

    by_id: dict[str, Sample] = {s.id: s for s in corpus.samples if s.id}

    def lineage_of(sample: Sample) -> list[dict]:
        v = sample.get("lineage")
        return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []

    for s in corpus.samples:
        for entry in lineage_of(s):
            relation = entry.get("relation")
            target = entry.get("target")
            if relation not in known or not isinstance(target, str):
                continue  # shape already reported by validate_fields
            if target not in by_id:
                r.error("4.4", "LINEAGE_TARGET_MISSING",
                        f"lineage {relation} -> {target} does not resolve; the "
                        "target is not in the corpus", s.id)
                continue
            if relation in mutual:
                back = lineage_of(by_id[target])
                if not any(e.get("relation") == relation and e.get("target") == s.id
                           for e in back):
                    r.error("4.4", "RELATIONSHIP_NOT_MUTUAL",
                            f"{relation} to {target} is not declared reciprocally; "
                            f"{target} must declare {relation} back to {s.id}", s.id)
    return r


# -- Section 5.4: manifest -----------------------------------------------

def validate_manifest(manifest: Manifest, corpus_root: Path | None = None) -> Report:
    """Manifest completeness (Section 5.4) and file checksums (Section 2.4)."""
    r = Report(checked=1)
    required = ("corpus_version", "metadata_schema_version", "splits",
                "checksums", "axis_vocabularies", "covering_matrix",
                "decontamination", "category_exemptions")
    for key in required:
        if key not in manifest.raw:
            r.error("5.4", "MANIFEST_MISSING_KEY", f"manifest lacks {key!r}")

    if manifest.corpus_version is None:
        r.error("2.4", "NO_CORPUS_VERSION",
                "every reported result must cite an exact corpus version")

    decon = manifest.raw.get("decontamination")
    if isinstance(decon, dict) and decon.get("status") != "passed":
        r.error("4.9", "DECONTAMINATION_NOT_PASSED",
                f"decontamination status is {decon.get('status')!r}; Section 9.1(d) "
                "requires a passed scan shipped in the manifest")

    cov = manifest.raw.get("covering_matrix")
    if isinstance(cov, dict) and cov.get("verified") is not True:
        r.error("2.7", "COVERING_MATRIX_UNVERIFIED",
                "the covering design must verify mechanically at release time")

    # Checksums (Section 2.4: releases are immutable and checksummed).
    root = Path(corpus_root) if corpus_root else (
        manifest.path.parent if manifest.path else None)
    if root is not None:
        for rel, expected in manifest.checksums.items():
            path = root / rel
            if not path.is_file():
                r.error("2.4", "CHECKSUM_FILE_MISSING",
                        f"manifest lists {rel} but the file is absent")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                r.error("2.4", "CHECKSUM_MISMATCH",
                        f"{rel}: manifest says {expected[:12]}…, file is {actual[:12]}…")
    return r


# -- Sections 2.3 / 4.2: split discipline --------------------------------

def validate_splits(corpus: Corpus) -> Report:
    """Split membership, id uniqueness, and provenance admissibility."""
    r = Report(checked=len(corpus))

    seen: dict[str, Sample] = {}
    for s in corpus.samples:
        if s.id is None:
            continue
        if s.id in seen:
            prior = seen[s.id]
            r.error("5.2", "DUPLICATE_ID",
                    f"id reused (also at {prior.source_file}:{prior.source_line})",
                    s.id, f"{s.source_file}:{s.source_line}")
        else:
            seen[s.id] = s

    # Section 4.2: T3 is inadmissible as ground truth in TEST/HIDDEN.
    for s in corpus.samples:
        if s.split in ("test", "hidden") and s.provenance_tier is not None:
            if s.provenance_tier not in ADMISSIBLE_TIERS_TEST_HIDDEN:
                r.error("4.2", "INADMISSIBLE_TIER",
                        f"tier {s.provenance_tier} is not admissible in {s.split}; "
                        "T3 is allowed in DEV only", s.id)
        if s.split in ("test", "hidden") and s.noisy_label:
            r.error("4.2", "NOISY_LABEL_OUTSIDE_DEV",
                    "noisy_label=true is permitted only on T3 DEV samples", s.id)
        if s.noisy_label and s.provenance_tier not in (None, "T3"):
            r.error("5.2", "NOISY_LABEL_TIER_CONFLICT",
                    f"noisy_label=true but tier is {s.provenance_tier}", s.id)

        # Section 4.2 cell requirements.
        if s.category in FAIRNESS_GATED_CATEGORIES and s.provenance_tier is not None:
            if s.provenance_tier not in FAIRNESS_GATED_TIERS:
                r.error("4.2", "FAIRNESS_TIER",
                        f"{s.category} is fairness-gated and requires T1 or T2, "
                        f"got {s.provenance_tier}", s.id)
        if s.track == "X" and s.provenance_tier is not None and s.provenance_tier != "T1":
            r.error("4.2", "HYBRID_TIER",
                    "hybrid categories require T1 — the diff is the label", s.id)
        if s.label == "AI" and s.provenance_tier is not None and s.provenance_tier != "T1":
            r.error("4.2", "AI_TIER",
                    "AI categories require T1 by construction", s.id)
    return r


# -- Section 9.1: release acceptance -------------------------------------

def validate_release(corpus: Corpus, phase: str | None = None,
                     benchmark_dir: Path | None = None) -> Report:
    """Corpus release acceptance (Section 9.1).

    Criteria 9.1(c) annotation kappa and 9.1(f) canary solvability are not
    computable from corpus files alone: the first needs the adjudication log,
    the second needs a detector run. Both are reported as explicit gaps rather
    than silently skipped, so a green release report never implies a check
    that did not happen.
    """
    r = Report(checked=len(corpus))
    cats = load_categories(benchmark_dir)
    fms = load_failure_modes(benchmark_dir)
    manifest = corpus.manifest
    phase = phase or (manifest.phase if manifest else "v1.0")
    if phase not in CELL_TARGETS:
        raise ValueError(f"unknown phase {phase!r}; expected one of {tuple(CELL_TARGETS)}")

    # 9.1(g) metadata validation — schema-complete on every sample.
    reg = load_field_registry(benchmark_dir)
    for s in corpus.samples:
        r.extend(validate_sample(s, cats, reg))

    # Split and provenance discipline feed 9.1(b).
    r.extend(validate_splits(corpus))

    # CAS §4.4: declared relationships resolve both ways (P10).
    r.extend(validate_relationships(corpus, reg))

    # 9.1(a) coverage: every (category x bucket x split) cell meets targets.
    targets = CELL_TARGETS[phase]
    populated = corpus.cells()
    for cid, meta in cats.items():
        exempt = manifest.exempt_buckets(cid) if manifest else set()
        for bucket in ("B25", "B50", "B100", "B250", "B500", "B1000"):
            if bucket in exempt:
                continue
            for split, want in targets.items():
                have = len(populated.get((cid, bucket, split), []))
                if have < want:
                    r.error("9.1a", "CELL_UNDERPOPULATED",
                            f"cell ({cid}, {bucket}, {split}) has {have}/{want} "
                            f"samples for phase {phase}",
                            location=f"{cid}/{bucket}/{split}")

    # 9.1(a) aggregate: pooled human TEST minimum (Section 2.6).
    pooled = len([s for s in corpus.samples
                  if s.split == "test" and s.label == "HUMAN"])
    need = POOLED_HUMAN_TEST_MINIMUM.get(phase, 3000)
    if pooled < need:
        r.error("2.6", "POOLED_HUMAN_TEST_TOO_SMALL",
                f"{pooled} human TEST samples; {need} required at {phase}. "
                "An FPR in the 0.5-1.0 percent range cannot be estimated below this.")

    # 9.1(b) provenance refs resolve.
    root = corpus.root
    for s in corpus.samples:
        ref = s.get("provenance_ref")
        if isinstance(ref, str) and ref.strip() and root is not None:
            if not (root.parent / ref).exists() and not (root / ref).exists():
                r.error("9.1b", "PROVENANCE_REF_UNRESOLVED",
                        f"provenance_ref {ref!r} does not resolve", s.id)

    # 9.1(e) PII and license present on every sample.
    for s in corpus.samples:
        if not isinstance(s.get("license"), str) or not s.get("license", "").strip():
            r.error("9.1e", "LICENSE_MISSING", "license field empty", s.id)

    # 9.1(h) every category has a failure-mode entry and a stress neighbour.
    covered: set[str] = set()
    for fm in fms.values():
        covered |= set(fm.get("categories", []))
    for cid in cats:
        if cid not in covered:
            r.error("9.1h", "NO_FAILURE_MODE_ENTRY",
                    f"category {cid} has no entry in the Section 6.2 map",
                    location=cid)

    # Manifest.
    if manifest is None:
        r.error("5.4", "NO_MANIFEST", "corpus has no manifest.json")
    else:
        r.extend(validate_manifest(manifest, corpus.root))

    # Checks that cannot be performed from files alone.
    r.warn("9.1c", "NOT_MECHANICALLY_CHECKED",
           "annotation kappa >= 0.8 requires the adjudication log; verify out of band")
    r.warn("9.1f", "NOT_MECHANICALLY_CHECKED",
           "canary solvability (>=95 percent on D1) requires a baseline detector run")
    return r
