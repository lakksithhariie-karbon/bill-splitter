"""Boundary-accuracy benchmark over the synthetic corpus.

Uses scoring.py (precision / recall / F1, exact-doc accuracy, perfect-packet
rate). This is the headline harness for the split pipeline.

IMPORTANT: synthetic packets are easier than real customer PDFs. Numbers from
this harness must not be quoted as a real-world rate.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pdfsplit.benchmark import load_ground_truth, load_manifest
from pdfsplit.boundary_constraints import (
    annotate_segments_with_evidence,
    compute_boundary_constraints,
    enforce_constraints_on_starts,
)
from pdfsplit.config import Settings, settings as default_settings
from pdfsplit.providers.mistral import (
    find_boundaries_with_llm,
    find_document_boundaries,
    starts_to_segments,
)
from pdfsplit.schema import SplitDocument, SplitResult
from pdfsplit.scoring import (
    DocTruth,
    boundary_metrics,
    exact_doc_accuracy,
    perfect_packet,
    score_packet as score_ranges_and_classes,
)


Mode = Literal["llm", "heuristic"]

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "vendors" / "mistral" / "fixtures"
)
_GGPL_FIXTURE = _FIXTURE_DIR / "pkt_ggpl_continuation_annexure.json"
_GGPL_BARE_FIXTURE = _FIXTURE_DIR / "pkt_ggpl_bare_number_annexure.json"
_CONTRACT_NOTE_FIXTURE = _FIXTURE_DIR / "pkt_contract_note_signature_page.json"

#: OCR-replay packets appended when ``limit is None`` (over-split / overlay).
_OCR_FIXTURE_SPECS: list[tuple[str, Path, list[str]]] = [
    (
        "pkt_ggpl_continuation_annexure",
        _GGPL_FIXTURE,
        ["over-split", "annexure", "continuation"],
    ),
    (
        "pkt_ggpl_bare_number_annexure",
        _GGPL_BARE_FIXTURE,
        ["over-split", "annexure", "overlay-merge"],
    ),
    (
        "pkt_contract_note_signature_page",
        _CONTRACT_NOTE_FIXTURE,
        ["over-split", "signature-page", "contract-note"],
    ),
]


def _apply_names_and_overlay(
    segs: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Ground names then run the post-judge overlay. No API calls."""
    extra: dict[str, int] = {}
    try:
        from pdfsplit.aia.filenames import attach_names_to_segments
        from pdfsplit.boundary_overlay import attach_overlay_and_extent

        attach_names_to_segments(segs, pages, packet_stem="packet")
        segs, overlay, _pre = attach_overlay_and_extent(segs, pages)
        attach_names_to_segments(segs, pages, packet_stem="packet")
        counts = overlay.get("tier_counts") or {}
        extra["overlay_merge"] = int(counts.get("merge") or 0)
        extra["overlay_suggest"] = int(counts.get("suggest") or 0)
        extra["overlay_blocked"] = int(counts.get("blocked") or 0)
        # Stash the overlay dict on the first segment for per-packet reporting.
        if segs:
            segs[0] = dict(segs[0])
            segs[0]["_overlay"] = overlay
    except Exception:  # noqa: BLE001 — overlay must never abort the harness
        extra["overlay_error"] = 1
    return segs, extra


@dataclass
class BoundaryPacketRun:
    packet_id: str
    category: str
    scenario_tags: list[str]
    pages: int
    truth: list[DocTruth]
    result: SplitResult
    score: dict
    signal_counts: dict[str, int] = field(default_factory=dict)
    layer1: bool = True
    overlay: dict[str, Any] = field(default_factory=dict)


def pages_from_pdf_text(pdf_path: Path) -> list[dict[str, Any]]:
    """Build OCR-like page dicts from PDF text + mediabox (no API).

    Used by ``--provider mock`` so the harness is testable offline. Not a
    substitute for Mistral OCR on real packets.
    """
    import pikepdf
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages: list[dict[str, Any]] = []
    with pikepdf.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            box = page.mediabox
            w = float(box[2] - box[0])
            h = float(box[3] - box[1])
            try:
                text = reader.pages[i].extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            # Fake a title block from the first non-empty line for the heuristic.
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            blocks: list[dict[str, Any]] = []
            if first:
                blocks.append(
                    {
                        "type": "title",
                        "content": first[:120],
                        "top_left_y": 40,
                    }
                )
            pages.append(
                {
                    "index": i,
                    "markdown": text,
                    "dimensions": {
                        "dpi": 72,
                        "width": round(w),
                        "height": round(h),
                    },
                    "blocks": blocks,
                }
            )
    return pages


