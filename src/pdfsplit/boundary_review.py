"""Reviewer-facing boundary status: Checked vs Needs a look.

The old 0.98 / 0.75 / 0.62 number meant "a hard rule fired", not "this cut
is safe to skip." CAs who trust a high number skip the check — worse than
showing nothing.

Under-splitting is the failure that produces a wrong voucher. A badge that
only labels existing cuts is blind to that. Every adjacent page pair inside
a multi-page document is a hold decision — audit those the same way as cuts.

``review_status`` stays sparse on purpose, but it must catch known weak
holds (e.g. invoice pages glued to terms / delivery notes).
"""

from __future__ import annotations

from typing import Any, Literal

from pdfsplit.boundary_trace import reason_asserts_continuation

ReviewStatus = Literal["checked", "needs_look"]

#: Signals that count as hard evidence for a cut (not packet_start).
_HARD_CUT_SIGNALS = {
    "declared_extent",
    "page_dimensions",
    "page_1_of_n",
    "shared_doc_number",
    "continuation_marker",
    "different_doc_number",
    "different_seller_gstin",
    "closing_page",
    "document_complete",
    "judge_shared_number",
}

#: Signals that count as hard evidence for HOLDING pages together.
_HARD_HOLD_SIGNALS = {
    "declared_extent",
    "continuation_marker",
    "shared_doc_number",
    "closing_page",
}


def _has_hard_cut_signal(signals: list[str] | None) -> bool:
    return any(s in _HARD_CUT_SIGNALS for s in (signals or []))


def conflict_pages(raw_constraints: list[Any] | None) -> set[int]:
    """Pages where both a must_split and a must_not_split fired before rank."""
    by_page: dict[int, set[str]] = {}
    for c in raw_constraints or []:
        kind = getattr(c, "kind", None) or (
            c.get("kind") if isinstance(c, dict) else None
        )
        page = getattr(c, "page", None) or (
            c.get("page") if isinstance(c, dict) else None
        )
        if kind not in ("must_split", "must_not_split") or page is None:
            continue
        by_page.setdefault(int(page), set()).add(str(kind))
    return {p for p, kinds in by_page.items() if len(kinds) >= 2}


def _constraint_attrs(c: Any) -> tuple[int, str, str]:
    if isinstance(c, dict):
        return int(c["page"]), str(c["kind"]), str(c.get("signal") or "")
    return int(c.page), str(c.kind), str(c.signal)


#: Split signals that are surprising to lose inside a held range.
#: ``document_complete`` losing to ``shared_doc_number`` is routine and
#: correct — do not cry wolf on that.
_STRONG_SPLIT_SIGNALS = {
    "different_doc_number",
    "different_seller_gstin",
    "page_1_of_n",
    "page_dimensions",
}


def suppressed_strong_split_pages(raw_constraints: list[Any] | None) -> set[int]:
    """Pages held despite a strong must_split losing on rank.

    Routine cases (e.g. ``document_complete`` losing to ``shared_doc_number``)
    are not included — those are how continuation works.
    """
    from pdfsplit.boundary_constraints import SIGNAL_RANK

    by_page: dict[int, list[tuple[str, str, int]]] = {}
    for c in raw_constraints or []:
        page, kind, signal = _constraint_attrs(c)
        if kind not in ("must_split", "must_not_split"):
            continue
        by_page.setdefault(page, []).append(
            (kind, signal, SIGNAL_RANK.get(signal, 0))
        )
    out: set[int] = set()
    for page, rows in by_page.items():
        strong_splits = [
            r for r in rows if r[0] == "must_split" and r[1] in _STRONG_SPLIT_SIGNALS
        ]
        holds = [r for r in rows if r[0] == "must_not_split"]
        if not strong_splits or not holds:
            continue
        max_split = max(r[2] for r in strong_splits)
        max_hold = max(r[2] for r in holds)
        if max_hold >= max_split:
            out.add(page)
    return out


def suppressed_split_pages(raw_constraints: list[Any] | None) -> set[int]:
    """Back-compat alias — strong splits only."""
    return suppressed_strong_split_pages(raw_constraints)


def derive_cut_review_reasons(
    seg: dict[str, Any],
    *,
    conflict_pages_set: set[int] | None = None,
    vendors_clearly_differ: bool = False,
) -> list[str]:
    """Reasons that the *start* of this segment needs a look."""
    reasons: list[str] = []
    start = int(seg.get("page_start") or 0)
    signals = list(seg.get("signals") or [])
    hard = _has_hard_cut_signal(signals)

    if seg.get("overlay_suggest"):
        reasons.append("after-check unsure — letterhead weak on one side")

    if start in (conflict_pages_set or ()):
        reasons.append("hard signals disagreed on this start")

    judge_reason = str(seg.get("judge_reason") or "")
    if (
        start > 1
        and reason_asserts_continuation(judge_reason)
        and not vendors_clearly_differ
    ):
        reasons.append("model reason argues this is the same bill")

    if start > 1 and not hard:
        reasons.append("only the model — no hard signal on this cut")

    return reasons


