# CORPUS_AUTHORING_SPEC

**Status:** Normative. Version 1.0 (draft for adoption).
**Date:** 2026-08-05
**Applies to:** All human contributors, AI generation operators,
reviewers, maintainers, release managers, and automated agents
participating in the creation of the GAUNTLET corpus.
**Companion document:** The GAUNTLET Benchmark Specification
(referred to below as "the Benchmark Specification"), which
defines what the corpus contains: tracks, categories, splits,
metrics, and regression policy. This document defines how corpus
material is created, evidenced, reviewed, versioned, and
governed. Where the two documents use the same terms (provenance
tiers T0-T3, labels, splits DEV/TEST/HIDDEN, difficulty D1-D5,
detector families DF1-DF7), the definitions are shared.

**Conformance language:** MUST, MUST NOT, SHOULD, SHOULD NOT,
and MAY are used in their customary normative senses. A MUST
violation makes a sample, release, or action non-conformant. A
SHOULD may be departed from only with a recorded justification.

**Exclusions:** This document deliberately contains no
implementation detail: no code, no tooling choices, no storage
formats, no interfaces. Any implementation that preserves every
normative statement here is conformant.

---

## 1. Philosophy

### 1.1 What makes a benchmark trustworthy

A benchmark is a measurement instrument. Its trustworthiness
rests on five properties, in priority order:

1. **Label validity.** Every ground-truth label is actually
   true, and is known to be true through evidence rather than
   belief. A benchmark with wrong labels is not a weaker
   instrument; it is a miscalibrated one that confidently
   rewards wrong behavior.
2. **Construct validity.** The benchmark measures what it
   claims to measure. GAUNTLET claims to measure detection of
   the authorship process. If labels ever derive from how text
   looks, the benchmark silently starts measuring agreement
   with a style stereotype instead, while still claiming to
   measure process detection.
3. **Reproducibility.** Every sample, label, and score can be
   traced to retained evidence and regenerated or re-verified
   by a party who was not present at creation.
4. **Resistance to gaming.** The benchmark anticipates that
   both detectors and contributors respond to incentives, and
   is designed so that gaming attempts are detectable and
   unprofitable.
5. **Honesty about limits.** The benchmark represents its own
   uncertainty: undecidable cells exist and are scored on
   calibration, small cells carry visible confidence intervals,
   and no single number summarizes the corpus.

### 1.2 Why benchmark integrity outranks detector accuracy

A defective detector fails the deployments that use it. A
defective benchmark fails every detector ever evaluated on it,
every paper that cites those evaluations, and every decision
made downstream, and it fails them silently, because the scores
still look like measurements. The error budget of the field
concentrates in its instruments.

Two asymmetries follow. First, correction cost: a detector bug
is fixed by a release; a benchmark label error, once published,
persists in every comparison already made against it and cannot
be recalled. Second, incentive corruption: a benchmark with
polluted human cells does not merely add noise. It teaches
detectors that "human" means whatever the polluting process
produced, and the better a detector optimizes against the
benchmark, the more wrong it becomes. Integrity failures are
therefore anti-correlated with their own visibility: the
benchmark keeps producing confident numbers precisely while it
misleads.

Consequently, whenever corpus growth, evaluation convenience,
schedule, or an impressive-looking result conflicts with
integrity, integrity wins. There are no documented exceptions
to this rule anywhere in this specification, by design.

### 1.3 Inviolable principles

The following principles MUST NOT be violated by any
contributor, reviewer, maintainer, agent, or process. Any
proposal that requires violating one of them is rejected
regardless of its benefits.

- **P1. Process-derived labels only.** A label states how a
  text was produced. It is assigned mechanically from
  production records, never inferred from the artifact.
- **P2. The Generation Firewall.** Text with any generative
  model involvement MUST NOT enter the HUMAN class through any
  pipeline, review, approval, editing pass, or waiting period.
  Review can accept or reject a candidate; it can never launder
  one upward in provenance. There is no quorum of approvals
  that converts model text into human ground truth.
- **P3. Inadmissibility of appearance.** Neither a human
  judgment that text "reads human" nor any detector's output is
  admissible evidence for or against a label. Using detector
  output to set labels makes the benchmark circular: it would
  thereafter measure agreement with that detector.
- **P4. Evidence retention.** The evidence behind every label
  is preserved, checksummed, and auditable for the life of the
  corpus.
- **P5. Nothing is deleted.** Samples are deprecated or
  redacted through governed processes; they are never removed
  from history. Deletion destroys the ability to audit past
  results.
- **P6. Hidden material stays hidden.** No convenience justifies
  exposing HIDDEN split content (Section 10).
- **P7. Fairness cells are never synthetic.** Cells that exist
  to measure demographic or population harm (for example,
  non-native writer false positives) MUST contain genuine text
  from the population in question. Imitations of a population
  are a different measurement and never substitute.
- **P8. Worst-case reporting.** Corpus and evaluation reporting
  obligations follow the Benchmark Specification: per-cell
  results always accompany aggregates.
- **P9. Defects become permanent tests.** Every confirmed
  corpus defect and every confirmed detector failure discovered
  through the corpus feeds the regression policy of the
  Benchmark Specification.
- **P10. Declared relationships.** Every intentional
  relationship between samples (shared topic, base and derived,
  transform pairs, mimicry pairs) is declared in metadata.
  Undeclared similarity is treated as a defect, not a
  coincidence.

---

## 2. Corpus Lifecycle

Every sample moves through an explicit state machine. States
are: IDEA, CANDIDATE, VALIDATED, SCREENED, REVIEWED, ACCEPTED,
ASSIGNED, RELEASED, DEPRECATED, with REJECTED and REDACTED as
terminal exception states. No stage may be skipped. The current
state of every sample is queryable at all times.

**Stage 1: Idea.** A need is identified: a coverage gap in the
cell matrix, a new failure hypothesis, a red-team submission, a
category mandated by the Benchmark Specification. The idea
records the intended cell (track, category, length bucket,
language) and the intended target weakness. Ideas are cheap and
carry no quality bar; their purpose is to make intent explicit
before production, because intent determines which generation
policy of Section 3 applies.

