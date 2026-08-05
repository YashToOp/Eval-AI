# GAUNTLET Technical Debt Register

**Status:** Authoritative. This document is the single source of truth for
deferred work, blocked features, external dependencies, and open governance
decisions across the GAUNTLET corpus system.

**Maintenance rule:** every deferral, blocked feature, or open question
introduced by any milestone is recorded here in the same commit that
introduces it. An item is closed only by moving its status to `RESOLVED` (with
the ratifying decision) or `DONE` (with the implementing commit) — rows are
never deleted, mirroring the corpus's own "nothing is deleted" principle
(CAS P5).

**Owners:** *Specification* (a normative decision is required before code can
be correct), *Engineering* (code work, unblocked), *Infrastructure* (needs a
resource or environment the repository does not currently have).

**Risk:** Low / Medium / High / Critical, judged by the damage if the item
ships unaddressed — for a benchmark, that damage is measured as a threat to
label validity, integrity, or reproducibility (CAS §1.1), not to convenience.

Governing documents: `docs/CORPUS_AUTHORING_SPEC.md` (CAS),
`docs/gauntlet-v1.0-spec.txt` (BS). Roadmap item codes (R-01…R-18) refer to
`docs/` roadmap and the Milestone 2 Phase A note.

---

## Summary by status

| Status | Count |
|---|---|
| RESOLVED (ratified interpretation) | 3 |
| DONE (implemented) | 7 |
| OPEN — Specification decision required | 16 |
| DEFERRED — scheduled to a later phase | 12 |
| BLOCKED — needs an external resource | 4 |
| CLOSED — will not do | 1 |

Cross-cutting: several DEFERRED items are also BLOCKED; the `Depends on` column
names the blocker.

---

## A. Ambiguities and canonical interpretations

| ID | Description | Affected | Blocking milestone | Risk | Owner | Proposed resolution | Status |
|---|---|---|---|---|---|---|---|
| TD-A01 | `expected_confusions` requiredness: BS §4.7 omits it, BS §5.2 is silent, CAS §4.2 says "optional but recommended". | `registry.py`, field registry | none | Low | Specification | CAS governs; field is OPTIONAL. Recorded in registry `interpretations` and Phase A doc. | **RESOLVED** 2026-08-05 |
| TD-A02 | Generator record for `HUMAN_AI_EDITED` and other model-involved labels. Milestone 1 exempted `HUMAN_AI_EDITED` in error. | `validate.py` generator rule | none | Medium | Specification | Required for EVERY non-HUMAN label; only pure HUMAN exempt. Corrected with a regression test. | **RESOLVED** 2026-08-05 |
| TD-A04 | Severity of an incomplete decontamination scan. CAS §3.7 screens at candidacy "so contaminated material [does not consume] review effort"; BS §9.1(d) makes the scan a release acceptance criterion. Neither says what happens when the reference corpora are unavailable. | `decontamination.py` `Stage` | Phase B (R-09) | Medium | Specification | An incomplete scan WARNS at candidacy and ERRORS at release. Erroring at candidacy would make the corpus unbuildable while TD-X01 is unresolved — no candidate could be submitted. Contamination actually found blocks at both stages, and the verdict is `INCOMPLETE` in both. Recorded rather than assumed; ratify or overrule. | **OPEN** |
| TD-A03 | Whether newer schema fields (e.g. `lineage`) retroactively invalidate older records. | `registry.py`, `validate.py` version arithmetic | none | Medium | Specification | Schema requirements are version-specific; v1 records validated under v1, v2 under v2. Newer fields never invalidate older records. | **RESOLVED** 2026-08-05 |

---

## B. Open governance decisions (Specification-owned, do not implement around)

