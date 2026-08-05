# gauntlet-write — contributor and reviewer guide

A writing session that records how the text was made.

```
python -m ai_text_eval.lwe            # opens http://127.0.0.1:8765/
python -m ai_text_eval.lwe --root ~/sessions --port 9000 --no-browser
```

No dependencies, no accounts, no network beyond your own machine.

---

## What this is honestly for

GAUNTLET needs human writing whose provenance is *evidence*, not a claim. The
Corpus Authoring Specification (§3.2) admits commissioned human text only when
it was written "under process logging … in an environment attested to be free
of generative writing tools", and it prohibits text from tools that offer
generative rewriting **"unless the session log proves it"**.

This tool produces that log.

### What it can prove

- The text was composed inside a recorded session that started empty.
- The recording is complete: replaying every edit reproduces exactly the
  finished text, so nothing is missing from the account.
- The recording has not been altered since it was written.
- Whether text arrived by typing or by pasting, and how much of each.

### What it cannot prove

**It cannot prove that no model was involved.** Someone can generate text in
another window and retype it here, and nothing inside this window would see
that. No logging tool can close that gap.

What logging does is make covert use *costly and recorded* rather than free and
invisible. The rest is handled outside the tool: supervised sessions where the
stakes justify it, spot verification of process facts, and the specification's
quarantine rule for anyone who declares falsely. If you see this tool described
as proof of human authorship, that description is wrong.

### What it will never do

It will never score you. There is no "authenticity" number, no suspicion flag,
no model watching your typing rhythm. Judging a contributor's process would be
the same circularity the specification forbids for judging their prose (CAS
§5.4), and an architecture test in this repository fails the build if anyone
adds one.

---

## For contributors

### The four screens

1. **Start.** Your name, what you were asked to write, and optionally the cell
   it is for.
2. **Before you begin.** Three separate agreements — see below.
3. **Write.** A plain surface, a word count, and a recording indicator. No
   autocomplete, no suggestions, no spell-check rewriting: an environment for
   this corpus must not itself be a generative writing tool.
4. **Finish.** You see every fact recorded about your session before anything
   is exported.

### The three agreements, and why they are separate

| Agreement | What it means | If you decline |
|---|---|---|
| **Consent to logging** | Your edits in this window are recorded | The session does not start. A record of someone who did not agree is not evidence, it is surveillance |
| **Tool-free environment** | No assistant, autocomplete, or rewriting feature is helping | **You still contribute.** Your session is recorded at a lower evidence tier (T2 instead of T1) rather than being refused |
| **Spot verification** | Your contribution may be checked | The session runs; the acknowledgement is recorded as absent |

They are asked separately because they are different promises, and a reviewer
needs to know which one you actually made.

Declining the environment attestation is a normal thing to do. If you are not
certain your editor has no generative features, say so — an honest T2 is worth
more to this corpus than a T1 that is not true.

### What is recorded, and what is not

**Recorded:** every insertion and its text, every deletion's *position and
length*, pastes and their content, when the window lost focus, and timings.

**Not recorded:** your screen, other applications, your clipboard, anything
outside this window — and **the content of text you delete**. If you type
something private and remove it, the log knows five characters vanished at
position 40 and nothing more.

### Retention, and the part people do not expect

Your finished text may be published as part of the corpus. **The recording of
how you wrote it is not published** — it is evidence, kept under access
control, used only to support the label and for audit.

Once a sample has been *accepted* into the corpus it cannot be deleted. The
specification forbids deletion outright (P5); the nearest operations are
correction and withdrawal from circulation. You can abandon a session at any
time before that and nothing is exported — but decide before you begin, not
after.

> **Current honest limitation:** the project does not yet have the
> access-controlled evidence store the specification describes. Until it does,
> session records live on your machine and on the maintainer's. This is stated
> here rather than promised away.

### If something goes wrong

- **"Not recorded" appears while writing.** Stop and tell the maintainer. Your
  work is safe in the browser, but text the journal did not receive would make
  the session fail verification, and a session that fails is not usable.
- **The application crashes.** Reopen it and load the session; the journal is
  flushed on every event, so at most the last keystroke is lost.
