"""End-to-end: dataset loading, evasion accounting, and the CLI."""

import json

import pytest

from ai_text_eval.cli import main
from ai_text_eval.dataset import (
    load_demo_corpus,
    load_demo_pairs,
    load_labeled_jsonl,
    load_pairs_jsonl,
)
from ai_text_eval.detectors import available_detectors
from ai_text_eval.detectors.base import Detector, DetectorResult
from ai_text_eval.evasion import EvasionPair, evaluate_evasion
from ai_text_eval.metrics import evaluate_scores


class _ScriptedDetector(Detector):
    """Returns preset scores in order, so evasion accounting is checkable."""

    name = "scripted"

    def __init__(self, scores):
        self._scores = list(scores)
        self._i = 0

    def score(self, text: str) -> DetectorResult:
        s = self._scores[self._i % len(self._scores)]
        self._i += 1
        return DetectorResult(score=s)


def test_demo_corpus_loads_and_is_balanced_enough():
    corpus = load_demo_corpus()
    labels = [c.label for c in corpus]
    assert labels.count(0) >= 10
    assert labels.count(1) >= 10
    assert all(c.text.strip() for c in corpus)


def test_demo_corpus_labels_match_files():
    human = load_labeled_jsonl(load_demo_corpus.__globals__["DATA_DIR"] / "demo_human.jsonl")
    ai = load_labeled_jsonl(load_demo_corpus.__globals__["DATA_DIR"] / "demo_ai.jsonl")
    assert {h.label for h in human} == {0}
    assert {a.label for a in ai} == {1}


def test_demo_pairs_load_and_differ():
    pairs = load_demo_pairs()
    assert len(pairs) >= 10
    for p in pairs:
        assert p.original.strip() and p.rephrased.strip()
        assert p.original != p.rephrased


def test_loader_rejects_bad_label(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"text": "x", "label": 7}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="label must be"):
        load_labeled_jsonl(bad)


def test_loader_rejects_missing_field(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"text": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="needs 'text' and 'label'"):
        load_labeled_jsonl(bad)


def test_loader_reports_line_number(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"text": "ok", "label": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=":2:"):
        load_labeled_jsonl(bad)


def test_loader_skips_blank_lines(tmp_path):
    f = tmp_path / "ok.jsonl"
    f.write_text('{"text": "a", "label": 1}\n\n\n{"text": "b", "label": 0}\n', encoding="utf-8")
    assert len(load_labeled_jsonl(f)) == 2


def test_pairs_loader_rejects_missing_field(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"original": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="needs 'original' and 'rephrased'"):
        load_pairs_jsonl(bad)


def test_evasion_accounting_is_exact():
    # 3 pairs. Detector returns all originals first, then all rephrased.
    pairs = [EvasionPair(f"orig {i}", f"reph {i}") for i in range(3)]
    # originals: 0.9, 0.8, 0.3  |  rephrased: 0.2, 0.7, 0.1
    det = _ScriptedDetector([0.9, 0.8, 0.3, 0.2, 0.7, 0.1])
    rep = evaluate_evasion(det, pairs, threshold=0.5)
    assert rep.n_pairs == 3
    # flagged originals: 0.9 and 0.8 -> 2
    assert rep.n_flagged_originals == 2
    # of those, 0.9 -> 0.2 escapes; 0.8 -> 0.7 does not
    assert rep.n_evaded == 1
    assert rep.evasion_rate == pytest.approx(0.5)
    assert rep.mean_delta == pytest.approx(((0.9 - 0.2) + (0.8 - 0.7) + (0.3 - 0.1)) / 3)


def test_evasion_rate_zero_when_nothing_flagged():
    pairs = [EvasionPair("a", "b")]
    det = _ScriptedDetector([0.1, 0.05])
    rep = evaluate_evasion(det, pairs, threshold=0.5)
    assert rep.n_flagged_originals == 0
    assert rep.evasion_rate == 0.0


def test_evasion_on_real_pairs_shows_score_drop():
    """The bundled rephrasings should beat the lexical detector."""
    pairs = load_demo_pairs()
    det = available_detectors(include_model_based=False)["phrases"]
    rep = evaluate_evasion(det, pairs, threshold=0.5)
    assert rep.mean_delta > 0, "rephrasing should lower the AI-marker score"


def test_benchmark_pipeline_on_demo_corpus():
    corpus = load_demo_corpus()
    labels = [c.label for c in corpus]
    texts = [c.text for c in corpus]
    dets = available_detectors(include_model_based=False)
    for name, det in dets.items():
        scores = [r.score for r in det.score_many(texts)]
        result = evaluate_scores(name, labels, scores)
        assert 0.0 <= result.auroc <= 1.0
        assert result.auroc_ci[0] <= result.auroc <= result.auroc_ci[1]
        assert result.n_human + result.n_ai == len(corpus)


def test_cli_score_json(capsys):
    assert main(["score", "--fast", "--json", "This is a test sentence for the CLI."]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "ensemble" in payload and "detectors" in payload
    assert 0.0 <= payload["ensemble"]["score"] <= 1.0
    assert payload["ensemble"]["reliable"] is False


def test_cli_score_text_output_warns_on_short_text(capsys):
    assert main(["score", "--fast", "Too short to judge."]) == 0
    out = capsys.readouterr().out
    assert "insufficient evidence" in out


def test_cli_score_reads_file(tmp_path, capsys):
    f = tmp_path / "t.txt"
    f.write_text("The quick brown fox jumps over the lazy dog. " * 12, encoding="utf-8")
    assert main(["score", "--fast", "--file", str(f)]) == 0
    assert "ENSEMBLE" in capsys.readouterr().out


def test_cli_score_rejects_text_and_file(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["score", "--fast", "hello", "--file", str(f)])


def test_cli_compare_json(capsys):
    assert (
        main([
            "compare", "--fast", "--json",
            "--original", "It is important to note that this underscores a pivotal paradigm. " * 6,
            "--rephrased", "The bus was late. I got wet. Nobody said anything about it. " * 6,
        ])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert "phrases" in payload and "ENSEMBLE" in payload
    assert payload["phrases"]["delta"] > 0


def test_cli_benchmark_demo_writes_report(tmp_path, capsys):
    assert main(["benchmark", "--fast", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "AUROC" in out
    assert "evasion rate" in out
    payload = json.loads((tmp_path / "benchmark.json").read_text())
    assert payload["results"] and payload["evasion"]
    for r in payload["results"]:
        assert 0.0 <= r["auroc"] <= 1.0


def test_cli_benchmark_requires_both_corpora(tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text('{"text": "x", "label": 0}\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["benchmark", "--fast", "--human", str(f)])


def test_cli_detectors_lists(capsys):
    assert main(["detectors", "--fast"]) == 0
    out = capsys.readouterr().out
    assert "stylometry" in out and "phrases" in out