| ID | Description | Affected | Blocking milestone | Risk | Owner | Proposed resolution | Status |
|---|---|---|---|---|---|---|---|
| TD-G01 | BS §9.1(h) requires every category to have a §6.2 failure-mode entry, but §6.2 covers only 66 of 99 categories. §9.1(h) cannot pass as written; CAS §15.2 also now requires an FM entry for every new category. | `failure_modes.json`, `validate.py` (9.1h), release gate | Corpus release (any) | High | Specification | Either extend §6.2 to the 33 uncovered categories or narrow §9.1(h). Validator reports `NO_FAILURE_MODE_ENTRY` and does not work around it. | **OPEN** |
| TD-G02 | Track U (§3.7) is described without numbered categories, but §5.2 ids require a category and §9.5 makes ids permanent. `U-01` is a harness placeholder. | `categories.json`, id scheme | Any Track U authoring | High | Specification | Assign the official Track U category numbering before any U sample is registered; ids cannot be renamed later. | **OPEN** |
| TD-G03 | Reference panel composition for empirical difficulty (§7.2 requires ≥1 each of DF1–DF4; §14 makes it a governed decision). | `difficulty` system (R-16) | Phase E (R-16), first release | High | Specification | Governance selects and versions the panel; document members well enough to rerun. | **OPEN** |
| TD-G04 | Author-share and session-share caps (§8.5, enforced at acceptance per A-13, X-12) have no numeric values. | Coverage plan (R-12), acceptance gate (R-11) | Phase D (R-12) | Medium | Specification | Set per-cell caps in the coverage plan; caps drive `A-13` enforcement. | **OPEN** |
| TD-G05 | Per-register near-duplicate thresholds (§8.2) are undefined; thresholds must be calibrated per register. | Duplicate detection (R-08) | Phase B (R-08) | Medium | Specification | Calibrate thresholds on known-independent same-register text; ship in the coverage/screening config. | **OPEN** |
| TD-G06 | License/rights vocabulary (§4.2: "unknown" is not a value) is not enumerated. | `registry.py` (license field), acceptance A-9 | Phase C (R-11) | Medium | Specification | Define the closed rights vocabulary; add to the field registry. Currently only non-empty is checked. | **OPEN** |
| TD-G07 | Per-panel-member decision-threshold / margin definitions for the D1–D5 empirical rules (§7.3). | Difficulty system (R-16) | Phase E (R-16) | Medium | Specification | Document each panel member's threshold and "high margin" definition alongside the panel version. | **OPEN** |
| TD-G08 | X-12 MT label policy: default (`HUMAN_AI_EDITED`, transform=MT) is in force and recorded in `manifest.json`, but the manifest note is a placeholder pending explicit ratification. | `manifest.json` policy block, `categories.json` X-12 | Any X-12 authoring | Low | Specification | Ratify the default or record a different decision in the manifest. Default currently applied per §3.3. | **OPEN** |
| TD-G09 | §8.5 share-cap denominator. "No single author or session may dominate a cell" is a share, but of what: the cell's *current* population, or its *planned target* size? Against the current population every cell's first sample is 100% of it, so a literal reading rejects every cell's first sample and makes the corpus unbuildable. | `duplicates.py` `_style_caps`, coverage plan (R-12), acceptance A-13 | Phase D (R-12) | Medium | Specification | Interim: caps are enforced only once the cell holds `ceil(1/cap)` samples, below which the cap cannot be satisfied by any submission; `SHARE_CAP_NOT_YET_ENFORCEABLE` is emitted so a pass is never silent. Governance should state the denominator, at which point the interim rule is replaced rather than tuned. | **OPEN** |

