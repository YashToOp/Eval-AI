# Logged Writing Environment — Technical Design

**Status:** design, pre-implementation. Governed by the GAUNTLET Benchmark
Specification (BS) and Corpus Authoring Specification (CAS); planning context
from `GAUNTLET_CORPUS_ACQUISITION_STRATEGY` §4.2 and §9.1, which names this
"the single highest-leverage piece of engineering remaining".

**Product name:** `gauntlet-write` (module `ai_text_eval.lwe`).

---

## 1. What this is, and what it can never be

The LWE is the instrument that makes T1 human writing acquirable. CAS §3.2
admits commissioned human text only when written "under process logging …
in an environment attested to be free of generative writing tools", and adds
that text from tools offering generative rewriting is prohibited **"unless
the session log proves it"**. The session log is the proof. Its integrity is
therefore the entire product; the writing surface is packaging.

**What it cannot be.** A logged writing environment cannot prove the absence
of model involvement. A contributor can generate text elsewhere and retype it,
and no amount of instrumentation inside one window changes that. What logging
does is make covert use *costly and recorded* rather than free and invisible,
and give reviewers process facts to reason about. The strategy states the same
thing in §8 R1: "the system is designed to detect and correct, not to assume
purity." Any claim beyond that would be false advertising about the strongest
guarantee the corpus has.

### 1.1 The hard architectural rule: record, never score

The LWE computes **counts and durations**. It never computes a judgment.

There will be pressure to add one — a "session authenticity score" from typing
dynamics is an obvious feature and a plausible research paper. It is
prohibited here, and the reason is exactly CAS §5.4: "the output of any
detector, classifier, or model judgment about the text (P3) … circularity does
not become acceptable by pointing in a convenient direction." A model scoring
*the contributor's process* instead of *the contributor's text* is the same
circularity one level up: the corpus would be shaped by an algorithm's opinion
of who writes like a human, which is the failure the benchmark exists to
escape.

So: paste events are counted, not weighted. Idle gaps are measured, not
flagged as suspicious. Revision counts are reported, not thresholded. A
reviewer reads the facts and decides. This is enforced by an architecture test
(§13.4).

---

## 2. Architecture

Four layers, each usable without the one above it.

```
  gauntlet-write (local web app)          ← contributor UX, §9
        │  HTTP, localhost only
  ┌─────▼──────────────────────────────┐
  │ lwe.server    request handling      │  stdlib http.server, no deps
  ├────────────────────────────────────┤
  │ lwe.session   lifecycle + facts     │  §7, §6
  ├────────────────────────────────────┤
  │ lwe.journal   hash-chained log      │  §5 — the integrity core
  ├────────────────────────────────────┤
  │ lwe.export    evidence package      │  §6 — the GAUNTLET boundary
  └────────────────────────────────────┘
        │  EvidencePackage + Candidate
  gauntlet IntakeDesk → ScreeningDesk → ReviewDesk → AcceptanceDesk
```

**Dependency direction:** `journal` imports nothing from GAUNTLET. `export` is
the only module that imports `gauntlet.evidence` / `gauntlet.intake`. This
keeps the writing environment usable as an independent application — a writer
can run it, produce a session, and never touch a benchmark — while the export
boundary produces artifacts GAUNTLET consumes directly. It also means the LWE
can be extracted to its own repository later, which the strategy's §9.6
repository split will eventually want.

`lwe` does **not** import the detector subsystem. The existing AST guard is
extended to cover it (§13.4).

---

## 3. Threat model

Adversary: a contributor who wants model-produced text to enter the HUMAN
class. Secondary adversary: a contributor who wants to salvage a session after
the fact.

