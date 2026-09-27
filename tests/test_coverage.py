"""Coverage invariant: uncovered pages must be loud, never silent."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import authed_client

from pdfsplit.app import main
from pdfsplit.app.main import app
from pdfsplit.extraction import (
    ExtractedDocument,
    coverage_error_codes,
    uncovered_pages,
)
from pdfsplit.providers.mistral import MistralSplitterProvider


def test_coverage_error_codes_contiguous_gaps():
    docs = [
        ExtractedDocument(page_start=1, page_end=3, doc_type="invoice"),
    ]
    codes = coverage_error_codes(docs, pages_processed=5)
    assert codes == ["uncovered_pages:4-5"]
    assert uncovered_pages(docs, 5) == [4, 5]


def test_map_extract_flags_partial_annotation(tmp_path, monkeypatch):
    """Annotation covering only pages 1-3 of a 5-page OCR response."""
    fixture = {
        "pages": [
            {"index": i, "markdown": f"page {i+1} text", "blocks": [], "confidence_scores": {}}
            for i in range(5)
        ],
        "document_annotation": json.dumps(
            {
                "documents": [
                    {
                        "page_start": 1,
                        "page_end": 1,
                        "doc_type": "invoice",
                        "vendor_name": "A",
                        "invoice_number": "1",
                        "invoice_date": "",
                        "currency": "INR",
                        "gstin": "",
                        "total_amount": "10",
                        "tax_amount": "0",
                        "line_items": [],
                    },
                    {
                        "page_start": 2,
                        "page_end": 3,
                        "doc_type": "invoice",
                        "vendor_name": "B",
                        "invoice_number": "2",
                        "invoice_date": "",
                        "currency": "INR",
                        "gstin": "",
                        "total_amount": "20",
                        "tax_amount": "0",
                        "line_items": [],
                    },
                ]
            }
        ),
        "usage_info": {"pages_processed": 5, "doc_size_bytes": 100},
        "model": "mistral-ocr-latest",
    }
    path = tmp_path / "partial_annotated.json"
    path.write_text(json.dumps(fixture))
    provider = MistralSplitterProvider(annotated_fixture_path=path)
    result = provider.extract(b"%PDF-1.4")
    assert "uncovered_pages:4-5" in result.errors
    assert uncovered_pages(result.documents, result.pages_processed) == [4, 5]


def test_split_payload_exposes_uncovered_pages(tmp_path, monkeypatch):
    """App payload carries uncovered_pages from a partial annotation fixture."""
    # Build a tiny synthetic annotated fixture: 3 docs on pages 1-3, claim 5 pages.
    # Use the real pkt fixture but we'll monkeypatch pages_processed via a custom fixture.
    fixture = {
        "pages": [
            {
                "index": i,
                "markdown": f"# Doc {i+1}\ninvoice body {i+1}",
                "blocks": [
                    {
                        "type": "title",
                        "content": f"Doc {i+1}",
                        "top_left_y": 10,
                    }
                ],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            }
            for i in range(5)
        ],
        "document_annotation": json.dumps(
            {
                "documents": [
                    {
                        "page_start": 1,
                        "page_end": 3,
                        "doc_type": "invoice",
                        "vendor_name": "Acme",
                        "invoice_number": "X1",
                        "invoice_date": "2020-01-01",
                        "currency": "INR",
                        "gstin": "",
                        "total_amount": "100",
                        "tax_amount": "0",
                        "line_items": [],
                    }
                ]
            }
        ),
        "usage_info": {"pages_processed": 5},
        "model": "mistral-ocr-latest",
    }
    fpath = tmp_path / "partial5.json"
    fpath.write_text(json.dumps(fixture))

    main.store = main.SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        main,
        "_build_provider",
        lambda: MistralSplitterProvider(annotated_fixture_path=fpath),
    )
    client = authed_client(app)

    # Upload any readable PDF; page count may differ from fixture's 5 — that's OK
    # for provider mapping which uses usage_info.pages_processed from the fixture.
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "pkt.pdf"
    c = canvas.Canvas(str(pdf))
    for i in range(5):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    with open(pdf, "rb") as fh:
        up = client.post(
            "/api/upload", files={"file": ("pkt.pdf", fh, "application/pdf")}
        )
    assert up.status_code == 200
    sid = up.json()["session_id"]
    resp = client.post(f"/api/session/{sid}/split")
    assert resp.status_code == 200
    body = resp.json()
    assert body["uncovered_pages"] == [4, 5]
    assert any(e.startswith("uncovered_pages:") for e in body["errors"] or [])
