"""Test corpus generation for the split pipeline.

Builds PDF packets and a ground-truth manifest that scoring.py / boundary-
benchmark can score.

Design goals (corpus_version 2026-08-12-boundary):
- Varied page sizes across documents (and uniform within a multi-page doc)
  so Layer 1 ``page_dimensions`` can fire — plus uniform packets where it
  correctly must NOT fire between docs.
- Realistic invoice numbers repeated on continuation pages for
  ``shared_doc_number``.
- Same-letterhead / same-vendor runs (the critical BBB case).
- Multi-page invoices with and without ``Page X of N`` markers.
- Non-invoice pages (cover, terms, delivery note) for exclusion.
- A genuine duplicate invoice number in one packet.
- An oversized (>12 page) invoice for the reject path.
- Every vendor name prefixed ``ZZ TEST`` so live-tenant pushes are obvious.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4, letter, legal

# Fixed metadata so regenerating a packet with no content change is byte-identical.
CORPUS_PDF_DATE = "D:20260101000000+00'00'"
CORPUS_PRODUCER = "pdfsplit-corpus"
from reportlab.pdfgen import canvas as rl_canvas

DOC_CLASSES = [
    "invoice",
    "bank_statement",
    "purchase_order",
    "report",
    "letter",
    "form",
    "contract",
    "receipt",
    "memo",
    "cover",
    "terms",
    "delivery_note",
]

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "letter": letter,  # 612 x 792
    "a4": A4,  # 595.27 x 841.89
    "legal": legal,  # 612 x 1008
}

_CLASS_PREFIX = {
    "invoice": "TAX INVOICE",
    "bank_statement": "BANK STATEMENT",
    "purchase_order": "PURCHASE ORDER",
    "report": "MONTHLY REPORT",
    "letter": "LETTER",
    "form": "APPLICATION FORM",
    "contract": "CONTRACT",
    "receipt": "RECEIPT",
    "memo": "MEMO",
    "cover": "COVER PAGE",
    "terms": "TERMS AND CONDITIONS",
    "delivery_note": "DELIVERY NOTE",
}


@dataclass
class PacketSpec:
    packet_id: str
    category: str
    scenario_tags: list[str]
    documents: list[dict]  # enriched doc dicts (see build_corpus)
    pages: int
    notes: str = ""
    blank_pages: list[int] = field(default_factory=list)


@dataclass
class Corpus:
    root: Path
    packets: list[PacketSpec] = field(default_factory=list)

    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def pdf_path(self, packet_id: str) -> Path:
        return self.root / f"{packet_id}.pdf"

    def save_manifest(self) -> Path:
        payload = {
            "schema_version": "1.0",
            "corpus_version": "2026-08-12-boundary",
            "doc_classes": DOC_CLASSES,
            "packets": [
                {
                    "packet_id": p.packet_id,
                    "file": f"{p.packet_id}.pdf",
                    "category": p.category,
                    "scenario_tags": p.scenario_tags,
                    "pages": p.pages,
                    "notes": p.notes,
                    "blank_pages": list(p.blank_pages or []),
                    "expected_documents": [
                        {
                            "doc_id": f"d{i + 1}",
                            "class": d["class"],
                            "start_page": d["start_page"],
                            "end_page": d["end_page"],
                            **{
                                k: d[k]
                                for k in (
                                    "vendor",
                                    "doc_number",
                                    "page_size",
                                    "letterhead",
                                    "page_of_n",
                                    "exclude",
                                    "seller_gstin",
                                    "buyer_gstin",
                                )
                                if k in d
                            },
                        }
                        for i, d in enumerate(p.documents)
                    ],
                }
                for p in self.packets
            ],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path()
        path.write_text(json.dumps(payload, indent=2))
        return path


def _doc_at(spec: PacketSpec, page: int) -> dict[str, Any]:
    for d in spec.documents:
        if d["start_page"] <= page <= d["end_page"]:
            return d
    return {"class": "unknown", "start_page": page, "end_page": page}


def _page_index_in_doc(doc: dict, page: int) -> tuple[int, int]:
    """1-based index within doc and total pages in doc."""
    start = int(doc["start_page"])
    end = int(doc["end_page"])
    return page - start + 1, end - start + 1


def _page_lines(spec: PacketSpec, page: int) -> list[str]:
    doc = _doc_at(spec, page)
    klass = doc.get("class") or "unknown"
    vendor = doc.get("vendor") or "ZZ TEST Generic Vendor"
    letterhead = doc.get("letterhead") or vendor
    doc_number = doc.get("doc_number")
    page_of_n = bool(doc.get("page_of_n"))
    idx, total = _page_index_in_doc(doc, page)
    header = _CLASS_PREFIX.get(klass, "DOCUMENT")

    # Continuation-style: letterhead only on page 1 of the doc.
    cont_style = bool(doc.get("continuation_style"))

    lines: list[str] = []
    if cont_style and idx > 1:
        lines.append(f"(continued) {header} — {letterhead}")
    else:
        lines.append(f"{letterhead}")
        lines.append(f"{header}   |   packet {spec.packet_id}")
        lines.append("=" * 60)

    if klass == "invoice":
        if doc_number:
            # Format matches boundary_constraints._RE_DOC_NUM_LINE.
            lines.append(f"Invoice No: {doc_number}")
        lines.append(f"Vendor: {vendor}")
        seller_gstin = doc.get("seller_gstin")
        if seller_gstin:
            lines.append(f"GSTIN: {seller_gstin}")
        lines.append("Bill To: ZZ TEST Buyer Co")
        buyer_gstin = doc.get("buyer_gstin") or "29AAFCI0214G1ZX"
        if seller_gstin or doc.get("buyer_gstin"):
            lines.append(f"GSTIN: {buyer_gstin}")
        lines.append("Amount due and payment reference details are summarized below.")
        if idx == 1:
            lines.append("Line 1: Widget Alpha (TEST)    Qty 1    Rate 100.00")
            lines.append("Line 2: Service Beta (TEST)    Qty 2    Rate 50.00")
        else:
            lines.append(f"Continuation lines for {doc_number or 'invoice'} page {idx}")
        lines.append("Subtotal: 200.00   Tax: 36.00   Grand Total: INR 236.00")
    elif klass == "cover":
        lines.append(f"COVER — batch for {vendor}")
        lines.append("This cover page is not an invoice.")
    elif klass == "terms":
        lines.append("TERMS AND CONDITIONS")
        lines.append("Payment due within 30 days. Not a tax invoice.")
        lines.append("These pages should be excluded from bill push.")
    elif klass == "delivery_note":
        lines.append(f"Delivery Note for {vendor}")
        if doc_number:
            lines.append(f"Reference: {doc_number}")
        lines.append("Goods dispatched. This is not a tax invoice.")
    else:
        lines.append(f"{header} body for {vendor}")
        lines.append("Amount due and payment reference details are summarized below.")
        if doc_number:
            lines.append(f"Invoice No: {doc_number}")

    if page_of_n:
        lines.append(f"Page {idx} of {total}")
    else:
        lines.append(f"Page {idx}")

    lines.append(f"Ref-{spec.packet_id}-p{page}-{klass}")
    lines.append("SYNTHETIC — DO NOT APPROVE")
    return lines


def _blank_page_lines() -> list[str]:
    return ["", "", ""]


def build_packet_pdf(
    spec: PacketSpec,
    out_path: Path,
    *,
    mode: str = "digital",  # digital | scanned | mixed | blank
    rotate_pages: set[int] | None = None,
    blank_pages: set[int] | None = None,
    low_quality: bool = False,
    page_size: str = "letter",
) -> Path:
    """Build a single packet PDF."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_pages = rotate_pages or set()
    blank_pages = blank_pages or set()

    if mode == "scanned":
        build_scanned_pdf(spec, out_path, rotate_pages, blank_pages, low_quality)
        return out_path
    if mode == "mixed":
        build_mixed_pdf(spec, out_path, rotate_pages, blank_pages, low_quality)
        return out_path

    build_text_pdf(spec, out_path, rotate_pages, blank_pages, default_page_size=page_size)
    return out_path


