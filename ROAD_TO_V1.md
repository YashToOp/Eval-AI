# Road to GAUNTLET v1.0

**Status:** planning document, produced at the pre-Phase-D specification
checkpoint. Not normative. Nothing here amends the Benchmark Specification
(BS) or the Corpus Authoring Specification (CAS); where it proposes a
decision, that decision requires governance ratification.

**Position as of this checkpoint.** The lifecycle is complete and correct from
IDEA to ACCEPTED, backed by 816 tests. The corpus is empty. Phases A–C built
an instrument for admitting material; no material has been admitted, and the
only corpus in the repository has been proven inadmissible (TD-B05). The
remaining distance to v1.0 is therefore not mostly engineering.

---

## 1. What GAUNTLET v1.0 is

A released, versioned, checksummed corpus of AI-text-detection benchmark
samples, each of which:

- carries process-derived ground truth backed by an admissible evidence
  package (never a judgment about how the text reads);
- passed the six duplicate screens and a complete decontamination scan;
- passed two independent reviews with measured inter-reviewer agreement;
- satisfies every CAS §12 criterion, with no §13 criterion applying;

together with a harness that evaluates a detector against it and reports
worst-cell metrics with Wilson intervals, and a manifest that ships the
covering-matrix verification and the decontamination scan summary.

**v1.0 is a corpus. The code is the means, not the deliverable.** A release
of this repository with an empty `corpus/` is not GAUNTLET v1.0 under any
reading.

### Recommended scope cut: v1.0 ships DEV + TEST; HIDDEN moves to v1.1

This is the single highest-leverage decision available and it needs
governance sign-off.

HIDDEN requires private, access-controlled, logged storage outside the public
repository (TD-B01, Critical; TD-X04 unprovisioned), contributor-blinded split
assignment that a single public repo cannot provide (TD-B04), and an
evaluation service with query budgets and canary tracking (TD-D13, R-18).
None of that exists, none of it is engineering-bound, and all of it sits on
the v1.0 critical path only because HIDDEN is in scope.

A benchmark with a public TEST split, a documented rotation plan, and an
honest statement that TEST is semi-public and will decay is a real benchmark.
A benchmark that cannot be released because it needs infrastructure the
project does not have is not.

**If accepted, this removes TD-B01, TD-B04, TD-D13, TD-X04 and R-18 from
v1.0**, and reduces the corpus target by ~3,000 samples (below).

---

## 2. Corpus work — the critical path

### 2.1 The scale, computed from BS §2.6

| Quantity | Value |
|---|---|
| Categories | 99 (H 20, A 12, X 14, V 17, F 20, E 15, U 1) |
| Length buckets | 6 (B25, B50, B100, B250, B500, B1000) |
| Cell columns (category × bucket) | 594 |
| v1.0 per-cell floors | TEST ≥ 10, DEV ≥ 5, HIDDEN ≥ 5 |
| Floor total, all three splits | ~11,880 samples |
| Floor total, DEV + TEST only | ~8,900 samples |

Less §2.5 cell exemptions (combinations that are impossible, e.g.
table-dominant samples below B100) and the category exemptions already in
`manifest.json`. The order of magnitude does not change: **v1.0 needs
something on the order of 10,000 samples**, each with an evidence package,
two independent reviews, and a screening pass.

### 2.2 A conflict inside §2.6 (new — proposed TD-G16)

§2.6 states two v1.0 constraints that do not appear to be simultaneously
satisfiable:

- per-cell: TEST ≥ 10 samples;
- aggregate: "the pooled human TEST set must contain ≥ 3,000 samples in
  v1.0", justified by the statistics of estimating a 0.5–1.0% FPR.

25 of the 99 categories carry an expected label of HUMAN (all 20 of Track H,
plus X-11, V-02, V-03, V-11, E-12). At 6 buckets and 10 TEST per cell, that
yields **1,500** pooled human TEST samples — half the aggregate floor. Even
counting all 34 categories whose expected label is unspecified (Tracks F, E,
U) as fully human, the ceiling is ~3,540, and those tracks are mixed by
construction.

**The aggregate constraint should win**, because it is the one with a stated
scientific justification: an FPR in the 0.5–1% range cannot be estimated from
1,500 negatives. That implies human cells must be built to roughly **20 TEST
per cell** in v1.0 — close to the v1.1 target of 25 — which materially
changes the human commissioning budget.

