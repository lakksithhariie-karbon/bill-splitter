"""Tests for physical PDF splitting."""

from __future__ import annotations

import pikepdf

from pdfsplit.pdf import split_pdf_by_ranges, verify_split
from tests.conftest import make_docs


def _make_src(tmp_path, n=8) -> "pikepdf path":
    src = tmp_path / "src.pdf"
    with pikepdf.Pdf.new() as pdf:
        for _ in range(n):
            pdf.add_blank_page(page_size=(100, 100))
        pdf.save(src)
    return src


def test_split_produces_correct_page_counts(tmp_path):
    src = _make_src(tmp_path, n=8)
    docs = make_docs(("invoice", 1, 3, 1.0), ("bank_statement", 4, 8, 1.0))
    splits = split_pdf_by_ranges(src, docs, tmp_path / "out", total_pages=8)

    assert [s.doc.start_page for s in splits] == [1, 4]
    counts = []
    for s in splits:
        with pikepdf.open(s.path) as pdf:
            counts.append(len(pdf.pages))
    assert counts == [3, 5]
    # Concatenated coverage == original.
    assert sum(counts) == 8
    assert verify_split(src, splits) == []


def test_split_rejects_invalid_ranges(tmp_path):
    src = _make_src(tmp_path, n=4)
    # Non-contiguous (gap between page 2 and 5).
    docs = make_docs(("a", 1, 2, 1.0), ("b", 5, 6, 1.0))
    import pytest

    with pytest.raises(ValueError):
        split_pdf_by_ranges(src, docs, tmp_path / "out", total_pages=4)


def test_split_rejects_out_of_range(tmp_path):
    src = _make_src(tmp_path, n=3)
    docs = make_docs(("a", 1, 9, 1.0))  # end > page count
    import pytest

    with pytest.raises(ValueError):
        split_pdf_by_ranges(src, docs, tmp_path / "out", total_pages=3)


def test_split_order_preserved(tmp_path):
    src = _make_src(tmp_path, n=5)
    docs = make_docs(("x", 1, 2, 1.0), ("y", 3, 5, 1.0))
    splits = split_pdf_by_ranges(src, docs, tmp_path / "out", total_pages=5)
    # First split is the lower range.
    assert splits[0].doc.start_page == 1
    assert splits[1].doc.start_page == 3
