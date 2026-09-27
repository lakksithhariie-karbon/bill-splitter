"""Gated boundary reasoning trace — console + session/boundary_trace.txt.

GATE: BOUNDARY_DEBUG must be on AND we must not be on Render.
The trace contains page text (vendors, amounts, GSTINs). Render retains
logs, so production verbosity would put customer invoice content into a
log service.

Never logs API keys, cookies, or the AIA session value.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from pdfsplit.boundary_constraints import SIGNAL_RANK
from pdfsplit.boundary_overlay import (
    classify_adjacent_pair,
    normalize_doc_number,
    overlay_number,
    overlay_vendor,
)
from pdfsplit.chat import estimate_chat_usd
from pdfsplit.config import settings as default_settings


_RULE = "═" * 64
_THIN = "─" * 64

_SECRET_ENV_NAMES = (
    "MISTRAL_API_KEY",
    "AIA_SESSION_COOKIES",
    "AIA_API_BASE",
    "DEMO_TOKEN",
    "AUTHORIZATION",
)

_RE_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_RE_COOKIE_HEADER = re.compile(
    r"(?:Set-Cookie|Cookie)\s*[:=]\s*\S+", re.IGNORECASE
)


def on_render() -> bool:
    """True when running on Render (any of the platform-injected vars)."""
    return bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_SERVICE_ID")
        or os.getenv("RENDER_EXTERNAL_URL")
    )


def boundary_debug_enabled() -> bool:
    """Local opt-in. Forced off on Render even if BOUNDARY_DEBUG is set."""
    if on_render():
        return False
    raw = (os.getenv("BOUNDARY_DEBUG") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def redact_secrets(text: str) -> str:
    """Strip credentials / cookies / tokens. Page invoice text is kept."""
    out = text or ""
    for name in _SECRET_ENV_NAMES:
        val = (os.getenv(name) or "").strip()
        if len(val) >= 4:
            out = out.replace(val, f"[{name}=redacted]")
    try:
        key = (default_settings.mistral_api_key or "").strip()
        if len(key) >= 4:
            out = out.replace(key, "[MISTRAL_API_KEY=redacted]")
    except Exception:  # noqa: BLE001
        pass
    out = _RE_BEARER.sub("Bearer [redacted]", out)
    out = _RE_COOKIE_HEADER.sub("[cookie redacted]", out)
    return out


def _dash(value: Any) -> str:
    s = ("" if value is None else str(value)).strip()
    return s if s else "—"


def _clip(value: Any, n: int) -> str:
    s = re.sub(r"\s+", " ", ("" if value is None else str(value))).strip()
    if not s:
        return "—"
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _starts_of(segments: list[dict[str, Any]]) -> list[int]:
    return sorted(
        {
            int(s.get("page_start") or 0)
            for s in segments
            if int(s.get("page_start") or 0) >= 1
        }
    )


def _fmt_range(seg: dict[str, Any]) -> str:
    return f"p{int(seg.get('page_start') or 0)}-{int(seg.get('page_end') or 0)}"


def _constraint_on_page(constraints: list[Any], page: int) -> Any | None:
    hits = [c for c in constraints if int(getattr(c, "page", 0) or 0) == page]
    if not hits:
        return None
    return max(hits, key=lambda c: SIGNAL_RANK.get(getattr(c, "signal", ""), 0))


def caught_by_tags(
    segments: list[dict[str, Any]],
    overlay: dict[str, Any] | None,
    constraints: list[Any] | None,
) -> list[str]:
    """Packet-level tags, same idea as the benchmark ``caught_by`` column."""
    tags: list[str] = []
    ov = overlay or {}
    if ov.get("extent_hold_pages"):
        tags.append("declared_extent")
    counts = ov.get("tier_counts") or {}
    if int(counts.get("merge") or 0):
        tags.append("overlay_merge")
    if int(counts.get("blocked") or 0):
        tags.append("overlay_blocked")
    if int(counts.get("suggest") or 0):
        tags.append("overlay_suggest")
    if not tags and constraints:
        signals = {getattr(c, "signal", "") for c in constraints}
        for name in ("declared_extent", "document_complete", "page_1_of_n", "closing_page"):
            if name in signals:
                tags.append(name)
    return tags


def _caught_by_segment(
    seg: dict[str, Any], overlay: dict[str, Any] | None
) -> str:
    tier = (seg.get("overlay_tier") or "").strip()
    if tier == "merge":
        return "overlay_merge"
    signals = seg.get("signals") or []
    if "declared_extent" in signals:
        return "declared_extent"
    if "judge_shared_number" in signals:
        return "overlay_merge"
    if tier == "blocked":
        return "overlay_blocked"
    if tier == "suggest":
        return "overlay_suggest"
    if overlay and int(seg.get("page_start") or 0) == 1:
        if overlay.get("extent_hold_pages") and len(
            [
                s
                for s in (overlay.get("merges") or [])
            ]
        ) == 0:
            holds = overlay.get("extent_hold_pages") or []
            end = int(seg.get("page_end") or 0)
            if holds and end >= max(holds):
                return "declared_extent"
    evidence = " ".join(seg.get("evidence") or [])
    if "declares extent" in evidence:
        return "declared_extent"
    return "—"


def format_trace(payload: dict[str, Any]) -> str:
    """Human-readable, aligned, sectioned. Not JSON."""
    lines: list[str] = [_RULE, " BOUNDARY TRACE", _RULE]
    lines += [
        f" session   {_dash(payload.get('session_id'))}",
        f" file      {_dash(payload.get('filename'))}",
        f" pages     {_dash(payload.get('page_count'))}",
        f" provider  {_dash(payload.get('provider'))}",
        f" model     {_dash(payload.get('model'))}",
        _THIN,
        " PAGE EVIDENCE  (what the judge is given)",
        _THIN,
    ]
    pages = payload.get("page_evidence") or []
    if not pages:
        lines.append(" (none)")
    for row in pages:
        page = row.get("page")
        dims = row.get("dimensions") or {}
        wh = "—"
        try:
            w, h = dims.get("width"), dims.get("height")
            if w and h:
                wh = f"{int(w)}x{int(h)}"
        except (TypeError, ValueError):
            wh = "—"
        lines.append(
            f" p{page:<3} {wh:<11}  letterhead: {_clip(row.get('title'), 72)}"
        )
        lines.append(
            f"      doc# regex: {_dash(row.get('doc_number'))}"
        )
        lines.append(f"      head: {_clip(row.get('head'), 120)}")
        lines.append(f"      tail: {_clip(row.get('tail'), 80)}")

    lines += [_THIN, " LAYER 1 CONSTRAINTS  (before the judge)", _THIN]
    constraints = payload.get("constraints") or []
    if not constraints:
        lines.append(" none")
    else:
        for c in constraints:
            if isinstance(c, dict):
                page = c.get("page")
                kind = c.get("kind")
                signal = c.get("signal")
                detail = c.get("detail")
            else:
                page = getattr(c, "page", "")
                kind = getattr(c, "kind", "")
                signal = getattr(c, "signal", "")
                detail = getattr(c, "detail", "")
            rank = SIGNAL_RANK.get(str(signal), 0)
            if isinstance(c, dict) and c.get("rank") is not None:
                rank = int(c.get("rank") or rank)
            suppressed_by = (
                c.get("suppressed_by") if isinstance(c, dict) else None
            )
            suppressed_rank = (
                c.get("suppressed_by_rank") if isinstance(c, dict) else None
            )
            if suppressed_by:
                sr = suppressed_rank if suppressed_rank is not None else SIGNAL_RANK.get(str(suppressed_by), 0)
                lines.append(
                    f" p{page:<3} {kind:<16} {signal:<22} r{rank:<3}  "
                    f"SUPPRESSED by {suppressed_by} (r{sr})"
                )
            else:
                lines.append(
                    f" p{page:<3} {kind:<16} {signal:<22} r{rank:<3}  {detail}"
                )

    judge = payload.get("judge") or {}
    lines += [_THIN, " JUDGE CALL", _THIN]
    called = bool(judge.get("called"))
    if not called:
        lines.append(
            f" model     {_dash(judge.get('model') or '(not called)')}"
        )
        lines.append(
            f" tokens    in=0  out=0   —ms   $0"
            f"    {_dash(judge.get('reason') or 'heuristic / no chat')}"
        )
    else:
        usage = judge.get("usage") or {}
        pin = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        pout = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        cost = judge.get("cost_usd")
        if cost is None:
            cost = 0.0
        lines.append(f" model     {_dash(judge.get('model'))}")
        lines.append(
            f" tokens    in={pin}  out={pout}   "
            f"{judge.get('latency_ms', '—')}ms   ${cost}"
        )
    jstarts = judge.get("starts") or []
    lines.append(f" starts    {jstarts if jstarts else '—'}")
    jsegs = judge.get("segments") or []
    if not jsegs:
        lines.append(" proposal  (none)")
    for seg in jsegs:
        conf = seg.get("judge_confidence")
        conf_s = f"{float(conf):.2f}" if conf is not None else "—"
        lines.append(
            f" {_fmt_range(seg):<8}  {(_dash(seg.get('doc_type'))):<16}  "
            f"vendor={_clip(seg.get('vendor'), 36)}  "
            f"number={_dash(seg.get('document_number'))}  "
            f"judge_conf={conf_s}"
        )
        lines.append(f"           reason: {_dash(seg.get('judge_reason'))}")

    lines += [_THIN, " RECONCILE  (constraints vs judge)", _THIN]
    recon = payload.get("reconcile") or []
    if not recon:
        lines.append(" none — judge and constraints agreed (or judge not called)")
    else:
        for row in recon:
            lines.append(f" {row}")

    lines += [_THIN, " OVERLAY", _THIN]
    pairs = payload.get("overlay_pairs") or []
    if not pairs:
        lines.append(" no pairs reached the overlay")
    else:
        for row in pairs:
            lines.append(f" {row}")

    lines += [_THIN, " FINAL", _THIN]
    final = payload.get("final_segments") or []
    packet_tags = payload.get("caught_by") or []
    lines.append(f" caught_by {packet_tags if packet_tags else '—'}")
    if not final:
        lines.append(" (no documents)")
    for seg in final:
        derived = seg.get("confidence")
        derived_s = f"{float(derived):.2f}" if derived is not None else "—"
        lines.append(
            f" {_fmt_range(seg):<8}  {(_dash(seg.get('doc_type'))):<16}  "
            f"{_dash(seg.get('document_number')):<22}  "
            f"caught_by={_caught_by_segment(seg, payload.get('overlay'))}  "
            f"signals_conf={derived_s}"
        )

    warnings = payload.get("warnings") or []
    lines += [_THIN, " CONTRADICTION WARNING", _THIN]
    if not warnings:
        lines.append(" none")
    else:
        for w in warnings:
            lines.append(f" *** {w}")
    analyze = payload.get("analyze_response")
    if isinstance(analyze, dict) and analyze:
        lines += [
            _THIN,
            " ANALYZE RESPONSE  (same body as POST /analyze)",
            _THIN,
            json.dumps(analyze, indent=4, default=str, ensure_ascii=False),
        ]
    lines.append(_RULE)
    return "\n".join(lines) + "\n"


def page_evidence_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exactly the structured hints the judge sees, clipped for the trace."""
    from pdfsplit.providers.mistral import (
        _page_markdown_head_tail,
        extract_boundary_evidence,
        page_boundary_summaries,
    )

    rows: list[dict[str, Any]] = []
    summaries = page_boundary_summaries(pages)
    for i, page in enumerate(pages):
        summary = summaries[i] if i < len(summaries) else {}
        md = page.get("markdown") or ""
        head, tail = _page_markdown_head_tail(md)
        evidence = extract_boundary_evidence(md)
        rows.append(
            {
                "page": i + 1,
                "title": summary.get("title") or "",
                "doc_number": evidence.get("doc_number"),
                "head": head[:120],
                "tail": tail[-80:] if tail else "",
                "dimensions": page.get("dimensions") or {},
            }
        )
    return rows


