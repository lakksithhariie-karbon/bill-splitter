"""Forced under-split: different bill numbers held in one document."""

from __future__ import annotations

from pathlib import Path

from pdfsplit.boundary_benchmark import pages_from_pdf_text
from pdfsplit.boundary_constraints import (
    compute_boundary_constraints,
    extract_own_doc_number,
)
from pdfsplit.boundary_review import (
    annotate_segments_with_review_status,
    derive_hold_review_reasons,
)
from pdfsplit.corpus import build_corpus


def test_forced_undersplit_diff_bill_numbers_flags_badge(tmp_path: Path):
    """Two real invoices, different numbers, forced into one segment.

    Layer-1 would split this packet; we force the under-split to prove the
    hold rule that reaches Tally as a wrong voucher actually fires.
    """
    corpus = build_corpus(tmp_path / "corpus")
    spec = next(
        p
        for p in corpus.packets
        if p.packet_id == "pkt_b12_undersplit_diff_bill_numbers"
    )
    pages = pages_from_pdf_text(corpus.pdf_path(spec.packet_id))
    assert len(pages) == 2
    own_a = extract_own_doc_number(pages[0])
    own_b = extract_own_doc_number(pages[1])
    assert own_a and own_b and own_a != own_b, (own_a, own_b)

    # Forced under-split — one document covering both bills.
    forced = [
        {
            "page_start": 1,
            "page_end": 2,
            "doc_type": "invoice",
            "signals": [],
            "confidence": 0.98,
        }
    ]
    resolved = compute_boundary_constraints(pages)
    reasons = derive_hold_review_reasons(
        forced[0],
        pages=pages,
        resolved_constraints=resolved,
        raw_constraints=resolved,
    )
    assert any(
        "different bill numbers held together" in r for r in reasons
    ), reasons

    segs = annotate_segments_with_review_status(
        forced,
        pages=pages,
        resolved_constraints=resolved,
        raw_constraints=resolved,
    )
    assert segs[0]["review_status"] == "needs_look"
    assert any(
        "different bill numbers" in r for r in segs[0]["review_reasons"]
    )
