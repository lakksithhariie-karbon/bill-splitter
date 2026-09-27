"""AIA filename format: packet-VEN3-number with grounding + collisions."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from pdfsplit.aia.filenames import (
    apply_collision_suffixes,
    attach_names_to_segments,
    build_base_file_name,
    extract_doc_number_id,
    plan_filenames,
    resolve_slots_for_segment,
    sanitize_slot,
    text_grounded_on_pages,
    NameSlots,
)
from pdfsplit.aia.push import plan_from_segments
from pdfsplit.app.session import SessionStore


def _tiny_pdf(path: Path, pages: int = 1) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path.read_bytes()


def test_extract_doc_number_id_from_labelled_line():
    assert extract_doc_number_id("Invoice No. INV-2026-8841") == "INV-2026-8841"
    assert extract_doc_number_id("Bill No: BL-99") == "BL-99"
    assert extract_doc_number_id("Invoice No KFIPL/November20/04") == (
        "KFIPL/November20/04"
    )


def test_sanitize_unsafe_vendor_chars():
    raw = 'Acme "Corp"/東京 🚀 Ltd'
    safe = sanitize_slot(raw, fallback="x")
    assert "/" not in safe
    assert '"' not in safe
    assert " " not in safe
    assert safe  # non-empty
    assert len(safe) <= 30


def test_collision_suffixes_stable_order():
    bases = [
        "pkt-ACM-INV-1.pdf",
        "pkt-ACM-INV-1.pdf",
        "pkt-OTH-INV-2.pdf",
        "pkt-ACM-INV-1.pdf",
    ]
    out = apply_collision_suffixes(bases)
    assert out == [
        "pkt-ACM-INV-1.pdf",
        "pkt-ACM-INV-1-2.pdf",
        "pkt-OTH-INV-2.pdf",
        "pkt-ACM-INV-1-3.pdf",
    ]


def test_identical_invoice_numbers_get_distinct_filenames(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "march-bills.pdf", pages=2)
    session = store.create("march-bills.pdf", pdf, page_count=2)
    planned = plan_from_segments(
        session,
        [
            {
                "page_start": 1,
                "page_end": 1,
                "vendor": "Freshworks",
                "document_number": "INV-2026-8841",
            },
            {
                "page_start": 2,
                "page_end": 2,
                "vendor": "Freshworks",
                "document_number": "INV-2026-8841",
            },
        ],
        source_stem="march-bills",
    )
    names = [p.file_name for p in planned]
    assert names[0] == "march-bills-FRE-INV-2026-8841.pdf"
    assert names[1] == "march-bills-FRE-INV-2026-8841-2.pdf"
    assert names[0] != names[1]


def test_judge_number_absent_from_page_rejected():
    pages = ["Freshworks Inc\nInvoice No. INV-REAL-1\nTotal 100"]
    slots = resolve_slots_for_segment(
        packet="march-bills",
        index=1,
        vendor_hint="Freshworks Inc",
        number_hint="INV-FAKE-999",
        page_markdowns=pages,
        header_text="Freshworks Inc",
    )
    assert slots.number == "INV-REAL-1" or slots.number_source == "regex"
    assert slots.number != "INV-FAKE-999"
    assert "FAKE" not in (slots.number or "")


def test_judge_empty_falls_back_positional():
    slots = resolve_slots_for_segment(
        packet="march-bills",
        index=4,
        vendor_hint=None,
        number_hint=None,
        page_markdowns=["no identifiers here at all"],
        header_text="",
    )
    assert slots.used_positional
    assert slots.vendor == "Invoice-04"
    name = build_base_file_name(slots)
    assert name == "march-bills-INV.pdf"


def test_grounding_requires_verbatim_on_page():
    assert text_grounded_on_pages("INV-1", ["Invoice No. INV-1"])
    assert not text_grounded_on_pages("INV-2", ["Invoice No. INV-1"])


def test_same_packet_twice_identical_names():
    pages = [
        {
            "markdown": "# Freshworks\nInvoice No. INV-2026-8841\nTotal 10",
            "blocks": [
                {
                    "type": "title",
                    "content": "Freshworks",
                    "top_left_y": 0.01,
                }
            ],
        },
        {
            "markdown": "# Acme\nBill No. BL-55\nTotal 20",
            "blocks": [
                {"type": "title", "content": "Acme", "top_left_y": 0.01}
            ],
        },
    ]
    segs_a = [
        {"page_start": 1, "page_end": 1, "doc_type": "invoice"},
        {"page_start": 2, "page_end": 2, "doc_type": "invoice"},
    ]
    segs_b = [
        {"page_start": 1, "page_end": 1, "doc_type": "invoice"},
        {"page_start": 2, "page_end": 2, "doc_type": "invoice"},
    ]
    attach_names_to_segments(segs_a, pages, packet_stem="march-bills")
    attach_names_to_segments(segs_b, pages, packet_stem="march-bills")
    assert [s["aia_file_name"] for s in segs_a] == [
        s["aia_file_name"] for s in segs_b
    ]
    assert segs_a[0]["vendor"] == segs_b[0]["vendor"]
    assert segs_a[0]["document_number"] == segs_b[0]["document_number"]


def test_user_edit_survives_reattach():
    """Edits passed into resolve are authoritative (skip grounding)."""
    slots = resolve_slots_for_segment(
        packet="pkt",
        index=1,
        vendor_hint="WrongVendor",
        number_hint="WRONG-1",
        page_markdowns=["Real Vendor\nInvoice No. REAL-9"],
        header_text="Real Vendor",
        vendor_edit="My Vendor",
        number_edit="EDIT-42",
    )
    assert slots.vendor == "My Vendor"
    assert slots.number == "EDIT-42"
    assert slots.vendor_source == "edit"
    assert slots.number_source == "edit"


def test_preview_equals_push_filename(tmp_path: Path):
    """planFilenames (preview) == plan_from_segments (push) byte-for-byte."""
    store = SessionStore(tmp_path / "sessions")
    pdf = _tiny_pdf(tmp_path / "pkt.pdf", pages=3)
    session = store.create("original.pdf", pdf, page_count=3)
    items = [
        {"vendor": "Freshworks", "document_number": "INV-1", "index": 1},
        {"vendor": "Freshworks", "document_number": "INV-1", "index": 2},
        {"vendor": "", "document_number": "", "index": 3},
    ]
    preview = plan_filenames(packet="march-bills", items=items)
    planned = plan_from_segments(
        session,
        [
            {
                "page_start": 1,
                "page_end": 1,
                "vendor": "Freshworks",
                "document_number": "INV-1",
            },
            {
                "page_start": 2,
                "page_end": 2,
                "vendor": "Freshworks",
                "document_number": "INV-1",
            },
            {"page_start": 3, "page_end": 3},
        ],
        source_stem="march-bills",
    )
    assert [p.file_name for p in planned] == preview


def test_fallbacks_omit_empty_number_slot():
    assert (
        build_base_file_name(
            NameSlots(packet="march-bills", vendor="", number="INV-1")
        )
        == "march-bills-NA-INV-1.pdf"
    )
    assert (
        build_base_file_name(
            NameSlots(packet="march-bills", vendor="Freshworks", number="")
        )
        == "march-bills-FRE.pdf"
    )


def test_vendor_code_short_and_empty():
    # Empty vendor → NA.
    assert build_base_file_name(NameSlots(packet="pkt", vendor="", number="INV-1")) == (
        "pkt-NA-INV-1.pdf"
    )
    # Vendor shorter than 3 → use what exists.
    assert build_base_file_name(NameSlots(packet="pkt", vendor="AB", number="INV-1")) == (
        "pkt-AB-INV-1.pdf"
    )
    # Uppercased, first 3 of sanitized.
    assert build_base_file_name(
        NameSlots(packet="pkt", vendor="GROCERYGRID PRIVATE LIMITED", number="GGPL/4045/26-27")
    ) == "pkt-GRO-GGPL-4045-26-27.pdf"


def test_slot_cap_truncates_long_vendor():
    long = "A" * 80
    name = build_base_file_name(
        NameSlots(packet="pkt", vendor=long, number="INV-1")
    )
    # Vendor code is fixed at first-3 (AAA), packet + code + number.
    assert name.startswith("pkt-AAA-")
    assert len(name) <= 180
