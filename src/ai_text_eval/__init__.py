"""ai_text_eval — an evaluation framework for AI-generated-text detection.

The package has three jobs:

1. **Detect**: score a text on how AI-like it looks, using the same families
   of signals the state-of-the-art detectors use (perplexity & burstiness,
   cross-perplexity ratios, stylometry, lexical forensics).
2. **Evaluate**: benchmark any detector on labeled human/AI corpora with the
   metrics the research literature reports (AUROC, TPR at low FPR,
   calibration error, bootstrap confidence intervals).
3. **Attack**: measure how much a paraphrase/"humanization" rewrite of an
   AI text degrades each detector — the evasion experiment.

Nothing here (or anywhere) can reliably classify a single sentence.
The framework is explicit about that: detectors report a `reliable` flag
and the ensemble abstains below a minimum-evidence word count.
"""

from ai_text_eval.conformal import ConformalCalibration, calibrate, min_calibration_size
from ai_text_eval.detectors.base import Detector, DetectorResult
from ai_text_eval.detectors.ensemble import EnsembleDetector
from ai_text_eval.detectors.phrases import PhraseDetector
from ai_text_eval.detectors.stylometry import StylometryDetector
from ai_text_eval.detectors.supervised import SupervisedDetector, cross_val_scores
from ai_text_eval.engine import DetectionEngine, EngineResult
from ai_text_eval.normalize import normalize
from ai_text_eval.provenance import ProvenanceSignal, ProvenanceStatus
from ai_text_eval.spans import analyze_spans
from ai_text_eval.verdict import Label, Verdict, decide

__version__ = "2.0.0"

__all__ = [
    "Detector",
    "DetectorResult",
    "StylometryDetector",
    "PhraseDetector",
    "SupervisedDetector",
    "EnsembleDetector",
    "DetectionEngine",
    "EngineResult",
    "ConformalCalibration",
    "calibrate",
    "min_calibration_size",
    "cross_val_scores",
    "normalize",
    "analyze_spans",
    "ProvenanceSignal",
    "ProvenanceStatus",
    "Label",
    "Verdict",
    "decide",
    "__version__",
]