This is a governance decision. It is recorded here and must not be resolved
by assumption.

### 2.3 Sourcing, per class

| Class | Admissible sourcing (BS §4.3, CAS §3) | Blocker |
|---|---|---|
| Human (T0) | Pre-2020 archives with a genuine archive record | licensing, acquisition |
| Human (T1) | Freshly commissioned writing under process logging | **TD-X05: no logging tooling exists** |
| Human (T2) | Attested contributions | contributor recruitment |
| AI | Captured generation sessions with the full BS §4.4 record | generation harness + provider access |
| Hybrid | Controlled sessions with every edit round captured (T1 by construction) | editing-session capture tooling |

**TD-X05 is the quiet blocker.** BS §4.3 says the T0 pool alone is
insufficient because human style drifts, and H-20 exists specifically to keep
the human distribution current. Current human material must be commissioned
under logging, and no logging tool has been specified, chosen, or built. It
is not on any roadmap item.

### 2.4 Corpus milestones

| # | Milestone | Exit criterion |
|---|---|---|
| C-1 | Sourcing plan ratified | Named archives + licences; commissioning process and logging tool chosen; generation harness specified |
| C-2 | Pilot cell end-to-end | One (category × bucket) cell fully populated through IDEA → ACCEPTED, all screens run, both reviews complete, kappa measured |
| C-3 | Human baseline | Pooled human TEST reaches the §2.6 aggregate floor (see §2.2) |
| C-4 | Covering design satisfied | BS §2.7 pairwise covering matrix verifies mechanically |
| C-5 | Full floor | Every non-exempt cell meets its v1.0 target; exemptions listed in the manifest |

C-2 is the one to schedule next. It is the first real test of every
assumption Phases A–C encoded, and it will find integration defects that unit
fixtures cannot.

---

## 3. Governance work

16 items are OPEN. Grouped by what must be answered and when.

### 3.1 Must be resolved before Phase D

| ID | Cause | Why it blocks Phase D |
|---|---|---|
| TD-G04 | Underspecified threshold | Share caps live in the coverage plan, which *is* R-12 |
| TD-G09 | Missing specification | The cap denominator; answering G04 without G09 produces an unenforceable number |
| TD-G02 | Missing vocabulary | §9.5 makes identifiers permanent; a Track U sample authored under the `U-01` placeholder can never be renamed, and R-12 sets authoring targets |
| **New** | Missing specification | HIDDEN → TEST promotion has no lifecycle transition (§4 below). R-13 and R-15 need it |
| TD-G16 | Conflicting specification | §2.6's per-cell and aggregate floors (§2.2 above); it sets R-12's targets |

### 3.2 Must be resolved before v1.0 release, not before Phase D

| ID | Cause | Gates |
|---|---|---|
| TD-G01 | Conflicting specification | BS §9.1(h): 66 of 99 categories have a §6.2 failure-mode entry. Either extend §6.2 or narrow §9.1(h) |
| TD-G15 | Conflicting specification | Kappa per release (BS) vs per batch (CAS). The release gate computes one of them |
| TD-G12 | Missing vocabulary | Which fields are judgment fields; currently an unratified registry interpretation |
| TD-G13 | Underspecified threshold | Minimum batch size for kappa; without it a 3-item batch passes on noise |
| TD-G06 | Missing vocabulary | Rights vocabulary; A-9 and X-9 cannot pass mechanically without it |
| TD-G05 | Underspecified threshold | Per-register near-duplicate thresholds; needs empirical calibration, not a decision |
| TD-G10 | Underspecified threshold | Containment-ratio threshold, or a ratified decision that none exists |
| TD-G03 | Missing benchmark policy | Difficulty panel composition; §9.1(f) canary solvability depends on it |
| TD-G07 | Underspecified threshold | Per-panel-member decision thresholds |

### 3.3 Safe to defer to v1.1

| ID | Why deferrable |
|---|---|
| TD-A04 | Interim reading applied and reported; ratification is bookkeeping, and behaviour does not change |
| TD-G11 | `screen` action already added and recorded; §14.3 makes amendments prospective |
| TD-G14 | "repeatedly", "periodic", "senior" affect reviewer process quality, not label validity. Interim: report the record, apply no rule |
| TD-G08 | X-12 MT default is in force and recorded in the manifest; only binds when X-12 is authored |

