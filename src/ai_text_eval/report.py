"""Human-readable rendering of scores and benchmark results."""

from __future__ import annotations

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, DetectorResult
from ai_text_eval.detectors.ensemble import verdict_for
from ai_text_eval.evasion import EvasionReport
from ai_text_eval.metrics import BenchmarkResult


def _bar(score: float, width: int = 24) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def _unreliable_reason(result: DetectorResult) -> str:
    """Say which condition actually failed, not just "too short".

    A 70-word bulleted document is not short; it fails because it yields too
    few sentences for the burstiness features. Reporting the wrong cause
    sends the reader off to lengthen a text that was already long enough.
    """
    details = result.details or {}
    if details.get("error"):
        return str(details["error"])
    n_words = details.get("n_words")
    n_sentences = details.get("n_sentences")
    reasons = []
    if isinstance(n_words, int) and n_words < MIN_RELIABLE_WORDS:
        reasons.append(f"only {n_words} words, needs {MIN_RELIABLE_WORDS}")
    if isinstance(n_sentences, int) and n_sentences < 4:
        reasons.append(f"only {n_sentences} sentences, needs 4")
    return "; ".join(reasons) if reasons else "insufficient evidence"


def render_score(
    ensemble_result: DetectorResult,
    per_detector: dict[str, DetectorResult],
    show_details: bool = False,
) -> str:
    lines: list[str] = []
    lines.append("Per-detector scores (0 = human-like, 1 = AI-like):")
    for name in sorted(per_detector):
        r = per_detector[name]
        flag = "" if r.reliable else f"   [low confidence: {_unreliable_reason(r)}]"
        lines.append(f"  {name:<12} {_bar(r.score)} {r.score:.3f}{flag}")
        if show_details:
            for k, v in r.details.items():
                lines.append(f"      {k}: {v}")
    lines.append("")
    lines.append(f"  {'ENSEMBLE':<12} {_bar(ensemble_result.score)} {ensemble_result.score:.3f}")
    lines.append("")
    verdict = verdict_for(ensemble_result.score, ensemble_result.reliable)
    lines.append(f"Verdict: {verdict}")
    if not ensemble_result.reliable:
        lines.append(
            "Note: below ~50 words (a sentence or two), NO detector — including\n"
            "the best published systems — produces evidence worth acting on."
        )
    return "\n".join(lines)


def render_benchmark(results: list[BenchmarkResult]) -> str:
    lines: list[str] = []
    thr = results[0].threshold if results else 0.5
    f1_col = f"F1@{thr:g}"
    header = (
        f"{'detector':<12} {'AUROC':>7} {'95% CI':>17} {'TPR@5%FPR':>10} "
        f"{'(95% CI)':>17} {'TPR@1%FPR':>10} {f1_col:>8} {'Brier':>7} {'ECE':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    any_unmeasurable = False
    for r in results:
        ci = f"[{r.auroc_ci[0]:.3f}, {r.auroc_ci[1]:.3f}]"
        tci = f"[{r.tpr_at_fpr_5_ci[0]:.3f}, {r.tpr_at_fpr_5_ci[1]:.3f}]"
        tpr5 = f"{r.tpr_at_fpr_5:.3f}" + ("" if r.tpr_5_is_measurable else "*")
        tpr1 = f"{r.tpr_at_fpr_1:.3f}" + ("" if r.tpr_1_is_measurable else "*")
        any_unmeasurable |= not (r.tpr_5_is_measurable and r.tpr_1_is_measurable)
        lines.append(
            f"{r.detector:<12} {r.auroc:>7.4f} {ci:>17} {tpr5:>10} "
            f"{tci:>17} {tpr1:>10} "
            f"{r.metrics_at_threshold.get('f1', 0.0):>8.3f} "
            f"{r.brier:>7.4f} {r.ece:>6.3f}"
        )
    if any_unmeasurable and results:
        res = results[0].fpr_resolution
        lines.append("")
        lines.append(
            f"* FPR budget is finer than this corpus can resolve. With "
            f"{results[0].n_human} human texts the smallest non-zero FPR is "
            f"1/{results[0].n_human} = {res:.3f}, so any budget below that means "
            f"'zero false positives allowed' — the starred columns are the same\n"
            f"  measurement under two different names, not two operating points."
        )
    return "\n".join(lines)


def render_evasion(reports: list[EvasionReport]) -> str:
    lines: list[str] = []
    header = (
        f"{'detector':<12} {'pairs':>6} {'mean orig':>10} {'mean reph':>10} "
        f"{'mean drop':>10} {'evasion rate':>13}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    any_undefined = False
    for r in reports:
        d = r.to_dict()
        orig = f"{d['mean_score_original']:.3f}" if d["mean_score_original"] is not None else "n/a"
        reph = f"{d['mean_score_rephrased']:.3f}" if d["mean_score_rephrased"] is not None else "n/a"
        if r.evasion_rate is None:
            any_undefined = True
            rate = "n/a"
        else:
            rate = f"{r.evasion_rate:.1%}"
        lines.append(
            f"{r.detector:<12} {r.n_pairs:>6} {orig:>10} "
            f"{reph:>10} {r.mean_delta:>10.3f} {rate:>13}"
        )
    lines.append("")
    lines.append(
        "evasion rate = share of AI texts flagged at the threshold whose\n"
        "rephrased version escapes the flag. Higher = weaker detector."
    )
    if any_undefined:
        lines.append(
            "n/a = the detector flagged no originals at this threshold, so the\n"
            "rate is undefined. That is not evidence of robustness."
        )
    return "\n".join(lines)


def render_compare(
    name_scores: dict[str, tuple[DetectorResult, DetectorResult]],
    threshold: float = 0.5,
) -> str:
    """Render an original-vs-rephrased comparison for one text pair."""
    lines: list[str] = []
    header = f"{'detector':<12} {'original':>9} {'rephrased':>10} {'drop':>8}  outcome"
    lines.append(header)
    lines.append("-" * len(header))
    for name in sorted(name_scores):
        orig, reph = name_scores[name]
        delta = orig.score - reph.score
        if orig.score >= threshold and reph.score < threshold:
            outcome = "EVADED"
        elif reph.score >= threshold:
            outcome = "still flagged"
        else:
            outcome = "never flagged"
        lines.append(
            f"{name:<12} {orig.score:>9.3f} {reph.score:>10.3f} {delta:>+8.3f}  {outcome}"
        )
    return "\n".join(lines)
