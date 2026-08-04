"""Benchmark runner skeleton (Sections 9.3, 9.4, 8.6).

Skeleton means: the execution spine, cell bookkeeping, split discipline and
worst-cell reporting are implemented; the task-specific metrics beyond T1 are
declared and raise NotImplementedError rather than returning a plausible
number. A runner that silently produced *something* for T3 or T4 would be
manufacturing evidence, which is worse than an unimplemented task.

Two rules are enforced structurally rather than by convention:

  - Tasks are never averaged together (Section 9.3). There is no API that
    combines T1..T4 into one number.
  - The headline is the WORST cell, never the mean (P4, Section 9.3(a)), and
    no result object exposes a corpus-level scalar without its per-cell table.

This module does not import or modify any detector. It consumes anything with
`.score(text) -> DetectorResult`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ai_text_eval.gauntlet.loader import Corpus
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.spec import (
    AI_INVOLVED_LABELS,
    HIDDEN_SCORE_QUANTUM,
    PRIMARY_FPR_POINTS,
    TASKS,
)


class ScoresText(Protocol):
    """The detector contract the harness depends on. Deliberately minimal."""

    def score(self, text: str): ...


# -- Wilson interval (Section 2.6: intervals print next to every cell) ---

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval.

    Section 2.6 requires intervals beside every cell metric because
    adversarial cells at n=10-25 have very wide ones. Wilson rather than the
    normal approximation: at the small n and near-zero rates this benchmark
    lives in, the normal interval produces negative lower bounds.
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def quantize(value: float, quantum: float = HIDDEN_SCORE_QUANTUM) -> float:
    """Round to the reporting granularity of Section 8.6 (HIDDEN split)."""
    return round(round(value / quantum) * quantum, 10)


# -- results -------------------------------------------------------------

@dataclass
class CellResult:
    """Metrics for one (category, bucket, split) cell."""

    cell: tuple
    n: int
    n_positive: int
    n_negative: int
    tpr_at_fpr: dict[float, float] = field(default_factory=dict)
    tpr_ci: dict[float, tuple[float, float]] = field(default_factory=dict)
    n_abstained: int = 0
    measurable_fpr: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def fpr_resolution(self) -> float:
        """Finest non-zero FPR this cell can express: 1/n_negative."""
        return 1.0 / self.n_negative if self.n_negative else float("inf")

    def is_measurable_at(self, fpr: float) -> bool:
        return self.fpr_resolution <= fpr

    def to_dict(self) -> dict:
        return {
            "cell": list(self.cell),
            "n": self.n,
            "n_positive": self.n_positive,
            "n_negative": self.n_negative,
            "n_abstained": self.n_abstained,
            "fpr_resolution": round(self.fpr_resolution, 6)
            if math.isfinite(self.fpr_resolution) else None,
            "tpr_at_fpr": {
                str(k): {
                    "value": round(v, 6),
                    "ci95": [round(c, 6) for c in self.tpr_ci.get(k, (0.0, 1.0))],
                    "measurable": self.is_measurable_at(k),
                }
                for k, v in self.tpr_at_fpr.items()
            },
            "notes": self.notes,
        }


@dataclass
class TaskResult:
    """Per-task results. Never merged with another task's (Section 9.3)."""

    task: str
    split: str
    corpus_version: str | None
    detector_name: str
    cells: list[CellResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    quantized: bool = False

    def worst_cell(self, fpr: float) -> CellResult | None:
        """Section 9.3(a): the corpus-level headline is the worst cell.

        Cells that cannot express the requested FPR are excluded from the
        headline rather than counted as zero — an unmeasurable cell is not a
        failing cell, and conflating the two would understate the detector for
        the wrong reason. They stay in the table, flagged.
        """
        eligible = [c for c in self.cells
                    if c.is_measurable_at(fpr) and fpr in c.tpr_at_fpr]
        if not eligible:
            return None
        return min(eligible, key=lambda c: c.tpr_at_fpr[fpr])

    def macro_mean(self, fpr: float) -> float | None:
        """Secondary diagnostic only. Never the headline (P4)."""
        vals = [c.tpr_at_fpr[fpr] for c in self.cells
                if c.is_measurable_at(fpr) and fpr in c.tpr_at_fpr]
        return sum(vals) / len(vals) if vals else None

    def to_dict(self) -> dict:
        """Serialize. The per-cell table always ships (Section 9.3)."""
        out = {
            "task": self.task,
            "task_definition": TASKS.get(self.task, ""),
            "split": self.split,
            "corpus_version": self.corpus_version,
            "detector": self.detector_name,
            "quantized": self.quantized,
            "headline_worst_cell": {},
            "secondary_macro_mean": {},
            "cells": [c.to_dict() for c in self.cells],
            "skipped": self.skipped,
        }
        for fpr in PRIMARY_FPR_POINTS:
            worst = self.worst_cell(fpr)
            value = worst.tpr_at_fpr[fpr] if worst else None
            macro = self.macro_mean(fpr)
            if self.quantized:
                value = quantize(value) if value is not None else None
                macro = quantize(macro) if macro is not None else None
            out["headline_worst_cell"][str(fpr)] = {
                "tpr": value,
                "cell": list(worst.cell) if worst else None,
            }
            out["secondary_macro_mean"][str(fpr)] = macro
        return out


# -- task definitions ----------------------------------------------------

def _t1_membership(s: Sample) -> bool | None:
    """T1 pure discrimination: HUMAN vs AI, hybrids excluded (Section 9.3)."""
    if s.label == "AI":
        return True
    if s.label == "HUMAN":
        return False
    return None  # hybrid — excluded from this task by definition


def _t2_membership(s: Sample) -> bool | None:
    """T2 involvement detection: HUMAN vs any AI involvement."""
    if s.label in AI_INVOLVED_LABELS:
        return True
    if s.label == "HUMAN":
        return False
    return None


TASK_MEMBERSHIP: dict[str, Callable[[Sample], bool | None]] = {
    "T1": _t1_membership,
    "T2": _t2_membership,
}


# -- runner --------------------------------------------------------------

class BenchmarkRunner:
    """Executes a detector over a split and reports per cell."""

    def __init__(self, corpus: Corpus, detector: ScoresText,
                 detector_name: str = "detector"):
        self.corpus = corpus
        self.detector = detector
        self.detector_name = detector_name

    def run(self, task: str, split: str, allow_dev_reporting: bool = False) -> TaskResult:
        if task not in TASKS:
            raise ValueError(f"unknown task {task!r}; expected one of {tuple(TASKS)}")
        if task in ("T3", "T4"):
            raise NotImplementedError(
                f"{task} ({TASKS[task]}) is declared but not implemented. "
                "Returning a number here would manufacture evidence; implement "
                "the task before reporting it."
            )

        # Section 2.3 / 9.4: DEV numbers are debugging output, never results.
        sub = (self.corpus.split(split) if (split == "dev" and allow_dev_reporting)
               else self.corpus.for_reporting(split))

        version = self.corpus.manifest.corpus_version if self.corpus.manifest else None
        result = TaskResult(
            task=task, split=split, corpus_version=version,
            detector_name=self.detector_name,
            quantized=(split == "hidden"),  # Section 8.6
        )

        membership = TASK_MEMBERSHIP[task]
        ordered_cells = sorted(sub.cells().items(),
                               key=lambda kv: tuple(str(x) for x in kv[0]))
        for cell, samples in ordered_cells:
            scored: list[tuple[bool, float]] = []
            abstained = 0
            for s in samples:
                in_task = membership(s)
                if in_task is None:
                    continue
                if s.text is None:
                    continue
                res = self.detector.score(s.text)
                if getattr(res, "reliable", True) is False:
                    abstained += 1
                scored.append((in_task, float(res.score)))

            if not scored:
                result.skipped.append(f"{cell}: no samples in task {task}")
                continue

            pos = [v for m, v in scored if m]
            neg = [v for m, v in scored if not m]
            cr = CellResult(cell=cell, n=len(scored), n_positive=len(pos),
                            n_negative=len(neg), n_abstained=abstained)
            if not pos or not neg:
                cr.notes.append("cell lacks both classes; TPR at fixed FPR undefined")
                result.cells.append(cr)
                continue

            for fpr in PRIMARY_FPR_POINTS:
                tpr, _thr = _tpr_at_fpr(pos, neg, fpr)
                cr.tpr_at_fpr[fpr] = tpr
                cr.tpr_ci[fpr] = wilson_interval(round(tpr * len(pos)), len(pos))
                if not cr.is_measurable_at(fpr):
                    cr.notes.append(
                        f"FPR={fpr} is finer than this cell can express "
                        f"(1/{len(neg)} = {1/len(neg):.4f})"
                    )
            result.cells.append(cr)
        return result


def _tpr_at_fpr(positives: list[float], negatives: list[float],
                max_fpr: float) -> tuple[float, float]:
    """Best TPR with empirical FPR <= max_fpr, and the threshold achieving it.

    Threshold selection here is *within-cell and descriptive*: it reports what
    the score distribution supports. It is not a deployment threshold, which
    Section 9.4 requires to be chosen on DEV.
    """
    if not positives or not negatives:
        return 0.0, float("inf")
    ordered = sorted(negatives, reverse=True)
    allowed = int(len(ordered) * max_fpr)
    if allowed >= len(ordered):
        return 1.0, float("-inf")
    threshold = math.nextafter(ordered[allowed], math.inf)
    return sum(1 for p in positives if p >= threshold) / len(positives), threshold
