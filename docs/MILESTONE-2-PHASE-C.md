# Milestone 2, Phase C — Review, Acceptance, and the Stage 5 Gap

**Scope:** TD-D18, TD-B05, R-10, R-11. **Status:** complete, pending review.
**Commits:** `55cf6cd` (TD-D18), `2856390` (TD-B05), `09c6b0f` (R-10),
`36bcd8b` (R-11).

Worked unattended at the user's direction, under the standing rule: skip
anything that hits a governance wall, finish everything else in full, and
report exactly what was left out.

Governing documents: `docs/CORPUS_AUTHORING_SPEC.md` (CAS),
`docs/gauntlet-v1.0-spec.txt` (BS). Open items: `docs/TECHNICAL_DEBT.md`.

---

## 1. Summary

| Item | Delivers | Module | Tests |
|---|---|---|---|
| TD-D18 | Stage 5 orchestration, VALIDATED → SCREENED | `screening.py` (215) | 27 |
| TD-B05 | Fixture migration attempted and refused | — | 33 |
| R-10 | Reviewer QA, Stage 6 (CAS §6) | `review.py` (721) | 64 |
| R-11 | Acceptance gate, Stage 7 (CAS §12/§13) | `acceptance.py` (623) | 44 |

Full suite: **816 passed**, of which 168 are new in Phase C. The lifecycle now
runs unbroken from IDEA to ACCEPTED.

---

## 2. The one finding that changes a plan

**The demo fixture corpus can never enter GAUNTLET, and the register's
previous analysis of it was wrong.**

TD-B05 had the 36-sample fixture scheduled for migration into DEV as
T3/noisy, blocked only on intake existing. Intake exists now, so the migration
was attempted. Neither half survives:

- **The human half is model-produced text.** Its own metadata says "excerpt
  transcribed from memory" — a model reconstructing public-domain prose, not
  a person transcribing a book. Declared honestly, that is model involvement
  in a HUMAN-labelled candidate: P2, with X-1 making the rejection automatic
  and not curable. The earlier note called this a §3.2 sourcing blemish that
  "T3+DEV tolerates". That conflated two different bars — T3 and DEV lower the
  *provenance* requirement, and P2 is not a provenance rule but a §1.3
  inviolable principle. Recollection is separately inadmissible under §5.4.
- **The AI half has no generation record.** Genuinely model-authored, so the
  label is right, but nothing captured model version, provider, prompt,
  decoding parameters, seeds, or raw response. BS §4.2 requires T1 for all AI
  categories by construction and does not scope that to TEST/HIDDEN, so DEV
  does not rescue it. Writing the record now would be fabricated evidence.

Kept as 33 tests rather than a paragraph, so the conclusion is verifiable and
the fixture cannot later be admitted by relaxing a check. TD-B05 is now
**CLOSED — will not do**.

Two of those tests document the machinery's *limits* rather than its
strengths, which matters more than the refusal itself:

- At T3, with no declaration and no evidence, nothing mechanical stands in the
  way. The firewall finds no generation session, and an empty package is what
  T3 *means*. That is inherent, not a fixable gap: involvement cannot be
  detected from records that do not exist, and detecting it from the text is
  inadmissible under P3. The guarantee comes from the binding declaration and
  §11.6 quarantine, contained by T3 implying DEV. **The fixture is refused
  because the truthful declaration blocks it, not because a validator caught a
  lie.**
- A complete-looking generation record for a session that never happened
  passes every structural check. The bar against it is conduct plus §5.6 spot
  verification and BS §4.4 audit-time regeneration — not `validate_package`.

**Consequence for planning:** GAUNTLET has no starting material. Human cells
need T0 archive records or T1 commissioned writing under logging; AI cells
need real captured generation sessions. That is an acquisition problem, and it
is now on the critical path ahead of any further engineering.

---

## 3. Architecture changes

**Three new modules; still no new dependencies and no detector-side change.**
The AST guards pass in both directions (46 tests).

**Stage 5 (TD-D18) turns on a distinction the specification makes and the
earlier code did not.** §8.2 says an undeclared near-duplicate "holds pending
explanation" and §8.4 returns a templated batch to its contributor — but
rejection here is terminal (§6.4) and burns the identifier forever (§9.5).
Routing a hold through rejection would destroy candidates the specification
expects back explained. So Stage 5 has three dispositions:

| Disposition | State | Trigger |
|---|---|---|
| ADVANCED | → SCREENED | no errors; warnings ride along to reviewers |
| HELD | stays VALIDATED | any error; re-screenable once explained |
| REJECTED | terminal | §8.1 exact duplication only |

A held candidate advancing once its lineage is declared is tested directly —
that is what makes a hold a hold rather than rejection under another name.

**One governed vocabulary was extended, with the user's explicit approval.**
§14.1's authority matrix listed no screening authority, but §14.2 requires
every privileged action to land in the decision record and a Stage 5 hold is
exactly that. `screen` was added to the ledger's closed action vocabulary
rather than overloading `modify_metadata_pre_acceptance`, which would have
filed a screening hold as a metadata edit. Recorded as TD-G11 for the next
numbered spec version, per §14.3's prospective-amendment rule.

**Judgment fields moved into governed data.** Neither specification enumerates
which registry fields are "judgment fields" — BS §4.6 names them by
description. `review.py` reads the mapping from `field_registry.json`, which
carries it as an interpretation with `status: proposed` (TD-G12), rather than
hardcoding a governance decision in a module.

---

## 4. Test coverage summary

168 new tests. The load-bearing ones all defend the same property in different
places: **absence of a result is not a passing result.**