### 3.4 Safe to defer to v2

Nothing in the OPEN list is genuinely a v2 item. Everything either gates the
release or has a stated, reported interim. The v2 deferrals are engineering
and corpus scope, not governance: HIDDEN infrastructure (if the §1 scope cut
is accepted), Track U at scale, empirical difficulty re-estimation, and
per-cell targets rising to 50 TEST.

### 3.5 Clusters that must be answered together

- **G04 + G09** — a cap number without a denominator is unenforceable.
- **G12 + G13 + G15** — what is measured, over how many items, at what unit.
  Answering any one alone produces an incoherent agreement gate.
- **G01 + G02** — both are the category system being incomplete: one track
  has no numbering, and a third of categories have no failure-mode entry.
- **G03 + G07** — panel composition and panel thresholds; the second is
  meaningless without the first.
- **G05 + G10** — both are "how similar is too similar", and both want
  calibration on real material rather than a governance vote. They cannot be
  answered before C-2.

---

## 4. Engineering work

### 4.1 Lifecycle gap: HIDDEN → TEST promotion has no transition

CAS §10.6 and BS §2.3 both require that a slice of HIDDEN is promoted to TEST
at each MAJOR release, and that a leaked slice is "immediately retired to
TEST". `LEGAL_TRANSITIONS` has `RELEASED → {DEPRECATED, REDACTED}` and
nothing else, and `split` is a field §4.2 says is assigned only at Stage 8.

Promotion is therefore a second split assignment against an already-RELEASED
sample, and neither the state machine nor the errata path (§9.2) represents
it. Whether this is an errata, a re-assignment, or a new state is a
governance question; the engineering follows the answer.

If the §1 scope cut is accepted this is deferred with HIDDEN — but the
decision should still be recorded, because the rotation plan is part of what
makes a semi-public TEST split defensible.

### 4.2 Policy constants that should move into governed data

Detailed in the accompanying review. Summary: seven policy decisions
currently live as Python constants with no governance trail, and the AST
vocabulary guard inspects only `spec.py`, so none of them is protected
against drift.

| Constant | Module | Why it is policy |
|---|---|---|
| `EXPLANATORY_RELATIONS` | `duplicates.py` | Duplicates `field_registry.relationship_types`; decides whether an undeclared near-duplicate is rejected |
| `TERMINAL_CODES` | `screening.py` | Decides whether an identifier is burned forever |
| `DEFAULT_NEAR_THRESHOLDS` and the semantic/template defaults | `duplicates.py` | These are TD-G05's answer-in-waiting |
| `ACCEPTANCE_CRITERIA` / `REJECTION_CRITERIA` | `acceptance.py` | Transcriptions of §12/§13 |
| `Criterion.mechanization` | `acceptance.py` | Determines which criteria require a human confirmation |
| `REQUIRED_REFERENCES` | `decontamination.py` | The §4.9 reference-corpus floor |
| `PRODUCER_ROLES` | `ledger.py` *and* `review.py` | Defined twice |

**Recommended:** one `benchmark/policy.json`, read the way `spec.py` reads
vocabularies, plus extending the AST guard to every `gauntlet/*.py` module.
Small, and it closes the last structural gap between "governed" and "coded".

### 4.3 Remaining roadmap items

| Item | Phase | Depends on |
|---|---|---|
| R-12 coverage plan | D | TD-G04, TD-G09, TD-G16 |
| R-13 split assignment | D | TD-B04 (or the §1 scope cut) |
| R-15 release builder + post-release workflows | D | §4.1 promotion decision |
| R-16 difficulty system | E | TD-G03, TD-G07, TD-X02, TD-X03 |
| R-17 regression execution and CI tiers | E | R-15 |
| R-18 evaluation service | E | TD-B01 — **droppable via the §1 scope cut** |
| TD-D14 runner tasks T3/T4 | E | — (currently `NotImplementedError`) |
| TD-D15 calibration reporting (ECE, Brier, leakage index) | E | — |
| TD-D19 declared-interest register | C (spillover) | — |
| TD-D20 provenance-challenge register | D | — |
| TD-D16 supersedes-target state check | B (spillover) | — |

### 4.4 External dependencies

