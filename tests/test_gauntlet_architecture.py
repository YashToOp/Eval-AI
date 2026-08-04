"""Architecture guard tests.

These assert *properties* rather than behaviours. Properties are what rot
silently: nothing fails when a vocabulary drifts out of sync or an import
crosses a boundary, until something downstream is quietly wrong. Approved at
the Milestone 2 architecture checkpoint.

Two guards:

1. **Vocabulary unification.** The field registry is the single source of
   truth for closed vocabularies (CAS §4.1). Runtime constants in `spec.py`
   are read from it, and these tests fail if the two ever diverge.
2. **Import direction.** The benchmark must never depend on the detector.
   "The benchmark is the product; the detector is a consumer of the
   benchmark" — if `gauntlet` could import detector internals, the instrument
   could co-evolve with the thing it measures.
"""

import ast
import json
from pathlib import Path

import pytest

from ai_text_eval.gauntlet import spec
from ai_text_eval.gauntlet.registry import load_field_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
GAUNTLET_DIR = REPO_ROOT / "src" / "ai_text_eval" / "gauntlet"


# =====================================================================
# 1. Vocabulary unification (CAS §4.1)
# =====================================================================

@pytest.mark.parametrize("constant,vocabulary", [
    ("TRACKS", "track"),
    ("LABELS", "label"),
    ("PROVENANCE_TIERS", "provenance_tier"),
    ("DIFFICULTIES", "difficulty"),
    ("SPLITS", "split"),
    ("PII_STATUSES", "pii_status"),
])
def test_runtime_vocabulary_matches_the_registry(constant, vocabulary):
    """Closed vocabularies are governed data; the runtime must not drift."""
    reg = load_field_registry()
    assert list(getattr(spec, constant)) == reg.vocabulary(vocabulary), (
        f"spec.{constant} has diverged from the {vocabulary!r} vocabulary in "
        "benchmark/field_registry.json. The registry is the single source of "
        "truth (CAS §4.1); update it, not the constant."
    )


def test_detector_families_match_the_axes_vocabulary():
    axes = json.loads((REPO_ROOT / "benchmark" / "axes.json").read_text())
    assert list(spec.DETECTOR_FAMILIES) == axes["detector_family"]


def test_length_bucket_names_match_the_registry():
    reg = load_field_registry()
    assert list(spec.LENGTH_BUCKETS) == reg.vocabulary("length_bucket")


def test_field_order_matches_the_registry_v1_order():
    reg = load_field_registry()
    assert list(spec.FIELD_ORDER) == reg.field_order("1")


def test_v1_field_order_is_the_bs_schema_order():
    """Guards the registry itself: BS §5.2 fixes this order, and a governance
    edit must not silently reshuffle it. Compared against a literal so the
    check cannot become tautological."""
    assert list(spec.FIELD_ORDER) == [
        "id", "schema_version", "corpus_version", "split", "text", "category",
        "track", "domain", "format", "language", "length_words",
        "length_bucket", "label", "ai_token_share", "span_map", "source_type",
        "provenance_tier", "provenance_ref", "generator", "transforms",
        "topic_group_id", "difficulty", "rationale", "target_weakness",
        "expected_confusions", "noisy_label", "license", "pii_status",
        "created", "notes",
    ]


def test_optional_fields_are_derived_from_the_registry():
    reg = load_field_registry()
    expected = {name for name, meta in reg.fields.items()
                if not meta.get("required", True)}
    assert set(spec.OPTIONAL_FIELDS) == expected


def test_expected_confusions_remains_optional():
    """Canonical ruling TD-A01, frozen 2026-08-05. CAS §4.2 governs."""
    assert "expected_confusions" in spec.OPTIONAL_FIELDS
    assert load_field_registry().fields["expected_confusions"]["required"] is False


def test_ai_involved_labels_is_every_label_except_human():
    """Derived, not duplicated: a future hybrid label added to the registry is
    included automatically rather than needing a second edit here."""
    assert spec.AI_INVOLVED_LABELS == frozenset(set(spec.LABELS) - {"HUMAN"})


def test_admissible_tiers_excludes_only_t3():
    assert spec.ADMISSIBLE_TIERS_TEST_HIDDEN == frozenset(
        set(spec.PROVENANCE_TIERS) - {"T3"})


def test_vocabularies_are_immutable_tuples():
    """A mutable module-level vocabulary could be edited at runtime, which
    would defeat the point of governing it as data."""
    for name in ("TRACKS", "LABELS", "PROVENANCE_TIERS", "DIFFICULTIES",
                 "SPLITS", "PII_STATUSES", "DETECTOR_FAMILIES", "FIELD_ORDER"):
        assert isinstance(getattr(spec, name), tuple), f"spec.{name} must be a tuple"