| # | Attack | LWE response | Residual |
|---|---|---|---|
| T1 | Paste generated text into the surface | Recorded as a `paste` event with position and length; surfaced in the fact summary | None — visible. Whether a paste disqualifies T1 is a **governance question** (§14) |
| T2 | Retype generated text from another window | **Not detectable.** Nothing inside one window sees another | Real. Countered outside the tool: supervised sessions, spot verification, incentive design (strategy R1) |
| T3 | Edit the log after the session to remove a paste | Hash chain breaks; replay diverges from the sealed text checksum | None |
| T4 | Truncate the log | Chain breaks at the seam and the final replay no longer matches | None |
| T5 | Fabricate an entire log offline | Chain is self-consistent if you generate it yourself | **Real.** The chain proves internal integrity, not authenticity. Authenticity comes from the contributor relationship and, later, server-hosted sessions (§4.3) |
| T6 | Backdate a session | Wall-clock is contributor-controlled | Real; recorded as contributor-asserted, with a monotonic counter alongside so *ordering* is trustworthy even when *dating* is not |
| T7 | Reuse a previous session's text | Not the LWE's job — duplicate screening (R-08) catches it downstream | None |
| T8 | Attest falsely that the environment is tool-free | Not detectable by the tool | Real; §11.6 quarantine and spot verification are the answer, as for every attestation |
| T9 | Start logging after writing has begun | Structurally impossible: the session opens with an empty document, and any first content event that is a bulk insert is recorded as a paste | None |

**The honest summary for contributors and reviewers alike:** the LWE makes
T3, T4, T7 and T9 impossible, makes T1 visible, and makes T2, T5, T6 and T8
matters of trust backed by verification rather than matters of proof. That
sentence belongs in the reviewer documentation verbatim.

---

## 4. Security model

### 4.1 Local-only by default
The MVP binds `127.0.0.1` on an ephemeral port. No network listener beyond
loopback, no outbound connections, no telemetry. The writing surface is a
local page; nothing leaves the contributor's machine until they export.

### 4.2 Integrity, not secrecy
The session record is not encrypted at rest in the MVP. Its security property
is **tamper-evidence**, not confidentiality: the contributor is not the party
the record is protected from at this stage; the record protects the *claim*.
Confidentiality is a custody concern once the record leaves the contributor
(§8.3).

A per-session random token guards the local HTTP endpoints so that another
process on the same machine cannot append to a live session.

### 4.3 What a hosted deployment would add (not MVP)
Server-side session hosting closes T5 by making the log's authorship
attributable to the service rather than the contributor. It is the natural v0.5
upgrade and the interfaces here are shaped so it is an added backend, not a
rewrite. It is deliberately out of scope: the pilot needs 30 commissioned
samples, not infrastructure.

---

## 5. Data model — the journal

An append-only, hash-chained event log. One JSON object per line.

```json
{"seq": 12, "t": 48213, "wall": "2026-08-05T10:14:22Z",
 "kind": "insert", "payload": {"pos": 341, "text": "and then"},
 "prev": "sha256:…", "hash": "sha256:…"}
```

- `seq` — monotonic from 0. Gaps are detectable.
- `t` — milliseconds since session open, from a monotonic clock. Trustworthy
  for ordering and duration.
- `wall` — contributor wall-clock, recorded as an assertion (T6).
- `hash` — `sha256(prev || canonical_json(seq, t, wall, kind, payload))`.
  Any edit to any earlier event invalidates every later hash.

### 5.1 Event kinds

| Kind | Payload | Why |
|---|---|---|
| `session_open` | contributor, cell intent, consent record, environment attestation, tool version, clock basis | Must be event 0. Carries the pre-writing attestation, so it cannot be added afterwards |
| `insert` | `pos`, `text` | Composition |
| `delete` | `pos`, `length` | Composition. **The removed text is not stored** — see §8.1 |
| `paste` | `pos`, `length`, `text` | An insert the surface identified as a paste. Distinguished at capture time, never inferred later |
| `focus_out` / `focus_in` | — | Process fact: the contributor left the window. Recorded, not judged |
| `note` | text | Contributor's own annotation |
| `session_close` | final text, `sha256`, word count | Seals the log |

### 5.2 Replay is the completeness proof