| ID | Needed for | State |
|---|---|---|
| TD-X01 | Decontamination; blocks BS §9.1(d) permanently until resolved | not acquired |
| TD-X05 | T1 human commissioning — **the corpus critical path** | not provisioned |
| TD-X06 | §8.3 semantic screening (lexical stand-in in place) | not provisioned |
| TD-X02 / TD-X03 | Difficulty panel members DF1 / DF4 | not provisioned |
| TD-X04 | HIDDEN custody | droppable via the §1 scope cut |

---

## 5. Release criteria for v1.0

Every BS §9.1 criterion, plus the project-level conditions this checkpoint
adds:

| # | Criterion | Source |
|---|---|---|
| 1 | Every non-exempt cell meets its v1.0 target; covering matrix verifies mechanically; exemptions listed in the manifest | §9.1(a) |
| 2 | 100% of TEST samples at T0/T1/T2; hybrid cells 100% T1; provenance refs resolve | §9.1(b) |
| 3 | Kappa ≥ 0.8 on all judgment fields at the ratified unit; adjudication log complete | §9.1(c), TD-G15 |
| 4 | Decontamination scan passed **and complete** — every required source consulted | §9.1(d), TD-X01 |
| 5 | PII and licence fields present and screened on every sample | §9.1(e) |
| 6 | A public baseline detector reaches ≥ 95% on D1 cells | §9.1(f) |
| 7 | Metadata schema-complete, including rationale and target_weakness | §9.1(g) |
| 8 | Every category has a §6.2 failure-mode entry and an adversarial entry | §9.1(h), TD-G01 |
| 9 | Every OPEN governance item in §3.1 and §3.2 resolved and ratified | this checkpoint |
| 10 | No policy constant remains outside governed data (§4.2) | this checkpoint |
| 11 | The evaluating detector is not maintained in the same repository, or the conflict is disclosed in the release notes | CAS §6.6 |

Criterion 11 is the one most likely to be overlooked. See §6.

---

## 6. Open-source release

**Do not publish this as GAUNTLET v1.0.** Publishing a repository under that
name with an empty corpus invites the reading that the benchmark exists. What
exists is a specification and the machinery to enforce it — which is worth
publishing, under a name that says so.

Two recommended actions before any public release:

1. **Separate the detector from the benchmark.** The repository's public
   identity today is `ai-text-eval`, "an AI text detector". GAUNTLET is a
   benchmark that would evaluate detectors, including that one. CAS §6.6
   prohibits review by "anyone with a declared interest in a detector
   currently under evaluation", and a benchmark maintained in the same
   repository as a detector it scores is that conflict at the project level.
   Either split the repositories or disclose the conflict prominently and
   permanently.

2. **Publish the specification and the harness as a pre-corpus release** —
   `gauntlet-spec` at 0.x, with the corpus explicitly absent and the debt
   register shipped as a first-class document. That is honest, it invites the
   governance input the 16 OPEN items need, and it does not claim a benchmark
   that does not yet exist.

---

## 7. Explicitly postponed to v1.1

| Item | Rationale |
|---|---|
| HIDDEN split, custody, evaluation service (TD-B01, B04, D13, X04, R-18) | Needs infrastructure the project does not have; postponing it does not weaken the DEV+TEST benchmark, and shipping without it beats not shipping |
| Per-cell TEST ≥ 25 | v1.1 target per §2.6 |
| Empirical difficulty re-estimation | Provisional difficulty is sufficient for v1.0 if TD-G03 is answered |
| Runner tasks T3 (origin attribution) and T4 (span localization) | Currently `NotImplementedError`; v1.0 can ship T1/T2 with the gap documented |
| Track U at scale | One placeholder category; TD-G02 must be answered before *any* Track U sample is authored, but the track need not be populated for v1.0 |
| TD-G08, TD-G11, TD-G14, TD-A04 | Interim readings applied, reported, and behaviourally stable |
| A real semantic backend (TD-X06) | Lexical stand-in ships, labelled as such, with its miss regression-tested |

---

## 8. Honest summary of the distance

Phases A–C are roughly 6,000 lines of instrument and 816 tests. What remains
is one lifecycle transition, one data-migration of policy constants, three
Phase D roadmap items, and a corpus of order 10,000 samples that does not
exist and cannot be produced without commissioning tooling that has not been
chosen.

The engineering is the small part. That was true at the start and it is more
true now.
