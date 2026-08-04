# Contributing

Thanks for considering a contribution. This project has an unusual bar in one
respect, so it's worth stating up front.

## The one rule that matters

**An eval tool that is quietly wrong is worse than no tool**, because its
output looks identical either way. The characteristic failure here is not a
crash — it's printing an authoritative-looking number the data cannot support.

So any change that touches scoring, metrics, or thresholds needs a test that
would fail if the number were wrong, not just a test that the code runs. See
`tests/test_review_regressions.py` for the shape: each test names the specific
wrong answer it locks out.

Examples of the failure mode this project has already shipped and fixed:

- `TPR@1%FPR` printed as its own column when 18 negatives can only resolve
  1/18 = 5.6% — two identical numbers under different names.
- A bootstrap percentile off by one, tilting every confidence interval upward.
- `evasion_rate` returning `0.0` for 0/0, ranking a detector that never fires
  as the most robust.

None of those crashed. All of them lied.

## Getting set up

```bash
git clone https://github.com/YashToOp/Eval-AI.git
cd Eval-AI
pip install -e '.[dev]'
pytest
```

The core package has no dependencies. `pip install -e '.[perplexity]'` adds
torch and transformers for the model-based detectors.

## Before opening a PR

```bash
pytest                    # all 194 must pass
python analyze.py --file some_sample.txt    # smoke-test the runner
```

CI runs the suite on Python 3.10–3.13 plus smoke tests for the CLI.

## What's most useful

**High value:**

- **Real corpora.** The bundled demo is 36 documents and its human half is
  pre-1930 public-domain prose — era is confounded with authorship. Loaders
  for RAID, MIRAGE, M4, or HC3 would be worth more than any new detector.
- **A real supervised detector.** `TransformerClassifier` in
  `detectors/supervised.py` is an integration point with no implementation.
  The bundled logistic regression is a reference, not a competitor.
- **Non-English calibration.** Thresholds do not transfer across languages.
  Hinglish and other code-mixed cases are badly under-resourced.
- **Newer zero-shot methods** — Glimpse, Lastde++, RepreGuard, TOCSIN — as
  detectors implementing the `Detector` interface.

**Please don't:**

- Add a detector without reporting its performance *with confidence
  intervals*, on data it was not tuned on.
- Weaken or remove an abstention path to make the tool "more decisive."
  Abstention is the feature.
- Add attacks that are primarily useful for evading detection rather than for
  measuring robustness. The attack suite exists to test defenses; that's why
  it stops at mechanical perturbations and does not implement paraphrase
  evasion.

## Reporting a bug

Include the input that triggers it if you can share it. For a wrong-number bug
— the important kind — say what the number was and what you expected, with the
reasoning. "The AUROC looks high" is hard to act on; "AUROC 0.99 on n=12 with
no CI reported" is a bug report.

## Code style

Match the surrounding code. Comments explain *why*, especially why a
statistical choice was made — most of this codebase's subtleties are one line
of arithmetic with a paragraph of reasoning behind them.
