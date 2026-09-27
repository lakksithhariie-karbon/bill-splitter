"""Provider-agnostic scoring of split predictions against ground truth.

Metrics (all page-based, 1-indexed):
- Boundary Precision / Recall / F1 over boundary indices between documents.
- Exact Document Accuracy: a ground-truth doc is exact iff a prediction matches
  its full page range AND class.
- Perfect Packet Rate: fraction of packets where EVERY ground-truth doc is exact.
- Classification accuracy (per-doc, on discovered ranges).
- Confidence calibration: exact-doc accuracy restricted to predictions at
  confidence >= T, with coverage.

A boundary index is the page number where a NEW document starts. Ground truth
B_true = {start_page of each doc except the first}. Prediction B_pred likewise.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pdfsplit.schema import SplitDocument


class HasStart(Protocol):
    start_page: int


@dataclass
class DocTruth:
    doc_type: str
    start_page: int
    end_page: int


def boundary_indices(documents: Sequence[HasStart]) -> set[int]:
    """Return the set of page indices where a new document starts (excluding page 1)."""
    starts = {d.start_page for d in documents if d.start_page > 1}
    return starts


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def boundary_metrics(truth: list[DocTruth], pred: list[SplitDocument]) -> dict:
    b_true = boundary_indices(truth)
    b_pred = boundary_indices(pred)
    tp = len(b_true & b_pred)
    fp = len(b_pred - b_true)
    fn = len(b_true - b_pred)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "boundary_true": sorted(b_true),
        "boundary_pred": sorted(b_pred),
        "boundary_tp": tp,
        "boundary_fp": fp,
        "boundary_fn": fn,
        "boundary_precision": round(precision, 4),
        "boundary_recall": round(recall, 4),
        "boundary_f1": round(f1(precision, recall), 4),
        "pred_doc_count": len(pred),
        "true_doc_count": len(truth),
    }


def _match_prediction(truth: DocTruth, preds: list[SplitDocument]) -> bool:
    for p in preds:
        if (
            p.start_page == truth.start_page
            and p.end_page == truth.end_page
            and p.doc_type == truth.doc_type
        ):
            return True
    return False


def exact_doc_accuracy(truth: list[DocTruth], pred: list[SplitDocument]) -> float:
    if not truth:
        return 0.0
    correct = sum(1 for t in truth if _match_prediction(t, pred))
    return correct / len(truth)


def perfect_packet(truth: list[DocTruth], pred: list[SplitDocument]) -> bool:
    if not truth:
        return False
    return all(_match_prediction(t, pred) for t in truth)


def classification_accuracy(truth: list[DocTruth], pred: list[SplitDocument]) -> float:
    """Fraction of discovered ground-truth docs whose class label matched.

    Pair truth docs to predictions by maximal page overlap; a doc counts only
    if its predicted range overlaps and the class matches. Falls back to
    matching by range then class.
    """
    if not truth:
        return 0.0
    used: set[int] = set()
    correct = 0
    for t in truth:
        best = _best_overlap(t, pred)
        if best is None:
            continue
        used.add(id(best))
        if best.doc_type == t.doc_type:
            correct += 1
    return correct / len(truth)


def _best_overlap(t: DocTruth, preds: list[SplitDocument]):
    best = None
    best_overlap = 0
    for p in preds:
        overlap = max(
            0, min(t.end_page, p.end_page) - max(t.start_page, p.start_page) + 1
        )
        if overlap > best_overlap:
            best_overlap = overlap
            best = p
    return best


def score_packet(truth: list[DocTruth], pred: list[SplitDocument]) -> dict:
    bm = boundary_metrics(truth, pred)
    exact = exact_doc_accuracy(truth, pred)
    return {
        **bm,
        "exact_doc_accuracy": round(exact, 4),
        "perfect": perfect_packet(truth, pred),
        "classification_accuracy": round(classification_accuracy(truth, pred), 4),
    }


# -- aggregation -------------------------------------------------------------


def confidence_thresholds() -> list[float]:
    return [0.80, 0.90, 0.95, 0.98]


def calibration_curve(
    packets: Iterable[tuple[list[DocTruth], list[SplitDocument]]],
) -> dict:
    """Exact-doc accuracy and coverage at each confidence threshold."""
    result = {}
    for t in confidence_thresholds():
        exact_numer = exact_denom = 0
        doc_total = 0
        for truth, pred in packets:
            high_conf = [d for d in pred if d.confidence >= t]
            exact_denom += len(truth)
            exact_numer += int(exact_doc_accuracy(truth, high_conf) * len(truth))
            doc_total += len(truth)
        coverage = (doc_total / exact_denom) if exact_denom else 0.0
        result[f"{t:.2f}"] = {
            "threshold": t,
            "exact_doc_accuracy": round(exact_numer / exact_denom, 4)
            if exact_denom
            else 0.0,
            "coverage": round(coverage, 4),
        }
    return result
