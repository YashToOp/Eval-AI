#!/usr/bin/env python3
"""Run every check in the framework against one piece of text, in one pass.

    python analyze.py "some text to check"
    python analyze.py --file draft.txt
    cat draft.txt | python analyze.py
    python analyze.py --file draft.txt --json

Prints, in order: input integrity, per-detector scores, the evidence behind
them, span-level attribution, the conformally-certified verdict, and how the
score holds up under adversarial attack.

This is a convenience runner over the `ai_text_eval` package — the same
machinery the CLI subcommands use, gathered into a single report so you do not
have to invoke five commands to see everything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ai_text_eval.attacks import attack_names, apply_attack  # noqa: E402
from ai_text_eval.conformal import calibrate, load_calibrations, min_calibration_size  # noqa: E402
from ai_text_eval.dataset import load_demo_corpus  # noqa: E402
from ai_text_eval.detectors import available_detectors  # noqa: E402
from ai_text_eval.detectors.supervised import SupervisedDetector  # noqa: E402
from ai_text_eval.engine import DetectionEngine  # noqa: E402
from ai_text_eval.normalize import normalize  # noqa: E402
from ai_text_eval.spans import analyze_spans  # noqa: E402
from ai_text_eval.text_features import sentences, words  # noqa: E402
from ai_text_eval.verdict import Label  # noqa: E402

# --- presentation -------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


DIM = lambda s: _c(s, "2")          # noqa: E731
BOLD = lambda s: _c(s, "1")         # noqa: E731
RED = lambda s: _c(s, "31")         # noqa: E731
GREEN = lambda s: _c(s, "32")       # noqa: E731
YELLOW = lambda s: _c(s, "33")      # noqa: E731
CYAN = lambda s: _c(s, "36")        # noqa: E731

WIDTH = 74


def header(title: str) -> None:
    print()
    print(BOLD(title))
    print(DIM("─" * WIDTH))


def bar(score: float, width: int = 22) -> str:
    filled = max(0, min(width, round(score * width)))
    glyph = "█" * filled + "░" * (width - filled)
    if score >= 0.70:
        return RED(glyph)
    if score >= 0.40:
        return YELLOW(glyph)
    return GREEN(glyph)


def kv(label: str, value: str, indent: int = 2) -> None:
    print(" " * indent + f"{label:<26} {value}")


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


# --- report sections ----------------------------------------------------

def section_input(text: str, clean: str, report) -> None:
    n_words = len(words(clean))
    n_sents = len(sentences(clean))
    header("INPUT")
    kv(
        "Length",
        f"{n_words:,} {_plural(n_words, 'word')} · "
        f"{n_sents:,} {_plural(n_sents, 'sentence')} · "
        f"{len(text):,} {_plural(len(text), 'character')}",
    )

    if report.total_repairs == 0:
        kv("Integrity", GREEN("clean") + DIM("  (no Unicode tampering)"))
        return

    detail = (
        f"{report.homoglyphs_replaced} homoglyph(s), "
        f"{report.invisibles_removed} invisible char(s), "
        f"{report.odd_spaces_replaced} odd space(s)"
    )
    if report.is_suspicious:
        kv("Integrity", RED("TAMPERED") + f"  {detail}")
        print()
        print(DIM("  Deliberate obfuscation. Repaired before scoring, but the"))
        print(DIM("  tampering itself is evidence — route this to human review."))
    else:
        kv("Integrity", YELLOW("normalized") + f"  {detail}")


def section_scores(result, engine) -> None:
    header("DETECTOR SCORES" + DIM("   0 = human · 1 = AI"))
    rows = []
    if result.primary_score is not None:
        rows.append(("supervised", result.primary_score, "primary layer, 60% weight"))
    else:
        print(f"  {'supervised':<14} {DIM('not fitted — run with --train (needs a corpus)')}")
    for name in sorted(result.corroborator_scores):
        rows.append((name, result.corroborator_scores[name], "corroborating"))

    for name, score, note in rows:
        print(f"  {name:<14} {bar(score)}  {score:.3f}  {DIM(note)}")

    print(DIM("  " + "─" * (WIDTH - 2)))
    print(f"  {'BLENDED':<14} {bar(result.score)}  {BOLD(f'{result.score:.3f}')}")

    if result.is_ood:
        print()
        print(
            "  " + YELLOW("OUT OF DISTRIBUTION") +
            f"  primary and corroborators differ by {result.ood_disagreement:.2f}"
        )
        print(DIM("  A supervised detector is most often confidently wrong here."))


def section_evidence(clean: str, detectors) -> None:
    header("EVIDENCE")

    phrases = detectors.get("phrases")
    if phrases:
        d = phrases.score(clean).details
        hits = d.get("lexicon_hits", {})
        rate = d.get("weighted_rate_per_kw", 0.0)
        kv("Lexical marker rate", f"{rate:.1f} per 1000 words")
        if hits:
            shown = ", ".join(f"{p}×{n}" for p, n in list(hits.items())[:8])
            print(DIM(f"    {shown}"))
        else:
            print(DIM("    no AI-associated markers found"))
        if d.get("structural_hits"):
            print(DIM(f"    structural: {d['structural_hits']}"))

    stylo = detectors.get("stylometry")
    if stylo:
        d = stylo.score(clean).details
        print()
        kv("Burstiness", f"{d.get('burstiness', 0):.3f}" + DIM("   sentence-length variation; lower = more AI-like"))
        kv("Lexical diversity", f"{d.get('mattr', 0):.3f}" + DIM("   MATTR"))
        kv("Mean word length", f"{d.get('mean_word_len', 0):.2f}" + DIM("   longer = more AI-like"))
        kv("Short sentences", f"{d.get('short_sentence_rate', 0):.1%}" + DIM("   humans use more"))
        kv("Em-dashes", f"{d.get('em_dash_per_kw', 0):.1f}" + DIM(" per 1000 words"))


def section_spans(result) -> None:
    spans = result.spans
    if spans is None:
        return
    header("SPAN ATTRIBUTION")
    if not spans.reliable:
        print(DIM("  Unavailable: the document is too short to resolve internal"))
        print(DIM(f"  structure. Needs roughly {2 * spans.window_words} words; span analysis uses"))
        print(DIM(f"  {spans.window_words}-word windows and cannot localize anything finer."))
        return

    kv("AI-attributed text", f"{spans.ai_word_fraction:.0%} of words " + DIM("(approximate)"))
    cp = spans.change_point
    if cp and cp.is_significant:
        kv(
            "Possible seam",
            f"sentence {cp.sentence_index}  ({cp.mean_before:.2f} → {cp.mean_after:.2f})",
        )
    print()
    for s in spans.spans[:14]:
        mark = RED("AI") if s.score >= spans.threshold else DIM("  ")
        preview = s.text if len(s.text) <= 52 else s.text[:49] + "..."
        print(f"  {mark} {s.score:.3f}  {DIM(preview)}")
    if len(spans.spans) > 14:
        print(DIM(f"     … {len(spans.spans) - 14} more sentences"))
    print()
    print(DIM(f"  Resolution ~{spans.resolution_words} words. The percentage is biased toward"))
    print(DIM("  whichever class dominates — read it as a proportion, not a count."))


def section_verdict(result, calibration, cap: float) -> None:
    header("VERDICT")
    v = result.verdict
    colour = {
        Label.AI: RED, Label.HUMAN: GREEN,
        Label.MIXED: YELLOW, Label.ABSTAIN: CYAN,
    }[v.label]
    # explain() already leads with the label; keep one copy, colour it.
    _, _, detail = v.explain().partition(" — ")
    print("  " + colour(BOLD(v.label.value.upper())) + (f"   {detail}" if detail else ""))

    if v.threshold is not None and v.actionable:
        kv("Threshold", f"{v.threshold:.4f}" + DIM(f"  certified at FPR ≤ {v.fpr_cap:.2%}"))

    if calibration is not None and not calibration.is_certified:
        print()
        print("  " + YELLOW("Calibration cannot certify the requested cap."))
        kv("Calibration texts", str(calibration.n_calibration))
        kv("Minimum for this cap", str(min_calibration_size(cap)))
        plural = "text" if calibration.shortfall == 1 else "texts"
        print(DIM(f"    Need {calibration.shortfall} more human {plural}. Until then no finite"))
        print(DIM("    threshold honors the cap, so the engine abstains by design."))

    for note in v.notes:
        print(DIM(f"    · {note}"))


def section_robustness(engine, clean: str) -> None:
    header("ROBUSTNESS" + DIM("   how this text's score survives evasion"))
    baseline = engine.score_texts([clean])[0]
    kv("Baseline", f"{baseline:.3f}")
    print()
    print(DIM(f"  {'attack':<14}{'undefended':>12}{'defended':>11}{'residual':>11}"))
    for name in attack_names():
        attacked = apply_attack(name, clean, seed=0)
        raw = _score_raw(engine, attacked)
        defended = engine.score_texts([attacked])[0]
        residual = baseline - defended
        flag = "" if abs(residual) < 0.02 else RED("  ←")
        print(
            f"  {name:<14}{raw:>12.3f}{defended:>11.3f}"
            f"{residual:>11.3f}{flag}"
        )
    print()
    print(DIM("  undefended = normalization bypassed · defended = what the engine sees"))
    print(DIM("  residual near 0 means the attack is neutralized. Paraphrase and"))
    print(DIM("  humanizer attacks need a generator and are NOT simulated here."))


def _score_raw(engine, text: str) -> float:
    corr = {n: d.score(text).score for n, d in engine.corroborators.items()}
    primary = None
    if engine.primary is not None:
        pr = engine.primary.score(text)
        if not pr.details.get("error"):
            primary = pr.score
    return engine._blend(primary, corr)


def section_caveats(trained_on_demo: bool) -> None:
    header("READ THIS BEFORE ACTING ON ANY OF IT")
    notes = [
        "A score is evidence about a distribution, not proof about a document.",
        "Detectors falsely flag non-native English, neurodivergent, and formulaic "
        "writing at elevated rates. Never use this as sole evidence against a person.",
        "Below ~100 words no published detector is reliable, including this one.",
        "Paraphrasing defeats every detector measured here, and a paraphrase of AI "
        "text is still AI text — the score drops, the truth does not change.",
    ]
    if trained_on_demo:
        notes.append(
            "The supervised layer was trained on the bundled 36-document demo corpus, "
            "which is a toy. Its scores are illustrative, not trustworthy."
        )
    for n in notes:
        wrapped = _wrap(n, WIDTH - 6)
        print(f"  {DIM('·')} {wrapped[0]}")
        for line in wrapped[1:]:
            print(f"    {line}")


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out or [""]


# --- main ---------------------------------------------------------------

def read_input(args) -> str:
    if args.text and args.file:
        raise SystemExit("give the text as an argument OR --file, not both")
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.text:
        return args.text
    if sys.stdin.isatty():
        raise SystemExit(
            "no text provided.\n"
            '  python analyze.py "text to check"\n'
            "  python analyze.py --file draft.txt\n"
            "  cat draft.txt | python analyze.py"
        )
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit("no text provided on stdin")
    return data


def build(args):
    """Assemble the engine and a conformal calibration."""
    corroborators = available_detectors(include_model_based=not args.fast)

    primary = None
    trained_on_demo = False
    if not args.no_train:
        corpus = load_demo_corpus()
        primary = SupervisedDetector(corroborators=corroborators)
        primary.fit([c.text for c in corpus], [c.label for c in corpus])
        trained_on_demo = True

    calibrations = {}
    calibration = None
    if args.calibration:
        calibrations = load_calibrations(args.calibration)
        calibration = calibrations.get(args.language)
    else:
        # Calibrate on the bundled human texts so the verdict layer is live.
        # With 18 documents this will not certify a serious cap, and saying so
        # is the point.
        engine0 = DetectionEngine(corroborators=corroborators, primary=primary)
        human = [c.text for c in load_demo_corpus() if c.label == 0]
        scores = engine0.score_texts(human)
        calibration = calibrate(
            scores, [len(words(t)) for t in human],
            alpha=args.fpr_cap, language=args.language,
        )
        calibrations = {args.language: calibration}

    engine = DetectionEngine(
        corroborators=corroborators, primary=primary, calibrations=calibrations
    )
    return engine, corroborators, calibration, trained_on_demo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="Run every AI-text-detection check against one input, in one pass.",
    )
    p.add_argument("text", nargs="?", help="text to analyze (or --file, or stdin)")
    p.add_argument("--file", help="read the text from a file")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--fast", action="store_true",
                   help="skip model-based detectors even if torch is installed")
    p.add_argument("--no-train", action="store_true",
                   help="skip the supervised layer (zero-shot only)")
    p.add_argument("--calibration", help="JSON calibration file from `ai-text-eval calibrate`")
    p.add_argument("--fpr-cap", type=float, default=0.05,
                   help="false-positive policy cap (default 0.05)")
    p.add_argument("--language", default="en")
    p.add_argument("--no-robustness", action="store_true", help="skip the attack sweep")
    args = p.parse_args(argv)

    try:
        text = read_input(args)
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if not text.strip():
        print("error: the input is empty", file=sys.stderr)
        return 2

    engine, detectors, calibration, trained_on_demo = build(args)
    clean, norm_report = normalize(text)
    result = engine.analyze(text, language=args.language)

    if args.json:
        payload = result.to_dict(include_spans=True)
        payload["calibration"] = calibration.to_dict() if calibration else None
        if not args.no_robustness:
            baseline = engine.score_texts([clean])[0]
            payload["robustness"] = {
                name: {
                    "undefended": round(_score_raw(engine, apply_attack(name, clean, seed=0)), 4),
                    "defended": round(engine.score_texts([apply_attack(name, clean, seed=0)])[0], 4),
                }
                for name in attack_names()
            }
            payload["robustness"]["baseline"] = round(baseline, 4)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print()
    print(BOLD("  AI TEXT ANALYSIS") + DIM("   ai-text-eval v2"))

    section_input(text, clean, norm_report)
    section_scores(result, engine)
    section_evidence(clean, detectors)
    section_spans(result)
    section_verdict(result, calibration, args.fpr_cap)
    if not args.no_robustness:
        section_robustness(engine, clean)
    section_caveats(trained_on_demo)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
