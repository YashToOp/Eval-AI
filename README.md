<div align="center">

# ai-text-eval

**Open-source AI text detector and evaluation framework — detect ChatGPT/LLM-generated text, benchmark detectors, and certify your false-positive rate.**

*Python · no dependencies · MIT licensed*

[![Tests](https://github.com/YashToOp/Eval-AI/actions/workflows/tests.yml/badge.svg)](https://github.com/YashToOp/Eval-AI/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/core%20dependencies-none-brightgreen.svg)](pyproject.toml)

[Quickstart](#quickstart) · [Why](#why-this-exists) · [How detection works](#how-detection-works) · [Architecture](#architecture) · [Commands](#commands) · [FAQ](#faq) · [Limits](#known-limits)

</div>

---

Most detectors hand you a confident percentage. This one tells you when it
has no right to.

It implements the four signal families real detectors use, benchmarks them
with the metrics the research literature reports, controls its own
false-positive rate with a distribution-free guarantee, and **abstains** when
the evidence or the calibration cannot support an answer.

```console
$ python analyze.py --file draft.txt

  AI TEXT ANALYSIS   ai-text-eval v2

INPUT
──────────────────────────────────────────────────────────────────────
  Length                     285 words · 18 sentences
  Integrity                  clean  (no Unicode tampering)

DETECTOR SCORES   0 = human · 1 = AI
──────────────────────────────────────────────────────────────────────
  supervised     ████░░░░░░░░░░░░░░░░░░  0.189  primary layer, 60% weight
  phrases        ██████████████████████  1.000  corroborating
  stylometry     ████████░░░░░░░░░░░░░░  0.359  corroborating
  ──────────────────────────────────────────────────────────────────
  BLENDED        ████████░░░░░░░░░░░░░░  0.385

  OUT OF DISTRIBUTION  primary and corroborators differ by 0.49

SPAN ATTRIBUTION
──────────────────────────────────────────────────────────────────────
  AI-attributed text         76% of words (approximate)
  Possible seam              sentence 8  (0.44 → 0.94)

VERDICT
──────────────────────────────────────────────────────────────────────
  ABSTAIN   calibration too small for the requested FPR cap
```

---

## Quickstart

```bash
git clone https://github.com/YashToOp/Eval-AI.git
cd Eval-AI
pip install -e .
```

Everything at once, on one piece of text:

```bash
python analyze.py "text to check"
python analyze.py --file draft.txt
cat draft.txt | python analyze.py
python analyze.py --file draft.txt --json     # machine-readable
```

Or use the CLI for individual stages:

```bash
ai-text-eval calibrate --human human.jsonl --fpr-cap 0.005 --out cal.json
ai-text-eval analyze   --calibration cal.json --file draft.txt
ai-text-eval fairness  --human human.jsonl --group-by native_language
ai-text-eval robustness
ai-text-eval benchmark --human h.jsonl --ai a.jsonl --pairs pairs.jsonl
```

The core package has **no dependencies**. Only the two model-based detectors
need extras:

```bash
pip install -e '.[perplexity]'   # adds torch + transformers
```

---

## Why this exists

Two claims this framework is built to demonstrate honestly.

> **Detecting whether a single sentence was written by AI is not a solved
> problem — it is close to an impossible one.**

> **The number that matters is not accuracy. It is the false-positive rate,
> per subgroup, at a threshold you can actually certify.**

Detectors have falsely flagged **61.3%** of non-native-English TOEFL essays in
published study, with the effect replicated for neurodivergent writers, AAVE
speakers, and formulaic genres. Surveys report **20%** of Black teens saying
their schoolwork was falsely flagged, versus **7%** of white teens. Dozens of
universities have disabled these tools; students have sued over wrongful
accusations.

Every one of those harms is a **false positive**. So this framework is built
around controlling them, not around maximizing accuracy.

---

## How detection works

Four signal families. All four are implemented here.

<table>
<tr><th>Family</th><th>Idea</th><th>Module</th></tr>
<tr>
<td><b>Perplexity</b></td>
<td>LLMs decode from the high-probability region of their own distribution, so their output looks unsurprising to any similar model. Humans swing between predictable and genuinely unexpected word choices.</td>
<td><code>detectors/perplexity.py</code></td>
</tr>
<tr>
<td><b>Cross-model</b></td>
<td>Binoculars: the ratio of observer perplexity to observer–performer cross-perplexity. Machine text sits where two models <i>agree</i>. The normalization cancels "this passage was inherently predictable", which is what makes raw-perplexity detectors false-accuse recipes and boilerplate.</td>
<td><code>detectors/binoculars.py</code></td>
</tr>
<tr>
<td><b>Stylometry</b></td>
<td>Sentence-length burstiness, lexical diversity, punctuation profile. Weak individually, cheap, interpretable.</td>
<td><code>detectors/stylometry.py</code></td>
</tr>
<tr>
<td><b>Lexical forensics</b></td>
<td>Measurable post-2023 frequency spikes: <i>delve</i>, <i>tapestry</i>, <i>underscores</i>, <i>plays a crucial role</i>. Catches unedited output instantly; misses anything edited for ten minutes.</td>
<td><code>detectors/phrases.py</code></td>
</tr>
</table>

<details>
<summary><b>Newer methods, and why they aren't bundled</b></summary>

<br>

Glimpse scores through proprietary APIs using only top-K logprobs.
Lastde/Lastde++ treat the token-probability sequence as a time series.
RepreGuard uses surrogate hidden states. TOCSIN adds token cohesiveness as a
plug-in to any base detector. WaveDetect applies wavelet transforms.

Most need model access this repo doesn't ship. Treat their headline numbers
with care — several drop sharply when re-evaluated on RAID rather than on
their authors' own suites (Glimpse: ~0.95 → ~0.76).

**Not signal families:** watermarking needs generator cooperation and is
spoofable and scrubbable; naive trained classifiers are strong in-distribution
and brittle across model generations.

</details>

<details>
<summary><b>What is the actual ceiling?</b></summary>

<br>

Best case, on the authors' own favorable splits, on documents of several
hundred words:

| System | Reported | Conditions |
|---|---|---|
| Supervised commercial | ~99% TPR @ 5% FPR clean, ~97.7% adversarial | RAID-based shared task, blinded |
| Binoculars | ~79% aggregate TPR @ 5% FPR | RAID non-adversarial split |
| Glimpse / Lastde / RepreGuard | 0.95 / 0.959 / 0.949 AUROC | authors' own benchmarks |
| Watermark detection | p < 1e-6 | only if the generator watermarked |

Under realistic conditions:

| Condition | What happens |
|---|---|
| **Paraphrased / humanized** | Detection becomes probabilistic. One audit found a leading detector's false-negative rate rose to ~50%+. RL attacks drive several zero-shot detectors to ~0.001 TPR@1%FPR. |
| **Base (non-instruction-tuned) models** | Judged **96.7%** and **98.8% human** by two leading commercial detectors. Detectors track instruction-tuning artifacts, not an invariant machine signature. |
| **Short text (< ~100 words)** | All three major commercial tools lose accuracy below 50 words. |
| **Non-native English** | 61.3% of TOEFL essays falsely flagged. |
| **Each frontier generation** | Somewhat harder than the last. Claude-family text is repeatedly reported as hardest. |

Theory has settled into a nuanced middle: detection degrades as the machine
and human distributions converge in total variation (Sadasivan), but remains
possible with more samples (Chakraborty). Empirically the achievable
*operating point* keeps sliding.

</details>

### A single sentence?

**No.** A 15-word sentence carries 20–30 tokens of evidence; the class
distributions overlap almost entirely. The engine abstains below 100 words:

```console
$ python analyze.py "The system leverages advanced algorithms to optimize performance."
VERDICT
  ABSTAIN   text too short
```

Any tool returning a confident percentage for one sentence is inventing it.

---

## Architecture

```
normalize → score (primary + corroborators) → OOD check
          → span analysis → conformal threshold → ternary verdict
```

The order is load-bearing.

### Conformal false-positive control

Calibrate on human text; get a distribution-free, finite-sample guarantee
`P(score(new human text) ≥ τ) ≤ α` for *any* detector and *any* score
distribution.

The property that matters most is the one that refuses:

```console
$ ai-text-eval calibrate --human human.jsonl --fpr-cap 0.005
Calibration texts:   18
Minimum required:    199

Status: NOT CERTIFIED — 181 more human texts are needed.
        The engine will abstain rather than flag, because no finite
        threshold can honor the requested cap at this sample size.
```

Split conformal takes the `ceil((n+1)(1-α))`-th order statistic, which exceeds
`n` unless `n + 1 ≥ 1/α`:

| FPR cap | Human texts required |
|--------:|---------------------:|
| 0.5% | 199 |
| 1% | 99 |
| 5% | 19 |

Below that, no threshold is honest — so the engine returns `+∞` instead of a
number the data cannot support. Multiscale conformal adds per-length-bucket
thresholds, which is what makes short-text handling principled rather than a
hand-set word count.

### Ternary verdicts with mandatory abstention

`Human` · `AI` · `Mixed` · `Abstain`

`Mixed` decouples confidence from proportion — a document can be confidently
30% generated. `Abstain` fires on: text under 100 words, no calibration for
the language, a calibration set too small to certify the cap, or detected
Unicode tampering. Each reason is machine-readable.

### Normalization defense

Homoglyph substitution and zero-width insertion are the cheapest evasions in
the literature. Measured:

| Attack | Baseline | Undefended | Defended | Residual |
|---|---:|---:|---:|---:|
| homoglyph | 0.794 | 0.665 | 0.794 | **0.000** |
| zero_width | 0.794 | 0.787 | 0.794 | **0.000** |
| whitespace | 0.794 | 0.793 | 0.794 | **0.000** |

Fully neutralized — and the tampering itself becomes evidence, triggering
abstention rather than being silently repaired.

### Span attribution

Overlapping 120-word windows, each sentence inheriting the mean of the windows
covering it, so evidence per decision stays above the reliability floor even
though output is per-sentence. Plus change-point detection for the common
one-seam hybrid document.

> **Resolution is bounded by window width (~120 words) and the proportion
> estimate is biased toward the majority class.** Shrinking the window does
> not help — below the evidence floor the detector's scores drift upward, so
> narrower windows trade smearing for false positives and the estimate gets
> *worse*. This is measured, not assumed.

### Provenance asymmetry

> **Verified AI provenance raises confidence. Absent provenance changes
> nothing.**

Enforced in code — `combine()` is monotone and cannot lower a score, verified
for every status by a parametrized test. Treating "no watermark" as evidence
of human authorship would let every unwatermarked model (nearly all of them)
launder output through the detector, and would make stripping a watermark an
exculpatory act.

No signature verification is implemented; C2PA needs COSE checking against a
trust list and watermarks need the generator's key. This module consumes an
already-verified result and enforces how it may be used.

---

## Commands

| Command | Purpose |
|---|---|
| `python analyze.py` | **Everything at once** for a single input |
| `ai-text-eval analyze` | Full pipeline → ternary verdict |
| `ai-text-eval calibrate` | Fit conformal thresholds at an FPR policy cap |
| `ai-text-eval spans` | Sentence-level scores + AI-proportion estimate |
| `ai-text-eval robustness` | Score degradation under adversarial attack |
| `ai-text-eval fairness` | Per-subgroup false-positive rates |
| `ai-text-eval benchmark` | Full metric suite on a labeled corpus |
| `ai-text-eval compare` | Original vs rephrased — the evasion experiment |
| `ai-text-eval score` | Quick per-detector scores |

### Fairness reporting

An aggregate FPR that meets the cap while one subgroup sits far above it is a
failing detector, and only the breakdown shows it:

```console
$ ai-text-eval fairness --human human.jsonl --group-by native_language

group                    n  flagged      FPR             95% CI
non_native             100       20    0.200     [0.133, 0.290]  OVER CAP
native                 100        0    0.000     [0.000, 0.037]

Largest between-group gap: 0.200   Policy cap: 0.500%
```

Wilson intervals, because these rates are small and a normal approximation
produces negative lower bounds.

### Metrics

- **AUROC** with correct tie handling (Mann-Whitney identity)
- **TPR @ 1% and 5% FPR**, with the corpus's FPR *resolution* reported — a
  budget finer than `1/n_negatives` is not measurable, and those cells are
  starred rather than printed as two identical numbers under different names
- **Domain-adjusted TPR** (RAID's headline metric), macro-averaged over domains
- **Brier + ECE** — is the score a probability or just a ranking?
- **Per-subgroup FPR** with Wilson intervals
- **Stratified bootstrap CIs** on AUROC and TPR@5%FPR
- **Evasion rate** under paraphrase, returning `n/a` rather than `0.0` when
  nothing was flagged — `0.0` is the *best* value on a "higher = weaker" scale
  and would rank a detector that never fires as the most robust

---

## Data format

JSONL, one record per line:

```json
{"text": "...", "label": 1, "source": "gpt-4o", "meta": {"domain": "essay"}}
```

`label`: `1` = AI-generated, `0` = human-written.

Paraphrase-attack pairs — both sides are AI-generated, so the rewrite does not
change ground truth:

```json
{"original": "...", "rephrased": "...", "meta": {"technique": "voice_shift"}}
```

### Real corpora

The bundled 36-document demo corpus is a **smoke test, not a benchmark**. For
real evaluation:

| Dataset | Notes |
|---|---|
| **RAID** | 600k+ samples, 11 generators, 8 domains, 11 adversarial attacks. The reference. |
| **MIRAGE** | 10 corpora, 5 domains, 17 mostly-proprietary LLMs |
| **M4** | multi-generator, multi-domain, multilingual |
| **HC3** | older and easier; models have moved on |

---

## FAQ

<details open>
<summary><b>How accurate are AI detectors, really?</b></summary>

<br>

On long documents, in-domain, against models they were trained on: very good —
the best supervised systems report ~99% true-positive rate at 5% false-positive
rate. Outside those conditions the number collapses. Paraphrasing, short text,
a newer model, or a non-native-English writer each move it dramatically. Any
single accuracy figure quoted without its conditions is close to meaningless,
which is why this framework reports confidence intervals and refuses to print
metrics its corpus cannot support.

</details>

<details>
<summary><b>Can AI detectors be fooled?</b></summary>

<br>

Yes, reliably. Paraphrasing is the standard method and it works against every
detector measured here — in the bundled demo, 100% of flagged texts escaped
after rewriting. Published work drives several zero-shot detectors to ~0.001
TPR at 1% FPR with RL-optimized rewriting. Homoglyph and zero-width tricks also
work against detectors without a normalization stage; this one has one, and
neutralizes them completely.

Worth stating plainly: a paraphrase of AI text is still AI text. The score
drops; the ground truth doesn't change. That gap is the detector failing, not
the text becoming human.

</details>

<details>
<summary><b>Can it detect AI in a single sentence or a short paragraph?</b></summary>

<br>

No, and neither can anything else. A 15-word sentence carries 20–30 tokens of
evidence and the human/AI distributions overlap almost entirely at that length.
This tool abstains below 100 words rather than guessing. Commercial detectors
measurably lose accuracy below 50 words.

</details>

<details>
<summary><b>Is it safe to use an AI detector to accuse a student?</b></summary>

<br>

No. Detectors falsely flagged 61.3% of non-native-English TOEFL essays in
published research, with the effect replicated for neurodivergent writers and
AAVE speakers. Dozens of universities have disabled these tools and students
have sued over false accusations. Vendors' own documentation now says the
output should not be the sole basis for action.

This framework is built for *measuring detectors*, not for judging people. Its
fairness command exists specifically to surface the subgroup disparities that
aggregate accuracy hides.

</details>

<details>
<summary><b>How is this different from GPTZero, Turnitin, or Originality.ai?</b></summary>

<br>

Those are detection *products*. This is an evaluation *framework* — its job is
to tell you how well any detector works, including its own, and to make
overclaiming structurally difficult. It's open source, runs locally with no
API calls or data leaving your machine, and reports the things products
generally don't: confidence intervals, per-subgroup false-positive rates,
evasion robustness, and an explicit refusal when calibration is insufficient.

If you need a production detector, plug one into `TransformerClassifier` and
use this to evaluate it honestly.

</details>

<details>
<summary><b>Does it work on languages other than English?</b></summary>

<br>

Only if you calibrate it per language, which it supports and enforces —
score distributions differ sharply, so an English threshold carries no
guarantee elsewhere. The engine abstains for any language it lacks a
calibration for rather than silently reusing one. English detection far
outpaces everything else in the literature; code-mixed text like Hinglish is
badly under-resourced.

</details>

<details>
<summary><b>Does it need a GPU or an API key?</b></summary>

<br>

Neither. The core package has zero dependencies and runs on CPU. The optional
perplexity and Binoculars detectors need `torch` and `transformers`
(`pip install -e '.[perplexity]'`) and will use a GPU if one is present.
Nothing is sent anywhere.

</details>

---

## Known limits

Stated plainly, because an eval tool that hides its own limits is the problem
it claims to solve.

- **The demo corpus is a toy.** 36 documents, and its "human" half is pre-1930
  public-domain prose against modern assistant output — era and genre are
  confounded with authorship. Demo AUROCs are not detector performance, and
  the tool prints that caveat next to its own numbers and inside its JSON.
- **The bundled supervised layer is a reference implementation**, not a
  competitor to a real one. It is regularized logistic regression over
  interpretable features. `TransformerClassifier` is the integration point for
  a production model. Benchmark with `cross_val_scores`, never in-sample.
- **Span resolution is ~120 words** and the AI-proportion estimate is biased
  toward the majority class.
- **Thresholds do not transfer across languages.** Score distributions differ
  sharply, so calibrations are stored per language and the engine abstains for
  any language it lacks one for. English detection far outpaces everything
  else; code-mixed (Hinglish) is badly under-resourced.
- **Paraphrase and humanizer attacks are not simulated.** They need a
  generator. The attack suite covers mechanical perturbations only; supply
  real pairs to the evasion benchmark rather than trusting a stand-in.

<details>
<summary><b>The framework was adversarially reviewed — here's what it got wrong</b></summary>

<br>

v1 was put through a multi-lens review with every finding verified by
reproduction. All are fixed with named regression tests in
`tests/test_review_regressions.py`.

| Defect | Why it mattered |
|---|---|
| Multiword markers matched as bare substrings | `"here are some"` fired inside *"**W**here are some of the best places…"* |
| `"no"`, `"co"`, `"vs"` treated as abbreviations | *"The answer was no. Then…"* lost a sentence boundary, corrupting burstiness |
| Bulleted text collapsed into one "sentence" | broke the statistics on the format LLM output most often takes |
| Bootstrap upper percentile off by one | every reported CI tilted upward |
| ECE bin index clamped only at the top | a negative score wrapped into the *top* bin |
| `evasion_rate` returned `0.0` for 0/0 | a detector that never fires ranked as most robust |
| Binoculars cross-perplexity transposed | cross-entropy isn't symmetric; it computed a quantity the paper never defines |
| `TPR@1%FPR` printed as its own column | unmeasurable at n=18 |

The last one is the pattern to internalize: nothing crashed, no test failed,
and the table looked authoritative — it simply printed a number the corpus
could not support. That is the characteristic failure of eval code, and it is
why this framework reports intervals, stars unmeasurable cells, refuses short
text, and prints its own confounds next to its own results.

v2's change-point detector had the same class of bug, caught the same way: an
unweighted max-t scan reported a seam at sentence 3 of a document whose seam
was at 11, because lopping off one extreme sentence maximizes a raw mean
difference. Fixed with segment-size weighting.

</details>

---

## Project layout

```
analyze.py                  all-in-one report for a single input
src/ai_text_eval/
├── conformal.py            split + multiscale conformal FPR control
├── verdict.py              ternary Human/AI/Mixed + abstention policy
├── engine.py               the v2 pipeline
├── normalize.py            homoglyph / zero-width defense
├── attacks.py              adversarial transforms for robustness eval
├── spans.py                windowed sentence scoring + change-point detection
├── provenance.py           watermark / C2PA, with enforced asymmetry
├── metrics.py              AUROC, TPR@FPR, domain-adjusted TPR, subgroup FPR
├── evasion.py              paraphrase-attack accounting
├── detectors/
│   ├── supervised.py       primary layer + cross_val_scores + integration point
│   ├── stylometry.py       phrases.py  perplexity.py  binoculars.py  ensemble.py
├── dataset.py  report.py  cli.py  text_features.py
└── data/                   demo corpora (shipped with the package)
tests/                      194 tests
```

## Development

```bash
pip install -e '.[dev]'
pytest
```

## Regulation

EU AI Act Article 50 machine-readable marking obligations became applicable
2 August 2026 (transition to 2 December 2026 for pre-existing systems);
California SB 942 imposes parallel disclosure requirements; C2PA v2.3 extended
Content Credentials to unstructured text. `provenance.py` is the integration
point. Text provenance is structurally weaker than for images — text is
trivially copy-pasteable and platforms strip metadata — so absence of a
credential remains uninformative.

---

## Intended use

This is a **measurement** tool. It scores text, benchmarks detectors, and
quantifies robustness.

> Detector scores are evidence about a *distribution*, not proof about a
> *document*. Given documented false-positive rates against non-native
> English writers, neurodivergent writers, and AAVE speakers — and the
> resulting litigation — **no score from this or any detector should be the
> sole basis for an accusation against an individual.**

## License

[MIT](LICENSE)
