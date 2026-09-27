"""Tests for the scoring/metrics module."""

from __future__ import annotations

from pdfsplit.scoring import (
    DocTruth,
    boundary_indices,
    boundary_metrics,
    exact_doc_accuracy,
    perfect_packet,
    score_packet,
)
from tests.conftest import make_docs


def T(*specs):
    """Truth docs: (type, start, end)."""
    return [DocTruth(doc_type=t, start_page=s, end_page=e) for t, s, e in specs]


def test_boundary_indices_excludes_page_one():
    truth = T(("invoice", 1, 4), ("invoice", 5, 8), ("invoice", 9, 12))
    assert boundary_indices(truth) == {5, 9}


def test_same_type_back_to_back_boundary_recall_zero_when_merged():
    """The critical case: classifier labels all pages right but never discovers
    separate docs. Boundary recall must be 0, and the packet not perfect."""
    truth = T(("invoice", 1, 4), ("invoice", 5, 8), ("invoice", 9, 12))
    # Model says: everything is ONE invoice, pages 1-12.
    pred = make_docs(("invoice", 1, 12, 0.99))
    s = score_packet(truth, pred)
    assert s["boundary_recall"] == 0.0
    assert s["exact_doc_accuracy"] == 0.0
    assert s["perfect"] is False
    # Boundary count: 0 predicted boundaries.
    assert s["boundary_pred"] == []


def test_perfect_packet_when_exact_match():
    truth = T(("invoice", 1, 4), ("bank_statement", 5, 7))
    pred = make_docs(("invoice", 1, 4, 0.98), ("bank_statement", 5, 7, 0.94))
    s = score_packet(truth, pred)
    assert s["perfect"] is True
    assert s["exact_doc_accuracy"] == 1.0
    assert s["boundary_recall"] == 1.0
    assert s["boundary_precision"] == 1.0


def test_false_positive_boundary():
    truth = T(("invoice", 1, 4), ("bank_statement", 5, 7))
    # Model splits the first invoice into two -> spurious boundary at page 3.
    pred = make_docs(
        ("invoice", 1, 2, 0.9), ("invoice", 3, 4, 0.9), ("bank_statement", 5, 7, 0.9)
    )
    s = score_packet(truth, pred)
    assert s["boundary_precision"] < 1.0
    assert s["perfect"] is False


def test_missing_boundary_drops_recall():
    truth = T(("memo", 1, 2), ("contract", 3, 6))
    # Model merges both into one contract.
    pred = make_docs(("contract", 1, 6, 0.8))
    s = score_packet(truth, pred)
    assert s["boundary_recall"] == 0.0
    assert s["boundary_f1"] == 0.0


def test_boundary_metrics_counts():
    truth = T(("a", 1, 2), ("b", 3, 4), ("c", 5, 6))
    pred = make_docs(("a", 1, 2, 1.0), ("b", 3, 5, 1.0), ("c", 6, 6, 1.0))
    bm = boundary_metrics(truth, pred)
    # truth boundaries {3,5}, pred boundaries {3,6}
    assert bm["boundary_tp"] == 1
    assert bm["boundary_fp"] == 1
    assert bm["boundary_fn"] == 1


def test_exact_doc_accuracy_matches_range_and_class():
    truth = T(("invoice", 1, 4), ("bank_statement", 5, 7))
    pred = make_docs(("invoice", 1, 4, 0.9), ("report", 5, 7, 0.9))
    assert exact_doc_accuracy(truth, pred) == 0.5
    assert perfect_packet(truth, pred) is False
