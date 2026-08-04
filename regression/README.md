# Regression entries (GAUNTLET Section 8)

One file per confirmed bug: `BUG-YYYYMMDD-NNN.jsonl`.

**The suite only grows.** No entry is ever deleted. A superseded entry is
marked with a successor reference and remains executable (§8.1).

## Lifecycle (§8.2)

1. **Discovery** — a misbehavior is observed.
2. **Minimization** — reduce to the smallest sample set, target 1-5 samples,
   that still reproduces it.
3. **Entry** — create `BUG-YYYYMMDD-NNN.jsonl` with the samples plus the
   record below.
4. **Gate** — enters CI immediately as `expected_fail`, then must pass forever.

## Record schema (§8.3)

The first line of each file is the regression record; subsequent lines are
corpus samples in the Section 5.2 schema.

```json
{
  "bug_id": "BUG-20260805-001",
  "discovery_date": "2026-08-05",
  "reporter": "",
  "linked_issue": "",
  "affected_detector_versions": ["2.0.0"],
  "description": "One paragraph.",
  "category_refs": ["V-05"],
  "expected_behavior": {"kind": "confidence_max", "value": 0.7},
  "status": "expected_fail"
}
```

`expected_behavior.kind` is one of the assertion forms §8.3 permits, and
nothing else:

| kind | meaning |
|---|---|
| `label_equals` | `label == X` |
| `p_ai_involvement_within` | `p_ai_involvement` within `[lo, hi]` |
| `confidence_max` | `confidence <= c` — undecidable and degenerate cases |
| `no_crash` | `no_crash and defined_output` |
| `score_delta_max` | `score_delta <= epsilon` under transform `T` (invariance) |

`status` is `expected_fail`, `passing`, or `superseded` with a `successor_id`.

## Relationship to `tests/test_review_regressions.py`

That file predates GAUNTLET and holds regressions against detector internals
found before the specification was adopted. It satisfies §8.1 in spirit —
minimal, permanent, each test naming the wrong answer it locks out — but its
entries do not carry the §8.3 record schema. Retrofitting them is tracked
work, not yet done.

Entries here are corpus-level: they reference samples and assert detector
behavior on them. The two layers are complementary and neither replaces the
other.
