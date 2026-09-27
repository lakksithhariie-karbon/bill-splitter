"""Tests for the pkt_008 rotated-content fix.

pkt_008 must be genuinely born-digital (extractable text) while pages 2 and 5
render sideways (a rotation transform in the content stream). This tests both
"an orientation change must not create a false boundary" and "can it read
rotated text" in one packet.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from pypdf import PdfReader

from pdfsplit.corpus import build_packet_pdf, PacketSpec


def _build_pkt_008(tmp_path) -> Path:
    spec = PacketSpec(
        packet_id="pkt_008_rotated",
        category="born-digital",
        scenario_tags=["rotated", "single-doc"],
        documents=[{"class": "report", "start_page": 1, "end_page": 6}],
        pages=6,
    )
    out = tmp_path / "pkt_008_rotated.pdf"
    build_packet_pdf(spec, out, mode="digital", rotate_pages={2, 5})
    return out


def test_pkt_008_has_extractable_text_on_all_pages(tmp_path):
    pdf = _build_pkt_008(tmp_path)
    reader = PdfReader(str(pdf))
    assert len(reader.pages) == 6
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        assert len(text) > 0, f"page {i + 1} has no extractable text"


def test_pkt_008_rotated_pages_carry_content_stream_rotation(tmp_path):
    pdf = _build_pkt_008(tmp_path)
    with pikepdf.open(pdf) as doc:
        for idx, page in enumerate(doc.pages):
            contents = page.get("/Contents")
            if hasattr(contents, "read_bytes"):
                data = contents.read_bytes()
            else:
                data = b"".join(c.read_bytes() for c in contents)
            text = data.decode("latin-1")
            has_rot = ("0 1 -1 0" in text) or ("0 -1 1 0" in text)
            # Pages 2 and 5 (1-indexed) must be rotated; others must not.
            if idx + 1 in (2, 5):
                assert has_rot, f"page {idx + 1} should carry a rotation transform"
            else:
                assert not has_rot, f"page {idx + 1} should not be rotated"


def test_pkt_008_rotated_pages_have_rotate_zero(tmp_path):
    """/Rotate must be 0 so the viewer does not rotate the content back upright."""
    pdf = _build_pkt_008(tmp_path)
    reader = PdfReader(str(pdf))
    for i, page in enumerate(reader.pages):
        assert page.get("/Rotate", 0) == 0, f"page {i + 1} /Rotate should be 0"
