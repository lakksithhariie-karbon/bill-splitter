"""Structured field extraction from a Mistral OCR annotated response.

This is the product's main data model: document-level header fields plus line
items tagged with the page they appear on. It is deliberately separate from
schema.py (the splitter seam) because extraction carries far more than a page
range — and the task forbids changing schema.py.

Grounding is three checks on FieldValue evidence:

  A — hallucination: each line of ``source_text`` appears on the tagged page
      (markdown-stripped normaliser). Failure → ``ungrounded`` (loud badge).
  B — normalisation faithfulness: ``value`` is a type-aware transform of
      ``source_text``. Failure → ``value_mismatch`` (quiet).
  C — evidence contiguity: full ``source_text`` is one contiguous normalised
      span. Failure → ``evidence_imprecise`` (quiet).

``value`` is the normalised form (ISO dates, plain numbers); ``source_text`` is
verbatim. Checking the page against ``value`` false-flags by construction.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """One line item, tagged with the 1-indexed page it appears on."""

    page: int = Field(ge=1)
    description: str = ""
    quantity: str = ""
    unit_price: str = ""
    amount: str = ""
    #: Field names whose value was not found verbatim in the page's OCR text.
    ungrounded: list[str] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    """One logical document with its header fields and line items."""

    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    doc_type: str = "unknown"
    vendor_name: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    currency: str = ""
    gstin: str = ""
    total_amount: str = ""
    tax_amount: str = ""
    line_items: list[LineItem] = Field(default_factory=list)
    #: Page-level aggregate confidence Mistral returned for the start page.
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Header field names whose value was not found verbatim in the doc's pages.
    ungrounded: list[str] = Field(default_factory=list)


#: Bumped when ``_EXTRACT_SYSTEM`` (mistral chat/markdown) changes.
PROMPT_VERSION = "v5"
#: Bumped only for additive/breaking DOCUMENT_V2 schema changes.
SCHEMA_VERSION = "v3"


class ExtractionResult(BaseModel):
    """Normalized output of the annotated extraction call."""

    provider: str
    model_version: str
    documents: list[ExtractedDocument] = Field(default_factory=list)
    pages_processed: int = 0
    cost_usd: float = 0.0
    raw_response_path: str | None = None
    errors: list[str] = Field(default_factory=list)
    #: Open-schema documents (architecture B). Default empty so OLD caches rehydrate.
    documents_v2: list = Field(default_factory=list)
    #: Optional cost split (ocr vs chat). None/omit on legacy annotated runs.
    cost_breakdown: dict | None = None
    #: Chat-mode only: groups of page ranges sharing a document_id value.
    duplicate_groups: list = Field(default_factory=list)
    #: Prompt/schema stamps. Empty default so legacy session caches still load.
    prompt_version: str = ""
    schema_version: str = ""


#: Header fields checked for grounding, in display order.
HEADER_FIELDS = [
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "gstin",
    "total_amount",
    "tax_amount",
]

#: Line-item fields checked for grounding (legacy closed model).
LINE_ITEM_FIELDS = ["description", "quantity", "unit_price", "amount"]

#: Line-item fields checked for grounding on ExtractedDocumentV2.
LINE_ITEM_FIELDS_V2 = [
    *LINE_ITEM_FIELDS,
    "hsn_sac",
    "discount",
]

#: Header FieldValue paths grounded on ExtractedDocumentV2 (any-page).
#: reverse_charge is a boolean classification — not grounded (Cause 3).
HEADER_FIELDS_V2 = [
    "document_id",
    "issue_date",
    "due_date",
    "currency",
    "po_number",
    "payment_terms",
    "billing_period",
    "narration",
    "shipping_address",
    "source_of_supply",
    "destination_of_supply",
]

#: Markdown table / emphasis chars never appear inside real invoice values.
_MD_SYNTAX_CHARS = "|*_`#"
_MD_SYNTAX_TRANS = str.maketrans("", "", _MD_SYNTAX_CHARS)

#: Currency symbols / codes stripped before numeric compare.
_CURRENCY_RE = re.compile(
    r"(?:₹|\$|€|£|¥|inr|usd|eur|gbp|rs\.?|rupees?)",
    re.IGNORECASE,
)

#: Trailing money/token fallback when FieldValue.value is empty.
_TRAILING_NUMERIC_RE = re.compile(
    r"(?:₹|\$|€|£|¥|INR|USD|EUR|GBP|Rs\.?)?\s*[\d,]+\.?\d*\s*%?\s*$",
    re.IGNORECASE,
)

#: Money-like tokens mined from page text for decimal compare.
_MONEY_TOKEN_RE = re.compile(
    r"(?:(?:₹|\$|€|£|¥|INR|USD|EUR|GBP|Rs\.?)\s*)?"
    r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def coverage_gaps(
    documents: list[ExtractedDocument], pages_processed: int
) -> list[tuple[int, int]]:
    """Return contiguous uncovered page ranges within 1..pages_processed."""
    if pages_processed <= 0:
        return []
    covered: set[int] = set()
    for doc in documents:
        start = max(1, int(doc.page_start))
        end = min(pages_processed, int(doc.page_end))
        if end < start:
            continue
        for p in range(start, end + 1):
            covered.add(p)
    missing = sorted(set(range(1, pages_processed + 1)) - covered)
    if not missing:
        return []
    gaps: list[tuple[int, int]] = []
    gap_start = missing[0]
    prev = missing[0]
    for p in missing[1:]:
        if p == prev + 1:
            prev = p
            continue
        gaps.append((gap_start, prev))
        gap_start = prev = p
    gaps.append((gap_start, prev))
    return gaps


def uncovered_pages(
    documents: list[ExtractedDocument], pages_processed: int
) -> list[int]:
    """Flat sorted list of pages not covered by any document range."""
    pages: list[int] = []
    for start, end in coverage_gaps(documents, pages_processed):
        pages.extend(range(start, end + 1))
    return pages


def coverage_error_codes(
    documents: list[ExtractedDocument], pages_processed: int
) -> list[str]:
    """Error codes `uncovered_pages:<start>-<end>` for each contiguous gap."""
    return [
        f"uncovered_pages:{start}-{end}"
        for start, end in coverage_gaps(documents, pages_processed)
    ]


def _normalise(text: str) -> str:
    """Normalise for substring match: case, whitespace, markdown syntax chars.

    Strips ``| * _ ` #`` so markdown table pipes do not break verbatim checks.
    Those characters do not appear inside real invoice field values.
    """
    cleaned = text.translate(_MD_SYNTAX_TRANS)
    return "".join(cleaned.split()).lower()


def _mostly_numeric(value: str) -> bool:
    """True when value is a money/rate-like numeric (optional currency / %)."""
    s = _CURRENCY_RE.sub("", value.strip())
    s = s.replace(" ", "").replace(",", "").replace("%", "").replace("−", "-")
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", s))


def _to_decimal(token: str) -> Decimal | None:
    """Parse a money/rate token to Decimal, or None if not numeric."""
    s = _CURRENCY_RE.sub("", token.strip())
    s = s.replace(" ", "").replace(",", "").replace("%", "").replace("−", "-")
    # Parenthetical negatives: (1,234.00)
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    if not s or not re.search(r"\d", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _decimals_equal(a: Decimal, b: Decimal) -> bool:
    """Compare money amounts to 2 decimal places."""
    q = Decimal("0.01")
    return a.quantize(q) == b.quantize(q)


def _numeric_on_page(claim: str, page_text: str) -> bool:
    """True if claim's decimal equals some money-like token on the page."""
    claim_dec = _to_decimal(claim)
    if claim_dec is None:
        return False
    # Compare against money tokens on raw and Cause-1-normalised page text.
    # Avoid bare substring checks (e.g. claim "0" inside "100.00").
    for text in (page_text, _normalise(page_text)):
        for tok in _MONEY_TOKEN_RE.findall(text):
            tok_dec = _to_decimal(tok)
            if tok_dec is not None and _decimals_equal(tok_dec, claim_dec):
                return True
    return False


def _value_grounded(value: str, page_text: str) -> bool:
    """True if a non-empty value appears on the page (tier-1 / legacy).

    Empty values are not "ungrounded" — they are simply missing, which is a
    different failure (a field the model chose not to fill). Only a non-empty
    value that cannot be found in the page text is a hallucination.

    Numerics: currency/thousands-separator tolerant, compared to 2dp.
    Text: Cause-1 normalised substring match.
    """
    if not value:
        return True
    if _mostly_numeric(value):
        return _numeric_on_page(value, page_text)
    return _normalise(value) in _normalise(page_text)


def _resolve_fv_pages(
    fv: FieldValue, page_markdowns: dict[int, str]
) -> list[int]:
    return [p for p in (fv.pages or []) if p in page_markdowns]


def _pages_text_match(
    needle: str,
    pages: list[int],
    page_markdowns: dict[int, str],
    *,
    any_page: bool,
    matcher,
) -> bool:
    if not pages:
        return False
    if any_page:
        return any(matcher(needle, page_markdowns.get(p, "")) for p in pages)
    return matcher(needle, page_markdowns.get(pages[0], ""))


def _source_line_on_page(line: str, page_text: str) -> bool:
    """True if one non-empty source line appears on the page (CHECK A piece)."""
    needle = line.strip()
    if not needle:
        return True
    return _normalise(needle) in _normalise(page_text)


def _check_a_source_on_page(
    fv: FieldValue, page_markdowns: dict[int, str], *, any_page: bool
) -> bool:
    """CHECK A: each non-empty line of source_text appears on tagged page(s).

    Empty source_text is not a hallucination (nothing claimed from the page).
    Multi-line source may be stitched — lines are checked independently so
    CHECK C can still flag non-contiguous full spans.
    """
    source = fv.source_text or ""
    if not source.strip():
        return True
    pages = _resolve_fv_pages(fv, page_markdowns)
    if not pages:
        return False
    lines = [ln for ln in source.splitlines() if ln.strip()] or [source]
    for line in lines:
        if not _pages_text_match(
            line,
            pages,
            page_markdowns,
            any_page=any_page,
            matcher=_source_line_on_page,
        ):
            return False
    return True


def _check_c_evidence_contiguous(
    fv: FieldValue, page_markdowns: dict[int, str], *, any_page: bool
) -> bool:
    """CHECK C: full source_text is one contiguous normalised span of the page."""
    source = fv.source_text or ""
    if not source.strip():
        return True
    pages = _resolve_fv_pages(fv, page_markdowns)
    if not pages:
        return False

    def contiguous(needle: str, page_text: str) -> bool:
        return _normalise(needle) in _normalise(page_text)

    return _pages_text_match(
        source, pages, page_markdowns, any_page=any_page, matcher=contiguous
    )


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
)


def _parse_date(text: str) -> date | None:
    """Best-effort date parse for CHECK B; None if not date-like."""
    s = (text or "").strip()
    if not s:
        return None
    # Drop common labels so "Invoice Date Sep 27, 2020" still parses.
    s = re.sub(
        r"(?i)\b(invoice\s*date|bill\s*date|due\s*date|date|dated)\b[:\s]*",
        " ",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip(" .:;,")
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last token attempts (e.g. trailing date in a longer phrase).
    parts = s.split()
    for n in (3, 4, 1):
        if len(parts) >= n:
            chunk = " ".join(parts[-n:])
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(chunk, fmt).date()
                except ValueError:
                    continue
    return None


def _looks_like_date_pair(value: str, source: str) -> bool:
    """True when either side parses as a date (CHECK B date branch)."""
    return _parse_date(value) is not None or _parse_date(source) is not None


def _numeric_key(text: str) -> Decimal | None:
    """Strip currency / thousands separators; return Decimal or None."""
    return _to_decimal(text)


def _source_has_sign(source: str) -> bool:
    s = source or ""
    if "-" in s or "−" in s:
        return True
    return bool(re.search(r"\(\s*[\d,.]", s))


def _text_key(text: str) -> str:
    """Collapse whitespace and punctuation for text faithfulness."""
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE).casefold()


def _check_b_value_faithful(fv: FieldValue) -> bool:
    """CHECK B: ``value`` is a faithful type-aware transform of ``source_text``.

    Empty value or empty source is not a mismatch (nothing to compare).
    """
    value = (fv.value or "").strip()
    source = (fv.source_text or "").strip()
    if not value or not source:
        return True

    # Date branch: ISO value vs verbose source.
    if _looks_like_date_pair(value, source):
        dv, ds = _parse_date(value), _parse_date(source)
        if dv is not None and ds is not None:
            return dv == ds
        # One side date-like but the other unparsable → mismatch.
        if dv is not None or ds is not None:
            return False

    # Numeric branch (money / rates / quantities).
    if _mostly_numeric(value) or _numeric_key(source) is not None:
        v_dec = _numeric_key(value)
        # Prefer last money-like token in source when source is a phrase.
        s_dec = _numeric_key(source)
        if s_dec is None:
            m = _TRAILING_NUMERIC_RE.search(source)
            if m:
                s_dec = _numeric_key(m.group(0))
            if s_dec is None:
                for tok in _MONEY_TOKEN_RE.findall(source):
                    s_dec = _numeric_key(tok)
                    if s_dec is not None:
                        break
        if v_dec is not None and s_dec is not None:
            if _decimals_equal(v_dec, s_dec):
                return True
            # Sign: ignore difference only when source carries the sign.
            if _source_has_sign(source) and _decimals_equal(abs(v_dec), abs(s_dec)):
                return True
            return False
        if v_dec is not None and s_dec is None:
            return False

    # Text branch.
    return _text_key(value) == _text_key(source) or _text_key(value) in _text_key(
        source
    )


# Back-compat aliases used by older tests / callers.
def _tier1_claim(fv: FieldValue) -> str:
    """Legacy: prefer value; else trailing token of source."""
    if (fv.value or "").strip():
        return fv.value
    source = (fv.source_text or "").strip()
    if not source:
        return ""
    m = _TRAILING_NUMERIC_RE.search(source)
    if m:
        return m.group(0).strip()
    parts = source.split()
    return parts[-1] if parts else source


def _tier1_value_grounded(
    fv: FieldValue, page_markdowns: dict[int, str], *, any_page: bool
) -> bool:
    """CHECK A (hallucination): source_text lines present on page."""
    return _check_a_source_on_page(fv, page_markdowns, any_page=any_page)


def _tier2_evidence_contiguous(
    fv: FieldValue, page_markdowns: dict[int, str], *, any_page: bool
) -> bool:
    """CHECK C: full source_text contiguous on page."""
    return _check_c_evidence_contiguous(fv, page_markdowns, any_page=any_page)


def apply_grounding(
    documents: list[ExtractedDocument], page_markdowns: dict[int, str]
) -> list[ExtractedDocument]:
    """Flag every extracted field whose value is not grounded in the page text.

    `page_markdowns` maps 1-indexed page -> OCR markdown for that page.
    Header fields are checked against the union of all pages in the document's
    range; line items are checked against the single page they claim to be on.
    Returns new documents with `ungrounded` lists populated.
    """
    result: list[ExtractedDocument] = []
    for doc in documents:
        doc_text = " ".join(
            page_markdowns.get(p, "") for p in range(doc.page_start, doc.page_end + 1)
        )
        ungrounded = [
            f
            for f in HEADER_FIELDS
            if not _value_grounded(getattr(doc, f), doc_text)
        ]
        new_items: list[LineItem] = []
        for item in doc.line_items:
            page_text = page_markdowns.get(item.page, "")
            item_ungrounded = [
                f
                for f in LINE_ITEM_FIELDS
                if not _value_grounded(getattr(item, f), page_text)
            ]
            new_items.append(item.model_copy(update={"ungrounded": item_ungrounded}))
        result.append(
            doc.model_copy(update={"ungrounded": ungrounded, "line_items": new_items})
        )
    return result


# ---------------------------------------------------------------------------
# Architecture B open contract (FieldValue + ExtractedDocumentV2)
# ---------------------------------------------------------------------------


class FieldValue(BaseModel):
    """One extracted value with verbatim page evidence and page tags.

    ``value`` is the normalised form (ISO dates, plain numbers); ``source_text``
    is verbatim page text. Grounding CHECK A uses ``source_text`` against the
    page; CHECK B compares ``value`` to ``source_text``; CHECK C requires
    ``source_text`` to be a contiguous page span.
    """

    value: str = ""
    source_text: str = ""
    pages: list[int] = Field(default_factory=list)


class PartyInfo(BaseModel):
    name: FieldValue = Field(default_factory=FieldValue)
    address: FieldValue = Field(default_factory=FieldValue)
    tax_id: FieldValue = Field(default_factory=FieldValue)


class TaxLineV2(BaseModel):
    name: FieldValue = Field(default_factory=FieldValue)
    rate: FieldValue = Field(default_factory=FieldValue)
    base: FieldValue = Field(default_factory=FieldValue)
    amount: FieldValue = Field(default_factory=FieldValue)
    #: Discriminant hint: gst | tds | other | unknown (as FieldValue for grounding).
    kind: FieldValue = Field(default_factory=FieldValue)


class TotalsV2(BaseModel):
    subtotal: FieldValue = Field(default_factory=FieldValue)
    tax_lines: list[TaxLineV2] = Field(default_factory=list)
    total: FieldValue = Field(default_factory=FieldValue)
    amount_due: FieldValue = Field(default_factory=FieldValue)
    tds: FieldValue = Field(default_factory=FieldValue)
    other_taxes: FieldValue = Field(default_factory=FieldValue)


class LineItemV2(BaseModel):
    page: int = Field(ge=1)
    description: FieldValue = Field(default_factory=FieldValue)
    quantity: FieldValue = Field(default_factory=FieldValue)
    unit_price: FieldValue = Field(default_factory=FieldValue)
    amount: FieldValue = Field(default_factory=FieldValue)
    hsn_sac: FieldValue = Field(default_factory=FieldValue)
    discount: FieldValue = Field(default_factory=FieldValue)
    #: CHECK A: source_text not found on the page (hallucination).
    ungrounded: list[str] = Field(default_factory=list)
    #: CHECK C: source_text not a contiguous page span (imprecise evidence).
    evidence_imprecise: list[str] = Field(default_factory=list)
    #: CHECK B: value is not a faithful transform of source_text.
    value_mismatch: list[str] = Field(default_factory=list)


class OtherField(BaseModel):
    name: str = ""
    value: FieldValue = Field(default_factory=FieldValue)


class ExtractedDocumentV2(BaseModel):
    """Open extraction document — nothing on the page has nowhere to go.

    Bank rails, SKU, UOM, and item_type are intentionally absent: they have no
    destination on the production AP voucher (and inventing masters is worse).
    """

    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    doc_type: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    seller: PartyInfo = Field(default_factory=PartyInfo)
    buyer: PartyInfo = Field(default_factory=PartyInfo)
    document_id: FieldValue = Field(default_factory=FieldValue)
    issue_date: FieldValue = Field(default_factory=FieldValue)
    due_date: FieldValue = Field(default_factory=FieldValue)
    currency: FieldValue = Field(default_factory=FieldValue)
    po_number: FieldValue = Field(default_factory=FieldValue)
    payment_terms: FieldValue = Field(default_factory=FieldValue)
    billing_period: FieldValue = Field(default_factory=FieldValue)
    narration: FieldValue = Field(default_factory=FieldValue)
    shipping_address: FieldValue = Field(default_factory=FieldValue)
    source_of_supply: FieldValue = Field(default_factory=FieldValue)
    destination_of_supply: FieldValue = Field(default_factory=FieldValue)
    reverse_charge: FieldValue = Field(default_factory=FieldValue)
    totals: TotalsV2 = Field(default_factory=TotalsV2)
    line_items: list[LineItemV2] = Field(default_factory=list)
    other_fields: list[OtherField] = Field(default_factory=list)
    #: CHECK A: source_text not found on the page (hallucination).
    ungrounded: list[str] = Field(default_factory=list)
    #: CHECK C: source_text not a contiguous page span (imprecise evidence).
    evidence_imprecise: list[str] = Field(default_factory=list)
    #: CHECK B: value is not a faithful transform of source_text.
    value_mismatch: list[str] = Field(default_factory=list)
    #: Arithmetic: subtotal + additive taxes − discounts ≉ total.
    totals_mismatch: bool = False
    #: Names of tax_lines dropped as summary rows (Total GST, etc.).
    tax_summary_rows_dropped: list[str] = Field(default_factory=list)


#: Absolute money tolerance for totals reconciliation (rupees / dollars).
TOTALS_RECONCILE_TOLERANCE = Decimal("1.00")

#: Line descriptions that are tax/TDS summary rows, not goods/services.
_TAX_NAMED_LINE_RE = re.compile(
    r"^(?:(?:add|less)\s*:\s*)?(cgst|sgst|igst|gst|tds)"
    r"(?:\s*@?\s*\d+(?:\.\d+)?%?)?\s*$",
    re.IGNORECASE,
)

#: tax_lines names that summarise other tax components (not components).
_TAX_SUMMARY_NAME_RE = re.compile(
    r"(?i)^\s*total\s+(?:gst|tax)(?:es)?(?:\s+amount)?\s*$"
    r"|^\s*(?:gst|tax)\s+amount\s*$"
)

#: Kinds that reduce invoice value (reconcile as deductions).
_DEDUCTION_KINDS = frozenset({"discount"})
#: Kinds that must not be added into totals.total (TDS reduces amount_due).
_NON_ADDITIVE_KINDS = frozenset({"discount", "tds"})


def fold_tax_named_line_items(doc: ExtractedDocumentV2) -> ExtractedDocumentV2:
    """Move lone GST/TDS description rows from line_items into tax_lines.

    FIX 1 residual: the model sometimes still emits a line whose description
    is only ``GST`` / ``TDS`` / ``CGST`` / …. Those belong in ``tax_lines``.
    """
    kept: list[LineItemV2] = []
    new_tax = list(doc.totals.tax_lines)
    tds_fv = doc.totals.tds
    for it in doc.line_items:
        desc = (it.description.value or it.description.source_text or "").strip()
        m = _TAX_NAMED_LINE_RE.match(desc)
        if not m:
            kept.append(it)
            continue
        token = m.group(1).lower()
        kind = "tds" if token == "tds" else "gst"
        amt = (it.amount.value or it.amount.source_text or "").strip()
        already = False
        for tl in new_tax:
            n = (tl.name.value or tl.name.source_text or "").strip().lower()
            a = (tl.amount.value or tl.amount.source_text or "").strip()
            if n == desc.lower() and a == amt:
                already = True
                break
        if not already:
            new_tax.append(
                TaxLineV2(
                    name=it.description,
                    amount=it.amount,
                    kind=FieldValue(
                        value=kind,
                        source_text="",
                        pages=list(it.description.pages or [it.page]),
                    ),
                )
            )
        if kind == "tds" and not (tds_fv.value or tds_fv.source_text or "").strip():
            tds_fv = it.amount
    if len(kept) == len(doc.line_items):
        return doc
    totals = doc.totals.model_copy(update={"tax_lines": new_tax, "tds": tds_fv})
    return doc.model_copy(update={"line_items": kept, "totals": totals})


def _tax_line_amount(tl: TaxLineV2) -> Decimal | None:
    return _to_decimal((tl.amount.value or tl.amount.source_text or "").strip())


def _tax_line_name(tl: TaxLineV2) -> str:
    return (tl.name.value or tl.name.source_text or "").strip()


def _tax_line_kind(tl: TaxLineV2) -> str:
    return (tl.kind.value or tl.kind.source_text or "").strip().lower()


def drop_tax_summary_rows(doc: ExtractedDocumentV2) -> ExtractedDocumentV2:
    """Drop tax_lines that summarise other tax components.

    A summary row is not a member of the collection it summarises — same rule
    as line_items vs totals. Drop when the name matches a total pattern, or
    when one line's amount equals the sum of the other lines' amounts.
    """
    lines = list(doc.totals.tax_lines)
    if len(lines) < 2 and not any(
        _TAX_SUMMARY_NAME_RE.search(_tax_line_name(tl)) for tl in lines
    ):
        return doc

    drop_idx: set[int] = set()
    dropped_names: list[str] = []

    for i, tl in enumerate(lines):
        name = _tax_line_name(tl)
        if name and _TAX_SUMMARY_NAME_RE.search(name):
            drop_idx.add(i)
            dropped_names.append(name)

    # Amount equals sum of the others — only when that is a real summary:
    # ≥2 other components, or the name itself looks like a total.
    amounts = [_tax_line_amount(tl) for tl in lines]
    for i, amt in enumerate(amounts):
        if i in drop_idx or amt is None:
            continue
        others = [
            a
            for j, a in enumerate(amounts)
            if j != i and j not in drop_idx and a is not None
        ]
        if not others:
            continue
        if abs(amt - sum(others)) > TOTALS_RECONCILE_TOLERANCE:
            continue
        if amt == 0:
            continue  # zeros are not a useful summary signal
        name = _tax_line_name(lines[i])
        name_looks_total = bool(
            name and ("total" in name.lower() or _TAX_SUMMARY_NAME_RE.search(name))
        )
        if len(others) >= 2 or name_looks_total:
            drop_idx.add(i)
            dropped_names.append(name or f"tax_lines[{i}]")
    if not drop_idx:
        return doc

    kept = [tl for i, tl in enumerate(lines) if i not in drop_idx]
    totals = doc.totals.model_copy(update={"tax_lines": kept})
    notes = list(doc.tax_summary_rows_dropped) + dropped_names
    return doc.model_copy(
        update={
            "totals": totals,
            "tax_summary_rows_dropped": notes,
        }
    )


def totals_reconcile(
    doc: ExtractedDocumentV2,
    *,
    tolerance: Decimal = TOTALS_RECONCILE_TOLERANCE,
) -> bool:
    """Return True when subtotal + additive taxes − discounts ≈ total.

    Additive taxes: tax_lines whose kind is not ``discount`` or ``tds``.
    Deductions: kind ``discount`` (and name hint ``discount``).

    TDS is intentionally neither added nor subtracted here: on the real
    packet (e.g. p6/p10) TOTAL = subtotal + GST, then TDS reduces BALANCE
    DUE / amount_due — not the invoice total. Subtracting TDS from this
    check would false-flag those documents.
    """
    sub = _to_decimal(
        (doc.totals.subtotal.value or doc.totals.subtotal.source_text or "").strip()
    )
    tot = _to_decimal(
        (doc.totals.total.value or doc.totals.total.source_text or "").strip()
    )
    if sub is None or tot is None:
        return True
    additive = Decimal(0)
    deductions = Decimal(0)
    for tl in doc.totals.tax_lines:
        amt = _tax_line_amount(tl)
        if amt is None:
            continue
        kind = _tax_line_kind(tl)
        name_l = _tax_line_name(tl).lower()
        is_discount = kind in _DEDUCTION_KINDS or "discount" in name_l
        if is_discount:
            deductions += abs(amt)
            continue
        if kind in _NON_ADDITIVE_KINDS:
            continue
        additive += amt
    expected = sub + additive - deductions
    return abs(expected - tot) <= tolerance


def apply_totals_reconciliation(
    documents: list[ExtractedDocumentV2],
) -> list[ExtractedDocumentV2]:
    """Set ``totals_mismatch`` when the reconcile formula does not hold."""
    out: list[ExtractedDocumentV2] = []
    for doc in documents:
        ok = totals_reconcile(doc)
        out.append(doc.model_copy(update={"totals_mismatch": not ok}))
    return out


def normalize_extracted_document_v2(doc: ExtractedDocumentV2) -> ExtractedDocumentV2:
    """Deterministic post-parse cleanups before grounding / reconciliation."""
    doc = fold_tax_named_line_items(doc)
    doc = drop_tax_summary_rows(doc)
    return doc


def _fv_from_raw(raw) -> FieldValue:
    """Coerce a chat JSON field into FieldValue."""
    if raw is None:
        return FieldValue()
    if isinstance(raw, str):
        return FieldValue(value=raw, source_text=raw, pages=[])
    if isinstance(raw, dict):
        pages = raw.get("pages") or []
        try:
            pages = [int(p) for p in pages]
        except (TypeError, ValueError):
            pages = []
        value = raw.get("value", "") or ""
        source = raw.get("source_text", "") or value
        return FieldValue(value=str(value), source_text=str(source), pages=pages)
    return FieldValue(value=str(raw), source_text=str(raw), pages=[])


def _party_from_raw(raw) -> PartyInfo:
    raw = raw or {}
    if not isinstance(raw, dict):
        return PartyInfo()
    return PartyInfo(
        name=_fv_from_raw(raw.get("name")),
        address=_fv_from_raw(raw.get("address")),
        tax_id=_fv_from_raw(raw.get("tax_id")),
    )


def document_v2_from_dict(raw: dict, page_start: int, page_end: int) -> ExtractedDocumentV2:
    """Parse one chat-extract JSON object into ExtractedDocumentV2."""
    raw = raw or {}
    totals_raw = raw.get("totals") or {}
    tax_lines = []
    for tl in totals_raw.get("tax_lines") or []:
        if not isinstance(tl, dict):
            continue
        tax_lines.append(
            TaxLineV2(
                name=_fv_from_raw(tl.get("name")),
                rate=_fv_from_raw(tl.get("rate")),
                base=_fv_from_raw(tl.get("base")),
                amount=_fv_from_raw(tl.get("amount")),
                kind=_fv_from_raw(tl.get("kind")),
            )
        )
    totals = TotalsV2(
        subtotal=_fv_from_raw(totals_raw.get("subtotal")),
        tax_lines=tax_lines,
        total=_fv_from_raw(totals_raw.get("total")),
        amount_due=_fv_from_raw(totals_raw.get("amount_due")),
        tds=_fv_from_raw(totals_raw.get("tds")),
        other_taxes=_fv_from_raw(totals_raw.get("other_taxes")),
    )
    # bank / sku / uom / item_type intentionally ignored if present in legacy JSON.
    items: list[LineItemV2] = []
    for it in raw.get("line_items") or []:
        if not isinstance(it, dict):
            continue
        page = int(it.get("page") or page_start)
        items.append(
            LineItemV2(
                page=page,
                description=_fv_from_raw(it.get("description")),
                quantity=_fv_from_raw(it.get("quantity")),
                unit_price=_fv_from_raw(it.get("unit_price")),
                amount=_fv_from_raw(it.get("amount")),
                hsn_sac=_fv_from_raw(it.get("hsn_sac")),
                discount=_fv_from_raw(it.get("discount")),
            )
        )
    other: list[OtherField] = []
    for of in raw.get("other_fields") or []:
        if not isinstance(of, dict):
            continue
        other.append(
            OtherField(
                name=str(of.get("name") or ""),
                value=_fv_from_raw(of.get("value")),
            )
        )
    return ExtractedDocumentV2(
        page_start=int(raw.get("page_start") or page_start),
        page_end=int(raw.get("page_end") or page_end),
        doc_type=str(raw.get("doc_type") or "unknown"),
        confidence=float(raw.get("confidence") or 0.0),
        seller=_party_from_raw(raw.get("seller")),
        buyer=_party_from_raw(raw.get("buyer")),
        document_id=_fv_from_raw(raw.get("document_id")),
        issue_date=_fv_from_raw(raw.get("issue_date")),
        due_date=_fv_from_raw(raw.get("due_date")),
        currency=_fv_from_raw(raw.get("currency")),
        po_number=_fv_from_raw(raw.get("po_number")),
        payment_terms=_fv_from_raw(raw.get("payment_terms")),
        billing_period=_fv_from_raw(raw.get("billing_period")),
        narration=_fv_from_raw(raw.get("narration")),
        shipping_address=_fv_from_raw(raw.get("shipping_address")),
        source_of_supply=_fv_from_raw(raw.get("source_of_supply")),
        destination_of_supply=_fv_from_raw(raw.get("destination_of_supply")),
        reverse_charge=_fv_from_raw(raw.get("reverse_charge")),
        totals=totals,
        line_items=items,
        other_fields=other,
    )


def _field_value_grounded(fv: FieldValue, page_markdowns: dict[int, str], *, any_page: bool) -> bool:
    """CHECK A: source_text present on tagged page(s)."""
    return _check_a_source_on_page(fv, page_markdowns, any_page=any_page)


def apply_grounding_v2(
    documents: list[ExtractedDocumentV2], page_markdowns: dict[int, str]
) -> list[ExtractedDocumentV2]:
    """Three-check FieldValue grounding.

    CHECK A (``ungrounded``): source_text lines missing from tagged page(s).
    CHECK B (``value_mismatch``): value is not a faithful transform of source.
    CHECK C (``evidence_imprecise``): source_text is not one contiguous span
    (only recorded when CHECK A passed).

    ``reverse_charge`` and ``tax_lines[].kind`` are never grounded (Cause 3).
    """
    result: list[ExtractedDocumentV2] = []
    for doc in documents:
        ungrounded: list[str] = []
        evidence_imprecise: list[str] = []
        value_mismatch: list[str] = []

        def check(path: str, fv: FieldValue, any_page: bool = True) -> None:
            if not _check_a_source_on_page(fv, page_markdowns, any_page=any_page):
                ungrounded.append(path)
                return
            if not _check_b_value_faithful(fv):
                value_mismatch.append(path)
            if not _check_c_evidence_contiguous(fv, page_markdowns, any_page=any_page):
                evidence_imprecise.append(path)

        check("seller.name", doc.seller.name)
        check("seller.address", doc.seller.address)
        check("seller.tax_id", doc.seller.tax_id)
        check("buyer.name", doc.buyer.name)
        check("buyer.address", doc.buyer.address)
        check("buyer.tax_id", doc.buyer.tax_id)
        for key in HEADER_FIELDS_V2:
            check(key, getattr(doc, key))
        check("totals.subtotal", doc.totals.subtotal)
        check("totals.total", doc.totals.total)
        check("totals.amount_due", doc.totals.amount_due)
        check("totals.tds", doc.totals.tds)
        check("totals.other_taxes", doc.totals.other_taxes)
        for i, tl in enumerate(doc.totals.tax_lines):
            check(f"totals.tax_lines[{i}].name", tl.name)
            check(f"totals.tax_lines[{i}].rate", tl.rate)
            check(f"totals.tax_lines[{i}].base", tl.base)
            check(f"totals.tax_lines[{i}].amount", tl.amount)
            # kind is a gst|tds|other|unknown discriminant — never grounded.
        new_items: list[LineItemV2] = []
        for i, item in enumerate(doc.line_items):
            item_ungrounded: list[str] = []
            item_imprecise: list[str] = []
            item_mismatch: list[str] = []
            for f in LINE_ITEM_FIELDS_V2:
                fv: FieldValue = getattr(item, f)
                # Ensure page tag for line cells.
                if not fv.pages:
                    fv = fv.model_copy(update={"pages": [item.page]})
                if not _check_a_source_on_page(fv, page_markdowns, any_page=False):
                    item_ungrounded.append(f)
                    continue
                if not _check_b_value_faithful(fv):
                    item_mismatch.append(f)
                if not _check_c_evidence_contiguous(fv, page_markdowns, any_page=False):
                    item_imprecise.append(f)
            new_items.append(
                item.model_copy(
                    update={
                        "ungrounded": item_ungrounded,
                        "evidence_imprecise": item_imprecise,
                        "value_mismatch": item_mismatch,
                    }
                )
            )
        for i, of in enumerate(doc.other_fields):
            check(f"other_fields[{i}].{of.name or i}", of.value)
        result.append(
            doc.model_copy(
                update={
                    "ungrounded": ungrounded,
                    "evidence_imprecise": evidence_imprecise,
                    "value_mismatch": value_mismatch,
                    "line_items": new_items,
                }
            )
        )
    return result


def to_legacy_headers(doc: ExtractedDocumentV2) -> ExtractedDocument:
    """Project V2 onto the closed 7-header model for UI / corrections compat."""

    def val(fv: FieldValue) -> str:
        return fv.value or fv.source_text or ""

    tax_amount = ""
    if doc.totals.tax_lines:
        # Prefer sum of tax line amounts when present; else first amount.
        parts = [val(tl.amount) for tl in doc.totals.tax_lines if val(tl.amount)]
        tax_amount = parts[0] if len(parts) == 1 else ("; ".join(parts) if parts else "")
    items = [
        LineItem(
            page=it.page,
            description=val(it.description),
            quantity=val(it.quantity),
            unit_price=val(it.unit_price),
            amount=val(it.amount),
            ungrounded=list(it.ungrounded),
        )
        for it in doc.line_items
    ]
    # Map V2 ungrounded paths onto legacy field names where possible.
    legacy_ungrounded: list[str] = []
    mapping = {
        "seller.name": "vendor_name",
        "document_id": "invoice_number",
        "issue_date": "invoice_date",
        "currency": "currency",
        "seller.tax_id": "gstin",
        "totals.total": "total_amount",
    }
    for path in doc.ungrounded:
        if path in mapping:
            legacy_ungrounded.append(mapping[path])
        elif path.startswith("totals.tax_lines") and "tax_amount" not in legacy_ungrounded:
            legacy_ungrounded.append("tax_amount")
    return ExtractedDocument(
        page_start=doc.page_start,
        page_end=doc.page_end,
        doc_type=doc.doc_type,
        vendor_name=val(doc.seller.name),
        invoice_number=val(doc.document_id),
        invoice_date=val(doc.issue_date),
        currency=val(doc.currency),
        gstin=val(doc.seller.tax_id),
        total_amount=val(doc.totals.total),
        tax_amount=tax_amount,
        line_items=items,
        confidence=doc.confidence,
        ungrounded=legacy_ungrounded,
    )


#: doc_type values that map cleanly onto a Tally-style voucher type hint.
_KNOWN_VOUCHER_TYPES = frozenset(
    {
        "purchase",
        "sales",
        "debit_note",
        "credit_note",
        "payment",
        "receipt",
        "journal",
    }
)


def to_voucher_payload(doc: ExtractedDocumentV2) -> dict:
    """Project V2 onto a production review-voucher–shaped dict (OCR stage).

    Coding masters (purchase_account, cost_centre, godown) are always null —
    enrichment / HITL fills them later. Does not rename internal V2 paths.
    """

    def val(fv: FieldValue) -> str:
        return (fv.value or fv.source_text or "").strip()

    dt_raw = (doc.doc_type or "").strip()
    # Collapse whitespace/hyphen runs (matches prior live TS export behaviour).
    dt_key = re.sub(r"[\s-]+", "_", dt_raw.casefold())
    vch_type = dt_raw if dt_key in _KNOWN_VOUCHER_TYPES else None

    items = []
    for it in doc.line_items:
        items.append(
            {
                "itemDesc": val(it.description),
                "hsnSac": val(it.hsn_sac) or None,
                "quantity": val(it.quantity) or None,
                "unitPrice": val(it.unit_price) or None,
                "discount": val(it.discount) or None,
                "lineAmount": val(it.amount) or None,
            }
        )

    tax_lines = []
    for tl in doc.totals.tax_lines:
        tax_lines.append(
            {
                "name": val(tl.name) or None,
                "rate": val(tl.rate) or None,
                "base": val(tl.base) or None,
                "amount": val(tl.amount) or None,
                "kind": val(tl.kind) or None,
            }
        )

    return {
        "vendorName": val(doc.seller.name) or None,
        "gstin": val(doc.seller.tax_id) or None,
        "supInvNo": val(doc.document_id) or None,
        "billDate": val(doc.issue_date) or None,
        "dueDate": val(doc.due_date) or None,
        "vchType": vch_type,
        "narration": val(doc.narration) or None,
        "sourceOfSupply": val(doc.source_of_supply) or None,
        "destinationOfSupply": val(doc.destination_of_supply) or None,
        "shippingAddress": val(doc.shipping_address) or None,
        "isReverseChargeApplied": val(doc.reverse_charge) or None,
        "vchAmt": val(doc.totals.total) or None,
        "subtotal": val(doc.totals.subtotal) or None,
        "tds": val(doc.totals.tds) or None,
        "otherTaxes": val(doc.totals.other_taxes) or None,
        "amountDue": val(doc.totals.amount_due) or None,
        "currency": val(doc.currency) or None,
        "alreadyPaid": is_already_paid(doc),
        "totalsMismatch": bool(doc.totals_mismatch),
        # Coding slots — enrichment fills later (not OCR targets).
        "purchase_account": None,
        "cost_centre": None,
        "godown": None,
        "lines": {
            "items": items,
            "taxes": tax_lines,
        },
        "page_start": doc.page_start,
        "page_end": doc.page_end,
        "doc_type": doc.doc_type,
    }


def is_already_paid(doc: ExtractedDocumentV2) -> bool:
    """True when amount_due is present and zero while total is non-zero.

    Signals a settled bill (e.g. card-paid Freshworks invoice). Warning only —
    never blocks approval.
    """
    total_raw = (doc.totals.total.value or doc.totals.total.source_text or "").strip()
    due_raw = (
        doc.totals.amount_due.value or doc.totals.amount_due.source_text or ""
    ).strip()
    if not due_raw or not total_raw:
        return False
    total = _to_decimal(total_raw)
    due = _to_decimal(due_raw)
    if total is None or due is None:
        return False
    return due == 0 and total != 0


#: JSON schema for chat document extraction (architecture B).
DOCUMENT_V2_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "page_start": {"type": "integer"},
        "page_end": {"type": "integer"},
        "doc_type": {"type": "string"},
        "seller": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"$ref": "#/$defs/field_value"},
                "address": {"$ref": "#/$defs/field_value"},
                "tax_id": {"$ref": "#/$defs/field_value"},
            },
            "required": ["name", "address", "tax_id"],
        },
        "buyer": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"$ref": "#/$defs/field_value"},
                "address": {"$ref": "#/$defs/field_value"},
                "tax_id": {"$ref": "#/$defs/field_value"},
            },
            "required": ["name", "address", "tax_id"],
        },
        "document_id": {"$ref": "#/$defs/field_value"},
        "issue_date": {"$ref": "#/$defs/field_value"},
        "due_date": {"$ref": "#/$defs/field_value"},
        "currency": {"$ref": "#/$defs/field_value"},
        "po_number": {"$ref": "#/$defs/field_value"},
        "payment_terms": {"$ref": "#/$defs/field_value"},
        "billing_period": {"$ref": "#/$defs/field_value"},
        "narration": {"$ref": "#/$defs/field_value"},
        "shipping_address": {"$ref": "#/$defs/field_value"},
        "source_of_supply": {"$ref": "#/$defs/field_value"},
        "destination_of_supply": {"$ref": "#/$defs/field_value"},
        "reverse_charge": {"$ref": "#/$defs/field_value"},
        "totals": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "subtotal": {"$ref": "#/$defs/field_value"},
                "tax_lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"$ref": "#/$defs/field_value"},
                            "rate": {"$ref": "#/$defs/field_value"},
                            "base": {"$ref": "#/$defs/field_value"},
                            "amount": {"$ref": "#/$defs/field_value"},
                            "kind": {"$ref": "#/$defs/field_value"},
                        },
                        "required": ["name", "rate", "base", "amount"],
                    },
                },
                "total": {"$ref": "#/$defs/field_value"},
                "amount_due": {"$ref": "#/$defs/field_value"},
                "tds": {"$ref": "#/$defs/field_value"},
                "other_taxes": {"$ref": "#/$defs/field_value"},
            },
            "required": ["subtotal", "tax_lines", "total", "amount_due"],
        },
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page": {"type": "integer"},
                    "description": {"$ref": "#/$defs/field_value"},
                    "quantity": {"$ref": "#/$defs/field_value"},
                    "unit_price": {"$ref": "#/$defs/field_value"},
                    "amount": {"$ref": "#/$defs/field_value"},
                    "hsn_sac": {"$ref": "#/$defs/field_value"},
                    "discount": {"$ref": "#/$defs/field_value"},
                },
                "required": [
                    "page",
                    "description",
                    "quantity",
                    "unit_price",
                    "amount",
                ],
            },
        },
        "other_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "value": {"$ref": "#/$defs/field_value"},
                },
                "required": ["name", "value"],
            },
        },
    },
    "required": [
        "page_start",
        "page_end",
        "doc_type",
        "seller",
        "buyer",
        "document_id",
        "issue_date",
        "due_date",
        "currency",
        "po_number",
        "payment_terms",
        "billing_period",
        "totals",
        "line_items",
        "other_fields",
    ],
    "$defs": {
        "field_value": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "value": {"type": "string"},
                "source_text": {"type": "string"},
                "pages": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["value", "source_text", "pages"],
        }
    },
}

#: Per-segment chat response: one or more documents (self-healing splits).
_DOCUMENT_V2_ITEM_SCHEMA = {
    k: v for k, v in DOCUMENT_V2_JSON_SCHEMA.items() if k != "$defs"
}
SEGMENT_DOCUMENTS_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "documents": {
            "type": "array",
            "minItems": 1,
            "items": _DOCUMENT_V2_ITEM_SCHEMA,
        }
    },
    "required": ["documents"],
    "$defs": DOCUMENT_V2_JSON_SCHEMA["$defs"],
}


def overlapping_page_error_codes(
    documents: list[ExtractedDocument],
) -> list[str]:
    """Emit `overlapping_doc_ranges:<pages>` when predicted ranges overlap."""
    owned: dict[int, int] = {}
    overlaps: set[int] = set()
    for doc in documents:
        for p in range(int(doc.page_start), int(doc.page_end) + 1):
            if p in owned:
                overlaps.add(p)
            else:
                owned[p] = doc.page_start
    if not overlaps:
        return []
    pages = sorted(overlaps)
    # Compact contiguous runs: 3,4,5 -> 3-5
    parts: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return [f"overlapping_doc_ranges:{','.join(parts)}"]