**Stage 2: Candidate Generation.** Text is produced under the
class-appropriate policy of Section 3, together with its
evidence package. At the end of this stage the text is frozen:
the candidate receives a permanent identifier, a cryptographic
checksum of the exact text, and a timestamp. From this point
the text MUST NOT change. Any change, however small, creates a
new candidate with a lineage link to the old one; the old one
is rejected with reason "superseded". The freeze exists because
every downstream judgment (validation, screening, review,
difficulty) is a judgment about one specific string.

**Stage 3: Metadata Assignment.** The full metadata record
(Section 4) is completed. Ground-truth fields are derived
mechanically from the evidence package; judgment fields
(difficulty intent, rationale, target weakness) are drafted by
the contributor and later confirmed in review.

**Stage 4: Validation.** Automated conformance checking of the
metadata record: completeness, vocabulary membership,
cross-field consistency (Section 4.4), length bucket
verification, evidence references resolving. Validation is
mechanical and produces a pass or an itemized failure list. A
candidate MUST pass validation before any human reviews it, so
reviewer attention is spent on judgment, not on typos.

**Stage 5: Duplicate Detection.** The candidate is screened
against the entire corpus history under Section 8: all splits,
all releases, deprecated samples, the regression registry, and
the current candidate pool. Declared relationships are checked
for declaration; undeclared similarity above thresholds places
the candidate on hold pending contributor explanation.
Decontamination screening against external public detection
corpora (Benchmark Specification, Section 4.9) also runs here.

**Stage 6: Reviewer QA.** Human review under Section 6:
provenance audit and content/metadata review by independent
reviewers, with adjudication on disagreement. Output is an
acceptance recommendation, a revision request (metadata only;
text is frozen), or a rejection with recorded reasons.

**Stage 7: Acceptance.** A maintainer confirms that every
criterion of Section 12 holds and that no criterion of Section
13 applies, and records the acceptance decision. Acceptance
makes the sample part of the corpus inventory but not yet part
of any release.

**Stage 8: Corpus (Split Assignment).** A release manager
assigns the sample to DEV, TEST, or HIDDEN under the rules of
Sections 10 and 11. Contributors are never told the assignment.
Assignment respects tier admissibility (T3 material may enter
DEV only) and the cross-split similarity constraints of Section
8.

**Stage 9: Release.** The sample ships in a versioned,
immutable, checksummed corpus release (Section 9). Only at this
point may evaluation results reference it. First-release
evaluation by the reference panel also fixes its empirical
difficulty (Section 7).

**Stage 10: Regression.** Post-release, the sample participates
in the living system: label challenges (Section 5.6), errata
(Section 9.2), deprecation (Section 9.3), and the promotion of
any defect it reveals into permanent regression material per
the Benchmark Specification. The lifecycle has no final resting
state other than DEPRECATED or REDACTED; a released sample
remains an active measurement instrument indefinitely.

---

## 3. Candidate Generation

### 3.1 Rules common to all classes

1. Intent first: the target cell and target weakness are
   declared before production (Stage 1), because the class of
   the sample is determined by how it is produced, and how it
   is produced must be chosen in advance, not rationalized
   afterward.
2. Evidence package: every candidate is submitted together with
   the evidence its tier requires (Section 5). Candidates
   without complete evidence do not enter validation.
3. Declaration duty: the submitting contributor MUST declare
   every instance of model involvement in the text's history,
   including assistance they consider trivial. Discovery of
   undeclared model involvement at any later time triggers
   Section 11.6.
4. Freeze on registration: per Stage 2.
5. Conflict rule: the person who produced or operated the
   production of a sample MUST NOT review it (Section 6.6).

### 3.2 Human-authored text (target label HUMAN)

Acceptable sources, each with its tier:

- **Archival (T0):** Text captured by an independent archive
  with a verifiable capture timestamp predating 2020, obtained
  with rights to use, with the archive's integrity record
  retained. The date bound exists because generative writing
  assistance after that point is widespread enough that
  publication on the open web stops being evidence of purely
  human production.
- **Commissioned (T1):** Text written in sessions arranged for
  the corpus, under process logging (keystroke capture, edit
  session recording, or equivalent), in an environment attested
  to be free of generative writing tools. The logging
  arrangement MUST be in place before writing begins;
  retroactive logging does not exist.
- **Attested (T2):** Text contributed with a signed attestation
  from the named author describing when and how it was written
  and affirming the absence of model involvement, accepted only
  where T0 and T1 are impossible for the cell, and subject to
  spot verification. Fairness-gated cells additionally require
  T1 or T2 with population membership genuinely established
  (P7).

Prohibited sources for the HUMAN class:

- Any text produced with any generative model involvement,
  including "AI-style human imitation" produced by a model, and
  including model-assisted brainstorming that contributed
  phrasing (P2). This prohibition is absolute and survives any
  amount of review, editing, or delay.
- Web text published after 2022 without T1/T2 evidence, however
  human it appears (P3).
- Text produced with writing tools that offer generative
  rewriting, even if the contributor believes they did not use
  those features, unless the session log proves it.
- Crowdsourced text without process controls; platform labels
  ("posted by a person") as evidence; text recalled or
  reconstructed from memory; text laundered through paraphrase
  of any source.
- Famous, widely memorized, or previously published text
  presented as newly authored.

### 3.3 AI-generated text (target label AI)

Acceptable production: a logged generation session recording
the model family and exact version, provider, complete prompt
including any system prompt, all decoding parameters, seeds
where the interface supports them, the request date, and the
raw output preserved byte-exact. This record is the provenance
(T1 by construction). Post-generation modification MUST be
limited to whitespace normalization; any other human change
reclassifies the material as hybrid or discards it.

Prohibited sources for the AI class:

- Model text found in the wild without a generation record. Its
  process cannot be established (the generator, the prompt, and
  possible human edits are all unknown), so it is inadmissible
  for TEST and HIDDEN; it MAY enter DEV only, flagged as noisy.
- Outputs from interfaces that cannot produce a session
  transcript, unless a complete contemporaneous capture of the
  session exists.
- Text whose generation involved undocumented human steering
  mid-generation beyond the recorded prompts.

### 3.4 Hybrid text: human-edited AI and AI-edited human

Hybrids MUST be manufactured, never found. The production
session captures: the base text (itself meeting Section 3.2 or
3.3 as appropriate), every editing round as a stored
intermediate state, the instructions given to whichever party
edited (the instruction defines the category: a grammar-only
instruction and a free-rewrite instruction produce different
cells), and the identity and class (human or model, with model
config) of the editor at each round. The share of final tokens
of model origin is computed mechanically from the stored
difference chain, never estimated by a person.

Prohibited: hybrids encountered in the wild (their process is
unknowable), reconstruction of an editing history after the
fact, contributor-estimated edit shares, and any hybrid whose
intermediate states were not retained.

### 3.5 Collaborative mixed text

Interleaved authorship requires span records created at
production time: which contiguous regions of the final text
originate from which party, recorded as the text is assembled.
Post-hoc span annotation of a finished text is prohibited,
because origin of a span is a fact about production, not a
property recoverable from reading.

### 3.6 Adversarial samples

Adversarial material is produced by applying documented,
parameterized, seeded transforms to bases that themselves meet
the provenance requirements of their class. Rules:

1. A transform never changes the authorship label. A
   paraphrased AI text is still AI; a Unicode-perturbed human
   text is still HUMAN. The transform is recorded as applied
   history, and the derived sample carries a lineage link to
   its base (P10).
2. Every transform records its tool or method identity, its
   version or date (external tools drift, so the date is part
   of the sample's identity), its parameters, and enough
   information to regenerate or exactly verify the result.
3. Attack instructions given to generators (style constraints,
   tell suppression, persona directives) are stored verbatim
   with the sample.
4. Red-team submissions from outside the core team are welcome
   and enter the normal lifecycle; they MUST arrive with
   provenance meeting this section, and their target weakness
   MUST be articulated in terms of a detector assumption, not
   in terms of one implementation's bug (see Section 13, item
   X-11).

### 3.7 Decontamination at candidacy

Every candidate is screened for overlap with public detection
corpora and with the corpus's own DEV split before acceptance,
under the thresholds of the Benchmark Specification. Screening
at candidacy rather than at release keeps contaminated material
from consuming review effort.

---

## 4. Metadata

### 4.1 Registry principles

Metadata is governed by a field registry. Every field has a
stated purpose, an allowed-value definition, validation rules,
and a "since" version. Vocabularies (tracks, categories,
domains, formats, languages, labels, tiers, difficulty grades)
are closed and versioned: values are added through governance
(Section 14), never invented inline by a contributor. Records
MUST NOT contain fields absent from the registry; consumers
MUST ignore fields added after their own version (forward
compatibility). Fields are deprecated, never removed, so old
records remain readable forever.

### 4.2 Field registry

Fields are grouped by function. For each: purpose, allowed
values, validation, and an example.

**Identity fields**

- **identifier.** Purpose: permanent unique name. Values: a
  structured code encoding track, category, length bucket, and
  a serial (e.g., V-05-B250-0031). Validation: unique across
  all history including rejected and redacted material; never
  reused; never renamed. Example: H-14-B100-0007.
- **schema version / corpus version.** Purpose: bind the record
  to the registry and release that define its meaning.
  Validation: must reference published versions.
- **lineage references.** Purpose: declare relationships (P10):
  base sample for a derived transform, superseded candidate for
  a resubmission, paired sample for mimicry or tell pairs.
  Validation: referenced identifiers must exist; relationship
  type drawn from a closed vocabulary. Example: derived-from
  A-01-B250-0114.

**Classification fields**

- **split.** Values: dev, test, hidden. Validation: assigned
  only at Stage 8 by a release manager; tier admissibility
  enforced (T3 implies dev).
- **track and category.** Values: registry of the Benchmark
  Specification, Section 3. Validation: category must belong to
  track; the cell must exist in the coverage matrix.
- **domain, format, language.** Values: closed vocabularies per
  the Benchmark Specification's axes. Validation: membership;
  language values distinguish native, non-native, mixed, and
  specific non-English languages as the registry grows.
- **length.** Purpose: exact word count and bucket. Validation:
  count computed by the harness's defined counting rule, not
  contributor-supplied; bucket boundaries per the Benchmark
  Specification, Section 2.5; count must fall inside the
  declared bucket.

**Ground-truth fields**

- **label.** Values: HUMAN, AI, AI_HUMAN_EDITED,
  HUMAN_AI_EDITED, COLLAB_MIXED. Validation: derived
  mechanically from the evidence package class (Section 3);
  reviewers verify derivation, they do not choose labels.
- **model-origin share.** Purpose: fraction of final tokens of
  model origin. Values: 0.0 to 1.0. Validation: exactly 0.0 for
  HUMAN, exactly 1.0 for AI, computed from stored difference
  chains for hybrids; hand-entered values are non-conformant.
- **span map.** Purpose: region-level origin for interleaved
  and spliced samples. Validation: required when label is
  COLLAB_MIXED and for splice categories; spans must tile the
  text without overlap; created at production time (Section
  3.5).

**Provenance fields**

- **provenance tier.** Values: T0, T1, T2, T3 (Section 5).
  Validation: tier must be supported by the referenced
  evidence; split admissibility follows tier.
- **source type.** Purpose: one-line human-readable production
  summary. Example: commissioned_writer_T1;
  archive_2017_mailing_list.
- **provenance reference.** Purpose: pointer to the retained
  evidence package. Validation: must resolve; evidence
  checksums must verify.

**Generation and transform fields**

