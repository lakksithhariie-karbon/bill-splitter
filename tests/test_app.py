"""Tests for the FastAPI backend: upload, split caching, save, corrections round-trip."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from pdfsplit.app import main
from pdfsplit.app.main import app
from tests.conftest import authed_client
from pdfsplit.schema import SplitDocument
from pdfsplit.scoring import DocTruth, boundary_metrics, score_packet

_WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
_requires_web_dist = pytest.mark.skipif(
    not (_WEB_DIST / "index.html").is_file(),
    reason="web/dist missing — run: npm --prefix web run build",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Point the session store at a temp dir so tests don't pollute output/.
    main.store = main.SessionStore(tmp_path / "sessions")
    # Force the provider to replay a Mistral annotated fixture (no API key, no
    # credit spend). A fixture is passed explicitly — never via an env var,
    # which would silently turn the live app into replay.
    fixture = (
        Path(__file__).resolve().parents[1]
        / "vendors"
        / "mistral"
        / "fixtures"
        / "pkt_001_annotated.json"
    )
    monkeypatch.setattr(main, "_build_provider", _replay_mistral_provider(fixture))
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    return authed_client(app)


def _replay_mistral_provider(fixture: Path):
    from pdfsplit.providers.mistral import MistralSplitterProvider

    def _build():
        return MistralSplitterProvider(annotated_fixture_path=fixture)

    return _build


@pytest.fixture()
def corpus_pdf(tmp_path):
    """12-page PDF for app tests (independent of boundary corpus packet ids)."""
    from reportlab.pdfgen import canvas

    path = tmp_path / "pkt_001_control_multi_class.pdf"
    c = canvas.Canvas(str(path))
    for i in range(12):
        c.drawString(72, 720, f"Fixture page {i + 1}")
        c.showPage()
    c.save()
    return path


def _upload(client, pdf_path):
    with open(pdf_path, "rb") as fh:
        resp = client.post(
            "/api/upload", files={"file": ("pkt_001.pdf", fh, "application/pdf")}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_returns_session_and_thumbnails(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    assert "session_id" in data
    assert data["page_count"] == 12
    assert len(data["thumbnails"]) == 12
    assert data["thumbnails"][0].endswith("?size=thumb")


def test_page_image_renders(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    resp = client.get(f"/api/session/{sid}/page/1.png?size=thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 0


def test_split_called_once_per_session(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    r1 = client.post(f"/api/session/{sid}/split")
    r2 = client.post(f"/api/session/{sid}/split")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    # The fixture is replayed; the cache file exists and holds the result.
    session = main.store.get(sid)
    assert session.has_split()
    assert session.split_cache_path.exists()


def test_save_returns_zip_matching_corrected_boundaries(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    client.post(f"/api/session/{sid}/split")
    # Corrected boundaries: merge the 3 predicted docs into 2.
    corrected = [
        {"doc_type": "invoice", "start_page": 1, "end_page": 3},
        {"doc_type": "statement", "start_page": 4, "end_page": 12},
    ]
    resp = client.post(
        f"/api/session/{sid}/save",
        json={"documents": corrected, "correction_types": ["BOUNDARY", "CLASS"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) == 2
    # Verify each split PDF has the right page count.
    from pypdf import PdfReader

    for name in names:
        with zf.open(name) as fh:
            n = len(PdfReader(io.BytesIO(fh.read())).pages)
        if "p0001-0003" in name:
            assert n == 3
        elif "p0004-0012" in name:
            assert n == 9


def test_save_invalid_range_returns_4xx(client, corpus_pdf):
    """An invalid range (e.g. end_page < start_page) must return a readable 4xx,
    not a 500. This is the failure mode of the duplicate-cut bug, where a
    [1..0] document reached the save endpoint."""
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    client.post(f"/api/session/{sid}/split")
    resp = client.post(
        f"/api/session/{sid}/save",
        json={
            "documents": [{"doc_type": "invoice", "start_page": 1, "end_page": 0}],
            "correction_types": ["BOUNDARY"],
        },
    )
    assert resp.status_code == 400
    assert "Invalid document range" in resp.json()["detail"]


def test_corrections_json_feeds_scoring_without_shim(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    client.post(f"/api/session/{sid}/split")
    corrected = [
        {"doc_type": "invoice", "start_page": 1, "end_page": 3},
        {"doc_type": "statement", "start_page": 4, "end_page": 12},
    ]
    client.post(
        f"/api/session/{sid}/save",
        json={"documents": corrected, "correction_types": ["BOUNDARY", "CLASS"]},
    )
    session = main.store.get(sid)
    payload = json.loads(session.corrections_path.read_text())

    # Build truth (DocTruth) and pred (SplitDocument) from the file, exactly as
    # scoring.py expects, with no shim.
    truth = [
        DocTruth(
            doc_type=d["doc_type"],
            start_page=d["start_page"],
            end_page=d["end_page"],
        )
        for d in payload["corrected_documents"]
    ]
    pred = [
        SplitDocument(
            doc_type=d["doc_type"],
            start_page=d["start_page"],
            end_page=d["end_page"],
            confidence=d["confidence"],
        )
        for d in payload["predicted_documents"]
    ]
    score = score_packet(truth, pred)
    # The corrected truth is 2 docs; the provider predicted 3. Boundary metrics
    # must compute without error.
    assert "boundary_precision" in score
    assert "boundary_recall" in score
    assert "boundary_f1" in score
    assert score["true_doc_count"] == 2
    assert score["pred_doc_count"] == 3


def test_providers_endpoint(client):
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert "mistral" in body
    assert body["mistral"]["status"] in ("ready", "missing_credentials")


def test_upload_rejects_non_pdf(client):
    resp = client.post(
        "/api/upload", files={"file": ("x.txt", b"not a pdf", "text/plain")}
    )
    assert resp.status_code == 400


def test_split_response_includes_extraction_and_cost(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    resp = client.post(f"/api/session/{sid}/split")
    body = resp.json()
    # Cost in dollars, pages billed, annotated rate.
    assert body["cost_usd"] == pytest.approx(12 * 0.005)  # 12 pages @ $0.005
    assert body["pages_billed"] == 12
    assert body["annotated_rate"] == 0.005
    # Extraction data present with real confidence (not 0.0).
    ext = body["extraction"]
    assert ext is not None
    assert len(ext["documents"]) == 3
    assert all(d["confidence"] > 0.0 for d in ext["documents"])
    # The fixture is replayed, so the response must flag it as a replay.
    assert body["is_replay"] is True
    assert body["model_version"].startswith("replay-")


def test_split_missing_credentials_returns_json_error(corpus_pdf, tmp_path, monkeypatch):
    """Without an API key or fixture, /split must return JSON detail — not a
    bare 500 text body that breaks the React client's res.json()."""
    from dataclasses import replace

    from pdfsplit.config import settings as app_settings

    main.store = main.SessionStore(tmp_path / "sessions")
    empty = replace(app_settings, mistral_api_key="")
    monkeypatch.setattr(
        main,
        "_build_provider",
        lambda: __import__(
            "pdfsplit.providers.mistral", fromlist=["MistralSplitterProvider"]
        ).MistralSplitterProvider(settings=empty),
    )
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client = authed_client(app)

    data = _upload(client, corpus_pdf)
    resp = client.post(f"/api/session/{data['session_id']}/split")
    assert resp.status_code == 400
    body = resp.json()
    assert "detail" in body
    assert "MISTRAL_API_KEY" in body["detail"]


