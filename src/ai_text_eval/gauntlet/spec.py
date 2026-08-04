"""GAUNTLET constants: the parts of the specification that are law.

Everything here transcribes a numbered section of docs/gauntlet-v1.0-spec.txt.
Each constant cites its section so a reader can check the code against the
governing document rather than against someone's memory of it.

Nothing in this module is a heuristic or a tuned value. Anything that could
drift — the category registry, axis vocabularies, the failure-mode map, tell
lists — lives in /benchmark as versioned data (P6, 7.4, 10.4), not here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

SPEC_VERSION = "1.0.0-draft"
METADATA_SCHEMA_VERSION = "1"

#: /benchmark — benchmark definition data, versioned separately from code.
BENCHMARK_DIR = Path(__file__).resolve().parents[3] / "benchmark"
#: /corpus — the data itself.
CORPUS_DIR = Path(__file__).resolve().parents[3] / "corpus"
#: /regression — permanent regression entries (Section 8).
REGRESSION_DIR = Path(__file__).resolve().parents[3] / "regression"


# -- Governed vocabularies (CAS §4.1) ------------------------------------
#
# Closed vocabularies are DATA, not code. They are read here from
# benchmark/field_registry.json (and benchmark/axes.json) so the registry is
# the single source of truth and a governance amendment cannot leave a stale
# copy behind in Python.
#
# The read is a direct JSON load rather than a call into registry.py, because
# registry.py imports this module; going the other way would be circular.
# Consequently this module owns *parsing* the vocabularies and registry.py
# owns *validating records against* them.
#
# Structural constants that are not vocabularies — bucket word ranges, cell
# targets, FPR operating points — stay in code below: they are arithmetic
# from the Benchmark Specification, not values governance adds to.


def _read_benchmark_json(filename: str) -> dict:
    path = BENCHMARK_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"benchmark definition missing: {path}. Closed vocabularies are "
            "governed data (CAS §4.1); there is no code fallback."
        )
    return json.loads(path.read_text(encoding="utf-8"))


_REGISTRY_DATA = _read_benchmark_json("field_registry.json")
_AXES_DATA = _read_benchmark_json("axes.json")


def _vocab(name: str) -> tuple[str, ...]:
    """A closed vocabulary from the field registry, as an immutable tuple."""
    values = _REGISTRY_DATA.get("vocabularies", {}).get(name)
    if not isinstance(values, list):
        raise KeyError(
            f"vocabulary {name!r} is not defined in the field registry; "
            "vocabularies are added through governance, never inline"
        )
    return tuple(values)


#: Section 2.1.
TRACKS = _vocab("track")

#: Section 4.1. The primary label is the authorship *process*, never the
#: artifact. Binary labels are named in the spec as the root cause of most
#: detector evaluation failures, so no binary alias is provided here.
LABELS = _vocab("label")

#: Section 4.2. T3 is provenance *inferred* and is inadmissible as ground
#: truth outside DEV.
PROVENANCE_TIERS = _vocab("provenance_tier")

#: Section 4.8.
DIFFICULTIES = _vocab("difficulty")

SPLITS = _vocab("split")

PII_STATUSES = _vocab("pii_status")

#: Section 6.1. Referenced by `expected_confusions`.
DETECTOR_FAMILIES = tuple(_AXES_DATA["detector_family"])

# -- Derived sets (logic over the vocabularies, not vocabularies themselves) --

#: Labels whose base process involves a model at any stage (Section 9.3 T2).
#: Derived: every label except the pure-human one, so adding a future hybrid
#: label to the registry includes it here automatically.
AI_INVOLVED_LABELS = frozenset(set(LABELS) - {"HUMAN"})

#: Section 4.1: span_map is required for COLLAB_MIXED and splice categories.
SPAN_MAP_REQUIRED_LABELS = frozenset({"COLLAB_MIXED"})
SPAN_MAP_REQUIRED_CATEGORIES = frozenset({"V-13", "X-06", "X-07", "X-08", "X-09"})

#: Section 4.2: T3 is the only inadmissible tier outside DEV.
ADMISSIBLE_TIERS_TEST_HIDDEN = frozenset(set(PROVENANCE_TIERS) - {"T3"})

#: Section 4.2: fairness-gated categories require T1 or T2.
FAIRNESS_GATED_CATEGORIES = frozenset({"H-14", "H-17"})
FAIRNESS_GATED_TIERS = frozenset({"T1", "T2"})


# -- Section 2.5: length buckets -----------------------------------------

#: Bucket -> (min_words, max_words). max None means unbounded.
#:
#: These ranges are NOT contiguous: 31-39, 61-84, 116-214, 286-429 and
#: 571-849 words fall between buckets. That is the specification as written,
#: so a sample can legitimately fail to belong to any bucket, and the
#: validator reports that rather than rounding it into a neighbour. Silently
#: widening the ranges would weaken the benchmark to admit samples the spec
#: excludes.
LENGTH_BUCKETS: dict[str, tuple[int, int | None]] = {
    "B25": (20, 30),
    "B50": (40, 60),
    "B100": (85, 115),
    "B250": (215, 285),
    "B500": (430, 570),
    "B1000": (850, None),
}
BUCKET_ORDER = tuple(LENGTH_BUCKETS)


def count_words(text: str) -> int:
    """Section 2.5: whitespace-delimited count after Unicode normalization.

    Deliberately tokenizer-independent. This is the harness counter and it is
    the number stored in metadata; detectors may count however they like, but
    bucket membership is ground truth and must not depend on a detector's
    tokenizer.
    """
    return len(unicodedata.normalize("NFC", text).split())


def bucket_for(n_words: int) -> str | None:
    """Bucket containing `n_words`, or None if it falls in a spec gap."""
    for name, (lo, hi) in LENGTH_BUCKETS.items():
        if n_words >= lo and (hi is None or n_words <= hi):
            return name
    return None


# -- Section 2.6: cell sizing and statistical power ----------------------

#: Phase -> {split: minimum samples per cell}.
CELL_TARGETS: dict[str, dict[str, int]] = {
    "v1.0": {"test": 10, "dev": 5, "hidden": 5},
    "v1.1": {"test": 25, "dev": 5, "hidden": 5},
    "v2.0": {"test": 50, "dev": 5, "hidden": 5},
}

#: Aggregate constraint, independent of cells (Section 2.6). Small negative
#: pools cannot estimate an FPR in the 0.5-1.0 percent range at all.
POOLED_HUMAN_TEST_MINIMUM: dict[str, int] = {"v1.0": 3000, "v1.1": 3000, "v2.0": 10000}


# -- Section 5.2: metadata schema ----------------------------------------

#: Field order is fixed for diff-friendliness (Section 5.1). Writers must
#: emit in this order. Sourced from the registry.
#:
#: This is the *v1* order deliberately: it is the BS §5.2 order, it is what
#: `Sample.to_ordered_dict` has always emitted, and later-schema fields
#: round-trip via the unknown-field tail. Callers needing a specific
#: schema's order should ask the registry for it (`FieldRegistry.field_order`)
#: rather than assume this constant tracks the newest schema.
FIELD_ORDER: tuple[str, ...] = tuple(_REGISTRY_DATA["field_order"]["1"])

#: Fields the registry marks `required: false`. Section 4.7 makes the rest
#: release-blocking when absent. Per canonical ruling TD-A01,
#: `expected_confusions` is optional.
OPTIONAL_FIELDS = frozenset(
    name for name, meta in _REGISTRY_DATA["fields"].items()
    if not meta.get("required", True)
)

#: Section 5.2: "TRACK-CAT-BUCKET-NNNN, e.g. V-05-B250-0031". The example
#: shows the track letter is carried by the category prefix, so the id is
#: CATEGORY-BUCKET-SEQUENCE and both components must agree with their fields.
ID_PATTERN = re.compile(
    r"^(?P<category>(?P<track>[HAXVFEU])-\d{2})-(?P<bucket>B(?:25|50|100|250|500|1000))-\d{4}$"
)


# -- Section 9.3: evaluation tasks ---------------------------------------

#: Tasks are evaluated separately and never averaged together (Section 9.3).
TASKS = {
    "T1": "Pure discrimination: HUMAN vs AI, hybrids excluded.",
    "T2": "Involvement detection: HUMAN vs any AI involvement.",
    "T3": "Origin attribution: base-process classification over the taxonomy.",
    "T4": "Span localization: span-F1 against span_map.",
}

#: Section 9.3(a): primary operating points.
PRIMARY_FPR_POINTS = (0.01, 0.005)

#: Section 9.3(b): calibration targets.
ECE_TARGET_OVERALL = 0.05
ECE_TARGET_PER_BUCKET = 0.10

#: Section 9.3(c): Track U overconfidence.
TRACK_U_CONFIDENCE_THRESHOLD = 0.7
TRACK_U_OVERCONFIDENCE_TARGET = 0.05

#: Section 9.2: fairness gates, release-blocking for detectors.
NONNATIVE_FPR_RATIO_LIMIT = 1.25
CONFIDENT_AI_THRESHOLD = 0.9
CONFIDENT_AI_MAX_RATE_ON_HUMAN = 0.01

#: Section 8.6: HIDDEN results are reported at coarse granularity to slow
#: benchmark overfitting.
HIDDEN_SCORE_QUANTUM = 0.005


# -- Benchmark definition data (loaded from /benchmark) ------------------

def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"benchmark definition missing: {path}. "
            "The category registry and axis vocabularies are versioned data "
            "under /benchmark, not constants in code (P6)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_categories(benchmark_dir: Path | None = None) -> dict[str, dict]:
    """Category registry (Section 3), keyed by category id."""
    d = benchmark_dir or BENCHMARK_DIR
    return _load_json(d / "categories.json")["categories"]


def load_axes(benchmark_dir: Path | None = None) -> dict[str, list[str]]:
    """Axis vocabularies (Section 2.1) for domain, format, language."""
    d = benchmark_dir or BENCHMARK_DIR
    return _load_json(d / "axes.json")


def load_failure_modes(benchmark_dir: Path | None = None) -> dict[str, dict]:
    """Failure-mode map (Section 6.2), keyed by FM id."""
    d = benchmark_dir or BENCHMARK_DIR
    return _load_json(d / "failure_modes.json")["failure_modes"]
