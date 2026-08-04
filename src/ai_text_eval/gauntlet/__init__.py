"""GAUNTLET: the benchmark corpus, its validators, and the evaluation harness.

Governing document: docs/gauntlet-v1.0-spec.txt. Where this code and the
specification disagree, the specification wins and the code is the bug.

The benchmark is the product; the detector is a consumer of the benchmark.
Nothing in this package imports detector internals — the harness depends only
on a `.score(text)` contract.
"""

from ai_text_eval.gauntlet.loader import (
    Corpus,
    Manifest,
    SplitDisciplineError,
    load_corpus,
    load_manifest,
)
from ai_text_eval.gauntlet.runner import (
    BenchmarkRunner,
    CellResult,
    TaskResult,
    quantize,
    wilson_interval,
)
from ai_text_eval.gauntlet.sample import Sample, parse_jsonl, write_jsonl
from ai_text_eval.gauntlet.spec import (
    LABELS,
    LENGTH_BUCKETS,
    SPEC_VERSION,
    TASKS,
    TRACKS,
    bucket_for,
    count_words,
)
from ai_text_eval.gauntlet.validate import (
    Finding,
    Report,
    Severity,
    validate_manifest,
    validate_release,
    validate_sample,
    validate_splits,
)

__all__ = [
    "SPEC_VERSION", "TRACKS", "LABELS", "LENGTH_BUCKETS", "TASKS",
    "bucket_for", "count_words",
    "Sample", "parse_jsonl", "write_jsonl",
    "Corpus", "Manifest", "load_corpus", "load_manifest", "SplitDisciplineError",
    "Report", "Finding", "Severity",
    "validate_sample", "validate_manifest", "validate_splits", "validate_release",
    "BenchmarkRunner", "TaskResult", "CellResult", "wilson_interval", "quantize",
]