def _size_for_page(spec: PacketSpec, page: int, default: str = "letter") -> tuple[float, float]:
    doc = _doc_at(spec, page)
    key = str(doc.get("page_size") or default).lower()
    return PAGE_SIZES.get(key, letter)


def _corpus_trailer_id(packet_id: str) -> tuple[bytes, bytes]:
    digest = hashlib.sha256(f"pdfsplit-corpus:{packet_id}".encode("utf-8")).digest()
    return digest[:16], digest[16:32]


def _stabilize_pdf_bytes(data: bytes, packet_id: str) -> bytes:
    """Pin trailer /ID without rewriting PDF objects.

    ReportLab's invariant mode still regenerates a fresh /ID each run. A
    length-preserving patch keeps xref offsets valid. CreationDate / ModDate
    are set via setDateFormatter on the canvas path (and left alone here so
    we never shift byte offsets). pikepdf full rewrite is avoided because it
    reorders dict keys and is not byte-stable across runs.
    """
    import re

    id0, id1 = _corpus_trailer_id(packet_id)
    hex0, hex1 = id0.hex(), id1.hex()

    id_pat = re.compile(rb"(/ID\s*\[\s*)<([0-9a-fA-F]+)>(\s*)<([0-9a-fA-F]+)>(\s*\])")

    def _id_repl(m: re.Match[bytes]) -> bytes:
        h1, h2 = m.group(2), m.group(4)
        if len(h1) != len(hex0) or len(h2) != len(hex1):
            raise ValueError(
                f"PDF /ID width mismatch for {packet_id}: "
                f"{len(h1)}/{len(h2)} vs {len(hex0)}/{len(hex1)}"
            )
        return (
            m.group(1)
            + f"<{hex0}>".encode("ascii")
            + m.group(3)
            + f"<{hex1}>".encode("ascii")
            + m.group(5)
        )

    data2, n = id_pat.subn(_id_repl, data, count=1)
    if n != 1:
        raise ValueError(f"could not patch trailer /ID for {packet_id}")
    if len(data2) != len(data):
        raise ValueError(f"stabilize changed PDF length for {packet_id}")
    return data2