Applying every `insert`/`paste`/`delete` in `seq` order reconstructs the final
text. `verify()` checks three things:

1. the hash chain is intact from event 0;
2. replay reproduces exactly the text sealed in `session_close`;
3. event 0 is a `session_open` and the document began empty.

This is the same discipline as R-06's `replay()` for diff chains, and it is
what turns `complete_session: true` from a contributor's assertion into a
mechanical property. A log that fails any of the three cannot export at T1 —
not because a policy forbids it, but because the evidence does not exist.

---

## 6. Evidence package generation

`export.build_candidate(session, metadata)` produces exactly what
`IntakeDesk.submit()` takes, and nothing bypasses intake.

### 6.1 Tier is earned, not requested

The export tier is a function of what was actually captured:

| Captured | Emits | Tier |
|---|---|---|
| Verified journal + pre-writing environment attestation + complete close | `process_capture` with all three CAS attributes `true` | **T1** |
| Verified journal, environment attestation declined or given late | `attestation` (author, signature, contemporaneous, tools described, spot-verification acknowledged) | **T2** |
| Journal fails verification, or session never closed | No evidence item; export refuses and says which check failed | **none** |

This implements the strategy's "burden proportional to ambition" structurally.
A contributor who declines the attestation still gets a usable contribution —
routed to DEV — rather than a rejection. The tier follows the evidence; the
split system enforces the consequence.

The export never writes `generative_tools_attested_absent: true` unless the
contributor affirmed it *in event 0*, before any text existed.

### 6.2 Artifacts written

```
sessions/<session-id>/
  journal.jsonl        the hash-chained log        (evidence, restricted)
  session.json         manifest: facts, checksums  (evidence, restricted)
  text.txt             the final text              (becomes the sample)
  evidence.json        EvidencePackage manifest    (PACKAGE_MANIFEST)
```

`journal.jsonl` is checksummed into the `EvidenceItem`, so `verify_integrity()`
detects post-export tampering with the file on disk.

### 6.3 Declaration

The LWE emits a `Declaration(contributor, model_involved=False,
tools_used=[…])` only when the contributor affirmed it. It never synthesises
one. If the contributor declares involvement, the export still runs and the
Generation Firewall rejects it at intake — which is correct, and which the
LWE must not pre-empt by refusing to export. Recording an honest declaration
that then fails is the system working.

---

## 7. Session lifecycle

```
  CREATED ──consent+attestation──▶ OPEN ──close──▶ CLOSED ──export──▶ EXPORTED
     │                              │
     └──────────abandon─────────────┴──▶ ABANDONED (retained, never exported)
```

- **CREATED** — journal allocated. No text may be appended. This state exists
  so that the consent and attestation land in event 0.
- **OPEN** — the only state that accepts content events.
- **CLOSED** — sealed; final checksum recorded; no further events.
- **EXPORTED** — evidence package written. Idempotent.
- **ABANDONED** — retained under P5 (nothing is deleted). An abandoned session
  is evidence of nothing and exports nothing, but its existence is a fact.

Mapping to GAUNTLET: a session produces a *candidate*. The candidate enters at
CAS §2 Stage 2 (freeze) via `IntakeDesk.submit()`. **The LWE owns nothing
downstream of that** — no state, no split opinion, no acceptance path.

---

## 8. Privacy model

Keystroke logs are sensitive personal data. The strategy is explicit: "Collect
the minimum the tier requires (the writing surface, not the screen)."

### 8.1 Minimization decisions

| Decision | Rationale |
|---|---|
| No screen capture, no global keylogger, no clipboard monitoring | Out of scope of the tier requirement; enormously more invasive |
| Only events inside the LWE's own surface | The tool cannot and should not see the rest of the machine |
| **`delete` stores position and length, never the removed text** | Forward replay does not need it. A contributor who types something private and deletes it should not find it preserved forever in an evidence archive. This costs nothing and removes the single worst privacy exposure |
| Paste content *is* stored | It is part of the final text or was; and a paste with unknown content is not evidence |
| Millisecond timing retained | Needed to distinguish paste from typing at capture time. **Not** used for inference (§1.1) |

