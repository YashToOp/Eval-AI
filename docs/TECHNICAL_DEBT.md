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
| DONE (implemented) | 4 |
| OPEN — Specification decision required | 9 |
| DEFERRED — scheduled to a later phase | 13 |
| BLOCKED — needs an external resource | 5 |

Cross-cutting: several DEFERRED items are also BLOCKED; the `Depends on` column
names the blocker.

---

## A. Ambiguities and canonical interpretations

| ID | Description | Affected | Blocking milestone | Risk | Owner | Proposed resolution | Status |
|---|---|---|---|---|---|---|---|
| TD-A01 | `expected_confusions` requiredness: BS §4.7 omits it, BS §5.2 is silent, CAS §4.2 says "optional but recommended". | `registry.py`, field registry | none | Low | Specification | CAS governs; field is OPTIONAL. Recorded in registry `interpretations` and Phase A doc. | **RESOLVED** 2026-08-05 |
| TD-A02 | Generator record for `HUMAN_AI_EDITED` and other model-involved labels. Milestone 1 exempted `HUMAN_AI_EDITED` in error. | `validate.py` generator rule | none | Medium | Specification | Required for EVERY non-HUMAN label; only pure HUMAN exempt. Corrected with a regression test. | **RESOLVED** 2026-08-05 |
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

---

## C. Deferred implementation (Engineering-owned, scheduled)

| ID | Description | Affected | Blocking milestone | Risk | Owner | Depends on | Status |
|---|---|---|---|---|---|---|---|
| TD-D01 | Evidence package schemas and chain of custody (R-05, CAS §5). Currently only `provenance_ref` path resolution exists; nothing validates what it points to. | new module | Phase B | High | Engineering | — | **DONE** `f7bdcdc` (R-05) |
| TD-D02 | Mechanical derivation engines (R-06): recompute `ai_token_share` from diff chains, verify span tilings against production records, replay diff chains. Hand-entered shares are currently accepted if internally coherent. | new module, `validate.py` | Phase B | High | Engineering | TD-D01 | **DONE** `c552b39` (R-06) |
| TD-D03 | Candidate intake with the Generation Firewall (R-07, P2/X-1): structural rejection of any model-involved candidate targeting HUMAN, contributor declaration capture, freeze execution wired to lifecycle. | new module, `lifecycle.py`, `ledger.py` | Phase B | Critical | Engineering | TD-D01 | **DONE** (R-07) `db80d35`; contributor-side process logging still TD-X05 |
| TD-D04 | Duplicate detection, six classes (R-08, §8). | `duplicates.py` | Phase B | High | Engineering | TD-G05, TD-D09 | **DONE** (R-08); thresholds uncalibrated (TD-G05), semantic backend is a lexical stand-in (TD-X06), share caps interim (TD-G09) |
| TD-D05 | Decontamination screening (R-09, §3.7, BS §4.9): 13-gram containment vs external corpora and DEV. | new module | Phase B | High | Engineering | TD-X01 | DEFERRED |
| TD-D06 | Review workflow (R-10, §6): dual review, kappa ≥ 0.8 gating, adjudication, calibration exercises, COI routing. | new module, `ledger.py` | Phase C | High | Engineering | TD-D01 | DEFERRED |
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
| TD-D17 | Retrofit `tests/test_review_regressions.py` (pre-GAUNTLET detector regressions) to the §8.3 record schema. Currently satisfies §8.1 in spirit but lacks `bug_id`/`expected_behavior`/`status`. | `tests/`, `regression/` | Phase E (R-17) | Low | Engineering | TD-D12 | DEFERRED |

---

## D. Blocked features (Infrastructure-owned)

| ID | Description | Affected | Blocking milestone | Risk | Owner | Proposed resolution | Status |
|---|---|---|---|---|---|---|---|
| TD-B01 | HIDDEN custody (§10.2) is unimplementable inside a public repository: HIDDEN text and evidence must live in access-controlled storage separate from the public corpus, with two-release-manager export. | R-14, R-18, split assignment | Phase D | Critical | Infrastructure | Provision a private, access-logged store outside the public repo before any HIDDEN material exists. Until then, no sample may be assigned HIDDEN. | **BLOCKED** |
| TD-B02 | Difficulty empirical panel needs model access: DF1 (likelihood) requires `torch`/`transformers`; DF4 (judge) requires an LLM API. Neither ships in the current environment. | R-16 difficulty | Phase E | Medium | Infrastructure | Provision the panel runtime; provisional-difficulty machinery can be built without it (TD-D11 splits accordingly). | **BLOCKED** |
| TD-B03 | Decontamination and reference-difficulty need external public detection corpora (HC3, RAID, M4, MGTBench, GPT-2 output corpus) with their licenses. | R-09, R-16 | Phase B | High | Infrastructure | Acquire and license the external corpora; record checksums in the manifest. | **BLOCKED** |
| TD-B04 | Contributor-blinding of split assignment (§10.3) is only as strong as repository access separation; in a single public repo, assignment records are visible. | R-13 split assignment | Phase D | High | Infrastructure | Decide where assignment records physically live (private store, TD-B01) so contributors cannot infer assignments. | **BLOCKED** |
| TD-B05 | Fixture migration (milestone 3): the 36-sample demo fixture must enter DEV as T3/noisy via the normal lifecycle. Its human half additionally trips §3.2's "recalled or reconstructed from memory" prohibition (transcribed public-domain text), which T3+DEV tolerates but must be recorded in rationale. | `corpus/`, `lifecycle.py` | Milestone 3 | Low | Engineering | Register each fixture sample through the lifecycle (idea→…→dev) with `noisy_label=true`, `provenance_tier=T3`, and a rationale noting the memory-transcription caveat. Blocked on intake (TD-D03). | **BLOCKED** on TD-D03 |

---

## E. External dependencies (consolidated)

| ID | Dependency | Needed by | Owner | Status |
|---|---|---|---|---|
| TD-X01 | External public detection corpora (HC3, RAID, M4, MGTBench, GPT-2 output) + licenses. | TD-D05 decontamination, TD-D11 difficulty | Infrastructure | Not acquired (see TD-B03) |
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
