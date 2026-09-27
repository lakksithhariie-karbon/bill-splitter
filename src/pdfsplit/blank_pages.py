"""Blank separator page detection and segment stripping.

People insert blank sheets between bills when scanning a batch. Those pages
are never a voucher — auto-exclude them. OCR-empty is not always paper-empty
(faint scan / handwriting), so auto-exclude requires two gates.
"""

from __future__ import annotations

import re
from typing import Any

_RE_PAGE_ONLY = re.compile(
    r"^(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?$",
    re.IGNORECASE,
)
_RE_ALNUM = re.compile(r"[A-Za-z0-9]")
#: Tokens that alone do not count as invoice content.
_NOISE_WORDS = {
    "blank",
    "empty",
    "page",
    "of",
    "continued",
    "cont",
}


def _meaningful_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", text or ""):
        low = raw.lower()
        if low in _NOISE_WORDS:
            continue
        if raw.isdigit() and len(raw) <= 3:
            # Lone page numbers.
            continue
        tokens.append(raw)
    return tokens


def text_gate_empty(markdown: str) -> bool:
    """True when markdown has no real content (allow page-number-only lines)."""
    lines = []
    for ln in (markdown or "").splitlines():
        s = ln.strip().lstrip("#").strip()
        if not s:
            continue
        if _RE_PAGE_ONLY.match(s):
            continue
        lines.append(s)
    if not lines:
        return True
    joined = "\n".join(lines)
    return not _meaningful_tokens(joined)


def sparse_gate(page: dict[str, Any]) -> bool:
    """True when OCR has essentially no text blocks / almost no content.

    Second gate: protects faint scans that OCR emptied but that still have
    block structure or real tokens beyond a lone page number.
    """
    blocks = page.get("blocks") or page.get("text_blocks") or []
    text_blocks = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = (b.get("text") or b.get("content") or "").strip()
        if _meaningful_tokens(t):
            text_blocks += 1
    if text_blocks > 0:
        return False
    md = page.get("markdown") or ""
    # No real tokens left after dropping page-number noise → sparse.
    return len(_meaningful_tokens(md)) == 0


def is_auto_blank_page(page: dict[str, Any]) -> bool:
    """Auto-exclude only when BOTH text and sparse gates fire."""
    md = page.get("markdown") or ""
    return text_gate_empty(md) and sparse_gate(page)


def detect_blank_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return auto-excluded blank page records (1-indexed)."""
    out: list[dict[str, Any]] = []
    for i, page in enumerate(pages):
        if is_auto_blank_page(page):
            out.append(
                {
                    "page": i + 1,
                    "reason": "Blank separator",
                    "source": "auto",
                }
            )
    return out


def possible_blank_pages(pages: list[dict[str, Any]]) -> list[int]:
    """Text-empty but not sparse — do not auto-exclude; flag for a look."""
    out: list[int] = []
    for i, page in enumerate(pages):
        md = page.get("markdown") or ""
        if text_gate_empty(md) and not sparse_gate(page):
            out.append(i + 1)
    return out


def strip_pages_from_segments(
    segments: list[dict[str, Any]],
    excluded: set[int],
) -> list[dict[str, Any]]:
    """Drop excluded pages from segments; split into contiguous runs.

    A blank in the middle of a merged range becomes a gap between two bills.
    """
    if not excluded:
        return [dict(s) for s in segments]
    out: list[dict[str, Any]] = []
    for seg in segments:
        start = int(seg["page_start"])
        end = int(seg["page_end"])
        keep = [p for p in range(start, end + 1) if p not in excluded]
        if not keep:
            continue
        run_start = keep[0]
        prev = keep[0]
        for p in keep[1:]:
            if p == prev + 1:
                prev = p
                continue
            row = dict(seg)
            row["page_start"] = run_start
            row["page_end"] = prev
            out.append(row)
            run_start = p
            prev = p
        row = dict(seg)
        row["page_start"] = run_start
        row["page_end"] = prev
        out.append(row)
    return out
