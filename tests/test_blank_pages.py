"""Blank separator detection and segment stripping."""

from __future__ import annotations

from pdfsplit.blank_pages import (
    detect_blank_pages,
    is_auto_blank_page,
    possible_blank_pages,
    strip_pages_from_segments,
    text_gate_empty,
)


def test_empty_markdown_is_blank():
    page = {"markdown": "\n\n", "blocks": []}
    assert is_auto_blank_page(page)
    assert detect_blank_pages([page])[0]["page"] == 1


def test_page_number_only_is_blank():
    page = {"markdown": "Page 2\n", "blocks": []}
    assert text_gate_empty(page["markdown"])
    assert is_auto_blank_page(page)


def test_invoice_content_not_blank():
    page = {
        "markdown": "TAX INVOICE\nInvoice No: INV-1\nGrand Total: 100\n",
        "blocks": [{"text": "TAX INVOICE"}],
    }
    assert not is_auto_blank_page(page)


def test_text_empty_but_blocks_present_is_possible_not_auto():
    """Faint scan stand-in: OCR emptied markdown but left blocks."""
    page = {
        "markdown": "\n",
        "blocks": [{"text": "faint mark 12"}],
    }
    assert text_gate_empty(page["markdown"])
    assert not is_auto_blank_page(page)
    assert possible_blank_pages([page]) == [1]


def test_strip_splits_around_blank():
    segs = strip_pages_from_segments(
        [{"page_start": 1, "page_end": 5, "doc_type": "invoice"}],
        {2, 4},
    )
    assert [(s["page_start"], s["page_end"]) for s in segs] == [
        (1, 1),
        (3, 3),
        (5, 5),
    ]
