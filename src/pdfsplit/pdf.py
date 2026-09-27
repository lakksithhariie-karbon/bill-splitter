"""Physical PDF splitting + integrity verification.

We own the physical split (Google is the intelligence layer only). Uses pikepdf
(qpdf engine — the same engine Google's Toolbox uses) for lossless page
selection. pypdf is used only as a read-side fallback for verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from pdfsplit.schema import SplitDocument


@dataclass
class SplitFile:
    doc: SplitDocument
    path: Path


def _filename_slug(name: str) -> str:
    """Sanitize a display name / doc_type for use in a PDF filename."""
    slug = re.sub(r"[^\w\-]+", "_", (name or "document").strip(), flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:80] or "document"


def split_pdf_by_ranges(
    src: Path,
    documents: list[SplitDocument],
    out_dir: Path,
    total_pages: int | None = None,
    excluded_pages: list[int] | None = None,
    display_names: dict[tuple[int, int], str] | None = None,
    use_display_names_as_filenames: bool = False,
) -> list[SplitFile]:
    """Physically split `src` into one PDF per logical document.

    Page numbers in `documents` are 1-indexed inclusive. Excluded pages appear
    in no output PDF. Output files are named
    ``<stem>__p{start:04d}-{end:04d}__{slug}.pdf`` where slug prefers the
    reviewer display name when provided, else ``doc_type``. When
    ``use_display_names_as_filenames`` is true, use the reviewer name as the
    complete PDF filename instead.

    Invariant: documents ∪ excluded_pages cover pages 1..N exactly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if not documents and not (excluded_pages or []):
        raise ValueError("No documents to split.")

    total_pages = total_pages or _page_count(src)
    _validate_ranges(documents, total_pages, excluded_pages)

    src_path = Path(src)
    stem = src_path.stem
    results: list[SplitFile] = []
    used_names: set[str] = set()

    with pikepdf.open(src_path) as pdf:
        for doc in documents:
            # pikepdf select() is zero-based and inclusive of end index.
            zero_start = doc.start_page - 1
            zero_end = doc.end_page - 1
            new_pdf = pikepdf.Pdf.new()
            new_pdf.pages.extend(pdf.pages[i] for i in range(zero_start, zero_end + 1))
            key = (doc.start_page, doc.end_page)
            raw_name = (display_names or {}).get(key) or doc.doc_type
            if use_display_names_as_filenames:
                raw_name = re.sub(r"\.pdf$", "", raw_name.strip(), flags=re.I)
            slug = _filename_slug(raw_name)
            if use_display_names_as_filenames:
                base_slug = slug
                suffix = 2
                while f"{slug}.pdf".casefold() in used_names:
                    slug = f"{base_slug[: 80 - len(str(suffix)) - 1]}-{suffix}"
                    suffix += 1
                out = out_dir / f"{slug}.pdf"
                used_names.add(out.name.casefold())
            else:
                out = (
                    out_dir
                    / f"{stem}__p{doc.start_page:04d}-{doc.end_page:04d}__{slug}.pdf"
                )
            new_pdf.save(out)
            results.append(SplitFile(doc=doc, path=out))

    return results


def _page_count(pdf_path: Path) -> int:
    with pikepdf.open(pdf_path) as pdf:
        return len(pdf.pages)


def _validate_ranges(
    documents: list[SplitDocument],
    total_pages: int,
    excluded_pages: list[int] | None = None,
) -> None:
    """Validate documents ∪ excluded cover pages 1..N exactly.

    Documents must be non-overlapping, each a contiguous inclusive range, and
    must not include any excluded page. Gaps between documents are allowed only
    when every gap page is listed in ``excluded_pages``.
    """
    excluded = sorted({int(p) for p in (excluded_pages or [])})
    for p in excluded:
        if p < 1 or p > total_pages:
            raise ValueError(
                f"Excluded page {p} invalid for {total_pages}-page PDF."
            )
    excluded_set = set(excluded)

    for d in documents:
        if d.start_page < 1 or d.end_page > total_pages or d.start_page > d.end_page:
            raise ValueError(
                f"Range {d.start_page}-{d.end_page} invalid for {total_pages}-page PDF "
                f"(doc '{d.doc_type}')."
            )
        for p in range(d.start_page, d.end_page + 1):
            if p in excluded_set:
                raise ValueError(
                    f"Document '{d.doc_type}' range {d.start_page}-{d.end_page} "
                    f"includes excluded page {p}."
                )

    ordered = sorted(documents, key=lambda d: d.start_page)
    covered: set[int] = set(excluded_set)
    prev_end = 0
    for d in ordered:
        if d.start_page <= prev_end:
            raise ValueError(
                f"Ranges overlap: doc '{d.doc_type}' starts at {d.start_page} "
                f"but previous ended at {prev_end}."
            )
        for p in range(prev_end + 1, d.start_page):
            if p not in excluded_set:
                raise ValueError(
                    f"Page {p} is neither in a document nor excluded "
                    f"(gap before doc '{d.doc_type}' at {d.start_page})."
                )
        for p in range(d.start_page, d.end_page + 1):
            if p in covered:
                raise ValueError(f"Page {p} covered more than once.")
            covered.add(p)
        prev_end = d.end_page

    for p in range(prev_end + 1, total_pages + 1):
        if p not in excluded_set:
            raise ValueError(
                f"Page {p} is neither in a document nor excluded "
                f"(coverage ended at {prev_end}, PDF has {total_pages} pages)."
            )
        covered.add(p)

    if covered != set(range(1, total_pages + 1)):
        missing = sorted(set(range(1, total_pages + 1)) - covered)
        raise ValueError(
            f"documents ∪ excluded must cover pages 1..{total_pages}; "
            f"missing {missing}."
        )


def verify_split(src: Path, splits: list[SplitFile]) -> list[str]:
    """Verify each split: page count, contiguity, and page-order integrity.

    Returns a list of problem strings (empty == all good).
    """
    problems: list[str] = []
    src_path = Path(src)
    src_text = _page_texts(src_path)

    for s in splits:
        expected = s.doc.end_page - s.doc.start_page + 1
        with pikepdf.open(s.path) as pdf:
            actual = len(pdf.pages)
        if actual != expected:
            problems.append(f"{s.path.name}: expected {expected} pages, got {actual}")
            continue
        # Spot-check text of first and last page against the source.
        sub_text = _page_texts(s.path)
        if src_text:
            first_ok = _text_equiv(src_text[s.doc.start_page - 1], sub_text[0])
            last_ok = _text_equiv(src_text[s.doc.end_page - 1], sub_text[-1])
            if not (first_ok and last_ok):
                problems.append(f"{s.path.name}: page text mismatch vs source")
    return problems


def _page_texts(pdf_path: Path) -> list[str]:
    """Return per-page extracted text. Uses pypdf for read-only verification."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    texts: list[str] = []
    for page in reader.pages:
        try:
            texts.append((page.extract_text() or "").strip())
        except Exception:  # noqa: BLE001 - text extraction is best-effort
            texts.append("")
    return texts


def _text_equiv(a: str, b: str) -> bool:
    """Two pages match if the smaller text is contained in the larger (OCR/whitespace tolerant)."""
    na, nb = a.replace(" ", "").replace("\n", ""), b.replace(" ", "").replace("\n", "")
    if not na or not nb:
        return True  # can't verify empty text; skip
    return na in nb or nb in na
