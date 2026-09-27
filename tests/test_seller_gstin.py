"""Seller GSTIN split signal and overlay block for group letterheads."""

from __future__ import annotations

from pdfsplit.boundary_constraints import (
    compute_boundary_constraints,
    extract_seller_gstin,
)
from pdfsplit.boundary_overlay import classify_adjacent_pair


def _page(md: str) -> dict:
    return {"markdown": md, "blocks": [], "dimensions": {"width": 612, "height": 792}}


def test_extract_seller_gstin_after_buyer_to_block():
    """Indian layout: title, To,, buyer GSTIN, then seller GSTIN near PAN."""
    md = (
        "KUDOS FINANCE\n"
        "## TAX INVOICE\n"
        "To,\n"
        "Interropac Private Limited\n"
        "GSTIN – 29AAFCI0214G1ZX\n"
        "Invoice No.: KFIPL/1\n"
        "Grand Total: 100\n"
        "PAN NO: AADCK6462G\n"
        "GSTIN: 27AADCK6462G1ZF\n"
    )
    assert extract_seller_gstin(md) == "27AADCK6462G1ZF"


def test_extract_seller_gstin_ignores_buyer_block():
    md = (
        "IBC Group\n"
        "TAX INVOICE\n"
        "GSTIN: 29AAACC9836F1ZG\n"
        "Invoice No: INV-1\n"
        "Bill To: Interropac\n"
        "GSTIN: 29AAFCI0214G1ZX\n"
    )
    assert extract_seller_gstin(_page(md)) == "29AAACC9836F1ZG"


def test_different_seller_gstin_must_split():
    pages = [
        _page(
            "IBC Group\nTAX INVOICE\nGSTIN: 29AAACC9836F1ZG\n"
            "Invoice No: INV-7701\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            "Grand Total: 100\nPage 1 of 1\n"
        ),
        _page(
            "IBC Group\nTAX INVOICE\nGSTIN: 29AABCC1234D1Z5\n"
            "Invoice No: INV-7702\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            "Grand Total: 200\nPage 1 of 1\n"
        ),
    ]
    cons = compute_boundary_constraints(pages)
    kinds = {(c.page, c.kind, c.signal) for c in cons}
    assert (2, "must_split", "different_seller_gstin") in kinds


def test_same_seller_gstin_does_not_force_split():
    pages = [
        _page(
            "IBC Group\nTAX INVOICE\nGSTIN: 29AAACC9836F1ZG\n"
            "Invoice No: INV-1\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            "Page 1 of 2\n"
        ),
        _page(
            "IBC Group\n(continued)\nGSTIN: 29AAACC9836F1ZG\n"
            "Invoice No: INV-1\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
            "Page 2 of 2\n"
        ),
    ]
    cons = compute_boundary_constraints(pages)
    assert not any(
        c.signal == "different_seller_gstin" for c in cons
    )


def test_overlay_blocks_merge_when_seller_gstin_differs():
    pages = [
        _page(
            "IBC Group\nTAX INVOICE\nGSTIN: 29AAACC9836F1ZG\n"
            "Invoice No: SHARED-9\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
        ),
        _page(
            "IBC Group\nTAX INVOICE\nGSTIN: 29AABCC1234D1Z5\n"
            "Invoice No: SHARED-9\nBill To: Buyer\nGSTIN: 29AAFCI0214G1ZX\n"
        ),
    ]
    left = {
        "page_start": 1,
        "page_end": 1,
        "vendor": "IBC Group",
        "document_number": "SHARED-9",
    }
    right = {
        "page_start": 2,
        "page_end": 2,
        "vendor": "IBC Group",
        "document_number": "SHARED-9",
    }
    pair = classify_adjacent_pair(left, right, pages)
    assert pair is not None
    assert pair.tier == "blocked"
    assert "GSTIN" in pair.reason
