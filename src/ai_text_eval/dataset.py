"""Dataset loading.

Format: JSON Lines, one record per line:

    {"text": "...", "label": 1, "source": "gpt-4o", "meta": {...}}

label: 1 = AI-generated, 0 = human-written. `source` and `meta` are
optional. Paired evasion files use:

    {"original": "...", "rephrased": "...", "meta": {...}}

The repo bundles small demo corpora under data/ — enough to smoke-test
the harness and demonstrate the metrics, *not* a research benchmark.
For real evaluations point the CLI at corpora like HC3, M4, or RAID
(see README: "Getting real data").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@dataclass
class LabeledText:
    text: str
    label: int  # 1 = AI, 0 = human
    source: str = "unknown"
    meta: dict = field(default_factory=dict)


@dataclass
class EvasionPair:
    original: str
    rephrased: str
    meta: dict = field(default_factory=dict)


def load_labeled_jsonl(path: str | Path) -> list[LabeledText]:
    items: list[LabeledText] = []
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as err:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {err}") from err
            if "text" not in rec or "label" not in rec:
                raise ValueError(f"{path}:{line_no}: record needs 'text' and 'label'")
            label = int(rec["label"])
            if label not in (0, 1):
                raise ValueError(f"{path}:{line_no}: label must be 0 or 1")
            items.append(
                LabeledText(
                    text=str(rec["text"]),
                    label=label,
                    source=str(rec.get("source", "unknown")),
                    meta=rec.get("meta", {}) or {},
                )
            )
    return items


def load_pairs_jsonl(path: str | Path) -> list[EvasionPair]:
    pairs: list[EvasionPair] = []
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "original" not in rec or "rephrased" not in rec:
                raise ValueError(f"{path}:{line_no}: record needs 'original' and 'rephrased'")
            pairs.append(
                EvasionPair(
                    original=str(rec["original"]),
                    rephrased=str(rec["rephrased"]),
                    meta=rec.get("meta", {}) or {},
                )
            )
    return pairs


def load_demo_corpus() -> list[LabeledText]:
    """The bundled human+AI demo corpus."""
    human = load_labeled_jsonl(DATA_DIR / "demo_human.jsonl")
    ai = load_labeled_jsonl(DATA_DIR / "demo_ai.jsonl")
    return human + ai


def load_demo_pairs() -> list[EvasionPair]:
    return load_pairs_jsonl(DATA_DIR / "demo_evasion_pairs.jsonl")