@_requires_web_dist
def test_index_serves_frontend(client):
    """Serve the React build from web/dist."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="root"' in html
    assert "/assets/" in html


@_requires_web_dist
def test_index_assets_all_load(client):
    """Every src/href on the served React index must return 200."""
    import re

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html)
    assert refs, "expected at least one src/href in the served HTML"

    for ref in refs:
        if ref.startswith("#") or ref.startswith("http"):
            continue
        asset = client.get(ref)
        assert asset.status_code == 200, f"asset {ref!r} returned {asset.status_code}"

    assert re.search(
        r'<script[^>]*\bsrc\s*=\s*["\']/assets/[^"\']+\.js["\']', html
    ), "expected a <script> tag loading a /assets/*.js bundle"
    assert 'id="root"' in html


def test_index_missing_dist_returns_build_instruction(client, tmp_path, monkeypatch):
    """Without web/dist, / must tell the operator to build — not 500."""
    from pdfsplit.app import main as app_main

    monkeypatch.setattr(app_main, "_WEB_DIST", tmp_path / "missing-dist")
    resp = client.get("/")
    assert resp.status_code == 503
    assert "npm --prefix web run build" in resp.json()["detail"]


def test_analyze_returns_segments_without_extraction(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    resp = client.post(f"/api/session/{sid}/analyze")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "segments" in body
    assert len(body["segments"]) >= 1
    for seg in body["segments"]:
        assert "page_start" in seg and "page_end" in seg and "doc_type" in seg
    assert body["page_count"] == 12
    assert body["pages_processed"] == 12
    assert "extraction" not in body or body.get("extraction") is None
    session = main.store.get(sid)
    assert session.has_ocr()
    assert session.has_analyze()
    assert session.extraction_result is None
    assert session.ocr_cache_path.exists()


def test_analyze_twice_one_provider_call(corpus_pdf, tmp_path, monkeypatch):
    """Second /analyze must hit the session cache, not the provider."""
    from unittest.mock import MagicMock

    from pdfsplit.extraction import ExtractionResult

    main.store = main.SessionStore(tmp_path / "sessions")
    ocr = {
        "pages": [
            {"index": i, "markdown": f"page {i+1}", "blocks": []}
            for i in range(12)
        ],
        "usage_info": {"pages_processed": 12},
    }
    analyze_out = {
        "segments": [
            {
                "page_start": 1,
                "page_end": 12,
                "doc_type": "packet",
                "confidence": 0.9,
            }
        ],
        "pages_processed": 12,
        "errors": [],
        "raw_response_path": None,
        "ocr_response": ocr,
        "model_version": "mistral-test",
        "latency_ms": 1.0,
        "provider": "mistral",
    }
    provider = MagicMock()
    provider.name = "mistral"
    provider.model_version = "mistral-test"
    provider.analyze.return_value = analyze_out
    provider._ocr_response = ocr
    provider.extract.return_value = ExtractionResult(
        provider="mistral",
        model_version="mistral-test",
        documents=[],
        pages_processed=12,
    )
    monkeypatch.setattr(main, "_build_provider", lambda: provider)
    client = authed_client(app)

    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    r1 = client.post(f"/api/session/{sid}/analyze")
    r2 = client.post(f"/api/session/{sid}/analyze")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["segments"] == r2.json()["segments"]
    assert provider.analyze.call_count == 1


def test_extract_with_confirmed_segments(corpus_pdf, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from pdfsplit.extraction import ExtractedDocument, ExtractionResult

    main.store = main.SessionStore(tmp_path / "sessions")
    ocr = {
        "pages": [
            {"index": i, "markdown": f"page {i+1}", "blocks": []}
            for i in range(12)
        ],
        "usage_info": {"pages_processed": 12},
    }
    analyze_out = {
        "segments": [
            {
                "page_start": 1,
                "page_end": 3,
                "doc_type": "invoice",
                "confidence": 0.8,
            },
            {
                "page_start": 4,
                "page_end": 12,
                "doc_type": "statement",
                "confidence": 0.7,
            },
        ],
        "pages_processed": 12,
        "errors": [],
        "raw_response_path": None,
        "ocr_response": ocr,
        "model_version": "mistral-test",
        "latency_ms": 1.0,
        "provider": "mistral",
    }
    extraction = ExtractionResult(
        provider="mistral",
        model_version="mistral-test",
        documents=[
            ExtractedDocument(
                page_start=1,
                page_end=3,
                doc_type="invoice",
                confidence=0.8,
            ),
            ExtractedDocument(
                page_start=4,
                page_end=12,
                doc_type="statement",
                confidence=0.7,
            ),
        ],
        pages_processed=12,
        cost_usd=0.01,
        cost_breakdown={"segment_disagreements": []},
    )
    provider = MagicMock()
    provider.name = "mistral"
    provider.model_version = "mistral-test"
    provider.analyze.return_value = analyze_out
    provider._ocr_response = None
    provider.extract.return_value = extraction
    monkeypatch.setattr(main, "_build_provider", lambda: provider)
    client = authed_client(app)

    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    assert client.post(f"/api/session/{sid}/analyze").status_code == 200

    confirmed = [
        {"page_start": 1, "page_end": 3, "doc_type": "invoice"},
        {"page_start": 4, "page_end": 12, "doc_type": "statement"},
    ]
    resp = client.post(f"/api/session/{sid}/extract", json={"segments": confirmed})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extraction"] is not None
    assert len(body["extraction"]["documents"]) == 2
    assert body["segment_disagreements"] == []
    provider.extract.assert_called_once()
    call_kwargs = provider.extract.call_args
    assert call_kwargs.kwargs.get("precomputed_segments") == confirmed
    # OCR must be loaded onto the provider before extract.
    assert provider._ocr_response == ocr


def test_extract_invalid_segments_returns_400(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    assert client.post(f"/api/session/{sid}/analyze").status_code == 200
    # Gap / incomplete coverage.
    resp = client.post(
        f"/api/session/{sid}/extract",
        json={
            "segments": [
                {"page_start": 1, "page_end": 3, "doc_type": "invoice"},
                {"page_start": 5, "page_end": 12, "doc_type": "statement"},
            ]
        },
    )
    assert resp.status_code == 400
    assert "cover" in resp.json()["detail"].lower() or "gap" in resp.json()["detail"].lower()

    # Overlap.
    resp2 = client.post(
        f"/api/session/{sid}/extract",
        json={
            "segments": [
                {"page_start": 1, "page_end": 6, "doc_type": "invoice"},
                {"page_start": 5, "page_end": 12, "doc_type": "statement"},
            ]
        },
    )
    assert resp2.status_code == 400


def test_extract_without_analyze_returns_400(client, corpus_pdf):
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    resp = client.post(
        f"/api/session/{sid}/extract",
        json={
            "segments": [
                {"page_start": 1, "page_end": 12, "doc_type": "packet"},
            ]
        },
    )
    assert resp.status_code == 400
    assert "analyze" in resp.json()["detail"].lower()


def test_split_still_works_after_two_stage(client, corpus_pdf):
    """Legacy /split remains a working analyze+extract wrapper."""
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    resp = client.post(f"/api/session/{sid}/split")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extraction"] is not None
    assert len(body["extraction"]["documents"]) == 3
    assert body["is_replay"] is True
    session = main.store.get(sid)
    assert session.has_ocr()
    assert session.ocr_cache_path.exists()


def test_vouchers_endpoint_maps_via_python(client, corpus_pdf):
    """Export uses server-side to_voucher_payload, not a client mirror."""
    data = _upload(client, corpus_pdf)
    sid = data["session_id"]
    assert client.post(f"/api/session/{sid}/split").status_code == 200

    docs = [
        {
            "page_start": 1,
            "page_end": 1,
            "doc_type": "purchase",
            "seller": {
                "name": {"value": "Acme", "source_text": "Acme", "pages": [1]},
                "address": {"value": "", "source_text": "", "pages": []},
                "tax_id": {"value": "GST1", "source_text": "GST1", "pages": [1]},
            },
            "buyer": {
                "name": {"value": "", "source_text": "", "pages": []},
                "address": {"value": "", "source_text": "", "pages": []},
                "tax_id": {"value": "", "source_text": "", "pages": []},
            },
            "document_id": {"value": "INV-9", "source_text": "INV-9", "pages": [1]},
            "issue_date": {"value": "2025-01-01", "source_text": "2025-01-01", "pages": [1]},
            "due_date": {"value": "", "source_text": "", "pages": []},
            "currency": {"value": "INR", "source_text": "INR", "pages": [1]},
            "po_number": {"value": "", "source_text": "", "pages": []},
            "payment_terms": {"value": "", "source_text": "", "pages": []},
            "billing_period": {"value": "", "source_text": "", "pages": []},
            "totals": {
                "subtotal": {"value": "100", "source_text": "100", "pages": [1]},
                "tax_lines": [
                    {
                        "name": {"value": "CGST", "source_text": "CGST", "pages": [1]},
                        "rate": {"value": "", "source_text": "", "pages": []},
                        "base": {"value": "", "source_text": "", "pages": []},
                        "amount": {"value": "9", "source_text": "9", "pages": [1]},
                        "kind": {"value": "gst", "source_text": "gst", "pages": []},
                    }
                ],
                "total": {"value": "118", "source_text": "118", "pages": [1]},
                "amount_due": {"value": "0.00", "source_text": "0.00", "pages": [1]},
                "tds": {"value": "", "source_text": "", "pages": []},
                "other_taxes": {"value": "", "source_text": "", "pages": []},
            },
            "line_items": [
                {
                    "page": 1,
                    "description": {"value": "Widget", "source_text": "Widget", "pages": [1]},
                    "quantity": {"value": "1", "source_text": "1", "pages": [1]},
                    "unit_price": {"value": "100", "source_text": "100", "pages": [1]},
                    "amount": {"value": "100", "source_text": "100", "pages": [1]},
                    "hsn_sac": {"value": "8471", "source_text": "8471", "pages": [1]},
                    "discount": {"value": "", "source_text": "", "pages": []},
                }
            ],
            "other_fields": [],
            "totals_mismatch": False,
        }
    ]
    resp = client.post(f"/api/session/{sid}/vouchers", json={"documents": docs})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["vouchers"]) == 1
    v = body["vouchers"][0]
    assert v["vendorName"] == "Acme"
    assert v["supInvNo"] == "INV-9"
    assert v["vchType"] == "purchase"
    assert v["vchAmt"] == "118"
    assert v["amountDue"] == "0.00"
    assert v["alreadyPaid"] is True
    assert v["purchase_account"] is None
    assert v["godown"] is None
    assert "uom" not in v["lines"]["items"][0]
    assert "skuCode" not in v["lines"]["items"][0]
    assert v["lines"]["taxes"][0]["kind"] == "gst"
    assert "bank" not in v

    bad = client.post(f"/api/session/{sid}/vouchers", json={"documents": []})
    assert bad.status_code == 400
