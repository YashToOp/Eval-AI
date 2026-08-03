"""Command-line interface.

    ai-text-eval score "some text"            # or --file path
    ai-text-eval compare --original a.txt --rephrased b.txt
    ai-text-eval benchmark                    # demo corpus
    ai-text-eval benchmark --human h.jsonl --ai a.jsonl --pairs p.jsonl
    ai-text-eval detectors

Model-based detectors (perplexity, binoculars) are used automatically when
torch/transformers are installed; disable with --fast.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_text_eval.dataset import (
    load_demo_corpus,
    load_demo_pairs,
    load_labeled_jsonl,
    load_pairs_jsonl,
)
from ai_text_eval.detectors import available_detectors, detector_names
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.evasion import evaluate_evasion
from ai_text_eval.metrics import evaluate_scores
from ai_text_eval.report import (
    render_benchmark,
    render_compare,
    render_evasion,
    render_score,
)
from ai_text_eval.text_features import words

DEMO_CAVEAT_LIST = [
    "The bundled human texts are pre-1930 public-domain prose and the AI texts "
    "are modern assistant output, so era and genre are confounded with "
    "authorship: a detector may be separating centuries, not authors.",
    "n=36 documents. The bootstrap CIs are wide enough that most differences "
    "between detectors here are not statistically meaningful.",
    "The detectors' calibration constants were chosen by looking at this "
    "corpus, so these are in-sample numbers, not held-out generalization.",
]

DEMO_CAVEAT = (
    "Demo-corpus caveats (these numbers are NOT detector performance):\n"
    + "\n".join(f"  - {c}" for c in DEMO_CAVEAT_LIST)
)

DEMO_EVASION_CAVEAT = (
    "Demo-pairs caveat: the rephrasings were written to be effective attacks,\n"
    "and their originals are the same AI texts used in the table above, so the\n"
    "two tables are not independent evidence. Published results find the same\n"
    "collapse against far stronger detectors, but do not read the rate here as\n"
    "a measurement of how easy evasion is in general."
)


def _read_text(arg_text: str | None, arg_file: str | None, what: str) -> str:
    if arg_text and arg_file:
        raise SystemExit(f"give {what} as an argument OR --file, not both")
    if arg_file:
        return Path(arg_file).read_text(encoding="utf-8")
    if arg_text:
        return arg_text
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit(f"no {what} provided (argument, --file, or stdin)")
    return data


def _build_detectors(fast: bool):
    dets = available_detectors(include_model_based=not fast)
    return dets, EnsembleDetector(dets)


def cmd_score(args: argparse.Namespace) -> int:
    text = _read_text(args.text, args.file, "text")
    dets, ensemble = _build_detectors(args.fast)
    result, per_detector = ensemble.score_verbose(text)
    if args.json:
        payload = {
            "ensemble": {"score": result.score, "reliable": result.reliable},
            "detectors": {n: {"score": r.score, "reliable": r.reliable, "details": r.details}
                          for n, r in per_detector.items()},
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_score(result, per_detector, show_details=args.details))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    original = _read_text(args.original, args.original_file, "original text")
    rephrased = _read_text(args.rephrased, args.rephrased_file, "rephrased text")
    dets, ensemble = _build_detectors(args.fast)

    name_scores = {}
    for name, det in dets.items():
        name_scores[name] = (det.score(original), det.score(rephrased))
    eo, _ = ensemble.score_verbose(original)
    er, _ = ensemble.score_verbose(rephrased)
    name_scores["ENSEMBLE"] = (eo, er)

    if args.json:
        payload = {
            n: {"original": o.score, "rephrased": r.score, "delta": o.score - r.score}
            for n, (o, r) in name_scores.items()
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_compare(name_scores, threshold=args.threshold))
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    if args.human or args.ai:
        if not (args.human and args.ai):
            raise SystemExit("--human and --ai must be given together")
        human = load_labeled_jsonl(args.human)
        ai = load_labeled_jsonl(args.ai)
        if any(x.label != 0 for x in human) or any(x.label != 1 for x in ai):
            raise SystemExit("--human file must contain only label 0, --ai only label 1")
        corpus = human + ai
        using_demo = False
    else:
        corpus = load_demo_corpus()
        using_demo = True
        print("[using bundled demo corpus — pass --human/--ai for your own data]\n")

    if not corpus:
        raise SystemExit("corpus is empty — nothing to evaluate")
    labels = [item.label for item in corpus]
    if len(set(labels)) < 2:
        raise SystemExit(
            "corpus contains only one class; detection metrics need both "
            "human (label 0) and AI (label 1) texts"
        )
    texts = [item.text for item in corpus]

    dets, ensemble = _build_detectors(args.fast)

    # Score each text once per base detector and reuse those results for the
    # ensemble, rather than letting the ensemble re-run every sub-detector.
    per_text_sub = [
        {name: det.score(t) for name, det in dets.items()} for t in texts
    ]
    scores_by_detector: dict[str, list[float]] = {
        name: [sub[name].score for sub in per_text_sub] for name in dets
    }
    scores_by_detector["ensemble"] = [
        ensemble.combine_results(sub, len(words(t))).score
        for sub, t in zip(per_text_sub, texts)
    ]

    results = [
        evaluate_scores(name, labels, scores, threshold=args.threshold)
        for name, scores in scores_by_detector.items()
    ]

    print(f"Corpus: {labels.count(0)} human, {labels.count(1)} AI texts\n")
    print(render_benchmark(results))
    if using_demo:
        print("\n" + DEMO_CAVEAT)

    pairs = None
    evasion_reports: list = []
    if args.pairs:
        pairs = load_pairs_jsonl(args.pairs)
        if not pairs:
            print(f"\n[warning: {args.pairs} contained no pairs — skipping evasion analysis]")
    elif using_demo:
        pairs = load_demo_pairs()
    if pairs:
        all_detectors = {**dets, "ensemble": ensemble}
        print(f"\nParaphrase-attack robustness ({len(pairs)} original/rephrased AI pairs):\n")
        evasion_reports = [
            evaluate_evasion(det, pairs, threshold=args.threshold)
            for det in all_detectors.values()
        ]
        print(render_evasion(evasion_reports))
        if using_demo:
            print("\n" + DEMO_EVASION_CAVEAT)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "corpus": {"n_human": labels.count(0), "n_ai": labels.count(1)},
            "threshold": args.threshold,
            "results": [r.to_dict() for r in results],
        }
        if using_demo:
            # The caveats have to travel with the numbers. A bare JSON blob of
            # AUROCs gets quoted; a JSON blob that names its own confounds
            # cannot be quoted without them.
            payload["caveats"] = DEMO_CAVEAT_LIST
        if evasion_reports:
            payload["evasion"] = [r.to_dict() for r in evasion_reports]
        out_path = out_dir / "benchmark.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {out_path}")
    return 0


def cmd_detectors(args: argparse.Namespace) -> int:
    """List detector names without constructing anything.

    Instantiating the model-based detectors here would download ~1GB of
    weights just to print four names.
    """
    names = detector_names(include_model_based=not args.fast)
    print("Available detectors:")
    for name in sorted(names):
        print(f"  {name}")
    if args.fast:
        print("\n(--fast: model-based detectors were not listed)")
    elif "perplexity" not in names:
        print(
            "\n(model-based detectors unavailable — install them with\n"
            "  pip install 'ai-text-eval[perplexity]')"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-text-eval",
        description="Evaluate AI-generated-text detectors: score, compare, benchmark.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("score", help="score one text for AI-likeness")
    sp.add_argument("text", nargs="?", help="the text (or use --file / stdin)")
    sp.add_argument("--file", help="read the text from a file")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--details", action="store_true", help="show raw feature values")
    sp.add_argument("--fast", action="store_true", help="skip model-based detectors")
    sp.set_defaults(func=cmd_score)

    cp = sub.add_parser("compare", help="original vs rephrased: the evasion experiment")
    cp.add_argument("--original", help="original text (string)")
    cp.add_argument("--original-file", help="original text (file)")
    cp.add_argument("--rephrased", help="rephrased text (string)")
    cp.add_argument("--rephrased-file", help="rephrased text (file)")
    cp.add_argument("--threshold", type=float, default=0.5)
    cp.add_argument("--json", action="store_true")
    cp.add_argument("--fast", action="store_true", help="skip model-based detectors")
    cp.set_defaults(func=cmd_compare)

    bp = sub.add_parser("benchmark", help="run the metric suite on a labeled corpus")
    bp.add_argument("--human", help="JSONL of human texts (label 0)")
    bp.add_argument("--ai", help="JSONL of AI texts (label 1)")
    bp.add_argument("--pairs", help="JSONL of original/rephrased pairs for the evasion eval")
    bp.add_argument("--threshold", type=float, default=0.5)
    bp.add_argument("--out", help="directory for the JSON report")
    bp.add_argument("--fast", action="store_true", help="skip model-based detectors")
    bp.set_defaults(func=cmd_benchmark)

    dp = sub.add_parser("detectors", help="list detectors available in this environment")
    dp.add_argument("--fast", action="store_true")
    dp.set_defaults(func=cmd_detectors)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except (OSError, ValueError) as err:
        # Missing files and malformed corpora are ordinary user mistakes.
        # A traceback tells them where our code is, not what they typed wrong.
        print(f"error: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
