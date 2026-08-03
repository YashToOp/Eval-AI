"""Ternary verdicts with mandatory abstention.

Binary Human/AI is the wrong output shape and a strict FPR cap is the wrong
thing to leave to a hand-set threshold. This module is the decision layer:

    Human | AI | Mixed | Abstain

`Mixed` decouples *confidence* from *proportion* — a document can be
confidently 30% generated. `Abstain` is a first-class outcome, returned
whenever the engine has no licence to speak: text too short, no conformal
calibration for the language, a calibration set too small to certify the
requested cap, or evidence of Unicode tampering that makes the score
untrustworthy.

Abstention is not hedging. Given documented false-positive rates against
non-native and neurodivergent writers and active litigation over wrongful
accusations, refusing to answer is the correct output for a large share of
real inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ai_text_eval.conformal import ConformalCalibration
from ai_text_eval.normalize import NormalizationReport
from ai_text_eval.spans import SpanAnalysis

# Below this, no published detector produces evidence worth acting on.
MIN_VERDICT_WORDS = 100

# A document needs both confident-AI and confident-human mass to be Mixed
# rather than simply uncertain.
MIXED_LOWER = 0.15
MIXED_UPPER = 0.85


class Label(str, Enum):
    HUMAN = "human"
    AI = "ai"
    MIXED = "mixed"
    ABSTAIN = "abstain"


class AbstainReason(str, Enum):
    TOO_SHORT = "text_too_short"
    NO_CALIBRATION = "no_calibration_for_language"
    UNCERTIFIED_CAP = "calibration_too_small_for_fpr_cap"
    TAMPERED = "unicode_tampering_detected"
    AMBIGUOUS = "score_in_ambiguous_band"


@dataclass
class Verdict:
    label: Label
    score: float
    n_words: int
    abstain_reasons: list[AbstainReason] = field(default_factory=list)
    ai_word_fraction: float | None = None
    threshold: float | None = None
    fpr_cap: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.label is not Label.ABSTAIN

    def to_dict(self) -> dict:
        return {
            "label": self.label.value,
            "score": round(self.score, 4),
            "n_words": self.n_words,
            "actionable": self.actionable,
            "ai_word_fraction": (
                round(self.ai_word_fraction, 4) if self.ai_word_fraction is not None else None
            ),
            "threshold": round(self.threshold, 6) if self.threshold is not None else None,
            "fpr_cap": self.fpr_cap,
            "abstain_reasons": [r.value for r in self.abstain_reasons],
            "notes": self.notes,
        }

    def explain(self) -> str:
        if self.label is Label.ABSTAIN:
            reasons = ", ".join(r.value.replace("_", " ") for r in self.abstain_reasons)
            return f"ABSTAIN — {reasons}"
        if self.label is Label.MIXED:
            pct = (self.ai_word_fraction or 0.0) * 100
            return f"MIXED — roughly {pct:.0f}% of the text scores as AI-generated"
        if self.label is Label.AI:
            return (
                f"AI — clears the threshold certified at FPR <= {self.fpr_cap:.3%}"
                if self.fpr_cap is not None
                else "AI"
            )
        return "HUMAN — does not clear the FPR-capped threshold"


def decide(
    score: float,
    n_words: int,
    calibration: ConformalCalibration | None = None,
    spans: SpanAnalysis | None = None,
    normalization: NormalizationReport | None = None,
    language: str = "en",
    provenance_is_ai: bool | None = None,
) -> Verdict:
    """Turn a score plus its context into an actionable verdict or an abstention.

    `provenance_is_ai` carries a *verified* watermark or C2PA result: True is
    strong positive evidence, None is uninformative. False is deliberately not
    treated as exculpatory — see provenance.py.
    """
    reasons: list[AbstainReason] = []
    notes: list[str] = []

    if n_words < MIN_VERDICT_WORDS:
        reasons.append(AbstainReason.TOO_SHORT)
        notes.append(
            f"{n_words} words; below {MIN_VERDICT_WORDS} the between-class "
            "distributions overlap almost completely."
        )

    if normalization is not None and normalization.is_suspicious:
        reasons.append(AbstainReason.TAMPERED)
        notes.append(
            f"{normalization.homoglyphs_replaced} homoglyphs and "
            f"{normalization.invisibles_removed} invisible characters repaired; "
            "the document was deliberately obfuscated, so route to human review."
        )

    if calibration is None:
        reasons.append(AbstainReason.NO_CALIBRATION)
        notes.append(
            f"no conformal calibration for language '{language}'; thresholds do "
            "not transfer across languages because score distributions differ."
        )
    else:
        if calibration.language != language:
            reasons.append(AbstainReason.NO_CALIBRATION)
            notes.append(
                f"calibration is for '{calibration.language}' but the text is "
                f"'{language}'."
            )
        if not calibration.is_certified:
            reasons.append(AbstainReason.UNCERTIFIED_CAP)
            notes.append(
                f"calibration has {calibration.n_calibration} human texts but "
                f"an FPR cap of {calibration.alpha:.3%} needs at least "
                f"{calibration.n_calibration + calibration.shortfall}. No finite "
                "threshold can honor the requested cap."
            )

    # Verified provenance is the one signal strong enough to answer through a
    # short-text abstention: a valid AI manifest is not a statistical inference.
    if provenance_is_ai is True:
        return Verdict(
            label=Label.AI,
            score=max(score, 0.99),
            n_words=n_words,
            ai_word_fraction=spans.ai_word_fraction if spans else None,
            fpr_cap=calibration.alpha if calibration else None,
            notes=notes + ["verified provenance: cryptographic evidence, not a score"],
        )

    if reasons:
        return Verdict(
            label=Label.ABSTAIN,
            score=score,
            n_words=n_words,
            abstain_reasons=reasons,
            ai_word_fraction=spans.ai_word_fraction if spans else None,
            fpr_cap=calibration.alpha if calibration else None,
            notes=notes,
        )

    assert calibration is not None
    threshold = calibration.threshold_for(n_words)
    fraction = spans.ai_word_fraction if (spans and spans.reliable) else None

    if fraction is not None and MIXED_LOWER <= fraction <= MIXED_UPPER:
        label = Label.MIXED
        if spans and spans.change_point and spans.change_point.is_significant:
            notes.append(
                f"score shifts by {spans.change_point.delta:+.2f} at sentence "
                f"{spans.change_point.sentence_index} — a likely human/AI seam."
            )
    elif score >= threshold:
        label = Label.AI
    else:
        label = Label.HUMAN

    return Verdict(
        label=label,
        score=score,
        n_words=n_words,
        ai_word_fraction=fraction,
        threshold=threshold,
        fpr_cap=calibration.alpha,
        notes=notes,
    )
