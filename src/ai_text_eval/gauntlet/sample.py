"""The Sample record (Section 5.2) and its JSONL serialization.

Parsing here is deliberately permissive: a Sample can be constructed from a
malformed record so that the validator (validate.py) can report *every*
problem with it at once. Rejecting on the first bad field would turn corpus
authoring into a guessing game.

The `text` field is never normalized on load. Section 5.2 requires storage to
be byte-exact, with all normalization happening inside detectors where it can
be tested; normalizing here would silently defeat Track V's encoding attacks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ai_text_eval.gauntlet.spec import FIELD_ORDER


@dataclass
class Sample:
    """One corpus record. Field names mirror Section 5.2 exactly."""

    raw: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""
    source_line: int = 0

    # -- typed accessors -------------------------------------------------
    # Accessors return None rather than raising when a field is missing or of
    # the wrong type, because the validator needs to see the whole record.

    def get(self, name: str, default: Any = None) -> Any:
        return self.raw.get(name, default)

    def _str(self, name: str) -> str | None:
        v = self.raw.get(name)
        return v if isinstance(v, str) else None

    @property
    def id(self) -> str | None:
        return self._str("id")

    @property
    def split(self) -> str | None:
        return self._str("split")

    @property
    def text(self) -> str | None:
        return self._str("text")

    @property
    def category(self) -> str | None:
        return self._str("category")

    @property
    def track(self) -> str | None:
        return self._str("track")

    @property
    def label(self) -> str | None:
        return self._str("label")

    @property
    def length_bucket(self) -> str | None:
        return self._str("length_bucket")

    @property
    def length_words(self) -> int | None:
        v = self.raw.get("length_words")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    @property
    def provenance_tier(self) -> str | None:
        return self._str("provenance_tier")

    @property
    def difficulty(self) -> str | None:
        return self._str("difficulty")

    @property
    def ai_token_share(self) -> float | None:
        v = self.raw.get("ai_token_share")
        if isinstance(v, bool):
            return None
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def span_map(self) -> list | None:
        v = self.raw.get("span_map")
        return v if isinstance(v, list) else None

    @property
    def noisy_label(self) -> bool:
        return bool(self.raw.get("noisy_label", False))

    @property
    def topic_group_id(self) -> str | None:
        return self._str("topic_group_id")

    @property
    def transforms(self) -> list:
        v = self.raw.get("transforms")
        return v if isinstance(v, list) else []

    @property
    def generator(self) -> dict | None:
        v = self.raw.get("generator")
        return v if isinstance(v, dict) else None

    @property
    def cell(self) -> tuple[str | None, str | None, str | None]:
        """(category, length_bucket, split) — the unit of Section 2.6 sizing."""
        return (self.category, self.length_bucket, self.split)

    def to_ordered_dict(self) -> dict[str, Any]:
        """Re-emit with the fixed field order of Section 5.1.

        Unknown fields are preserved and appended, so a newer schema round
        trips through older tooling without silent data loss.
        """
        out = {k: self.raw[k] for k in FIELD_ORDER if k in self.raw}
        for k in self.raw:
            if k not in out:
                out[k] = self.raw[k]
        return out


def parse_jsonl(path: Path) -> Iterator[Sample]:
    """Yield Samples from a JSONL file, reporting location on malformed lines."""
    with Path(path).open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as err:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {err}") from err
            if not isinstance(rec, dict):
                raise ValueError(f"{path}:{line_no}: record must be a JSON object")
            yield Sample(raw=rec, source_file=str(path), source_line=line_no)


def write_jsonl(path: Path, samples: list[Sample]) -> None:
    """Write samples in fixed field order (Section 5.1)."""
    with Path(path).open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s.to_ordered_dict(), ensure_ascii=False) + "\n")
