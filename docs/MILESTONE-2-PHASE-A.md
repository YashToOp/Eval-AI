# Milestone 2, Phase A — Foundation

Implements roadmap items R-01…R-04. Governing document:
[`CORPUS_AUTHORING_SPEC`](../CORPUS_AUTHORING_SPEC.md) (CAS); the Benchmark
Specification (BS) is [`gauntlet-v1.0-spec.txt`](gauntlet-v1.0-spec.txt).

Phase A builds the substrate every later phase writes against: the field
registry, the identifier lifecycle, the decision ledger, and the completed
cross-field rules. Nothing here produces, reviews, or accepts a sample — that
is Phase B onward.

## Components

| Item | Module | Enforces |
|---|---|---|
| R-01 field registry | `gauntlet/registry.py` + `benchmark/field_registry.json` | CAS §4.1–4.3 |
| R-02 lifecycle | `gauntlet/lifecycle.py` | CAS §2, §9.5, X-5 |
| R-03 decision ledger | `gauntlet/ledger.py` | CAS §14.2, §11.7, §6.6 |
| R-04 consistency | `gauntlet/validate.py` | CAS §4.4, §4.2, §7.3, P10 |

Shared primitive: `gauntlet/findings.py` holds `Finding`/`Report`/`Severity`,
extracted from `validate.py` so the registry and ledger can report findings
without importing the validators. Dependency direction stays one-way:
checkers depend on findings, never on each other.

## R-01 — Field registry

Metadata fields exist only via `benchmark/field_registry.json`: per-field
purpose, `since` version, required flag, and closed vocabularies (including
the CAS §4.2 relationship types and the §11 role vocabulary). Version
arithmetic implements both halves of CAS §4.1:

- A record **at or below** the registry's schema version is validated
  strictly: a v1 record carrying a v2 field is claiming a schema it does not
  have (`FIELD_FROM_FUTURE_SCHEMA`).
- A record **newer** than the registry is tolerated with a visible warning
  (`NEWER_SCHEMA_TOLERATED`) — forward compatibility.

Fields deprecate, never disappear, so v1 records stay valid forever. Schema
v2 adds `lineage`, `difficulty_panel_version`, `difficulty_provisional` as an
additive amendment; the v1 field order is byte-identical to BS §5.2.

**Decision:** a missing registry is a hard error with no code fallback. A
fallback would silently reintroduce fields-defined-in-code, which is the
exact thing the registry exists to prevent.

## R-02 — Identifier registry and lifecycle

The CAS §2 state machine as an append-only event log; current state is a
replay, never an in-place mutation (P4, P5). Two structural invariants:

- **No stage is skipped** — `LEGAL_TRANSITIONS` is the whole relation.
- **Identifiers are unique across all history** — one ever registered, even
  now REJECTED or REDACTED, cannot be registered again (§9.5). This closes
  the gap the plain `DUPLICATE_ID` check could not: it saw only the loaded
  corpus, not rejected/redacted history.

Freeze at Stage 2 records a SHA-256 of the exact text bytes; a later edit
that changes the checksum raises (X-5). `supersede()` implements the Stage 2
edit path.

**Decision:** timestamps are parameters supplied by the acting process, not
read from a clock inside the registry. CAS §4.2 treats them as recorded
evidence, and passing them in keeps replay deterministic and testable.

## R-03 — Decision ledger

Every privileged action from the §14.1 authority matrix, recorded
append-only: action (closed vocabulary), actor person and role, scope/sample,
reason, evidence refs. Conflict detection covers the §11.7 and §6.6
same-sample role pairs (a producer may not review, adjudicate, or assign
splits for their own sample; a reviewer may not adjudicate it).

**Decision:** the ledger records faithfully and never refuses, because §11.7
requires every same-person dual-role action to be *noted*. Conflicts are
surfaced as findings; `strict=True` records first then raises. Blocking a
sample is the acceptance gate's job (R-11), not the ledger's — the ledger's
duty is that the conflict is visible. The §11.5 release-cycle constraints
need cycle modelling and are deferred to R-15, noted rather than omitted.

## R-04 — Cross-field consistency completion

Closes the §4.4 gaps and wires R-01 into `validate_sample`:

