"""Provenance signals: watermarks and content credentials.

EU AI Act Article 50 machine-readable marking obligations and California
SB 942 create real demand for a provenance layer, and C2PA v2.3 extended
Content Credentials to unstructured text. So an eval engine needs somewhere
to put verified provenance — but the literature is equally clear about how
weak the signal is in the absorbing direction:

- SynthID-Text degrades under paraphrase, back-translation and copy-paste,
  and has been spoofed (DITTO distillation attack) and scrubbed (watermark
  stealing). Its presence is even cheaply detectable in black-box settings.
- Text provenance is structurally weaker than for images or audio, because
  text is trivially copy-pasteable and platforms strip metadata.
- No public production text watermark from OpenAI, Anthropic, or Meta has
  been confirmed.

The asymmetry that follows is the whole design of this module and it is
enforced in code, not left to the caller's discipline:

    **Verified AI provenance raises confidence. Absent provenance changes
    nothing.**

`combine` cannot lower a score. Treating "no watermark found" as evidence of
human authorship would mean every unwatermarked model — which is nearly all
of them — launders its output through the detector, and would make stripping
a watermark an exculpatory act.

Nothing here verifies signatures. C2PA verification needs COSE signature
checking against a trust list, and watermark detection needs the generator's
key. Both belong to the caller; this module consumes an already-verified
result and enforces how it may be used.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProvenanceStatus(str, Enum):
    #: A signature or watermark was verified and attributes the text to a model.
    VERIFIED_AI = "verified_ai"
    #: A manifest was verified and attributes the text to a human author.
    #: Weak: it attests to a claim about origin, not to the absence of AI.
    VERIFIED_HUMAN_CLAIM = "verified_human_claim"
    #: A manifest or watermark was found but failed verification — tampering.
    INVALID = "invalid"
    #: Nothing found. Uninformative, and by far the most common case.
    ABSENT = "absent"
    #: Not checked.
    UNKNOWN = "unknown"


@dataclass
class ProvenanceSignal:
    status: ProvenanceStatus = ProvenanceStatus.UNKNOWN
    scheme: str = ""          # e.g. "c2pa-2.3", "synthid-text"
    issuer: str = ""
    detail: str = ""

    @property
    def is_ai_evidence(self) -> bool:
        return self.status is ProvenanceStatus.VERIFIED_AI

    @property
    def is_informative(self) -> bool:
        return self.status in (
            ProvenanceStatus.VERIFIED_AI,
            ProvenanceStatus.INVALID,
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "scheme": self.scheme,
            "issuer": self.issuer,
            "detail": self.detail,
            "informative": self.is_informative,
        }


def combine(score: float, signal: ProvenanceSignal) -> tuple[float, str]:
    """Fold provenance into a detector score. Returns (score, explanation).

    Monotone by construction: the returned score is never below the input.
    """
    if signal.status is ProvenanceStatus.VERIFIED_AI:
        return max(score, 0.99), (
            f"verified {signal.scheme or 'provenance'} marks this text as "
            "machine-generated; this is cryptographic evidence, not inference"
        )
    if signal.status is ProvenanceStatus.INVALID:
        # A broken manifest means someone edited signed content. That is a
        # reason to look harder, never a reason to relax.
        return max(score, min(1.0, score + 0.10)), (
            "provenance manifest present but failed verification — the content "
            "was altered after signing; route to human review"
        )
    if signal.status is ProvenanceStatus.VERIFIED_HUMAN_CLAIM:
        return score, (
            "a human-authorship claim is signed, but a signed claim attests to "
            "its issuer, not to how the text was written; score unchanged"
        )
    if signal.status is ProvenanceStatus.ABSENT:
        return score, (
            "no watermark or content credential found, which is uninformative: "
            "most models emit none, and watermarks are removable by paraphrase"
        )
    return score, "provenance not checked"


def absence_is_not_evidence() -> str:
    """The one-line policy this module exists to enforce."""
    return (
        "Absence of a watermark is not evidence of human authorship. Most "
        "deployed models emit no text watermark at all, and those that do can "
        "be scrubbed by paraphrase or spoofed onto human text."
    )
