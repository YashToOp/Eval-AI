# ai-text-eval

An evaluation framework for **AI-generated-text detection**: it implements the
signal families real detectors use, benchmarks them with the metrics the research
literature actually reports, and — most importantly — measures how badly they
break under paraphrase attack.

The headline finding it is built to demonstrate honestly:

> **Detecting whether a single sentence was written by AI is not a solved problem.
> It is close to an impossible one.** Detection works, imperfectly, on long
> documents. At sentence length every published system degrades toward guessing.

```
pip install -e .
ai-text-eval benchmark            # run the demo eval
ai-text-eval score --file draft.txt
ai-text-eval compare --original-file a.txt --rephrased-file b.txt
```

---

## 1. How does an eval actually check whether text was written by AI?

There is no magic. Every detector in existence — commercial or academic — draws
from four signal families. This repo implements all four so you can see and
measure each one.

### (a) Perplexity / likelihood signals — `detectors/perplexity.py`

A language model assigns a probability to every next token. Run text through a
reference LM and measure how *surprised* it is:

- **Low perplexity → likely AI.** LLMs decode from the high-probability region
  of their own distribution, so their output looks unsurprising to any similar
  model.
- **High surprisal variance ("burstiness") → likely human.** Humans swing between
  predictable phrasing and genuinely unexpected word choices. LLM output is
  smoother.

This is the core of GPTZero and of the whole zero-shot detection literature
(DetectGPT and successors). It's the single strongest cheap signal.

### (b) Cross-model / curvature signals — `detectors/binoculars.py`

The current state of the art. **Binoculars** (Hans et al., 2024) computes the
ratio of a text's perplexity under an *observer* model to its cross-perplexity
between the observer and a *performer* model:

```
B(s) = logPPL_observer(s) / X-PPL_{observer,performer}(s)
```

Machine text sits where two different models *agree*; human text surprises both
models in uncorrelated ways. Low `B` → machine. This normalization is why
Binoculars beats raw perplexity: it cancels out "this prompt was just inherently
predictable" (a recipe, a legal boilerplate paragraph) which is what causes raw
perplexity detectors to false-accuse.

DetectGPT's related trick is **probability curvature**: perturb the text slightly
and re-score. AI text sits on a local maximum of the likelihood surface, so
perturbation reliably lowers the score; human text doesn't behave that way.

### (c) Stylometry — `detectors/stylometry.py`

Distributional texture, no model required: sentence-length variance, share of
very short sentences, moving-average type-token ratio, mean word length,
punctuation profile (em-dash density is a famous post-2023 tell). Weak
individually, cheap, and interpretable — every raw feature value is returned in
`details`.

### (d) Lexical forensics — `detectors/phrases.py`

The measurable frequency spikes in post-2023 text: *delve*, *tapestry*,
*underscores*, *it's important to note*, *plays a crucial role*, the
not-only-but-also frame, triadic lists. A weighted lexicon scored per 1000 words.

This one is the most *legible* and the most *fragile*. It catches lazy unedited
LLM output instantly and misses anything a person edited for ten minutes.

### The two things that are not signal families

- **Watermarking** (Kirchenbauer et al.): the generator biases token choice
  toward a secret pseudorandom "green list", which a verifier can detect with a
  formal p-value. This is the *only* approach with real statistical guarantees —
  but it requires cooperation from whoever generated the text, so it can't be
  applied retroactively or to a model that doesn't implement it.
