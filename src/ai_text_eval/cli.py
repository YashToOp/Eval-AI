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
from ai_text_eval.attacks import ATTACKS, apply_attack, attack_names
from ai_text_eval.conformal import (
    DEFAULT_FPR_CAP,
    ConformalCalibration,
    calibrate,
    load_calibrations,
    min_calibration_size,
    save_calibrations,
)
from ai_text_eval.detectors import available_detectors, detector_names
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.detectors.supervised import SupervisedDetector, cross_val_scores
from ai_text_eval.engine import DetectionEngine
from ai_text_eval.metrics import max_subgroup_gap, subgroup_fpr
from ai_text_eval.normalize import normalize
from ai_text_eval.spans import analyze_spans
from ai_text_eval.evasion import evaluate_evasion
from ai_text_eval.metrics import evaluate_scores
from ai_text_eval.report import (
    render_benchmark,
    render_compare,
    render_engine_result,
    render_evasion,
    render_fairness,
    render_robustness,
    render_score,
    render_spans,
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


def _build_engine(args, corpus=None):
    """Assemble the v2 engine: supervised primary + zero-shot corroborators.

    When a labeled corpus is available the supervised layer is trained on it
    and, for benchmarking, scored out-of-fold. With no corpus the primary is
    absent and the engine degrades to zero-shot corroborators, which it says
    out loud rather than pretending the architecture is intact.
    """
    corroborators = available_detectors(include_model_based=not args.fast)
    primary = None
    if corpus:
        primary = SupervisedDetector(corroborators=corroborators)
        try:
            primary.fit([c.text for c in corpus], [c.label for c in corpus])
        except ValueError:
            primary = None

    calibrations: dict[str, ConformalCalibration] = {}
    cal_path = getattr(args, "calibration", None)
    if cal_path:
        calibrations = load_calibrations(cal_path)

    return DetectionEngine(
        corroborators=corroborators,
        primary=primary,
        calibrations=calibrations,
    )


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


def cmd_analyze(args: argparse.Namespace) -> int:
    """v2 pipeline: normalize, score, span, conformal threshold, ternary verdict."""
    text = _read_text(args.text, args.file, "text")
    corpus = load_demo_corpus() if args.train_on_demo else None
    engine = _build_engine(args, corpus=corpus)
    result = engine.analyze(text, language=args.language, want_spans=not args.no_spans)

    if args.json:
        print(json.dumps(result.to_dict(include_spans=args.spans), indent=2, ensure_ascii=False))
        return 0

    print(render_engine_result(result, show_spans=args.spans))
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Fit conformal thresholds on human-written text at an FPR policy cap."""
    human = load_labeled_jsonl(args.human)
    if any(x.label != 0 for x in human):
        raise SystemExit("--human must contain only human texts (label 0)")
    if not human:
        raise SystemExit("calibration needs at least one human text")

    corpus = load_demo_corpus() if args.train_on_demo else None
    engine = _build_engine(args, corpus=corpus)
    texts = [x.text for x in human]
    scores = engine.score_texts(texts)
    lengths = [len(words(t)) for t in texts]

    cal = calibrate(scores, lengths, alpha=args.fpr_cap, language=args.language)
    needed = min_calibration_size(args.fpr_cap)

    print(f"Language:            {cal.language}")
    print(f"FPR policy cap:      {cal.alpha:.3%}")
    print(f"Calibration texts:   {cal.n_calibration}")
    print(f"Minimum required:    {needed}")
    if cal.is_certified:
        print(f"Global threshold:    {cal.global_threshold:.4f}")
        print("\nStatus: CERTIFIED — flagging at this threshold keeps the")
        print(f"        false-positive rate at or below {cal.alpha:.3%}.")
    else:
        print("Global threshold:    none (infinite)")
        print(f"\nStatus: NOT CERTIFIED — {cal.shortfall} more human texts are needed.")
        print("        The engine will abstain rather than flag, because no finite")
        print("        threshold can honor the requested cap at this sample size.")
    if cal.bin_thresholds:
        print("\nLength-bucketed thresholds (multiscale conformal):")
        edges = [0] + cal.bin_edges
        for idx in sorted(cal.bin_counts):
            lo = edges[idx] if idx < len(edges) else edges[-1]
            hi = cal.bin_edges[idx] if idx < len(cal.bin_edges) else None
            span = f"{lo}-{hi}" if hi else f"{lo}+"
            thr = cal.bin_thresholds.get(idx, float("inf"))
            shown = f"{thr:.4f}" if thr != float("inf") else "none (defers to global)"
            print(f"  {span:>10} words  n={cal.bin_counts[idx]:<5} threshold={shown}")

    if args.out:
        existing = {}
        if Path(args.out).exists():
            existing = load_calibrations(args.out)
        existing[cal.language] = cal
        save_calibrations(args.out, existing)
        print(f"\nWrote {args.out}")
    return 0


def cmd_spans(args: argparse.Namespace) -> int:
    """Sentence-level analysis and AI-proportion estimate for hybrid documents."""
    text = _read_text(args.text, args.file, "text")
    corpus = load_demo_corpus() if args.train_on_demo else None
    engine = _build_engine(args, corpus=corpus)
    detector = engine.primary or next(iter(engine.corroborators.values()))
    clean, _ = normalize(text)
    analysis = analyze_spans(detector, clean, threshold=args.threshold)

    if args.json:
        print(json.dumps(analysis.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(render_spans(analysis))
    return 0


def cmd_robustness(args: argparse.Namespace) -> int:
    """Measure score degradation under mechanical adversarial attacks."""
    corpus = load_demo_corpus() if not args.ai else load_labeled_jsonl(args.ai)
    ai_texts = [c.text for c in corpus if c.label == 1]
    if not ai_texts:
        raise SystemExit("robustness needs AI-labeled texts")

    train = load_demo_corpus() if args.train_on_demo else None
    engine = _build_engine(args, corpus=train)

    baseline = engine.score_texts(ai_texts)
    rows = []
    for name in attack_names():
        attacked = [apply_attack(name, t, seed=args.seed) for t in ai_texts]
        with_defense = engine.score_texts(attacked)
        # Bypass normalization to show what the attack would have done.
        raw = [
            _score_without_normalization(engine, t) for t in attacked
        ]
        rows.append((name, ATTACKS[name].description, baseline, raw, with_defense))

    print(render_robustness(rows))
    return 0


def _score_without_normalization(engine: DetectionEngine, text: str) -> float:
    corr = {n: d.score(text).score for n, d in engine.corroborators.items()}
    primary = None
    if engine.primary is not None:
        pr = engine.primary.score(text)
        if not pr.details.get("error"):
            primary = pr.score
    return engine._blend(primary, corr)


def cmd_fairness(args: argparse.Namespace) -> int:
    """Per-subgroup false-positive rates among human writers."""
    if args.human:
        human = load_labeled_jsonl(args.human)
        if any(x.label != 0 for x in human):
            raise SystemExit("--human must contain only human texts (label 0)")
    else:
        human = [c for c in load_demo_corpus() if c.label == 0]
        print("[using bundled demo human texts — pass --human for your own]\n")

    corpus = load_demo_corpus() if args.train_on_demo else None
    engine = _build_engine(args, corpus=corpus)
    texts = [x.text for x in human]
    scores = engine.score_texts(texts)
    groups = [str(x.meta.get(args.group_by, "unknown")) for x in human]
    labels = [0] * len(texts)

    results = subgroup_fpr(labels, scores, groups, threshold=args.threshold)
    print(render_fairness(results, args.group_by, args.threshold, args.fpr_cap))
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

    # -- v2 commands ---------------------------------------------------

    ap = sub.add_parser(
        "analyze",
        help="v2 pipeline: normalize, score, span-analyze, and return a ternary verdict",
    )
    ap.add_argument("text", nargs="?", help="the text (or use --file / stdin)")
    ap.add_argument("--file", help="read the text from a file")
    ap.add_argument("--language", default="en", help="language code for calibration lookup")
    ap.add_argument("--calibration", help="JSON file of conformal calibrations")
    ap.add_argument("--train-on-demo", action="store_true",
                    help="train the supervised primary on the bundled demo corpus")
    ap.add_argument("--spans", action="store_true", help="print per-sentence scores")
    ap.add_argument("--no-spans", action="store_true", help="skip span analysis entirely")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fast", action="store_true", help="skip model-based detectors")
    ap.set_defaults(func=cmd_analyze)

    cal = sub.add_parser(
        "calibrate",
        help="fit conformal thresholds on human text at an FPR policy cap",
    )
    cal.add_argument("--human", required=True, help="JSONL of human texts (label 0)")
    cal.add_argument("--fpr-cap", type=float, default=DEFAULT_FPR_CAP,
                     help=f"false-positive policy cap (default {DEFAULT_FPR_CAP})")
    cal.add_argument("--language", default="en")
    cal.add_argument("--calibration", help=argparse.SUPPRESS)
    cal.add_argument("--train-on-demo", action="store_true")
    cal.add_argument("--out", help="write/merge calibrations into this JSON file")
    cal.add_argument("--fast", action="store_true")
    cal.set_defaults(func=cmd_calibrate)

    spn = sub.add_parser("spans", help="sentence-level scores and AI-proportion estimate")
    spn.add_argument("text", nargs="?")
    spn.add_argument("--file")
    spn.add_argument("--threshold", type=float, default=0.5)
    spn.add_argument("--calibration", help=argparse.SUPPRESS)
    spn.add_argument("--train-on-demo", action="store_true")
    spn.add_argument("--json", action="store_true")
    spn.add_argument("--fast", action="store_true")
    spn.set_defaults(func=cmd_spans)

    rb = sub.add_parser(
        "robustness", help="score degradation under mechanical adversarial attacks"
    )
    rb.add_argument("--ai", help="JSONL of AI texts (label 1); defaults to the demo corpus")
    rb.add_argument("--seed", type=int, default=0)
    rb.add_argument("--calibration", help=argparse.SUPPRESS)
    rb.add_argument("--train-on-demo", action="store_true")
    rb.add_argument("--fast", action="store_true")
    rb.set_defaults(func=cmd_robustness)

    fr = sub.add_parser("fairness", help="per-subgroup false-positive rates among humans")
    fr.add_argument("--human", help="JSONL of human texts (label 0)")
    fr.add_argument("--group-by", default="domain", help="meta field to group by")
    fr.add_argument("--threshold", type=float, default=0.5)
    fr.add_argument("--fpr-cap", type=float, default=DEFAULT_FPR_CAP)
    fr.add_argument("--calibration", help=argparse.SUPPRESS)
    fr.add_argument("--train-on-demo", action="store_true")
    fr.add_argument("--fast", action="store_true")
    fr.set_defaults(func=cmd_fairness)

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
