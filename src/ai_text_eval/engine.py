"""The v2 detection engine: supervised-primary, zero-shot-corroborating.

Pipeline, in order, because the order is load-bearing:

    normalize -> score (primary + corroborators) -> OOD check
              -> span analysis -> conformal threshold -> ternary verdict

Architectural changes from v1, each traceable to the 2025-26 evidence:

- **Supervised primary.** Zero-shot detectors no longer set the verdict; they
  corroborate it and act as out-of-distribution tripwires. Supervised systems
  dominate at the low FPR an eval engine needs.
- **Conformal thresholds replace hand-set ones.** The decision boundary comes
  from a human calibration set and a policy cap, not from 0.5.
- **Normalization runs first**, so homoglyph and zero-width attacks are
  defeated before scoring rather than being absorbed as signal.
- **Disagreement is information.** When the primary and the corroborators
  diverge sharply, the input is likely out of the primary's training
  distribution — exactly the case where a supervised detector is confidently
  wrong — so the engine says so instead of averaging the conflict away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_text_eval.conformal import ConformalCalibration
from ai_text_eval.detectors.base import Detector, DetectorResult
from ai_text_eval.normalize import NormalizationReport, normalize
from ai_text_eval.provenance import ProvenanceSignal, ProvenanceStatus, combine
from ai_text_eval.spans import SpanAnalysis, analyze_spans
from ai_text_eval.text_features import words
from ai_text_eval.verdict import Verdict, decide

# Weight on the supervised layer when it is fitted. The remainder is split
# across the zero-shot corroborators by their own weights.
PRIMARY_WEIGHT = 0.60

# Primary-vs-corroborator gap above which the input is treated as likely
# out-of-distribution for the primary.
OOD_DISAGREEMENT = 0.45


@dataclass
class EngineResult:
    verdict: Verdict
    score: float
    primary_score: float | None
    corroborator_scores: dict[str, float] = field(default_factory=dict)
    normalization: NormalizationReport | None = None
    spans: SpanAnalysis | None = None
    provenance: ProvenanceSignal | None = None
    ood_disagreement: float = 0.0
    is_ood: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self, include_spans: bool = False) -> dict:
        return {
            "verdict": self.verdict.to_dict(),
            "score": round(self.score, 4),
            "primary_score": (
                round(self.primary_score, 4) if self.primary_score is not None else None
            ),
            "corroborators": {k: round(v, 4) for k, v in self.corroborator_scores.items()},
            "ood_disagreement": round(self.ood_disagreement, 4),
            "out_of_distribution": self.is_ood,
            "normalization": self.normalization.to_dict() if self.normalization else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "spans": self.spans.to_dict(include_spans=include_spans) if self.spans else None,
            "notes": self.notes,
        }


class DetectionEngine:
    """Composes the layers into one auditable decision."""

    def __init__(
        self,
        corroborators: dict[str, Detector],
        primary: Detector | None = None,
        calibrations: dict[str, ConformalCalibration] | None = None,
        corroborator_weights: dict[str, float] | None = None,
        primary_weight: float = PRIMARY_WEIGHT,
    ):
        if not corroborators and primary is None:
            raise ValueError("engine needs a primary detector or at least one corroborator")
        self.primary = primary
        self.corroborators = corroborators
        self.calibrations = calibrations or {}
        self.primary_weight = primary_weight
        self.corroborator_weights = corroborator_weights or {n: 1.0 for n in corroborators}

    # -- scoring ---------------------------------------------------------

    def _primary_is_usable(self, result: DetectorResult | None) -> bool:
        return result is not None and not result.details.get("error")

    def _blend(self, primary: float | None, corr: dict[str, float]) -> float:
        corr_total = sum(self.corroborator_weights.get(n, 1.0) for n in corr)
        corr_mean = (
            sum(self.corroborator_weights.get(n, 1.0) * v for n, v in corr.items()) / corr_total
            if corr_total > 0
            else None
        )
        if primary is None:
            if corr_mean is None:
                return 0.5
            return corr_mean
        if corr_mean is None:
            return primary
        return self.primary_weight * primary + (1 - self.primary_weight) * corr_mean

    def analyze(
        self,
        text: str,
        language: str = "en",
        provenance: ProvenanceSignal | None = None,
        want_spans: bool = True,
    ) -> EngineResult:
        notes: list[str] = []

        clean, norm_report = normalize(text)
        if norm_report.total_repairs:
            notes.append(
                f"normalization repaired {norm_report.total_repairs} character(s) "
                "before scoring"
            )

        corr = {n: d.score(clean).score for n, d in self.corroborators.items()}

        primary_score: float | None = None
        if self.primary is not None:
            pr = self.primary.score(clean)
            if self._primary_is_usable(pr):
                primary_score = pr.score
            else:
                notes.append(
                    "supervised primary is unavailable (not fitted); falling back "
                    "to zero-shot corroborators, which are weaker at low FPR"
                )

        score = self._blend(primary_score, corr)

        # Out-of-distribution tripwire.
        disagreement = 0.0
        is_ood = False
        if primary_score is not None and corr:
            corr_mean = sum(corr.values()) / len(corr)
            disagreement = abs(primary_score - corr_mean)
            if disagreement >= OOD_DISAGREEMENT:
                is_ood = True
                notes.append(
                    f"primary and corroborators disagree by {disagreement:.2f}; "
                    "likely outside the primary's training distribution, where a "
                    "supervised detector is most often confidently wrong"
                )

        prov_is_ai: bool | None = None
        if provenance is not None:
            score, explanation = combine(score, provenance)
            notes.append(explanation)
            if provenance.status is ProvenanceStatus.VERIFIED_AI:
                prov_is_ai = True

        spans = None
        if want_spans:
            span_detector = self.primary if primary_score is not None else None
            if span_detector is None and self.corroborators:
                span_detector = next(iter(self.corroborators.values()))
            if span_detector is not None:
                spans = analyze_spans(span_detector, clean)

        n_words = len(words(clean))
        calibration = self.calibrations.get(language)
        verdict = decide(
            score=score,
            n_words=n_words,
            calibration=calibration,
            spans=spans,
            normalization=norm_report,
            language=language,
            provenance_is_ai=prov_is_ai,
        )
        # An OOD input is not something to answer confidently, even if the
        # score clears the bar.
        if is_ood:
            verdict.notes.append(
                "out-of-distribution: corroborate before acting on this verdict"
            )

        return EngineResult(
            verdict=verdict,
            score=score,
            primary_score=primary_score,
            corroborator_scores=corr,
            normalization=norm_report,
            spans=spans,
            provenance=provenance,
            ood_disagreement=disagreement,
            is_ood=is_ood,
            notes=notes,
        )

    def score_texts(self, texts: list[str]) -> list[float]:
        """Blended scores only — the fast path for benchmarking."""
        out: list[float] = []
        for t in texts:
            clean, _ = normalize(t)
            corr = {n: d.score(clean).score for n, d in self.corroborators.items()}
            primary_score = None
            if self.primary is not None:
                pr = self.primary.score(clean)
                if self._primary_is_usable(pr):
                    primary_score = pr.score
            out.append(self._blend(primary_score, corr))
        return out
