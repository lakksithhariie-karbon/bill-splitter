"""Mistral OCR provider: boundaries from blocks + structured extraction.

Implements the documented Mistral OCR API shape
(https://docs.mistral.ai/capabilities/document/, fetched 2026-08-11):

Request flow
  POST https://api.mistral.ai/v1/ocr
    { "model": "mistral-ocr-latest",
      "document": { "type": "document_url",
                    "document_url": "data:application/pdf;base64,<b64>" },
      "include_blocks": true,
      "confidence_scores_granularity": "word",
      "document_annotation_format": { "type": "json_schema",
        "json_schema": { "name": "...", "schema": {...} } } }
    -> { "pages": [...], "model": "...", "document_annotation": "<json string>",
         "usage_info": { "pages_processed": N, "doc_size_bytes": B } }

Limits (docs, 2026-08-11): 50 MB and 1000 pages per request. We reject larger
inputs with a clear error before calling.

Boundary rule (deliberately simple, one function so it is easy to tune):
  a new document starts on a page where a `title` block appears in the top
  region of the page AND the page's header/letterhead text differs from the
  previous page's. Page 1 always starts a document.

This provider DOES return a real confidence signal (per-page aggregate
confidence), unlike Extend, so we populate SplitDocument.confidence honestly
and never zero it.

Replay mode
  A fixture is used ONLY when passed explicitly via fixture_path=... to the
  constructor. There is deliberately NO environment-variable path: an env var
  silently turning the live app into replay is how fake data reached the
  review screen. Replay stays available for tests and the CLI, never as an
  implicit default for the web app.
"""

from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from pdfsplit.chat import ChatClient, estimate_chat_usd
from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings
from pdfsplit.extraction import (
    DOCUMENT_V2_JSON_SCHEMA,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SEGMENT_DOCUMENTS_JSON_SCHEMA,
    ExtractionResult,
    ExtractedDocument,
    ExtractedDocumentV2,
    LineItem,
    apply_grounding,
    apply_grounding_v2,
    apply_totals_reconciliation,
    normalize_extracted_document_v2,
    coverage_error_codes,
    document_v2_from_dict,
    overlapping_page_error_codes,
    to_legacy_headers,
)
from pdfsplit.providers import SplitterProvider
from pdfsplit.schema import SplitDocument, SplitResult

#: Production base URL (docs, 2026-08-11).
BASE_URL = "https://api.mistral.ai"
#: Model id. Pinning the latest generation; the task specifies this exact id.
MODEL = "mistral-ocr-latest"
#: Max request size (docs, 2026-08-11).
MAX_BYTES = 50 * 1024 * 1024
#: Max pages per request (docs, 2026-08-11).
MAX_PAGES = 1000
#: Top region threshold (y coordinate) for a title block to count as a header.
TOP_REGION_Y = 200


def _chat_rates_for_model(
    settings: Settings, model_id: str
) -> tuple[float, float]:
    """Pick input/output USD-per-MTok rates for a chat model id."""
    mid = (model_id or "").lower()
    if "medium" in mid or "large" in mid:
        # Mistral Medium list rates (USD/MTok).
        return (1.5, 7.5)
    return (
        float(settings.mistral_chat_input_usd_per_mtok),
        float(settings.mistral_chat_output_usd_per_mtok),
    )


#: JSON schema for the annotated extraction (single-call approach).
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_start": {"type": "integer"},
                    "page_end": {"type": "integer"},
                    "doc_type": {"type": "string"},
                    "vendor_name": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "invoice_date": {"type": "string"},
                    "currency": {"type": "string"},
                    "gstin": {"type": "string"},
                    "total_amount": {"type": "string"},
                    "tax_amount": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "page": {"type": "integer"},
                                "description": {"type": "string"},
                                "quantity": {"type": "string"},
                                "unit_price": {"type": "string"},
                                "amount": {"type": "string"},
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
                },
                "required": ["page_start", "page_end", "doc_type", "line_items"],
            },
        }
    },
    "required": ["documents"],
}


def _normalise_class(text: str) -> str:
    """Normalise a title/class string to a snake_case class name."""
    if not text:
        return "unknown"
    # Strip markdown heading markers and the trailing "| packet ..." suffix.
    cleaned = text.lstrip("#").strip()
    if "|" in cleaned:
        cleaned = cleaned.split("|", 1)[0].strip()
    words = "".join(c if c.isalnum() else " " for c in cleaned).split()
    return "_".join(w.lower() for w in words) or "unknown"


def _page_header_text(page: dict) -> str:
    """The header/letterhead text of a page: the top-region title block.

    Markdown heading markers (# / ##) are stripped so a heading-level change
    (e.g. `# MONTHLY REPORT` vs `## MONTHLY REPORT`) does not create a false
    document boundary — only a change in the document identity should.
    """
    for block in page.get("blocks") or []:
        if block.get("type") == "title" and block.get("top_left_y", 9999) < TOP_REGION_Y:
            content = (block.get("content") or "").strip()
            return content.lstrip("#").strip()
    return ""


def _page_confidence(page: dict) -> float:
    """Page-level aggregate confidence Mistral returns for a page."""
    cs = page.get("confidence_scores") or {}
    return float(cs.get("average_page_confidence_score", 0.0) or 0.0)


def find_document_boundaries(pages: list[dict]) -> list[int]:
    """Return the 1-indexed start pages of each document.

    Heuristic (deliberately simple, tune after seeing real results):
      a new document starts on a page where a `title` block appears in the top
      region of the page AND the page's header/letterhead text differs from the
      previous page's. Page 1 always starts a document.
    """
    starts = [1]
    prev_header = _page_header_text(pages[0]) if pages else ""
    for i in range(1, len(pages)):
        header = _page_header_text(pages[i])
        if header and header != prev_header:
            starts.append(i + 1)
        prev_header = header
    return starts


#: Head/tail of OCR markdown shown to the boundary judge (identity + totals).
_BOUNDARY_HEAD_CHARS = 800
_BOUNDARY_TAIL_CHARS = 300
#: Chunk the judge call when the user message would exceed this many chars
#: (~roughly 25–30k tokens with overhead). Never drop pages — overlap windows.
_BOUNDARY_USER_CHAR_BUDGET = 90_000
_BOUNDARY_CHUNK_PAGES = 16
_BOUNDARY_CHUNK_OVERLAP = 1


def _page_markdown_head_tail(
    markdown: str,
    *,
    head_chars: int = _BOUNDARY_HEAD_CHARS,
    tail_chars: int = _BOUNDARY_TAIL_CHARS,
) -> tuple[str, str]:
    """Return (head, tail) slices of page markdown for the boundary judge.

    Short pages may overlap head and tail — that is intentional so the totals
    block is always visible in ``markdown_tail``.
    """
    md = (markdown or "").strip()
    if not md:
        return "", ""
    head = md[:head_chars]
    tail = md[-tail_chars:] if len(md) > tail_chars else md
    return head, tail


