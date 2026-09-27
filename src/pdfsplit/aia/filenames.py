"""AIA Needs Review filenames: packet-VEN3-number.pdf with grounded slots.

Rules (hard):
- Format: ``<packet>-<VEN3>[-<number>].pdf`` with predictable fallbacks, where
  ``VEN3`` is the first 3 characters of the sanitized vendor, uppercased
  (``NA`` when the vendor is empty).
- Grounding: vendor / number must appear on the document's pages, or discard.
- Determinism: resolve once at analyze time; push uses cached/UI values only.
- Collisions: detect the full set first; suffix ``-2``, ``-3`` in document order.
- Never fail a push because naming failed — fall back to positional names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pdfsplit.extraction import _normalise

#: Per-slot cap (packet / vendor / number).
MAX_SLOT = 30
#: Whole filename (including ``.pdf``).
MAX_FILE_NAME = 180
#: Reserved Windows names (case-insensitive).
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

#: Broader doc-number line detectors (fallback when the judge is silent).
#: Require No/Number/# so a title like "TAX INVOICE" is not itself a label.
_RE_DOC_LABEL = re.compile(
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
#: GSTIN / GST number — never a document number, even unlabelled.
_RE_GSTIN_TOKEN = re.compile(
    r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$",
    re.IGNORECASE,
)
_RE_PAN_TOKEN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.IGNORECASE)
_RE_CIN_TOKEN = re.compile(
    r"^[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$", re.IGNORECASE
)
_RE_SKIP_ID_LINE = re.compile(
    r"^\s*(?:gstin|gst\s*(?:no|number|#)?|pan|cin|"
    r"client\s*code|settlement\s*(?:no\.?|number))\b",
    re.IGNORECASE,
)
_RE_ORDINAL = re.compile(r"^\d+(?:st|nd|rd|th)$", re.IGNORECASE)
_RE_FILENAME = re.compile(
    r"\.(?:jpe?g|png|gif|pdf|tiff?)$", re.IGNORECASE
)
#: Pull an identifier token from a labelled line.
_RE_ID_TOKEN = re.compile(
    r"(?:"
    r"(?:INV|BILL|VOUCHER|DN|CN|PO)[-/]?\s*[\w./-]+|"
    r"[A-Z]{1,6}[-/]\d[\w./-]*|"
    r"\d{2,}[-/]\d[\w./-]*|"
    r"[A-Z0-9][\w./-]{2,}"
    r")",
    re.IGNORECASE,
)


def sanitize_slot(name: str, *, fallback: str = "") -> str:
    """Filename-safe single slot: no path seps, unicode ok via ``\\w``, max 30."""
    raw = (name or "").strip()
    if not raw:
        return fallback
    for sep in ("/", "\\", "\0"):
        raw = raw.replace(sep, "-")
    raw = re.sub(r"\s+", "-", raw)
    raw = re.sub(r"[^\w.\-]+", "-", raw, flags=re.UNICODE)
    raw = re.sub(r"-{2,}", "-", raw).strip(".-_")
    raw = (raw[:MAX_SLOT] or "").rstrip(".-_")
    if not raw:
        return fallback
    if raw.upper() in _RESERVED:
        raw = f"{raw}_file"
    return raw[:MAX_SLOT].rstrip(".-_") or fallback


def sanitize_packet_stem(filename: str, fallback: str = "packet") -> str:
    """Sanitize packet/upload stem for the first filename slot."""
    from pathlib import Path

    stem = Path(filename or "").stem or fallback
    return sanitize_slot(stem, fallback=fallback) or fallback


def extract_doc_number_id(line: str) -> str | None:
    """From a labelled line, return just the identifier (not the whole label)."""
    raw = (line or "").strip()
    if not raw:
        return None
    # Prefer text after the label keyword.
    m = _RE_DOC_LABEL.search(raw)
    tail = raw[m.end() :] if m else raw
    tail = tail.lstrip(" .:-\t")
    if not tail:
        # Bare identifier line (no label).
        tail = raw
    # Take the first plausible token; strip trailing punctuation.
    for tok in _RE_ID_TOKEN.finditer(tail):
        cand = tok.group(0).strip(" .,:;")
        # Reject pure label / column words and digit-less tokens.
        if cand.lower() in {
            "invoice",
            "bill",
            "tax",
            "voucher",
            "number",
            "no",
            "date",
            "total",
            "amount",
            "gstin",
            "gst",
            "page",
            "original",
            "duplicate",
            "copy",
            "contract",
            "note",
            "challan",
            "document",
            "client",
            "code",
        }:
            continue
        if not any(ch.isdigit() for ch in cand):
            continue
        compact = cand.replace(" ", "")
        if _RE_GSTIN_TOKEN.match(compact) or _RE_PAN_TOKEN.match(compact) or _RE_CIN_TOKEN.match(compact):
            continue
        if len(cand) >= 2:
            return cand[:MAX_SLOT]
    return None


def regex_doc_number_from_pages(page_markdowns: list[str]) -> str | None:
    """Fallback: first grounded-looking invoice/bill number on the pages."""
    for md in page_markdowns:
        for ln in (md or "").splitlines():
            ln = ln.strip()
            if not ln or not _RE_DOC_LABEL.search(ln):
                continue
            if _RE_SKIP_ID_LINE.search(ln):
                continue
            ident = extract_doc_number_id(ln)
            if ident and text_grounded_on_pages(ident, [md]):
                return ident
    return None


def bare_header_doc_number(
    page_markdowns: list[str], header_text: str | None = None
) -> str | None:
    """Unlabelled header identifier the Layer-1 regexes miss (e.g. ZZCO/8801/26-27).

    Naming-slot seed only — does not add a Layer-1 constraint. Skips GSTIN
    lines and digit-only dates so page-1 GSTIN is never the invoice number.
    """
    lines: list[str] = []
    seen: set[str] = set()

    def _add(ln: str) -> None:
        s = ln.strip().lstrip("#").strip()
        if s and s not in seen:
            seen.add(s)
            lines.append(s)

    if header_text:
        for ln in header_text.splitlines():
            _add(ln)
    for md in page_markdowns[:1]:
        n = 0
        for ln in (md or "").splitlines():
            s = ln.strip()
            if not s:
                continue
            _add(s)
            n += 1
            if n >= 12:
                break
        if n:
            break
    for ln in lines:
        low = ln.lower()
        if low.startswith("gstin") or low.startswith("gst "):
            continue
        if _RE_SKIP_ID_LINE.search(ln):
            continue
        if low.startswith("date"):
            continue
        if low.startswith("page "):
            continue
        ident = extract_doc_number_id(ln)
        if not ident:
            continue
        compact = ident.replace(" ", "")
        if _RE_GSTIN_TOKEN.match(compact):
            continue
        if _RE_PAN_TOKEN.match(compact) or _RE_CIN_TOKEN.match(compact):
            continue
        if _RE_ORDINAL.match(compact):
            continue
        if _RE_FILENAME.search(compact):
            continue
        if not any(ch.isalpha() for ch in ident):
            continue
        if not any(ch.isdigit() for ch in ident):
            continue
        # Bare identifiers we care about look like ZZCO/8801/26-27 or INV-9.
        # Digit-letter crumbs (H62, 65A) and UUID-like envelope ids are not.
        if "/" not in ident and "-" not in ident:
            continue
        if ident.count("-") >= 3:
            continue
        if len(ident) < 6:
            continue
        if text_grounded_on_pages(ident, page_markdowns):
            return ident
    return None


def text_grounded_on_pages(claim: str, page_markdowns: list[str]) -> bool:
    """True if ``claim`` appears on at least one page (same normalise as fields)."""
    needle = (claim or "").strip()
    if not needle:
        return False
    norm_n = _normalise(needle)
    if not norm_n:
        return False
    for md in page_markdowns:
        if norm_n in _normalise(md or ""):
            return True
    return False


def positional_name(index: int) -> str:
    """1-based positional fallback: Invoice-01, Invoice-02, …"""
    return f"Invoice-{index:02d}"


def vendor_code(vendor: str) -> str:
    """VEN3 segment: first 3 chars of the sanitized vendor, uppercased.

    Empty sanitized result → ``NA``. Shorter than 3 → use what exists.
    """
    safe = sanitize_slot(vendor, fallback="")
    if not safe:
        return "NA"
    return safe[:3].upper()


@dataclass(frozen=True)
class NameSlots:
    packet: str
    vendor: str
    number: str
    #: Why each slot was chosen (for reporting / debug).
    vendor_source: str = ""  # judge | header | edit | empty
    number_source: str = ""  # judge | regex | edit | empty
    used_positional: bool = False


def build_base_file_name(slots: NameSlots) -> str:
    """Build unsuffixed ``packet-VEN3[-number].pdf`` (or positional)."""
    packet = sanitize_packet_stem(slots.packet, fallback="packet") or "packet"
    number = sanitize_slot(slots.number, fallback="")
    parts = [packet, vendor_code(slots.vendor)]
    if number:
        parts.append(number)
    name = "-".join(parts) + ".pdf"
    if len(name) <= MAX_FILE_NAME:
        return name
    # Trim later slots first so packet survives.
    overflow = len(name) - MAX_FILE_NAME
    if number and len(number) > 8:
        trim = min(overflow, len(number) - 8)
        number = number[: len(number) - trim].rstrip(".-_")
        parts = [packet, vendor_code(slots.vendor)]
        if number:
            parts.append(number)
        name = "-".join(parts) + ".pdf"
        overflow = len(name) - MAX_FILE_NAME
    if overflow > 0:
        # Vendor code is fixed at 3 chars; trim packet from the tail instead.
        trim = min(overflow, len(packet) - 3)
        packet = packet[: len(packet) - trim].rstrip(".-_")
        parts = [packet, vendor_code(slots.vendor)]
        if number:
            parts.append(number)
        name = "-".join(parts) + ".pdf"
    return name


def apply_collision_suffixes(base_names: list[str]) -> list[str]:
    """Deterministic collision suffixes in document order.

    First occurrence keeps the base name; later duplicates get ``-2``, ``-3``, …
    inserted before ``.pdf``.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in base_names:
        key = name.lower()
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count == 1:
            out.append(name)
            continue
        if name.lower().endswith(".pdf"):
            stem = name[:-4]
            out.append(f"{stem}-{count}.pdf")
        else:
            out.append(f"{name}-{count}")
    # Cap total length after suffix.
    capped: list[str] = []
    for name in out:
        if len(name) <= MAX_FILE_NAME:
            capped.append(name)
            continue
        if name.lower().endswith(".pdf"):
            stem = name[:-4][: MAX_FILE_NAME - 4].rstrip(".-_")
            capped.append(f"{stem}.pdf")
        else:
            capped.append(name[:MAX_FILE_NAME])
    return capped


