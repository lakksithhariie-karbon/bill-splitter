"""Session metadata + AIA filename planning (packet-VEN3-number)."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from pdfsplit.app.session import SessionStore
from pdfsplit.aia.push import (
    build_push_file_name,
    plan_from_segments,
    sanitize_display_name,
)


def _tiny_pdf(path: Path, pages: int = 1) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path.read_bytes()


def test_create_persists_and_get_rehydrates_filename(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "src.pdf", pages=2)
    created = store.create("march-bills.pdf", pdf, page_count=2)
    assert created.filename == "march-bills.pdf"
    assert created.meta_path.is_file()

    loaded = store.get(created.id)
    assert loaded is not None
    assert loaded.filename == "march-bills.pdf"
    assert loaded.page_count == 2
    # On-disk bytes stay at upload.pdf; only the metadata name changes.
    assert loaded.pdf_path.name == "upload.pdf"


def test_get_falls_back_without_metadata(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "src.pdf", pages=1)
    created = store.create("ignored.pdf", pdf, page_count=1)
    created.meta_path.unlink()

    loaded = store.get(created.id)
    assert loaded is not None
    assert loaded.filename == "upload.pdf"


def test_sanitize_display_name_collapses_whitespace_and_strips_paths():
    assert sanitize_display_name("Freshworks  Sept") == "Freshworks-Sept"
    assert sanitize_display_name("a/b\\c") == "a-b-c"
    assert sanitize_display_name("  Invoice 03  ") == "Invoice-03"


def test_build_push_file_name_format():
    name = build_push_file_name(
        source_filename="march-bills.pdf",
        vendor="Freshworks",
        document_number="INV-2026-8841",
    )
    assert name == "march-bills-FRE-INV-2026-8841.pdf"


def test_plan_from_segments_uses_source_stem_and_slots(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "pkt.pdf", pages=10)
    session = store.create("pkt_b08_volume_push_mix.pdf", pdf, page_count=10)
    session = store.get(session.id)
    assert session is not None

    planned = plan_from_segments(
        session,
        [
            {
                "page_start": 2,
                "page_end": 3,
                "doc_type": "invoice",
                "vendor": "Invoice-01",
            },
            {
                "page_start": 6,
                "page_end": 8,
                "doc_type": "invoice",
                "vendor": "Freshworks Sept",
            },
        ],
    )
    assert planned[0].file_name == "pkt_b08_volume_push_mix-INV.pdf"
    assert planned[1].file_name == "pkt_b08_volume_push_mix-FRE.pdf"


def test_plan_from_segments_respects_source_stem_override(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "pkt.pdf", pages=4)
    session = store.create("original-upload.pdf", pdf, page_count=4)
    planned = plan_from_segments(
        session,
        [
            {
                "page_start": 1,
                "page_end": 2,
                "vendor": "Freshworks Sept",
            },
            {"page_start": 3, "page_end": 4, "vendor": "Invoice 02"},
        ],
        source_stem="march bills",
    )
    assert planned[0].file_name == "march-bills-FRE.pdf"
    assert planned[1].file_name == "march-bills-INV.pdf"
