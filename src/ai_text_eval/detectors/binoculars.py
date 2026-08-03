"""Binoculars-style cross-perplexity detector (Hans et al., 2024).

Binoculars is one of the strongest zero-shot detectors published:
instead of asking "is this text likely under an LM?" it asks "is this
text *more* likely than one LM would predict another LM to be?"

    B(s) = logPPL_observer(s) / X-PPL_{observer, performer}(s)

where X-PPL is the cross-entropy, averaged over positions, between the
*performer's* next-token distribution and the *observer's* log-probs.
Machine text tends to sit where the two models agree (B is low); human
text surprises both models in different ways (B is high).

The published system uses the Falcon-7B / Falcon-7B-Instruct pair and
reports ~99% TPR at 0.01% FPR on long documents. This implementation is
pair-agnostic; the default `gpt2` / `distilgpt2` pair keeps the download
small and demonstrates the method, but it is *not* expected to reproduce
the paper's numbers — swap in a larger shared-tokenizer pair for that.

We report an AI-likelihood as a logistic transform of -B so all
detectors share the same orientation (higher = more AI-like).

Requires the `perplexity` extra (torch + transformers). The two models
must share a tokenizer/vocabulary.
"""

from __future__ import annotations

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import logistic, words

try:  # pragma: no cover
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "BinocularsDetector needs the 'perplexity' extra: "
        "pip install 'ai-text-eval[perplexity]'"
    ) from _err


class BinocularsDetector(Detector):
    name = "binoculars"

    # Calibration for the gpt2/distilgpt2 pair (fit on the demo corpora;
    # the falcon pair in the paper uses a threshold near 0.90).
    B_MID = 0.94
    B_SCALE = 0.05

    MAX_TOKENS = 1024

    def __init__(
        self,
        observer: str = "gpt2",
        performer: str = "distilgpt2",
        device: str | None = None,
    ):
        self.observer_name = observer
        self.performer_name = performer
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(observer)
        self.observer = AutoModelForCausalLM.from_pretrained(observer).to(self.device).eval()
        self.performer = AutoModelForCausalLM.from_pretrained(performer).to(self.device).eval()

    @torch.no_grad()
    def _binoculars_score(self, text: str) -> float | None:
        ids = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=self.MAX_TOKENS
        ).input_ids.to(self.device)
        if ids.shape[1] < 2:
            return None

        obs_logits = self.observer(ids).logits[:, :-1, :].float()
        perf_logits = self.performer(ids).logits[:, :-1, :].float()
        labels = ids[:, 1:]

        # logPPL under the observer.
        obs_logprobs = torch.log_softmax(obs_logits, dim=-1)
        nll = -obs_logprobs.gather(-1, labels.unsqueeze(-1)).squeeze(-1)
        log_ppl = float(nll.mean())

        # Cross-entropy of performer's distribution against observer's
        # log-probs, averaged over positions.
        perf_probs = torch.softmax(perf_logits, dim=-1)
        xent = float(-(perf_probs * obs_logprobs).sum(dim=-1).mean())
        if xent == 0.0:
            return None
        return log_ppl / xent

    def score(self, text: str) -> DetectorResult:
        n_words = len(words(text))
        b = self._binoculars_score(text)
        if b is None:
            return DetectorResult(score=0.5, reliable=False, details={"error": "text too short"})

        # Low B -> AI-like.
        ai_score = logistic((self.B_MID - b) / self.B_SCALE)
        details = {
            "observer": self.observer_name,
            "performer": self.performer_name,
            "binoculars_score": round(b, 4),
            "n_words": n_words,
        }
        return DetectorResult(
            score=ai_score,
            reliable=n_words >= MIN_RELIABLE_WORDS,
            details=details,
        )
