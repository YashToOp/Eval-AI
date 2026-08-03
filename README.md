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
| Binoculars | ~90%+ TPR at very low FPR, ~0.99 AUROC on its best splits | several hundred tokens, specific domains and generator models; per-dataset results vary widely |
| DetectGPT | ~0.95 AUROC | in-domain, known source model |
| Commercial detectors | 80–98% claimed accuracy | vendor-reported, own test sets |
| Watermark detection | p < 1e-6 achievable | only if the generator watermarked |

Treat every row as "best case on the authors' own evaluation splits". These are
ceilings measured under favorable conditions, not numbers you should expect on
your data — which is the entire reason this framework reports confidence
intervals and an evasion column alongside any headline metric.

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

detector       AUROC            95% CI  TPR@5%FPR          (95% CI)  TPR@1%FPR   F1@0.5   Brier    ECE
------------------------------------------------------------------------------------------------------
stylometry    0.7099    [0.519, 0.873]     0.000*    [0.000, 0.556]     0.000*    0.706  0.2253  0.206
phrases       0.7870    [0.620, 0.926]     0.611*    [0.389, 0.833]     0.611*    0.727  0.1699  0.121
ensemble      0.7685    [0.590, 0.907]     0.667*    [0.444, 0.833]     0.667*    0.727  0.1809  0.203

Paraphrase-attack robustness (12 original/rephrased AI pairs):

detector      pairs  mean orig  mean reph  mean drop  evasion rate
------------------------------------------------------------------
stylometry       12      0.563      0.222      0.341        100.0%
phrases          12      0.973      0.370      0.603        100.0%
ensemble         12      0.768      0.296      0.472        100.0%
```

Read these numbers with the following caveats, which are part of the result.
The tool prints them itself, next to the table and inside the JSON report —
a bare list of AUROCs gets quoted onward, a list that names its own confounds
cannot be.

1. **n = 36. The confidence intervals are enormous.** `stylometry`'s CI includes
   0.52 — statistically indistinguishable from a coin flip. So is the apparent
   gap between `stylometry`'s TPR of 0.000 and the ensemble's 0.667: those
   intervals ([0.000, 0.556] and [0.444, 0.833]) overlap.
2. **`TPR@1%FPR` is not measurable here**, hence the asterisks. With 18 human
   texts the smallest non-zero false-positive rate the corpus can express is
   1/18 = 0.056, so a 1% budget and a 5% budget both collapse to "zero false
   positives allowed" and report the identical number under two names.
3. **The demo human corpus is a confound.** It is pre-1930 public-domain prose
   (Darwin, Austen, Melville, Woolf). A detector separating *19th-century
   literature* from *2020s LLM blog prose* may be detecting era and genre, not
   authorship. Real evaluation needs contemporary human text in the same domain
   and register as the AI text.
4. **The model-based detectors are absent from these numbers** — they need
   `torch`/`transformers` (`pip install -e '.[perplexity]'`). Perplexity and
   Binoculars are the strong signals; the two shown here are the weak ones.
   Installing the extra does not remove caveat 5 — those detectors' constants
   are in-sample too.
5. **The calibration constants were chosen partly by looking at this corpus**,
   so these are optimistic in-sample numbers, not held-out generalization.
6. **The ensemble does not beat `phrases` alone here** (0.769 vs 0.787). With
   this corpus and these two weak detectors, combination buys nothing. Reported
   because it's true, not because it flatters the design.

**The 100% evasion rate is the headline — with one caveat that belongs right
next to it.** Every AI text that got flagged escaped the flag after being
rewritten, with no change to the ground-truth label. But the bundled rewrites
were *written to be effective attacks*, and their originals are the same AI
texts used in the detection table above, so these two tables are not
independent evidence. Treat the demo number as an illustration of the failure
mode, not a measurement of how easy evasion is in general.

What makes the finding credible is not this corpus: it is that the published
literature reports the same collapse against far stronger detectors (Krishna et
al. 2023 drove multiple systems from ~70% to ~5–20% TPR with DIPPER). Use
RAID's adversarial splits if you want to measure the size of the effect
properly.

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
  data/                     demo corpora (shipped with the package)
tests/                      117 tests
```

`pytest` to run the suite. The core package has **no dependencies**; only the
two model-based detectors need the `perplexity` extra.

---

## 7. The framework was itself adversarially reviewed

A measurement tool that is quietly wrong is worse than no tool, because its
output looks exactly the same. This code was put through a multi-lens review
(correctness, statistics, API, and scientific validity), with each finding
independently verified by reproduction before being accepted. Every defect
below was real, is fixed, and has a named regression test in
`tests/test_review_regressions.py`.

The ones worth knowing about, because they are easy to reproduce in any
detector you write yourself:

| Defect | Why it mattered |
|---|---|
| Multiword markers matched as bare substrings | `"here are some"` fired inside *"**W**here are some of the best places…"*, manufacturing a confident false positive from ordinary human prose |
| `"no"`, `"co"`, `"est"`, `"vs"` treated as abbreviations | *"The answer was no. Then…"* lost a real sentence boundary, silently corrupting the burstiness feature |
| Bulleted text collapsed into one "sentence" | LLM output is bullet-heavy, so the length statistics were meaningless on exactly the format that matters most |
| Triad bonus was a step function | One extra triad swung the score across most of its range; now a softplus ramp |
| Nested lexicon entries double-charged | `"in the realm of"` also billed for the `"realm"` inside it, so effective weights differed from declared ones |
| Bootstrap upper percentile off by one | Every reported confidence interval tilted upward |
| ECE bin index clamped only at the top | A negative score wrapped via Python negative indexing into the *top* bin |
| `evasion_rate` returned `0.0` for 0/0 | A detector that never fires ranked as the most robust on a "higher = weaker" scale; now `None`, rendered `n/a` |
| Binoculars cross-perplexity was transposed | Cross-entropy is not symmetric, so it computed a quantity the cited paper never defines |
| `--threshold` never reached the metrics table | `--threshold 0.95` and `--threshold 0.5` printed identical numbers |
| Demo data resolved by repo-relative path | `benchmark` crashed in any non-editable install |
| Fractional labels silently truncated | `label: 0.7` loaded as `0`, recording an AI-leaning example as human ground truth |
| `TPR@1%FPR` reported as its own column | Unmeasurable at n=18; now starred, with the resolution limit explained inline |

The last one is the pattern to internalize. Nothing crashed, no test failed,
and the table looked authoritative — it simply printed a number that the corpus
could not support. That is the characteristic failure of eval code, and it is
why this framework reports intervals, marks unmeasurable cells, refuses to
score short text, and prints its own confounds next to its own results.

---

## 8. Intended use

This is a **measurement** tool. It scores text, benchmarks detectors, and
quantifies how much paraphrasing degrades them — the paraphrase module consumes
pairs you supply, it does not generate evasions.

Detector scores are evidence about a *distribution*, not proof about a *document*.
Given the documented false-positive rate against non-native English writers, no
score from this or any detector should be used as the basis for an accusation
against an individual.
