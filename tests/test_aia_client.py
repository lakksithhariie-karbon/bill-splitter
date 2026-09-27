"""Tests for AIA AP client — fixtures only, never live."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfsplit.aia.client import AiaApClient, PushStepError
from pdfsplit.aia.models import RejectReasonCode
from pdfsplit.aia.push import plan_session_invoices, push_session
from pdfsplit.aia.reasons import parse_reject_reason
from pdfsplit.aia.transport import (
    AiaLiveDisabledError,
    FixtureTransport,
    assert_live_allowed,
)
from pdfsplit.app.session import SessionStore
from pdfsplit.config import Settings
from pdfsplit.extraction import ExtractedDocument, ExtractionResult
from pdfsplit.schema import SplitDocument, SplitResult

FIXTURES = Path(__file__).resolve().parents[1] / "vendors" / "aia" / "fixtures"


def _tiny_pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.drawString(72, 720, f"Fixture page {i + 1}")
        c.showPage()
    c.save()
    return path


def _settings(**kwargs) -> Settings:
    base = {
        "mistral_api_key": "",
        "aia_api_base": "",
        "aia_allow_writes": False,
        "max_downstream_pages": 12,
    }
    base.update(kwargs)
    return Settings(**base)


def test_default_refuses_live_http():
    """Guardrail: default settings cannot construct a live transport."""
    with pytest.raises(AiaLiveDisabledError):
        assert_live_allowed(api_base="", allow_writes=False)
    with pytest.raises(AiaLiveDisabledError):
        assert_live_allowed(api_base="https://example.test", allow_writes=False)
    with pytest.raises(AiaLiveDisabledError):
        assert_live_allowed(api_base="", allow_writes=True)

    cfg = _settings()
    client = AiaApClient(settings=cfg)
    assert isinstance(client._transport, FixtureTransport)

    # Even with a base URL, writes must be opted in — client stays on fixtures.
    cfg2 = _settings(aia_api_base="https://example.test", aia_allow_writes=False)
    client2 = AiaApClient(settings=cfg2)
    assert isinstance(client2._transport, FixtureTransport)
    assert AiaApClient.live_writes_enabled(cfg2) is False


def test_http_transport_ctor_refuses_without_allow_writes():
    """HttpTransport.__init__ itself enforces the guardrail — injection cannot bypass."""
    from pdfsplit.aia.transport import HttpTransport

    with pytest.raises(AiaLiveDisabledError):
        HttpTransport(
            api_base="https://app.example.com",
            cookie_header="session=fixture-not-real",
            settings=_settings(aia_allow_writes=False),
        )


def test_http_transport_requires_both_env_vars():
    with pytest.raises(AiaLiveDisabledError):
        assert_live_allowed(api_base="", allow_writes=True)
    with pytest.raises(AiaLiveDisabledError):
        assert_live_allowed(api_base="https://example.test", allow_writes=False)
    base = assert_live_allowed(
        api_base="https://example.test", allow_writes=True
    )
    assert base == "https://example.test"
    # Client still defaults to fixtures unless BOTH settings are set.
    cfg = _settings(aia_api_base="https://example.test", aia_allow_writes=False)
    assert isinstance(AiaApClient(settings=cfg)._transport, FixtureTransport)


def test_push_bill_happy_path(tmp_path: Path):
    pdf = _tiny_pdf(tmp_path / "inv.pdf", pages=2)
    transport = FixtureTransport(FIXTURES)
    client = AiaApClient(settings=_settings(), transport=transport, erp="tally")
    ref = client.push_bill(pdf, "inv.pdf", page_count=2, invoice_number="INV-1")
    assert ref.ok
    assert ref.step_completed == 4
    assert ref.file_uuid
    methods = [c["method"] + " " + c["path"] for c in transport.calls]
    assert methods[0].startswith("GET /api/auth/session")
    assert "POST /api/upload-file-to-dms" in methods
    assert "POST /api/upload-file" in methods
    assert "PATCH /api/upload-file-to-dms" in methods
    assert "POST /api/accounts-payable/bill-status" in methods


@pytest.mark.parametrize("fail_step", [1, 2, 3, 4])
def test_push_bill_failure_at_each_step(tmp_path: Path, fail_step: int):
    pdf = _tiny_pdf(tmp_path / "inv.pdf")
    transport = FixtureTransport(FIXTURES, fail_steps={fail_step})
    client = AiaApClient(settings=_settings(), transport=transport)
    with pytest.raises(PushStepError) as ei:
        client.push_bill(pdf, "inv.pdf")
    ref = ei.value.bill_ref
    assert ref.failed_step == fail_step
    assert ref.step_completed == fail_step - 1
    if fail_step >= 2:
        assert ref.file_uuid  # obtained at step 1


def test_hitl_rejected_page_limit_reason():
    transport = FixtureTransport(
        FIXTURES,
        overrides={
            "GET /api/accounts-payable/bill-status": FIXTURES
            / "bill_status_get_rejected.json",
            "GET /api/accounts-payable/file-status-error-details": FIXTURES
            / "error_details_page_limit.json",
        },
    )
    client = AiaApClient(settings=_settings(), transport=transport)
    rows = client.bill_status()
    assert rows[0].status == "file_hitl_rejected"
    reason = client.rejection_for(rows[0])
    assert reason is not None
    assert reason.code == RejectReasonCode.PAGE_LIMIT
    assert reason.pdf_pages == 31
    assert reason.page_limit == 12


def test_parse_page_limit_message():
    r = parse_reject_reason("Extraction failed: PDF has 31 pages; limit is 12.")
    assert r.code == RejectReasonCode.PAGE_LIMIT
    assert r.pdf_pages == 31


def test_duplicate_invoice_check():
    transport = FixtureTransport(FIXTURES)
    client = AiaApClient(settings=_settings(), transport=transport)
    assert client.supplier_invoice_exists("INV-OK-1") is False
    assert client.supplier_invoice_exists("DUP-ALREADY") is True


def test_review_voucher_read():
    client = AiaApClient(settings=_settings(), transport=FixtureTransport(FIXTURES))
    env = client.review_voucher("00000000-0000-4000-8000-0000000000b1")
    assert "prediction" in env
    assert env["prediction"]["status"] == "needs_review"


def test_oversized_preflight_and_dry_run(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "packet.pdf", pages=20)
    session = store.create("packet.pdf", pdf.read_bytes(), page_count=20)
    # One 15-page invoice + one 2-page invoice
    store.save_split(
        session,
        SplitResult(
            input_ref=str(pdf),
            provider="mock",
            model_version="test",
            documents=[
                SplitDocument(
                    doc_type="invoice", start_page=1, end_page=15, confidence=1.0
                ),
                SplitDocument(
                    doc_type="invoice", start_page=16, end_page=17, confidence=1.0
                ),
            ],
            latency_ms=0,
            cost_usd=0,
        ),
    )
    store.save_extraction(
        session,
        ExtractionResult(
            provider="mock",
            model_version="test",
            documents=[
                ExtractedDocument(
                    page_start=1,
                    page_end=15,
                    invoice_number="BIG-1",
                ),
                ExtractedDocument(
                    page_start=16,
                    page_end=17,
                    invoice_number="DUP-ALREADY",
                ),
            ],
            documents_v2=[
                {
                    "page_start": 1,
                    "page_end": 15,
                    "document_id": "BIG-1",
                    "doc_type": "invoice",
                },
                {
                    "page_start": 16,
                    "page_end": 17,
                    "document_id": "DUP-ALREADY",
                    "doc_type": "invoice",
                },
            ],
        ),
    )

    transport = FixtureTransport(FIXTURES)
    client = AiaApClient(settings=_settings(), transport=transport)
    outcomes = push_session(
        session.id,
        dry_run=True,
        client=client,
        settings=_settings(output_dir=tmp_path),
        store=store,
    )
    assert len(outcomes) == 2
    assert outcomes[0].action == "skipped_oversized"
    assert outcomes[0].reject_reason is not None
    assert outcomes[0].reject_reason.code == RejectReasonCode.OVERSIZED_PREFLIGHT
    assert "AIA reads up to 12 pages" in (outcomes[0].reject_reason.message or "")
    assert outcomes[1].action == "skipped_duplicate"
    # Dry-run must not call upload endpoints.
    write_calls = [
        c
        for c in transport.calls
        if c["method"] in {"POST", "PATCH"} and "upload" in c["path"]
    ]
    assert write_calls == []


def test_push_session_execute_fixture(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "packet.pdf", pages=2)
    session = store.create("packet.pdf", pdf.read_bytes(), page_count=2)
    store.save_split(
        session,
        SplitResult(
            input_ref=str(pdf),
            provider="mock",
            model_version="test",
            documents=[
                SplitDocument(
                    doc_type="invoice", start_page=1, end_page=2, confidence=1.0
                ),
            ],
            latency_ms=0,
            cost_usd=0,
        ),
    )
    store.save_extraction(
        session,
        ExtractionResult(
            provider="mock",
            model_version="test",
            documents_v2=[
                {
                    "page_start": 1,
                    "page_end": 2,
                    "document_id": "INV-OK",
                    "doc_type": "invoice",
                }
            ],
        ),
    )
    transport = FixtureTransport(FIXTURES)
    client = AiaApClient(settings=_settings(), transport=transport)
    outcomes = push_session(
        session.id,
        dry_run=False,
        client=client,
        settings=_settings(output_dir=tmp_path),
        store=store,
    )
    assert len(outcomes) == 1
    assert outcomes[0].action == "pushed"
    assert outcomes[0].bill_ref is not None and outcomes[0].bill_ref.ok
    saved = json.loads((session.dir / "aia_push_outcomes.json").read_text())
    assert saved["dry_run"] is False


def test_plan_session_invoices_empty():
    assert callable(plan_session_invoices)