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


def _model_extra_installed() -> bool:
    """Whether the optional model-based detectors could be constructed.

    Checked with find_spec rather than by importing, so merely asking which
    detectors exist never triggers a model download.
    """
    from importlib.util import find_spec

    try:
        return find_spec("torch") is not None and find_spec("transformers") is not None
    except (ImportError, ValueError):
        return False


def detector_names(include_model_based: bool = True) -> list[str]:
    """Names of the detectors that could run here, without building any."""
    names = ["stylometry", "phrases"]
    if include_model_based and _model_extra_installed():
        names += ["perplexity", "binoculars"]
    return names


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
    "detector_names",
]
