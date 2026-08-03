from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Below this many words, no detector's output should be treated as evidence.
# Single sentences are ~10-25 words; the research consensus is that
# reliable detection needs hundreds of tokens.
MIN_RELIABLE_WORDS = 50


@dataclass
class DetectorResult:
    """Outcome of scoring one text with one detector.

    score:    P(AI-generated)-like value in [0, 1]; 0.5 means "no signal".
    reliable: False when the text is too short (or the detector otherwise
              lacks evidence) for the score to mean anything. Benchmarks
              still use the raw score; verdicts shown to people must not.
    details:  raw feature values, for transparency and debugging.
    """

    score: float
    reliable: bool = True
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.score = min(1.0, max(0.0, self.score))


class Detector(ABC):
    """Interface every detector implements."""

    #: short identifier used in CLI flags and reports
    name: str = "detector"

    @abstractmethod
    def score(self, text: str) -> DetectorResult:
        """Score one text. Higher = more AI-like."""

    def score_many(self, texts: list[str]) -> list[DetectorResult]:
        """Batch hook; model-based detectors override this for speed."""
        return [self.score(t) for t in texts]
