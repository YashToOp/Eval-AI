# Demo corpora

These files exist to **smoke-test the harness and demonstrate the metrics**.
They are not a research benchmark, and numbers computed on them should not be
cited as detector performance. Three reasons, all of which matter:

### 1. The human corpus is an era/genre confound

`demo_human.jsonl` is 18 excerpts of public-domain prose (Darwin, Austen,
Melville, Thoreau, Lincoln, Woolf, Crane, …), all pre-1930. `demo_ai.jsonl` is
18 modern assistant-written texts. A detector that separates them may be
detecting *19th-century literary register* rather than *human authorship*.

A valid human corpus is contemporary text in the **same domain and register** as
the AI text: modern blog posts vs AI blog posts, student essays vs AI essays.

The excerpts were transcribed from memory and may contain minor wording errors.
They are public domain, but they are not authoritative editions.

### 2. It is tiny

18 + 18 = 36 documents. Every AUROC from this corpus carries a bootstrap CI
roughly ±0.15 wide. That is why the framework reports CIs at all.

### 3. The calibration constants saw this data

The logistic midpoints in `stylometry.py` and the rate scale in `phrases.py`
were set partly by looking at these texts. Benchmarking on them is in-sample
evaluation, which is optimistic by construction.

## Hard negatives

Six of the 18 AI texts are marked `"style": "plain_hard_negative"` — written
deliberately without AI-isms (plain informational prose, a short review, a
recipe, a status email). They are there so `phrases` cannot get an easy win, and
they are the cases where it fails. Keep this ratio or raise it when you extend
the corpus; a corpus of only florid LLM prose makes every detector look good.

## `demo_evasion_pairs.jsonl`

Twelve `(original, rephrased)` pairs. **Both sides are AI-generated** — the
rewrite changes the style, not the ground-truth label, so any score drop is
detector failure by definition.

The rewrites apply the techniques that are documented to defeat detectors:
voice shift to first person, concrete specifics and anecdote, varied sentence
length including fragments, hedging and mild negativity, and removal of the
marker vocabulary. This is the same experimental design as the paraphrase-attack
literature (Krishna et al. 2023, DIPPER), scaled down.

Note the design honestly: these pairs were written to be effective attacks, so
a high evasion rate is partly built in. The result is still meaningful because
the published literature finds the same thing against much stronger detectors —
but do not read "100% evasion" here as a measurement of *how easy* evasion is in
general. Use RAID's adversarial splits for that.

## Getting real data

| Dataset | Scale | Notes |
|---|---|---|
| RAID (Dugan et al. 2024) | 6M+ generations | 11 models × 11 domains × 11 attacks. Best available. |
| M4 | multi-generator | multi-domain, multilingual |
| HC3 | ~40k pairs | human vs ChatGPT QA; older, easier |
| GPT-Wiki-Intro | ~150k | paired human/AI Wikipedia intros |

Convert to JSONL with `{"text": ..., "label": 0|1}` and run:

```
ai-text-eval benchmark --human human.jsonl --ai ai.jsonl --pairs pairs.jsonl
```
