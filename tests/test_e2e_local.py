"""On-demand local e2e: upload → analyze → confirm → cut → verify.

Default path replays OCR fixtures (no Mistral spend). Pass --live for a
real analyze. AIA push stays off unless both env vars are set.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pdfsplit.app import main
from pdfsplit.app.main import app
from pdfsplit.config import Settings
from pdfsplit.pdf import verify_split
from pdfsplit.providers.mistral import MistralSplitterProvider
from pdfsplit.schema import SplitDocument

ROOT = Path(__file__).resolve().parents[1]
GGPL_FIXTURE = (
    ROOT / "vendors" / "mistral" / "fixtures" / "pkt_ggpl_continuation_annexure.json"
)


def _pdf_from_lines(path: Path, pages: list[list[str]]) -> Path:
    c = canvas.Canvas(str(path))
    for lines in pages:
        y = 780
        for line in lines:
            c.drawString(48, y, line[:110])
            y -= 16
        c.showPage()
    c.save()
    return path


def _ggpl_pdf(tmp_path: Path) -> Path:
    pages = json.loads(GGPL_FIXTURE.read_text())["pages"]
    lines = []
    for page in pages:
        md = page.get("markdown") or ""
        lines.append([ln for ln in md.splitlines() if ln.strip()][:24])
    return _pdf_from_lines(tmp_path / "pkt_ggpl_continuation_annexure.pdf", lines)


def _simple_pdf(tmp_path: Path, n: int) -> Path:
    return _pdf_from_lines(
        tmp_path / f"pkt_{n}p.pdf",
        [[f"Invoice No: INV-E2E-{i + 1:04d}", f"Fixture page {i + 1}"] for i in range(n)],
    )


def _replay_client(tmp_path, monkeypatch, fixture: Path) -> TestClient:
    main.store = main.SessionStore(tmp_path / "sessions")
    empty = Settings(mistral_api_key="")
    monkeypatch.setattr(
        main,
        "_build_provider",
        lambda: MistralSplitterProvider(settings=empty, fixture_path=fixture),
    )
    return TestClient(app)


def _upload(client: TestClient, pdf_path: Path, name: str = "packet.pdf") -> dict:
    with open(pdf_path, "rb") as fh:
        resp = client.post("/api/upload", files={"file": (name, fh, "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirm_and_cut(client: TestClient, sid: str, page_count: int, documents: list[dict]):
    """Human confirms ranges (analyze already cached a split) then cuts."""
    resp = client.post(
        f"/api/session/{sid}/save",
        json={"documents": documents, "correction_types": ["BOUNDARY"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert names, "zip is empty"
    session = main.store.get(sid)
    splits = []
    for d in documents:
        doc = SplitDocument(
            doc_type=d.get("doc_type") or "invoice",
            start_page=int(d["start_page"]),
            end_page=int(d["end_page"]),
            confidence=1.0,
        )
        expected = doc.end_page - doc.start_page + 1
        match = [n for n in names if f"p{doc.start_page:04d}-{doc.end_page:04d}" in n]
        assert match, names
        raw = zf.read(match[0])
        n_pages = len(PdfReader(io.BytesIO(raw)).pages)
        assert n_pages == expected
        # Round-trip integrity against the files on disk.
        for p in session.documents_dir.glob("*.pdf"):
            if p.name == match[0]:
                splits.append(type("S", (), {"doc": doc, "path": p})())
    problems = verify_split(session.pdf_path, splits)
    assert problems == [], problems
    covered = sum(d["end_page"] - d["start_page"] + 1 for d in documents)
    assert covered == page_count
    return names


def test_ggpl_fixture_is_redacted_and_keeps_structure():
    data = json.loads(GGPL_FIXTURE.read_text())
    blob = json.dumps(data)
    assert "GGPL" not in blob
    assert "karbon" not in blob.lower()
    pages = data["pages"]
    assert len(pages) == 3
    md = [p["markdown"] for p in pages]
    assert "continued to page number 3" in md[0]
    assert "ZZCO/8801/26-27" in md[0] and "ZZCO/8801/26-27" in md[1] and "ZZCO/8801/26-27" in md[2]
    assert "Grand Total" in md[1] and "Authorized Signatory" in md[1]
    assert "STATUTORY TAX ANALYSIS" in md[2]
    assert md[0].rstrip().endswith("Page 2")
    assert md[1].rstrip().endswith("Page 3")
    assert md[2].rstrip().endswith("Page 2")
    assert "Invoice No" not in blob and "Invoice Number" not in blob


def test_e2e_ggpl_replay_confirm_cut(tmp_path, monkeypatch):
    client = _replay_client(tmp_path, monkeypatch, GGPL_FIXTURE)
    pdf = _ggpl_pdf(tmp_path)
    data = _upload(client, pdf, "pkt_ggpl_continuation_annexure.pdf")
    assert data["page_count"] == 3
    sid = data["session_id"]

    analyzed = client.post(f"/api/session/{sid}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    body = analyzed.json()
    assert body["is_replay"] is True
    segs = body["segments"]
    assert segs, "analyze returned no segments"
    assert len(segs) == 1, segs
    assert int(segs[0]["page_start"]) == 1
    assert int(segs[0]["page_end"]) == 3
    overlay = body.get("overlay") or {}
    assert overlay.get("extent_hold_pages") == [2, 3]
    # Extent is what made it one document; overlay did not need to merge.
    assert (overlay.get("tier_counts") or {}).get("merge", 0) == 0
    evidence = " ".join(segs[0].get("evidence") or [])
    assert "declares extent" in evidence or "declared_extent" in (
        segs[0].get("signals") or []
    )

    # Human confirms the same range (already correct).
    names = _confirm_and_cut(
        client,
        sid,
        page_count=3,
        documents=[{"doc_type": "invoice", "start_page": 1, "end_page": 3}],
    )
    assert len(names) == 1


def test_e2e_simple_packet_replay(tmp_path, monkeypatch):
    """Generic packet: analyze returns coverage, human keeps those cuts."""
    client = _replay_client(tmp_path, monkeypatch, GGPL_FIXTURE)
    pdf = _simple_pdf(tmp_path, 3)
    data = _upload(client, pdf)
    sid = data["session_id"]
    analyzed = client.post(f"/api/session/{sid}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    segs = analyzed.json()["segments"]
    documents = [
        {
            "doc_type": s.get("doc_type") or "invoice",
            "start_page": int(s["page_start"]),
            "end_page": int(s["page_end"]),
        }
        for s in segs
    ]
    _confirm_and_cut(client, sid, page_count=3, documents=documents)


def test_aia_push_stays_off_without_both_env_vars(tmp_path, monkeypatch):
    client = _replay_client(tmp_path, monkeypatch, GGPL_FIXTURE)
    monkeypatch.setenv("AIA_ALLOW_WRITES", "false")
    monkeypatch.delenv("AIA_API_BASE", raising=False)
    pdf = _simple_pdf(tmp_path, 3)
    data = _upload(client, pdf)
    sid = data["session_id"]
    client.post(f"/api/session/{sid}/analyze")
    resp = client.post(
        f"/api/session/{sid}/push-aia",
        json={"dry_run": False, "segments": [{"page_start": 1, "page_end": 3}]},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.live
def test_e2e_live_mistral_analyze_and_cut(tmp_path, monkeypatch, request):
    if not request.config.getoption("--live"):
        pytest.skip("pass --live to spend Mistral credit")
    from pdfsplit.config import settings as live_settings

    if not live_settings.has_mistral_credentials():
        pytest.skip("MISTRAL_API_KEY not set")

    main.store = main.SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(main, "_build_provider", lambda: MistralSplitterProvider())
    client = TestClient(app)
    pdf = _simple_pdf(tmp_path, 2)
    data = _upload(client, pdf, "live_e2e.pdf")
    sid = data["session_id"]
    analyzed = client.post(f"/api/session/{sid}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    body = analyzed.json()
    assert body.get("is_replay") is not True
    segs = body["segments"]
    assert segs
    documents = [
        {
            "doc_type": s.get("doc_type") or "invoice",
            "start_page": int(s["page_start"]),
            "end_page": int(s["page_end"]),
        }
        for s in segs
    ]
    _confirm_and_cut(client, sid, page_count=2, documents=documents)