def _stabilize_pdf(path: Path, packet_id: str) -> None:
    path.write_bytes(_stabilize_pdf_bytes(path.read_bytes(), packet_id))


def build_text_pdf(
    spec,
    out_path,
    rotate_pages: set[int],
    blank_pages: set[int],
    default_page_size: str = "letter",
) -> Path:
    """Born-digital PDF; page mediabox follows each document's page_size."""
    buf = io.BytesIO()
    # Seed with first page size; setPageSize before each showPage.
    first = _size_for_page(spec, 1, default_page_size)
    c = rl_canvas.Canvas(buf, pagesize=first)
    c._doc.invariant = 1
    c.setTitle(spec.packet_id)
    c.setProducer(CORPUS_PRODUCER)
    c.setCreator(CORPUS_PRODUCER)
    c.setDateFormatter(lambda *args: CORPUS_PDF_DATE)
    for p in range(1, spec.pages + 1):
        size = _size_for_page(spec, p, default_page_size)
        w, h = size
        c.setPageSize(size)
        lines = _blank_page_lines() if p in blank_pages else _page_lines(spec, p)
        if p in rotate_pages:
            c.saveState()
            c.translate(w / 2, h / 2)
            c.rotate(90)
            c.translate(-w / 2, -h / 2)
            _draw_lines(c, lines, h)
            c.restoreState()
        else:
            _draw_lines(c, lines, h)
        c.showPage()
    c.save()
    _write_bytes(out_path, buf.getvalue())
    if rotate_pages:
        _apply_page_rotation(out_path, rotate_pages)
    _stabilize_pdf(out_path, spec.packet_id)
    return out_path


def _apply_page_rotation(pdf_path: Path, rotate_pages: set[int]) -> None:
    tmp = pdf_path.with_suffix(".rot.tmp.pdf")
    with pikepdf.open(pdf_path) as pdf:
        for p in rotate_pages:
            if 1 <= p <= len(pdf.pages):
                pdf.pages[p - 1].Rotate = 0
        pdf.save(tmp, deterministic_id=True, recompress_flate=False)
    tmp.replace(pdf_path)


