# ai-text-eval v2

An evaluation framework for **AI-generated-text detection**. It implements the
signal families real detectors use, benchmarks them with the metrics the
research literature reports, and — the part most tools skip — controls its own
false-positive rate with a distribution-free guarantee and refuses to answer
when it cannot.

```
pip install -e .

ai-text-eval calibrate --human human.jsonl --fpr-cap 0.005 --out cal.json
ai-text-eval analyze --calibration cal.json --file draft.txt
ai-text-eval fairness --human human.jsonl --group-by native_language
ai-text-eval robustness
ai-text-eval benchmark --human h.jsonl --ai a.jsonl --pairs pairs.jsonl
```

Two claims the framework is built to demonstrate honestly:

> **Detecting whether a single sentence was written by AI is not a solved
> problem — it is close to an impossible one.**
>
> **The number that matters is not accuracy. It is the false-positive rate,
> per subgroup, at a threshold you can actually certify.**

---

## What changed in v2

v1 was a zero-shot ensemble with hand-set weights and a threshold of 0.5.
The 2025-26 literature makes that architecture obsolete. v2 rebuilds around
five findings:

| Finding | v1 | v2 |
|---|---|---|
| Supervised, adversarially-trained classifiers dominate at low FPR; only one system met a strict FPR ≤ 0.5% cap in independent audit | zero-shot ensemble, equal-ish weights | **supervised primary** (60%), zero-shot demoted to corroboration and OOD tripwires |
| Thresholds must come from an FPR policy cap, not accuracy | fixed 0.5 | **split + multiscale conformal prediction**, length-dependent, with a finite-sample guarantee |
| Documents are increasingly hybrid; binary output is the wrong shape | Human/AI score | **ternary Human/AI/Mixed** + span-level attribution + change-point detection |
| Abstention is a required output, not a disclaimer | soft "low confidence" note | **`ABSTAIN` is a first-class label** with machine-readable reasons |
| Homoglyph/invisible-character attacks are the cheapest evasion | none | **normalization runs before scoring**, and tampering itself triggers abstention |

Plus: per-subgroup FPR reporting, provenance handling with enforced asymmetry,
an adversarial attack suite, per-language calibration, and RAID's
domain-adjusted TPR metric.

---

## 1. How detectors work

Four signal families. All four are implemented here.

**(a) Perplexity / likelihood** — `detectors/perplexity.py`. LLMs decode from
the high-probability region of their own distribution, so their output looks
unsurprising to any similar model. Humans alternate predictable and genuinely
unexpected word choices, so their per-sentence surprisal variance is higher.

**(b) Cross-model / curvature** — `detectors/binoculars.py`. Binoculars
computes the ratio of an observer model's perplexity to the observer-performer
cross-perplexity. Machine text sits where two models *agree*; human text
surprises both in uncorrelated ways. The normalization is why it beats raw
perplexity: it cancels "this passage was inherently predictable" (a recipe,
legal boilerplate), which is what makes raw-perplexity detectors false-accuse.
Canonical pair is Falcon-7B / Falcon-7B-Instruct; instruction-tuning the
performer measurably helps.

**(c) Stylometry** — `detectors/stylometry.py`. Sentence-length burstiness,
lexical diversity, punctuation profile. Weak individually, cheap, interpretable.

**(d) Lexical forensics** — `detectors/phrases.py`. The measurable post-2023
frequency spikes: *delve*, *tapestry*, *underscores*, *plays a crucial role*.
Catches unedited output instantly; misses anything edited for ten minutes.

