"""Perplexity + burstiness detector (the GPTZero family of signals).

Idea: language models emit text their own probability model finds *likely*.
Scoring a text with a reference LM therefore gives:

- **log-perplexity**: LLM output sits in the high-probability region of any
  reasonably similar LM, so its perplexity is low; human writing is more
  surprising, so its perplexity is higher.
- **surprisal burstiness**: humans alternate predictable and wildly
  unpredictable spans; per-sentence perplexity variance is higher for
  human text.

Requires the `perplexity` extra (torch + transformers). GPT-2 is used by
default because it is small, fully open, and the standard baseline in the
detection literature (DetectGPT, GPTZero-style analyses).
"""

from __future__ import annotations

import math

from ai_text_eval.detectors.base import MIN_RELIABLE_WORDS, Detector, DetectorResult
from ai_text_eval.text_features import logistic, sentences, stdev, words

try:  # pragma: no cover - import guard exercised only without the extra
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "PerplexityDetector needs the 'perplexity' extra: "
        "pip install 'ai-text-eval[perplexity]'"
    ) from _err


class PerplexityDetector(Detector):
    name = "perplexity"

    # Logistic calibration on log-perplexity under GPT-2:
    # modern-LLM output typically lands at ppl ~8-35 (log ~2.1-3.6),
    # human prose at ppl ~40-200 (log ~3.7-5.3).
    LOGPPL_MID = 3.65
    LOGPPL_SCALE = 0.45
    # Per-sentence log-ppl stdev: humans ~0.5-1.0, LLMs ~0.2-0.5.
    BURST_MID = 0.55
    BURST_SCALE = 0.20
    W_PPL = 0.7
    W_BURST = 0.3

    MAX_TOKENS = 1024

    def __init__(self, model_name: str = "gpt2", device: str | None = None):
        self.model_name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.model.to(self.device)
        self.model.eval()

    @torch.no_grad()
    def _log_ppl(self, text: str) -> float | None:
        """Mean negative log-likelihood per token (natural log)."""
        ids = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=self.MAX_TOKENS
        ).input_ids.to(self.device)
        if ids.shape[1] < 2:
            return None
        logits = self.model(ids).logits
        # Predict token i+1 from prefix ending at i.
        shift_logits = logits[:, :-1, :]
        shift_labels = ids[:, 1:]
        loss = torch.nn.functional.cross_entropy(
            shift_logits.reshape(-1, shift_logits.size(-1)),
            shift_labels.reshape(-1),
            reduction="mean",
        )
        return float(loss)

    def score(self, text: str) -> DetectorResult:
        n_words = len(words(text))
        overall = self._log_ppl(text)
        if overall is None:
            return DetectorResult(score=0.5, reliable=False, details={"error": "text too short"})

        sents = [s for s in sentences(text) if len(words(s)) >= 3]
        per_sent = [lp for s in sents if (lp := self._log_ppl(s)) is not None]
        burst = stdev(per_sent) if len(per_sent) >= 3 else None

        # Low perplexity -> AI-like.
        ppl_score = logistic((self.LOGPPL_MID - overall) / self.LOGPPL_SCALE)
        if burst is not None:
            burst_score = logistic((self.BURST_MID - burst) / self.BURST_SCALE)
            score = self.W_PPL * ppl_score + self.W_BURST * burst_score
        else:
            score = ppl_score

        details = {
            "model": self.model_name,
            "log_perplexity": round(overall, 4),
            "perplexity": round(math.exp(overall), 2),
            "sentence_logppl_stdev": round(burst, 4) if burst is not None else None,
            "n_scored_sentences": len(per_sent),
            "n_words": n_words,
        }
        return DetectorResult(
            score=score,
            reliable=n_words >= MIN_RELIABLE_WORDS,
            details=details,
        )
