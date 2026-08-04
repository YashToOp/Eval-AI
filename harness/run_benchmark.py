#!/usr/bin/env python3
"""GAUNTLET harness entry point.

    python harness/run_benchmark.py validate            # release acceptance (9.1)
    python harness/run_benchmark.py validate --split test
    python harness/run_benchmark.py stats               # corpus inventory
    python harness/run_benchmark.py run --task T1 --split test

`validate` is the command that matters today: the corpus is empty, so it
reports exactly which Section 9.1 criteria are unmet. That report is the
work list for corpus authoring.

`run` requires a detector and a populated split. It refuses DEV as a
reporting split (Section 2.3) and quantizes HIDDEN output (Section 8.6).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_text_eval.gauntlet import (  # noqa: E402
    BenchmarkRunner,
    SplitDisciplineError,
    load_corpus,
    validate_release,
)
from ai_text_eval.gauntlet.spec import CORPUS_DIR, TASKS  # noqa: E402


def cmd_validate(args) -> int:
    corpus = load_corpus(args.corpus, require_manifest=False)
    report = validate_release(corpus, phase=args.phase)

    print(f"GAUNTLET release validation — {args.corpus or CORPUS_DIR}")
    version = corpus.manifest.corpus_version if corpus.manifest else None
    print(f"  corpus_version : {version}")
    print(f"  samples        : {len(corpus)}")
    print(f"  errors         : {len(report.errors)}")
    print(f"  warnings       : {len(report.warnings)}\n")

    counts = report.by_code()
    if counts:
        print("Findings by code:")
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n:>6}  {code}")
        print()

    if args.verbose:
        for f in report.findings[: args.limit]:
            print(f"  {f}")
        if len(report.findings) > args.limit:
            print(f"  … {len(report.findings) - args.limit} more "
                  f"(raise --limit to see them)")
        print()

    verdict = "RELEASABLE" if report.ok else "NOT RELEASABLE"
    print(f"Verdict: {verdict}")
    if not report.ok:
        print("Section 9.1 acceptance is not met. The findings above are the "
              "work list, not a bug in the validator.")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "corpus_version": version,
            "samples": len(corpus),
            "ok": report.ok,
            "counts": counts,
            "findings": [
                {"severity": f.severity.value, "section": f.section,
                 "code": f.code, "message": f.message,
                 "sample_id": f.sample_id, "location": f.location}
                for f in report.findings
            ],
        }, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0 if report.ok else 1


def cmd_stats(args) -> int:
    corpus = load_corpus(args.corpus, require_manifest=False)
    print(f"samples: {len(corpus)}")
    if not len(corpus):
        print("\nThe corpus is empty. This is the expected state until corpus "
              "authoring begins; see `validate` for the Section 9.1 work list.")
        return 0
    for axis in ("split", "track", "label", "length_bucket", "provenance_tier",
                 "difficulty"):
        groups = corpus.by(axis)
        print(f"\n{axis}:")
        for key, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
            print(f"  {str(key):<22} {len(items)}")
    cells = corpus.cells()
    print(f"\npopulated cells: {len(cells)}")
    return 0


def cmd_run(args) -> int:
    corpus = load_corpus(args.corpus, require_manifest=False)
    if not len(corpus):
        print("error: the corpus is empty; there is nothing to evaluate.",
              file=sys.stderr)
        print("Populate the corpus before running a benchmark. Reporting "
              "numbers from an empty or fixture corpus is forbidden by "
              "Section 9.4.", file=sys.stderr)
        return 2

    from ai_text_eval.detectors import available_detectors
    from ai_text_eval.detectors.ensemble import EnsembleDetector

    detectors = available_detectors(include_model_based=not args.fast)
    detector = EnsembleDetector(detectors)

    runner = BenchmarkRunner(corpus, detector, detector_name="ensemble")
    try:
        result = runner.run(args.task, args.split)
    except SplitDisciplineError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except NotImplementedError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description="GAUNTLET benchmark harness. Governing document: "
                    "docs/gauntlet-v1.0-spec.txt",
    )
    p.add_argument("--corpus", help="corpus root (default: ./corpus)")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="run Section 9.1 release acceptance")
    v.add_argument("--phase", default=None, help="cell-sizing phase (v1.0/v1.1/v2.0)")
    v.add_argument("--verbose", action="store_true", help="print individual findings")
    v.add_argument("--limit", type=int, default=40)
    v.add_argument("--json", help="write the full report to this path")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("stats", help="corpus inventory by axis")
    s.set_defaults(func=cmd_stats)

    r = sub.add_parser("run", help="evaluate a detector over a split")
    r.add_argument("--task", default="T1", choices=sorted(TASKS))
    r.add_argument("--split", default="test")
    r.add_argument("--fast", action="store_true")
    r.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
