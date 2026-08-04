"""Corpus and manifest loading, with split discipline enforced at the API.

Section 2.3 gives each split a different epistemic status, and Section 9.4
forbids reporting DEV numbers as results or selecting thresholds on
TEST/HIDDEN. Those rules are enforced here rather than left to the caller's
memory: `Corpus.for_reporting` refuses DEV, and `Corpus.for_threshold_selection`
refuses TEST and HIDDEN. Getting this wrong is silent and invalidates every
number downstream, which is exactly the class of error the specification
exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ai_text_eval.gauntlet.sample import Sample, parse_jsonl
from ai_text_eval.gauntlet.spec import CORPUS_DIR, SPLITS, TRACKS

TRACK_FILES = {t: f"track_{t.lower()}.jsonl" for t in TRACKS}


class SplitDisciplineError(RuntimeError):
    """Raised when a split is used for a purpose Section 9.4 forbids."""


@dataclass
class Manifest:
    """corpus/manifest.json (Section 5.4)."""

    raw: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def corpus_version(self) -> str | None:
        v = self.raw.get("corpus_version")
        return v if isinstance(v, str) else None

    @property
    def checksums(self) -> dict:
        v = self.raw.get("checksums")
        return v if isinstance(v, dict) else {}

    @property
    def category_exemptions(self) -> dict:
        """Bucket exemptions per Section 2.5, recorded in the manifest."""
        v = self.raw.get("category_exemptions")
        return v if isinstance(v, dict) else {}

    @property
    def phase(self) -> str:
        """Cell-sizing phase from Section 2.6. Defaults to the v1.0 targets."""
        v = self.raw.get("phase")
        return v if isinstance(v, str) else "v1.0"

    def exempt_buckets(self, category: str) -> set[str]:
        v = self.category_exemptions.get(category, {})
        buckets = v.get("exempt_buckets", []) if isinstance(v, dict) else []
        return set(buckets) if isinstance(buckets, list) else set()


def load_manifest(corpus_dir: Path | None = None) -> Manifest:
    d = Path(corpus_dir or CORPUS_DIR)
    path = d / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    return Manifest(raw=json.loads(path.read_text(encoding="utf-8")), path=path)


@dataclass
class Corpus:
    samples: list[Sample] = field(default_factory=list)
    manifest: Manifest | None = None
    root: Path | None = None

    def __len__(self) -> int:
        return len(self.samples)

    # -- slicing ---------------------------------------------------------

    def filter(self, **axes) -> "Corpus":
        """Slice along any axis (P1: results must be sliceable by axis).

        Values may be a scalar or a collection; `None` matches everything.
        """
        out = []
        for s in self.samples:
            keep = True
            for name, want in axes.items():
                if want is None:
                    continue
                have = s.get(name)
                if isinstance(want, (set, frozenset, list, tuple)):
                    if have not in want:
                        keep = False
                        break
                elif have != want:
                    keep = False
                    break
            if keep:
                out.append(s)
        return Corpus(samples=out, manifest=self.manifest, root=self.root)

    def split(self, name: str) -> "Corpus":
        if name not in SPLITS:
            raise ValueError(f"unknown split {name!r}; expected one of {SPLITS}")
        return self.filter(split=name)

    def for_reporting(self, name: str) -> "Corpus":
        """Split intended for reported results. Section 2.3 excludes DEV.

        DEV is assumed contaminated — any detector may have trained on it —
        so numbers from it are debugging output, never results.
        """
        if name == "dev":
            raise SplitDisciplineError(
                "Section 2.3: DEV is assumed contaminated and its numbers are "
                "never reported as results. Use TEST, or call .split('dev') "
                "explicitly for debugging."
            )
        return self.split(name)

    def for_threshold_selection(self, name: str = "dev") -> "Corpus":
        """Split permitted for choosing thresholds. Section 9.4 allows DEV only."""
        if name != "dev":
            raise SplitDisciplineError(
                "Section 9.4: thresholds are chosen on DEV, never on TEST or "
                "HIDDEN. Selecting on the evaluation split invalidates every "
                "number computed from it."
            )
        return self.split(name)

    # -- grouping --------------------------------------------------------

    def cells(self) -> dict[tuple, list[Sample]]:
        """Group by (category, length_bucket, split) — the Section 2.6 cell."""
        out: dict[tuple, list[Sample]] = {}
        for s in self.samples:
            out.setdefault(s.cell, []).append(s)
        return out

    def by(self, axis: str) -> dict:
        out: dict = {}
        for s in self.samples:
            out.setdefault(s.get(axis), []).append(s)
        return out

    def ids(self) -> list[str]:
        return [s.id for s in self.samples if s.id]


def load_corpus(corpus_dir: Path | None = None,
                require_manifest: bool = True) -> Corpus:
    """Load every track file under <corpus_dir>/samples/.

    Missing track files are not an error: an empty corpus is a valid state and
    the release validator is what decides whether it is releasable.
    """
    root = Path(corpus_dir or CORPUS_DIR)
    samples: list[Sample] = []
    samples_dir = root / "samples"
    if samples_dir.is_dir():
        for track in TRACKS:
            path = samples_dir / TRACK_FILES[track]
            if path.is_file():
                samples.extend(parse_jsonl(path))

    manifest = None
    if (root / "manifest.json").is_file():
        manifest = load_manifest(root)
    elif require_manifest:
        raise FileNotFoundError(
            f"manifest not found under {root}. Pass require_manifest=False to "
            "load samples without one."
        )
    return Corpus(samples=samples, manifest=manifest, root=root)