def resolve_slots_for_segment(
    *,
    packet: str,
    index: int,
    vendor_hint: str | None,
    number_hint: str | None,
    page_markdowns: list[str],
    header_text: str | None = None,
    vendor_edit: str | None = None,
    number_edit: str | None = None,
) -> NameSlots:
    """Resolve vendor/number with grounding. Edits skip grounding (authoritative).

    Never raises — empty/failed extraction falls back to positional.
    """
    vendor = ""
    vendor_source = "empty"
    number = ""
    number_source = "empty"

    # --- vendor ---
    if vendor_edit is not None and vendor_edit.strip():
        vendor = vendor_edit.strip()
        vendor_source = "edit"
    else:
        for candidate, source in (
            ((vendor_hint or "").strip(), "judge"),
            ((header_text or "").strip(), "header"),
        ):
            if not candidate:
                continue
            if text_grounded_on_pages(candidate, page_markdowns):
                vendor = candidate
                vendor_source = source
                break

    # --- number ---
    if number_edit is not None and number_edit.strip():
        number = number_edit.strip()
        number_source = "edit"
    else:
        judge_num = (number_hint or "").strip()
        # Judge may return a labelled line — extract the id first.
        if judge_num:
            ident = extract_doc_number_id(judge_num) or judge_num
            if text_grounded_on_pages(ident, page_markdowns):
                number = ident
                number_source = "judge"
        if not number:
            regex_num = regex_doc_number_from_pages(page_markdowns)
            if regex_num:
                number = regex_num
                number_source = "regex"
        if not number:
            bare = bare_header_doc_number(page_markdowns, header_text)
            if bare:
                number = bare
                number_source = "bare_header"

    used_positional = False
    if not vendor and not number:
        vendor = positional_name(index)
        vendor_source = "positional"
        used_positional = True

    return NameSlots(
        packet=packet,
        vendor=vendor,
        number=number,
        vendor_source=vendor_source,
        number_source=number_source,
        used_positional=used_positional,
    )


