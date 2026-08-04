# Launch checklist and announcement drafts

Everything here is a draft for **you** to post. Nothing in this file has been
published anywhere.

---

## Part 1 — Do these first (they matter more than any post)

A post that sends people to a repo that looks abandoned converts nobody. These
take ten minutes and they gate everything else.

### 1. Make the repository public

Settings → General → Danger Zone → Change visibility → Public.

Nothing below matters while the repo is private.

### 2. Rename the default branch to `main`

The default branch is currently `claude/ai-detection-evals-btefos`. That name
reads as an abandoned scratch branch, and it's the first thing a visitor sees.

Settings → Branches → rename to `main`. GitHub redirects the old name and
updates open PRs automatically.

### 3. Set the description and topics

Repo home → the ⚙️ gear next to "About".

**Description** (paste exactly):

```
Open-source AI text detector & evaluation framework. Detect ChatGPT/LLM-generated text, benchmark detectors, and certify your false-positive rate with conformal prediction. Python, zero dependencies.
```

**Topics** (GitHub's main discovery surface — paste one at a time):

```
ai-detection
ai-text-detector
ai-content-detector
chatgpt-detector
gpt-detector
llm
machine-generated-text
ai-generated-text
nlp
text-classification
conformal-prediction
machine-learning
python
evaluation-framework
benchmark
adversarial-robustness
ai-safety
ai-ethics
stylometry
watermarking
```

Also tick **Releases** and **Packages** in that panel so the sidebar isn't
empty.

### 4. Cut a release

Releases → Draft a new release → tag `v2.0.0` → title
`v2.0.0 — conformal false-positive control`. Releases appear in GitHub's feed
for anyone watching related topics, and they make the repo look maintained.

Use the "What's new" text from Part 2 as the release body.

### 5. Add a social preview image

Settings → General → Social preview. Without one, every link shared to
X/LinkedIn/Slack renders as a grey placeholder, which roughly halves
click-through. A screenshot of `python analyze.py` output works fine — the
terminal output is the most compelling thing this project has.

### 6. Optional: publish to PyPI

`pyproject.toml` now carries keywords, classifiers, and project URLs. If you
want `pip install ai-text-eval` to work:

```bash
pip install build twine
python -m build
twine upload dist/*
```

Check the name is free on PyPI first. This is the single highest-leverage
distribution step for a Python library.

---

## Part 2 — Announcement drafts

Adapt the voice; these are starting points, not scripts. **Do not oversell
the numbers** — the demo corpus is 36 documents and the README says so. If a
post claims more than the repo delivers, the first commenter will find it and
that's the whole thread.

### Hacker News (Show HN)

Title (80 char limit, no emoji, no hype — HN punishes both):

```
Show HN: An AI-text-detector eval framework that abstains when it can't be sure
```

Body:

> I kept running into AI detectors that hand you a confident percentage for a
> single sentence, which is a number nobody can support — at that length the
> human and machine distributions overlap almost completely.
>
> So I built the eval framework instead of another detector. It implements the
> four signal families real detectors use (perplexity, Binoculars-style
> cross-perplexity, stylometry, lexical forensics), then adds the parts that
> usually get skipped:
>
> - **Conformal false-positive control.** Calibrate on human text and you get a
>   distribution-free guarantee. The useful consequence is that it refuses: a
>   0.5% FPR cap provably needs ≥199 human calibration texts, so with 18 it
>   returns an infinite threshold and flags nothing instead of inventing a
>   number.
> - **Ternary Human/AI/Mixed + abstention** as a first-class output.
> - **Per-subgroup false-positive rates.** Every documented harm from this
>   technology is a false positive concentrated in a group — non-native English
>   writers, neurodivergent writers, AAVE speakers. Aggregate accuracy hides
>   that; the breakdown doesn't.
> - **Unicode homoglyph defense**, measured: attacks drop the score 0.129
>   undefended, 0.000 after normalization.
>
> Honest bit: on the bundled 36-document demo corpus, 100% of flagged AI texts
> escaped detection after paraphrasing. That's the real state of the art, not a
> flaw in this implementation. I also ran an adversarial review over my own
> code and it found a dozen wrong-number bugs — a bootstrap CI off by one, an
> ECE bin index that wrapped negative scores into the top bin, a TPR@1%FPR
> column that was mathematically identical to the 5% one at n=18. All fixed
> with named regression tests; the README lists them.
>
> Zero dependencies, MIT. [link]

**HN notes:** post Tue–Thu, ~8–10am ET. Reply to every comment in the first two
hours. Never ask for upvotes. If someone finds a bug, thank them and fix it in
the thread — that's what does well there.

### Reddit — r/MachineLearning

Use the `[P]` project tag.

```
[P] ai-text-eval: an AI-text-detection framework with conformal FPR control and mandatory abstention
```

> Most detection work optimizes AUROC. For anything with consequences that's
> the wrong target — the cost of a false accusation dwarfs the cost of a miss,
> and every documented harm here is a false positive concentrated in an
> identifiable group of writers.
>
> So this framework is built around FPR instead:
>
> - Split + multiscale conformal prediction for distribution-free FPR control,
>   with the sample-size requirement made explicit (`n + 1 ≥ 1/α`, so a 0.5%
>   cap needs 199 human calibration texts and it refuses below that)
> - Supervised-primary / zero-shot-corroborating ensemble, with detector
>   disagreement used as an OOD tripwire
> - Span-level attribution + change-point detection for hybrid documents
> - Per-subgroup FPR with Wilson intervals
> - RAID-style domain-adjusted TPR@FPR
> - Adversarial robustness suite, with the normalization defense measured
>   against it
>
> Implements perplexity, Binoculars-style cross-perplexity, stylometry, and
> lexical forensics. Pure Python core, no dependencies.
>
> Caveats up front: the bundled corpus is a 36-document toy with an era
> confound, and the bundled supervised layer is logistic regression — a
> reference implementation of the architectural slot, not a competitor to a
> production classifier. Loaders for RAID/MIRAGE/M4 are the most useful thing
> anyone could contribute.
>
> MIT. [link]

Also consider: r/LanguageTechnology, r/Python (lead with the zero-dependency
angle), r/learnmachinelearning (lead with the conformal-prediction explainer).

**Reddit notes:** r/MachineLearning wants substance and hates marketing voice.
Lead with the method, put caveats early, and the thread goes well.

### X / Twitter thread

> 1/ Most AI detectors give you a confident % for a single sentence.
>
> At 15 words there are ~25 tokens of evidence and the human/AI distributions
> almost fully overlap. That number is invented.
>
> So I built the eval framework instead. Open source, MIT: [link]

> 2/ The core idea: stop optimizing accuracy, start controlling false
> positives.
>
> Conformal prediction gives a distribution-free guarantee — P(human text
> flagged) ≤ α, for any detector, no distributional assumptions.

> 3/ The useful consequence is that it *refuses*.
>
> A 0.5% FPR cap provably needs ≥199 human calibration texts. With 18 it
> returns an infinite threshold and flags nothing.
>
> Most tools would print a number anyway. That's the bug.

> 4/ Why FPR and not accuracy?
>
> Every documented harm from AI detection is a false positive concentrated in
> a group: non-native English writers (61.3% of TOEFL essays falsely flagged
> in one study), neurodivergent writers, AAVE speakers.
>
> Aggregate accuracy hides that.

> 5/ It also reports the uncomfortable result.
>
> On the demo corpus, 100% of flagged AI text escaped after paraphrasing.
>
> That's not a flaw in my implementation — it's the state of the art. A
> paraphrase of AI text is still AI text; the score drops, the truth doesn't.

> 6/ I ran an adversarial review over my own code. It found a dozen
> wrong-number bugs:
>
> · bootstrap CI off by one → every interval skewed up
> · ECE bin index wrapping negative scores into the top bin
> · TPR@1%FPR mathematically identical to TPR@5%FPR at n=18
>
> Nothing crashed. All of them lied.

> 7/ That's the characteristic failure of eval code, and why this one reports
> intervals, stars unmeasurable cells, refuses short text, and prints its own
> confounds next to its own results.
>
> Zero dependencies, MIT: [link]

### LinkedIn

> I built an open-source framework for evaluating AI-text detectors, and the
> most important thing it does is refuse to answer.
>
> Here's why that matters. Detectors have falsely flagged 61% of non-native
> English essays in published research. Similar effects are documented for
> neurodivergent writers and AAVE speakers. Dozens of universities have
> disabled these tools; students have sued over false accusations.
>
> Every one of those harms is a false positive. So optimizing for accuracy —
> what almost every detector reports — is optimizing the wrong thing.
>
> This framework uses conformal prediction to give a distribution-free
> guarantee on the false-positive rate, and it makes the sample-size
> requirement explicit: certifying a 0.5% FPR cap provably requires at least
> 199 human calibration documents. Below that it returns no threshold at all
> and abstains, rather than producing a number the data can't support.
>
> It also reports per-subgroup false-positive rates, because an aggregate rate
> that meets your policy while one group sits far above it is a failing system
> that looks like a passing one.
>
> Python, zero dependencies, MIT licensed. [link]
>
> #MachineLearning #NLP #AIEthics #OpenSource #ResponsibleAI

### Where else

- **Papers With Code / community leaderboards** — if you run it on RAID, submit
  the result. That gets it in front of exactly the right audience.
- **awesome-* lists** — `awesome-nlp`, `awesome-ai-safety`,
  `awesome-conformal-prediction`. A PR adding one line is normal and welcomed.
- **Hugging Face Space** — a demo box where people paste text is the single
  best conversion surface for a tool like this.
- **Relevant issue threads** — if someone is asking how to evaluate detector
  FPR, a genuinely useful reply mentioning the project is fine. Drive-by
  promotion is not.
- **dev.to / Hashnode** — a writeup of one narrow thing (the 199-documents
  result, or the adversarial review findings) tends to outperform a project
  announcement.

---

## Part 3 — What not to do

- **Don't claim accuracy numbers the demo corpus can't support.** 36 documents
  with an era confound. The README is explicit; a post that isn't will get
  taken apart, deservedly.
- **Don't market it as a way to catch students.** It's a measurement tool, it
  says so, and the fairness evidence points the other way.
- **Don't astroturf.** Sockpuppet upvotes and fake comments are the fastest way
  to get a project banned from HN and Reddit permanently.
- **Don't post everywhere the same day.** Stagger: HN first, then Reddit a few
  days later, then LinkedIn. Simultaneous cross-posting reads as spam and you
  only get one good shot per platform.