- **generator record.** Purpose: full production configuration
  for model-involved samples: family, exact version, provider,
  prompt style class, decoding parameters, seed where
  available, request date, and a reference to the complete
  session record. Validation: required whenever the label is
  not HUMAN; MUST be absent when the label is HUMAN (its
  presence with a HUMAN label is an automatic validation
  failure and a P2 alarm).
- **transform record.** Purpose: ordered list of adversarial or
  noise transforms applied, each with method identity, version
  or date, and parameters. Validation: required for Track V and
  transform-bearing Track E samples; empty otherwise; every
  entry must be regenerable or exactly verifiable.

**Evaluation-support fields**

- **topic group.** Purpose: link samples produced from the same
  topic assignment so content-versus-process effects can be
  separated. Validation: group must exist; groups span classes
  by design.
- **difficulty.** Values: D1-D5 plus the reference panel
  version that produced the value (Section 7). Validation:
  provisional at candidacy, empirical after first release;
  never hand-edited.
- **rationale.** Purpose: one to three sentences stating why
  this sample belongs to its class and cell. Validation:
  required, non-boilerplate; reviewed for substance.
- **target weakness.** Purpose: the detector assumption this
  sample probes, phrased against the failure-mode registry of
  the Benchmark Specification, Section 6. Validation: required;
  must reference a registered failure mode or propose a new one
  for review.
- **expected confusions.** Purpose: detector families (DF1-DF7)
  expected to err. Validation: optional but recommended; family
  codes from the shared taxonomy.

**Administrative fields**

- **noisy label flag.** Purpose: mark T3 material admissible in
  DEV only. Validation: true implies split dev.
- **license and rights.** Purpose: the legal basis for
  inclusion and redistribution. Validation: required; values
  from a rights vocabulary; "unknown" is not a value.
- **PII status.** Values: clean, scrubbed, synthetic.
  Validation: required; scrubbed requires the scrubbing record
  in evidence.
- **timestamps and actors.** Purpose: creation, freeze,
  acceptance, assignment, and release times, with the acting
  role (not necessarily the person) recorded for audit.
- **notes.** Free text; never load-bearing; nothing normative
  may live only in notes.

### 4.3 Extensibility

New fields are added by registry amendment with a since-version
and a default interpretation for older records. New vocabulary
values are additive. A change that alters the meaning of an
existing field or value is a breaking change and forces a MAJOR
corpus version (Section 9.1). Records never migrate silently:
old records keep their original schema version and are
interpreted under it.

### 4.4 Cross-field consistency rules

Validation (Stage 4) MUST enforce at minimum: label HUMAN
implies model-origin share 0.0, no generator record, and no
model-involving transforms; label AI implies share 1.0 and a
complete generator record; hybrid labels imply stored
difference chains and a share strictly between 0 and 1;
COLLAB_MIXED implies a tiling span map; tier T3 implies split
dev and the noisy flag; Track V implies a non-empty transform
record with a lineage link to a base; fairness-gated categories
imply tier T1 or T2; every declared relationship resolves both
ways. The consistency rule set is versioned with the registry
and grows monotonically: rules are added, never weakened,
except by MAJOR version with rationale.

---

## 5. Provenance

### 5.1 Definition

Provenance is the evidence-backed account of how a text came to
exist. It is the sole source of ground truth (P1). The tier
expresses the strength of the evidence class, not the quality
of the text.

### 5.2 Tiers and admissibility

- **T0, archival:** an independent third party captured the
  text before 2020 and can prove when. Admissible everywhere.
  Strongest for the HUMAN class because it predates the
  possibility of model involvement entirely.
- **T1, process-logged:** the production process itself was
  recorded: keystroke or edit-session capture for human
  writing, complete session transcripts for generation, stored
  intermediate chains for hybrids. Admissible everywhere.
  Required for all hybrid material and all fairness-gated
  cells (with T2 as the fallback for the latter).
- **T2, attested:** a named, contactable author signed a
  contemporaneous description of the process, and the
  contribution is subject to spot verification. Admissible
  everywhere but SHOULD be a minority of any cell; used where
  logging is impossible.
- **T3, heuristic:** provenance inferred from circumstance.
  Admissible in DEV only, flagged noisy, and never used for any
  headline or gating metric.

### 5.3 Acceptable evidence

- Independent archive records with capture timestamps and
  integrity data, where the archive predates and is not
  controlled by the contributor.
- Process capture: keystroke logs, edit-session recordings,
  screen recordings covering the full session, version-history
  chains created contemporaneously.
- Complete generation session records: prompts, configuration,
  raw responses, dates.
- Stored intermediate states with mechanical difference chains
  for hybrids.
- Signed attestations from identified authors, made at
  contribution time, describing tools, dates, and process, and
  acknowledging the spot-verification right.
- Transform regeneration records: parameters and seeds
  sufficient to reproduce or exactly verify a derived text.

### 5.4 Unacceptable evidence

The following MUST NOT support a label, alone or in
combination:

- Stylistic judgment by anyone, however expert ("this clearly
  reads human").
- The output of any detector, classifier, or model judgment
  about the text (P3). This includes using a detector to
  "verify" that commissioned human text is human: circularity
  does not become acceptable by pointing in a convenient
  direction.
- Publication-platform signals: account age, profile claims,
  platform "written by a person" badges, timestamps on
  contributor-controlled sites.
- Contributor recollection unsupported by records; process
  descriptions reconstructed after the fact.
- Screenshots or excerpts of session records where the complete
  record was not retained.
- The reputation of the contributor. Trust is not evidence;
  trusted contributors submit evidence like everyone else.

### 5.5 Chain of custody

Evidence packages are checksummed at intake and stored with
access control and integrity verification for the life of the
corpus (P4). Derived samples inherit their base's evidence and
append their transform records; a derived sample's provenance
is exactly (base evidence + transform record), and it can never
exceed its base's tier. Every access to HIDDEN-associated
evidence is logged.

### 5.6 Audits, challenges, and downgrades

Each release cycle, a random sample of newly accepted material
and a smaller random sample of legacy material undergo
provenance re-verification by a reviewer not involved in their
acceptance. Any person may challenge any label at any time; a
challenge opens an errata investigation (Section 9.2) whose
outcome is recorded whichever way it goes. If evidence is lost,
found deficient, or successfully challenged, the sample's tier
is downgraded to what the surviving evidence supports; split
eligibility is re-evaluated, and material no longer admissible
in its split is moved to DEV or deprecated in the next release,
with the event recorded in release notes. Detector disagreement
with a label is never grounds for a challenge by itself (P3);
challenges must be about evidence.

---

## 6. Quality Assurance

### 6.1 Review model

Every candidate for TEST or HIDDEN eligibility receives two
independent reviews with distinct mandates; DEV-only material
receives one combined review.

- **Provenance review** audits the evidence package: tier
  requirements met, records complete and internally consistent,
  checksums verifying, dates coherent, mechanical derivations
  (shares, span tilings, difference chains) recomputed and
  matching. The provenance reviewer's mandate is the evidence,
  and the review is performed without forming or recording any
  opinion about whether the text "seems" consistent with its
  label; such opinions are inadmissible (P3) and their
  appearance in a review is itself a review defect.
- **Content and metadata review** checks everything else:
  metadata substance (rationale non-boilerplate, target
  weakness correctly mapped), category fit, length and bucket,
  PII screening confirmation, rights, tone of duplicate-screen
  escalations, and conformance of the sample to its category
  definition.

Reviews are performed independently: neither reviewer sees the
other's conclusions before submitting their own.

### 6.2 Disagreement handling

If the two reviewers disagree, or either disagrees with the
contributor's metadata, a third senior reviewer adjudicates.
The adjudicator's decision and reasoning are recorded.
Recurring disagreements of the same kind trigger a guideline
clarification: systematic ambiguity is a specification bug, not
a reviewer failure, and it is fixed in this document or the
category definitions rather than re-litigated per sample.

### 6.3 Agreement measurement

Judgment fields are dual-annotated across each review batch,
and inter-reviewer agreement MUST reach the threshold set in
the Benchmark Specification (kappa at or above 0.8) per field
per batch. A batch below threshold is re-reviewed after a
calibration session; the batch does not ship on schedule
pressure.

### 6.4 Rejection

A candidate is rejected when any Section 12 criterion cannot be
satisfied, when any Section 13 criterion applies, or when a
requested metadata revision cycle fails twice. Rejection is
terminal for that identifier. Rejected identifiers and their
reasons are retained (P5): rejection history is how the project
learns, and how repeat submission of bad material is detected.

### 6.5 Revision tracking

Text never revises (Stage 2). Metadata revisions before
acceptance are normal and logged (field, old value, new value,
reason, actor role). Metadata changes after acceptance are
errata (Section 9.2). A resubmission of corrected text is a new
candidate carrying a supersedes link to the rejected one.

### 6.6 Reviewer integrity

The producer of a sample, the operator of its generation or
editing sessions, and anyone with a declared interest in a
detector currently under evaluation on the affected cells MUST
NOT review it. Reviewers complete periodic calibration
exercises containing seeded known-defect candidates; a reviewer
who passes seeded defects repeatedly is retrained before
reviewing further. Reviewer performance records are part of the
audit trail.

---

## 7. Difficulty

### 7.1 Principle

Difficulty is a measurement, not an opinion. A sample is hard
if competent detectors fail on it, and that is established by
running competent detectors, not by predicting them.

### 7.2 The reference panel

Difficulty is defined against a fixed, versioned reference
panel of baseline evaluation systems chosen to span the
detector family taxonomy (at minimum one likelihood-based, one
supervised, one stylometric, and one judge-based member, per
DF1-DF4). Panel composition is a governed decision (Section
14); panel members are documented well enough to rerun; the
panel version is recorded alongside every difficulty value it
produces. The detector under evaluation by any benchmark user
is, by definition, never a panel member for the difficulty
values they consume.

### 7.3 Assignment procedure

At candidacy, a sample carries a provisional difficulty derived
from objective structural facts only: its category's registered
default, its length bucket (shorter is harder), its transform
strength class, and its generator-configuration distance from
common defaults. Provisional values exist so coverage planning
can proceed; they are marked provisional.

At first release, the reference panel evaluates the sample and
the empirical difficulty is fixed by rule:

- **D1:** every panel member correct, all with high margin
  (scores far from their decision thresholds, as defined per
  member in the panel documentation).
- **D2:** every panel member correct, margins unrestricted.
- **D3:** at least one member wrong or at low margin.
- **D4:** a majority of members wrong, or every member of an
  entire family wrong (systematic family failure).
- **D5:** panel performance at or below chance, or the sample
  belongs to a cell where correct behavior is calibrated
  abstention rather than classification (Track U and degenerate
  inputs).

The mapping rules are versioned with the panel. No human edits
a difficulty value (the prohibition is absolute: difficulty
edited by hand is difficulty invented).

### 7.4 Re-estimation

At each MAJOR release the panel is re-versioned if needed and
difficulty is re-estimated corpus-wide. Historical values are
retained under their panel versions, never overwritten;
difficulty drift across panel versions is itself a published
signal, since systematic easing of once-hard cells is a direct
measurement of detector progress, and systematic hardening of
human cells is an early warning about distribution drift.

---

## 8. Duplicate Detection

Every candidate is screened against the complete corpus
history: all splits, all releases, deprecated and redacted
identifiers' retained fingerprints, the regression registry,
and the live candidate pool. Six duplicate classes are
recognized; the unifying rule is P10: designed relationships
are declared, and undeclared similarity is a defect.

### 8.1 Exact duplicates

Definition: identical text after the corpus's defined
normalization (and, separately, identical raw bytes, since some
tracks are sensitive to invisible characters). Detection:
checksum comparison over both forms. Handling: the newcomer is
rejected. If the collision is with material in another split,
the incident is investigated, because it may indicate
contributor reuse or leakage.

### 8.2 Near duplicates

Definition: high overlap under character- and word-level
similarity measures, with thresholds calibrated on known
independent text of the same register (registers differ:
issue-tracker text is naturally more self-similar than
fiction, so thresholds are per-register, not global).
Detection: pairwise similarity against the indexed history.
Handling: above threshold, the candidate holds pending
explanation. Legitimate explanations are declared
relationships: a transform derived from a base, a tell-pair
twin, a mimicry pairing. With declaration verified, both
samples stand and the link is recorded. Without it, rejection.

### 8.3 Semantic duplicates

Definition: same content in different words. Detection:
meaning-level similarity measures, flagging rather than
deciding. Handling: semantic overlap across classes within a
topic group is not duplication; it is the paired design the
corpus requires (the same topic realized as human, AI, and
hybrid is how content confounds are controlled). Semantic
overlap inside the same cell without a declared topic group is
capped: a cell MUST NOT be dominated by restatements of one
underlying situation, and reviewers enforce a diversity
judgment recorded in the review.

### 8.4 Template duplicates

Definition: samples sharing a structural skeleton with slots
varied: same opening construction, same section rhythm, same
argument shape. This is the characteristic failure of batch
generation. Detection: structural fingerprinting (sentence
shape sequences, opening-construction comparison, section
pattern comparison) applied per submission batch and against
history. Handling: batches exhibiting template convergence are
returned to the contributor as a batch; individually acceptable
samples from a templated batch may be resubmitted in a later,
independently produced batch. Template screening is mandatory
for every AI generation run, whose economics make templating
the default outcome unless resisted.

### 8.5 Style duplicates

Definition: one authorial voice, human or configured model,
overrepresented in a cell, making the cell secretly a
single-author benchmark. Detection: production records (author
identity, generation session identity), supplemented by
authorial-similarity flags. Handling: hard caps recorded in the
coverage plan: no single human author above a fixed share of
any HUMAN cell, no single generation session or configuration
above a fixed share of any AI cell, with the shares set in the
coverage plan and enforced at acceptance. Mimicry pairs (a
model imitating a specific author) are the declared exception
and are capped separately.

### 8.6 Cross-release duplicates

Definition: similarity between a candidate and material in any
earlier release, including deprecated material and regression
entries. Handling: exact and near matches are rejected
(history does not reset); semantic matches follow 8.3. One
additional rule protects the split structure: a candidate
similar above threshold to any DEV sample MUST NOT enter
HIDDEN, and similarity to TEST blocks HIDDEN likewise, because
memorization of public material must never pay inside the
hidden evaluation.

---

## 9. Versioning

### 9.1 Release semantics

Corpus releases carry three-part versions with fixed meanings.
MAJOR: any change to the label taxonomy, scoring semantics,
field meanings, split rotation, or difficulty panel that alters
how existing material is interpreted. MINOR: additive change:
new samples, new categories, new vocabulary values, new
languages. PATCH: corrections: errata to metadata or labels,
documentation fixes. Releases are immutable and checksummed;
published results MUST cite the exact version; results from
different MAJOR versions are not comparable, and results from
different MINOR versions are comparable only on the
intersection of samples, which the release notes enumerate.

### 9.2 Label changes (errata)

Labels can change, rarely, and only through errata. Valid
triggers: an upheld provenance challenge, discovery of new
evidence, or discovery of a mechanical derivation error. The
invalid trigger is named explicitly because it is the one that
will be proposed most often: a detector, or all detectors,
disagreeing with a label is never grounds for changing it (P3);
if the evidence stands, the detectors are wrong, and that is
the benchmark working. Errata process: investigation by a
reviewer uninvolved in the original acceptance; decision signed
by two roles (Section 14); the prior label preserved in the
record's history with the evidence and reasoning; a PATCH
release with a public errata note; and a notice identifying
which previously published cell results the change touches.

### 9.3 Deprecation

Samples are deprecated, never deleted (P5). Valid grounds: the
stress target is proven obsolete across two consecutive MAJOR
releases (per the Benchmark Specification's deprecation rule),
or a provenance downgrade leaves the sample inadmissible in any
split. Deprecated samples remain in the distribution, frozen
and executable, excluded from headline metrics, and listed per
release. The Benchmark Specification's warning is restated
here as a rule: convenient categories will attract deprecation
proposals precisely when they embarrass current detectors, and
such proposals fail the validity test above by construction.

### 9.4 Redaction

Redaction is the emergency path for legal necessity or
irreducible PII discovered post-release. The text is removed;
the identifier, metadata, fingerprints for duplicate screening,
and a reason code remain as a tombstone; the event appears in
release notes. Redaction requires the governance quorum of
Section 14 and is expected to be rare enough that every
instance is individually explainable.

### 9.5 Identifier evolution

Identifiers are permanent, unique across all history including
rejections and redactions, and never reused or renamed. Split
membership, difficulty, and every other mutable fact live in
metadata, not in the identifier. Resubmitted or derived
material takes a new identifier with a lineage link. The
identifier namespace reserves room for new tracks, categories,
and buckets so expansion (Section 15) never forces renaming.

---

## 10. Hidden Benchmark Policy

### 10.1 Threat model

HIDDEN material leaks through four channels: contributors who
authored it, evaluation feedback that reveals it one bit at a
time, storage or transfer compromise, and error analysis that
quotes it. The policy addresses each channel; no single
safeguard is trusted alone.

### 10.2 Custody

HIDDEN text and its evidence live in storage separated from the
public corpus, with access restricted to named release
managers, every access logged, and any export of hidden text
requiring two release managers acting together. Reviewers see
hidden-eligible candidates during QA, before split assignment,
which is precisely why contributors and reviewers are never
told the eventual assignment.

### 10.3 Contributor separation

Split assignment happens after acceptance, by release managers,
using a randomized procedure within the coverage plan's
constraints. Contributors and reviewers are not told, and MUST
NOT be able to infer, where a sample landed. Contribution
agreements bind contributors not to republish or disclose their
submissions. Because authors necessarily know their own text,
the residual risk is handled by conflict declaration: any party
evaluating a detector on GAUNTLET MUST declare their
contributions and their organization's contributions, and cells
containing those samples are excluded from that party's
reported HIDDEN results. The corpus tracks contributor-to-cell
mappings for exactly this purpose.

### 10.4 Evaluation service protections

Evaluation against HIDDEN returns aggregate results only, never
per-sample outcomes, quantized to coarse precision, under a
per-period query budget per detector lineage, with a reporting
delay. All evaluation requests are logged. Submission patterns
consistent with probing (families of near-identical detector
variants, bisection-shaped request sequences) trigger review
and budget suspension. These controls exist because a hidden
set leaks through its scores: unlimited precise queries are an
oracle, and an oracle is a download.

### 10.5 Leak tracing

Each evaluating party's HIDDEN runs include a small number of
canary samples unique to that party, excluded from scoring.
Appearance of a canary's content anywhere outside the
evaluation pipeline identifies the leaking channel. Canaries
are rotated like everything else.

### 10.6 Rotation and incident response

At each MAJOR release, a slice of HIDDEN is promoted to TEST
and replaced with fresh material, so the hidden set never
ossifies and long-horizon overfitting decays. On suspected
leakage, the affected slice is immediately retired to TEST,
replacements are commissioned, and the incident is documented
in release notes: transparency about the leak, secrecy about
the content.

### 10.7 Communication discipline

HIDDEN samples are never quoted in issues, papers, talks, or
public error analyses. Internal error analysis on HIDDEN is
performed by maintainers under access logging and is
communicated outward only in aggregate or in paraphrase that
does not identify samples.

---

## 11. Contributor Workflow

### 11.1 Human contributors

Onboarding covers this specification, the contribution
agreement (rights, attestation duties, non-disclosure of
submissions), and the process-logging arrangement. Commissioned
writing follows Section 3.2: logging in place first, topics
assigned from the coverage plan's topic groups, environment
attested free of generative tools, compensation and consent
documented where applicable (notably for fairness cells and
author-mimicry pairings, which require explicit consent from
the imitated author). The contributor submits the text, the
evidence, and the draft metadata as one package.

### 11.2 AI-assisted contributors and generation operators

Anyone operating model generation or hybrid sessions is a
contributor whose duty is complete records: configurations,
prompts, raw outputs, intermediate states. Their submissions
are eligible for the AI, hybrid, and adversarial tracks only.
All model involvement of any kind MUST be declared per sample
(Section 3.1); "the model only helped a little" is a
declaration, not an exemption.

### 11.3 Maintainers

Maintainers run intake: they confirm packages are complete,
shepherd candidates through validation and screening, steward
the field and vocabulary registries, keep the coverage plan
current, and route review assignments respecting conflict
rules. Maintainers do not approve samples.

### 11.4 Reviewers

Reviewers execute Section 6 with its two mandates, participate
in calibration exercises, and record decisions with reasons.
Reviewers do not assign splits and are not told assignments.

### 11.5 Release managers

Release managers assign splits, hold HIDDEN custody, execute
errata, sign releases (two together), and operate the
evaluation service protections. Release managers do not create
or review samples in the same cycle they assign.

### 11.6 Violations

Discovery of undeclared model involvement in any contribution
quarantines all of that contributor's material pending
re-audit, reverses affected acceptances by errata, and is
recorded. The severity is deliberate: undeclared involvement is
the one contributor act that attacks P2 directly, and the
policy is calibrated to make it never worth the shortcut.

### 11.7 Small-team provision

While the project is small, one person may hold several roles,
but MUST NOT act in two conflicting roles on the same sample:
producer and reviewer; reviewer and adjudicator; contributor
and split assigner for cells containing their material. The
conflict pairs above are the minimum; the governance record
notes every same-person dual-role action so that scale-up can
audit the era honestly.

---

## 12. Acceptance Criteria

A sample enters the corpus only if every criterion below holds.
The list is conjunctive; there is no compensating excellence.

- **A-1.** Provenance tier is admissible for every split the
  sample could be assigned to, and the evidence package fully
  supports the tier.
- **A-2.** The label is mechanically derived from the evidence
  under Section 3, and the derivation was independently
  recomputed in provenance review.
- **A-3.** Class-specific artifacts are present and verified:
  generator records for model text, difference chains for
  hybrids, production-time span maps for mixed text, transform
  records and lineage for adversarial material.
- **A-4.** The metadata record passes validation, including
  every cross-field rule of Section 4.4.
- **A-5.** The exact word count falls inside the declared
  bucket.
- **A-6.** All six duplicate screens pass, or every flagged
  similarity is covered by a verified declared relationship.
- **A-7.** Decontamination screening against external corpora
  and the DEV split passes.
- **A-8.** PII status is established, and any scrubbing is
  documented in evidence.
- **A-9.** Rights are recorded and permit corpus distribution.
- **A-10.** The rationale is substantive and specific to the
  sample, and the target weakness maps to the failure-mode
  registry (or a registered proposal for a new entry).
- **A-11.** Two independent reviews are complete with no
  unresolved objections, agreement thresholds hold for the
  batch, and no conflict-of-interest rule was breached.
- **A-12.** All contributor declarations are on file, and no
  provenance challenge against the sample is open.
- **A-13.** Author-share and session-share caps for the target
  cell are respected after this sample's inclusion.

---

## 13. Rejection Criteria

A sample MUST NOT enter the benchmark, regardless of other
merits, if any of the following applies. Rejection reasons are
recorded against the permanent identifier.

- **X-1.** Any generative model involvement in text proposed
  for the HUMAN class (P2). This rejection is automatic, is not
  curable by editing or re-review, and triggers Section 11.6 if
  the involvement was undeclared.
- **X-2.** Provenance cannot be established at a tier
  admissible for any split, or the evidence package is
  incomplete after one revision cycle.
- **X-3.** The label's justification anywhere relies on
  stylistic judgment or on any detector or model output (P3).
- **X-4.** The sample is a found hybrid, a reconstructed
  process, or carries contributor-estimated origin shares
  (Section 3.4).
- **X-5.** The text changed after freeze.
- **X-6.** Undeclared similarity: any duplicate screen flag
  that the contributor cannot resolve into a declared
  relationship.
- **X-7.** The sample is famous, widely memorized, or
  previously published text presented as newly authored, or
  otherwise misrepresents its origin.
- **X-8.** PII cannot be reduced to clean, scrubbed, or
  synthetic status, or the sample contains real personal
  records (medical, financial, minors' data) in any form.
- **X-9.** Rights are unclear, disputed, or incompatible with
  distribution.
- **X-10.** Content exclusions: material whose inclusion the
  benchmark does not need and whose presence creates harm or
  legal exposure, including harassment of identifiable people,
  sexual content involving minors under any framing, and
  instructions enabling serious harm. Registers that
  legitimately require sensitive-looking material (medical,
  legal) use synthetic or licensed sources.
- **X-11.** The sample's only articulable virtue is defeating
  one specific detector implementation's bug, with no
  generalizable weakness statement. Such material belongs in
  that detector's own regression suite, not in the shared
  corpus; the corpus collects assumptions-level stressors.
- **X-12.** Accepting the sample would breach an author-share
  or session-share cap, or the sample was produced under a
  conflict-of-interest violation.

---

## 14. Governance

### 14.1 Authority matrix

- **Create samples:** any registered contributor, within the
  class rules of Section 3.
- **Approve samples:** two reviewers per Section 6, neither of
  whom produced the sample; final acceptance recorded by a
  maintainer confirming Sections 12 and 13.
- **Modify metadata before acceptance:** the contributor via
  revision, or a maintainer, always logged.
- **Modify metadata after release:** errata only: investigation
  plus sign-off by a release manager and one reviewer
  uninvolved in the original acceptance.
- **Change labels:** errata only, same two-role sign-off,
  evidence-based triggers only (Section 9.2). No individual can
  change a label alone, including the project lead.
- **Deprecate samples:** the same errata quorum, under the
  grounds of Section 9.3 only.
- **Delete samples:** no one. The nearest operations are
  deprecation and redaction, and redaction (Section 9.4)
  requires two release managers and a recorded legal or PII
  necessity.
- **Assign splits and create releases:** release managers, with
  releases signed by two of them.

### 14.2 Records

Every privileged action above lands in an append-only decision
record: action, sample or scope, actors by role, reasons, and
references to evidence. The decision record is part of the
corpus's audit surface and survives as long as the corpus does.

### 14.3 Amending this specification

Amendments are proposed in writing with rationale grounded in
benchmark science, undergo an open comment period among the
roles above, and take effect in a numbered specification
version noted in the next release. Amendments are prospective:
they govern future material and never silently reinterpret
existing labels or evidence; any amendment that would change
the meaning of existing material forces a MAJOR corpus version
with migration notes. The inviolable principles of Section 1.3
are amendable only by the full quorum of all active release
managers and reviewers, unanimously, with the reasoning
published; this bar is intentionally set at the level of
"effectively never".

---

## 15. Future Expansion

### 15.1 Design stance

The corpus expands by addition, never by rewriting. Anything
that would require reinterpreting existing samples, labels, or
evidence is by definition a MAJOR change with migration notes,
and the default answer to "can we just redefine..." is no.

### 15.2 New tracks and categories

Tracks and categories are registry entries. A new category MUST
arrive with: a definition, its axis coordinates, its cell plan
across length buckets, at least one entry in the failure-mode
registry stating what it stresses and which detector families
are expected to err, and its generation policy mapped to
Section 3. The identifier namespace already reserves room, so
no existing identifier ever changes.

### 15.3 New languages

Each language addition brings its own provenance pipeline
(archives predating generative availability in that language,
commissioned writing with native-speaker logging), native-
speaker reviewers meeting Section 6 requirements, per-language
duplicate thresholds (Section 8.2), and its own fairness cells.
A language is not "supported" by translation of existing
samples; translated material enters the translation categories
with translation recorded as process, exactly as the Benchmark
Specification defines.

### 15.4 New writing styles and registers

Registers join the domain and format vocabularies additively.
Every new register ships with calibrated duplicate thresholds
and a statement of its characteristic confounds, so screening
and review adapt without redesign.

### 15.5 New model families

Generator slots are registry entries; new families and versions
are added each cycle, old ones are never removed (aging
generators are a permanent measurement, not clutter), and the
held-out slot rotation continues so there is always a family
unseen by detectors under test. The evidence requirements of
Section 3.3 are generator-agnostic by construction.

### 15.6 New evaluation tasks

Task definitions (discrimination, involvement detection, origin
attribution, span localization, and future tasks such as
edit-fraction estimation or temporal attribution) are versioned
separately from the corpus. The corpus's obligation is richer
ground truth than any current task consumes: process taxonomy,
origin shares, span maps, full generation records, and lineage
exist precisely so that future tasks can be defined over
existing material without re-collection. When a new task needs
ground truth the corpus lacks, the gap is filled by new
collection under this specification, never by retroactive
annotation of process facts that were not recorded at
production time (Section 3.5's prohibition generalizes: process
facts are recorded when they happen or not at all).

### 15.7 Absorbing model progress

Three mechanisms keep the benchmark meaningful as generators
improve: difficulty re-estimation against re-versioned panels
(Section 7.4), the versioned tell-list machinery of the
Benchmark Specification (surface stereotypes are data with
expiry dates, never assumptions), and periodic human
re-baselining (fresh T1 human material on a fixed cadence,
because the human distribution drifts too). Progress that makes
old cells easy is recorded, not hidden; progress that makes
human cells look "more AI" is investigated as drift before
anyone concludes anything about detectors.

---

*End of specification. This document is normative for all
GAUNTLET corpus authoring activity. Where this document and
the Benchmark Specification conflict, the conflict is a defect:
file it, and the governance process resolves it explicitly
rather than by local interpretation.*
