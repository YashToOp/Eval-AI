"""Human-readable rendering of scores and benchmark results."""

from __future__ import annotations

from ai_text_eval.detectors.base import DetectorResult
from ai_text_eval.detectors.ensemble import verdict_for
from ai_text_eval.evasion import EvasionReport
from ai_text_eval.metrics import BenchmarkResult


def _bar(score: float, width: int = 24) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def render_score(
    ensemble_result: DetectorResult,
    per_detector: dict[str, DetectorResult],
    show_details: bool = False,
) -> str:
    lines: list[str] = []
    lines.append("Per-detector scores (0 = human-like, 1 = AI-like):")
    for name in sorted(per_detector):
        r = per_detector[name]
        flag = "" if r.reliable else "   [low confidence: text too short]"
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
    header = (
        f"{'detector':<12} {'AUROC':>7} {'95% CI':>17} {'TPR@5%FPR':>10} "
        f"{'TPR@1%FPR':>10} {'F1@0.5':>7} {'Brier':>7} {'ECE':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        ci = f"[{r.auroc_ci[0]:.3f}, {r.auroc_ci[1]:.3f}]"
        lines.append(
            f"{r.detector:<12} {r.auroc:>7.4f} {ci:>17} {r.tpr_at_fpr_5:>10.3f} "
            f"{r.tpr_at_fpr_1:>10.3f} {r.metrics_at_half.get('f1', 0.0):>7.3f} "
            f"{r.brier:>7.4f} {r.ece:>6.3f}"
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
    for r in reports:
        d = r.to_dict()
        lines.append(
            f"{r.detector:<12} {r.n_pairs:>6} {d['mean_score_original']:>10.3f} "
            f"{d['mean_score_rephrased']:>10.3f} {r.mean_delta:>10.3f} "
            f"{r.evasion_rate:>12.1%}"
        )
    lines.append("")
    lines.append(
        "evasion rate = share of AI texts flagged at the threshold whose\n"
        "rephrased version escapes the flag. Higher = weaker detector."
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
