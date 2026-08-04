"""Duplicate detection, six classes (R-08, CAS §8).

Every candidate is screened against the complete corpus history: all splits,
all releases, deprecated and redacted identifiers' retained fingerprints, the
regression registry, and the live candidate pool. The unifying rule is P10 —
designed relationships are declared, and undeclared similarity is a defect,
not a coincidence.

The six classes and what each is really defending:

  8.1 exact       reuse and cross-split leakage
  8.2 near        paraphrased resubmission of existing material
  8.3 semantic    a cell secretly restating one situation
  8.4 template    batch generation's default failure mode
  8.5 style       a cell secretly being a single-author benchmark
  8.6 cross-release  history does not reset; and DEV/TEST similarity must
                  never buy its way into HIDDEN

**Thresholds are configuration, not constants.** §8.2 requires them
calibrated per register, because issue-tracker text is naturally more
self-similar than fiction. They therefore live in a screening config with a
documented default, and TD-G05 tracks the calibration as an open governance
item. A global constant here would be exactly the "convenient" simplification
the specification warns against.

**Semantic screening flags, it does not decide** (§8.3). The implementation
here is lexical (content-word overlap); real meaning-level similarity needs
an embedding model, which is a declared external dependency rather than
something to fake. The interface is stable so a real model attaches without
changing callers — see `SemanticBackend`.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

from ai_text_eval.gauntlet.findings import Report


class DuplicateClass(str, Enum):
    EXACT = "exact"
    NEAR = "near"
    SEMANTIC = "semantic"
    TEMPLATE = "template"
    STYLE = "style"
    CROSS_RELEASE = "cross_release"


#: Relationship types that legitimately explain high similarity (§8.2).
#: A flag covered by a verified declared relationship stands; one that is not
#: is rejected under X-6.
EXPLANATORY_RELATIONS = frozenset({"derived_from", "supersedes",
                                   "tell_pair", "mimicry_pair"})

#: Default per-register near-duplicate thresholds (§8.2). Registers differ:
#: issue-tracker and legal text are naturally self-similar, fiction is not.
#: These are documented starting points pending calibration (TD-G05), NOT
#: calibrated values — `ScreeningConfig.calibrated` records which is which.
DEFAULT_NEAR_THRESHOLDS: dict[str, float] = {
    "__default__": 0.80,
    "casual": 0.82,
    "technical": 0.85,
    "academic": 0.85,
    "creative": 0.75,
    "legal": 0.90,
    "medical": 0.90,
    "financial": 0.88,
    "marketing": 0.85,
    "support": 0.88,
    "engineering_communication": 0.88,
}

DEFAULT_SEMANTIC_THRESHOLD = 0.90
DEFAULT_TEMPLATE_THRESHOLD = 0.85

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


@dataclass
class ScreeningConfig:
    """Screening thresholds and caps (§8.2, §8.5).

    `calibrated` is False while the thresholds are documented defaults rather
    than values calibrated on known-independent same-register text. Screening
    still runs; the report says the thresholds are provisional so nobody reads
    a pass as a calibrated result.
    """

    near_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_NEAR_THRESHOLDS))
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD
    template_threshold: float = DEFAULT_TEMPLATE_THRESHOLD
    max_author_share_per_cell: float | None = None   # §8.5; TD-G04
    max_session_share_per_cell: float | None = None  # §8.5; TD-G04
    calibrated: bool = False

    def near_threshold_for(self, register: str | None) -> float:
        if register and register in self.near_thresholds:
            return self.near_thresholds[register]
        return self.near_thresholds.get("__default__", 0.80)


@dataclass
class CorpusEntry:
    """A previously seen text, from any split, release, or terminal state.

    History does not reset (§8.6), so deprecated and redacted identifiers
    remain here as retained fingerprints even when their text is gone.
    """

    identifier: str
    text: str | None = None
    split: str | None = None
    domain: str | None = None
    author: str | None = None
    session: str | None = None
    category: str | None = None
    length_bucket: str | None = None
    topic_group_id: str | None = None
    release: str | None = None
    state: str = "released"
    raw_checksum: str = ""
    normalized_checksum: str = ""

    def __post_init__(self):
        if self.text is not None:
            if not self.raw_checksum:
                self.raw_checksum = raw_checksum(self.text)
            if not self.normalized_checksum:
                self.normalized_checksum = normalized_checksum(self.text)


@dataclass
class Match:
    """One similarity finding against a prior entry."""

    duplicate_class: DuplicateClass
    other_id: str
    score: float
    explained_by: str | None = None   # the declared relation, if any
    detail: str = ""


# -- fingerprints --------------------------------------------------------

def raw_checksum(text: str) -> str:
    """SHA-256 of the exact bytes.

    §8.1 requires both forms, because some tracks are sensitive to invisible
    characters: a homoglyph or zero-width variant is a *different* sample by
    design, and collapsing it into its base would erase Track V.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_for_comparison(text: str) -> str:
    """The corpus's defined normalization for duplicate comparison."""
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def normalized_checksum(text: str) -> str:
    return hashlib.sha256(
        normalize_for_comparison(text).encode("utf-8")).hexdigest()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(normalize_for_comparison(text))


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = _words(text)
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def near_similarity(a: str, b: str) -> float:
    """Word-shingle Jaccard similarity — the §8.2 word-level measure."""
    return jaccard(_shingles(a), _shingles(b))