### 8.2 Consent
Recorded in event 0, before writing, in plain language: what is captured, what
is not, how long it is kept, who can see it, and what it will and will not be
used for. Consent that arrives after the writing is not consent, and it is
also not admissible as pre-writing attestation.

### 8.3 Retention and access
Session records are **evidence, not corpus content**. The final text is
published with the corpus; the journal is not. It lives under CAS §5.5 custody
— checksummed at intake, access-controlled, retained for the life of the
corpus (P4), never repurposed beyond label support and audit.

**Open item:** the project has no access-controlled evidence store (TD-X04 is
unprovisioned). Until it does, session records live on the contributor's
machine and in whatever the maintainer uses. This must be stated honestly at
consent time rather than promised away.

### 8.4 Withdrawal
A contributor may abandon a session at any time; nothing is exported. Once a
sample is *accepted*, CAS P5 forbids deletion — so withdrawal after acceptance
is a redaction request under §9.4, not a delete. This asymmetry must be stated
at consent time, because it is the part contributors will not expect.

---

## 9. Contributor UX

The friction budget is the design constraint. The strategy: "logging tooling
must be frictionless or writers will not finish."

**The whole flow is four screens and one of them is writing.**

1. **Start** — who you are, what cell you're writing for, how long the prompt
   suggests. One page.
2. **Consent and attestation** — plain language, three checkboxes (consent to
   logging; environment free of generative writing tools; agree to spot
   verification), each with one sentence of explanation. Declining the second
   is allowed and explained: "your contribution is still welcome and will be
   recorded at a lower evidence tier."
3. **Write** — a plain surface. Word count, a live "recording" indicator, and
   nothing else. No autocomplete, no suggestions, no spell-check rewriting —
   the environment must not itself be a generative writing tool.
4. **Finish** — the fact summary shown to the contributor before export, so
   nothing about them is recorded that they did not see.

**Non-negotiables:** no account, no network, no upload step in the MVP, and no
mid-session interruptions. Autosave to the journal is continuous.

---

## 10. Reviewer UX

The provenance reviewer's mandate (CAS §6.1) is the evidence, and their review
must be "performed without forming or recording any opinion about whether the
text 'seems' consistent with its label".

So the reviewer view shows, in this order:

1. **Verification result** — chain intact, replay matches, opened empty.
   Pass/fail, mechanical.
2. **Process facts** — session duration, active vs idle time, event counts by
   kind, paste count and total pasted characters, focus-out count, revision
   count, final word count. Numbers only.
3. **The attestations** — what the contributor affirmed, and when.
4. **The text** — last, and clearly marked as *not* evidence for the label.

No score, no flag colour, no "suspicious" badge. The ordering is deliberate:
a reviewer who sees a verdict first will reason backwards to it.

---

## 11. Export format

`session.json` is the reviewer-facing manifest and the machine-readable
summary:

```json
{
  "lwe_version": "0.1.0",
  "session_id": "…",
  "contributor": "…",
  "state": "exported",
  "verification": {"chain_intact": true, "replay_matches": true,
                   "opened_empty": true, "verified": true},
  "attestations": {"consent_to_logging": true,
                   "environment_free_of_generative_tools": true,
                   "spot_verification_acknowledged": true,
                   "recorded_at_event": 0},
  "facts": {"duration_ms": …, "active_ms": …, "idle_ms": …,
            "events": {"insert": …, "delete": …, "paste": …},
            "pasted_chars": …, "focus_out_count": …,
            "final_words": …, "final_chars": …},
  "text_sha256": "sha256:…",
  "journal_sha256": "sha256:…",
  "supported_tier": "T1"
}
```

`facts` contains no derived judgment. `supported_tier` is computed by the
same rule §6.1 states and is checkable by a reader.

---

## 12. Failure handling

