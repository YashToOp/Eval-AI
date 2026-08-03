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
from ai_text_eval.detectors import available_detectors
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.evasion import evaluate_evasion
from ai_text_eval.metrics import evaluate_scores
from ai_text_eval.report import (
    render_benchmark,
    render_compare,
    render_evasion,
    render_score,
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
        corpus = load_labeled_jsonl(args.human) + load_labeled_jsonl(args.ai)
        bad_human = [x for x in load_labeled_jsonl(args.human) if x.label != 0]
        bad_ai = [x for x in load_labeled_jsonl(args.ai) if x.label != 1]
        if bad_human or bad_ai:
            raise SystemExit("--human file must contain only label 0, --ai only label 1")
    else:
        corpus = load_demo_corpus()
        print("[using bundled demo corpus — pass --human/--ai for your own data]\n")

    labels = [item.label for item in corpus]
    texts = [item.text for item in corpus]

    dets, ensemble = _build_detectors(args.fast)
    all_detectors = {**dets, "ensemble": ensemble}

    results = []
    scores_by_detector: dict[str, list[float]] = {}
    for name, det in all_detectors.items():
        scores = [r.score for r in det.score_many(texts)]
        scores_by_detector[name] = scores
        results.append(evaluate_scores(name, labels, scores))

    print(f"Corpus: {labels.count(0)} human, {labels.count(1)} AI texts\n")
    print(render_benchmark(results))

    pairs = None
    if args.pairs:
        pairs = load_pairs_jsonl(args.pairs)
    elif not (args.human or args.ai):
        pairs = load_demo_pairs()
    if pairs:
        print(f"\nParaphrase-attack robustness ({len(pairs)} original/rephrased AI pairs):\n")
        evasion_reports = [
            evaluate_evasion(det, pairs, threshold=args.threshold)
            for det in all_detectors.values()
        ]
        print(render_evasion(evasion_reports))

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "corpus": {"n_human": labels.count(0), "n_ai": labels.count(1)},
            "results": [r.to_dict() for r in results],
        }
        if pairs:
            payload["evasion"] = [r.to_dict() for r in evasion_reports]
        out_path = out_dir / "benchmark.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {out_path}")
    return 0


def cmd_detectors(args: argparse.Namespace) -> int:
    dets = available_detectors(include_model_based=not args.fast)
    print("Available detectors:")
    for name in sorted(dets):
        print(f"  {name}")
    if "perplexity" not in dets:
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