| Where | Assertion |
|---|---|
| R-10 | `test_an_undefined_kappa_does_not_pass` — Cohen's kappa is 0/0 when both reviewers used one category throughout. Returning 1.0 would rank the least informative batch as the best one. Returns `None`; the gate says "unmeasured agreement, not passing agreement". |
| R-10 | `test_a_field_never_dual_annotated_is_reported_as_such` — silence about a judgment field is not agreement on it. |
| R-11 | `test_an_empty_dossier_confirms_nothing_and_accepts_nothing` — every criterion UNCONFIRMED, none PASS. |
| R-11 | `test_an_unconfirmed_criterion_blocks_exactly_like_a_failed_one` — §12 is conjunctive; no compensating excellence. |
| TD-D18 | `test_two_identical_candidates_in_one_batch_do_not_both_pass` — an advanced candidate joins the live candidate pool, so the second is screened against the first. |

Others worth naming:

- `test_a_reviewer_sees_nothing_before_the_round_is_complete` — §6.1
  independence is enforced by withholding. Instructing reviewers not to peek
  would record a promise; withholding records a property.
- `test_an_appearance_opinion_in_a_provenance_review_is_a_defect` — §6.1 says
  such opinions are inadmissible under P3 *and* that their appearance is
  itself a defect. `Review.appearance_opinion` exists so recording one is
  detectable; a schema with nowhere to put it would push the same opinion into
  free-text notes where nothing can see it.
- `test_confirmations_cannot_rescue_a_mechanical_failure` — §13 disqualifies
  regardless of other merits, and a human assertion about a computed fact is
  not evidence.
- `test_kappa_uses_both_raters_marginals_not_one` — added after my own
  hand-computed value in a test docstring turned out wrong (the implementation
  was right); this one pins the asymmetric case that would have caught it.

---

## 5. Technical debt updates

49 rows — 3 RESOLVED, 8 DONE, 16 OPEN, 12 DEFERRED, 4 BLOCKED, 1 CLOSED,
6 external dependencies.

**Closed:** TD-D06 → R-10, TD-D07 → R-11, TD-D18 → `55cf6cd`,
TD-B05 → will not do.

**Opened:**

| ID | Type | Question |
|---|---|---|
| TD-G11 | Governance | `screen` added to the §14.1 action vocabulary — ratify in the next spec version. |
| TD-G12 | Governance | Which registry fields are judgment fields (BS §4.6 names them only by description). |
| TD-G13 | Governance | No minimum batch size for kappa; a kappa over three items is noise. |
| TD-G14 | Governance | §6.6's "repeatedly", "periodic", and §6.2's "senior" all carry no definition. |
| TD-G15 | Governance | BS §4.6 says kappa per **release**; CAS §6.3 says per **batch**. Not the same test. |
| TD-D19 | Deferred | No declared-interest register exists, so §6.6's detector-COI arm cannot run. |
| TD-D20 | Deferred | No provenance-challenge register exists, so A-12 cannot pass mechanically. |

**TD-G15 is the one to look at first.** A release contains many batches, so
"every batch passed" and "the release passed" are different claims. CAS
governs per the TD-A01 precedent, so `agreement()` computes per batch, and a
release-level roll-up is a separate computation that passing every batch does
not imply. Which unit gates a release is a governance call.

**Eight of the 25 acceptance criteria cannot pass mechanically today:**
A-9/X-9 (rights vocabulary, TD-G06), A-13/X-12 (share caps have no numbers,
TD-G04), A-12 (no challenge register, TD-D20), and X-7/X-10/X-11 which are
judgment by nature and need a recorded confirmation with a basis. Each reports
*why*, not merely that it is unconfirmed.

---

## 6. Risks before Phase D

1. **There is no material (highest, and newly confirmed).** §2 was the
   assumption Phase C removed. The lifecycle is complete and empty, and the
   only corpus in the repository has now been proven inadmissible. Acquisition
   — archives, commissioned writing under logging, captured generation
   sessions — is ahead of engineering on the critical path.
2. **Every decontamination scan is still `INCOMPLETE` (TD-X01).** BS §9.1(d)
   cannot pass for any release. Unchanged from Phase B; still infrastructure,
   not engineering.
3. **Agreement thresholds are untested against real annotators.** Kappa
   arithmetic is verified against hand-computed values, but whether 0.8 is
   reachable on these judgment fields with real reviewers is unknown, and
   TD-G13 means a small batch can pass on noise.
4. **Governance backlog is now the largest single blocker.** 16 OPEN
   specification items, four opened this phase. Several are one-line config
   changes once answered, but code cannot answer them.
5. **Phase D (split assignment, release building) inherits TD-B01.** HIDDEN
   custody remains unimplementable inside a public repository, and split
   assignment is where that becomes load-bearing rather than theoretical.

---

## 7. Recommendations for Phase D

1. **Treat corpus acquisition as a work item, not a precondition.** It needs
   its own plan: which archives, under what licence, and who does the T1
   commissioning under what logging tool (TD-X05 is still unprovisioned).
2. **Answer the four cheap governance items in one pass** — TD-G04 (share cap
   numbers), TD-G06 (rights vocabulary), TD-G13 (minimum batch size), TD-G14
   ("repeatedly"). Each unblocks a criterion that currently cannot pass and
   each is a number or a list.
3. **Resolve TD-G15 before building the release gate**, since it determines
   what the release gate computes.
4. **Do not start Phase D's split assignment until TD-B01 has an answer.**
   §10.3 contributor blinding and §10.2 HIDDEN custody are both
   infrastructure decisions, and building assignment machinery against a
   public repository would bake in the wrong assumption.
5. **Build the two missing registers (TD-D19, TD-D20) with the review
   workflow, not after it.** Both are small, both currently degrade a check to
   a warning, and both are easy to forget once review looks finished.