def structural_fingerprint(text: str) -> tuple:
    """Skeleton of a text: sentence-shape sequence and opening construction.

    §8.4 defines template duplication as a shared structural skeleton with
    slots varied, so the fingerprint deliberately discards content and keeps
    shape: sentence count, the length class of each sentence, and the opening
    word sequence.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    shape = tuple(min(len(_words(s)) // 5, 8) for s in sentences)
    opening = tuple(_words(sentences[0])[:4]) if sentences else ()
    return (len(sentences), shape, opening)


def template_similarity(a: str, b: str) -> float:
    """Structural similarity, ignoring content (§8.4)."""
    fa, fb = structural_fingerprint(a), structural_fingerprint(b)
    if fa == fb:
        return 1.0
    score = 0.0
    if fa[0] == fb[0]:
        score += 0.3
    shape_a, shape_b = fa[1], fb[1]
    if shape_a and shape_b:
        matched = sum(1 for x, y in zip(shape_a, shape_b) if x == y)
        score += 0.5 * matched / max(len(shape_a), len(shape_b))
    if fa[2] and fa[2] == fb[2]:
        score += 0.2
    return min(1.0, score)


class SemanticBackend(Protocol):
    """Meaning-level similarity (§8.3). Flags, never decides.

    The bundled implementation is lexical. A real embedding model attaches by
    implementing this protocol; no caller changes. Declared as an external
    dependency rather than simulated.
    """

    def similarity(self, a: str, b: str) -> float: ...


class LexicalSemanticBackend:
    """Content-word overlap: a weak stand-in that never pretends otherwise.

    Reported as `semantic_backend="lexical"` so a reader never mistakes it for
    an embedding model.
    """

    name = "lexical"
    STOPWORDS = frozenset("""a an the and or but if then than that this these those
        of in on at to for with by from as is are was were be been being it its
        i you he she we they not no so such very can will would should could
        there here what which who whom when where how""".split())

    def similarity(self, a: str, b: str) -> float:
        ca = Counter(w for w in _words(a) if w not in self.STOPWORDS)
        cb = Counter(w for w in _words(b) if w not in self.STOPWORDS)
        if not ca or not cb:
            return 0.0
        overlap = sum((ca & cb).values())
        return overlap / min(sum(ca.values()), sum(cb.values()))


# -- the screen ----------------------------------------------------------

@dataclass
class ScreenResult:
    identifier: str
    matches: list[Match] = field(default_factory=list)
    report: Report = field(default_factory=Report)
    semantic_backend: str = "lexical"
    thresholds_calibrated: bool = False

    def of_class(self, cls: DuplicateClass) -> list[Match]:
        return [m for m in self.matches if m.duplicate_class is cls]

    @property
    def blocking(self) -> list[Match]:
        """Matches not explained by a declared relationship (X-6)."""
        return [m for m in self.matches
                if m.explained_by is None
                and m.duplicate_class in (DuplicateClass.EXACT,
                                          DuplicateClass.NEAR,
                                          DuplicateClass.CROSS_RELEASE)]

    @property
    def ok(self) -> bool:
        return not self.blocking


class DuplicateScreen:
    """Screens a candidate against the complete corpus history (§8)."""

    def __init__(self, history: Iterable[CorpusEntry],
                 config: ScreeningConfig | None = None,
                 semantic: SemanticBackend | None = None):
        self.history = list(history)
        self.config = config or ScreeningConfig()
        self.semantic = semantic or LexicalSemanticBackend()

    # -- individual classes ---------------------------------------------

    def _declared(self, lineage: list | None, other_id: str) -> str | None:
        for entry in lineage or []:
            if (isinstance(entry, dict) and entry.get("target") == other_id
                    and entry.get("relation") in EXPLANATORY_RELATIONS):
                return str(entry["relation"])
        return None

    def screen(self, identifier: str, text: str, *, domain: str | None = None,
               category: str | None = None, length_bucket: str | None = None,
               split: str | None = None, lineage: list | None = None,
               topic_group_id: str | None = None, author: str | None = None,
               session: str | None = None) -> ScreenResult:
        result = ScreenResult(
            identifier=identifier,
            semantic_backend=getattr(self.semantic, "name", "custom"),
            thresholds_calibrated=self.config.calibrated,
        )
        r = result.report
        r.checked = len(self.history)

        if not self.config.calibrated:
            r.warn("CAS 8.2", "THRESHOLDS_NOT_CALIBRATED",
                   "near-duplicate thresholds are documented defaults, not "
                   "values calibrated on known-independent same-register text; "
                   "a pass is not a calibrated result", identifier)

        cand_raw = raw_checksum(text)
        cand_norm = normalized_checksum(text)
        near_threshold = self.config.near_threshold_for(domain)

        for other in self.history:
            if other.identifier == identifier:
                continue
            declared = self._declared(lineage, other.identifier)

            # 8.1 exact — both forms.
            if other.raw_checksum == cand_raw or other.normalized_checksum == cand_norm:
                same_form = "raw bytes" if other.raw_checksum == cand_raw else "normalized text"
                result.matches.append(Match(
                    DuplicateClass.EXACT, other.identifier, 1.0, declared,
                    f"identical {same_form}"))
                r.error("CAS 8.1", "EXACT_DUPLICATE",
                        f"identical {same_form} to {other.identifier}; the "
                        "newcomer is rejected", identifier)
                if split and other.split and split != other.split:
                    r.error("CAS 8.1", "CROSS_SPLIT_COLLISION",
                            f"exact collision with {other.identifier} in split "
                            f"{other.split!r}; investigate for contributor reuse "
                            "or leakage", identifier)
                continue

            if other.text is None:
                continue  # redacted tombstone: only checksums survive

            # 8.2 near.
            score = near_similarity(text, other.text)
            if score >= near_threshold:
                result.matches.append(Match(
                    DuplicateClass.NEAR, other.identifier, score, declared,
                    f"shingle similarity {score:.3f} >= {near_threshold:.2f}"))
                if declared:
                    r.warn("CAS 8.2", "NEAR_DUPLICATE_DECLARED",
                           f"high similarity to {other.identifier} ({score:.3f}) "
                           f"explained by declared relation {declared!r}",
                           identifier)
                else:
                    r.error("CAS 8.2", "NEAR_DUPLICATE_UNDECLARED",
                            f"similarity {score:.3f} to {other.identifier} exceeds "
                            f"the {domain or 'default'} threshold "
                            f"{near_threshold:.2f} with no declared relationship "
                            "(X-6); hold pending contributor explanation",
                            identifier)

            # 8.3 semantic — flags, never decides.
            sem = self.semantic.similarity(text, other.text)
            if sem >= self.config.semantic_threshold:
                same_topic_group = (topic_group_id is not None
                                    and topic_group_id == other.topic_group_id)
                same_cell = (category == other.category
                             and length_bucket == other.length_bucket)
                result.matches.append(Match(
                    DuplicateClass.SEMANTIC, other.identifier, sem, declared,
                    f"semantic similarity {sem:.3f}"))
                if same_topic_group:
                    # The paired design the corpus requires (§8.3).
                    pass
                elif same_cell:
                    r.warn("CAS 8.3", "SEMANTIC_OVERLAP_IN_CELL",
                           f"semantic overlap {sem:.3f} with {other.identifier} in "
                           "the same cell with no declared topic group; a cell "
                           "MUST NOT be dominated by restatements of one "
                           "situation — reviewer diversity judgment required",
                           identifier)

            # 8.4 template.
            tmpl = template_similarity(text, other.text)
            if tmpl >= self.config.template_threshold:
                result.matches.append(Match(
                    DuplicateClass.TEMPLATE, other.identifier, tmpl, declared,
                    f"structural similarity {tmpl:.3f}"))
                r.warn("CAS 8.4", "TEMPLATE_CONVERGENCE",
                       f"structural skeleton shared with {other.identifier} "
                       f"({tmpl:.3f}); template screening is mandatory for every "
                       "AI generation run, whose economics make templating the "
                       "default outcome unless resisted", identifier)

            # 8.6 cross-release: split protection.
            if split == "hidden" and other.split in ("dev", "test"):
                if score >= near_threshold or other.raw_checksum == cand_raw:
                    r.error("CAS 8.6", "HIDDEN_SIMILARITY_TO_PUBLIC",
                            f"similar to {other.identifier} in split {other.split!r}; "
                            "material similar to DEV or TEST MUST NOT enter HIDDEN, "
                            "because memorization of public material must never "
                            "pay inside the hidden evaluation", identifier)
                    result.matches.append(Match(
                        DuplicateClass.CROSS_RELEASE, other.identifier, score,
                        None, f"blocks HIDDEN entry (other split={other.split})"))

        # 8.5 style — share caps over the target cell.
        result.report.extend(self._style_caps(identifier, category,
                                              length_bucket, author, session))
        return result

    def _style_caps(self, identifier: str, category: str | None,
                    length_bucket: str | None, author: str | None,
                    session: str | None) -> Report:
        """§8.5: no single voice may dominate a cell (A-13, X-12)."""
        r = Report()
        cfg = self.config
        if cfg.max_author_share_per_cell is None and cfg.max_session_share_per_cell is None:
            r.warn("CAS 8.5", "SHARE_CAPS_UNSET",
                   "author/session share caps are not configured; §8.5 requires "
                   "them recorded in the coverage plan and enforced at "
                   "acceptance (TD-G04)", identifier)
            return r

        cell = [e for e in self.history
                if e.category == category and e.length_bucket == length_bucket]
        total = len(cell) + 1  # including this candidate

        def enforce(kind: str, cap: float, matching: int, subject: str,
                    code: str) -> None:
            # A share cap is only *satisfiable* once the cell holds enough
            # samples for one contributor to fall under it: with a 50% cap the
            # first sample is unavoidably 100% of its cell. Enforcing below
            # that threshold would reject every cell's first sample and make
            # the corpus impossible to populate — so the cap is reported as
            # not-yet-enforceable rather than failed. This is arithmetic, not
            # a policy choice; whether the denominator should instead be the
            # cell's *target* size is a governance question (TD-G09).
            minimum = math.ceil(1 / cap) if cap > 0 else 0
            if total < minimum:
                r.warn("CAS 8.5", "SHARE_CAP_NOT_YET_ENFORCEABLE",
                       f"{kind} cap {cap:.0%} cannot be satisfied by any sample "
                       f"until cell ({category}, {length_bucket}) holds "
                       f"{minimum}; it holds {total}", identifier)
                return
            share = matching / total
            if share > cap:
                r.error("CAS 8.5", code,
                        f"{kind} {subject!r} would hold {share:.0%} of cell "
                        f"({category}, {length_bucket}), above the {cap:.0%} cap; "
                        "the cell would secretly become a single-voice benchmark",
                        identifier)

        if author and cfg.max_author_share_per_cell is not None:
            enforce("author", cfg.max_author_share_per_cell,
                    sum(1 for e in cell if e.author == author) + 1,
                    author, "AUTHOR_SHARE_CAP_EXCEEDED")

        if session and cfg.max_session_share_per_cell is not None:
            enforce("generation session", cfg.max_session_share_per_cell,
                    sum(1 for e in cell if e.session == session) + 1,
                    session, "SESSION_SHARE_CAP_EXCEEDED")
        return r