def build_scanned_pdf(
    spec, out_path, rotate_pages, blank_pages, low_quality: bool, default_page_size="letter"
) -> Path:
    pdf = pikepdf.Pdf.new()
    for p in range(1, spec.pages + 1):
        w, h = _size_for_page(spec, p, default_page_size)
        lines = _blank_page_lines() if p in blank_pages else _page_lines(spec, p)
        img = _text_to_image_pil(lines, w=w, h=h, low_quality=low_quality)
        if p in rotate_pages:
            img = img.rotate(-90, expand=True)
        _image_to_pdf_page(pdf, img)
    pdf.save(out_path, deterministic_id=True, recompress_flate=False)
    _stabilize_pdf(out_path, spec.packet_id)
    return out_path


def build_mixed_pdf(spec, out_path, rotate_pages, blank_pages, low_quality) -> Path:
    pdf = pikepdf.Pdf.new()
    digital_pages = spec.pages // 2
    for p in range(1, spec.pages + 1):
        w, h = _size_for_page(spec, p, "letter")
        lines = _blank_page_lines() if p in blank_pages else _page_lines(spec, p)
        if p <= digital_pages:
            img = _text_to_image_pil(lines, w=w, h=h, low_quality=False)
        else:
            img = _text_to_image_pil(lines, w=w, h=h, low_quality=low_quality)
        if p in rotate_pages:
            img = img.rotate(-90, expand=True)
        _image_to_pdf_page(pdf, img)
    pdf.save(out_path, deterministic_id=True, recompress_flate=False)
    _stabilize_pdf(out_path, spec.packet_id)
    return out_path


def _draw_lines(c, lines, h):
    c.setFont("Helvetica-Bold", 14)
    y = h - 60
    for i, line in enumerate(lines):
        if i == 0:
            c.drawString(50, y, line[:110])
            y -= 24
        else:
            c.setFont("Helvetica", 11)
            c.drawString(50, y, line[:110])
            y -= 18


