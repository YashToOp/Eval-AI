# GAUNTLET infrastructure — architecture

Governing document: [`gauntlet-v1.0-spec.txt`](gauntlet-v1.0-spec.txt). Where
this code and the specification disagree, **the specification wins and the
code is the bug.**

## Layout

```
docs/         the specification and this note
benchmark/    benchmark DEFINITION data, versioned separately from code
              categories.json      99 categories (§3)
              failure_modes.json   FM-01..FM-16 (§6.2), transcribed
              axes.json            axis vocabularies (§2.1)
corpus/       the DATA
              manifest.json        §5.4
              samples/track_*.jsonl
              generation_configs/  §4.4 prompts, parameters, raw responses
              provenance/          §4.2 edit sessions, attestations
regression/   §8 permanent regression entries
harness/      run_benchmark.py — executable entry point
src/ai_text_eval/gauntlet/
              spec.py       constants that are law, each citing its section
              findings.py   Finding/Report/Severity, shared by every checker
              sample.py     the §5.2 record
              loader.py     corpus + manifest loading, split discipline
              registry.py   field registry (R-01, CAS §4.1)
              lifecycle.py  identifier registry + state machine (R-02, CAS §2)
              ledger.py     append-only decision ledger (R-03, CAS §14.2)
              validate.py   validator layers + cross-field rules (R-04)
              runner.py     evaluation skeleton
```

Milestone 2 Phase A (R-01…R-04) is documented separately in
[`MILESTONE-2-PHASE-A.md`](MILESTONE-2-PHASE-A.md).

## Layering

```
spec.py  ──▶  sample.py  ──▶  loader.py  ──▶  validate.py
                                   │
                                   └──────▶  runner.py  ──▶  a detector
```

Dependencies point one way. `runner.py` depends on a `.score(text)` contract
and imports no detector internals, so the benchmark cannot accidentally
co-evolve with the thing it measures.

## The four validator layers

| Layer | Enforces | Answers |
|---|---|---|
| `validate_sample` | §4.7, §5.2 | Is this record well-formed and self-consistent? |
| `validate_manifest` | §5.4, §2.4 | Is the manifest complete and do checksums hold? |
| `validate_splits` | §2.3, §4.2 | Are ids unique and is provenance admissible for the split? |
| `validate_release` | §9.1 | Is the corpus releasable? |

Every finding carries the section it enforces, so a reader can check the call
against the specification rather than against the author's memory of it.

## Design decisions

**Validators report; they never repair.** A validator that quietly widened a
bucket boundary or filled a missing tier would be altering ground truth to
make a gate pass. Findings are returned, never fixed.

**Benchmark definition is data, not code.** The category registry, failure-mode
map, and axis vocabularies live in `/benchmark` as JSON because §7.4 and §10.4
require exactly this kind of content to be versioned and refreshed. P6 warns
against baking transient stereotypes into assumptions; a hardcoded registry
would be the same mistake one level up.

**The failure-mode map is transcribed, not completed.** §6.2 covers 66 of 99
categories. The remaining 33 are reported by the release validator under
§9.1(h) rather than papered over with invented mappings.

**Unimplemented tasks raise.** T3 and T4 raise `NotImplementedError`. A runner
that returned a plausible number for an unimplemented task would manufacture
evidence — worse than an obvious gap.

**Split discipline is structural.** `for_reporting("dev")` and
`for_threshold_selection("test")` both raise. §9.4 violations are silent and
invalidate everything downstream, so they are enforced at the API rather than
left to reviewer vigilance.

**Worst-cell is the headline; the macro mean is a named secondary.**
`TaskResult.worst_cell()` is primary per P4 and §9.3(a). `macro_mean()` exists
and is labelled a diagnostic. No API returns a corpus-level scalar without its
per-cell table.

**Unmeasurable cells are excluded from the headline, not scored zero.** A cell
of 12 negatives cannot express FPR = 0.005. Counting that as a failure would
understate a detector for the wrong reason; it stays in the table, flagged.

**The corpus ships empty.** Authoring samples to make validators pass would be
fabricating benchmark data. An empty corpus that the release validator
correctly rejects is more useful than a populated one that lies.

## Extension points

| To add | Where |
|---|---|
| A new evaluation task | `TASK_MEMBERSHIP` in `runner.py`; T3/T4 replace their `NotImplementedError` |
| A new detector under test | Anything with `.score(text)`; no harness change |
| A new category | `benchmark/categories.json` + an entry in `failure_modes.json` per §9.1(h) |
| A new tell list | `benchmark/tells-YYYY-MM.json`, referenced by sample `transforms` (§7.4) |
| Corpus growth phase | `CELL_TARGETS` in `spec.py` already carries v1.0/v1.1/v2.0 |
| Metadata schema v2 | Bump `METADATA_SCHEMA_VERSION`; unknown fields already survive round-trip |
| Calibration/ECE reporting | New module beside `runner.py`; §9.3(b) targets are already in `spec.py` |

## Not yet built

Milestone 2 remainder: calibration and confidence reporting (§9.3(b), (c)),
leakage index (§9.3(e)), G9 held-out delta (§9.3(f)), transform curves
(§9.3(g)), regression integration into CI tiers (§8.4), golden-output drift
alarms (§8.7).

Milestone 3: fixture migration into DEV.