| Rule | Codes | Section |
|---|---|---|
| Span maps tile the text, no gap/overlap | `SPAN_GAP`, `SPAN_OVERLAP`, `SPAN_INCOMPLETE`, `SPAN_OUT_OF_RANGE` | §4.2 |
| Generator required for every non-HUMAN label | `GENERATOR_REQUIRED` | §4.2/§4.4 |
| Hybrid share strictly in (0,1) | `HYBRID_SHARE_RANGE` | §4.4 |
| Track V: transform record + derived_from link | `V_TRANSFORM_REQUIRED`, `V_LINEAGE_REQUIRED` | §4.4, P10 |
| Empirical difficulty names its panel | `DIFFICULTY_WITHOUT_PANEL` | §7.3 |
| Relationships resolve both ways | `LINEAGE_TARGET_MISSING`, `RELATIONSHIP_NOT_MUTUAL` | §4.4, P10 |

**Correction shipped:** the milestone-1 carve-out exempting `HUMAN_AI_EDITED`
from the generator requirement was wrong — the editing model still needs a
recorded configuration. Fixed with a test that fails against the old code.

`validate_relationships` is cross-sample (a target must exist in the corpus),
so it is separate from the per-sample validator and runs inside release
validation.

## Backwards compatibility

- `Finding`/`Report`/`Severity` are re-exported from `validate.py`; no caller
  changed.
- v1 records validate exactly as before, minus the corrected generator rule
  and the now-optional `expected_confusions` (CAS §4.2 declares it optional;
  BS §4.7 never listed it as required).
- All 88 milestone-1 gauntlet tests pass unchanged.

## Test coverage

| Suite | Tests |
|---|---|
| `test_gauntlet_registry.py` | 22 |
| `test_gauntlet_lifecycle.py` | 20 |
| `test_gauntlet_ledger.py` | 20 |
| `test_gauntlet_consistency.py` | 25 |
| `test_gauntlet.py` (milestone 1, unchanged) | 88 |

Repository total: 374 passing.

## Remaining limitations (Phase A boundary)

- The lifecycle registry and decision ledger are **standalone primitives**;
  they are not yet invoked by an intake or review workflow (R-07, R-10,
  Phase B/C). A transition and its governance record are two separate writes
  today; wiring them into one intake action is later work.
- `validate_relationships` checks existence and reciprocity, not that a
  `supersedes` target is actually in the REJECTED/superseded state — that
  cross-references the identifier registry and belongs with intake (R-07).
- Difficulty/panel binding validates the *shape* of the claim (empirical ⇒
  panel named); it cannot check that the value was machine-produced. The
  hand-edit prohibition (§7.3) is enforced by the difficulty system (R-16),
  not here.

## Canonical interpretations (ratified)

The three ambiguities raised during Phase A were reviewed and ratified. These
are now **canonical**: they are settled, not open questions, and MUST NOT be
reopened without a specification amendment (CAS §14.3). They are tracked in
the technical debt register as `RESOLVED` for provenance.

1. **`expected_confusions` is OPTIONAL.** The CAS governs; BS §4.7's omission
   and BS §5.2's silence are superseded by CAS §4.2 ("optional but
   recommended"). The field registry marks it `required: false`.
   *Ratified 2026-08-05.*
2. **Generator record is required for every model-involved label.** Any sample
   whose label is not `HUMAN` MUST carry a generator record; only pure `HUMAN`
   is exempt. This covers `AI`, `AI_HUMAN_EDITED`, `HUMAN_AI_EDITED`, and
   `COLLAB_MIXED`. The milestone-1 carve-out for `HUMAN_AI_EDITED` was a
   defect and is corrected. *Ratified 2026-08-05.*
3. **Schema requirements are version-specific; newer schemas never
   retroactively invalidate older records.** A v1 record is validated under
   the v1 schema, a v2 record under the v2 schema. A field introduced at v2
   (e.g. `lineage`) is required only of v2+ records. This is the general rule,
   of which the Track V lineage requirement is one instance.
   *Ratified 2026-08-05.*

## Still-open governance items (do not implement around)

The following remain **intentionally unresolved** and await an explicit
specification update. No code works around them; the validator reports them as
findings and stops there.

- **BS §9.1(h) vs §6.2 failure-mode coverage** — 33 of 99 categories have no
  §6.2 entry, so §9.1(h) cannot pass as written.
- **Track U category numbering** — §3.7 describes the track without numbering
  its categories; identifiers are permanent (§9.5) so this must be settled
  before any U sample is registered.

Both are logged in the technical debt register with owner *Specification*.