- **You want to stop.** Use *Abandon*. The record is kept — nothing is ever
  deleted — but it exports nothing and supports no label.

---

## For reviewers

Your mandate under CAS §6.1 is the evidence. The review is performed "without
forming or recording any opinion about whether the text 'seems' consistent with
its label"; such an opinion is inadmissible under P3, and recording one is
itself a review defect.

The review page is ordered to help with that: **verification, then facts, then
attestations, then the text.** A reviewer who reads the prose first reasons
backwards from how it reads.

### 1. Verification — mechanical, four checks

| Check | Means |
|---|---|
| Hash chain intact | The log has not been altered since it was written |
| Replay reproduces the sealed text | The log is complete with respect to the text |
| Opened over an empty document | Logging preceded writing (§3.2: "retroactive logging does not exist") |
| Sealed | The session was properly closed |

All four must pass for T1. Each failure means something different, which is why
they are reported separately rather than as one verdict.

### 2. Process facts — numbers, not signals

Duration, active and idle time, typed characters, pasted characters, paste
count, deleted characters, focus-out count, final length.

**These are not evidence of dishonesty and are not thresholded.** A long idle
gap means someone made tea. A paste may be the contributor's own earlier draft
from a file. Read them as context for your judgment, and record your reasoning
in the review — not as a score the tool produced.

> **Open governance question:** whether a paste disqualifies T1 at all is
> undecided (`docs/LWE_DESIGN.md` §14.1). CAS §3.2 prohibits text "recalled or
> reconstructed from memory" and "laundered through paraphrase"; pasting one's
> own draft is neither. Until governance rules, record what you see and say
> what you concluded.

### 3. Attestations

What the contributor affirmed, and at which event. It should be event 0. An
attestation recorded later did not precede the writing, and the export drops
such a session to T2 automatically.

### 4. The text

Shown last, and marked as not evidence for the label. It is there so you can do
the *content* review — category fit, register, PII, rights — not so you can
form a view about whether it reads human.

---

## For maintainers

### What a session produces

```
sessions/<session-id>/
  journal.jsonl    the hash-chained event log    evidence — restricted
  session.json     verification, facts, tier     evidence — restricted
  text.txt         the finished text             becomes the sample
  evidence.json    the EvidencePackage manifest  evidence
```

### Getting a session into the corpus

```python
from ai_text_eval.lwe.session import load_session
from ai_text_eval.lwe.export import build_candidate
from ai_text_eval.gauntlet.intake import IntakeDesk

session = load_session("sessions/<session-id>")
candidate = build_candidate(session, "H-01-B100-0001", metadata)
result = IntakeDesk(registry, ledger).submit(candidate, "contributor", timestamp)
```

`metadata` is yours to supply. The tool does not assert the category (CAS §3.4:
the instruction defines it, and an instruction is not mechanically
classifiable) and never asserts the split (§4.2: assigned at Stage 8 by a
release manager).

Nothing downstream is bypassed. The candidate goes through intake, the
Generation Firewall, screening, review, and acceptance exactly like any other.

### Tier is earned, not requested

| What was captured | Evidence emitted | Tier |
|---|---|---|
| Verified journal + attestation at event 0 | `process_capture` | **T1** |
| Verified journal, attestation declined or late | `attestation` | **T2** |
| Journal fails verification, or never sealed | none | **—** |

`supported_tier(session)` returns the tier *and the reasons*, including when it
passes, so you can see which checks were consulted rather than inferring it
from silence.

### Before real contributors use this

1. The consent text needs review by someone legally qualified. It is written
   to be honest, not to be sufficient.
2. TD-X04 (access-controlled evidence storage) is unprovisioned. The retention
   promise above is currently weaker than the specification's custody rules
   describe, and contributors are told so.
3. The two governance questions in `docs/LWE_DESIGN.md` §14 need answers before
   the pilot's commissioned sessions are reviewed.

---

*Design: `docs/LWE_DESIGN.md`. Governing documents:
`docs/CORPUS_AUTHORING_SPEC.md`, `docs/gauntlet-v1.0-spec.txt`.*