def constraints_as_dicts(constraints: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in constraints:
        if isinstance(c, dict):
            out.append(dict(c))
            continue
        out.append(
            {
                "page": int(getattr(c, "page", 0) or 0),
                "kind": getattr(c, "kind", ""),
                "signal": getattr(c, "signal", ""),
                "detail": getattr(c, "detail", ""),
                "rank": SIGNAL_RANK.get(getattr(c, "signal", ""), 0),
                "suppressed_by": None,
                "suppressed_by_rank": None,
            }
        )
    return out


def reconcile_lines(
    judge_starts: list[int],
    after_starts: list[int],
    constraints: list[Any],
    n_pages: int,
) -> list[str]:
    """Every boundary where the judge and the constraints disagree."""
    jset = {1, *[s for s in judge_starts if 1 < s <= n_pages]}
    aset = {1, *[s for s in after_starts if 1 < s <= n_pages]}
    lines: list[str] = []
    for page in range(2, n_pages + 1):
        j_split = page in jset
        a_split = page in aset
        if j_split == a_split:
            continue
        winner = _constraint_on_page(constraints, page)
        judge_s = "SPLIT" if j_split else "HOLD"
        if winner is None:
            lines.append(
                f"p{page}  judge: {judge_s}  ·  (no constraint on this page)  "
                f"→  {'split kept' if a_split else 'split dropped'}"
            )
            continue
        signal = getattr(winner, "signal", "")
        kind = getattr(winner, "kind", "")
        rank = SIGNAL_RANK.get(signal, 0)
        others = [
            c
            for c in constraints
            if int(getattr(c, "page", 0) or 0) == page and c is not winner
        ]
        beat = ""
        if others:
            o = others[0]
            beat = (
                f" beats {getattr(o, 'signal', '')} "
                f"({SIGNAL_RANK.get(getattr(o, 'signal', ''), 0)})"
            )
        if kind == "must_not_split" and j_split and not a_split:
            outcome = "split rejected"
        elif kind == "must_split" and (not j_split) and a_split:
            outcome = "split inserted"
        else:
            outcome = "HOLD" if not a_split else "SPLIT"
        lines.append(
            f"p{page}  judge: {judge_s}  ·  {signal} ({rank}){beat}  →  {outcome}"
        )
    return lines


def overlay_pair_lines(
    pre_segments: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> list[str]:
    if len(pre_segments) < 2:
        return []
    lines: list[str] = []
    for i in range(len(pre_segments) - 1):
        left, right = pre_segments[i], pre_segments[i + 1]
        num_l = overlay_number(left, pages)
        num_r = overlay_number(right, pages)
        ven_l = overlay_vendor(left, pages)
        ven_r = overlay_vendor(right, pages)
        pair = classify_adjacent_pair(left, right, pages)
        left_r = _fmt_range(left)
        right_r = _fmt_range(right)
        g_l = "yes" if num_l else "no"
        g_r = "yes" if num_r else "no"
        header = (
            f"{left_r} | {right_r}  "
            f"num L={_dash(num_l)} (grounded {g_l})  "
            f"R={_dash(num_r)} (grounded {g_r})"
        )
        lines.append(header)
        if pair is None:
            same = (
                num_l
                and num_r
                and normalize_doc_number(num_l) == normalize_doc_number(num_r)
            )
            why = (
                "numbers do not qualify (empty or unequal)"
                if not same
                else "numbers equal but overlay declined"
            )
            lines.append(
                f"           vendors L={_clip(ven_l, 40)}  R={_clip(ven_r, 40)}"
            )
            lines.append(f"           skipped — {why}")
            continue
        rel = pair.vendor_relation
        lines.append(
            f"           vendors {rel.upper():<6}  "
            f"L={_clip(pair.vendor_left, 36)}  R={_clip(pair.vendor_right, 36)}"
        )
        lines.append(f"           {pair.tier.upper():<8}  {pair.reason}")
    return lines


_RE_CONTINUATION_REASON = re.compile(
    r"(?:"
    r"\bsame\s+(?:supplier|vendor|letterhead|bill|invoice|document|"
    r"number|reference|gstin)\b|"
    r"\bcontinues?\b|"
    r"\bcontinuation\b|"
    r"\bshared\s+(?:bill|invoice|number|reference)\b|"
    r"\bbelongs?\s+to\s+(?:the\s+)?(?:same|previous)\b|"
    r"\bnot\s+a\s+new\s+document\b|"
    r"\bstill\s+the\s+same\b"
    r")",
    re.IGNORECASE,
)


_RE_CUT_JUSTIFICATION = re.compile(
    r"(?:"
    r"\bdespite\b|"
    r"\bbegins?\s+here\b|"
    r"\bindicat(?:es|ing)\s+a\s+new\s+document\b"
    r")",
    re.IGNORECASE,
)


def reason_asserts_continuation(reason: str) -> bool:
    """True when a start-reason describes sameness, not a cut."""
    text = reason or ""
    if not _RE_CONTINUATION_REASON.search(text):
        return False
    if _RE_CUT_JUSTIFICATION.search(text):
        return False
    return True


def contradiction_warnings(
    segments: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> list[str]:
    """Adjacent segments carrying the same grounded number — the GGPL tell.

    Vendor mismatch is not a contradiction: overlay is supposed to keep
    those split. Warn when the number is shared and vendors do not differ.

    Also warn when a proposed start's ``judge_reason`` asserts continuation
    or sameness — that reason is an argument against the cut. Skip that
    warning when the two sides clearly have different vendors (legitimate
    duplicate-number case like pkt_b06): a "same number" phrase in the
    reason is not a cry for merge when the suppliers differ.
    """
    from pdfsplit.boundary_overlay import vendors_relation

    warnings: list[str] = []
    ordered = sorted(segments, key=lambda s: int(s.get("page_start") or 0))
    for i, seg in enumerate(ordered):
        start = int(seg.get("page_start") or 0)
        if start <= 1:
            continue
        reason = str(seg.get("judge_reason") or "")
        if not reason_asserts_continuation(reason):
            continue
        prev = ordered[i - 1] if i > 0 else None
        if prev is None or int(prev.get("page_end") or 0) + 1 != start:
            prev = next(
                (
                    s
                    for s in ordered
                    if int(s.get("page_end") or 0) + 1 == start
                ),
                None,
            )
        if prev is not None:
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
            if vendors_relation(ven_l, ven_r, left_mds, right_mds) == "differ":
                continue
        warnings.append(
            f"judge start at p{start} but reason asserts continuation: "
            f"{reason}"
        )
    for i in range(len(segments) - 1):
        left, right = segments[i], segments[i + 1]
        num_l = overlay_number(left, pages)
        num_r = overlay_number(right, pages)
        if not num_l or not num_r:
            continue
        if normalize_doc_number(num_l) != normalize_doc_number(num_r):
            continue
        ven_l = overlay_vendor(left, pages)
        ven_r = overlay_vendor(right, pages)
        left_mds = [
            (pages[p - 1].get("markdown") or "")
            if 0 <= p - 1 < len(pages)
            else ""
            for p in range(int(left["page_start"]), int(left["page_end"]) + 1)
        ]
        right_mds = [
            (pages[p - 1].get("markdown") or "")
            if 0 <= p - 1 < len(pages)
            else ""
            for p in range(int(right["page_start"]), int(right["page_end"]) + 1)
        ]
        if vendors_relation(ven_l, ven_r, left_mds, right_mds) == "differ":
            continue
        warnings.append(
            f"judge split {_fmt_range(left)} | {_fmt_range(right)} "
            f"but BOTH carry {num_l}"
        )
    return warnings


_CLIENT_SEG_KEYS = (
    "page_start",
    "page_end",
    "doc_type",
    "vendor",
    "document_number",
    "confidence",
    "evidence",
    "signals",
    "review_status",
    "review_reasons",
    "vendor_source",
    "number_source",
    "aia_file_name",
    "naming_fallback",
    "overlay_tier",
    "overlay_reason",
)


def client_analyze_payload(
    *,
    segments: list[dict[str, Any]],
    pre_overlay: list[dict[str, Any]],
    overlay: dict[str, Any] | None,
    pages_processed: int,
    page_count: int,
    errors: list[str] | None,
    is_replay: bool,
    auto_excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The JSON the browser sees on POST /analyze — for the console dump."""

    def _seg(seg: dict[str, Any]) -> dict[str, Any]:
        out = {k: seg[k] for k in _CLIENT_SEG_KEYS if k in seg}
        if "page_start" not in out and "page_start" in seg:
            out["page_start"] = seg["page_start"]
        return out

    return {
        "segments": [_seg(s) for s in segments],
        "pages_processed": pages_processed,
        "page_count": page_count,
        "errors": list(errors or []),
        "is_replay": bool(is_replay),
        "auto_excluded_pages": list(auto_excluded_pages or []),
        "overlay": overlay or {
            "merges": [],
            "suggestions": [],
            "blocked": [],
            "extent_hold_pages": [],
            "tier_counts": {"merge": 0, "suggest": 0, "blocked": 0},
        },
        "pre_overlay_segments": [_seg(s) for s in pre_overlay],
    }


def emit_trace(
    payload: dict[str, Any], *, session_dir: Path | None = None
) -> str:
    """Print to the server console and persist next to the session.

    No-op (empty string) when the gate is off. Never writes secrets.
    """
    if not boundary_debug_enabled():
        return ""
    text = redact_secrets(format_trace(payload))
    print(text, file=sys.stderr, flush=True)
    if session_dir is not None:
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "boundary_trace.txt").write_text(text)
        except OSError:
            pass
    return text


def estimate_judge_cost(usage: dict[str, Any] | None) -> float:
    return estimate_chat_usd(
        usage or {},
        input_usd_per_mtok=float(
            getattr(default_settings, "mistral_chat_input_usd_per_mtok", 0.15)
            or 0.15
        ),
        output_usd_per_mtok=float(
            getattr(default_settings, "mistral_chat_output_usd_per_mtok", 0.6)
            or 0.6
        ),
    )