| TD-G10 | BS §4.9 mandates 13-gram containment checks but states a numeric rule only for the contiguous 50-character overlap; there is no threshold on the containment *ratio*. | `decontamination.py` `ScreenConfig` | Any release scan | Medium | Specification | No ratio threshold is invented: `containment_review_threshold` is `None`, ratios are measured and reported per source, `CONTAINMENT_THRESHOLD_UNSET` marks the gap, and only the stated 50-character rule decides. Governance sets the value (or confirms none is wanted). | **OPEN** |
| TD-G11 | §14.1's authority matrix lists no screening authority, but §14.2 requires every privileged action to land in the decision record, and a Stage 5 hold is a consequential, contestable decision about a sample. | `ledger.py` `PRIVILEGED_ACTIONS`, `screening.py` | none (implemented) | Low | Specification | `screen` was added to the closed action vocabulary by explicit project decision rather than overloading `modify_metadata_pre_acceptance`, which would file a screening hold as a metadata edit. Screening is mechanical, so the actor is `system`. §14.3 makes this a prospective amendment to record in the next numbered spec version. | **OPEN** |
| TD-G12 | Which registry fields are "judgment fields". BS §4.6 names them by description ("register tags, difficulty, PII checks, quality screening") and CAS §6.3 requires kappa per field; neither enumerates them. | `field_registry.json` `judgment_fields`, `review.py` | Any review batch | Medium | Specification | Applied interpretation recorded in the registry's `interpretations` block with `status: proposed`: categorical = domain, format, language, difficulty, pii_status, noisy_label; free-text = rationale, target_weakness. Kappa is computed only for the categorical set — prose agreement is not a kappa. Ratify or amend. | **OPEN** |
| TD-G13 | No minimum batch size for kappa. §6.3 requires kappa ≥ 0.8 per field per batch; a kappa over three items is noise. | `review.py` `agreement_gate` | Any review batch | Medium | Specification | No minimum is invented. Item counts accompany every result and `KAPPA_BATCH_SIZE_NOT_GOVERNED` is emitted on every gate run so a small-n pass is never read as a strong one. | **OPEN** |
| TD-G14 | §6.6 retrains a reviewer who misses seeded defects "repeatedly" but sets no number. | `review.py` `ReviewerRecord` | Reviewer onboarding | Low | Specification | `retraining_report(threshold)` takes the number; with none supplied it reports the record and says the rule cannot be applied mechanically. Also unset: the exercise period ("periodic") and what makes a reviewer "senior" enough to adjudicate (§6.2). | **OPEN** |
| TD-G15 | Kappa unit mismatch: BS §4.6 and §9.1(c) say "per field per release", CAS §6.3 says "per field per batch". A release contains many batches, so the two gates are not the same test. | `review.py`, release gate (R-11) | First release | Medium | Specification | CAS governs per the TD-A01 precedent, so `agreement()` computes per batch. A release-level roll-up is a separate computation and is not implied by passing every batch. Ratify which unit gates a release. | **OPEN** |
---

## C. Deferred implementation (Engineering-owned, scheduled)