def derive_hold_review_reasons(
    seg: dict[str, Any],
    *,
    pages: list[dict[str, Any]] | None,
    resolved_constraints: list[Any] | None = None,
    raw_constraints: list[Any] | None = None,
) -> list[str]:
    """Reasons that a *hold* inside this multi-page segment needs a look.

    Every adjacent page pair inside the segment is a decision we made.
    Under-splitting (the wrong-voucher failure) only shows up here.
    """
    if not pages:
        return []
    start = int(seg.get("page_start") or 0)
    end = int(seg.get("page_end") or 0)
    if end <= start:
        return []

    from pdfsplit.boundary_constraints import (
        extract_own_doc_number,
        extract_seller_gstin,
    )

    reasons: list[str] = []
    hold_pages = {
        _constraint_attrs(c)[0]
        for c in (resolved_constraints or [])
        if _constraint_attrs(c)[1] == "must_not_split"
        and _constraint_attrs(c)[2] in _HARD_HOLD_SIGNALS
    }
    conflicts = conflict_pages(raw_constraints)
    suppressed_splits = suppressed_split_pages(raw_constraints)

    for p in range(start + 1, end + 1):
        prev_i, cur_i = p - 2, p - 1  # 0-based
        if not (0 <= prev_i < len(pages) and 0 <= cur_i < len(pages)):
            continue

        if p not in hold_pages:
            reasons.append(
                f"pages {p - 1}–{p} held with no hard hold signal"
            )

        if p in suppressed_splits:
            reasons.append(
                f"pages {p - 1}–{p}: strong split signal lost to a hold"
            )

        own_l = extract_own_doc_number(pages[prev_i])
        own_r = extract_own_doc_number(pages[cur_i])
        if own_l and own_r and own_l != own_r:
            reasons.append(
                f"different bill numbers held together "
                f"({own_l} / {own_r})"
            )

        gstin_l = extract_seller_gstin(pages[prev_i])
        gstin_r = extract_seller_gstin(pages[cur_i])
        if gstin_l and gstin_r and gstin_l != gstin_r:
            reasons.append(
                f"different seller GSTINs held together "
                f"({gstin_l} / {gstin_r})"
            )

    # After-check merge with only the judge number — no Layer-1 hold.
    signals = list(seg.get("signals") or [])
    if (
        seg.get("overlay_tier") == "merge"
        and "judge_shared_number" in signals
        and "shared_doc_number" not in signals
        and "declared_extent" not in signals
        and "continuation_marker" not in signals
    ):
        reasons.append("after-check merged with thin evidence")

    # Deduplicate preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def derive_review_status(
    seg: dict[str, Any],
    *,
    conflict_pages_set: set[int] | None = None,
    vendors_clearly_differ: bool = False,
    pages: list[dict[str, Any]] | None = None,
    resolved_constraints: list[Any] | None = None,
    raw_constraints: list[Any] | None = None,
) -> tuple[ReviewStatus, list[str]]:
    """Return (status, short reasons) for one segment — cuts and holds."""
    reasons = derive_cut_review_reasons(
        seg,
        conflict_pages_set=conflict_pages_set,
        vendors_clearly_differ=vendors_clearly_differ,
    )
    reasons.extend(
        derive_hold_review_reasons(
            seg,
            pages=pages,
            resolved_constraints=resolved_constraints,
            raw_constraints=raw_constraints,
        )
    )
    if reasons:
        return "needs_look", reasons
    return "checked", []


def annotate_segments_with_review_status(
    segments: list[dict[str, Any]],
    *,
    raw_constraints: list[Any] | None = None,
    pages: list[dict[str, Any]] | None = None,
    resolved_constraints: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach ``review_status`` + ``review_reasons`` to each segment."""
    from pdfsplit.boundary_constraints import compute_boundary_constraints
    from pdfsplit.boundary_overlay import overlay_vendor, vendors_relation

    conflicts = conflict_pages(raw_constraints)
    resolved = resolved_constraints
    if resolved is None and pages is not None:
        resolved = compute_boundary_constraints(pages)

    ordered = sorted(segments, key=lambda s: int(s.get("page_start") or 0))
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(ordered):
        vendors_differ = False
        if pages and int(seg.get("page_start") or 0) > 1:
            prev = ordered[i - 1] if i > 0 else None
            if prev is not None and int(prev.get("page_end") or 0) + 1 == int(
                seg["page_start"]
            ):
                left_mds = [
                    (pages[p - 1].get("markdown") or "")
                    if 0 <= p - 1 < len(pages)
                    else ""
                    for p in range(
                        int(prev["page_start"]), int(prev["page_end"]) + 1
                    )
                ]
                right_mds = [
                    (pages[p - 1].get("markdown") or "")
                    if 0 <= p - 1 < len(pages)
                    else ""
                    for p in range(
                        int(seg["page_start"]), int(seg["page_end"]) + 1
                    )
                ]
                ven_l = overlay_vendor(prev, pages)
                ven_r = overlay_vendor(seg, pages)
                vendors_differ = (
                    vendors_relation(ven_l, ven_r, left_mds, right_mds)
                    == "differ"
                )
        status, reasons = derive_review_status(
            seg,
            conflict_pages_set=conflicts,
            vendors_clearly_differ=vendors_differ,
            pages=pages,
            resolved_constraints=resolved,
            raw_constraints=raw_constraints,
        )
        row = dict(seg)
        row["review_status"] = status
        row["review_reasons"] = reasons
        out.append(row)
    return out
