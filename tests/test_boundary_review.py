"""Review status: Checked vs Needs a look (cuts and holds)."""

from __future__ import annotations

from pdfsplit.boundary_constraints import BoundaryConstraint
from pdfsplit.boundary_review import (
    annotate_segments_with_review_status,
    conflict_pages,
    derive_hold_review_reasons,
    derive_review_status,
)


def test_hard_signal_alone_is_checked():
    status, reasons = derive_review_status(
        {
            "page_start": 3,
            "page_end": 3,
            "signals": ["different_doc_number"],
            "evidence": ["different invoice numbers"],
        }
    )
    assert status == "checked"
    assert reasons == []


def test_model_only_needs_look():
    status, reasons = derive_review_status(
        {
            "page_start": 3,
            "page_end": 3,
            "signals": [],
            "judge_reason": "New supplier letterhead",
        }
    )
    assert status == "needs_look"
    assert any("only the model" in r for r in reasons)


def test_contradicting_reason_needs_look():
    status, reasons = derive_review_status(
        {
            "page_start": 3,
            "page_end": 3,
            "signals": ["different_doc_number"],
            "judge_reason": "Continuation of the same invoice number",
        }
    )
    assert status == "needs_look"
    assert any("same bill" in r for r in reasons)


def test_contradicting_reason_skipped_when_vendors_differ():
    status, reasons = derive_review_status(
        {
            "page_start": 3,
            "page_end": 3,
            "signals": ["different_doc_number"],
            "judge_reason": "Same document number on both sides",
        },
        vendors_clearly_differ=True,
    )
    assert status == "checked"


def test_overlay_suggest_needs_look():
    status, _ = derive_review_status(
        {
            "page_start": 3,
            "page_end": 3,
            "signals": ["shared_doc_number"],
            "overlay_suggest": {"reason": "letterhead weak"},
        }
    )
    assert status == "needs_look"


def test_conflict_pages_and_annotate():
    raw = [
        BoundaryConstraint(2, "must_split", "page_dimensions", "size"),
        BoundaryConstraint(2, "must_not_split", "shared_doc_number", "same"),
    ]
    assert conflict_pages(raw) == {2}
    segs = annotate_segments_with_review_status(
        [
            {
                "page_start": 1,
                "page_end": 1,
                "signals": ["packet_start"],
            },
            {
                "page_start": 2,
                "page_end": 2,
                "signals": ["page_dimensions"],
            },
        ],
        raw_constraints=raw,
    )
    assert segs[0]["review_status"] == "checked"
    assert segs[1]["review_status"] == "needs_look"


def test_weak_hold_inside_multipage_needs_look():
    """Invoice held onto terms/DN with no hold signal — the b05 under-split."""
    pages = [
        {"markdown": "TAX INVOICE\nInvoice No: INV-1\nPage 1 of 2\n"},
        {"markdown": "TAX INVOICE\nInvoice No: INV-1\nPage 2 of 2\nGrand Total: 1\n"},
        {"markdown": "TERMS AND CONDITIONS\nNot a tax invoice.\n"},
        {"markdown": "DELIVERY NOTE\nReference: DN-1\n"},
    ]
    resolved = [
        BoundaryConstraint(
            2, "must_not_split", "continuation_marker", "Page 2 of 2"
        ),
        BoundaryConstraint(
            2, "must_not_split", "shared_doc_number", "both carry INV-1"
        ),
    ]
    reasons = derive_hold_review_reasons(
        {"page_start": 1, "page_end": 4, "signals": ["continuation_marker"]},
        pages=pages,
        resolved_constraints=resolved,
        raw_constraints=resolved,
    )
    assert any("no hard hold signal" in r for r in reasons)
    assert any("3–4" in r or "pages 3" in r for r in reasons)

    segs = annotate_segments_with_review_status(
        [
            {
                "page_start": 1,
                "page_end": 4,
                "signals": ["continuation_marker", "shared_doc_number"],
            }
        ],
        pages=pages,
        resolved_constraints=resolved,
        raw_constraints=resolved,
    )
    assert segs[0]["review_status"] == "needs_look"


def test_strong_hold_multipage_stays_checked():
    pages = [
        {"markdown": "TAX INVOICE\nInvoice No: INV-1\nPage 1 of 2\n"},
        {"markdown": "TAX INVOICE\nInvoice No: INV-1\nPage 2 of 2\n"},
    ]
    resolved = [
        BoundaryConstraint(
            2, "must_not_split", "continuation_marker", "Page 2 of 2"
        ),
        BoundaryConstraint(
            2, "must_not_split", "shared_doc_number", "both carry INV-1"
        ),
    ]
    segs = annotate_segments_with_review_status(
        [
            {
                "page_start": 1,
                "page_end": 2,
                "signals": ["continuation_marker", "shared_doc_number"],
            }
        ],
        pages=pages,
        resolved_constraints=resolved,
        raw_constraints=resolved,
    )
    assert segs[0]["review_status"] == "checked"


def test_different_bill_numbers_held_needs_look():
    pages = [
        {
            "markdown": (
                "Acme Supplies Pvt Ltd\nTAX INVOICE\n"
                "Invoice No: INV-2026-1001\nGrand Total: 1\n"
            )
        },
        {
            "markdown": (
                "Acme Supplies Pvt Ltd\nTAX INVOICE\n"
                "Invoice No: INV-2026-1002\nGrand Total: 2\n"
            )
        },
    ]
    reasons = derive_hold_review_reasons(
        {"page_start": 1, "page_end": 2, "signals": []},
        pages=pages,
        resolved_constraints=[],
        raw_constraints=[],
    )
    assert any("different bill numbers" in r for r in reasons)


def test_different_seller_gstin_held_needs_look():
    pages = [
        {
            "markdown": (
                "IBC Group\nTAX INVOICE\nGSTIN: 29AAACC9836F1ZG\n"
                "Invoice No: INV-1\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            )
        },
        {
            "markdown": (
                "IBC Group\nTAX INVOICE\nGSTIN: 29AABCC1234D1Z5\n"
                "Invoice No: INV-2\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            )
        },
    ]
    reasons = derive_hold_review_reasons(
        {"page_start": 1, "page_end": 2, "signals": []},
        pages=pages,
        resolved_constraints=[],
        raw_constraints=[],
    )
    assert any("different seller GSTIN" in r for r in reasons)


def test_thin_overlay_merge_needs_look():
    reasons = derive_hold_review_reasons(
        {
            "page_start": 1,
            "page_end": 2,
            "signals": ["judge_shared_number"],
            "overlay_tier": "merge",
        },
        pages=[
            {"markdown": "Invoice No: X\n"},
            {"markdown": "Invoice No: X\n"},
        ],
        resolved_constraints=[],
        raw_constraints=[],
    )
    assert any("thin evidence" in r for r in reasons)