def attach_names_to_segments(
    segments: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    *,
    packet_stem: str = "packet",
) -> list[dict[str, Any]]:
    """Resolve + cache vendor/number on each segment (analyze-time, deterministic).

    Mutates segment dicts in place and returns them. Safe on malformed input.
    """
    n = len(pages)
    page_mds = [(p.get("markdown") or "") for p in pages]
    headers = []
    try:
        from pdfsplit.providers.mistral import _page_header_text

        headers = [_page_header_text(p) for p in pages]
    except Exception:  # noqa: BLE001
        headers = [""] * n

    bases: list[str] = []
    slots_list: list[NameSlots] = []
    for i, seg in enumerate(segments, start=1):
        try:
            start = int(seg.get("page_start") or 1)
            end = int(seg.get("page_end") or start)
        except (TypeError, ValueError):
            start, end = 1, 1
        start = max(1, min(start, n or 1))
        end = max(start, min(end, n or start))
        slice_mds = page_mds[start - 1 : end] if page_mds else [""]
        header = headers[start - 1] if headers and start - 1 < len(headers) else ""
        slots = resolve_slots_for_segment(
            packet=packet_stem,
            index=i,
            vendor_hint=str(seg.get("vendor") or "") or None,
            number_hint=str(seg.get("document_number") or seg.get("invoice_number") or "")
            or None,
            page_markdowns=slice_mds,
            header_text=header,
        )
        slots_list.append(slots)
        bases.append(build_base_file_name(slots))

    unique = apply_collision_suffixes(bases)
    for seg, slots, fname in zip(segments, slots_list, unique, strict=False):
        seg["vendor"] = slots.vendor
        seg["document_number"] = slots.number
        seg["vendor_source"] = slots.vendor_source
        seg["number_source"] = slots.number_source
        seg["aia_file_name"] = fname
        seg["naming_fallback"] = slots.used_positional
    return segments


def plan_filenames(
    *,
    packet: str,
    items: list[dict[str, Any]],
) -> list[str]:
    """Build final unique filenames from already-resolved (or edited) slots.

    Each item: ``{vendor?, document_number?, index?}``. Does **not** re-ground
    or re-derive — caller supplies authoritative values (cached or edited).
    """
    bases: list[str] = []
    for i, item in enumerate(items, start=1):
        vendor = str(item.get("vendor") or "").strip()
        number = str(item.get("document_number") or item.get("invoice_number") or "").strip()
        if not vendor and not number:
            vendor = positional_name(int(item.get("index") or i))
        slots = NameSlots(packet=packet, vendor=vendor, number=number)
        try:
            bases.append(build_base_file_name(slots))
        except Exception:  # noqa: BLE001 — naming must never block push
            bases.append(
                build_base_file_name(
                    NameSlots(
                        packet=packet or "packet",
                        vendor=positional_name(i),
                        number="",
                        used_positional=True,
                    )
                )
            )
    return apply_collision_suffixes(bases)