def _text_to_image_pil(lines, w, h, low_quality: bool):
    from PIL import ImageFont

    img = Image.new("RGB", (int(w), int(h)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()  # type: ignore[assignment]
    y = 60
    for line in lines:
        draw.text((50, y), line[:110], fill="black", font=font)
        y += 28
    if low_quality:
        img = _degrade(img)
    return img


def _degrade(img):
    img = img.convert("L").convert("RGB")
    img = img.resize((img.width // 2, img.height // 2))
    img = img.resize((img.width * 2, img.height * 2))
    try:
        import numpy as np

        arr = np.array(img)
        noise = np.random.default_rng(0).normal(0, 18, arr.shape)
        arr = np.clip(arr.astype(int) + noise, 0, 255).astype("uint8")
        img = Image.fromarray(arr)
    except ImportError:
        pass
    return img


def _image_to_pdf_page(pdf, img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    from pikepdf import Dictionary, Name, Stream

    stream = Stream(pdf, buf.getvalue())
    stream["/Subtype"] = Name("/Image")
    stream["/Width"] = img.width
    stream["/Height"] = img.height
    stream["/ColorSpace"] = Name("/DeviceRGB")
    stream["/BitsPerComponent"] = 8
    stream["/Filter"] = Name("/DCTDecode")

    page = pdf.add_blank_page(page_size=(img.width, img.height))
    page["/Resources"] = Dictionary({"/XObject": Dictionary({"/Im0": stream})})
    page["/Contents"] = Stream(
        pdf, f"q {img.width} 0 0 {img.height} 0 0 cm /Im0 Do Q".encode("latin-1")
    )
    return page


def _write_bytes(path: Path, data: bytes):
    path.write_bytes(data)


def _inv(
    klass: str,
    start: int,
    end: int,
    *,
    vendor: str,
    doc_number: str | None = None,
    page_size: str = "letter",
    letterhead: str | None = None,
    page_of_n: bool = False,
    continuation_style: bool = False,
    exclude: bool = False,
    seller_gstin: str | None = None,
    buyer_gstin: str | None = None,
) -> dict:
    d: dict[str, Any] = {
        "class": klass,
        "start_page": start,
        "end_page": end,
        "vendor": vendor,
        "page_size": page_size,
        "letterhead": letterhead or vendor,
    }
    if doc_number is not None:
        d["doc_number"] = doc_number
    if page_of_n:
        d["page_of_n"] = True
    if continuation_style:
        d["continuation_style"] = True
    if exclude:
        d["exclude"] = True
    if seller_gstin:
        d["seller_gstin"] = seller_gstin
    if buyer_gstin:
        d["buyer_gstin"] = buyer_gstin
    return d


def build_corpus(root: Path, *, seed: int = 42) -> Corpus:
    """Build the boundary-focused benchmark corpus and return it."""
    del seed  # reserved for future stochastic variants
    corpus = Corpus(root=root)
    packets: list[PacketSpec] = []

    # --- Layer 1: page dimensions ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b01_varied_page_sizes",
            category="born-digital",
            scenario_tags=["page-dimensions", "multi-docs", "varied-sizes"],
            documents=[
                _inv(
                    "invoice", 1, 2,
                    vendor="ZZ TEST SizeCo Alpha",
                    doc_number="INV-2026-8841",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 3, 4,
                    vendor="ZZ TEST SizeCo Beta",
                    doc_number="KFIPL/November20/04",
                    page_size="a4",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 5, 6,
                    vendor="ZZ TEST SizeCo Gamma",
                    doc_number="TX-7726",
                    page_size="legal",
                    page_of_n=True,
                ),
            ],
            pages=6,
            notes="Different page sizes between docs; identical within each. page_dimensions must_split at 3 and 5.",
        )
    )

    packets.append(
        PacketSpec(
            packet_id="pkt_b02_uniform_page_sizes",
            category="born-digital",
            scenario_tags=["page-dimensions", "multi-docs", "uniform-sizes", "negative"],
            documents=[
                _inv(
                    "invoice", 1, 2,
                    vendor="ZZ TEST Uniform One",
                    doc_number="INV-2026-1001",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 3, 4,
                    vendor="ZZ TEST Uniform Two",
                    doc_number="INV-2026-1002",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 5, 6,
                    vendor="ZZ TEST Uniform Three",
                    doc_number="INV-2026-1003",
                    page_size="letter",
                    page_of_n=True,
                ),
            ],
            pages=6,
            notes="All letter size — page_dimensions must NOT fire between docs; doc numbers differ.",
        )
    )

    # --- Critical: same letterhead BBB ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b03_same_letterhead_bbb",
            category="born-digital",
            scenario_tags=["same-letterhead", "same-type-back-to-back", "multi-docs", "critical"],
            documents=[
                _inv(
                    "invoice", 1, 2,
                    vendor="ZZ TEST Acme Supplies Pvt Ltd",
                    doc_number="INV-2026-5501",
                    page_size="letter",
                    letterhead="ZZ TEST Acme Supplies Pvt Ltd",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 3, 4,
                    vendor="ZZ TEST Acme Supplies Pvt Ltd",
                    doc_number="INV-2026-5502",
                    page_size="letter",
                    letterhead="ZZ TEST Acme Supplies Pvt Ltd",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 5, 6,
                    vendor="ZZ TEST Acme Supplies Pvt Ltd",
                    doc_number="INV-2026-5503",
                    page_size="letter",
                    letterhead="ZZ TEST Acme Supplies Pvt Ltd",
                    page_of_n=True,
                ),
            ],
            pages=6,
            notes="Three invoices, one vendor, one letterhead, back to back. Critical BBB case.",
        )
    )

    # --- Multi-page with / without Page X of N ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b04_multipage_markers",
            category="born-digital",
            scenario_tags=["page-of-n", "continuation", "multi-docs"],
            documents=[
                _inv(
                    "invoice", 1, 3,
                    vendor="ZZ TEST Marker Co",
                    doc_number="INV-2026-6601",
                    page_size="letter",
                    page_of_n=True,
                    continuation_style=True,
                ),
                _inv(
                    "invoice", 4, 5,
                    vendor="ZZ TEST NoMarker Co",
                    doc_number="INV-2026-6602",
                    page_size="letter",
                    page_of_n=False,
                ),
            ],
            pages=5,
            notes="Doc1 has Page X of N + shared number; doc2 has neither markers.",
        )
    )

    # --- Non-invoice pages (exclusion) ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b05_non_invoice_pages",
            category="born-digital",
            scenario_tags=["exclusion", "non-invoice", "multi-docs"],
            documents=[
                _inv(
                    "cover", 1, 1,
                    vendor="ZZ TEST Bundle Co",
                    page_size="letter",
                    exclude=True,
                ),
                _inv(
                    "invoice", 2, 3,
                    vendor="ZZ TEST Bundle Co",
                    doc_number="INV-2026-7701",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "terms", 4, 4,
                    vendor="ZZ TEST Bundle Co",
                    page_size="letter",
                    exclude=True,
                ),
                _inv(
                    "delivery_note", 5, 5,
                    vendor="ZZ TEST Bundle Co",
                    doc_number="DN-2026-12",
                    page_size="letter",
                    exclude=True,
                ),
                _inv(
                    "invoice", 6, 7,
                    vendor="ZZ TEST Bundle Co",
                    doc_number="INV-2026-7702",
                    page_size="letter",
                    page_of_n=True,
                ),
            ],
            pages=7,
            notes="Cover/terms/delivery_note for exclusion; two real invoices to push.",
        )
    )

    # --- Genuine duplicate invoice number ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b06_duplicate_invoice_number",
            category="born-digital",
            scenario_tags=["duplicate", "multi-docs"],
            documents=[
                _inv(
                    "invoice", 1, 2,
                    vendor="ZZ TEST Duplo First",
                    doc_number="INV-2026-9999",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 3, 4,
                    vendor="ZZ TEST Duplo Second",
                    doc_number="INV-2026-9999",
                    page_size="letter",
                    page_of_n=True,
                ),
            ],
            pages=4,
            notes="Same invoice number appears twice in one packet (genuine duplicate).",
        )
    )

    # --- Oversized single invoice (>12 pages) ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b07_oversized_invoice_13",
            category="born-digital",
            scenario_tags=["oversized", "single-doc", "page-limit"],
            documents=[
                _inv(
                    "invoice", 1, 13,
                    vendor="ZZ TEST Oversize Industries",
                    doc_number="INV-2026-OVER-13",
                    page_size="letter",
                    page_of_n=True,
                    continuation_style=True,
                ),
            ],
            pages=13,
            notes="Single invoice 13 pages — AIA 12-page reject path.",
        )
    )

    # --- Volume-push packet: multi-invoice, varied sizes, non-invoice, realistic ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b08_volume_push_mix",
            category="born-digital",
            scenario_tags=["volume-push", "multi-docs", "varied-sizes", "exclusion", "same-letterhead"],
            documents=[
                _inv(
                    "cover", 1, 1,
                    vendor="ZZ TEST Volume Vendor",
                    page_size="letter",
                    letterhead="ZZ TEST Volume Vendor",
                    exclude=True,
                ),
                _inv(
                    "invoice", 2, 3,
                    vendor="ZZ TEST Volume Vendor",
                    doc_number="INV-2026-8801",
                    page_size="letter",
                    letterhead="ZZ TEST Volume Vendor",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 4, 5,
                    vendor="ZZ TEST Volume Vendor",
                    doc_number="INV-2026-8802",
                    page_size="a4",
                    letterhead="ZZ TEST Volume Vendor",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 6, 8,
                    vendor="ZZ TEST Volume Vendor",
                    doc_number="KFIPL/November20/88",
                    page_size="letter",
                    letterhead="ZZ TEST Volume Vendor",
                    page_of_n=True,
                    continuation_style=True,
                ),
                _inv(
                    "terms", 9, 9,
                    vendor="ZZ TEST Volume Vendor",
                    page_size="letter",
                    letterhead="ZZ TEST Volume Vendor",
                    exclude=True,
                ),
                _inv(
                    "invoice", 10, 10,
                    vendor="ZZ TEST Other Vendor",
                    doc_number="TX-8803",
                    page_size="legal",
                ),
            ],
            pages=10,
            notes="Packet for live volume push: 4 invoices + cover/terms to exclude.",
        )
    )

    # --- Shared doc number continuation (must_not_split) ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b09_shared_doc_number",
            category="born-digital",
            scenario_tags=["shared-doc-number", "continuation", "single-doc"],
            documents=[
                _inv(
                    "invoice", 1, 4,
                    vendor="ZZ TEST Cont Corp",
                    doc_number="INV-2026-4410",
                    page_size="letter",
                    page_of_n=True,
                    continuation_style=True,
                ),
            ],
            pages=4,
            notes="One invoice, shared number + Page X of N on all pages — must_not_split inside.",
        )
    )

    # --- Group companies: same letterhead brand, different seller GSTIN ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b10_group_letterhead_diff_gstin",
            category="born-digital",
            scenario_tags=[
                "group-companies",
                "same-letterhead",
                "seller-gstin",
                "multi-docs",
                "critical",
            ],
            documents=[
                _inv(
                    "invoice", 1, 2,
                    vendor="ZZ TEST IBC Galaxy Developers Ltd",
                    doc_number="INV-2026-7701",
                    page_size="letter",
                    letterhead="IBC Group",
                    page_of_n=True,
                    seller_gstin="29AAACC9836F1ZG",
                ),
                _inv(
                    "invoice", 3, 4,
                    vendor="ZZ TEST IBC Century Holdings Ltd",
                    doc_number="INV-2026-7702",
                    page_size="letter",
                    letterhead="IBC Group",
                    page_of_n=True,
                    seller_gstin="29AABCC1234D1Z5",
                ),
            ],
            pages=4,
            notes=(
                "Two group companies on one letterhead brand with different "
                "seller GSTINs — must stay two vouchers (silent-merge case)."
            ),
        )
    )

    # --- Blank separator sheets between bills ---
    packets.append(
        PacketSpec(
            packet_id="pkt_b11_blank_separators",
            category="born-digital",
            scenario_tags=["blank-separator", "auto-exclude", "multi-docs"],
            documents=[
                _inv(
                    "invoice", 1, 1,
                    vendor="ZZ TEST BlankSep Alpha",
                    doc_number="INV-2026-8101",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 3, 3,
                    vendor="ZZ TEST BlankSep Beta",
                    doc_number="INV-2026-8102",
                    page_size="letter",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 5, 5,
                    vendor="ZZ TEST BlankSep Gamma",
                    doc_number="INV-2026-8103",
                    page_size="letter",
                    page_of_n=True,
                ),
            ],
            pages=5,
            blank_pages=[2, 4],
            notes=(
                "Blank sheets between three one-page invoices. Blanks must "
                "auto-exclude; docs ∪ excluded cover every page."
            ),
        )
    )

    # --- Forced under-split: two invoices, different bill numbers ---
    # Truth is two docs. The badge test forces a single 1-2 segment to prove
    # "different bill numbers held together" fires (the Tally wrong-voucher
    # case). Layer-1 correctly splits this packet; the fixture exists so the
    # hold rule is exercised, not to expect the model to under-split.
    packets.append(
        PacketSpec(
            packet_id="pkt_b12_undersplit_diff_bill_numbers",
            category="born-digital",
            scenario_tags=[
                "under-split",
                "different-bill-numbers",
                "review-badge",
                "multi-docs",
                "critical",
            ],
            documents=[
                _inv(
                    "invoice", 1, 1,
                    vendor="ZZ TEST Undersplit Co",
                    doc_number="INV-2026-9101",
                    page_size="letter",
                    letterhead="ZZ TEST Undersplit Co",
                    page_of_n=True,
                ),
                _inv(
                    "invoice", 2, 2,
                    vendor="ZZ TEST Undersplit Co",
                    doc_number="INV-2026-9102",
                    page_size="letter",
                    letterhead="ZZ TEST Undersplit Co",
                    page_of_n=True,
                ),
            ],
            pages=2,
            notes=(
                "Two one-page invoices, same vendor, different bill numbers. "
                "Truth: two docs. Badge test forces a 1-2 hold to exercise "
                "the different-bill-numbers rule."
            ),
        )
    )

    corpus.packets = packets

    for spec in corpus.packets:
        out = corpus.pdf_path(spec.packet_id)
        build_packet_pdf(
            spec,
            out,
            mode="digital",
            blank_pages=set(spec.blank_pages or []),
        )

    corpus.save_manifest()
    return corpus