def page_boundary_summaries(pages: list[dict]) -> list[dict[str, Any]]:
    """Per-page summary for the boundary LLM judge.

    Structured hints (title / doc_number / …) plus head+tail OCR markdown so
    the model sees real vendor / invoice-number / totals text even when
    regexes miss table cells or non-standard labels.
    """
    out: list[dict[str, Any]] = []
    for i, page in enumerate(pages):
        md = (page.get("markdown") or "").strip()
        first_line = md.split("\n", 1)[0].strip() if md else ""
        evidence = extract_boundary_evidence(md)
        head, tail = _page_markdown_head_tail(md)
        out.append(
            {
                "page": i + 1,
                "title": _page_header_text(page),
                "first_line": first_line[:200],
                "doc_number": evidence["doc_number"],
                "date_line": evidence["date_line"],
                "total_line": evidence["total_line"],
                "markdown_head": head,
                "markdown_tail": tail,
            }
        )
    return out


#: Regexes for discriminating boundary evidence (first match wins).
#: Same labels as naming — require No/Number/# so a title is not a hit.
_RE_DOC_NUMBER = re.compile(
    r"(?:"
    r"(?:tax\s*)?invoice\s*(?:no\.?|number|#)|"
    r"bill\s*(?:no\.?|number|#)|"
    r"challan\s*(?:no\.?|number|#)|"
    r"voucher\s*(?:no\.?|number|#)|"
    r"doc(?:ument)?\s*(?:no\.?|number|#)|"
    r"debit\s*note\s*(?:no\.?|number|#)|"
    r"credit\s*note\s*(?:no\.?|number|#)|"
    r"contract\s*note\s*(?:no\.?|number|#)|"
    r"ref(?:erence)?\s*(?:no\.?|number|#)|"
    r"\binv\.?\s*(?:no\.?|#)"
    r")",
    re.IGNORECASE,
)
_RE_DATE = re.compile(r"date", re.IGNORECASE)
_RE_TOTAL = re.compile(r"total\s*(?:amount)?\s*\(?.*?\)?", re.IGNORECASE)


def extract_boundary_evidence(markdown: str) -> dict[str, str | None]:
    """Pull up to three discriminating evidence strings from page markdown.

    ``doc_number`` prefers the bare identifier (INV-…) when extractable; the
    full labelled line is kept only when the id cannot be isolated (judge still
    sees a useful hint).
    """
    from pdfsplit.aia.filenames import extract_doc_number_id

    lines = [ln.strip() for ln in (markdown or "").splitlines() if ln.strip()]
    doc_number = None
    date_line = None
    total_line = None
    for ln in lines:
        if doc_number is None and _RE_DOC_NUMBER.search(ln):
            ident = extract_doc_number_id(ln)
            # Skip bare headings ("TAX INVOICE") with no identifier token.
            if ident:
                doc_number = ident[:120]
        if date_line is None and _RE_DATE.search(ln):
            date_line = ln[:120]
        if doc_number is not None and date_line is not None:
            break
    # Prefer a total near the end of the page (invoice totals usually trail).
    for ln in reversed(lines):
        if _RE_TOTAL.search(ln):
            total_line = ln[:120]
            break
    return {
        "doc_number": doc_number,
        "date_line": date_line,
        "total_line": total_line,
    }


def starts_to_segments(
    starts: list[int], n_pages: int, types: dict[int, str] | None = None
) -> list[dict[str, Any]]:
    """Convert start pages into inclusive segments covering 1..n_pages."""
    if n_pages <= 0:
        return []
    clean = sorted({1, *[s for s in starts if 1 <= s <= n_pages]})
    if not clean or clean[0] != 1:
        clean = [1, *[s for s in clean if s > 1]]
    types = types or {}
    segments: list[dict[str, Any]] = []
    for i, start in enumerate(clean):
        end = clean[i + 1] - 1 if i + 1 < len(clean) else n_pages
        segments.append(
            {
                "page_start": start,
                "page_end": end,
                "doc_type": types.get(start) or "unknown",
            }
        )
    return segments


def detect_segment_disagreements(
    confirmed_segments: list[dict[str, Any]],
    documents: list[Any],
) -> list[dict[str, Any]]:
    """Flag when extraction subdivides a human-confirmed segment.

    Does not silently rewrite boundaries — returns proposed splits so the UI
    can ask the human. A disagreement is any confirmed segment that yields
    more than one document, or a single document whose range does not exactly
    match the confirmed segment.
    """
    disagreements: list[dict[str, Any]] = []
    for seg in confirmed_segments:
        ps = int(seg["page_start"])
        pe = int(seg["page_end"])
        matching = [
            d
            for d in documents
            if int(getattr(d, "page_start", 0)) >= ps
            and int(getattr(d, "page_end", 0)) <= pe
        ]
        if not matching:
            continue
        if len(matching) == 1:
            d0 = matching[0]
            if int(d0.page_start) == ps and int(d0.page_end) == pe:
                continue
        disagreements.append(
            {
                "page_start": ps,
                "page_end": pe,
                "proposed_splits": [
                    {
                        "page_start": int(d.page_start),
                        "page_end": int(d.page_end),
                        "doc_type": getattr(d, "doc_type", None) or "unknown",
                    }
                    for d in matching
                ],
            }
        )
    return disagreements