**Successors worth knowing** (not implemented — most need model access this
repo doesn't ship): Glimpse scores through proprietary APIs using only top-K
logprobs; Lastde/Lastde++ treat the token-probability sequence as a time
series; RepreGuard uses surrogate hidden states; TOCSIN adds token cohesiveness
as a plug-in to any base detector; WaveDetect applies wavelet transforms.
Treat their headline numbers with care — several drop sharply when
re-evaluated on RAID rather than their authors' own suites.

**Not signal families:** watermarking (needs generator cooperation; SynthID-Text
is spoofable and scrubbable) and trained classifiers used naively
(strong in-distribution, brittle across model generations).

---

## 2. The ceiling

Best case, on the authors' own favorable splits, on documents of several
hundred words:

| System | Reported | Conditions |
|---|---|---|
| Supervised commercial (Pangram-class) | ~99% TPR @ 5% FPR clean, ~97.7% adversarial | RAID-based shared task, blinded |
| Binoculars | ~79% aggregate TPR @ 5% FPR | RAID non-adversarial split |
| Glimpse / Lastde / RepreGuard | 0.95 / 0.959 / 0.949 AUROC | authors' own benchmarks; Glimpse drops to ~0.76 on a RAID subset |
| Watermark detection | p < 1e-6 | only if the generator watermarked |

And under realistic conditions:

| Condition | What happens |
|---|---|
| **Paraphrased / humanized** | Detection becomes probabilistic. Independent audit found one detector's false-negative rate rose to ~50%+ on humanized text while another stayed robust. RL-based attacks (StealthRL) drive several zero-shot detectors to ~0.001 TPR@1%FPR. |
| **Base (non-instruction-tuned) models** | Judged **96.7%** and **98.8% human** by two leading commercial detectors. Detectors track instruction-tuning artifacts, not an invariant machine signature. |
| **Short text (< ~100 words)** | All three major commercial tools lost accuracy below 50 words. |
| **Non-native English** | 61.3% of TOEFL essays falsely flagged in the original study; replicated and extended to neurodivergent writers, AAVE, and formulaic genres. 20% of Black teens report falsely-flagged schoolwork versus 7% of white teens. |
| **Each new frontier generation** | Somewhat harder than its predecessor. Claude-family text is repeatedly reported as the hardest major family. |

The theory has settled into a nuanced middle: detection degrades as machine and
human distributions converge in total variation (Sadasivan), but remains
possible with more samples (Chakraborty). Empirically, the achievable
*operating point* keeps sliding.

### A single sentence?

**No.** A 15-word sentence carries 20–30 tokens of evidence; the class
distributions overlap almost entirely. v2 abstains below 100 words:

```
$ ai-text-eval analyze "The system leverages advanced algorithms to optimize performance."
VERDICT: ABSTAIN — text too short
```

Any tool returning a confident percentage for one sentence is inventing it.

---

## 3. The v2 pipeline

```
normalize → score (primary + corroborators) → OOD check
          → span analysis → conformal threshold → ternary verdict
```

The order is load-bearing.

### Conformal false-positive control (`conformal.py`)

Calibrate on human text, get a distribution-free, finite-sample guarantee:

```
P(score(new human text) ≥ τ) ≤ α
```

for *any* detector and *any* score distribution. The property that matters most
is the one that refuses:

```
$ ai-text-eval calibrate --human human.jsonl --fpr-cap 0.005
Calibration texts:   18
Minimum required:    199

Status: NOT CERTIFIED — 181 more human texts are needed.
        The engine will abstain rather than flag, because no finite
        threshold can honor the requested cap at this sample size.
```

Split conformal takes the `ceil((n+1)(1-α))`-th order statistic, which exceeds
`n` unless `n + 1 ≥ 1/α`. So a **0.5% cap needs 199 human texts, a 1% cap needs
99, a 5% cap needs 19.** Below that, no threshold is honest, and the engine
returns +∞ instead of a number the data cannot support.

Multiscale conformal adds per-length-bucket thresholds, which is what makes
short-text handling principled rather than a hand-set word count.

### Ternary verdicts with mandatory abstention (`verdict.py`)

`Human | AI | Mixed | Abstain`. `Mixed` decouples confidence from proportion —
a document can be confidently 30% generated. `Abstain` fires on: text under 100
words, no calibration for the language, a calibration set too small to certify
the cap, or detected Unicode tampering. Each reason is machine-readable.

Given documented false-positive rates against non-native and neurodivergent
writers, and active litigation over wrongful accusations, refusing to answer is
the correct output for a large share of real inputs.

### Normalization defense (`normalize.py`)

Homoglyph substitution and zero-width insertion are the cheapest evasions in
the literature. Measured here:

```
attack          baseline  attacked  defended  undefended drop  residual
homoglyph          0.794     0.665     0.794            0.129    0.000
zero_width         0.794     0.787     0.794            0.006    0.000
whitespace         0.794     0.793     0.794            0.001    0.000
```

Fully neutralized — and the tampering itself becomes evidence, triggering
abstention rather than being silently repaired.

### Span analysis (`spans.py`)

Overlapping 120-word windows, with each sentence inheriting the mean of the
windows covering it, so evidence per decision stays above the reliability floor
even though output is per-sentence. Plus change-point detection for the common
one-seam hybrid document.

**Resolution is bounded by window width (~120 words), and the AI-proportion
estimate is biased toward the majority class.** Shrinking the window does not
help: below the evidence floor the detector's scores drift upward, so narrower
windows trade smearing for false positives and the estimate gets *worse*. This
is measured, not assumed — see `test_span_analysis_unreliable_on_short_documents`.

### Provenance asymmetry (`provenance.py`)

**Verified AI provenance raises confidence. Absent provenance changes nothing.**
Enforced in code — `combine()` is monotone and cannot lower a score, verified
for every status by a parametrized test. Treating "no watermark" as evidence of
human authorship would let every unwatermarked model (nearly all of them)
launder output through the detector, and would make stripping a watermark an
exculpatory act.

No signature verification is implemented; C2PA needs COSE checking against a
trust list and watermarks need the generator's key. This module consumes an
already-verified result and enforces how it may be used.

---

## 4. Fairness reporting

The documented harms are all false positives concentrated in identifiable
groups. An aggregate FPR that meets the cap while one subgroup sits far above
it is a failing detector, and only the breakdown shows it:

```
$ ai-text-eval fairness --human human.jsonl --group-by native_language

group                    n  flagged      FPR             95% CI
non_native             100       20    0.200     [0.133, 0.290]  OVER CAP
native                 100        0    0.000     [0.000, 0.037]

Largest between-group gap: 0.200   Policy cap: 0.500%
```

Wilson intervals, because these rates are small and a normal approximation
produces negative lower bounds.

---

## 5. Metrics

- **AUROC** with correct tie handling (Mann-Whitney identity).
- **TPR @ 1% and 5% FPR**, with the corpus's FPR *resolution* reported — a
  budget finer than `1/n_negatives` is not measurable, and the framework
  stars those cells rather than printing two identical numbers under
  different names.
- **Domain-adjusted TPR** (RAID's headline metric): macro-averaged over
  domains, so a corpus that is 80% news can't report a news-only detector as
  strong.
- **Brier + ECE** — is the score a probability or just a ranking?
- **Per-subgroup FPR** with Wilson intervals.
- **Stratified bootstrap CIs** on AUROC and TPR@5%FPR.
- **Evasion rate** under paraphrase, returning `None` (rendered `n/a`) rather
  than `0.0` when nothing was flagged — 0.0 is the *best* value on a
  "higher = weaker" scale and would rank a detector that never fires as the
  most robust.

---

## 6. Demo results, and why not to trust them

```
detector       AUROC            95% CI  TPR@5%FPR          (95% CI)  TPR@1%FPR
stylometry    0.7099    [0.519, 0.873]     0.000*    [0.000, 0.556]     0.000*
phrases       0.7870    [0.620, 0.926]     0.611*    [0.389, 0.833]     0.611*
ensemble      0.7685    [0.590, 0.907]     0.667*    [0.444, 0.833]     0.667*
```

The tool prints these caveats itself, next to the table and inside the JSON
report — a bare list of AUROCs gets quoted onward; a list that names its own
confounds cannot be.

1. **n = 36.** The CIs are enormous; `stylometry`'s includes 0.52. The apparent
   0.000-vs-0.667 TPR gap has overlapping intervals.
2. **`TPR@1%FPR` is unmeasurable here** (starred). With 18 human texts the
   finest expressible FPR is 1/18 = 0.056.
3. **The human corpus is an era/genre confound** — pre-1930 public-domain prose
   versus modern assistant output. A detector separating those may be detecting
   centuries, not authors.
4. **The supervised layer is a reference implementation**, not a competitor to
   a real one. It is regularized logistic regression over interpretable
   features. Benchmark it with `cross_val_scores`, never in-sample.
5. **Calibration constants are in-sample.**

**The demo cannot certify a 0.5% FPR cap.** That is the framework working: 18
human texts is 181 short, and it says so.

### Getting real data

| Dataset | Notes |
|---|---|
| **RAID** | 600k+ samples, 11 generators, 8 domains, 11 adversarial attacks. The reference robustness benchmark. |
| **MIRAGE** | 10 corpora, 5 domains, 17 mostly-proprietary LLMs. |
| **M4** | multi-generator, multi-domain, multilingual. |
| **HC3** | older and easier; models have moved on. |

---

## 7. Multilingual

English detection far outpaces everything else, and thresholds do **not**
transfer: score distributions differ sharply by language, so an English
threshold applied to Hindi or Hinglish carries no guarantee. Calibrations are
therefore stored per language, and the engine abstains for any language it
lacks calibration for.

```
ai-text-eval calibrate --human hinglish_human.jsonl --language hi --out cal.json
```

Code-mixed (Hinglish) detection is badly under-resourced; the substrate is
multilingual encoders (MuRIL, XLM-R, IndicBERT) and datasets like PHINC and
L3Cube.

---

## 8. Regulation

EU AI Act Article 50 machine-readable marking obligations became applicable
2 August 2026 (transition to 2 December 2026 for pre-existing systems);
California SB 942 imposes parallel disclosure requirements; C2PA v2.3 extended
Content Credentials to unstructured text. `provenance.py` is the integration
point. Note that text provenance is structurally weaker than for images —
text is trivially copy-pasteable and platforms strip metadata — so absence of
a credential remains uninformative.

---

## 9. The framework was adversarially reviewed

v1 was put through a multi-lens review with each finding verified by
reproduction. Every defect is fixed with a named regression test in
`tests/test_review_regressions.py`. The instructive ones:

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

The last one is the pattern to internalize: nothing crashed, no test failed, and
the table looked authoritative — it simply printed a number the corpus could not
support. That is the characteristic failure of eval code, and it is why this
framework reports intervals, stars unmeasurable cells, refuses short text, and
prints its own confounds next to its own results.

v2's change-point detector had the same class of bug, caught the same way: the
unweighted max-t scan reported a seam at sentence 3 of a document whose seam was
at 11, because lopping off one extreme sentence maximizes a raw mean difference.
Fixed with the standard segment-size weighting.

---

## 10. Layout

```
src/ai_text_eval/
  conformal.py              split + multiscale conformal FPR control
  verdict.py                ternary Human/AI/Mixed + abstention policy
  engine.py                 the v2 pipeline
  normalize.py              homoglyph / zero-width defense
  attacks.py                adversarial transforms for robustness eval
  spans.py                  windowed sentence scoring + change-point detection
  provenance.py             watermark / C2PA, with enforced asymmetry
  metrics.py                AUROC, TPR@FPR, domain-adjusted TPR, subgroup FPR, ECE
  evasion.py                paraphrase-attack accounting
  detectors/
    supervised.py           primary layer + cross_val_scores + integration point
    stylometry.py  phrases.py  perplexity.py  binoculars.py  ensemble.py
  dataset.py  report.py  cli.py  text_features.py
  data/                     demo corpora (shipped with the package)
tests/                      194 tests
```

Core package has **no dependencies**; only the two model-based detectors need
the `perplexity` extra.

---

## 11. Intended use

This is a **measurement** tool. It scores text, benchmarks detectors, and
quantifies robustness. The paraphrase module consumes pairs you supply; the
attack suite exists to test defenses and is limited to mechanical
perturbations for that reason.

Detector scores are evidence about a *distribution*, not proof about a
*document*. Given the documented false-positive rates against non-native
English writers, neurodivergent writers, and AAVE speakers — and the
resulting litigation — no score from this or any detector should be the sole
basis for an accusation against an individual. Institutions have been
disabling these tools for exactly this reason, and vendors' own documentation
now says the same.
