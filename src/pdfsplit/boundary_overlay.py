"""Post-judge overlay: merge / suggest / block on the judge's numbers.

Layer 1 runs before the judge from page markdown. The judge can extract a
document number the regexes miss (bare ``ZZCO/8801/26-27``). This pass runs
AFTER the judge (and after naming attach) and only on what those layers
already put on the segments.

Tiers, for an adjacent pair that shares a grounded document number:
  vendors clearly differ  → blocked (stay split)
  vendors match           → merge
  vendor empty either side → suggest (stay split; human may join)

Never invent a number from the first identifier-shaped token (GSTIN).
When in doubt, do not merge.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pdfsplit.aia.filenames import text_grounded_on_pages
from pdfsplit.boundary_constraints import (
    compute_boundary_constraints,
    extract_seller_gstin,
)


VendorRelation = Literal["match", "differ", "empty"]
OverlayTier = Literal["merge", "suggest", "blocked"]

_SKIP_LETTERHEAD = {
    "tax invoice",
    "invoice",
    "debit note",
    "credit note",
    "bill",
    "document",
    "original",
    "duplicate",
    "copy",
    "tax invoice |",
    "statutory tax analysis",
    "tax analysis",
    "terms and conditions",
    "annexure",
}

_RE_VENDOR_TOKEN = re.compile(r"[a-z0-9]+")
_RE_COMPANY_MARK = re.compile(
    r"\b(?:pvt|pty|ltd|limited|llp|llc|inc|gmbh|plc|corp|corporation|co)\b",
    re.IGNORECASE,
)


def normalize_doc_number(raw: str) -> str:
    """Compare invoice numbers without whitespace / case / trailing punct."""
    return "".join((raw or "").split()).upper().rstrip(".,;")


def _norm_vendor(raw: str) -> str:
    return " ".join(_RE_VENDOR_TOKEN.findall((raw or "").lower()))


def _is_document_heading(raw: str) -> bool:
    """True for invoice/title lines that are not a supplier name."""
    s = (raw or "").strip().lstrip("#").strip()
    if not s:
        return True
    low = s.lower().rstrip(" .|#*")
    if low in _SKIP_LETTERHEAD or low.startswith("page "):
        return True
    if low.startswith("gstin") or low.startswith("gst "):
        return True
    if _RE_COMPANY_MARK.search(s):
        return False
    # ALL-CAPS title without a company marker ("STATUTORY TAX ANALYSIS").
    letters = [ch for ch in s if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters) and " " in s:
        return True
    return False


def letterhead_vendor(page: dict[str, Any] | None) -> str:
    """First identity line on the page, or empty. Never a GSTIN / title-only."""
    if not page:
        return ""
    md = page.get("markdown") or ""
    for ln in md.splitlines():
        s = ln.strip().lstrip("#").strip()
        if not s:
            continue
        if _is_document_heading(s):
            continue
        letters = sum(1 for ch in s if ch.isalpha())
        if letters >= 3 and (len(s) >= 10 or " " in s):
            return s[:120]
    return ""


def overlay_vendor(
    seg: dict[str, Any],
    pages: list[dict[str, Any]],
) -> str:
    """Vendor used for the overlay rule. Empty demotes to suggest.

    Prefer the judge/naming slot when it is grounded and is not a document
    heading (``TAX INVOICE`` is a title, not a supplier). Otherwise the
    first letterhead line of the start page, if grounded.
    """
    start = int(seg["page_start"])
    end = int(seg["page_end"])
    slice_mds = _segment_markdowns(pages, start, end)
    claimed = (seg.get("vendor") or "").strip()
    if (
        claimed
        and not _is_document_heading(claimed)
        and text_grounded_on_pages(claimed, slice_mds)
    ):
        return claimed
    page = _page_at(pages, start)
    letterhead = letterhead_vendor(page)
    if letterhead and text_grounded_on_pages(letterhead, slice_mds):
        return letterhead
    return ""


def overlay_number(
    seg: dict[str, Any],
    pages: list[dict[str, Any]],
) -> str:
    """Document number from the judge/naming slot, only if grounded here.

    Does not fall back to a page-wide identifier scan (that picks GSTIN).
    """
    claimed = (seg.get("document_number") or "").strip()
    if not claimed:
        return ""
    start = int(seg["page_start"])
    end = int(seg["page_end"])
    slice_mds = _segment_markdowns(pages, start, end)
    ident = claimed
    if text_grounded_on_pages(ident, slice_mds):
        return ident
    return ""


def vendors_relation(
    left: str,
    right: str,
    left_mds: list[str],
    right_mds: list[str],
) -> VendorRelation:
    """Match / differ / empty. Only a positive unambiguous difference blocks.

    Containment and cross-grounding cover letterhead variation (an opener
    like ``IBC Group`` on a continuation page before the same legal name).
    Empty on either side is not a mismatch — it demotes to suggest.
    """
    lv, rv = (left or "").strip(), (right or "").strip()
    if not lv or not rv:
        return "empty"
    nl, nr = _norm_vendor(lv), _norm_vendor(rv)
    if not nl or not nr:
        return "empty"
    if nl == nr:
        return "match"
    if nl in nr or nr in nl:
        return "match"
    if text_grounded_on_pages(lv, right_mds) or text_grounded_on_pages(
        rv, left_mds
    ):
        return "match"
    return "differ"


def _page_at(pages: list[dict[str, Any]], page_no: int) -> dict[str, Any] | None:
    idx = page_no - 1
    if 0 <= idx < len(pages):
        return pages[idx]
    return None


def _segment_markdowns(
    pages: list[dict[str, Any]], start: int, end: int
) -> list[str]:
    out: list[str] = []
    for p in range(start, end + 1):
        page = _page_at(pages, p)
        out.append((page.get("markdown") or "") if page else "")
    return out


def _coverage_pages(segments: list[dict[str, Any]]) -> list[int]:
    covered: list[int] = []
    for seg in segments:
        covered.extend(range(int(seg["page_start"]), int(seg["page_end"]) + 1))
    return covered


def _assert_full_coverage(
    segments: list[dict[str, Any]], n_pages: int
) -> None:
    covered = _coverage_pages(segments)
    expected = list(range(1, n_pages + 1))
    if covered != expected:
        raise ValueError(
            f"overlay: coverage broken {covered} != {expected}"
        )


@dataclass
class OverlayPair:
    left_start: int
    left_end: int
    right_start: int
    right_end: int
    tier: OverlayTier
    reason: str
    document_number: str
    vendor_left: str
    vendor_right: str
    vendor_relation: VendorRelation


@dataclass
class OverlayReport:
    merges: list[OverlayPair] = field(default_factory=list)
    suggestions: list[OverlayPair] = field(default_factory=list)
    blocked: list[OverlayPair] = field(default_factory=list)
    extent_hold_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _pair(p: OverlayPair) -> dict[str, Any]:
            return {
                "left_start": p.left_start,
                "left_end": p.left_end,
                "right_start": p.right_start,
                "right_end": p.right_end,
                "tier": p.tier,
                "reason": p.reason,
                "document_number": p.document_number,
                "vendor_left": p.vendor_left,
                "vendor_right": p.vendor_right,
                "vendor_relation": p.vendor_relation,
            }

        return {
            "merges": [_pair(p) for p in self.merges],
            "suggestions": [_pair(p) for p in self.suggestions],
            "blocked": [_pair(p) for p in self.blocked],
            "extent_hold_pages": list(self.extent_hold_pages),
            "tier_counts": {
                "merge": len(self.merges),
                "suggest": len(self.suggestions),
                "blocked": len(self.blocked),
            },
        }


def classify_adjacent_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    pages: list[dict[str, Any]],
) -> OverlayPair | None:
    """Classify one adjacent pair, or None when numbers do not qualify."""
    if int(left["page_end"]) + 1 != int(right["page_start"]):
        return None
    left_mds = _segment_markdowns(
        pages, int(left["page_start"]), int(left["page_end"])
    )
    right_mds = _segment_markdowns(
        pages, int(right["page_start"]), int(right["page_end"])
    )
    num_l = overlay_number(left, pages)
    num_r = overlay_number(right, pages)
    if not num_l or not num_r:
        return None
    if normalize_doc_number(num_l) != normalize_doc_number(num_r):
        return None
    # Grounding is already required by overlay_number; require both sides.
    if not text_grounded_on_pages(num_l, left_mds):
        return None
    if not text_grounded_on_pages(num_r, right_mds):
        return None
    vendor_l = overlay_vendor(left, pages)
    vendor_r = overlay_vendor(right, pages)
    relation = vendors_relation(vendor_l, vendor_r, left_mds, right_mds)
    shown = num_l.strip()
    # Group companies on one letterhead: matching brand name must not merge
    # when seller GSTINs clearly differ.
    gstin_l = extract_seller_gstin(_page_at(pages, int(left["page_start"])))
    gstin_r = extract_seller_gstin(_page_at(pages, int(right["page_start"])))
    if gstin_l and gstin_r and gstin_l != gstin_r:
        return OverlayPair(
            left_start=int(left["page_start"]),
            left_end=int(left["page_end"]),
            right_start=int(right["page_start"]),
            right_end=int(right["page_end"]),
            tier="blocked",
            reason=(
                f"Same number {shown} but seller GSTIN differs "
                f"({gstin_l} vs {gstin_r})"
            ),
            document_number=shown,
            vendor_left=vendor_l,
            vendor_right=vendor_r,
            vendor_relation="differ",
        )
    if relation == "differ":
        tier: OverlayTier = "blocked"
        reason = (
            f"Same number {shown} but vendors differ "
            f"({vendor_l} vs {vendor_r})"
        )
    elif relation == "empty":
        tier = "suggest"
        reason = (
            f"Same number {shown} — letterhead unreadable on one side"
        )
    else:
        tier = "merge"
        reason = f"Merged: both pages carry {shown}"
    return OverlayPair(
        left_start=int(left["page_start"]),
        left_end=int(left["page_end"]),
        right_start=int(right["page_start"]),
        right_end=int(right["page_end"]),
        tier=tier,
        reason=reason,
        document_number=shown,
        vendor_left=vendor_l,
        vendor_right=vendor_r,
        vendor_relation=relation,
    )


def _merge_two(
    left: dict[str, Any], right: dict[str, Any], pair: OverlayPair
) -> dict[str, Any]:
    """Join two adjacent segments. Undo snapshot is the pre-merge pair."""
    merged = dict(left)
    undo_left = copy.deepcopy(left)
    undo_right = copy.deepcopy(right)
    prior_undo = list(left.get("overlay_undo") or [])
    if not prior_undo:
        prior_undo = [undo_left]
    prior_undo.append(undo_right)
    merged["page_end"] = int(right["page_end"])
    merged["overlay_tier"] = "merge"
    merged["overlay_reason"] = pair.reason
    merged["overlay_undo"] = prior_undo
    if not (merged.get("vendor") or "").strip() and (
        right.get("vendor") or ""
    ).strip():
        merged["vendor"] = right["vendor"]
    if not (merged.get("document_number") or "").strip():
        merged["document_number"] = pair.document_number
    signals = list(merged.get("signals") or [])
    if "judge_shared_number" not in signals:
        signals.append("judge_shared_number")
    merged["signals"] = signals
    evidence = list(merged.get("evidence") or [])
    if pair.reason not in evidence:
        evidence.append(pair.reason)
    merged["evidence"] = evidence
    merged["confidence"] = max(
        float(left.get("confidence") or 0.0),
        float(right.get("confidence") or 0.0),
        0.98,
    )
    return merged


def apply_judge_overlay(
    segments: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    *,
    constraints: list[Any] | None = None,
) -> tuple[list[dict[str, Any]], OverlayReport]:
    """Merge adjacent segments that share a grounded number and matching vendor.

    Suggestions and blocked pairs stay split. Deterministic; no API calls.
    Full page coverage is preserved.
    """
    n_pages = len(pages)
    report = OverlayReport()
    if constraints is None:
        constraints = compute_boundary_constraints(pages)
    report.extent_hold_pages = sorted(
        {c.page for c in constraints if c.signal == "declared_extent"}
    )
    if n_pages == 0:
        return [], report
    if len(segments) < 2:
        _assert_full_coverage(segments, n_pages)
        return [dict(s) for s in segments], report

    work = [dict(s) for s in segments]
    _assert_full_coverage(work, n_pages)

    # Merge passes until stable. Classify leftover pairs after.
    merged_pairs: list[OverlayPair] = []
    changed = True
    while changed:
        changed = False
        for i in range(len(work) - 1):
            pair = classify_adjacent_pair(work[i], work[i + 1], pages)
            if pair is None or pair.tier != "merge":
                continue
            work[i] = _merge_two(work[i], work[i + 1], pair)
            del work[i + 1]
            merged_pairs.append(pair)
            changed = True
            break

    leftover_suggest: list[OverlayPair] = []
    leftover_blocked: list[OverlayPair] = []
    for i in range(len(work) - 1):
        pair = classify_adjacent_pair(work[i], work[i + 1], pages)
        if pair is None:
            continue
        if pair.tier == "suggest":
            leftover_suggest.append(pair)
            right = dict(work[i + 1])
            right["overlay_tier"] = "suggest"
            right["overlay_reason"] = pair.reason
            right["overlay_suggest"] = {
                "left_start": pair.left_start,
                "right_start": pair.right_start,
                "reason": pair.reason,
                "document_number": pair.document_number,
            }
            work[i + 1] = right
        elif pair.tier == "blocked":
            leftover_blocked.append(pair)
            right = dict(work[i + 1])
            right["overlay_tier"] = "blocked"
            right["overlay_reason"] = pair.reason
            work[i + 1] = right

    report.merges = merged_pairs
    report.suggestions = leftover_suggest
    report.blocked = leftover_blocked
    _assert_full_coverage(work, n_pages)
    return work, report


def attach_overlay_and_extent(
    segments: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Apply overlay; return (new_segments, report_dict, pre_overlay_copy)."""
    pre = [dict(s) for s in segments]
    constraints = compute_boundary_constraints(pages)
    new_segs, report = apply_judge_overlay(
        segments, pages, constraints=constraints
    )
    return new_segs, report.to_dict(), pre