def _clip_judge_reason(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    if len(s) > 200:
        return s[:199] + "…"
    return s


def _parse_judge_confidence(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _justification_from_obj(obj: dict[str, Any], page: int) -> dict[str, Any] | None:
    reason = obj.get("reason")
    conf = _parse_judge_confidence(obj.get("confidence"))
    if not (isinstance(reason, str) and reason.strip()) and conf is None:
        return None
    out: dict[str, Any] = {"page": page}
    if isinstance(reason, str) and reason.strip():
        out["reason"] = _clip_judge_reason(reason)
    if conf is not None:
        out["confidence"] = conf
    return out


def parse_boundary_llm_response(
    data: dict, n_pages: int, heuristic_starts: list[int]
) -> tuple[
    list[int],
    dict[int, str],
    dict[int, dict[str, str]],
    dict[int, dict[str, Any]],
]:
    """Parse boundary chat JSON into starts, types, naming, and justifications.

    Naming hints (``vendor``, ``document_number``) are optional extras from the
    same judge call — grounded later; never trusted raw for filenames.

    Justifications (``reason``, ``confidence``) live on ``boundaries`` only —
    they answer why a new document STARTS at that page. Segment objects may
    describe the bill; they must not carry the cut reason. Trace-only —
    they must not replace Layer-1 derived confidence on final segments.
    """
    starts: list[int] = []
    types: dict[int, str] = {}
    naming: dict[int, dict[str, str]] = {}
    justifications: dict[int, dict[str, Any]] = {}
    segments = data.get("segments") or data.get("documents") or []
    if isinstance(segments, list) and segments:
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            start = int(seg.get("page_start") or seg.get("start_page") or 0)
            if start < 1:
                continue
            starts.append(start)
            dtype = seg.get("doc_type") or seg.get("type")
            if dtype:
                types[start] = _normalise_class(str(dtype))
            hint: dict[str, str] = {}
            vendor = seg.get("vendor") or seg.get("vendor_name")
            if isinstance(vendor, str) and vendor.strip():
                hint["vendor"] = vendor.strip()
            doc_no = (
                seg.get("document_number")
                or seg.get("invoice_number")
                or seg.get("doc_number")
            )
            if isinstance(doc_no, str) and doc_no.strip():
                hint["document_number"] = doc_no.strip()
            if hint:
                naming[start] = hint
            # Reason/confidence belong on ``boundaries``, not on segments.
    else:
        raw_starts = data.get("start_pages") or data.get("starts") or []
        for s in raw_starts:
            try:
                starts.append(int(s))
            except (TypeError, ValueError):
                continue
    bounds = data.get("boundaries") or []
    if isinstance(bounds, list):
        for bound in bounds:
            if not isinstance(bound, dict):
                continue
            page = int(bound.get("page") or bound.get("page_start") or 0)
            if page < 1:
                continue
            hit = _justification_from_obj(bound, page)
            if hit:
                justifications[page] = hit
    starts = sorted({s for s in starts if 1 <= s <= n_pages})
    if not starts or starts[0] != 1:
        # Invalid — caller should fall back.
        raise ValueError("boundary_llm: missing page 1 start")
    # Ensure coverage continuity is possible (starts within range).
    if max(starts) > n_pages:
        raise ValueError("boundary_llm: start beyond page count")
    justifications = {
        page: row
        for page, row in justifications.items()
        if 1 <= page <= n_pages
    }
    return starts, types, naming, justifications


#: Judge system prompt. A "document" is one bill / one voucher, not a
#: logical document type. Annexures and signature pages stay with the bill.
_BOUNDARY_JUDGE_SYSTEM = (
    "You split a multi-page OCR packet into logical documents. "
    "Decide, for each adjacent pair, whether page N+1 begins a NEW "
    "document or continues the previous one. "
    "A document here means everything belonging to ONE BILL that an "
    "accountant would enter as one voucher. That INCLUDES its continuation "
    "pages, statutory annexures and tax schedules, terms and conditions "
    "printed by the same supplier, and signature or declaration pages — "
    "even when those pages repeat the letterhead, carry their own totals, "
    "or look self-contained. "
    "A new document starts only when a DIFFERENT BILL begins: a different "
    "supplier, or the same supplier with a different bill reference. "
    "Return JSON with key `segments`: an array of "
    "{page_start, page_end, doc_type, vendor, document_number}. "
    "Rules: page 1 always starts a document; every page 1..N must be "
    "covered exactly once with contiguous non-overlapping ranges. "
    "A complete invoice face (header/letterhead + line items + totals) "
    "does not by itself mean the next page is a new document — the next "
    "page may be an annexure, tax schedule, terms, or signature page of "
    "the SAME bill. "
    "Same letterhead / same vendor on consecutive pages are SEPARATE "
    "documents only when the bill reference differs (or the supplier "
    "differs). Own totals or a repeated header are not enough. "
    "When unsure whether a page is a new bill or an annexure of the same "
    "bill, keep it with the previous bill. "
    "Use markdown_head (identity) and markdown_tail (totals) — do not "
    "rely only on the structured hint fields. "
    "doc_type is a short snake_case guess (invoice, tax_invoice, "
    "debit_note, contract_note, terms_and_conditions, other). "
    "vendor is the supplier/letterhead name as printed (or empty string). "
    "document_number is the document's own reference number as printed, "
    "whatever the page calls it (Invoice No, Contract Note No, Bill No, "
    "Challan No, Voucher No, Debit Note Number, Document No, Ref No). "
    "Copy it VERBATIM. Empty string if absent. Never use GSTIN, PAN, "
    "CIN, order numbers, PO numbers, or client codes. "
    "Never invent. "
    "A page that only reprints the letterhead above a signature block, "
    "declaration, terms, or 'yours faithfully' closing — with no trades "
    "or totals — CONTINUES the previous document. "
    "Also return `boundaries`: one object per proposed START page "
    "(including page 1). `reason` answers: why does a NEW document "
    "START at this page? Not a description of what the page contains. "
    "Page 1: it is the first page. "
    "`confidence` is your 0-1 belief that this start is correct. "
    "Do not emit a boundary for a continuation page. "
    "HARD CONSTRAINTS below are deterministic and MUST NOT be violated: "
    "a MUST-start page must begin a segment; a MUST-NOT-start page must "
    "not begin a segment (it continues the previous document)."
)

#: Repeated at the end of every judge user message (single-window and chunked).
_BOUNDARY_JUDGE_USER_TAIL = (
    "For each adjacent pair, decide if the later page starts a NEW "
    "document. A document is one bill (one voucher): annexures, tax "
    "schedules, terms, and signature pages of the same supplier and bill "
    "reference CONTINUE even when they repeat the letterhead or carry "
    "their own totals. A new document starts only when a different bill "
    "begins. "
    "Emit one `boundaries` entry per start page. Each `reason` must "
    "justify the cut — why a new document starts there — not describe "
    "the page. "
)


def find_boundaries_with_llm(
    pages: list[dict],
    chat: ChatClient | None = None,
    settings: Settings | None = None,
    *,
    use_constraints: bool = True,
    use_signature_signal: bool = False,
    trace: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """LLM-assisted boundaries seeded by the heuristic + Layer-1 constraints.

    Returns (segments, errors). On any failure, falls back to the heuristic
    (with constraints enforced when enabled) and adds error code
    ``boundary_llm_fallback``.

    Each segment includes ``confidence``, ``evidence``, and ``signals`` when
    Layer 1 is enabled (TASK 3 — show the reviewer why).

    Large packets are judged in overlapping page windows so every uploaded
    page is considered — never silently drop pages.
    """
    from pdfsplit.boundary_constraints import (
        annotate_segments_with_evidence,
        collect_boundary_constraints,
        enforce_constraints_on_starts,
        explain_constraint_conflicts,
        format_constraints_for_prompt,
        _resolve_constraint_conflicts,
    )

    settings = settings or default_settings
    errors: list[str] = []
    heuristic = find_document_boundaries(pages)
    n_pages = len(pages)
    if n_pages == 0:
        return [], errors

    raw_constraints = (
        collect_boundary_constraints(pages, use_signature=use_signature_signal)
        if use_constraints
        else []
    )
    constraints = (
        _resolve_constraint_conflicts(raw_constraints) if use_constraints else []
    )
    if trace is not None:
        trace["constraints"] = constraints
        trace["constraints_raw"] = raw_constraints
        trace["constraint_rows"] = explain_constraint_conflicts(raw_constraints)
        trace["heuristic_starts"] = list(heuristic)
    summaries = page_boundary_summaries(pages)
    constraint_block = format_constraints_for_prompt(constraints)
    system = _BOUNDARY_JUDGE_SYSTEM
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "page_start": {"type": "integer"},
                        "page_end": {"type": "integer"},
                        "doc_type": {"type": "string"},
                        "vendor": {"type": "string"},
                        "document_number": {"type": "string"},
                    },
                    "required": [
                        "page_start",
                        "page_end",
                        "doc_type",
                        "vendor",
                        "document_number",
                    ],
                },
            },
            "boundaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "page": {"type": "integer"},
                        "reason": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["page", "reason", "confidence"],
                },
            },
        },
        "required": ["segments", "boundaries"],
    }

    def _user_for(summaries_slice: list[dict], page_lo: int, page_hi: int) -> str:
        """Build judge user message for pages page_lo..page_hi inclusive."""
        seed = [s for s in heuristic if page_lo <= s <= page_hi]
        if page_lo not in seed:
            seed = [page_lo, *seed]
        # Constraints that touch this window.
        window_cons = [
            c
            for c in constraints
            if page_lo <= c.page <= page_hi
        ]
        return (
            f"Packet pages {page_lo}..{page_hi} of {n_pages} "
            f"(respond with segments covering ONLY these pages).\n"
            f"Heuristic seed start pages (within window): {seed}\n"
            f"HARD CONSTRAINTS (do not violate):\n"
            f"{format_constraints_for_prompt(window_cons)}\n"
            f"Per-page summaries (JSON):\n"
            f"{json.dumps(summaries_slice, ensure_ascii=False)}\n"
            + _BOUNDARY_JUDGE_USER_TAIL
            + "Emit segments covering every page in this window. "
            "Emit one `boundaries` entry per start page with a one-sentence "
            "reason and 0-1 confidence."
        )

    def _finalize(
        starts: list[int],
        types: dict[int, str],
        llm_used: bool,
        naming: dict[int, dict[str, str]] | None = None,
    ):
        if use_constraints:
            starts = enforce_constraints_on_starts(starts, n_pages, constraints)
        segments = starts_to_segments(starts, n_pages, types)
        naming = naming or {}
        for seg in segments:
            hint = naming.get(int(seg["page_start"])) or {}
            if hint.get("vendor"):
                seg["vendor"] = hint["vendor"]
            if hint.get("document_number"):
                seg["document_number"] = hint["document_number"]
        covered: set[int] = set()
        for seg in segments:
            for p in range(seg["page_start"], seg["page_end"] + 1):
                if p in covered:
                    raise ValueError("boundary_llm: overlapping segments")
                covered.add(p)
        if covered != set(range(1, n_pages + 1)):
            raise ValueError("boundary_llm: incomplete coverage")
        return annotate_segments_with_evidence(
            segments, constraints, llm_used=llm_used
        )

    def _proposal(
        starts: list[int],
        types: dict[int, str],
        naming: dict[int, dict[str, str]] | None,
        justifications: dict[int, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        segs = starts_to_segments(starts, n_pages, types)
        naming = naming or {}
        justifications = justifications or {}
        for seg in segs:
            hint = naming.get(int(seg["page_start"])) or {}
            if hint.get("vendor"):
                seg["vendor"] = hint["vendor"]
            if hint.get("document_number"):
                seg["document_number"] = hint["document_number"]
            just = justifications.get(int(seg["page_start"])) or {}
            if just.get("reason"):
                seg["judge_reason"] = just["reason"]
            if just.get("confidence") is not None:
                seg["judge_confidence"] = just["confidence"]
            # Derived Layer-1 confidence is attached later on final segments.
            # Leave it empty on the raw proposal so the trace can show both.
            seg.setdefault("evidence", [])
            seg.setdefault("confidence", None)
        return segs

    def _fill_judge_trace(
        *,
        called: bool,
        starts: list[int],
        types: dict[int, str] | None = None,
        naming: dict[int, dict[str, str]] | None = None,
        justifications: dict[int, dict[str, Any]] | None = None,
        model: str = "",
        usage: dict[str, Any] | None = None,
        latency_ms: float | None = None,
        reason: str = "",
        cost_usd: float | None = None,
    ) -> None:
        if trace is None:
            return
        trace["judge_starts"] = list(starts)
        trace["judge"] = {
            "called": called,
            "model": model,
            "usage": usage or {},
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "cost_usd": cost_usd,
            "reason": reason,
            "starts": list(starts),
            "segments": _proposal(
                starts, types or {}, naming, justifications
            ),
        }

    def _windows() -> list[tuple[int, int]]:
        """1-indexed inclusive page windows; overlap so boundaries aren't lost."""
        full_user = _user_for(summaries, 1, n_pages)
        if (
            len(full_user) <= _BOUNDARY_USER_CHAR_BUDGET
            and n_pages <= _BOUNDARY_CHUNK_PAGES * 2
        ):
            return [(1, n_pages)]
        wins: list[tuple[int, int]] = []
        start = 1
        while start <= n_pages:
            end = min(n_pages, start + _BOUNDARY_CHUNK_PAGES - 1)
            wins.append((start, end))
            if end >= n_pages:
                break
            start = max(start + 1, end - _BOUNDARY_CHUNK_OVERLAP + 1)
        return wins

    try:
        client = chat or ChatClient(settings=settings)
        all_starts: set[int] = {1}
        all_types: dict[int, str] = {}
        all_naming: dict[int, dict[str, str]] = {}
        all_just: dict[int, dict[str, Any]] = {}
        usage_acc: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        judge_model = ""
        t_judge = time.perf_counter()
        windows = _windows()
        if len(windows) > 1:
            errors.append(f"boundary_judge_chunked:{len(windows)}")
        for lo, hi in windows:
            slice_sums = [s for s in summaries if lo <= int(s["page"]) <= hi]
            offset = lo - 1  # map absolute page → 1..window
            local_sums = []
            for s in slice_sums:
                row = dict(s)
                row["page"] = int(s["page"]) - offset
                local_sums.append(row)
            window_n = hi - lo + 1
            local_heuristic = sorted(
                {
                    1,
                    *[
                        s - offset
                        for s in heuristic
                        if lo <= s <= hi
                    ],
                }
            )
            # Remap constraints detail pages stay human-readable via absolute
            # numbers in the user text; structured list uses absolute too.
            user = (
                f"Packet pages {lo}..{hi} of {n_pages} "
                f"(page numbers below are LOCAL 1..{window_n} for this window; "
                f"LOCAL page 1 = global page {lo}).\n"
                f"Heuristic seed LOCAL start pages: {local_heuristic}\n"
                f"HARD CONSTRAINTS (global page numbers — map LOCAL = global-{offset}):\n"
                f"{format_constraints_for_prompt([c for c in constraints if lo <= c.page <= hi])}\n"
                f"Per-page summaries (JSON, LOCAL page ids):\n"
                f"{json.dumps(local_sums, ensure_ascii=False)}\n"
                + _BOUNDARY_JUDGE_USER_TAIL
                + f"Emit segments covering LOCAL pages 1..{window_n} exactly. "
                "Emit one `boundaries` entry per LOCAL start page with a "
                "one-sentence reason and 0-1 confidence."
            )
            result = client.complete_json(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                json_schema=schema,
                schema_name="document_boundaries",
            )
            if not result.used_json_schema:
                errors.append("boundary_json_schema_fallback")
            starts, types, naming, justifications = parse_boundary_llm_response(
                result.data, window_n, local_heuristic
            )
            for s in starts:
                all_starts.add(s + offset)
            for k, v in types.items():
                all_types[k + offset] = v
            for k, v in naming.items():
                all_naming[k + offset] = v
            for k, v in justifications.items():
                all_just[k + offset] = v
            judge_model = result.model or judge_model
            u = result.usage or {}
            usage_acc["prompt_tokens"] += int(
                u.get("prompt_tokens") or u.get("input_tokens") or 0
            )
            usage_acc["completion_tokens"] += int(
                u.get("completion_tokens") or u.get("output_tokens") or 0
            )
        judge_starts = sorted(all_starts)
        latency_ms = (time.perf_counter() - t_judge) * 1000.0
        cost = None
        if trace is not None:
            from pdfsplit.boundary_trace import estimate_judge_cost

            cost = estimate_judge_cost(usage_acc)
        _fill_judge_trace(
            called=True,
            starts=judge_starts,
            types=all_types,
            naming=all_naming,
            justifications=all_just,
            model=judge_model,
            usage=usage_acc,
            latency_ms=latency_ms,
            cost_usd=cost,
        )
        return _finalize(
            judge_starts, all_types, llm_used=True, naming=all_naming
        ), errors
    except Exception:  # noqa: BLE001 — defensive fallback is the product requirement
        errors.append("boundary_llm_fallback")
        _fill_judge_trace(
            called=False,
            starts=list(heuristic),
            reason="boundary_llm_fallback",
            model="(not called)",
        )
        return (
            _finalize(heuristic, {}, llm_used=False, naming=None),
            errors,
        )

_EXTRACT_SYSTEM = (
    "You extract structured fields from OCR markdown for one or more logical "
    "documents inside a page segment. The segment may contain more than one "
    "logical document. Return JSON with key `documents`: an array of document "
    "objects. Return one object per logical document, each with its true "
    "page_start/page_end within the segment. Never merge two invoices into "
    "one object. Extract every piece of information on the pages. "
    "source_text must be verbatim as printed, keeping currency symbols, "
    "commas, and date formatting exactly; value may be a normalized form "
    "(ISO date, plain number). Never invent. Leave empty strings when absent. "
    "Tag every FieldValue with the exact page it appears on. "
    "line_items are GOODS AND SERVICES rows only — the purchasable or "
    "billable items. Do NOT put subtotal, tax, GST/CGST/SGST/IGST add-on, "
    "rounding, discount-summary, or grand-total rows in line_items; those "
    "amounts belong in the totals block (subtotal, tax_lines, tds, "
    "other_taxes, total, amount_due). Rows whose description is only a tax "
    "name (GST, CGST, SGST, IGST, TDS) belong in tax_lines (with kind gst or "
    "tds), never in line_items. If a summary row cannot be placed in "
    "totals, put it in other_fields — never drop it, and never file it as a "
    "line item. Fill hsn_sac and discount on goods/services lines when "
    "printed. For multi-page documents take totals from the page where the "
    "total block appears. "
    "tax_lines holds INDIVIDUAL tax components only (CGST, SGST, IGST, VAT, "
    "TDS, cess, and invoice-level Discount with kind discount). Take tax "
    "lines from the tax / totals block — never from the goods/services item "
    "table (item-table cells like PT, HSN, qty are not tax lines). Rows that "
    "total other tax rows — Total GST, Total Tax, Total GST Amount — are NOT "
    "tax lines; the sum is derived, do not emit them. "
    "totals.total / Grand Total is the invoice value BEFORE payments already "
    "made. totals.amount_due / Amount Due / Balance Due is what remains "
    "AFTER payments (and after TDS when TDS reduces what is payable). When "
    "a Payments, Amount Paid, or Amount Due line exists, total and "
    "amount_due are different numbers and BOTH must be filled — never put "
    "the amount due into total. "
    "Anything that does not fit canonical fields goes "
    "into other_fields — never drop it. Non-invoice pages use doc_type "
    "terms_and_conditions or other with empty fields rather than being "
    "skipped. When present on the page, also fill: narration; place of "
    "supply (source_of_supply / destination_of_supply); shipping_address; "
    "reverse_charge; totals.tds and totals.other_taxes; tax_lines[].kind "
    "(gst | tds | discount | other | unknown); and per line_item discount. "
    "Do NOT extract bank/payment rails, SKU, UOM, or item_type — those are "
    "out of scope."
)

class MistralSplitterProvider(SplitterProvider):
    """Mistral OCR adapter: boundaries from blocks + structured extraction."""

    name = "mistral"

    def __init__(
        self,
        settings: Settings | None = None,
        fixture_path: str | Path | None = None,
        annotated_fixture_path: str | Path | None = None,
        chat_client: ChatClient | None = None,
        ocr_response: dict | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.model_version = f"mistral-{MODEL}"
        # A fixture is used ONLY when passed explicitly. No env-var fallback.
        self._fixture_path = Path(fixture_path) if fixture_path else None
        self._annotated_fixture_path = (
            Path(annotated_fixture_path) if annotated_fixture_path else None
        )
        self._chat_client = chat_client
        # Optional injected OCR JSON for offline chat-mode tests.
        self._ocr_response = ocr_response
        self._trace_meta: dict[str, str] = {}

    # -- public API ----------------------------------------------------------

    def split(self, input_ref: str | Path | bytes) -> SplitResult:
        """Split an input PDF into logical documents using Mistral blocks."""
        if self._fixture_path is not None:
            return self._split_from_fixture(input_ref)
        if not self.settings.has_mistral_credentials():
            raise ValueError(
                "mistral: MISTRAL_API_KEY not set and no fixture configured. "
                "Set MISTRAL_API_KEY for a live call, or pass fixture_path "
                "for replay."
            )
        return self._split_live(input_ref)

    def extract(
        self,
        input_ref: str | Path | bytes,
        *,
        precomputed_segments: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        """Run extraction (annotated legacy or chat understanding).

        ``precomputed_segments`` — when provided, skip the boundary LLM call
        and use these segments for the understanding arm (A/B shared cuts).
        """
        mode = (self.settings.mistral_extraction_mode or "chat").lower()
        # Explicit annotated fixture always uses the annotated mapper.
        if self._annotated_fixture_path is not None:
            return self._extract_from_fixture(input_ref)
        if not self.settings.has_mistral_credentials() and self._ocr_response is None:
            raise ValueError(
                "mistral: MISTRAL_API_KEY not set and no fixture configured. "
                "Set MISTRAL_API_KEY for a live call, or pass "
                "annotated_fixture_path for replay."
            )
        if mode == "annotated":
            return self._extract_live(input_ref)
        return self._extract_live_chat(
            input_ref,
            precomputed_segments=precomputed_segments,
        )

    def analyze(self, input_ref: str | Path | bytes) -> dict[str, Any]:
        """Stage 1: OCR + document boundaries only (no field extraction).

        Sets ``self._ocr_response`` so a later ``extract(..., precomputed_segments=)``
        can reuse page markdown without a second OCR call. Returns a dict with
        segments, pages_processed, errors, raw_response_path, ocr_response,
        model_version, and latency_ms.
        """
        start = time.perf_counter()
        replay = False
        if self._ocr_response is not None:
            resp = self._ocr_response
        elif self._annotated_fixture_path is not None:
            resp = json.loads(self._annotated_fixture_path.read_text())
            self._ocr_response = resp
            replay = True
        elif self._fixture_path is not None:
            resp = json.loads(self._fixture_path.read_text())
            self._ocr_response = resp
            replay = True
        else:
            if not self.settings.has_mistral_credentials():
                raise ValueError(
                    "mistral: MISTRAL_API_KEY not set and no fixture configured. "
                    "Set MISTRAL_API_KEY for a live call, or pass fixture_path "
                    "for replay."
                )
            data, name = self._read_input(input_ref)
            self._check_limits(data, name)
            resp = self._ocr(data, include_blocks=True, annotate=False)
            self._ocr_response = resp

        pages = resp.get("pages") or []
        usage = resp.get("usage_info") or {}
        pages_processed = int(usage.get("pages_processed", 0) or len(pages))

        from pdfsplit.blank_pages import (
            detect_blank_pages,
            possible_blank_pages,
            strip_pages_from_segments,
        )
        from pdfsplit.boundary_trace import boundary_debug_enabled

        trace: dict[str, Any] | None = {} if boundary_debug_enabled() else None

        chat = self._chat_client
        if chat is None and self.settings.has_mistral_credentials():
            chat = ChatClient(settings=self.settings)
        segments, errors = find_boundaries_with_llm(
            pages, chat=chat, settings=self.settings, trace=trace
        )
        # Confidence comes from Layer-1 evidence (already on segments).
        # Do not overwrite with OCR page-confidence opinion.
        # Resolve + cache grounded vendor/document_number once (deterministic).
        overlay: dict[str, Any] = {}
        pre_overlay: list[dict[str, Any]] = [dict(s) for s in segments]
        try:
            from pdfsplit.aia.filenames import attach_names_to_segments
            from pdfsplit.boundary_overlay import attach_overlay_and_extent

            packet_stem = "packet"
            try:
                if isinstance(input_ref, (str, Path)):
                    packet_stem = Path(input_ref).stem or "packet"
            except Exception:  # noqa: BLE001
                packet_stem = "packet"
            attach_names_to_segments(
                segments, pages, packet_stem=packet_stem
            )
            segments, overlay, pre_overlay = attach_overlay_and_extent(
                segments, pages
            )
            attach_names_to_segments(
                segments, pages, packet_stem=packet_stem
            )
        except Exception:  # noqa: BLE001 — naming/overlay must never break analyze
            errors = [*errors, "naming_attach_fallback"]

        # Blank separators: auto-exclude after boundaries so coverage stays
        # documents ∪ excluded = 1..N. Never drop a faint-scan candidate.
        auto_excluded = detect_blank_pages(pages)
        blank_set = {int(r["page"]) for r in auto_excluded}
        if blank_set:
            segments = strip_pages_from_segments(segments, blank_set)
            pre_overlay = strip_pages_from_segments(pre_overlay, blank_set)

        try:
            from pdfsplit.boundary_review import (
                annotate_segments_with_review_status,
            )

            raw_constraints = (
                list(trace.get("constraints_raw") or []) if trace else []
            )
            if not raw_constraints:
                from pdfsplit.boundary_constraints import (
                    collect_boundary_constraints,
                )

                raw_constraints = collect_boundary_constraints(pages)
            segments = annotate_segments_with_review_status(
                segments,
                raw_constraints=raw_constraints,
                pages=pages,
            )
            # Text-empty but not sparse → do not exclude; nudge a look.
            for p in possible_blank_pages(pages):
                for seg in segments:
                    if int(seg["page_start"]) <= p <= int(seg["page_end"]):
                        if seg.get("review_status") != "needs_look":
                            seg["review_status"] = "needs_look"
                            reasons = list(seg.get("review_reasons") or [])
                            reasons.append(
                                f"page {p} looks empty to OCR — check the scan"
                            )
                            seg["review_reasons"] = reasons
        except Exception:  # noqa: BLE001
            errors = [*errors, "review_status_fallback"]

        model_version = self.model_version
        if replay:
            errors = [*errors, "replay_fixture"]
            model_version = f"replay-{self.model_version}"

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        out: dict[str, Any] = {
            "segments": segments,
            "pages_processed": pages_processed,
            "errors": errors,
            "raw_response_path": self._save_raw(resp),
            "ocr_response": resp,
            "model_version": model_version,
            "latency_ms": round(elapsed_ms, 1),
            "provider": self.name,
            "overlay": overlay,
            "pre_overlay_segments": pre_overlay,
            "auto_excluded_pages": auto_excluded,
        }
        if trace is not None:
            out["_boundary_trace"] = self._build_boundary_trace(
                pages=pages,
                segments=segments,
                pre_overlay=pre_overlay,
                overlay=overlay,
                errors=errors,
                model_version=model_version,
                trace=trace,
            )
        return out

    def _build_boundary_trace(
        self,
        *,
        pages: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        pre_overlay: list[dict[str, Any]],
        overlay: dict[str, Any],
        errors: list[str],
        model_version: str,
        trace: dict[str, Any],
    ) -> dict[str, Any]:
        from pdfsplit.boundary_constraints import enforce_constraints_on_starts
        from pdfsplit.boundary_trace import (
            caught_by_tags,
            constraints_as_dicts,
            contradiction_warnings,
            overlay_pair_lines,
            page_evidence_rows,
            reconcile_lines,
        )

        constraints = list(trace.get("constraints") or [])
        judge = dict(trace.get("judge") or {})
        if errors and not judge.get("reason"):
            judge["reason"] = ", ".join(errors)
        judge_starts = list(trace.get("judge_starts") or judge.get("starts") or [])
        after_starts = [
            int(s["page_start"])
            for s in pre_overlay
            if int(s.get("page_start") or 0) >= 1
        ]
        n_pages = len(pages)
        if judge_starts and constraints:
            after_from_judge = enforce_constraints_on_starts(
                judge_starts, n_pages, constraints
            )
        else:
            after_from_judge = after_starts
        meta = getattr(self, "_trace_meta", None) or {}
        return {
            "session_id": meta.get("session_id") or "",
            "filename": meta.get("filename") or "",
            "page_count": n_pages,
            "provider": self.name,
            "model": model_version,
            "page_evidence": page_evidence_rows(pages),
            "constraints": (
                list(trace.get("constraint_rows") or [])
                or constraints_as_dicts(constraints)
            ),
            "judge": judge,
            "reconcile": reconcile_lines(
                judge_starts, after_from_judge, constraints, n_pages
            ),
            "overlay_pairs": overlay_pair_lines(pre_overlay, pages),
            "overlay": overlay,
            "final_segments": segments,
            "caught_by": caught_by_tags(segments, overlay, constraints),
            "warnings": contradiction_warnings(
                list(judge.get("segments") or []) or pre_overlay,
                pages,
            ),
        }

    # -- live path -----------------------------------------------------------

    def _split_live(self, input_ref: str | Path | bytes) -> SplitResult:
        start = time.perf_counter()
        data, name = self._read_input(input_ref)
        self._check_limits(data, name)
        resp = self._ocr(data, include_blocks=True, annotate=False)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return self._map_split_response(input_ref, resp, elapsed_ms)

    def _extract_live(self, input_ref: str | Path | bytes) -> ExtractionResult:
        start = time.perf_counter()
        data, name = self._read_input(input_ref)
        self._check_limits(data, name)
        resp = self._ocr(data, include_blocks=True, annotate=True)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return self._map_extract_response(input_ref, resp, elapsed_ms)

    def _extract_live_chat(
        self,
        input_ref: str | Path | bytes,
        *,
        precomputed_segments: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        """Architecture B: plain OCR → LLM boundaries → chat per segment."""
        start = time.perf_counter()
        if self._ocr_response is not None:
            resp = self._ocr_response
        else:
            data, name = self._read_input(input_ref)
            self._check_limits(data, name)
            resp = self._ocr(data, include_blocks=True, annotate=False)
            self._ocr_response = resp

        pages = resp.get("pages") or []
        page_markdowns = {
            p.get("index", 0) + 1: p.get("markdown", "") or "" for p in pages
        }
        n_pages = len(pages)
        usage = resp.get("usage_info") or {}
        pages_processed = int(usage.get("pages_processed", 0) or n_pages)

        chat = self._chat_client or ChatClient(settings=self.settings)
        errors: list[str] = []
        if precomputed_segments is not None:
            segments = list(precomputed_segments)
        else:
            # Boundaries always use the text chat model (cheap / same as chat arm).
            segments, errors = find_boundaries_with_llm(
                pages, chat=chat, settings=self.settings
            )

        docs_v2: list[ExtractedDocumentV2] = []
        chat_usd = 0.0
        prompt_tokens_total = 0
        completion_tokens_total = 0
        understand_model = self.settings.mistral_chat_model
        chat_raw_paths: list[str] = []
        input_rate, output_rate = _chat_rates_for_model(
            self.settings, understand_model
        )

        def _extract_one(
            seg: dict,
        ) -> tuple[list[ExtractedDocumentV2], dict, str, bool, str | None, list[str]]:
            local_errors: list[str] = []
            ps, pe = int(seg["page_start"]), int(seg["page_end"])
            parts = []
            for p in range(ps, pe + 1):
                parts.append(f"----- PAGE {p} -----\n{page_markdowns.get(p, '')}")
            markdown = "\n\n".join(parts)
            text_preamble = (
                f"Segment spans pages {ps}-{pe}. Suggested doc_type seed: "
                f"{seg.get('doc_type') or 'unknown'}.\n"
                "Return one documents[] entry per logical document in range.\n"
            )
            user_content = text_preamble + "\n" + markdown
            model_id = self.settings.mistral_chat_model

            result = chat.complete_json(
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                json_schema=SEGMENT_DOCUMENTS_JSON_SCHEMA,
                schema_name="document_extraction_v2_segment",
                model=model_id,
                timeout=float(self.settings.mistral_chat_timeout_s),
            )
            raw_path = self._save_raw(
                {
                    "data": result.data,
                    "usage": result.usage,
                    "model": result.model,
                    "raw_content": result.raw_content,
                    "used_json_schema": result.used_json_schema,
                }
            )
            raw_docs = result.data.get("documents")
            if raw_docs is None and isinstance(result.data, dict):
                # Tolerate a single bare document object.
                if "page_start" in result.data or "doc_type" in result.data:
                    raw_docs = [result.data]
            if not isinstance(raw_docs, list) or not raw_docs:
                raise ValueError(
                    f"chat_extract: no usable documents for segment {ps}-{pe}"
                )

            docs: list[ExtractedDocumentV2] = []
            for raw in raw_docs:
                if not isinstance(raw, dict):
                    continue
                doc = document_v2_from_dict(raw, ps, pe)
                start = max(ps, min(pe, int(doc.page_start)))
                end = max(ps, min(pe, int(doc.page_end)))
                if end < start:
                    continue
                conf = (
                    _page_confidence(pages[start - 1])
                    if 0 < start <= len(pages)
                    else 0.0
                )
                docs.append(
                    doc.model_copy(
                        update={
                            "page_start": start,
                            "page_end": end,
                            "doc_type": doc.doc_type
                            or seg.get("doc_type")
                            or "unknown",
                            "confidence": conf,
                        }
                    )
                )
            if not docs:
                raise ValueError(
                    f"chat_extract: all documents clamped empty for {ps}-{pe}"
                )
            return (
                docs,
                result.usage,
                result.model,
                result.used_json_schema,
                raw_path,
                local_errors,
            )

        workers = max(1, int(self.settings.mistral_chat_concurrency or 1))
        if segments:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_extract_one, seg) for seg in segments]
                for fut in as_completed(futures):
                    try:
                        (
                            docs,
                            usage_i,
                            model_i,
                            used_schema,
                            raw_path,
                            local_errors,
                        ) = fut.result()
                        docs_v2.extend(docs)
                        chat_usd += estimate_chat_usd(
                            usage_i,
                            input_usd_per_mtok=input_rate,
                            output_usd_per_mtok=output_rate,
                        )
                        prompt_tokens_total += int(
                            usage_i.get("prompt_tokens")
                            or usage_i.get("input_tokens")
                            or 0
                        )
                        completion_tokens_total += int(
                            usage_i.get("completion_tokens")
                            or usage_i.get("output_tokens")
                            or 0
                        )
                        understand_model = model_i or understand_model
                        if not used_schema:
                            if "chat_json_schema_fallback" not in errors:
                                errors.append("chat_json_schema_fallback")
                        if raw_path:
                            chat_raw_paths.append(raw_path)
                        errors.extend(local_errors)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"chat_extract_error:{exc}")

        docs_v2.sort(key=lambda d: d.page_start)
        docs_v2 = [normalize_extracted_document_v2(d) for d in docs_v2]
        # Grounding still uses OCR markdown (cross-perception disagreement signal).
        docs_v2 = apply_grounding_v2(docs_v2, page_markdowns)
        docs_v2 = apply_totals_reconciliation(docs_v2)
        from pdfsplit.packing import find_duplicate_doc_groups

        dup_errors, duplicate_groups = find_duplicate_doc_groups(docs_v2)
        errors.extend(dup_errors)
        legacy = [to_legacy_headers(d) for d in docs_v2]
        errors.extend(overlapping_page_error_codes(legacy))
        errors.extend(coverage_error_codes(legacy, pages_processed))

        segment_disagreements: list[dict[str, Any]] = []
        if precomputed_segments is not None:
            segment_disagreements = detect_segment_disagreements(
                precomputed_segments, docs_v2
            )

        ocr_usd = round(pages_processed * self.settings.mistral_ocr_usd_per_page, 6)
        chat_usd = round(chat_usd, 6)
        total = round(ocr_usd + chat_usd, 6)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return ExtractionResult(
            provider=self.name,
            model_version=self.model_version,
            documents=legacy,
            documents_v2=[d.model_dump() for d in docs_v2],
            pages_processed=pages_processed,
            cost_usd=total,
            cost_breakdown={
                "ocr_usd": ocr_usd,
                "chat_usd_estimated": chat_usd,
                "total_usd": total,
                "chat_model": understand_model,
                "chat_raw_paths": chat_raw_paths,
                "understanding_mode": "chat",
                "wall_ms": round(elapsed_ms, 1),
                "retries_used": int(getattr(chat, "retries_used", 0) or 0),
                "shared_segments": precomputed_segments is not None,
                "prompt_tokens": prompt_tokens_total,
                "completion_tokens": completion_tokens_total,
                "total_tokens": prompt_tokens_total + completion_tokens_total,
                "segment_disagreements": segment_disagreements,
            },
            raw_response_path=self._save_raw(resp),
            errors=errors,
            duplicate_groups=duplicate_groups,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )

    def _ocr(self, data: bytes, include_blocks: bool, annotate: bool) -> dict:
        b64 = base64.b64encode(data).decode()
        payload = {
            "model": MODEL,
            "document": {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
            },
            "include_blocks": include_blocks,
            "confidence_scores_granularity": "word",
        }
        if annotate:
            payload["document_annotation_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "invoice_extraction",
                    "schema": EXTRACTION_SCHEMA,
                },
            }
        # Transient 5xx from Mistral is common on large packets — retry briefly
        # then surface a clear ValueError (mapped to HTTP 400/502 by the app).
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{BASE_URL}/v1/ocr",
                    headers={
                        "Authorization": f"Bearer {self.settings.mistral_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=320,
                )
                if r.status_code >= 500:
                    last_exc = requests.HTTPError(
                        f"{r.status_code} Server Error for url: {r.url}",
                        response=r,
                    )
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    detail = (r.text or "").strip()[:240]
                    raise ValueError(
                        f"mistral OCR failed: HTTP {r.status_code}"
                        + (f" — {detail}" if detail else "")
                    )
                return r.json()
            except requests.Timeout as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        status = getattr(getattr(last_exc, "response", None), "status_code", None)
        raise ValueError(
            "mistral OCR failed after retries"
            + (f" (HTTP {status})" if status else "")
            + ". The OCR provider had an internal error — try Re-run detection."
        )
    @staticmethod
    def _read_input(input_ref):
        if isinstance(input_ref, bytes):
            return input_ref, "upload.pdf"
        p = Path(input_ref)
        return p.read_bytes(), p.name

    @staticmethod
    def _check_limits(data: bytes, name: str) -> None:
        if len(data) > MAX_BYTES:
            raise ValueError(
                f"mistral: {name} is {len(data)} bytes, exceeding the 50 MB limit."
            )
        from pypdf import PdfReader
        import io

        try:
            n = len(PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"mistral: not a readable PDF: {exc}")
        if n > MAX_PAGES:
            raise ValueError(
                f"mistral: {name} has {n} pages, exceeding the 1000-page limit."
            )

    # -- replay path ---------------------------------------------------------

    def _split_from_fixture(self, input_ref: str | Path | bytes) -> SplitResult:
        data = json.loads(self._fixture_path.read_text())
        saved_version = self.model_version
        self.model_version = f"replay-{saved_version}"
        try:
            result = self._map_split_response(input_ref, data, 0.0)
        finally:
            self.model_version = saved_version
        result.errors.append("replay_fixture")
        result.raw_response_path = str(self._fixture_path)
        return result

    def _extract_from_fixture(self, input_ref: str | Path | bytes) -> ExtractionResult:
        data = json.loads(self._annotated_fixture_path.read_text())
        saved_version = self.model_version
        self.model_version = f"replay-{saved_version}"
        try:
            result = self._map_extract_response(input_ref, data, 0.0)
        finally:
            self.model_version = saved_version
        result.errors.append("replay_fixture")
        result.raw_response_path = str(self._annotated_fixture_path)
        return result

    # -- mapping -------------------------------------------------------------

    def _map_split_response(
        self, input_ref, resp: dict, elapsed_ms: float
    ) -> SplitResult:
        pages = resp.get("pages") or []
        starts = find_document_boundaries(pages)
        documents: list[SplitDocument] = []
        for i, start in enumerate(starts):
            end = starts[i + 1] - 1 if i + 1 < len(starts) else len(pages)
            page = pages[start - 1]
            title = _page_header_text(page)
            documents.append(
                SplitDocument(
                    doc_type=_normalise_class(title),
                    start_page=start,
                    end_page=end,
                    confidence=_page_confidence(page),
                    text=page.get("markdown"),
                )
            )
        usage = resp.get("usage_info") or {}
        pages_processed = int(usage.get("pages_processed", 0) or 0)
        cost_usd = round(
            pages_processed * self.settings.mistral_ocr_usd_per_page, 6
        )
        return SplitResult(
            input_ref=str(input_ref),
            provider=self.name,
            model_version=self.model_version,
            documents=documents,
            latency_ms=round(elapsed_ms, 1),
            cost_usd=cost_usd,
            raw_response_path=self._save_raw(resp),
            errors=[],
        )

    def _map_extract_response(
        self, input_ref, resp: dict, elapsed_ms: float
    ) -> ExtractionResult:
        pages = resp.get("pages") or []
        page_markdowns = {p.get("index", 0) + 1: p.get("markdown", "") for p in pages}
        documents: list[ExtractedDocument] = []
        errors: list[str] = []

        annotation = resp.get("document_annotation")
        if not annotation:
            errors.append("no_document_annotation")
        else:
            try:
                parsed = json.loads(annotation) if isinstance(annotation, str) else annotation
                for d in parsed.get("documents", []):
                    items = [
                        LineItem(
                            page=int(it.get("page", 1)),
                            description=it.get("description", "") or "",
                            quantity=it.get("quantity", "") or "",
                            unit_price=it.get("unit_price", "") or "",
                            amount=it.get("amount", "") or "",
                        )
                        for it in d.get("line_items", [])
                    ]
                    documents.append(
                        ExtractedDocument(
                            page_start=int(d.get("page_start", 1)),
                            page_end=int(d.get("page_end", 1)),
                            doc_type=_normalise_class(d.get("doc_type") or "unknown"),
                            vendor_name=d.get("vendor_name", "") or "",
                            invoice_number=d.get("invoice_number", "") or "",
                            invoice_date=d.get("invoice_date", "") or "",
                            currency=d.get("currency", "") or "",
                            gstin=d.get("gstin", "") or "",
                            total_amount=d.get("total_amount", "") or "",
                            tax_amount=d.get("tax_amount", "") or "",
                            line_items=items,
                            confidence=0.0,
                        )
                    )
            except (ValueError, TypeError) as exc:
                errors.append(f"annotation_parse_error:{exc}")

        # Populate per-document confidence from the start page's aggregate score.
        for doc in documents:
            page = pages[doc.page_start - 1] if 0 < doc.page_start <= len(pages) else None
            if page is not None:
                doc.confidence = _page_confidence(page)

        # Hallucination check: flag any field whose value is not in the page text.
        documents = apply_grounding(documents, page_markdowns)

        usage = resp.get("usage_info") or {}
        pages_processed = int(usage.get("pages_processed", 0) or 0)
        # Loud coverage gate: never silently drop pages from a dense packet.
        errors.extend(coverage_error_codes(documents, pages_processed))
        # Annotated (Document AI) rate applies.
        cost_usd = round(pages_processed * self.settings.mistral_docai_usd_per_page, 6)

        return ExtractionResult(
            provider=self.name,
            model_version=self.model_version,
            documents=documents,
            pages_processed=pages_processed,
            cost_usd=cost_usd,
            raw_response_path=self._save_raw(resp),
            errors=errors,
        )

    def _save_raw(self, resp: dict) -> str | None:
        import tempfile

        try:
            fd, path = tempfile.mkstemp(prefix="mistral_raw_", suffix=".json")
            with open(fd, "w") as fh:
                json.dump(resp, fh, indent=2)
            return path
        except OSError:
            return None
