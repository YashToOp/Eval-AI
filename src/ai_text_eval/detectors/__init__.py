"""Detector implementations.

Every detector maps text -> DetectorResult with a score in [0, 1],
where higher means "more likely AI-generated". Model-based detectors
(perplexity, binoculars) are optional and degrade gracefully when
torch/transformers are not installed.
"""

from ai_text_eval.detectors.base import Detector, DetectorResult
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.detectors.phrases import PhraseDetector
from ai_text_eval.detectors.stylometry import StylometryDetector


def available_detectors(include_model_based: bool = True) -> dict[str, Detector]:
    """Instantiate every detector that can run in this environment."""
    detectors: dict[str, Detector] = {
        "stylometry": StylometryDetector(),
        "phrases": PhraseDetector(),
    }
    if include_model_based:
        try:
            from ai_text_eval.detectors.perplexity import PerplexityDetector

            detectors["perplexity"] = PerplexityDetector()
        except ImportError:
            pass
        try:
            from ai_text_eval.detectors.binoculars import BinocularsDetector

            detectors["binoculars"] = BinocularsDetector()
        except ImportError:
            pass
    return detectors


__all__ = [
    "Detector",
    "DetectorResult",
    "StylometryDetector",
    "PhraseDetector",
    "EnsembleDetector",
    "available_detectors",
]
