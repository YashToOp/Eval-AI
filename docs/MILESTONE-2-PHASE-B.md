# Milestone 2, Phase B — Corpus Intake Infrastructure

**Scope:** R-05 … R-09. **Status:** complete, pending review.
**Commits:** `f7bdcdc` (R-05), `c552b39` (R-06), `db80d35` (R-07),
`370147a` (R-08), `529fb20` (R-09).

Governing documents: `docs/CORPUS_AUTHORING_SPEC.md` (CAS),
`docs/gauntlet-v1.0-spec.txt` (BS). Open items: `docs/TECHNICAL_DEBT.md`.

---

## 1. Phase B summary

Phase A built the machinery that reads a metadata record and says whether it
conforms. Phase B builds the machinery that decides whether a record is
*entitled* to exist: where its text came from, whether its numbers were
derived or typed, whether it may enter the class it claims, and whether it
duplicates or is contaminated by something already in the world.

| Item | Delivers | Module | Tests |
|---|---|---|---|
| R-05 | Evidence packages and chain of custody (CAS §5) | `evidence.py` (471) | 60 |
| R-06 | Mechanical derivation engines (CAS §3.4, §3.5, §4.2, §6.1) | `derive.py` (344) | 46 |
| R-07 | Candidate intake and the Generation Firewall (CAS §2, §3.1, X-1) | `intake.py` (257) | 23 |
| R-08 | Duplicate detection, six classes (CAS §8, P10, X-6) | `duplicates.py` (475) | 42 |
| R-09 | Decontamination screening (CAS §3.7, BS §4.9, §5.4, §9.1(d)) | `decontamination.py` (691) | 55 |

Full suite: **645 passed**, of which 226 are new in Phase B.

### What each item is really defending

**R-05 — evidence.** A provenance tier is a claim about custody, so the
package must support the tier it claims rather than merely accompany it.
`supported_tier()` computes the tier the evidence actually earns;
`derived_tier(base, transform_recorded)` returns T3 when no transform record
exists, because an unrecorded derivation is untraceable regardless of how
good its base was. `checksum_package()` never overwrites an existing checksum
— recomputing one would silently bless whatever the file has become.

**R-06 — derivation.** CAS §4.2 requires `ai_token_share` computed from the
diff chain, not estimated; §3.5 prohibits post-hoc span annotation; P1 makes
labels process-derived. All three come from **one** attribution pass over the
diff chain, so share, span map, and label cannot disagree with each other —
a class of inconsistency that no validator could have caught after the fact.
`derive_label` returns `None` where the chain does not determine a label
rather than guessing, and does not attempt to guess the *category*: §3.4 says
the editing instruction defines the category, and an instruction is not
mechanically classifiable.

**R-07 — the firewall.** The most important code in the repository. CAS X-1
says a P2 breach "is not curable by editing or re-review", so intake is the
only place it can be stopped. Three independent detectors (declared
involvement, evidence contradiction, metadata contradiction), because a
contributor may be honest, mistaken, or neither. The firewall runs *before*
evidence and metadata validation so a breached candidate never occupies a
VALIDATED-looking state a later approval could appear to bless. It reads
records and never the text: judging whether prose "looks generated" is
inadmissible under P3, and a firewall that did so would turn the corpus into
a detector's opinion of itself.

**R-08 — duplicates.** All six §8 classes over the complete corpus history.
The unifying rule is P10: designed relationships are declared, and undeclared
similarity is a defect rather than a coincidence — so the same similarity
number passes with a lineage record and fails without one. Semantic overlap
inside a topic group is the §8.3 paired design and is exempt; the same
overlap inside one cell is flagged.

**R-09 — decontamination.** 13-gram containment against the three BS §4.9
source classes, the §5.4 manifest block, and the §9.1(d) release gate. Its
one load-bearing rule is in §3 below.

---

## 2. Architecture changes

**No detector-side API was touched.** `_blend` and every other detector
internal are unchanged; the benchmark/detector separation is enforced by AST
guards (`tests/test_gauntlet_architecture.py`, 45 passing) in both
directions.