| Failure | Behaviour |
|---|---|
| Crash mid-session | The journal is append-only and flushed per event; reopening replays it and resumes. No work is lost beyond the last event |
| Chain broken on reopen | Session refuses to reopen as OPEN; it becomes verifiable-as-broken and can be exported only as a *record of a failed session*, never as T1 evidence |
| Replay mismatch at close | Close fails with the diverging position reported. The contributor is told; the session does not silently seal |
| Disk full | Append fails loudly before acknowledging the event to the UI, so the UI's document and the journal cannot diverge |
| Export called twice | Idempotent; the second call verifies the existing artifacts rather than rewriting them |
| Clock jumps backwards | Monotonic `t` is unaffected; the `wall` anomaly is recorded as a fact, not corrected |

The governing principle matches the rest of the project: **report, never
repair.** A broken journal is never silently rebuilt to something that
validates.

---

## 13. Integration with the GAUNTLET lifecycle

### 13.1 The boundary
`export.build_candidate()` → `Candidate` → `IntakeDesk.submit()`. Nothing
else. The LWE does not touch `IdentifierRegistry`, `DecisionLedger`,
screening, review, or acceptance.

### 13.2 What each downstream stage receives
- **Intake** — a `Candidate` with an `EvidencePackage` whose `process_capture`
  attributes are true only if earned, and a `Declaration` reflecting what the
  contributor actually said. The Generation Firewall runs unchanged.
- **Evidence validation** — `validate_package()` already enforces the three
  T1 attributes and `verify_integrity()` already re-checks the journal
  checksum. **No change to `evidence.py` is required**, which is the
  strongest evidence that the design fits.
- **Review** — `session.json` is the provenance reviewer's working document.
- **Acceptance** — A-1 (tier supported) and A-12 (declarations on file) are
  satisfied by artifacts the LWE produced as a side effect of writing.

### 13.3 What is deliberately not integrated
Split assignment, difficulty, and category assignment. The LWE records the
*intended* cell from the session start as contributor intent; it never asserts
it as metadata. Category is defined by the instruction (CAS §3.4) and is a
review judgment.

### 13.4 Architecture guards to extend
- `lwe` must not import the detector subsystem.
- `lwe.journal` must not import GAUNTLET (independence of the app).
- An AST test asserting no scoring/classification of process facts: the fact
  summary is counts and durations only.

---

## 14. Governance questions raised, not resolved

Neither blocks implementation; both need answers before the pilot's
commissioned sessions are reviewed.

1. **Does a paste disqualify T1?** CAS §3.2 prohibits text "recalled or
   reconstructed from memory" and "laundered through paraphrase", but pasting
   one's own earlier draft from a file is neither. The LWE records pastes as
   facts and takes no position. Governance should state whether a T1 session
   admits pastes, admits them below some fraction, or admits them only with a
   recorded explanation.
2. **Is a declined environment attestation T2, or nothing?** §6.1 above routes
   it to T2 on the reading that a named contributor affirming their process
   *is* an attestation. If governance disagrees, the export table changes by
   one row.

A third item is legal rather than governance: the consent text and the
retention promise need review by someone qualified before real contributors
sign them, and TD-X04 (no access-controlled evidence store) means the current
honest promise is weaker than the specification's custody rules describe.

---

## 15. Implementation milestones

| # | Scope | Exit |
|---|---|---|
| M1 | `journal` — events, hash chain, replay, verification | Tamper detection tested against edited, truncated, and reordered logs |
| M2 | `session` — lifecycle, facts, resume-after-crash | State machine refuses content in CREATED/CLOSED; facts carry no judgment |
| M3 | `export` — evidence package, tier derivation, `Candidate` | A session end-to-end through `IntakeDesk` reaching VALIDATED |
| M4 | `server` + UI — local app, four screens | A human can write a sample and export it without reading documentation |
| M5 | Docs — contributor guide, reviewer guide, threat-model statement | The §3 honest summary is stated where both audiences see it |

Each milestone: implementation, tests, documentation, commit.