| ID | Description | Affected | Blocking milestone | Risk | Owner | Depends on | Status |
|---|---|---|---|---|---|---|---|
| TD-D01 | Evidence package schemas and chain of custody (R-05, CAS §5). Currently only `provenance_ref` path resolution exists; nothing validates what it points to. | new module | Phase B | High | Engineering | — | **DONE** `f7bdcdc` (R-05) |
| TD-D02 | Mechanical derivation engines (R-06): recompute `ai_token_share` from diff chains, verify span tilings against production records, replay diff chains. Hand-entered shares are currently accepted if internally coherent. | new module, `validate.py` | Phase B | High | Engineering | TD-D01 | **DONE** `c552b39` (R-06) |
| TD-D03 | Candidate intake with the Generation Firewall (R-07, P2/X-1): structural rejection of any model-involved candidate targeting HUMAN, contributor declaration capture, freeze execution wired to lifecycle. | new module, `lifecycle.py`, `ledger.py` | Phase B | Critical | Engineering | TD-D01 | **DONE** (R-07) `db80d35`; contributor-side process logging still TD-X05 |
| TD-D04 | Duplicate detection, six classes (R-08, §8). | `duplicates.py` | Phase B | High | Engineering | TD-G05, TD-D09 | **DONE** (R-08); thresholds uncalibrated (TD-G05), semantic backend is a lexical stand-in (TD-X06), share caps interim (TD-G09) |
| TD-D05 | Decontamination screening (R-09, §3.7, BS §4.9): 13-gram containment vs external corpora and DEV. | `decontamination.py` | Phase B | High | Engineering | TD-X01 | **DONE** (R-09) — machinery, source interface, manifest block and §9.1(d) gate built; no external corpus is attached, so every scan is `INCOMPLETE` until TD-X01 resolves |
| TD-D06 | Review workflow (R-10, §6): dual review, kappa ≥ 0.8 gating, adjudication, calibration exercises, COI routing. | `review.py` | Phase C | High | Engineering | — | **DONE** (R-10); residuals TD-G12 (judgment-field set), TD-G13 (kappa batch size), TD-G14 ("repeatedly"), TD-D19 (declared-interest register) |
| TD-D07 | Acceptance gate (R-11, §12/§13): mechanize A-1…A-13 where possible, explicit recorded confirmations otherwise. | new module | Phase C | High | Engineering | TD-D01…D06 | DEFERRED |
| TD-D08 | Coverage plan artifact (R-12, §8.5, A-13): cell targets, share caps, topic-group registry. | new module/data | Phase D | Medium | Engineering | TD-G04 | DEFERRED |
| TD-D09 | Split assignment with contributor blinding (R-13, §10.3): randomized release-manager assignment, contributor-to-cell mapping. | new module, `ledger.py` | Phase D | High | Engineering | TD-B04 | DEFERRED |
| TD-D10 | Release builder and post-release workflows (R-15, §9): immutable checksummed releases, semver enforcement, errata, deprecation, redaction tombstones. | new module, `lifecycle.py`, `ledger.py` | Phase D | High | Engineering | — | DEFERRED |
| TD-D11 | Difficulty system (R-16, §7): provisional assigner, versioned panel, empirical D1–D5, re-estimation. | new module | Phase E | Medium | Engineering | TD-G03, TD-G07, TD-X02, TD-X03 | DEFERRED |
| TD-D12 | Regression execution and CI tiers (R-17, BS §8, P9): execute §8.3 entries, expected-fail lifecycle, Tier 1/2/3, golden-vector drift alarms (BS §8.7), metamorphic/invariance suite (BS §8.5). Schema documented in `regression/README.md`; nothing executes it. | new module, CI | Phase E | High | Engineering | TD-D10 | DEFERRED |
| TD-D13 | Evaluation service protections (R-18, §10.4): aggregate-only HIDDEN, query budgets, canary exclusion, probe detection. | new service | Phase E | High | Engineering | TD-B01 | DEFERRED |
| TD-D14 | Runner tasks T3 (origin attribution) and T4 (span localization) raise `NotImplementedError` rather than return a number. | `runner.py` | Phase E | Medium | Engineering | — | DEFERRED |
| TD-D15 | Calibration and confidence reporting (BS §9.3b/c): ECE, Brier, Track U overconfidence, leakage index (§9.3e), G9 held-out delta (§9.3f), transform curves (§9.3g). Constants present in `spec.py`; reporting not built. | new module beside `runner.py` | Phase E | Medium | Engineering | — | DEFERRED |
| TD-D16 | `validate_relationships` checks a `supersedes` target exists but not that it is in the REJECTED/superseded state; that cross-references the identifier registry. | `validate.py`, `lifecycle.py` | Phase B (R-07) | Low | Engineering | TD-D03 | DEFERRED |
| TD-D18 | CAS §2 Stage 5 orchestration (VALIDATED → SCREENED): run the R-08 duplicate screen and the R-09 decontamination screen, and place a candidate *on hold* — not rejected — on an undeclared similarity or a contamination hit. | `screening.py`, `ledger.py` | Phase C | Medium | Engineering | — | **DONE**. `screen` added to the §14.1 action vocabulary by explicit project decision (see TD-G11); holds live in the decision ledger, so no lifecycle note event was needed. Three dispositions: ADVANCED / HELD (stays VALIDATED, re-screenable) / REJECTED (§8.1 only). |
| TD-D19 | Declared-interest register (§6.6): reviewers with a declared interest in a detector under evaluation on the affected cells MUST NOT review. `check_reviewer_eligibility` takes the register as a parameter and warns `DECLARED_INTERESTS_NOT_SUPPLIED` when absent, but the project keeps no such register and has no process for declaring an interest. | new data + process | Phase C | Medium | Infrastructure | TD-D06 | DEFERRED |
| TD-D17 | Retrofit `tests/test_review_regressions.py` (pre-GAUNTLET detector regressions) to the §8.3 record schema. Currently satisfies §8.1 in spirit but lacks `bug_id`/`expected_behavior`/`status`. | `tests/`, `regression/` | Phase E (R-17) | Low | Engineering | TD-D12 | DEFERRED |

---

## D. Blocked features (Infrastructure-owned)

