"""Mechanical derivation from stored production records (R-06).

CAS §3.4: "The share of final tokens of model origin is computed mechanically
from the stored difference chain, never estimated by a person." CAS §4.2 says
the same of the label and repeats it for span maps. This module is where
"mechanically" is made real: given the retained intermediate states, it
derives the token attribution, the model-origin share, the span map, and the
label — deterministically, with no judgment anywhere in the path.

**Why deriving spans here is not post-hoc annotation.** CAS §3.5 prohibits
annotating spans on a finished text, because origin is a fact about
production and not a property recoverable from reading. Deriving spans from a
stored diff chain is the opposite operation: the chain *is* the production
record, and the derivation reads it rather than the prose. The prohibition is
on inferring origin from the artifact; this infers nothing.

Attribution rule (one rule, applied per round):

    Start with the base text's tokens, each tagged with the base's origin.
    For each edit round, diff the previous token sequence against the new
    one. Tokens that survive unchanged keep the origin they already had;
    tokens inserted or replaced take the origin of whoever performed that
    round. Deleted tokens are gone.

So a token's origin is *whoever last wrote it*, which is what "fraction of
final tokens of model origin" means. Everything else here follows from that.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from ai_text_eval.gauntlet.findings import Report

#: The two origin classes a token or span can carry.
HUMAN = "human"
AI = "ai"
ORIGINS = (HUMAN, AI)

#: Share comparisons are exact rational quantities, but floats are stored.
#: This tolerance absorbs float representation only, not estimation.
SHARE_TOLERANCE = 1e-9

_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class AttributedToken:
    token: Token
    origin: str


@dataclass
class EditRound:
    """One recorded editing round (CAS §3.4)."""

    editor: str            # HUMAN or AI — the class of whoever edited
    text: str              # the full state after this round
    instruction: str = ""  # the instruction defines the category (§3.4)
    editor_config: dict | None = None  # model config when editor is AI


@dataclass
class DiffChain:
    """A base text plus every stored intermediate state (CAS §3.4)."""

    base_text: str
    base_origin: str                     # HUMAN or AI
    rounds: list[EditRound] = field(default_factory=list)

    @property
    def final_text(self) -> str:
        return self.rounds[-1].text if self.rounds else self.base_text

    @property
    def editor_classes(self) -> set[str]:
        return {r.editor for r in self.rounds}


class DerivationError(ValueError):
    """The chain is malformed and no derivation can be trusted from it."""


def tokenize(text: str) -> list[Token]:
    """Whitespace-delimited tokens with character offsets.

    Matches the harness's counting rule (whitespace-delimited) so a derived
    share is expressed in the same units the corpus counts lengths in.
    """
    return [Token(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _check_chain(chain: DiffChain) -> None:
    if chain.base_origin not in ORIGINS:
        raise DerivationError(
            f"base_origin {chain.base_origin!r} must be one of {ORIGINS}")
    for i, rnd in enumerate(chain.rounds):
        if rnd.editor not in ORIGINS:
            raise DerivationError(
                f"round {i} editor {rnd.editor!r} must be one of {ORIGINS}")


def attribute(chain: DiffChain) -> list[AttributedToken]:
    """Attribute every token of the final text to whoever last wrote it."""
    _check_chain(chain)

    tokens = tokenize(chain.base_text)
    origins = [chain.base_origin] * len(tokens)

    for rnd in chain.rounds:
        new_tokens = tokenize(rnd.text)
        matcher = difflib.SequenceMatcher(
            a=[t.text for t in tokens], b=[t.text for t in new_tokens],
            autojunk=False,
        )
        new_origins: list[str] = [""] * len(new_tokens)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                # Survived untouched: keeps the origin it already had.
                for offset in range(j2 - j1):
                    new_origins[j1 + offset] = origins[i1 + offset]
            elif tag in ("insert", "replace"):
                # Written by this round's editor.
                for j in range(j1, j2):
                    new_origins[j] = rnd.editor
            # "delete": the tokens are gone; nothing to carry forward.
        tokens, origins = new_tokens, new_origins

    return [AttributedToken(t, o) for t, o in zip(tokens, origins)]


def ai_token_share(chain: DiffChain) -> float:
    """Fraction of final tokens of model origin (CAS §4.2).

    An empty final text has a share of 0.0 — there are no model-origin tokens
    because there are no tokens. The caller decides whether an empty sample is
    admissible; that is not this function's judgment to make.
    """
    attributed = attribute(chain)
    if not attributed:
        return 0.0
    return sum(1 for a in attributed if a.origin == AI) / len(attributed)


def span_map(chain: DiffChain) -> list[list]:
    """Character-offset span map over the final text (CAS §4.2).

    Spans tile the text with no gap and no overlap, as §4.2 requires:
    whitespace between two tokens of the same origin stays inside that span,
    and whitespace at a boundary attaches to the preceding span.
    """
    attributed = attribute(chain)
    text = chain.final_text
    if not attributed:
        return []

    spans: list[list] = []
    current_origin = attributed[0].origin
    for a in attributed[1:]:
        if a.origin != current_origin:
            spans.append([0, a.token.start, current_origin])  # start fixed below
            current_origin = a.origin
    spans.append([0, len(text), current_origin])

    # Rewrite starts so the spans tile from 0 to len(text).
    cursor = 0
    for span in spans:
        span[0] = cursor
        cursor = span[1]
    return spans


def derive_label(chain: DiffChain) -> str | None:
    """Label derived from the production record (CAS §4.1, §4.2).

    Mapping — base origin against the set of editor classes:

    | base  | editors        | label            |
    |-------|----------------|------------------|
    | ai    | (none)         | AI               |
    | human | (none)         | HUMAN            |
    | ai    | {ai}           | AI               |
    | human | {human}        | HUMAN            |
    | ai    | {human}        | AI_HUMAN_EDITED  |
    | human | {ai}           | HUMAN_AI_EDITED  |
    | any   | {human, ai}    | COLLAB_MIXED     |

    Returns None when the record does not determine a label mechanically,
    rather than guessing. A caller that wants a label from an undetermined
    chain has a provenance problem, not a derivation problem.

    Note this derives the *label*, not the category: CAS §3.4 makes the
    editing instruction decide the category (a grammar-only instruction and a
    free-rewrite instruction produce different cells), and an instruction is
    not mechanically classifiable.
    """
    _check_chain(chain)
    editors = chain.editor_classes
    base = chain.base_origin

    if not editors:
        return "AI" if base == AI else "HUMAN"
    if editors == {AI, HUMAN}:
        return "COLLAB_MIXED"
    if editors == {base}:
        return "AI" if base == AI else "HUMAN"
    if base == AI and editors == {HUMAN}:
        return "AI_HUMAN_EDITED"
    if base == HUMAN and editors == {AI}:
        return "HUMAN_AI_EDITED"
    return None


def replay(chain: DiffChain, expected_text: str) -> Report:
    """Verify the chain reproduces the sample text (CAS §6.1).

    Provenance review recomputes mechanical derivations and requires them to
    match. A chain whose final state is not the sample is not that sample's
    production record.
    """
    r = Report(checked=1)
    if chain.final_text != expected_text:
        r.error("CAS 6.1", "CHAIN_REPLAY_MISMATCH",
                "the chain's final state does not equal the sample text; this "
                "chain is not this sample's production record")
    return r


def verify_share(chain: DiffChain, claimed: float | None,
                 sample_id: str | None = None) -> Report:
    """Recompute the model-origin share and compare it to the stored value.

    A mismatch is an error, not a rounding note: CAS §4.2 makes hand-entered
    shares non-conformant, and a stored value that does not equal the derived
    one is either hand-entered or derived from a different chain.
    """
    r = Report(checked=1)
    try:
        derived = ai_token_share(chain)
    except DerivationError as err:
        r.error("CAS 3.4", "CHAIN_MALFORMED", str(err), sample_id)
        return r
    if claimed is None:
        r.error("CAS 4.2", "SHARE_ABSENT",
                f"no ai_token_share stored; the chain derives {derived:.6f}",
                sample_id)
        return r
    if abs(derived - claimed) > SHARE_TOLERANCE:
        r.error("CAS 4.2", "SHARE_NOT_DERIVED",
                f"stored ai_token_share {claimed} does not match the value "
                f"derived from the difference chain ({derived:.6f}); shares are "
                "computed mechanically, never estimated", sample_id)
    return r


def verify_span_map(chain: DiffChain, claimed: list | None,
                    sample_id: str | None = None) -> Report:
    """Recompute the span map and compare it to the stored value."""
    r = Report(checked=1)
    try:
        derived = span_map(chain)
    except DerivationError as err:
        r.error("CAS 3.4", "CHAIN_MALFORMED", str(err), sample_id)
        return r
    if claimed is None:
        r.error("CAS 4.2", "SPAN_MAP_ABSENT",
                f"no span_map stored; the chain derives {len(derived)} span(s)",
                sample_id)
        return r
    normalized = [[int(s[0]), int(s[1]), str(s[2])] for s in claimed
                  if isinstance(s, (list, tuple)) and len(s) == 3]
    if normalized != derived:
        r.error("CAS 4.2", "SPAN_MAP_NOT_DERIVED",
                f"stored span_map does not match the map derived from the "
                f"difference chain (stored {len(normalized)} span(s), derived "
                f"{len(derived)}); span origin is recorded at production time, "
                "never annotated afterwards", sample_id)
    return r


def verify_label(chain: DiffChain, claimed: str | None,
                 sample_id: str | None = None) -> Report:
    """Recompute the label from the production record (P1)."""
    r = Report(checked=1)
    try:
        derived = derive_label(chain)
    except DerivationError as err:
        r.error("CAS 3.4", "CHAIN_MALFORMED", str(err), sample_id)
        return r
    if derived is None:
        r.error("CAS 4.2", "LABEL_NOT_DERIVABLE",
                "the production record does not determine a label mechanically",
                sample_id)
        return r
    if claimed != derived:
        r.error("CAS 4.2", "LABEL_NOT_DERIVED",
                f"stored label {claimed!r} does not match the label derived from "
                f"the production record ({derived!r}); labels are assigned "
                "mechanically from production records, never chosen", sample_id)
    return r


def chain_from_evidence(attributes: dict) -> DiffChain:
    """Build a DiffChain from an INTERMEDIATE_CHAIN evidence item's attributes.

    Expected shape (the storage format is an implementation choice; CAS §3.4
    fixes only the facts that must be retained):

        {"base_origin": "ai",
         "states": ["<base text>", "<after round 1>", ...],
         "editors": ["human", ...],            # one per round after the base
         "instructions": ["fix grammar only"]} # one per round
    """
    states = attributes.get("states")
    if not isinstance(states, list) or len(states) < 1:
        raise DerivationError("chain evidence must retain at least the base state")
    editors = attributes.get("editors") or []
    instructions = attributes.get("instructions")
    if isinstance(instructions, str):
        instructions = [instructions] * max(0, len(states) - 1)
    instructions = instructions or []

    if len(editors) != len(states) - 1:
        raise DerivationError(
            f"{len(states) - 1} edit round(s) recorded but {len(editors)} "
            "editor class(es); every round must record who edited")

    rounds = [
        EditRound(editor=editors[i], text=str(states[i + 1]),
                  instruction=instructions[i] if i < len(instructions) else "")
        for i in range(len(states) - 1)
    ]
    return DiffChain(base_text=str(states[0]),
                     base_origin=str(attributes.get("base_origin", "")),
                     rounds=rounds)
