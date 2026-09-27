"""Concurrent Mode A push — order, partial failure, retry subset."""

from __future__ import annotations

import json
import time
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfsplit.aia.client import AiaApClient
from pdfsplit.aia.push import plan_from_segments, push_session
from pdfsplit.aia.transport import FixtureTransport
from pdfsplit.app.session import SessionStore
from pdfsplit.config import Settings
from pdfsplit.schema import SplitDocument, SplitResult

FIXTURES = Path(__file__).resolve().parents[1] / "vendors" / "aia" / "fixtures"


def _tiny_pdf(path: Path, pages: int = 4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path


def _settings(tmp_path: Path, **kwargs) -> Settings:
    base = {
        "mistral_api_key": "",
        "aia_api_base": "",
        "aia_allow_writes": False,
        "max_downstream_pages": 12,
        "aia_push_concurrency": 4,
        "output_dir": tmp_path,
    }
    base.update(kwargs)
    return Settings(**base)


def test_concurrent_push_preserves_document_order(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "packet.pdf", pages=4)
    session = store.create("packet.pdf", pdf.read_bytes(), page_count=4)
    store.save_split(
        session,
        SplitResult(
            input_ref=str(pdf),
            provider="mock",
            model_version="test",
            documents=[
                SplitDocument(
                    doc_type="invoice", start_page=i, end_page=i, confidence=1.0
                )
                for i in range(1, 5)
            ],
            latency_ms=0,
            cost_usd=0,
        ),
    )
    transport = FixtureTransport(FIXTURES)
    client = AiaApClient(settings=_settings(tmp_path), transport=transport)
    events: list[dict] = []

    t0 = time.monotonic()
    outcomes = push_session(
        session.id,
        dry_run=False,
        wait_for_hitl=True,
        client=client,
        settings=_settings(tmp_path),
        store=store,
        segments=[
            {
                "page_start": i,
                "page_end": i,
                "doc_type": "invoice",
                "display_name": f"Invoice {i:02d}",
            }
            for i in range(1, 5)
        ],
        on_progress=events.append,
    )
    elapsed = time.monotonic() - t0

    assert [o.page_start for o in outcomes] == [1, 2, 3, 4]
    assert all(o.action == "pushed" for o in outcomes)
    # Fixture HITL is immediate — wall should stay well under serial*4.
    assert elapsed < 5.0

    # Progress indices never imply reordering of the row list.
    invoice_events = [e for e in events if e.get("type") == "invoice"]
    assert invoice_events
    assert max(e["index"] for e in invoice_events) == 3


def test_partial_failure_and_retry_failed_only(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "packet.pdf", pages=3)
    session = store.create("packet.pdf", pdf.read_bytes(), page_count=3)

    class FlakyTransport(FixtureTransport):
        def __call__(self, request):  # type: ignore[no-untyped-def]
            method = str(request.get("method") or "").upper()
            path = str(request.get("path") or "")
            body = request.get("json") or {}
            # Fail only Invoice 02's DMS create (step 1).
            if (
                method == "POST"
                and path.startswith("/api/upload-file-to-dms")
                and str(body.get("fileName") or "") == "packet-INV-2.pdf"
            ):
                return {
                    "status": 500,
                    "json": {"error": "forced failure"},
                    "headers": {},
                }
            return super().__call__(request)

    transport = FlakyTransport()
    client = AiaApClient(settings=_settings(tmp_path), transport=transport)
    segments = [
        {
            "page_start": i,
            "page_end": i,
            "doc_type": "invoice",
            "display_name": f"Invoice {i:02d}",
        }
        for i in range(1, 4)
    ]
    outcomes = push_session(
        session.id,
        dry_run=False,
        wait_for_hitl=False,
        client=client,
        settings=_settings(tmp_path),
        store=store,
        segments=segments,
    )
    assert len(outcomes) == 3
    assert outcomes[0].action == "pushed"
    assert outcomes[1].action == "failed"
    assert outcomes[2].action == "pushed"

    # Retry failed only with a healthy transport.
    ok_transport = FixtureTransport(FIXTURES)
    ok_client = AiaApClient(settings=_settings(tmp_path), transport=ok_transport)
    retried = push_session(
        session.id,
        dry_run=False,
        wait_for_hitl=False,
        client=ok_client,
        settings=_settings(tmp_path),
        store=store,
        segments=segments,
        only_indices=[1],
    )
    assert retried[0].action == "pushed"  # preserved from prior
    assert retried[1].action == "pushed"  # retried
    assert retried[2].action == "pushed"  # preserved
    saved = json.loads((session.dir / "aia_push_outcomes.json").read_text())
    assert saved["only_indices"] == [1]


def test_plan_order_stable_for_concurrent_names(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "march-bills.pdf", pages=3)
    session = store.create("march-bills.pdf", pdf.read_bytes(), page_count=3)
    session = store.get(session.id)
    assert session is not None
    planned = plan_from_segments(
        session,
        [
            {"page_start": 1, "page_end": 1, "display_name": "Invoice 01"},
            {"page_start": 2, "page_end": 2, "display_name": "Invoice 02"},
            {"page_start": 3, "page_end": 3, "display_name": "Invoice 03"},
        ],
    )
    assert [p.file_name for p in planned] == [
        "march-bills-INV.pdf",
        "march-bills-INV-2.pdf",
        "march-bills-INV-3.pdf",
    ]