def segments_to_split_result(
    segments: list[dict[str, Any]],
    *,
    input_ref: str,
    provider: str,
    model_version: str,
    latency_ms: float,
    errors: list[str],
) -> SplitResult:
    docs = [
        SplitDocument(
            doc_type=str(s.get("doc_type") or "unknown"),
            start_page=int(s["page_start"]),
            end_page=int(s["page_end"]),
            confidence=float(s.get("confidence") or 0.0),
            text=None,
        )
        for s in segments
    ]
    return SplitResult(
        input_ref=input_ref,
        provider=provider,
        model_version=model_version,
        documents=docs,
        latency_ms=latency_ms,
        cost_usd=0.0,
        errors=list(errors),
    )


def run_boundaries_on_pages(
    pages: list[dict[str, Any]],
    *,
    mode: Mode,
    layer1: bool,
    use_signature: bool,
    chat: Any | None,
    settings: Settings,
    trace: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Run one boundary strategy; return segments, errors, signal counts."""
    constraints = (
        compute_boundary_constraints(pages, use_signature=use_signature)
        if layer1
        else []
    )
    signal_counts = Counter(c.signal for c in constraints)

    if mode == "heuristic" or chat is None:
        starts = find_document_boundaries(pages)
        if layer1:
            starts = enforce_constraints_on_starts(starts, len(pages), constraints)
        segs = starts_to_segments(starts, len(pages))
        segs = annotate_segments_with_evidence(
            segs, constraints, llm_used=False
        )
        segs, extra_signals = _apply_names_and_overlay(segs, pages)
        signal_counts.update(extra_signals)
        return segs, [], dict(signal_counts)

    segs, errors = find_boundaries_with_llm(
        pages,
        chat=chat,
        settings=settings,
        use_constraints=layer1,
        use_signature_signal=use_signature,
        trace=trace,
    )
    segs, extra_signals = _apply_names_and_overlay(segs, pages)
    signal_counts.update(extra_signals)
    return segs, errors, dict(signal_counts)


def boundary_perfect(truth: list[DocTruth], pred: list[SplitDocument]) -> bool:
    """Perfect ranges ignoring class labels (split product cares about bounds)."""
    if not truth:
        return False
    t_ranges = sorted((t.start_page, t.end_page) for t in truth)
    p_ranges = sorted((p.start_page, p.end_page) for p in pred)
    return t_ranges == p_ranges


def score_boundary_packet(
    truth: list[DocTruth], result: SplitResult
) -> dict[str, Any]:
    base = score_ranges_and_classes(truth, result.documents)
    base["boundary_perfect"] = boundary_perfect(truth, result.documents)
    # Boundary-only exactness (range match, ignore class).
    if not truth:
        base["boundary_exact_doc_accuracy"] = 0.0
    else:
        matched = 0
        for t in truth:
            if any(
                p.start_page == t.start_page and p.end_page == t.end_page
                for p in result.documents
            ):
                matched += 1
        base["boundary_exact_doc_accuracy"] = round(matched / len(truth), 4)
    return base


def run_boundary_benchmark(
    corpus_dir: Path,
    out_dir: Path,
    *,
    provider: Literal["mistral", "mock"] = "mock",
    mode: Mode = "llm",
    layer1: bool = True,
    use_signature: bool = False,
    limit: int | None = None,
    settings: Settings | None = None,
    compare_baseline: bool = False,
    include_over_split_fixture: bool | None = None,
) -> dict[str, Any]:
    """Run boundary pipeline over the synthetic corpus and score it.

    ``provider=mock`` never calls Mistral — pages come from PDF text extract.
    ``provider=mistral`` OCRs each packet (costs money) then runs the judge.

    When ``compare_baseline`` is true, also runs without Layer 1 (same pages)
    and reports before/after deltas.
    """
    cfg = settings or default_settings
    manifest = load_manifest(corpus_dir)
    packets = list(manifest["packets"])
    if limit:
        packets = packets[:limit]

    chat = None
    ocr_provider = None
    if provider == "mistral" and mode == "llm":
        from pdfsplit.chat import ChatClient
        from pdfsplit.providers.mistral import MistralSplitterProvider

        if not cfg.has_mistral_credentials():
            raise ValueError("MISTRAL_API_KEY required for --provider mistral")
        chat = ChatClient(settings=cfg)
        ocr_provider = MistralSplitterProvider(settings=cfg)
    else:
        # mock + llm mode falls back to heuristic (no chat).
        if provider == "mock" and mode == "llm":
            mode = "heuristic"

    # OCR / page materialisation once per packet (shared across before/after).
    page_cache: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        pdf_path = corpus_dir / packet["file"]
        if provider == "mistral":
            assert ocr_provider is not None
            data, name = ocr_provider._read_input(pdf_path)
            ocr_provider._check_limits(data, name)
            resp = ocr_provider._ocr(data, include_blocks=True, annotate=False)
            page_cache[packet["packet_id"]] = resp.get("pages") or []
            ocr_provider._ocr_response = resp
        else:
            page_cache[packet["packet_id"]] = pages_from_pdf_text(pdf_path)

    if include_over_split_fixture is None:
        include_over_split_fixture = limit is None
    if include_over_split_fixture:
        for packet_id, path, tags in _OCR_FIXTURE_SPECS:
            if not path.is_file():
                continue
            blob = json.loads(path.read_text())
            fx_pages = list(blob.get("pages") or [])
            fx_packet = {
                "packet_id": packet_id,
                "file": str(path),
                "category": "ocr-fixture",
                "scenario_tags": list(tags),
                "pages": len(fx_pages),
                "expected_documents": [
                    {
                        "class": "invoice",
                        "start_page": 1,
                        "end_page": len(fx_pages) or 3,
                    }
                ],
            }
            packets.append(fx_packet)
            page_cache[packet_id] = fx_pages

    def _score_packet(
        packet: dict,
        pages: list[dict[str, Any]],
        segs: list[dict[str, Any]],
        errors: list[str],
        sigs: dict[str, int],
        *,
        layer: bool,
        latency_ms: float,
    ) -> BoundaryPacketRun:
        file_field = packet.get("file") or packet["packet_id"]
        pdf_path = Path(file_field)
        if not pdf_path.is_absolute():
            pdf_path = corpus_dir / file_field
        if packet.get("expected_documents"):
            truth = load_ground_truth({"packets": [packet]}, packet)
        else:
            truth = load_ground_truth(manifest, packet)
        model_version = (
            ocr_provider.model_version
            if ocr_provider is not None
            else "mock-pdf-text"
        )
        segs = [dict(s) for s in segs]
        overlay = {}
        if segs and isinstance(segs[0].get("_overlay"), dict):
            overlay = segs[0].pop("_overlay")
            segs[0] = {k: v for k, v in segs[0].items() if k != "_overlay"}
        if provider == "mock":
            for seg in segs:
                for t in truth:
                    if (
                        t.start_page == int(seg["page_start"])
                        and t.end_page == int(seg["page_end"])
                    ):
                        seg["doc_type"] = t.doc_type
                        break
        result = segments_to_split_result(
            segs,
            input_ref=str(pdf_path),
            provider=provider,
            model_version=model_version,
            latency_ms=round(latency_ms, 1),
            errors=errors,
        )
        score = score_boundary_packet(truth, result)
        return BoundaryPacketRun(
            packet_id=packet["packet_id"],
            category=packet["category"],
            scenario_tags=list(packet.get("scenario_tags") or []),
            pages=int(packet["pages"]),
            truth=truth,
            result=result,
            score=score,
            signal_counts=sigs,
            layer1=layer,
            overlay=overlay,
        )

    after_runs: list[BoundaryPacketRun] = []
    before_runs: list[BoundaryPacketRun] | None = (
        [] if (compare_baseline and layer1) else None
    )

    for packet in packets:
        pages = page_cache[packet["packet_id"]]
        t0 = time.perf_counter()
        if compare_baseline and layer1 and mode == "llm" and chat is not None:
            # One LLM call without constraints; Layer 1 = enforce on same starts.
            segs_base, errors, _ = run_boundaries_on_pages(
                pages,
                mode=mode,
                layer1=False,
                use_signature=False,
                chat=chat,
                settings=cfg,
            )
            constraints = compute_boundary_constraints(
                pages, use_signature=use_signature
            )
            sigs = dict(Counter(c.signal for c in constraints))
            starts = [int(s["page_start"]) for s in segs_base]
            types = {
                int(s["page_start"]): str(s.get("doc_type") or "unknown")
                for s in segs_base
            }
            starts_l1 = enforce_constraints_on_starts(
                starts, len(pages), constraints
            )
            segs_l1 = annotate_segments_with_evidence(
                starts_to_segments(starts_l1, len(pages), types),
                constraints,
                llm_used=True,
            )
            segs_l1, extra = _apply_names_and_overlay(segs_l1, pages)
            sigs.update(extra)
            latency = (time.perf_counter() - t0) * 1000.0
            assert before_runs is not None
            before_runs.append(
                _score_packet(
                    packet,
                    pages,
                    segs_base,
                    errors,
                    {},
                    layer=False,
                    latency_ms=latency,
                )
            )
            after_runs.append(
                _score_packet(
                    packet,
                    pages,
                    segs_l1,
                    errors,
                    sigs,
                    layer=True,
                    latency_ms=latency,
                )
            )
        else:
            segs, errors, sigs = run_boundaries_on_pages(
                pages,
                mode=mode,
                layer1=layer1,
                use_signature=use_signature,
                chat=chat,
                settings=cfg,
            )
            latency = (time.perf_counter() - t0) * 1000.0
            after_runs.append(
                _score_packet(
                    packet,
                    pages,
                    segs,
                    errors,
                    sigs,
                    layer=layer1,
                    latency_ms=latency,
                )
            )
            if before_runs is not None:
                segs_b, err_b, _ = run_boundaries_on_pages(
                    pages,
                    mode=mode,
                    layer1=False,
                    use_signature=False,
                    chat=None if mode == "heuristic" else chat,
                    settings=cfg,
                )
                before_runs.append(
                    _score_packet(
                        packet,
                        pages,
                        segs_b,
                        err_b,
                        {},
                        layer=False,
                        latency_ms=latency,
                    )
                )

    report = _build_report(
        after_runs,
        provider=provider,
        mode=mode,
        layer1=layer1,
        use_signature=use_signature,
        corpus_version=manifest.get("corpus_version"),
        before_runs=before_runs,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"boundary_{provider}_{'l1' if layer1 else 'base'}.json"
    path.write_text(json.dumps(report, indent=2))
    report["report_path"] = str(path)
    return report


def _summarize_runs(runs: list[BoundaryPacketRun]) -> dict[str, Any]:
    if not runs:
        return {}

    def avg(key: str) -> float:
        vals = [r.score[key] for r in runs]
        return round(statistics.mean(vals), 4) if vals else 0.0

    by_category: dict[str, dict[str, Any]] = {}
    by_tag: dict[str, dict[str, Any]] = {}
    for r in runs:
        b = by_category.setdefault(r.category, {"count": 0, "perfect": 0, "boundary_perfect": 0})
        b["count"] += 1
        b["perfect"] += int(r.score["perfect"])
        b["boundary_perfect"] += int(r.score["boundary_perfect"])
        for tag in r.scenario_tags:
            t = by_tag.setdefault(tag, {"count": 0, "perfect": 0, "boundary_perfect": 0})
            t["count"] += 1
            t["perfect"] += int(r.score["perfect"])
            t["boundary_perfect"] += int(r.score["boundary_perfect"])

    signal_totals: Counter[str] = Counter()
    overlay_tiers: Counter[str] = Counter()
    for r in runs:
        signal_totals.update(r.signal_counts)
        ov = r.overlay or {}
        counts = ov.get("tier_counts") or {}
        overlay_tiers["merge"] += int(counts.get("merge") or 0)
        overlay_tiers["suggest"] += int(counts.get("suggest") or 0)
        overlay_tiers["blocked"] += int(counts.get("blocked") or 0)

    return {
        "packets": len(runs),
        "pages": sum(r.pages for r in runs),
        "avg_boundary_precision": avg("boundary_precision"),
        "avg_boundary_recall": avg("boundary_recall"),
        "avg_boundary_f1": avg("boundary_f1"),
        "avg_exact_doc_accuracy": avg("exact_doc_accuracy"),
        "avg_boundary_exact_doc_accuracy": avg("boundary_exact_doc_accuracy"),
        "perfect_packet_rate": round(
            sum(1 for r in runs if r.score["perfect"]) / len(runs), 4
        ),
        "boundary_perfect_packet_rate": round(
            sum(1 for r in runs if r.score["boundary_perfect"]) / len(runs), 4
        ),
        "by_category": {
            k: {
                "packets": v["count"],
                "perfect_packet_rate": round(v["perfect"] / v["count"], 4),
                "boundary_perfect_packet_rate": round(
                    v["boundary_perfect"] / v["count"], 4
                ),
            }
            for k, v in sorted(by_category.items())
        },
        "by_scenario_tag": {
            k: {
                "packets": v["count"],
                "perfect_packet_rate": round(v["perfect"] / v["count"], 4),
                "boundary_perfect_packet_rate": round(
                    v["boundary_perfect"] / v["count"], 4
                ),
            }
            for k, v in sorted(by_tag.items())
        },
        "signal_firings": dict(signal_totals),
        "overlay_tier_counts": dict(overlay_tiers),
    }


def _build_report(
    runs: list[BoundaryPacketRun],
    *,
    provider: str,
    mode: str,
    layer1: bool,
    use_signature: bool,
    corpus_version: str | None,
    before_runs: list[BoundaryPacketRun] | None,
) -> dict[str, Any]:
    summary = _summarize_runs(runs)
    corpus_runs = [r for r in runs if r.category != "ocr-fixture"]
    report: dict[str, Any] = {
        "disclaimer": (
            "SYNTHETIC CORPUS — easier than real customer packets. "
            "Do not quote these rates as real-world boundary accuracy."
        ),
        "provider": provider,
        "mode": mode,
        "layer1": layer1,
        "use_signature_signal": use_signature,
        "corpus_version": corpus_version,
        "summary": summary,
        "per_packet": [
            {
                "packet_id": r.packet_id,
                "category": r.category,
                "scenario_tags": r.scenario_tags,
                "pages": r.pages,
                "perfect": r.score["perfect"],
                "boundary_perfect": r.score["boundary_perfect"],
                "boundary_precision": r.score["boundary_precision"],
                "boundary_recall": r.score["boundary_recall"],
                "boundary_f1": r.score["boundary_f1"],
                "exact_doc_accuracy": r.score["exact_doc_accuracy"],
                "boundary_exact_doc_accuracy": r.score[
                    "boundary_exact_doc_accuracy"
                ],
                "signal_counts": r.signal_counts,
                "overlay": r.overlay,
                "caught_by": _caught_by(r),
                "predicted": [
                    {
                        "type": d.doc_type,
                        "start_page": d.start_page,
                        "end_page": d.end_page,
                        "confidence": d.confidence,
                    }
                    for d in r.result.documents
                ],
                "errors": r.result.errors,
            }
            for r in runs
        ],
    }
    if len(corpus_runs) != len(runs):
        report["corpus_only"] = _summarize_runs(corpus_runs)
    if before_runs is not None:
        before_summary = _summarize_runs(before_runs)
        report["baseline_without_layer1"] = before_summary
        report["delta_perfect_packet_rate"] = round(
            summary.get("perfect_packet_rate", 0)
            - before_summary.get("perfect_packet_rate", 0),
            4,
        )
        report["delta_boundary_perfect_packet_rate"] = round(
            summary.get("boundary_perfect_packet_rate", 0)
            - before_summary.get("boundary_perfect_packet_rate", 0),
            4,
        )
    return report


def _caught_by(run: BoundaryPacketRun) -> list[str]:
    tags: list[str] = []
    ov = run.overlay or {}
    if ov.get("extent_hold_pages"):
        tags.append("declared_extent")
    counts = ov.get("tier_counts") or {}
    if int(counts.get("merge") or 0):
        tags.append("overlay_merge")
    if int(counts.get("blocked") or 0):
        tags.append("overlay_blocked")
    if int(counts.get("suggest") or 0):
        tags.append("overlay_suggest")
    if not tags:
        for name in (
            "declared_extent",
            "closing_page",
            "shared_doc_number",
            "document_complete",
            "page_1_of_n",
        ):
            if int((run.signal_counts or {}).get(name) or 0):
                tags.append(name)
    return tags


def render_boundary_report(report: dict[str, Any]) -> str:
    s = report.get("summary") or {}
    lines = [
        "# Boundary benchmark (synthetic corpus)",
        "",
        f"> {report.get('disclaimer', '')}",
        "",
        f"- Provider: `{report.get('provider')}` | mode: `{report.get('mode')}` "
        f"| Layer 1: {report.get('layer1')} | signature signal: "
        f"{report.get('use_signature_signal')}",
        f"- Packets: {s.get('packets')} | Pages: {s.get('pages')}",
        f"- **Perfect packet rate (ranges+class): {s.get('perfect_packet_rate', 0):.2%}**",
        f"- **Boundary-perfect packet rate (ranges only): "
        f"{s.get('boundary_perfect_packet_rate', 0):.2%}**",
        f"- Boundary F1: {s.get('avg_boundary_f1', 0):.3f} "
        f"(P {s.get('avg_boundary_precision', 0):.3f} / "
        f"R {s.get('avg_boundary_recall', 0):.3f})",
        f"- Exact doc accuracy: {s.get('avg_exact_doc_accuracy', 0):.3f}",
        "",
        "## By category",
        "",
        "| category | packets | PPR | boundary-PPR |",
        "|---|---:|---:|---:|",
    ]
    for cat, row in (s.get("by_category") or {}).items():
        lines.append(
            f"| {cat} | {row['packets']} | {row['perfect_packet_rate']:.2%} | "
            f"{row['boundary_perfect_packet_rate']:.2%} |"
        )
    lines += [
        "",
        "## By scenario tag",
        "",
        "| tag | packets | PPR | boundary-PPR |",
        "|---|---:|---:|---:|",
    ]
    for tag, row in (s.get("by_scenario_tag") or {}).items():
        lines.append(
            f"| {tag} | {row['packets']} | {row['perfect_packet_rate']:.2%} | "
            f"{row['boundary_perfect_packet_rate']:.2%} |"
        )
    lines += [
        "",
        "## Layer-1 signal firings",
        "",
        "| signal | count |",
        "|---|---:|",
    ]
    firings = s.get("signal_firings") or {}
    if not firings:
        lines.append("| _(none)_ | 0 |")
    else:
        for sig, n in sorted(firings.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {sig} | {n} |")

    tiers = s.get("overlay_tier_counts") or {}
    lines += [
        "",
        "## Overlay tiers (adjacent shared-number pairs)",
        "",
        f"- merge: {tiers.get('merge', 0)} | suggest: {tiers.get('suggest', 0)} "
        f"| blocked (vendor mismatch): {tiers.get('blocked', 0)}",
    ]
    if "corpus_only" in report:
        c = report["corpus_only"]
        lines += [
            "",
            "## Corpus-only (excluding OCR fixtures)",
            "",
            f"- Packets: {c.get('packets')} | PPR: {c.get('perfect_packet_rate', 0):.2%} "
            f"| boundary-PPR: {c.get('boundary_perfect_packet_rate', 0):.2%}",
        ]
    lines += [
        "",
        "## Over-split / over-merge pair",
        "",
    ]
    for row in report.get("per_packet") or []:
        pid = row.get("packet_id") or ""
        if pid in {
            "pkt_ggpl_continuation_annexure",
            "pkt_ggpl_bare_number_annexure",
            "pkt_contract_note_signature_page",
            "pkt_b06_duplicate_invoice_number",
        }:
            pred = row.get("predicted") or []
            ranges = ", ".join(
                f"p{d['start_page']}-{d['end_page']}" for d in pred
            )
            lines.append(
                f"- `{pid}`: {len(pred)} doc(s) [{ranges}] "
                f"caught_by={row.get('caught_by')} "
                f"boundary_perfect={row.get('boundary_perfect')}"
            )

    if "baseline_without_layer1" in report:
        b = report["baseline_without_layer1"]
        lines += [
            "",
            "## Before / after Layer 1",
            "",
            f"- Baseline PPR: {b.get('perfect_packet_rate', 0):.2%} → "
            f"after: {s.get('perfect_packet_rate', 0):.2%} "
            f"(Δ {report.get('delta_perfect_packet_rate', 0):+.2%})",
            f"- Baseline boundary-PPR: {b.get('boundary_perfect_packet_rate', 0):.2%} → "
            f"after: {s.get('boundary_perfect_packet_rate', 0):.2%} "
            f"(Δ {report.get('delta_boundary_perfect_packet_rate', 0):+.2%})",
        ]
    return "\n".join(lines) + "\n"