#: Constants that MUST be sourced from governed data, never re-declared.
GOVERNED_CONSTANTS = ("TRACKS", "LABELS", "PROVENANCE_TIERS", "DIFFICULTIES",
                      "SPLITS", "PII_STATUSES", "DETECTOR_FAMILIES",
                      "FIELD_ORDER", "OPTIONAL_FIELDS")


def _literal_string_collection(node: ast.AST) -> bool:
    """Whether an assignment's value is a literal collection of strings."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        elements = node.elts
    elif isinstance(node, ast.Call) and node.args:
        # frozenset({...}) / tuple([...]) wrapping a literal
        return _literal_string_collection(node.args[0])
    else:
        return False
    return bool(elements) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements
    )


@pytest.mark.parametrize("constant", GOVERNED_CONSTANTS)
def test_governed_vocabularies_are_not_hardcoded_in_spec(constant):
    """The equality tests above compare two reads of one file and so cannot
    detect drift — after unification there is no second source to drift from.
    This test carries the real weight: it fails if anyone re-introduces a
    literal vocabulary in spec.py, which is what would recreate the second
    source of truth in the first place (CAS §4.1).
    """
    tree = ast.parse((GAUNTLET_DIR / "spec.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if constant in targets and _literal_string_collection(node.value):
            pytest.fail(
                f"spec.{constant} is assigned a literal string collection. "
                "Closed vocabularies are governed data (CAS §4.1) and must be "
                "read from benchmark/field_registry.json, not re-declared in "
                "code — a literal here recreates the second source of truth "
                "that Milestone 2 removed."
            )


def test_missing_registry_is_a_hard_error_with_no_fallback(tmp_path, monkeypatch):
    """CAS §4.1: there is no code fallback for governed vocabularies."""
    monkeypatch.setattr(spec, "BENCHMARK_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="no code fallback"):
        spec._read_benchmark_json("field_registry.json")


def test_unknown_vocabulary_name_raises():
    with pytest.raises(KeyError, match="never inline"):
        spec._vocab("not_a_governed_vocabulary")


# =====================================================================
# 2. Import direction: benchmark must not depend on detector
# =====================================================================

#: Modules of the detector subsystem. `gauntlet` may not import any of them.
DETECTOR_MODULES = frozenset({
    "detectors", "engine", "conformal", "verdict", "spans", "metrics",
    "normalize", "attacks", "provenance", "evasion", "report", "dataset",
    "text_features", "cli",
})


def _imported_names(path: Path) -> set[str]:
    """Every `ai_text_eval.X` submodule referenced by imports in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:1] == ["ai_text_eval"] and len(parts) > 1:
                out.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:1] == ["ai_text_eval"] and len(parts) > 1:
                    out.add(parts[1])
    return out


def _gauntlet_modules() -> list[Path]:
    return sorted(p for p in GAUNTLET_DIR.glob("*.py"))


def test_gauntlet_package_is_non_empty():
    """Guard the guard: if the glob broke, the import test would pass vacuously."""
    assert len(_gauntlet_modules()) >= 8


@pytest.mark.parametrize("module", _gauntlet_modules(), ids=lambda p: p.name)
def test_gauntlet_never_imports_the_detector_subsystem(module):
    """The benchmark is the product; the detector is a consumer of it.

    If this fails, the instrument has been coupled to the thing it measures.
    The runner depends on a `.score(text)` contract only — composing a real
    detector belongs to the harness entry point, not to the package.
    """
    offenders = _imported_names(module) & DETECTOR_MODULES
    assert not offenders, (
        f"{module.name} imports detector module(s) {sorted(offenders)}. "
        "gauntlet/ must not depend on the detector subsystem; depend on the "
        "`.score(text)` protocol and let the harness inject an implementation."
    )


def test_detector_subsystem_never_imports_gauntlet():
    """The reverse direction: detector code must run without the benchmark."""
    detector_root = REPO_ROOT / "src" / "ai_text_eval"
    paths = [p for p in detector_root.rglob("*.py") if "gauntlet" not in p.parts]
    assert paths, "no detector modules found; the guard would pass vacuously"
    for path in paths:
        assert "gauntlet" not in _imported_names(path), (
            f"{path.relative_to(REPO_ROOT)} imports gauntlet; the detector "
            "subsystem must not depend on the benchmark."
        )


def test_runner_depends_only_on_a_scoring_protocol():
    """The single permitted bridge is structural typing, not an import."""
    source = (GAUNTLET_DIR / "runner.py").read_text(encoding="utf-8")
    assert "Protocol" in source and "def score" in source
    assert not (_imported_names(GAUNTLET_DIR / "runner.py") & DETECTOR_MODULES)


def test_findings_module_has_no_intra_package_dependencies():
    """findings.py is the shared primitive; if it grew a dependency on a
    checker, the one-way dependency graph would become a cycle."""
    assert _imported_names(GAUNTLET_DIR / "findings.py") == set()
