"""Decontamination screening (R-09, CAS §3.7, BS §4.9, §5.4, §9.1(d)).

BS §4.9 requires 13-gram containment checks of every sample against
(a) known public detector training sets, (b) common pretraining corpus
indices where available, and (c) the corpus's own DEV split; any TEST/HIDDEN
sample with a contiguous 50+ character overlap with a public detection corpus
is replaced or moved to DEV, and the scan summary ships in the manifest.
CAS §3.7 moves the screen forward to candidacy so contaminated material never
consumes review effort.

**The one rule this module exists to protect.** A scan that could not consult
its reference corpora has not passed — it has not run. Every verdict is
therefore ternary (`CLEAN` / `CONTAMINATED` / `INCOMPLETE`), never boolean.
A hit is conclusive on its own; a miss is conclusive only when every required
source was present. Collapsing "nothing found" and "nothing looked" into one
green result is the single failure that would make this module worse than
having none, because a release would then ship BS §9.1(d) as satisfied on the
strength of an empty source list.

**No corpus is bundled or simulated.** The external detection corpora (HC3,
the GPT-2 output corpus, M4, RAID, MGTBench) are a declared infrastructure
dependency (TD-X01, blocked by TD-B03). `ReferenceSource` is the attachment
point: a real corpus becomes a source by answering "do you contain this
13-gram?". `NgramIndex` is a container for text the caller supplies; it never
invents text. Source (c), the DEV split, is the project's own data and is
buildable today — `dev_split_source` does exactly that.

**Severity depends on which document is speaking.** CAS §3.7 screens at
candidacy to save review effort; BS §9.1(d) is the release gate. So an
incomplete scan *warns* at candidacy — otherwise no candidate could ever be
submitted while TD-X01 is unresolved, and the corpus would be unbuildable —
and *errors* at release, where the scan is a stated acceptance criterion.
Contamination that is actually found blocks at both stages. This reading is
recorded as TD-A04 rather than assumed silently.

**What §4.9 does not specify.** It mandates 13-gram containment checks but
sets a numeric rule only for the contiguous 50-character overlap. There is no
stated threshold on the containment *ratio*, so none is invented here: the
ratio is measured and reported for every source, and only the specified rule
decides. `ScreenConfig.containment_review_threshold` is `None` until
governance sets it (TD-G10), and `CONTAINMENT_THRESHOLD_UNSET` keeps the gap
from passing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol, Sequence

from ai_text_eval.gauntlet.findings import Report

#: BS §4.9: "13-gram containment checks of every sample".
NGRAM_N = 13

#: BS §4.9: "a contiguous 50+ character overlap with a public detection
#: corpus is replaced or moved to DEV". The one numeric rule the section
#: states, and the only one that decides anything here.
CONTIGUOUS_CHAR_LIMIT = 50

#: Splits whose contamination is a release-blocking defect. DEV is excluded
#: deliberately: BS §2.3 defines DEV as "assume contaminated: any detector may
#: have trained on it", so overlap there is expected rather than a finding.
PROTECTED_SPLITS = frozenset({"test", "hidden"})


class ReferenceKind(str, Enum):
    """The three source classes of BS §4.9."""

    DETECTION_CORPUS = "detection_corpus"      # 4.9(a)
    PRETRAINING_INDEX = "pretraining_index"    # 4.9(b), "where available"
    DEV_SPLIT = "dev_split"                    # 4.9(c)


class Verdict(str, Enum):
    """Ternary by design — see the module docstring."""

    CLEAN = "clean"
    CONTAMINATED = "contaminated"
    INCOMPLETE = "incomplete"


class Stage(str, Enum):
    """Which specification is asking. Governs severity, never findings."""

    CANDIDACY = "candidacy"   # CAS §3.7
    RELEASE = "release"       # BS §9.1(d)


@dataclass(frozen=True)
class RequiredReference:
    """A source BS §4.9 names, and what its absence means.

    `optional` marks §4.9(b)'s "where available" — the only source class the
    specification itself treats as best-effort.
    """

    name: str
    kind: ReferenceKind
    citation: str
    debt_id: str = ""
    optional: bool = False


#: The sources BS §4.9 names. The detection corpora are given by example
#: ("e.g., HC3, GPT-2 output corpus, M4, RAID, MGTBench releases"), so this is
#: the required floor, not a closed list: registering more sources is always
#: admissible, registering fewer leaves the scan incomplete.
REQUIRED_REFERENCES: tuple[RequiredReference, ...] = (
    RequiredReference("HC3", ReferenceKind.DETECTION_CORPUS, "BS 4.9(a)", "TD-X01"),
    RequiredReference("GPT-2 output corpus", ReferenceKind.DETECTION_CORPUS,
                      "BS 4.9(a)", "TD-X01"),
    RequiredReference("M4", ReferenceKind.DETECTION_CORPUS, "BS 4.9(a)", "TD-X01"),
    RequiredReference("RAID", ReferenceKind.DETECTION_CORPUS, "BS 4.9(a)", "TD-X01"),
    RequiredReference("MGTBench", ReferenceKind.DETECTION_CORPUS, "BS 4.9(a)", "TD-X01"),
    RequiredReference("pretraining index", ReferenceKind.PRETRAINING_INDEX,
                      "BS 4.9(b)", "TD-X01", optional=True),
    RequiredReference("DEV split", ReferenceKind.DEV_SPLIT, "BS 4.9(c)"),
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)


# -- tokenization and shingling ------------------------------------------

@dataclass(frozen=True)
class _Word:
    key: str    # case-folded, for matching
    start: int  # offset into the original text, for exact char measurement
    end: int


def words_with_offsets(text: str) -> list[_Word]:
    """Case-folded word tokens keeping their offsets in the original text.

    Matching is done on the folded form so a source built from differently
    cased text still matches; character lengths are measured against the
    original so the 50-character rule is exact in the candidate.
    """
    return [_Word(m.group(0).casefold(), m.start(), m.end())
            for m in _WORD_RE.finditer(text)]


def ngrams(text: str, n: int = NGRAM_N) -> list[str]:
    """The word n-grams of `text`, space-joined and case-folded.

    Shorter than `n` words yields no n-grams: a 12-word sample cannot be
    13-gram screened, which the scan reports rather than treating as clean.
    """
    keys = [w.key for w in words_with_offsets(text)]
    return [" ".join(keys[i:i + n]) for i in range(len(keys) - n + 1)]


# -- reference sources ---------------------------------------------------

class ReferenceSource(Protocol):
    """A corpus that can answer 13-gram membership questions.

    Deliberately minimal: membership is the only capability every real corpus
    can provide cheaply (a hashed shingle set), so the interface does not
    force an implementation to be in-memory, or even local.

    Two optional refinements, both discovered by duck-typing so a minimal
    source stays a two-line class:

    - `max_contiguous_chars(text) -> int | None` measures the longest literal
      shared substring exactly. Without it the scan estimates that length
      from runs of consecutive matching n-grams (see `SourceScan`).
    - `contains_excluding(ngram, identifier) -> bool` answers membership while
      ignoring one sample's own contribution. A source built from the corpus
      itself needs this: without it every DEV sample matches the DEV index at
      100% containment because it *is* in the DEV index, and the scan would
      report every sample as contaminated by itself.
    """

    name: str
    kind: str
    version: str

    def contains(self, ngram: str) -> bool: ...


class NgramIndex:
    """An in-memory `ReferenceSource` over text the caller supplies.

    This is a container, not a corpus. It holds whatever text is handed to it
    and nothing else; there is no bundled, generated, or sampled data behind
    it. A production integration either fills one of these from a licensed
    corpus on disk or implements `ReferenceSource` over its own index.
    """

    def __init__(self, name: str, kind: ReferenceKind | str,
                 version: str = "unversioned", n: int = NGRAM_N):
        self.name = name
        self.kind = kind.value if isinstance(kind, ReferenceKind) else str(kind)
        self.version = version
        self.n = n
        self._shingles: set[str] = set()
        self._documents = 0

    def add_text(self, text: str) -> "NgramIndex":
        self._shingles.update(ngrams(text, self.n))
        self._documents += 1
        return self

    def add_texts(self, texts: Iterable[str]) -> "NgramIndex":
        for text in texts:
            self.add_text(text)
        return self

    def contains(self, ngram: str) -> bool:
        return ngram in self._shingles

    @property
    def documents(self) -> int:
        return self._documents

    @property
    def shingles(self) -> int:
        return len(self._shingles)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"NgramIndex({self.name!r}, kind={self.kind!r}, "
                f"documents={self._documents}, shingles={len(self._shingles)})")


class DevSplitIndex:
    """Source (c): the corpus's own DEV split, built from the project's data.

    Unlike (a) and (b) this needs nothing external, so a scan has no excuse
    for omitting it.

    It records which sample contributed each n-gram so a sample can be
    screened against the split *without* matching itself. One index therefore
    serves every scan; rebuilding a self-excluding index per sample would be
    quadratic and easy to forget in one call site.
    """

    kind = ReferenceKind.DEV_SPLIT.value

    def __init__(self, version: str = "unversioned", n: int = NGRAM_N,
                 name: str = "DEV split"):
        self.name = name
        self.version = version
        self.n = n
        self._owners: dict[str, set[str]] = {}

    def add_sample(self, identifier: str, text: str) -> "DevSplitIndex":
        for gram in ngrams(text, self.n):
            self._owners.setdefault(gram, set()).add(identifier)
        return self

    def add_samples(self, entries: Iterable[tuple[str, str]]) -> "DevSplitIndex":
        for identifier, text in entries:
            self.add_sample(identifier, text)
        return self

    def contains(self, ngram: str) -> bool:
        return ngram in self._owners

    def contains_excluding(self, ngram: str, identifier: str | None) -> bool:
        owners = self._owners.get(ngram)
        if not owners:
            return False
        return bool(owners - {identifier})

    def owners(self, ngram: str) -> frozenset[str]:
        return frozenset(self._owners.get(ngram, ()))

    @property
    def samples(self) -> int:
        return len({o for owners in self._owners.values() for o in owners})

    @property
    def shingles(self) -> int:
        return len(self._owners)


def dev_split_source(entries: Iterable[tuple[str, str]],
                     version: str = "unversioned",
                     n: int = NGRAM_N) -> DevSplitIndex:
    """Build source (c) from `(identifier, text)` pairs of the DEV split."""
    return DevSplitIndex(version, n).add_samples(entries)


# -- configuration -------------------------------------------------------

@dataclass
class ScreenConfig:
    """Screening parameters.

    `containment_review_threshold` is `None` because BS §4.9 states no
    threshold on the containment ratio. Setting it here would be inventing a
    normative number; leaving it None reports the measurement and lets the
    stated 50-character rule decide (TD-G10).
    """

    n: int = NGRAM_N
    contiguous_char_limit: int = CONTIGUOUS_CHAR_LIMIT
    containment_review_threshold: float | None = None


# -- scan results --------------------------------------------------------

@dataclass
class SourceScan:
    """One sample measured against one source."""

    source: str
    kind: str
    version: str
    total_ngrams: int
    matched_ngrams: int
    longest_run_ngrams: int
    longest_contiguous_chars: int
    #: False when `longest_contiguous_chars` was estimated from runs of
    #: consecutive matching n-grams rather than measured by the source. The
    #: estimate over-approximates: consecutive n-grams each appearing in a
    #: source does not prove they appear contiguously *in* it. It errs toward
    #: flagging, which is the safe direction — a false flag costs one sample,
    #: a missed one corrupts every result computed from the split.
    exact_char_measure: bool = False

    @property
    def containment(self) -> float:
        if not self.total_ngrams:
            return 0.0
        return self.matched_ngrams / self.total_ngrams

    def to_dict(self) -> dict:
        return {"source": self.source, "kind": self.kind, "version": self.version,
                "total_ngrams": self.total_ngrams,
                "matched_ngrams": self.matched_ngrams,
                "containment": round(self.containment, 6),
                "longest_run_ngrams": self.longest_run_ngrams,
                "longest_contiguous_chars": self.longest_contiguous_chars,
                "exact_char_measure": self.exact_char_measure}


@dataclass
class SampleScan:
    """The result of screening one sample against every registered source."""

    identifier: str
    split: str | None
    stage: Stage
    verdict: Verdict
    sources: list[SourceScan] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    report: Report = field(default_factory=Report)
    #: Any detection-corpus or DEV-split overlap makes the sample eligible for
    #: DEV only (BS §4.9). Split assignment (R-13, §10.3) is a release-manager
    #: decision, so this is a mechanical input to it, not the decision itself.
    dev_only: bool = False

    @property
    def complete(self) -> bool:
        """True only when every non-optional BS §4.9 source was consulted."""
        return not self.missing_sources

    @property
    def contaminated(self) -> bool:
        return self.verdict is Verdict.CONTAMINATED

    @property
    def passed(self) -> bool:
        """BS §9.1(d) sense of "passed": clean *and* complete.

        `not passed` is not the same as `contaminated` — an incomplete scan is
        neither.
        """
        return self.verdict is Verdict.CLEAN

    def to_dict(self) -> dict:
        return {"id": self.identifier, "split": self.split,
                "verdict": self.verdict.value, "complete": self.complete,
                "dev_only": self.dev_only,
                "sources": [s.to_dict() for s in self.sources],
                "missing_sources": list(self.missing_sources)}


@dataclass
class ScanSummary:
    """The corpus-wide scan that BS §5.4 ships in the manifest."""

    stage: Stage
    scans: list[SampleScan] = field(default_factory=list)
    sources_consulted: list[dict] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    report: Report = field(default_factory=Report)
    config: ScreenConfig = field(default_factory=ScreenConfig)

    @property
    def complete(self) -> bool:
        return not self.missing_sources

    def by_verdict(self) -> dict[str, int]:
        counts = {v.value: 0 for v in Verdict}
        for scan in self.scans:
            counts[scan.verdict.value] += 1
        return counts

    @property
    def contaminated_ids(self) -> list[str]:
        return [s.identifier for s in self.scans if s.contaminated]

    @property
    def incomplete_ids(self) -> list[str]:
        return [s.identifier for s in self.scans
                if s.verdict is Verdict.INCOMPLETE]

    def to_manifest(self, timestamp: str) -> dict:
        """The BS §5.4 manifest block.

        `status` is the ternary verdict, never a bare boolean: a manifest that
        said `"passed": true` after consulting no sources would be the exact
        misrepresentation this module exists to prevent.
        """
        if self.contaminated_ids:
            status = "contaminated"
        elif not self.complete:
            status = "incomplete"
        else:
            status = "passed"
        return {
            "status": status,
            "scanned_at": timestamp,
            "samples_scanned": len(self.scans),
            "ngram_n": self.config.n,
            "contiguous_char_limit": self.config.contiguous_char_limit,
            "containment_review_threshold": self.config.containment_review_threshold,
            "verdicts": self.by_verdict(),
            "sources_consulted": list(self.sources_consulted),
            "sources_missing": list(self.missing_sources),
            "contaminated": list(self.contaminated_ids),
            "incomplete": list(self.incomplete_ids),
        }


# -- the screen ----------------------------------------------------------

class DecontaminationScreen:
    """13-gram containment screening against the BS §4.9 sources.

    Registering zero sources is allowed and is the current state of the
    world (TD-X01 / TD-B03); what is not allowed is such a scan reporting a
    pass. Every scan names the sources it could not consult, and the verdict
    is `INCOMPLETE` until they are attached.
    """

    def __init__(self, sources: Iterable[ReferenceSource] = (),
                 config: ScreenConfig | None = None,
                 required: Sequence[RequiredReference] = REQUIRED_REFERENCES):
        self.sources = list(sources)
        self.config = config or ScreenConfig()
        self.required = tuple(required)

    # -- source bookkeeping ---------------------------------------------

    def missing_sources(self) -> list[str]:
        """Required BS §4.9 sources with no registered implementation.

        Matched by name, case-insensitively; §4.9(b) is exempt because the
        specification itself qualifies it with "where available".
        """
        have = {s.name.casefold() for s in self.sources}
        return [r.name for r in self.required
                if not r.optional and r.name.casefold() not in have]

    def optional_missing(self) -> list[str]:
        have = {s.name.casefold() for s in self.sources}
        return [r.name for r in self.required
                if r.optional and r.name.casefold() not in have]

    def sources_consulted(self) -> list[dict]:
        return [{"name": s.name, "kind": getattr(s, "kind", "unknown"),
                 "version": getattr(s, "version", "unversioned")}
                for s in self.sources]

    # -- one sample ------------------------------------------------------

    def _measure(self, source: ReferenceSource, identifier: str, text: str,
                 words: list[_Word], grams: list[str]) -> SourceScan:
        # A source built from the corpus contains the sample under scan; ask
        # it to ignore that sample's own contribution where it can.
        excluding = getattr(source, "contains_excluding", None)
        if callable(excluding):
            hits = [i for i, g in enumerate(grams) if excluding(g, identifier)]
        else:
            hits = [i for i, g in enumerate(grams) if source.contains(g)]
        longest_run = 0
        longest_chars = 0
        run_start = None
        previous = None
        for i in hits + [None]:  # sentinel closes the final run
            if previous is not None and i == previous + 1:
                previous = i
                continue
            if run_start is not None:
                run = previous - run_start + 1
                # A run of `run` consecutive n-grams covers words
                # run_start .. previous + n - 1 in the candidate.
                last_word = min(previous + self.config.n - 1, len(words) - 1)
                chars = words[last_word].end - words[run_start].start
                longest_run = max(longest_run, run)
                longest_chars = max(longest_chars, chars)
            run_start = i
            previous = i

        exact = False
        measurer = getattr(source, "max_contiguous_chars", None)
        if callable(measurer):
            measured = measurer(text)
            if measured is not None:
                longest_chars = int(measured)
                exact = True

        return SourceScan(
            source=source.name, kind=getattr(source, "kind", "unknown"),
            version=getattr(source, "version", "unversioned"),
            total_ngrams=len(grams), matched_ngrams=len(hits),
            longest_run_ngrams=longest_run, longest_contiguous_chars=longest_chars,
            exact_char_measure=exact)

    def scan(self, identifier: str, text: str, *, split: str | None = None,
             stage: Stage = Stage.CANDIDACY) -> SampleScan:
        """Screen one sample. Reports; never edits, moves, or replaces it."""
        r = Report(checked=1)
        split_key = (split or "").casefold()
        protected = split_key in PROTECTED_SPLITS

        words = words_with_offsets(text)
        grams = ngrams(text, self.config.n)
        if not grams:
            r.warn("BS 4.9", "TOO_SHORT_TO_SCREEN",
                   f"{len(words)} words is shorter than the {self.config.n}-gram "
                   "window, so containment screening cannot run on this sample; "
                   "absence of a match here is not evidence of cleanliness",
                   identifier)

        if self.config.containment_review_threshold is None:
            r.warn("BS 4.9", "CONTAINMENT_THRESHOLD_UNSET",
                   "§4.9 mandates 13-gram containment checks but states no "
                   "threshold on the containment ratio; ratios are reported and "
                   "only the 50-character rule decides (TD-G10)", identifier)

        scans = [self._measure(s, identifier, text, words, grams)
                 for s in self.sources]
        found_contamination = False
        dev_only = False

        for source_scan in scans:
            over_limit = (source_scan.longest_contiguous_chars
                          >= self.config.contiguous_char_limit)
            chars = source_scan.longest_contiguous_chars

            if source_scan.kind == ReferenceKind.DETECTION_CORPUS.value and over_limit:
                # §4.9's remedy — "replaced or moved to DEV" — is scoped to
                # TEST/HIDDEN, and DEV is its *destination*. Overlap in DEV is
                # therefore the specified end state, not a defect: BS §2.3
                # already says "assume contaminated" of the whole split.
                dev_only = True
                if protected:
                    found_contamination = True
                    r.error("BS 4.9", "PUBLIC_CORPUS_OVERLAP",
                            f"{chars} contiguous characters overlap "
                            f"{source_scan.source}; a {split_key.upper()} sample "
                            "in this state must be replaced or moved to DEV — "
                            "this screen reports the overlap and does not choose "
                            "between the two remedies", identifier)
                elif split_key == "dev":
                    r.warn("BS 4.9", "PUBLIC_CORPUS_OVERLAP_IN_DEV",
                           f"{chars} contiguous characters overlap "
                           f"{source_scan.source}; DEV is assumed contaminated "
                           "(BS §2.3), so this is recorded rather than blocking, "
                           "and the sample must never be promoted", identifier)
                else:
                    r.warn("BS 4.9", "PUBLIC_CORPUS_OVERLAP_UNASSIGNED",
                           f"{chars} contiguous characters overlap "
                           f"{source_scan.source}; the sample has no split yet, "
                           "so this does not block, but §4.9 makes it eligible "
                           "for DEV only", identifier)

            elif source_scan.kind == ReferenceKind.PRETRAINING_INDEX.value and over_limit:
                r.warn("BS 4.9", "PRETRAINING_INDEX_OVERLAP",
                       f"{chars} contiguous characters overlap "
                       f"{source_scan.source}; §4.9(b) states no remedy for "
                       "pretraining-index overlap, so this is reported for the "
                       "release manager's decision", identifier)

            elif source_scan.kind == ReferenceKind.DEV_SPLIT.value and over_limit:
                dev_only = True
                if protected:
                    found_contamination = True
                    r.error("BS 4.9", "DEV_SPLIT_OVERLAP",
                            f"{chars} contiguous characters overlap the DEV "
                            "split; DEV is public and may be trained on, so a "
                            "TEST/HIDDEN sample sharing text with it leaks its "
                            "own answer", identifier)
                else:
                    r.warn("BS 4.9", "DEV_SPLIT_OVERLAP_UNPROTECTED",
                           f"{chars} contiguous characters overlap the DEV "
                           "split; harmless within DEV, but the sample must not "
                           "be assigned to TEST or HIDDEN", identifier)

            threshold = self.config.containment_review_threshold
            if threshold is not None and source_scan.containment >= threshold:
                r.warn("BS 4.9", "CONTAINMENT_ABOVE_REVIEW_THRESHOLD",
                       f"{source_scan.containment:.3f} of the sample's "
                       f"{self.config.n}-grams appear in {source_scan.source}",
                       identifier)

        missing = self.missing_sources()
        if missing:
            message = ("decontamination could not consult "
                       f"{', '.join(missing)}; BS §4.9 requires these sources and "
                       "a scan that did not run them has not passed (TD-X01, "
                       "blocked by TD-B03)")
            if stage is Stage.RELEASE:
                r.error("BS 9.1", "DECONTAMINATION_INCOMPLETE", message, identifier)
            else:
                r.warn("CAS 3.7", "DECONTAMINATION_INCOMPLETE", message, identifier)

        for name in self.optional_missing():
            r.warn("BS 4.9", "OPTIONAL_SOURCE_ABSENT",
                   f"{name} was not consulted; §4.9(b) qualifies this source "
                   "class with \"where available\", so its absence does not make "
                   "the scan incomplete", identifier)

        # A hit is conclusive on its own; a miss is conclusive only when the
        # scan was complete.
        if found_contamination:
            verdict = Verdict.CONTAMINATED
        elif missing or not grams:
            verdict = Verdict.INCOMPLETE
        else:
            verdict = Verdict.CLEAN

        return SampleScan(identifier=identifier, split=split, stage=stage,
                          verdict=verdict, sources=scans, dev_only=dev_only,
                          missing_sources=missing, report=r)

    # -- whole corpus ----------------------------------------------------

    def scan_corpus(self, samples: Iterable[tuple[str, str, str | None]],
                    stage: Stage = Stage.RELEASE) -> ScanSummary:
        """Screen `(identifier, text, split)` triples for a release scan."""
        summary = ScanSummary(stage=stage, config=self.config,
                              sources_consulted=self.sources_consulted(),
                              missing_sources=self.missing_sources())
        for identifier, text, split in samples:
            scan = self.scan(identifier, text, split=split, stage=stage)
            summary.scans.append(scan)
            summary.report.extend(scan.report)
        return summary


def release_gate(summary: ScanSummary) -> Report:
    """BS §9.1(d): "Decontamination scan (4.9) passed and shipped".

    Two conditions, and the second is the one that is easy to lose: the scan
    must have *passed*, and it must have actually covered the release. A
    summary over zero samples satisfies "no contamination found" vacuously.
    """
    r = Report(checked=len(summary.scans))

    if not summary.scans:
        r.error("BS 9.1", "DECONTAMINATION_NOT_RUN",
                "the release carries no decontamination scan; §9.1(d) requires "
                "the §4.9 scan to have passed and to ship in the manifest")
        return r

    if summary.missing_sources:
        r.error("BS 9.1", "DECONTAMINATION_INCOMPLETE",
                "the scan could not consult "
                f"{', '.join(summary.missing_sources)}; §9.1(d) is not satisfied "
                "by a scan that did not run (TD-X01)")

    for scan in summary.scans:
        if scan.verdict is Verdict.CONTAMINATED:
            r.error("BS 9.1", "CONTAMINATED_SAMPLE_IN_RELEASE",
                    "sample is contaminated and must be replaced or moved to "
                    "DEV before release", scan.identifier)
        elif scan.verdict is Verdict.INCOMPLETE:
            r.error("BS 9.1", "SAMPLE_NOT_FULLY_SCREENED",
                    "sample was not screened against every required source, so "
                    "its cleanliness is unknown rather than established",
                    scan.identifier)

    return r