**Five new modules, no new dependencies.** Every module imports only
`findings` and, where genuinely needed, one another; `findings.py` still
imports nothing from the package. Phase B added no third-party dependency:
diffing is `difflib`, hashing is `hashlib`, shingling is a set of strings.

**Three extension points, each an interface rather than a stub.**

| Point | Interface | Waiting on |
|---|---|---|
| Semantic similarity (§8.3) | `duplicates.SemanticBackend` | an embedding model (TD-X06) |
| Reference corpora (BS §4.9) | `decontamination.ReferenceSource` | HC3/M4/RAID/… (TD-X01) |
| Exact overlap measurement | `ReferenceSource.max_contiguous_chars` | a source that can do it |

Each has a working, honestly-labelled default: the semantic backend reports
itself as `semantic_backend="lexical"` so no reader mistakes content-word
overlap for embeddings, and the contiguous-character measure records whether
it was measured or estimated.

**No orchestration was added.** Both screens are complete and callable, and
nothing calls them — see §5.

---

## 3. Test coverage summary

226 new tests. The ones that carry the phase:

**Absence is not a pass (R-09).** `test_a_scan_with_no_sources_is_incomplete_not_clean`
is the single most important assertion added in Phase B. Verdicts are ternary
(`CLEAN` / `CONTAMINATED` / `INCOMPLETE`) because a scan that could not consult
its corpora has not passed — it has not run. A hit is conclusive alone; a miss
is conclusive only when the scan was complete
(`test_a_hit_is_conclusive_even_when_the_scan_is_incomplete`). The manifest
block reports `status: "incomplete"` and carries no boolean `passed` field at
all (`test_manifest_block_never_claims_a_boolean_pass`).

**The firewall cannot be laundered (R-07).** Declared involvement, evidence
contradiction, and metadata contradiction each block independently; declaring
honestly still blocks HUMAN but is not double-penalised as an §11.6 violation;
rejection is terminal and the identifier cannot be resubmitted; and
`test_firewall_never_inspects_the_text` gives two candidates wildly different
prose with identical records and requires identical verdicts (P3).

**Derivation is checked against arithmetic, not against itself (R-06).** Shares
are asserted against hand-computed values (4 tokens, 1 model-written → exactly
0.25). Derived span maps are validated by the *validator's* tiling rule rather
than a reimplementation of it, so the two cannot drift into agreement.

**Self-matching is prevented (R-09).** A DEV sample screened against the DEV
split matches itself at 100% containment unless the index can exclude its own
contribution; `test_scanning_a_dev_sample_against_the_dev_split_does_not_match_itself`
pins that, and `DevSplitIndex` tracks per-n-gram ownership to make it O(n).

**Admitted weaknesses are regression-tested as weaknesses.**
`test_the_lexical_stand_in_misses_a_restatement_at_the_default_threshold`
records that the bundled semantic backend misses a restatement a reviewer
would catch. The honest response is a real embedding model (TD-X06), not a
lower threshold — a lower one would flag independent same-topic text too.
Tests that depend on an uncalibrated default now pass the threshold
explicitly, so no test encodes an admitted unknown as a green check.

---

## 4. Technical debt updates

`docs/TECHNICAL_DEBT.md`: 43 rows — 3 RESOLVED, 5 DONE, 11 OPEN, 13 DEFERRED,
5 BLOCKED, 6 external dependencies.

**Closed:** TD-D01 → `f7bdcdc`, TD-D02 → `c552b39`, TD-D03 → `db80d35`,
TD-D04 → R-08, TD-D05 → R-09 (machinery only; see below).

**Correction:** TD-D01/D02/D03 should have moved to DONE in their own commits
per the register's stated maintenance rule and did not. They were closed
retroactively in the R-08 commit with their implementing hashes. The lapse is
recorded here rather than quietly fixed.

**Opened:**

| ID | Type | Question |
|---|---|---|
| TD-A04 | Interpretation, applied | Severity of an incomplete decontamination scan: warns at candidacy (CAS §3.7), errors at release (BS §9.1(d)). |
| TD-G09 | Governance | §8.5 share-cap denominator: the cell's current population or its planned target? |
| TD-G10 | Governance | BS §4.9 sets no threshold on the containment *ratio*, only the 50-character rule. |
| TD-D18 | Deferred | CAS §2 Stage 5 orchestration and durable "on hold" records. |
| TD-X06 | External | A sentence-embedding model for §8.3 semantic screening. |