| ID | Description | Affected | Blocking milestone | Risk | Owner | Proposed resolution | Status |
|---|---|---|---|---|---|---|---|
| TD-B01 | HIDDEN custody (§10.2) is unimplementable inside a public repository: HIDDEN text and evidence must live in access-controlled storage separate from the public corpus, with two-release-manager export. | R-14, R-18, split assignment | Phase D | Critical | Infrastructure | Provision a private, access-logged store outside the public repo before any HIDDEN material exists. Until then, no sample may be assigned HIDDEN. | **BLOCKED** |
| TD-B02 | Difficulty empirical panel needs model access: DF1 (likelihood) requires `torch`/`transformers`; DF4 (judge) requires an LLM API. Neither ships in the current environment. | R-16 difficulty | Phase E | Medium | Infrastructure | Provision the panel runtime; provisional-difficulty machinery can be built without it (TD-D11 splits accordingly). | **BLOCKED** |
| TD-B03 | Decontamination and reference-difficulty need external public detection corpora (HC3, RAID, M4, MGTBench, GPT-2 output corpus) with their licenses. | R-09, R-16 | Phase B | High | Infrastructure | Acquire and license the external corpora; record checksums in the manifest. | **BLOCKED** |
| TD-B04 | Contributor-blinding of split assignment (§10.3) is only as strong as repository access separation; in a single public repo, assignment records are visible. | R-13 split assignment | Phase D | High | Infrastructure | Decide where assignment records physically live (private store, TD-B01) so contributors cannot infer assignments. | **BLOCKED** |
| TD-B05 | Fixture migration. **The original analysis was wrong and the migration is impossible.** Attempted once intake existed; neither half is admissible. *Human half:* its own metadata says "transcribed from memory" — model-produced text, so declared honestly it is P2 model involvement in a HUMAN candidate, and X-1 makes that rejection automatic and non-curable. T3/DEV lowers the *provenance* bar, not the P2 bar (§1.3 inviolable); recollection is separately inadmissible under §5.4. *AI half:* genuinely model-authored but with no BS §4.4 generation record (model version, provider, prompt, decoding, seeds, raw response), and BS §4.2 requires T1 for all AI categories by construction, unscoped by split. Writing that record now would be fabricated evidence. | `corpus/`, `src/ai_text_eval/data/` | Milestone 3 | Medium | Engineering | The fixture stays what it was built as — a detector test set — and is never migrated. GAUNTLET's human cells need T0 archive records or T1 commissioned writing under logging; its AI cells need real captured generation sessions. Proven mechanically and pinned in `tests/test_fixture_corpus_admissibility.py` (33 tests) so the conclusion cannot be quietly reversed. | **CLOSED — will not do** |

---

## E. External dependencies (consolidated)

| ID | Dependency | Needed by | Owner | Status |
|---|---|---|---|---|
| TD-X01 | External public detection corpora (HC3, RAID, M4, MGTBench, GPT-2 output) + licenses. | TD-D05 decontamination, TD-D11 difficulty | Infrastructure | Not acquired (see TD-B03). Attach point exists: `decontamination.ReferenceSource` — a corpus becomes a source by answering 13-gram membership. `REQUIRED_REFERENCES` names each one, and its absence is reported per scan. |
| TD-X02 | `torch` / `transformers` runtime for the DF1 panel member and model-based detectors. | TD-D11 difficulty (empirical) | Infrastructure | Available only via the `perplexity` extra; not installed in the current environment |
| TD-X03 | An LLM judge (API access) for the DF4 panel member. | TD-D11 difficulty (empirical) | Infrastructure | Not provisioned |
| TD-X04 | Private, access-controlled, logged storage for HIDDEN content and evidence (§10.2). | TD-B01, TD-D13 | Infrastructure | Not provisioned |
| TD-X05 | Process-logging tooling for T1 human commissioning (keystroke/edit-session capture, §3.2/§5.3). | TD-D03 intake (human contributors) | Infrastructure | Not provisioned |
| TD-X06 | A sentence-embedding model for §8.3 semantic near-duplicate screening. The bundled `LexicalSemanticBackend` is content-word overlap and is reported as `semantic_backend="lexical"` so no reader mistakes it for embeddings; it misses restatements a reviewer would catch (regression-tested in `test_the_lexical_stand_in_misses_a_restatement_at_the_default_threshold`). | R-08 semantic class | Infrastructure | Not provisioned; attaches via the `SemanticBackend` protocol with no caller changes |

---

## Notes on scope discipline

- The two OPEN governance items TD-G01 and TD-G02 are **not** worked around in
  code by explicit instruction. The validator surfaces them
  (`NO_FAILURE_MODE_ENTRY`, and the `U-01` placeholder) and stops.
- Nothing in section C or D has been started. Phase A delivered R-01…R-04
  only; this register is the last Phase A deliverable.
- Where a DEFERRED item is also BLOCKED (TD-D05↔TD-B03, TD-D11↔TD-B02, etc.),
  scheduling it into its phase does not unblock it; the external dependency
  must be resolved independently.
