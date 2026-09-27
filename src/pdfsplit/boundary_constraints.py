"""Deterministic boundary constraints (Layer 1).

High-precision, partial-recall signals that the LLM judge must not violate.
Measured independently of the model (page dimensions from OCR / PDF; shared
invoice numbers and Page X of N from page text).

MUST SPLIT at page P (P > 1):
  - page dimensions differ from page P-1
  - page P carries a \"Page 1 of N\" marker
  - pages P-1 and P carry **different own** document numbers
    (header + labelled; never PO / against / delivery refs)
  - pages P-1 and P carry **different seller GSTINs** (group companies on
    one letterhead still have distinct GSTINs)
  - page P-1 has a closing totals block AND page P is a real document
    start: header/title AND transactional content (``document_complete``)

MUST NOT SPLIT at page P (P > 1):
  - pages P-1 and P share a detectable document number
  - page P carries a continuation marker (\"Page 2 of 3\", etc.)
  - an earlier page declared extent through P (``declared_extent``),
    from a footer / bottom-of-page phrase only
  - page P reprints a letterhead above a signature / declaration / terms
    closing with no trades or totals (``closing_page``)

Conflict precedence (explicit — see ``SIGNAL_RANK`` / ``_resolve_constraint_conflicts``):
  When must_split and must_not_split both fire on the same page, the higher
  ``SIGNAL_RANK`` wins. ``declared_extent`` outranks every must_split — a
  document stating its own last page beats anything we infer.
  ``continuation_marker`` outranks remaining must_splits.
  ``shared_doc_number`` outranks ``document_complete`` (same invoice id is
  continuity). A wrong hard split is worse than a missed one; ties keep
  must_not_split.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


ConstraintKind = Literal["must_split", "must_not_split"]

#: Higher wins when must_split and must_not_split conflict on one page.
#: declared_extent outranks every inference (the document named its last page).
#: continuation_marker outranks remaining must_splits.
#: shared_doc_number outranks document_complete (same invoice id → continuation).
SIGNAL_RANK: dict[str, int] = {
    "declared_extent": 110,
    "continuation_marker": 100,
    "page_dimensions": 90,
    "page_1_of_n": 85,
    "different_seller_gstin": 75,
    "different_doc_number": 70,
    "shared_doc_number": 60,
    "closing_page": 58,
    "document_complete": 55,
    "signature_block": 20,
}

#: Header band for block-based OCR (same units as Mistral top_left_y).
HEADER_REGION_Y = 200
#: When blocks lack y-positions, treat the first N non-empty markdown lines
#: as the header (letterhead + invoice identity live here on real bills).
HEADER_LINE_CAP = 12
#: Footer band for declared_extent — lowest this fraction of page height.
#: Highest-precedence signal; body boilerplate ("continued on next page"
#: in T&C prose) must never fire it.
FOOTER_REGION_FRAC = 0.22
#: Markdown-only pages (no y-positioned blocks): last this many lines.
FOOTER_LINE_CAP = 6


@dataclass(frozen=True)
class BoundaryConstraint:
    """Constraint on whether ``page`` (1-indexed) may start a new document."""

    page: int
    kind: ConstraintKind
    signal: str
    detail: str


@dataclass
class BoundaryDecision:
    """Evidence attached to one predicted document segment."""

    page_start: int
    page_end: int
    doc_type: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)


_RE_PAGE_OF = re.compile(
    r"\bpage\s*(\d+)\s*(?:of|/)\s*(\d+)\b",
    re.IGNORECASE,
)
#: Document-type words. Full-page scan allows a missing "No" after the type
#: ("Tax Invoice INV-1"); own-id and naming still require No/Number/#.
_RE_DOC_TYPE = (
    r"(?:"
    r"(?:tax\s*)?invoice|"
    r"bill|"
    r"challan|"
    r"voucher|"
    r"document|"
    r"debit\s*note|"
    r"credit\s*note|"
    r"contract\s*note"
    r")"
)
#: Labelled own-id — No/Number/# required. Ref No is an own-id, not a PO.
_RE_DOC_LABEL = (
    r"(?:"
    + _RE_DOC_TYPE
    + r"\s*(?:no\.?|number|#|num)"
    r"|ref(?:erence)?\s*(?:no\.?|number|#)"
    r")"
)
#: Full-page scan (shared_doc_number / legacy helpers) — broader.
_RE_DOC_NUM_LINE = re.compile(
    r"(?:"
    + _RE_DOC_TYPE
    + r"\s*(?:no\.?|number|#|num|:)?\s*[:.#]?"
    r"|ref(?:erence)?\s*(?:no\.?|number|#)\s*[:.#]?"
    r")"
    r"\s*"
    r"([A-Z0-9][A-Z0-9\-/]*\d[A-Z0-9\-/]*)",
    re.IGNORECASE,
)
_RE_DOC_NUM_BARE = re.compile(
    r"\b((?:INV|DN|CN|PO)[-/]?\d{2,}[A-Z0-9\-/]*)\b",
    re.IGNORECASE,
)
#: Own-identifier labels only (never PO / order / delivery / against).
_RE_OWN_DOC_NUM = re.compile(
    _RE_DOC_LABEL
    + r"\s*[:.#]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]*\d[A-Z0-9\-/]*)",
    re.IGNORECASE,
)
#: Lines that mention other documents — do not treat as this page's own id.
_RE_FOREIGN_DOC_LABEL = re.compile(
    r"(?:"
    r"\b(?:p\.?\s*o\.?|purchase\s*order|order\s*(?:no\.?|number|#)|"
    r"delivery\s*(?:note|no\.?|number|#)|"
    r"against|your\s*ref(?:erence)?|"
    r"our\s*order|client\s*code|"
    r"in\s*response\s*to|referring\s*to)\b"
    r")",
    re.IGNORECASE,
)
_RE_GSTIN_TOKEN = re.compile(
    r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$",
    re.IGNORECASE,
)
#: GSTIN anywhere in text (India tax invoices).
_RE_GSTIN_FIND = re.compile(
    r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b",
    re.IGNORECASE,
)
#: Buyer / recipient block — GSTINs under these labels are not the seller.
#: Horizontal whitespace only between "invoice" and "to" so a title line
#: "TAX INVOICE" followed by "To," does not count as "Invoice To".
_RE_BUYER_BLOCK = re.compile(
    r"(?:"
    r"\b(?:bill|billed)[^\S\n]*to\b|"
    r"\binvoice[^\S\n]+to\b|"
    r"(?:^|\n)\s*to\s*,|"
    r"\bbuyer\b|"
    r"\bcustomer\b|"
    r"\bconsignee\b|"
    r"\bship[^\S\n]*to\b|"
    r"\bpurchaser\b|"
    r"\bplace\s*of\s*supply\b"
    r")",
    re.IGNORECASE,
)
_RE_PAN_TOKEN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.IGNORECASE)
_RE_CIN_TOKEN = re.compile(
    r"^[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$", re.IGNORECASE
)
_RE_CLOSING = re.compile(
    r"(?:"
    r"yours\s+faithfully|"
    r"yours\s+sincerely|"
    r"authori[sz]ed\s+signator|"
    r"\bdeclaration\b|"
    r"terms\s+and\s+conditions|"
    r"terms\s+of\s+payment|"
    r"computer\s+generated|"
    r"signature\s+not\s+required"
    r")",
    re.IGNORECASE,
)
_LINE_ITEM_HEADER_HINTS = (
    "description",
    "particulars",
    "scrip",
    "qty",
    "quantity",
    "hsn",
    "sac",
    "rate",
    "amount",
    "taxable",
    "brokerage",
    "charges",
    "cgst",
    "sgst",
    "igst",
)
#: Closing totals — prefer explicit end-of-invoice language over bare "total".
_RE_TOTALS_BLOCK = re.compile(
    r"(?:"
    r"\bgrand\s*total\b|"
    r"\bamount\s*(?:due|payable)\b|"
    r"\btotal\s*(?:due|payable|amount)\b|"
    r"\bnet\s*(?:amount|payable|total)\b|"
    r"\bbalance\s*due\b|"
    r"\btotal\s*:"
    r")",
    re.IGNORECASE,
)
_RE_MONEYISH = re.compile(
    r"(?:[$€£₹]|USD|INR|EUR|GBP)?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?"
)
#: Extent phrases — a page naming its own last page (range, not adjacent-pair).
_RE_EXTENT_TO_PAGE = re.compile(
    r"\bcont(?:inued|['’.]?d)\b\s+(?:to|on)\s+(?:the\s+)?page(?:\s+number)?\s+(\d+)",
    re.IGNORECASE,
)
_RE_EXTENT_NEXT_PAGE = re.compile(
    r"\bcont(?:inued|['’.]?d)\b\s+on\s+(?:the\s+)?next\s+page\b",
    re.IGNORECASE,
)
_RE_EXTENT_OF_N_PAGES = re.compile(
    r"\bof\s+(\d+)\s+pages\b",
    re.IGNORECASE,
)


def page_dimensions(page: dict) -> tuple[int, int] | None:
    """Return (width, height) ints from OCR dimensions, or None."""
    dims = page.get("dimensions") or {}
    try:
        w = int(round(float(dims["width"])))
        h = int(round(float(dims["height"])))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (w, h)


def page_header_text(page: dict) -> str:
    """Letterhead / identity band only — never the line-item body.

    Prefer OCR blocks in the top region, always unioned with the first
    ``HEADER_LINE_CAP`` non-empty markdown lines so a title-only block
    does not drop the Invoice No. row that sits just below the letterhead.
    """
    chunks: list[str] = []
    for block in page.get("blocks") or []:
        try:
            y = float(block.get("top_left_y", 9999) or 9999)
        except (TypeError, ValueError):
            y = 9999.0
        if y < HEADER_REGION_Y:
            content = (block.get("content") or "").strip()
            if content:
                chunks.append(content)
    lines = [
        ln.strip()
        for ln in (page.get("markdown") or "").splitlines()
        if ln.strip()
    ]
    md_header = "\n".join(lines[:HEADER_LINE_CAP])
    if chunks and md_header:
        return "\n".join(chunks) + "\n" + md_header
    return "\n".join(chunks) if chunks else md_header


def extract_seller_gstin(page: dict | str | None) -> str | None:
    """Supplier GSTIN on this page, or None when missing / only buyer GSTIN.

    India tax invoices carry both seller and buyer GSTINs. The buyer often
    repeats across a whole packet (one company receiving many bills), so a
    bare "any GSTIN differs" signal would false-split. We take GSTINs that
    appear *before* a Bill-To / Buyer block; when the seller GSTIN only
    appears below the buyer block (common on Indian formats), we take the
    *last* GSTIN if there are two or more.
    """
    if page is None:
        return None
    md = page if isinstance(page, str) else (page.get("markdown") or "")
    if not md:
        return None
    buyer_at: int | None = None
    for m in _RE_BUYER_BLOCK.finditer(md):
        buyer_at = m.start()
        break
    seller_hits: list[str] = []
    after_hits: list[str] = []
    for m in _RE_GSTIN_FIND.finditer(md):
        token = m.group(1).upper()
        if buyer_at is not None and m.start() >= buyer_at:
            after_hits.append(token)
        else:
            seller_hits.append(token)
    if seller_hits:
        return seller_hits[0]
    if buyer_at is not None:
        # Seller block often sits under PAN / bank details after Bill To.
        uniq = list(dict.fromkeys(after_hits))
        if len(uniq) >= 2:
            return uniq[-1]
        return None
    all_hits = [m.group(1).upper() for m in _RE_GSTIN_FIND.finditer(md)]
    if not all_hits:
        return None
    return all_hits[0]


def extract_doc_number_token(markdown: str) -> str | None:
    """Normalised document-number token from page markdown, if any."""
    text = markdown or ""
    m = _RE_DOC_NUM_LINE.search(text)
    if m:
        token = (m.group(1) or "").strip().upper().rstrip(".,;")
        if token and not _looks_like_statutory_id(token):
            return token
    m = _RE_DOC_NUM_BARE.search(text)
    if m:
        token = (m.group(1) or "").strip().upper().rstrip(".,;")
        if token and not _looks_like_statutory_id(token):
            return token
    return None


def extract_own_doc_number(page: dict) -> str | None:
    """This page's own document id — or None when unsure.

    Fires only when the token is:
      - in the header region (not the body),
      - adjacent to an invoice / bill / contract-note / voucher label,
      - not on a line that is a PO / order / delivery / against / client code,
      - not a GSTIN / PAN / CIN.

    A page with no detectable own number must never produce a constraint.
    """
    header = page_header_text(page)
    if not header:
        return None
    for ln in header.splitlines():
        if not ln.strip():
            continue
        if _RE_FOREIGN_DOC_LABEL.search(ln):
            continue
        m = _RE_OWN_DOC_NUM.search(ln)
        if not m:
            continue
        token = (m.group(1) or "").strip().upper().rstrip(".,;")
        if token and not _looks_like_statutory_id(token):
            return token
    return None


def page_footer_text(page: dict) -> str:
    """Footer / bottom-of-page text only — never the body.

    Includes:
      - blocks typed ``footer``
      - any block whose top_left_y sits in the lowest
        ``FOOTER_REGION_FRAC`` of the page
      - the page-level ``footer`` string when present

    When the page has y-positioned blocks, body markdown is ignored —
    a T&C clause in the middle of the page must not trigger
    ``declared_extent``. Markdown last-lines are a fallback only for
    pages with no geometry at all.
    """
    chunks: list[str] = []
    dims = page_dimensions(page)
    height = float(dims[1]) if dims else None
    y_min = (
        height * (1.0 - FOOTER_REGION_FRAC) if height and height > 0 else None
    )
    has_geometry = False
    for block in page.get("blocks") or []:
        content = (block.get("content") or "").strip()
        btype = (block.get("type") or "").lower()
        try:
            y = float(block.get("top_left_y", 9999) or 9999)
            has_geometry = True
        except (TypeError, ValueError):
            y = 9999.0
        if not content:
            continue
        if btype == "footer":
            chunks.append(content)
            continue
        if y_min is not None and y >= y_min:
            chunks.append(content)
    page_footer = page.get("footer")
    if isinstance(page_footer, str) and page_footer.strip():
        chunks.append(page_footer.strip())
    elif isinstance(page_footer, dict):
        extra = (page_footer.get("content") or "").strip()
        if extra:
            chunks.append(extra)
    if has_geometry or chunks:
        return "\n".join(chunks)
    lines = [
        ln.strip()
        for ln in (page.get("markdown") or "").splitlines()
        if ln.strip()
    ]
    if not lines:
        return ""
    start = max(0, len(lines) - FOOTER_LINE_CAP)
    return "\n".join(lines[start:])


def parse_declared_extent(
    markdown: str, page_no: int, n_pages: int
) -> list[tuple[int, str]]:
    """Return (N, how) declarations in ``markdown``. N is 1-indexed last page.

    Only yields when N is inside the packet and N > page_no. Not an
    adjacent-pair rule — the caller holds every boundary from page_no+1
    through N. Callers that have a page dict must pass
    ``page_footer_text(page)``, not the full page markdown.
    """
    text = markdown or ""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()

    def _accept(n: int, how: str) -> None:
        if n <= page_no or n > n_pages or n in seen:
            return
        seen.add(n)
        out.append((n, how))

    for m in _RE_EXTENT_TO_PAGE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        _accept(n, f"continued to page {n}")
    if _RE_EXTENT_NEXT_PAGE.search(text):
        _accept(page_no + 1, "continued on next page")
    for m in _RE_EXTENT_OF_N_PAGES.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        _accept(n, f"of {n} pages")
    return out


def declared_extent_constraints(
    pages: list[dict],
) -> list[BoundaryConstraint]:
    """Range holds: page P naming last page N → must_not_split on P+1..N.

    Phrases are read from the footer / bottom region only. Body prose
    (T&C "continued on next page") is silent.
    """
    n_pages = len(pages)
    out: list[BoundaryConstraint] = []
    seen: set[tuple[int, int]] = set()
    for i, page in enumerate(pages):
        page_no = i + 1
        md = page_footer_text(page)
        for last, how in parse_declared_extent(md, page_no, n_pages):
            for hold in range(page_no + 1, last + 1):
                key = (hold, last)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    BoundaryConstraint(
                        page=hold,
                        kind="must_not_split",
                        signal="declared_extent",
                        detail=(
                            f"page {page_no} declares extent through "
                            f"page {last} ({how})"
                        ),
                    )
                )
    return out


def extract_page_of_n(markdown: str) -> tuple[int, int] | None:
    """Return (page_index, total) from a Page X of N marker, if present."""
    m = _RE_PAGE_OF.search(markdown or "")
    if not m:
        return None
    try:
        cur, total = int(m.group(1)), int(m.group(2))
    except (TypeError, ValueError):
        return None
    if cur < 1 or total < 1 or cur > total:
        return None
    return (cur, total)


def page_has_signature_block(page: dict) -> bool:
    for block in page.get("blocks") or []:
        if (block.get("type") or "").lower() == "signature":
            return True
    return False


def page_has_totals_block(markdown: str) -> bool:
    """True when the page tail looks like a closing invoice totals block.

    Scans the last third of non-empty lines. Requires a totals label plus a
    money-like amount — bare \"Total Qty\" in a table does not count.
    Ambiguous → False (no constraint).
    """
    lines = [ln.strip() for ln in (markdown or "").splitlines() if ln.strip()]
    if not lines:
        return False
    start = max(0, (len(lines) * 2) // 3)
    for ln in reversed(lines[start:]):
        if _RE_TOTALS_BLOCK.search(ln) and _RE_MONEYISH.search(ln):
            return True
    return False


def page_has_header_identity(page: dict) -> bool:
    """True when the page opens with a letterhead / title identity block."""
    for block in page.get("blocks") or []:
        btype = (block.get("type") or "").lower()
        try:
            y = float(block.get("top_left_y", 9999) or 9999)
        except (TypeError, ValueError):
            y = 9999.0
        if y < HEADER_REGION_Y and btype in {"title", "header"} and (
            block.get("content") or ""
        ).strip():
            return True
    header = page_header_text(page)
    if not header:
        return False
    # First non-empty line with real letters = letterhead-ish opener.
    first = next((ln for ln in header.splitlines() if ln.strip()), "")
    letters = sum(1 for ch in first if ch.isalpha())
    return letters >= 3


def _looks_like_statutory_id(token: str) -> bool:
    compact = (token or "").replace(" ", "").upper()
    if not compact:
        return False
    return bool(
        _RE_GSTIN_TOKEN.match(compact)
        or _RE_PAN_TOKEN.match(compact)
        or _RE_CIN_TOKEN.match(compact)
    )


def _markdown_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(not c or set(c) <= {"-", ":"} for c in cells)


def _line_item_data_row_count(markdown: str) -> int:
    """Goods / charges rows under a line-item header. Empty chrome is 0."""
    count = 0
    in_item_table = False
    for ln in (markdown or "").splitlines():
        stripped = ln.strip()
        if not stripped.startswith("|"):
            in_item_table = False
            continue
        cells = _markdown_cells(stripped)
        joined = " ".join(cells)
        if _is_table_separator(cells):
            continue
        headerish = sum(
            1 for hint in _LINE_ITEM_HEADER_HINTS if hint in joined.lower()
        )
        if headerish >= 2:
            in_item_table = True
            continue
        if not in_item_table:
            continue
        if _RE_CLOSING.search(joined):
            in_item_table = False
            continue
        # Tax-rate chrome under the header ("0.00(%)") is not a line item.
        if re.search(r"0\.00\s*\(%\)", joined) and not re.search(
            r"[A-Za-z]{3,}", joined
        ):
            continue
        money_n = len(_RE_MONEYISH.findall(joined))
        has_word = bool(re.search(r"[A-Za-z]{3,}", joined))
        has_digit = bool(re.search(r"\d", joined))
        if money_n >= 2 or (has_digit and has_word):
            count += 1
    return count


def page_has_transactional_content(page: dict) -> bool:
    """True when the page has line items, a charges table, or its own totals.

    A reprinted letterhead above empty table headers is not a document start.
    """
    md = page.get("markdown") or ""
    if page_has_totals_block(md):
        return True
    return _line_item_data_row_count(md) >= 1


#: A standalone cover/terms/delivery-note document, not a reprinted closer.
_RE_STANDALONE_EXCLUSION_TITLE = re.compile(
    r"(?:"
    r"\bterms\s+and\s+conditions\b|"
    r"\bcover\s*page\b|"
    r"\bdelivery\s+note\b"
    r")",
    re.IGNORECASE,
)


def page_is_standalone_exclusion(page: dict) -> bool:
    """True when this page IS a terms/cover/DN document, not a signature closer."""
    for block in page.get("blocks") or []:
        if (block.get("type") or "").lower() in {"title", "header"}:
            if _RE_STANDALONE_EXCLUSION_TITLE.search(block.get("content") or ""):
                return True
    header = page_header_text(page)
    for ln in header.splitlines()[:8]:
        if _RE_STANDALONE_EXCLUSION_TITLE.search(ln):
            return True
    return False


def page_is_closing_continuation(page: dict) -> bool:
    """Letterhead reprinted above a signature / terms closing — not a new doc."""
    if page_has_transactional_content(page):
        return False
    if page_is_standalone_exclusion(page):
        return False
    if not page_has_header_identity(page):
        return False
    if page_has_signature_block(page):
        return True
    md = page.get("markdown") or ""
    return bool(_RE_CLOSING.search(md))


def collect_boundary_constraints(
    pages: list[dict],
    *,
    use_signature: bool = False,
) -> list[BoundaryConstraint]:
    """Every Layer-1 constraint that evaluated true, before rank resolution.

    A hold and a split on the same page can both be in this list. Enforcement
    uses ``compute_boundary_constraints`` (resolved). The trace prints this
    full list so a suppressed ``shared_doc_number`` is visible.
    """
    out: list[BoundaryConstraint] = []
    if len(pages) < 2:
        return out

    out.extend(declared_extent_constraints(pages))

    prev_dims = page_dimensions(pages[0])
    prev_md = pages[0].get("markdown") or ""
    prev_num = extract_doc_number_token(prev_md)
    prev_own = extract_own_doc_number(pages[0])
    prev_gstin = extract_seller_gstin(pages[0])
    prev_complete = page_has_totals_block(prev_md)

    for i in range(1, len(pages)):
        page_no = i + 1  # 1-indexed start-page candidate
        md = pages[i].get("markdown") or ""
        dims = page_dimensions(pages[i])
        num = extract_doc_number_token(md)
        own = extract_own_doc_number(pages[i])
        gstin = extract_seller_gstin(pages[i])
        page_of = extract_page_of_n(md)
        has_header = page_has_header_identity(pages[i])

        if prev_dims is not None and dims is not None and dims != prev_dims:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="page_dimensions",
                    detail=(
                        f"page size changed "
                        f"{prev_dims[0]}×{prev_dims[1]} → {dims[0]}×{dims[1]}"
                    ),
                )
            )

        if page_of is not None and page_of[0] == 1:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="page_1_of_n",
                    detail=f"Page 1 of {page_of[1]} marker",
                )
            )

        if page_of is not None and page_of[0] > 1:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_not_split",
                    signal="continuation_marker",
                    detail=f"Page {page_of[0]} of {page_of[1]} continuation",
                )
            )

        if prev_num and num and prev_num == num:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_not_split",
                    signal="shared_doc_number",
                    detail=f"both pages carry {num}",
                )
            )

        # Same vendor / letterhead with different own invoice numbers → split.
        # Only when BOTH sides clear the own-identifier bar; silence otherwise.
        if prev_own and own and prev_own != own:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="different_doc_number",
                    detail=(
                        f"different invoice numbers at p{page_no} "
                        f"({prev_own} → {own})"
                    ),
                )
            )

        # Group companies on one template: same letterhead look, different
        # seller GSTIN → different legal suppliers → must split.
        if prev_gstin and gstin and prev_gstin != gstin:
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="different_seller_gstin",
                    detail=(
                        f"different seller GSTIN at p{page_no} "
                        f"({prev_gstin} → {gstin})"
                    ),
                )
            )

        # Prev page closed with a totals block + this page is a REAL document
        # start (header AND trades/charges/totals). A reprinted letterhead
        # above a signature block is a continuation, not a new document.
        if (
            prev_complete
            and has_header
            and page_has_transactional_content(pages[i])
            and not (page_of is not None and page_of[0] > 1)
        ):
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="document_complete",
                    detail=(
                        f"page {page_no - 1} has closing totals and "
                        f"page {page_no} opens a new document"
                    ),
                )
            )

        if page_is_closing_continuation(pages[i]):
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_not_split",
                    signal="closing_page",
                    detail=(
                        f"page {page_no} reprints a header above a "
                        f"signature/terms closing with no trades"
                    ),
                )
            )

        if use_signature and page_has_signature_block(pages[i - 1]):
            # Signature on previous page → weak end-of-doc hint for a split here,
            # unless a must_not_split already applies from stronger signals.
            out.append(
                BoundaryConstraint(
                    page=page_no,
                    kind="must_split",
                    signal="signature_block",
                    detail="signature block on previous page",
                )
            )

        prev_dims = dims if dims is not None else prev_dims
        prev_num = num
        prev_own = own
        prev_gstin = gstin if gstin else prev_gstin
        prev_md = md
        prev_complete = page_has_totals_block(md)

    return out


def compute_boundary_constraints(
    pages: list[dict],
    *,
    use_signature: bool = False,
) -> list[BoundaryConstraint]:
    """Compute Layer-1 constraints between adjacent pages.

    ``use_signature`` is off by default — enable only when it measurably helps
    perfect-packet-rate on the synthetic corpus (TASK 1). Rank conflicts are
    resolved; losers are dropped here and kept only for the debug trace.
    """
    return _resolve_constraint_conflicts(
        collect_boundary_constraints(pages, use_signature=use_signature)
    )


def explain_constraint_conflicts(
    raw: list[BoundaryConstraint],
) -> list[dict[str, Any]]:
    """Trace rows: every true constraint, with SUPPRESSED losers marked.

    Winner is the higher ``SIGNAL_RANK`` on that page (tie keeps the hold).
    """
    by_page: dict[int, list[BoundaryConstraint]] = {}
    for c in raw:
        by_page.setdefault(c.page, []).append(c)
    rows: list[dict[str, Any]] = []
    for page in sorted(by_page):
        group = by_page[page]
        splits = [c for c in group if c.kind == "must_split"]
        holds = [c for c in group if c.kind == "must_not_split"]
        suppressor: BoundaryConstraint | None = None
        losers: set[int] = set()
        if splits and holds:
            split_win = max(splits, key=lambda c: SIGNAL_RANK.get(c.signal, 0))
            hold_win = max(holds, key=lambda c: SIGNAL_RANK.get(c.signal, 0))
            max_split = SIGNAL_RANK.get(split_win.signal, 0)
            max_hold = SIGNAL_RANK.get(hold_win.signal, 0)
            if max_split > max_hold:
                suppressor = split_win
                losers = {id(c) for c in holds}
            else:
                suppressor = hold_win
                losers = {id(c) for c in splits}
        for c in group:
            row = {
                "page": c.page,
                "kind": c.kind,
                "signal": c.signal,
                "detail": c.detail,
                "rank": SIGNAL_RANK.get(c.signal, 0),
                "suppressed_by": None,
                "suppressed_by_rank": None,
            }
            if suppressor is not None and id(c) in losers:
                row["suppressed_by"] = suppressor.signal
                row["suppressed_by_rank"] = SIGNAL_RANK.get(
                    suppressor.signal, 0
                )
            rows.append(row)
    return rows


def _resolve_constraint_conflicts(
    constraints: list[BoundaryConstraint],
) -> list[BoundaryConstraint]:
    """Resolve must_split vs must_not_split on the same page via ``SIGNAL_RANK``.

    Higher rank wins. Ties keep must_not_split (wrong hard split is worse
    than a miss). ``declared_extent`` (110) beats every must_split.
    ``continuation_marker`` (100) beats ``different_doc_number``;
    ``page_dimensions`` (90) beats ``shared_doc_number``.
    """
    by_page: dict[int, list[BoundaryConstraint]] = {}
    for c in constraints:
        by_page.setdefault(c.page, []).append(c)
    resolved: list[BoundaryConstraint] = []
    for page in sorted(by_page):
        group = by_page[page]
        splits = [c for c in group if c.kind == "must_split"]
        holds = [c for c in group if c.kind == "must_not_split"]
        if not splits or not holds:
            resolved.extend(group)
            continue
        max_split = max(SIGNAL_RANK.get(c.signal, 0) for c in splits)
        max_hold = max(SIGNAL_RANK.get(c.signal, 0) for c in holds)
        if max_split > max_hold:
            resolved.extend(splits)
        else:
            # hold wins on tie — prefer not forcing a split
            resolved.extend(holds)
    return resolved


def constraint_sets(
    constraints: list[BoundaryConstraint],
) -> tuple[set[int], set[int]]:
    """Return (must_split_pages, must_not_split_pages)."""
    must_split = {c.page for c in constraints if c.kind == "must_split"}
    must_not = {c.page for c in constraints if c.kind == "must_not_split"}
    must_not -= must_split
    return must_split, must_not


def enforce_constraints_on_starts(
    starts: list[int],
    n_pages: int,
    constraints: list[BoundaryConstraint],
) -> list[int]:
    """Repair start pages so Layer-1 constraints hold."""
    must_split, must_not = constraint_sets(constraints)
    cleaned = {1, *[s for s in starts if 1 < s <= n_pages]}
    cleaned |= must_split
    cleaned -= must_not
    cleaned.add(1)
    return sorted(cleaned)


def format_constraints_for_prompt(constraints: list[BoundaryConstraint]) -> str:
    if not constraints:
        return "None (no deterministic constraints detected)."
    lines = []
    for c in constraints:
        verb = "MUST start a document at" if c.kind == "must_split" else "MUST NOT start a document at"
        lines.append(f"- {verb} page {c.page}: [{c.signal}] {c.detail}")
    return "\n".join(lines)


def derive_segment_confidence(
    page_start: int,
    page_end: int,
    constraints: list[BoundaryConstraint],
    *,
    llm_used: bool,
) -> tuple[float, list[str], list[str]]:
    """Confidence + evidence strings for one segment from which signals fired.

    Deterministic constraints → high confidence. LLM-only → lower.
    """
    evidence: list[str] = []
    signals: list[str] = []
    for c in constraints:
        if c.kind == "must_split" and c.page == page_start and page_start > 1:
            evidence.append(c.detail)
            signals.append(c.signal)
        if c.kind == "must_not_split" and page_start < c.page <= page_end:
            evidence.append(c.detail)
            signals.append(c.signal)

    if page_start == 1 and page_end >= 1 and not evidence:
        # Opening segment with no internal holds — still certain page 1 starts.
        signals.append("packet_start")
        evidence.append("page 1 always starts a document")

    high = {
        "declared_extent",
        "page_dimensions",
        "page_1_of_n",
        "shared_doc_number",
        "continuation_marker",
        "different_doc_number",
        "different_seller_gstin",
        "closing_page",
        "document_complete",
    }
    if any(s in high for s in signals):
        conf = 0.98
    elif llm_used:
        conf = 0.62
    else:
        conf = 0.75
    # Deduplicate evidence preserving order.
    seen: set[str] = set()
    uniq_ev: list[str] = []
    for e in evidence:
        if e not in seen:
            seen.add(e)
            uniq_ev.append(e)
    uniq_sig = sorted(set(signals))
    return conf, uniq_ev, uniq_sig


def annotate_segments_with_evidence(
    segments: list[dict[str, Any]],
    constraints: list[BoundaryConstraint],
    *,
    llm_used: bool,
) -> list[dict[str, Any]]:
    """Attach confidence, evidence, signals to each segment dict (in place copy)."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        ps = int(seg["page_start"])
        pe = int(seg["page_end"])
        conf, evidence, signals = derive_segment_confidence(
            ps, pe, constraints, llm_used=llm_used
        )
        row = dict(seg)
        row["confidence"] = conf
        row["evidence"] = evidence
        row["signals"] = signals
        out.append(row)
    return out
