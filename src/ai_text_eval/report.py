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


def render_engine_result(result, show_spans: bool = False) -> str:
    """Full v2 verdict: score, provenance, spans, and the abstention reasoning."""
    lines: list[str] = []
    v = result.verdict

    lines.append("Layers:")
    if result.primary_score is not None:
        lines.append(f"  {'supervised*':<14} {_bar(result.primary_score)} {result.primary_score:.3f}")
    else:
        lines.append(f"  {'supervised*':<14} {'—':<24} not fitted")
    for name in sorted(result.corroborator_scores):
        s = result.corroborator_scores[name]
        lines.append(f"  {name:<14} {_bar(s)} {s:.3f}")
    lines.append("  * primary layer; the rest corroborate")
    lines.append("")
    lines.append(f"  {'BLENDED':<14} {_bar(result.score)} {result.score:.3f}")

    if result.normalization and result.normalization.total_repairs:
        n = result.normalization
        lines.append("")
        lines.append(
            f"Normalization: repaired {n.homoglyphs_replaced} homoglyph(s), "
            f"{n.invisibles_removed} invisible char(s), "
            f"{n.odd_spaces_replaced} odd space(s)"
            + ("  [SUSPICIOUS]" if n.is_suspicious else "")
        )

    if result.is_ood:
        lines.append("")
        lines.append(
            f"Out-of-distribution: primary and corroborators differ by "
            f"{result.ood_disagreement:.2f}"
        )

    if result.spans and result.spans.reliable:
        lines.append("")
        lines.append(f"AI-attributed text: {result.spans.ai_word_fraction:.0%} of words")
        cp = result.spans.change_point
        if cp and cp.is_significant:
            lines.append(
                f"Possible seam at sentence {cp.sentence_index} "
                f"({cp.mean_before:.2f} -> {cp.mean_after:.2f})"
            )

    lines.append("")
    lines.append(f"VERDICT: {v.explain()}")
    if v.threshold is not None and v.actionable:
        lines.append(
            f"         threshold {v.threshold:.4f}, conformally certified at "
            f"FPR <= {v.fpr_cap:.3%}"
        )
    for note in v.notes:
        lines.append(f"  - {note}")
    for note in result.notes:
        lines.append(f"  - {note}")

    if show_spans and result.spans:
        lines.append("")
        lines.append("Per-sentence scores:")
        for s in result.spans.spans:
            marker = "AI " if s.score >= result.spans.threshold else "   "
            preview = s.text if len(s.text) <= 68 else s.text[:65] + "..."
            lines.append(f"  {marker}{s.score:.3f}  {preview}")
    return "\n".join(lines)


def render_spans(analysis) -> str:
    lines: list[str] = []
    if not analysis.reliable:
        lines.append(
            "Span analysis unavailable: the document yields fewer than two "
            "scoring windows, so every sentence would inherit the same\n"
            "document-level score and the structure would be an artifact."
        )
        if not analysis.spans:
            return "\n".join(lines)
        lines.append("")
    lines.append(f"AI-attributed text: {analysis.ai_word_fraction:.0%} of words")
    cp = analysis.change_point
    if cp:
        verdict = "significant" if cp.is_significant else "not significant"
        lines.append(
            f"Largest shift at sentence {cp.sentence_index}: "
            f"{cp.mean_before:.2f} -> {cp.mean_after:.2f} ({cp.delta:+.2f}, {verdict})"
        )
    lines.append("")
    header = f"{'#':>3}  {'score':>6}  {'words':>5}  sentence"
    lines.append(header)
    lines.append("-" * (len(header) + 20))
    for s in analysis.spans:
        marker = "*" if s.score >= analysis.threshold else " "
        preview = s.text if len(s.text) <= 60 else s.text[:57] + "..."
        lines.append(f"{s.index:>3}{marker} {s.score:>6.3f}  {s.n_words:>5}  {preview}")
    lines.append("")
    lines.append(
        f"* scores at or above {analysis.threshold:.2f}. Each sentence inherits the mean of\n"
        f"  the {analysis.window_words}-word windows covering it — single sentences carry too\n"
        "  little evidence to score on their own.\n"
        f"\n"
        f"Resolution: ~{analysis.resolution_words} words. A seam is located only to within about\n"
        "  one window, and the percentage above is biased toward whichever class\n"
        "  dominates, because windows straddling the boundary mix both sides.\n"
        "  Treat it as an approximate proportion, not a word count."
    )
    return "\n".join(lines)


def render_robustness(rows) -> str:
    """rows: (name, description, baseline, raw_scores, defended_scores)."""
    lines: list[str] = []
    header = (
        f"{'attack':<14} {'baseline':>9} {'attacked':>9} {'defended':>9} "
        f"{'undefended drop':>16} {'residual':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for name, _desc, baseline, raw, defended in rows:
        b = sum(baseline) / len(baseline)
        r = sum(raw) / len(raw)
        d = sum(defended) / len(defended)
        lines.append(
            f"{name:<14} {b:>9.3f} {r:>9.3f} {d:>9.3f} {b - r:>16.3f} {b - d:>9.3f}"
        )
    lines.append("")
    lines.append(
        "baseline  = mean score on unmodified AI text\n"
        "attacked  = score with normalization bypassed (what the attack achieves)\n"
        "defended  = score with normalization applied (what the engine actually sees)\n"
        "residual  = drop that survives the defense; near zero means the attack is neutralized."
    )
    lines.append("")
    lines.append(
        "Mechanical attacks only. Recursive paraphrase, RL-optimized rewriting and\n"
        "commercial humanizers need a generator and are not simulated here — supply\n"
        "real pairs to the evasion benchmark instead of trusting these numbers to\n"
        "stand in for them."
    )
    return "\n".join(lines)


def render_fairness(results, group_by: str, threshold: float, fpr_cap: float) -> str:
    lines: list[str] = []
    lines.append(
        f"False-positive rate among human writers, by '{group_by}' "
        f"(threshold {threshold:.3f}):\n"
    )
    header = f"{'group':<28} {'n':>4} {'flagged':>8} {'FPR':>8} {'95% CI':>18}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        lo, hi = r.wilson_interval()
        over = "  OVER CAP" if r.fpr > fpr_cap else ""
        lines.append(
            f"{r.group:<28} {r.n:>4} {r.n_flagged:>8} {r.fpr:>8.3f} "
            f"{f'[{lo:.3f}, {hi:.3f}]':>18}{over}"
        )
    if results:
        gap = max(r.fpr for r in results) - min(r.fpr for r in results)
        lines.append("")
        lines.append(f"Largest between-group gap: {gap:.3f}   Policy cap: {fpr_cap:.3%}")
    lines.append("")
    lines.append(
        "An aggregate FPR that meets the cap while one subgroup sits far above it\n"
        "is a failing detector. The documented harms of this technology are all\n"
        "false positives concentrated in identifiable groups — non-native English\n"
        "writers, neurodivergent writers, AAVE speakers, and formulaic genres.\n"
        "Wilson intervals are used because these rates are small and the n is not."
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