- **Trained classifiers** (RoBERTa fine-tunes, OpenAI's retired classifier).
  Strong in-distribution, and they fall apart on new models, new domains, and
  non-native English writing. OpenAI withdrew theirs in July 2023 for low
  accuracy.

---

## 2. What is the actual ceiling? ("what is the max they can go")

Published, peer-reviewed numbers on **long documents** (several hundred words):

| System | Reported performance | Conditions |
|---|---|---|
| Binoculars | ~99% TPR @ 0.01% FPR | 512+ tokens, news/essay domain |
| DetectGPT | ~0.95 AUROC | in-domain, known source model |
| Commercial detectors | 80–98% claimed accuracy | vendor-reported, own test sets |
| Watermark detection | p < 1e-6 achievable | only if the generator watermarked |

And the same systems under realistic conditions:

| Condition | What happens |
|---|---|
| **Paraphrased** (DIPPER, or "rewrite this to sound human") | Detection collapses. Krishna et al. 2023 drove multiple detectors from ~70% to ~5–20% TPR. |
| **Short text (< ~100 words)** | AUROC falls toward 0.5–0.7. Variance dominates. |
| **Out-of-domain / newer model** | Large drops; calibration constants stop transferring. |
| **Non-native English writing** | Liang et al. 2023: detectors flagged **61%** of TOEFL essays by non-native speakers as AI. This is the most serious real-world harm in the field. |
| **Human text edited by AI, or AI text edited by a human** | Essentially undefined — the ground-truth label itself stops being binary. |

There is also a theoretical result worth taking seriously: **Sadasivan et al.,
"Can AI-Generated Text Be Reliably Detected?"** shows that as an LLM's output
distribution converges toward the human distribution, the total variation
distance between them shrinks, and *any* detector's AUROC is bounded toward 0.5.
You cannot separate two distributions that have converged. Detection is a race
that the generator wins asymptotically.

### So: a single sentence?

**No.** Not by this framework, not by GPTZero, not by anything published.
A 15-word sentence carries perhaps 20–30 tokens of evidence. The
between-class distributions overlap almost completely at that length. That is
why `MIN_RELIABLE_WORDS = 50` in `detectors/base.py`, and why the ensemble
returns *"insufficient evidence"* rather than a verdict below it:

```
$ ai-text-eval score "The system leverages advanced algorithms to optimize performance."
Verdict: insufficient evidence (text too short for any detector to be reliable)
```

Any tool that hands you a confident percentage for one sentence is selling you
a number it cannot support. Refusing to answer is the correct behavior, and
building that refusal into the harness is the single most important design
decision in this repo.

---

## 3. What this framework measures

```
ai-text-eval benchmark --human human.jsonl --ai ai.jsonl --pairs pairs.jsonl
```

**Detection metrics** (`metrics.py`, all pure-Python and deterministic):

- **AUROC** — rank-based, with correct tie handling via the Mann-Whitney identity.
- **TPR @ 1% and 5% FPR** — the number that actually matters. In deployment a
  false accusation costs vastly more than a miss, so accuracy at threshold 0.5 is
  close to meaningless. Modern papers report TPR at 0.01% FPR for this reason.
- **Brier score + Expected Calibration Error** — is the score a real probability,
  or just a ranking?
- **Stratified bootstrap 95% CIs** on AUROC — because point estimates on small
  corpora are noise.

**Robustness metrics** (`evasion.py`):

- **Mean score drop** under paraphrase.
- **Evasion rate** — of the AI texts a detector flagged, what fraction escape the
  flag after rewriting? Both texts in a pair are AI-generated, so *every* drop is
  detector failure, not a label change.

---

## 4. Demo results, and why you should not trust them

```
Corpus: 18 human, 18 AI texts

detector       AUROC            95% CI  TPR@5%FPR  TPR@1%FPR  F1@0.5   Brier    ECE
-----------------------------------------------------------------------------------
stylometry    0.7130    [0.522, 0.877]      0.000      0.000   0.706  0.2242  0.196
phrases       0.7886    [0.651, 0.900]      0.444      0.444   0.727  0.1706  0.049
ensemble      0.7870    [0.611, 0.926]      0.667      0.667   0.706  0.1791  0.214

Paraphrase-attack robustness (12 original/rephrased AI pairs):

detector      pairs  mean orig  mean reph  mean drop  evasion rate
------------------------------------------------------------------
stylometry       12      0.563      0.222      0.341       100.0%
phrases          12      0.965      0.300      0.665       100.0%
ensemble         12      0.764      0.261      0.503       100.0%
```

Read these numbers with the following caveats, which are part of the result:

1. **n = 36. The confidence intervals are enormous.** `stylometry`'s CI includes
   0.52 — statistically indistinguishable from a coin flip. Anyone reporting
   AUROC 0.71 from 36 documents without a CI is overclaiming.
2. **The demo human corpus is a confound.** It is public-domain literary and
   expository prose (Darwin, Austen, Melville, Woolf). A detector separating
   *19th-century literature* from *2020s LLM blog prose* may be detecting the
   era and genre, not the author. Real evaluation needs contemporary human text
   in the same domain and register as the AI text.
3. **The model-based detectors are absent from these numbers** — they need
   `torch`/`transformers` (`pip install -e '.[perplexity]'`). Perplexity and
   Binoculars are the strong signals; the two shown here are the weak ones.
4. **The calibration constants were chosen partly by looking at this corpus**,
   so these are optimistic in-sample numbers, not held-out generalization.
5. **The ensemble does not beat `phrases` alone here** (0.787 vs 0.789). With
   this corpus and these two weak detectors, combination buys nothing. Reported
   because it's true, not because it flatters the design.

**The 100% evasion rate is the honest headline.** Every AI text that got flagged
escaped the flag after being rewritten, with no change to the ground-truth label.
That result is robust to all the caveats above — and it is the same result the
published literature gets against far stronger detectors.

### Getting real data

The bundled corpora are for smoke-testing the harness. For an actual evaluation,
point the CLI at a research benchmark in the JSONL format below:

- **RAID** (Dugan et al. 2024) — 6M+ generations, 11 models, 11 domains, 11 adversarial attacks. The most rigorous option.
- **M4** — multi-generator, multi-domain, multilingual.
- **HC3** — human vs ChatGPT QA pairs; older and easier, models have moved on.
- **GPT-Wiki-Intro** — paired human/AI Wikipedia intros.

---

## 5. Data format

Labeled corpora (JSONL, one record per line):

```json
{"text": "...", "label": 1, "source": "gpt-4o", "meta": {"domain": "essay"}}
```

`label`: `1` = AI-generated, `0` = human-written.

Paraphrase-attack pairs — both sides are AI-generated; the rewrite does not
change the ground truth:

```json
{"original": "...", "rephrased": "...", "meta": {"technique": "voice_shift"}}
```

---

## 6. Layout

```
src/ai_text_eval/
  text_features.py          sentence splitting, MATTR, burstiness, logistic
  detectors/
    base.py                 Detector ABC, DetectorResult, MIN_RELIABLE_WORDS
    stylometry.py           distributional texture
    phrases.py              AI-ism lexicon + structural patterns
    perplexity.py           GPT-2 log-perplexity + surprisal burstiness  [extra]
    binoculars.py           cross-perplexity ratio, Hans et al. 2024     [extra]
    ensemble.py             weighted combination + logistic calibration
  metrics.py                AUROC, TPR@FPR, ECE, Brier, bootstrap CIs
  evasion.py                paraphrase-attack accounting
  dataset.py  report.py  cli.py
tests/                      65 tests
data/                       demo corpora
```

`pytest` to run the suite. The core package has **no dependencies**; only the
two model-based detectors need the `perplexity` extra.

---

## 7. Intended use

This is a **measurement** tool. It scores text, benchmarks detectors, and
quantifies how much paraphrasing degrades them — the paraphrase module consumes
pairs you supply, it does not generate evasions.

Detector scores are evidence about a *distribution*, not proof about a *document*.
Given the documented false-positive rate against non-native English writers, no
score from this or any detector should be used as the basis for an accusation
against an individual.