Two of these are places where a literal reading is unimplementable and the
implementation says so instead of choosing quietly:

- **TD-G09.** "No single author may dominate a cell" taken against the cell's
  current population rejects every cell's *first* sample — one sample is 100%
  of a one-sample cell — which makes the corpus unbuildable. Caps are enforced
  only once a cell holds `ceil(1/cap)` samples, and
  `SHARE_CAP_NOT_YET_ENFORCEABLE` is emitted below that so the gap is never
  silent. The denominator is a governance decision; the interim rule is meant
  to be *replaced*, not tuned.
- **TD-A04.** Erroring on an incomplete decontamination scan at candidacy has
  the same shape: with TD-X01 unresolved, no candidate could ever be
  submitted. The severity split follows from which document is speaking, and
  contamination actually *found* blocks at both stages.

**Unchanged and untouched by instruction:** TD-G01 (BS §9.1(h) vs §6.2
coverage) and TD-G02 (Track U numbering). Neither was implemented around.

---

## 5. Risks before Phase C

**1. Nothing calls the screens (highest).** R-08 and R-09 are complete and
have no caller. CAS §2 Stage 5 is the VALIDATED → SCREENED transition and is
where both belong, but a Stage 5 desk needs a durable record for a candidate
placed *on hold* — §2 is explicit that undeclared similarity "places the
candidate on hold pending contributor explanation", not that it rejects it,
and rejection in this system is terminal (§6.4). Two things are missing: the
§14.1 authority matrix is a closed vocabulary with no screening action, and
the lifecycle log has no non-transition note event. Inventing either would
have been a governance decision taken by code. Registered as TD-D18.

**2. Every decontamination scan is `INCOMPLETE` today.** That is the correct
report, not a bug, but it means BS §9.1(d) cannot pass for any release until
TD-X01 resolves. This is a hard release blocker with an infrastructure
dependency, not an engineering one.

**3. Duplicate thresholds are uncalibrated.** Every screen result carries
`thresholds_calibrated=False` and emits `THRESHOLDS_NOT_CALIBRATED`.
Calibration needs known-independent same-register text (TD-G05), which the
project does not have.

**4. Phase C reviews material that has never been screened.** Review (R-10)
sits after Stage 5. Until TD-D18 lands, reviewer effort would be spent on
candidates that may be duplicates or contaminated — the precise waste CAS §3.7
exists to prevent.

**5. The corpus is still empty.** Every module in Phases A and B has been
tested against constructed fixtures. No real sample has passed through intake,
and TD-B05 (fixture migration) is blocked behind exactly the machinery Phase B
just built.

---

## 6. Recommendations for entering Phase C

1. **Land TD-D18 before R-10.** Stage 5 orchestration is a prerequisite for
   review being worth anything, and it is small once the hold-record question
   is answered. It needs one governance answer first: where a hold is
   recorded, given that §14.1 is closed. Recommend adding a `screen` action to
   the authority matrix (a governance edit, not a code one) rather than
   overloading `modify_metadata_pre_acceptance`.
2. **Answer TD-G09 and TD-G10 together.** Both are "the spec states a rule
   with no number". Both currently ship an explicit report of the gap. Both
   become one-line config changes once answered.
3. **Treat TD-X01 as the release-critical path.** It blocks §9.1(d)
   permanently, and acquiring licensed corpora has lead time that engineering
   work does not.
4. **Migrate the fixture corpus early in Phase C (TD-B05).** It is the first
   end-to-end exercise of intake → derivation → evidence → screening, and it
   will find integration defects that unit fixtures cannot. It must go in as
   T3/DEV/noisy through the normal lifecycle, with the §3.2
   memory-transcription caveat recorded in each rationale.
5. **Do not build the review workflow against constructed fixtures alone.**
   Kappa gating, adjudication, and COI routing are all judgements about real
   disagreement; fixtures can prove the mechanics but not the thresholds.
