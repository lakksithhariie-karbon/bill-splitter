"""FastAPI application for the PDF splitter POC.

Endpoints:
  POST /api/upload            upload a PDF -> session id, page count, thumbnails
  GET  /api/session/{id}/page/{n}.png   rendered page (thumb | view)
  POST /api/session/{id}/analyze        stage 1: OCR + boundaries only
  POST /api/session/{id}/extract        stage 2: fields for confirmed segments
  POST /api/session/{id}/vouchers       map documents_v2 → Tally voucher payloads
  POST /api/session/{id}/split          analyze (if needed) + extract (machine segments)
  POST /api/session/{id}/save           cut PDF at corrected boundaries, zip
  POST /api/session/{id}/save-batched   pack into <=N-page batch PDFs + manifest
  GET  /api/providers                   credential status for every provider
  GET  /healthz                         liveness (no auth)
  GET  /                                serve React build (web/dist)
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from pdfsplit.app.auth import LinkTokenMiddleware
from pdfsplit.app.raster import THUMB_DPI, VIEW_DPI, ensure_rendered
from pdfsplit.app.session import SessionStore
from pdfsplit.config import settings
from pdfsplit.pdf import split_pdf_by_ranges, verify_split
from pdfsplit.schema import SplitDocument, SplitResult

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Log AIA cookie expiry DATE only when live push is enabled.
    from pdfsplit.aia.client import AiaApClient
    from pdfsplit.aia.transport import log_session_cookie_expiry

    if AiaApClient.live_writes_enabled(settings):
        try:
            log_session_cookie_expiry()
        except Exception as exc:  # noqa: BLE001
            logger.warning("AIA cookie expiry check failed: %s", type(exc).__name__)
    yield


app = FastAPI(title="aia-pdf-split POC", lifespan=_lifespan)
app.add_middleware(LinkTokenMiddleware)

#: Session store rooted under output/sessions/.
store = SessionStore(settings.output_dir / "sessions")

#: React build output (Vite). Required — no legacy static fallback.
_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_DIST_MISSING_DETAIL = (
    "React app not built. Run: npm --prefix web run build"
)


def _require_web_dist_file(name: str) -> Path:
    path = _WEB_DIST / name
    if not path.is_file():
        raise HTTPException(status_code=503, detail=_DIST_MISSING_DETAIL)
    return path


@app.get("/healthz")
def healthz():
    """Liveness probe — no auth, no session I/O."""
    return {"ok": True}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    """Disallow all crawlers — demo URL must not land in search indexes."""
    return "User-agent: *\nDisallow: /\n"


def _page_count(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def _page_dimensions(pdf_path: Path) -> list[dict[str, float | int]]:
    """Per-page mediabox size in PDF points (rotation-aware).

    Known at upload time — before OCR — so the client can size placeholders
    to the real page shape and avoid layout jump when thumbs arrive.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    out: list[dict[str, float | int]] = []
    for i, page in enumerate(reader.pages):
        box = page.mediabox
        width = float(box.width)
        height = float(box.height)
        rotate = int(page.get("/Rotate") or 0) % 360
        if rotate in (90, 270):
            width, height = height, width
        out.append(
            {
                "page": i + 1,
                "width_pt": round(width, 2),
                "height_pt": round(height, 2),
            }
        )
    return out


def _build_provider():
    """Build the OpenRouter model provider used by the web app."""
    from pdfsplit.providers.openrouter import OpenRouterSplitterProvider

    return OpenRouterSplitterProvider(settings=settings)


def _provider_status(name: str) -> str:
    from pdfsplit.cli import _provider_credential_status

    return _provider_credential_status(name)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    # Validate it is a readable PDF.
    tmp = settings.output_dir / "sessions" / "_probe.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        page_count = _page_count(tmp)
        page_dimensions = _page_dimensions(tmp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Not a readable PDF: {exc}")
    finally:
        tmp.unlink(missing_ok=True)

    max_pages = int(settings.max_upload_pages)
    if page_count > max_pages:
        raise HTTPException(
            status_code=400,
            detail=(
                f"PDF has {page_count} pages; upload limit is {max_pages}. "
                "Split the packet before uploading."
            ),
        )

    session = store.create(file.filename or "upload.pdf", data, page_count)
    thumb_urls = [
        f"/api/session/{session.id}/page/{n}.png?size=thumb"
        for n in range(1, page_count + 1)
    ]
    return {
        "session_id": session.id,
        "page_count": page_count,
        "thumbnails": thumb_urls,
        "page_dimensions": page_dimensions,
    }


@app.get("/api/session/{sid}/page/{n}.png")
def page_image(sid: str, n: int, size: str = Query("view")):
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if n < 1 or n > session.page_count:
        raise HTTPException(status_code=404, detail="Page out of range")
    dpi = THUMB_DPI if size == "thumb" else VIEW_DPI
    cache_dir = session.dir / "pages"
    path = ensure_rendered(session.pdf_path, n - 1, cache_dir, dpi)
    return FileResponse(path, media_type="image/png")


def _segments_from_split(result: SplitResult) -> list[dict[str, Any]]:
    return [
        {
            "page_start": d.start_page,
            "page_end": d.end_page,
            "doc_type": d.doc_type,
            "confidence": d.confidence,
        }
        for d in result.documents
    ]


def _pages_processed_from_ocr(session) -> int:
    ocr = session.ocr_response or {}
    usage = ocr.get("usage_info") or {}
    n = int(usage.get("pages_processed", 0) or 0)
    if n:
        return n
    pages = ocr.get("pages") or []
    return len(pages) or session.page_count


def _analyze_response(session) -> dict[str, Any]:
    result = session.split_result
    assert result is not None
    is_replay = "replay_fixture" in (result.errors or [])
    segments = _segments_from_split(result)
    # Prefer evidence-bearing segments written by the last analyze.
    evidence_path = session.dir / "analyze_segments.json"
    if evidence_path.is_file():
        try:
            rich = json.loads(evidence_path.read_text())
            if isinstance(rich, list) and rich:
                segments = rich
        except json.JSONDecodeError:
            pass
    payload: dict[str, Any] = {
        "segments": segments,
        "pages_processed": _pages_processed_from_ocr(session),
        "page_count": session.page_count,
        "errors": list(result.errors or []),
        "is_replay": is_replay,
    }
    overlay_path = session.dir / "analyze_overlay.json"
    if overlay_path.is_file():
        try:
            overlay = json.loads(overlay_path.read_text())
            if isinstance(overlay, dict):
                payload["overlay"] = overlay
        except json.JSONDecodeError:
            pass
    pre_path = session.dir / "analyze_pre_overlay.json"
    if pre_path.is_file():
        try:
            pre = json.loads(pre_path.read_text())
            if isinstance(pre, list):
                payload["pre_overlay_segments"] = pre
        except json.JSONDecodeError:
            pass
    excl_path = session.dir / "analyze_auto_excluded.json"
    if excl_path.is_file():
        try:
            excl = json.loads(excl_path.read_text())
            if isinstance(excl, list):
                payload["auto_excluded_pages"] = excl
        except json.JSONDecodeError:
            pass
    if is_replay:
        raw = result.raw_response_path or ""
        payload["replay_fixture"] = Path(raw).name if raw else "fixture"
    return payload


def _run_analyze(session, provider) -> dict[str, Any]:
    """Run stage-1 OCR + boundaries; persist OCR and lightweight split cache."""
    provider._trace_meta = {
        "session_id": session.id,
        "filename": session.filename,
    }
    out = provider.analyze(session.pdf_path)
    store.save_ocr(session, out["ocr_response"])
    documents = [
        SplitDocument(
            doc_type=str(s.get("doc_type") or "unknown"),
            start_page=int(s["page_start"]),
            end_page=int(s["page_end"]),
            confidence=float(s.get("confidence") or 0.0),
            text=None,
        )
        for s in out["segments"]
    ]
    result = SplitResult(
        input_ref=str(session.pdf_path),
        provider=out.get("provider") or provider.name,
        model_version=out.get("model_version") or provider.model_version,
        documents=documents,
        latency_ms=float(out.get("latency_ms") or 0.0),
        cost_usd=0.0,
        raw_response_path=out.get("raw_response_path"),
        errors=list(out.get("errors") or []),
    )
    store.save_split(session, result)
    (session.dir / "analyze_segments.json").write_text(
        json.dumps(out.get("segments") or [], indent=2, default=str)
    )
    (session.dir / "analyze_overlay.json").write_text(
        json.dumps(out.get("overlay") or {}, indent=2, default=str)
    )
    (session.dir / "analyze_pre_overlay.json").write_text(
        json.dumps(out.get("pre_overlay_segments") or [], indent=2, default=str)
    )
    (session.dir / "analyze_auto_excluded.json").write_text(
        json.dumps(out.get("auto_excluded_pages") or [], indent=2, default=str)
    )
    trace_payload = out.pop("_boundary_trace", None)
    if trace_payload:
        from pdfsplit.boundary_trace import client_analyze_payload, emit_trace

        is_replay = "replay_fixture" in (out.get("errors") or [])
        trace_payload["analyze_response"] = client_analyze_payload(
            segments=list(out.get("segments") or []),
            pre_overlay=list(out.get("pre_overlay_segments") or []),
            overlay=out.get("overlay") if isinstance(out.get("overlay"), dict) else {},
            pages_processed=int(out.get("pages_processed") or session.page_count),
            page_count=session.page_count,
            errors=list(out.get("errors") or []),
            is_replay=is_replay,
            auto_excluded_pages=list(out.get("auto_excluded_pages") or []),
        )
        emit_trace(trace_payload, session_dir=session.dir)
    return out


def _validate_confirmed_segments(
    segments: list[Any],
    page_count: int,
    excluded_pages: list[Any] | None = None,
) -> str | None:
    """Return an error detail if segments are invalid; else None.

    Requires documents ∪ excluded cover pages 1..page_count exactly.
    Segments must be non-overlapping contiguous runs that never include an
    excluded page.
    """
    try:
        excluded = sorted({int(p) for p in (excluded_pages or [])})
    except (TypeError, ValueError) as exc:
        return f"invalid excluded_pages: {exc}"
    for p in excluded:
        if p < 1 or p > page_count:
            return f"excluded page {p} out of range for page_count {page_count}"
    excluded_set = set(excluded)

    if not isinstance(segments, list):
        return "segments must be a list"
    if not segments and not excluded_set:
        return "segments must be a non-empty list (or exclude pages)"
    if not segments and excluded_set != set(range(1, page_count + 1)):
        return "segments empty but excluded_pages do not cover every page"

    parsed: list[tuple[int, int, str]] = []
    try:
        for s in segments:
            if not isinstance(s, dict):
                return "each segment must be an object"
            ps = int(s["page_start"])
            pe = int(s["page_end"])
            dtype = str(s.get("doc_type") or "unknown")
            if ps < 1 or pe < 1:
                return "segment pages must be >= 1"
            if pe < ps:
                return f"invalid segment range {ps}-{pe}: end before start"
            if pe > page_count:
                return f"segment {ps}-{pe} exceeds page_count {page_count}"
            for p in range(ps, pe + 1):
                if p in excluded_set:
                    return f"segment {ps}-{pe} includes excluded page {p}"
            parsed.append((ps, pe, dtype))
    except (KeyError, TypeError, ValueError) as exc:
        return f"invalid segments: {exc}"

    parsed.sort(key=lambda t: t[0])
    covered: set[int] = set(excluded_set)
    prev_end = 0
    for ps, pe, _ in parsed:
        if ps <= prev_end:
            return "segments overlap or are not ordered"
        for p in range(prev_end + 1, ps):
            if p not in excluded_set:
                return (
                    f"segments must cover every page; page {p} is neither in a "
                    f"segment nor excluded (expected start {prev_end + 1})"
                )
        for p in range(ps, pe + 1):
            if p in covered:
                return f"page {p} covered more than once"
            covered.add(p)
        prev_end = pe
    for p in range(prev_end + 1, page_count + 1):
        if p not in excluded_set:
            return (
                f"segments must cover pages 1..{page_count} exactly "
                f"(page {p} neither in a segment nor excluded)"
            )
        covered.add(p)
    if covered != set(range(1, page_count + 1)):
        missing = sorted(set(range(1, page_count + 1)) - covered)
        return f"incomplete coverage; missing pages {missing}"
    return None


def _segment_disagreements_from_extraction(
    confirmed: list[dict[str, Any]], extraction
) -> list[dict[str, Any]]:
    """Compare extraction docs to confirmed segments; surface UI disagreements."""
    from pdfsplit.providers.mistral import detect_segment_disagreements

    docs: list[Any] = []
    if extraction.documents_v2:
        class _Doc:
            __slots__ = ("page_start", "page_end", "doc_type")

            def __init__(self, d: dict):
                self.page_start = int(d.get("page_start") or 1)
                self.page_end = int(d.get("page_end") or self.page_start)
                self.doc_type = d.get("doc_type") or "unknown"

        docs = [_Doc(d) for d in extraction.documents_v2 if isinstance(d, dict)]
    elif extraction.documents:
        docs = list(extraction.documents)
    breakdown = extraction.cost_breakdown or {}
    cached = breakdown.get("segment_disagreements")
    if isinstance(cached, list) and cached:
        return cached
    return detect_segment_disagreements(confirmed, docs)


@app.post("/api/session/{sid}/analyze")
def analyze(sid: str, force: bool = Query(False, description="Bypass analyze cache")):
    """Stage 1: OCR + boundaries only. Cached unless force=true."""
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.has_analyze() and not force:
        return _analyze_response(session)

    if force:
        # Drop cached analyze artifacts so the provider runs again.
        session.split_result = None
        if session.split_cache_path.exists():
            session.split_cache_path.unlink()
        # Keep OCR cache — re-using OCR is correct; only re-judge boundaries.
        # If OCR missing, analyze() will fetch it.

    provider = _build_provider()
    try:
        if force and session.has_ocr():
            provider._ocr_response = session.ocr_response
        _run_analyze(session, provider)
    except ValueError as exc:
        detail = str(exc)
        # Provider outages / transient OCR failures → 502 with actionable text.
        status = 502 if "openrouter vision failed" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        # Never leave the UI with a bare "Internal Server Error".
        raise HTTPException(
            status_code=500,
            detail=f"Boundary detection failed: {type(exc).__name__}: {exc}",
        ) from exc
    return _analyze_response(session)


@app.post("/api/session/{sid}/extract")
def extract(sid: str, payload: dict):
    """Stage 2: extract fields against human-confirmed segments.

    Requires a prior /analyze (OCR cache). Validates full page coverage.
    """
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.has_ocr():
        raise HTTPException(
            status_code=400,
            detail="No OCR cache; call /analyze first",
        )

    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    raw_excluded = payload.get("excluded_pages") if isinstance(payload, dict) else None
    err = _validate_confirmed_segments(
        raw_segments or [], session.page_count, raw_excluded
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    confirmed = [
        {
            "page_start": int(s["page_start"]),
            "page_end": int(s["page_end"]),
            "doc_type": str(s.get("doc_type") or "unknown"),
        }
        for s in (raw_segments or [])
    ]
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail="no segments to extract (all pages excluded?)",
        )

    provider = _build_provider()
    provider._ocr_response = session.ocr_response
    try:
        extraction = provider.extract(
            session.pdf_path, precomputed_segments=confirmed
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Persist human-confirmed cuts as the session split result.
    prior_conf: dict[tuple[int, int], float] = {}
    if session.split_result:
        for d in session.split_result.documents:
            prior_conf[(d.start_page, d.end_page)] = d.confidence
    documents = [
        SplitDocument(
            doc_type=c["doc_type"],
            start_page=c["page_start"],
            end_page=c["page_end"],
            confidence=float(
                prior_conf.get((c["page_start"], c["page_end"]), 1.0)
            ),
            text=None,
        )
        for c in confirmed
    ]
    split_result = SplitResult(
        input_ref=str(session.pdf_path),
        provider=provider.name,
        model_version=extraction.model_version,
        documents=documents,
        latency_ms=0.0,
        cost_usd=extraction.cost_usd,
        raw_response_path=extraction.raw_response_path,
        errors=list(extraction.errors),
    )
    store.save_split(session, split_result)
    store.save_extraction(session, extraction)

    response = _split_response(session)
    response["segment_disagreements"] = _segment_disagreements_from_extraction(
        confirmed, extraction
    )
    return response


@app.post("/api/session/{sid}/vouchers")
def vouchers(sid: str, payload: dict):
    """Build Tally-shaped voucher payloads via ``to_voucher_payload``.

    Body ``documents`` is the current client ``documents_v2`` (including any
    in-browser field edits and boundary doc_type / page overlays). The server
    is the sole mapper — the web client must not reimplement this shape.
    """
    from pdfsplit.extraction import document_v2_from_dict, to_voucher_payload

    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.extraction_result is None:
        raise HTTPException(
            status_code=400, detail="No extraction yet; call /extract first"
        )

    raw_docs = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(raw_docs, list) or not raw_docs:
        raise HTTPException(
            status_code=400, detail="documents must be a non-empty list"
        )

    out: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_docs):
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=400, detail=f"documents[{i}] must be an object"
            )
        try:
            ps = int(raw.get("page_start") or 1)
            pe = int(raw.get("page_end") or ps)
            doc = document_v2_from_dict(raw, ps, pe)
            # document_v2_from_dict does not carry postprocess flags; preserve
            # totals_mismatch from the client copy when present.
            if "totals_mismatch" in raw:
                doc = doc.model_copy(
                    update={"totals_mismatch": bool(raw.get("totals_mismatch"))}
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"documents[{i}] invalid: {exc}"
            ) from exc
        out.append(to_voucher_payload(doc))
    return {"vouchers": out}


@app.post("/api/session/{sid}/split")
def split(sid: str):
    """Thin wrapper: analyze (if needed) then extract with machine segments."""
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.has_split() and session.extraction_result is not None:
        # Never call the provider twice for one upload.
        return _split_response(session)

    provider = _build_provider()
    try:
        if not session.has_analyze():
            _run_analyze(session, provider)
        else:
            provider._ocr_response = session.ocr_response

        segments = [
            {
                "page_start": d.start_page,
                "page_end": d.end_page,
                "doc_type": d.doc_type,
            }
            for d in session.split_result.documents
        ]
        extraction = provider.extract(
            session.pdf_path, precomputed_segments=segments
        )
    except ValueError as exc:
        # Provider config / input errors (missing API key, bad PDF, etc.) —
        # return JSON so the React client can show the real message instead of
        # choking on Starlette's plain-text "Internal Server Error".
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Persist OCR if analyze ran via a path that set it on the provider only.
    if provider._ocr_response is not None and not session.has_ocr():
        store.save_ocr(session, provider._ocr_response)

    # Derive boundary documents from extraction for UI / legacy /split shape.
    result = _extraction_to_split_result(provider, session, extraction)
    # Keep replay flag from analyze when extraction path drops it.
    if session.split_result and "replay_fixture" in (session.split_result.errors or []):
        if "replay_fixture" not in result.errors:
            result.errors.append("replay_fixture")
    store.save_split(session, result)
    store.save_extraction(session, extraction)
    return _split_response(session)


def _extraction_to_split_result(provider, session, extraction):
    """Derive a SplitResult (boundaries) from the annotated extraction."""
    from pdfsplit.schema import SplitDocument

    documents = [
        SplitDocument(
            doc_type=d.doc_type,
            start_page=d.page_start,
            end_page=d.page_end,
            confidence=d.confidence,
            text=None,
        )
        for d in extraction.documents
    ]
    return SplitResult(
        input_ref=str(session.pdf_path),
        provider=provider.name,
        model_version=extraction.model_version,
        documents=documents,
        latency_ms=0.0,
        cost_usd=extraction.cost_usd,
        raw_response_path=extraction.raw_response_path,
        errors=list(extraction.errors),
    )


def _split_response(session):
    """Return the split result plus the extraction data for the UI.

    The response carries the boundary documents (for the cut model), the
    structured extraction (header fields + line items + ungrounded flags),
    and the cost in dollars.
    """
    from pdfsplit.extraction import uncovered_pages as _uncovered_pages

    result = session.split_result
    payload = result.model_dump()
    extraction = session.extraction_result
    payload["extraction"] = (
        extraction.model_dump() if extraction is not None else None
    )
    # Cost in dollars: pages billed, whether the annotated rate applied, total.
    payload["pages_billed"] = extraction.pages_processed if extraction else 0
    payload["annotated_rate"] = settings.mistral_docai_usd_per_page
    payload["cost_usd"] = extraction.cost_usd if extraction else 0.0
    payload["cost_breakdown"] = (
        extraction.cost_breakdown if extraction is not None else None
    )
    if extraction is not None:
        payload["uncovered_pages"] = _uncovered_pages(
            extraction.documents, extraction.pages_processed
        )
        dup = list(getattr(extraction, "duplicate_groups", None) or [])
        if not dup and extraction.documents_v2:
            from pdfsplit.packing import find_duplicate_doc_groups

            _, dup = find_duplicate_doc_groups(extraction.documents_v2)
        payload["duplicate_groups"] = dup
    else:
        payload["uncovered_pages"] = []
        payload["duplicate_groups"] = []
    # Replay flag: when the result came from a fixture (no live call), the UI
    # must show a persistent banner and suppress the credit meter.
    is_replay = "replay_fixture" in (result.errors or [])
    payload["is_replay"] = is_replay
    if is_replay:
        raw = result.raw_response_path or ""
        payload["replay_fixture"] = Path(raw).name if raw else "fixture"
    return payload


@app.post("/api/session/{sid}/save")
def save(sid: str, payload: dict):
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.has_split():
        raise HTTPException(status_code=400, detail="No split result; call /split first")

    try:
        corrected = [
            SplitDocument(
                doc_type=d.get("doc_type", "unknown"),
                start_page=int(d["start_page"]),
                end_page=int(d["end_page"]),
                confidence=float(d.get("confidence", 0.0)),
            )
            for d in payload.get("documents", [])
        ]
        excluded_pages = [int(p) for p in (payload.get("excluded_pages") or [])]
        display_names: dict[tuple[int, int], str] = {}
        for d in payload.get("documents", []):
            name = (d.get("display_name") or "").strip()
            if name:
                display_names[(int(d["start_page"]), int(d["end_page"]))] = name
    except Exception as exc:  # pydantic ValidationError, ValueError, KeyError
        raise HTTPException(
            status_code=400,
            detail=f"Invalid document range in payload: {exc}",
        )
    correction_types = payload.get("correction_types", [])
    field_corrections = payload.get("field_corrections", [])

    # Physically cut the PDF at the corrected boundaries (excluded pages omitted).
    session.documents_dir.mkdir(parents=True, exist_ok=True)
    try:
        splits = split_pdf_by_ranges(
            session.pdf_path,
            corrected,
            session.documents_dir,
            total_pages=session.page_count,
            excluded_pages=excluded_pages,
            display_names=display_names or None,
            use_display_names_as_filenames=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    problems = verify_split(session.pdf_path, splits)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    store.save_corrections(
        session,
        corrected,
        correction_types,
        field_corrections,
        excluded_pages=excluded_pages,
    )

    # Build a zip of the split documents.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in splits:
            zf.write(s.path, arcname=s.path.name)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{sid}_documents.zip"'
        },
    )


@app.post("/api/session/{sid}/save-batched")
def save_batched(
    sid: str,
    max_pages: int | None = Query(None, description="Downstream page cap"),
):
    """Pack cached split docs into <=N-page batch PDFs + manifest.zip.

    Additive to /save — does not alter per-document split behaviour.
    """
    from pdfsplit.packing import (
        build_pack_manifest,
        doc_ranges_from_split_documents,
        find_duplicate_doc_groups,
        pack_documents,
    )

    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.has_split():
        raise HTTPException(status_code=400, detail="No split result; call /split first")

    cap = int(max_pages) if max_pages is not None else int(settings.max_downstream_pages)
    if cap < 1:
        raise HTTPException(status_code=400, detail="max_pages must be >= 1")

    ranges = doc_ranges_from_split_documents(session.split_result.documents)
    covered: set[int] = set()
    for r in ranges:
        for p in range(int(r["page_start"]), int(r["page_end"]) + 1):
            covered.add(p)
    inferred_excluded = [
        p for p in range(1, session.page_count + 1) if p not in covered
    ]
    try:
        batches = pack_documents(
            session.page_count, ranges, cap, excluded_pages=inferred_excluded
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duplicate_groups: list = []
    extraction = session.extraction_result
    if extraction is not None:
        duplicate_groups = list(getattr(extraction, "duplicate_groups", None) or [])
        if not duplicate_groups and extraction.documents_v2:
            _, duplicate_groups = find_duplicate_doc_groups(extraction.documents_v2)

    manifest = build_pack_manifest(
        packet_pages=session.page_count,
        max_pages=cap,
        batches=batches,
        duplicate_groups=duplicate_groups,
    )

    batch_docs = [
        SplitDocument(
            doc_type="batch",
            start_page=int(b["page_start"]),
            end_page=int(b["page_end"]),
            confidence=1.0,
        )
        for b in batches
    ]
    out_dir = session.dir / "batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear prior batch PDFs so stale files never leak into the zip.
    for old in out_dir.glob("batch_*.pdf"):
        old.unlink(missing_ok=True)

    splits = split_pdf_by_ranges(
        session.pdf_path, batch_docs, out_dir, total_pages=session.page_count
    )
    problems = verify_split(session.pdf_path, splits)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    # Rename stem-based outputs to batch_001.pdf …
    rename_map: dict[str, Path] = {}
    for batch, split in zip(batches, splits, strict=True):
        dest = out_dir / f"{batch['batch_id']}.pdf"
        if split.path != dest:
            split.path.replace(dest)
        rename_map[batch["batch_id"]] = dest

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for batch in batches:
            path = rename_map[batch["batch_id"]]
            zf.write(path, arcname=batch["batch_id"] + ".pdf")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{sid}_batches.zip"'
        },
    )


def _aia_disabled_reason() -> str | None:
    """Human-readable why live push is off — never empty when disabled."""
    base = bool(settings.aia_api_base.strip())
    writes = bool(settings.aia_allow_writes)
    if base and writes:
        return None
    missing: list[str] = []
    if not base:
        missing.append("AIA_API_BASE")
    if not writes:
        missing.append("AIA_ALLOW_WRITES=true")
    joined = " and ".join(missing)
    return (
        "AIA push is unavailable on this deployment: live writes are off "
        f"(missing {joined}). This keeps demo traffic from writing into a "
        "customer ledger. Push stays disabled until an operator enables both "
        "vars deliberately."
    )


@app.get("/api/aia/status")
def aia_status():
    """Public AIA integration flags — never includes credentials."""
    from pdfsplit.aia.client import AiaApClient
    from pdfsplit.aia.transport import session_cookie_expiry_iso

    enabled = AiaApClient.live_writes_enabled(settings)
    base = settings.aia_api_base.strip().rstrip("/")
    cookie_expires = session_cookie_expiry_iso() if enabled else None
    return {
        "allow_writes": enabled,
        "push_enabled": enabled,
        "api_base_configured": bool(settings.aia_api_base.strip()),
        "disabled_reason": None if enabled else _aia_disabled_reason(),
        "session_cookie_expires_on": cookie_expires,
        "max_downstream_pages": int(settings.max_downstream_pages),
        "max_upload_pages": int(settings.max_upload_pages),
        "default_dry_run": True,
        "mode_b_supported": False,
        "needs_review_url": f"{base}/accounts-payable" if base else None,
        "ably_upgrade_path": "ap_bill_file_status_changed (not wired)",
    }


def _push_aia_body(payload: dict | None) -> dict[str, Any]:
    body = payload or {}
    dry_run = bool(body.get("dry_run", True))
    erp = str(body.get("erp") or "tally").lower()
    if erp not in ("tally", "zoho"):
        raise HTTPException(status_code=400, detail="erp must be tally or zoho")
    wait = bool(body.get("wait_for_hitl", False))
    stream = bool(body.get("stream", False))
    raw_only = body.get("only_indices")
    only_indices: list[int] | None = None
    if raw_only is not None:
        if not isinstance(raw_only, list):
            raise HTTPException(status_code=400, detail="only_indices must be a list")
        try:
            only_indices = [int(i) for i in raw_only]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid only_indices: {exc}"
            ) from exc

    raw_segments = body.get("segments")
    segments: list[dict[str, Any]] | None = None
    if raw_segments is not None:
        if not isinstance(raw_segments, list):
            raise HTTPException(status_code=400, detail="segments must be a list")
        segments = [
            {
                "page_start": int(s["page_start"]),
                "page_end": int(s["page_end"]),
                "doc_type": str(s.get("doc_type") or "invoice"),
                **(
                    {"display_name": str(s["display_name"]).strip()}
                    if isinstance(s.get("display_name"), str)
                    and str(s.get("display_name")).strip()
                    else {}
                ),
                **(
                    {"vendor": str(s["vendor"]).strip()}
                    if isinstance(s.get("vendor"), str)
                    and str(s.get("vendor")).strip()
                    else {}
                ),
                **(
                    {"document_number": str(s["document_number"]).strip()}
                    if isinstance(s.get("document_number"), str)
                    and str(s.get("document_number")).strip()
                    else {}
                ),
            }
            for s in raw_segments
        ]

    return {
        "dry_run": dry_run,
        "erp": erp,
        "wait": wait,
        "stream": stream,
        "segments": segments,
        "raw_segments": raw_segments,
        "raw_excluded": body.get("excluded_pages"),
        "only_indices": only_indices,
        "source_stem": (
            str(body.get("packet_name") or body.get("source_stem") or "").strip()
            or None
        ),
    }


@app.post("/api/session/{sid}/push-aia")
def push_aia(sid: str, payload: dict | None = None):
    """Push confirmed invoices to AIA (Mode A).

    Body: ``{ dry_run?, erp?, wait_for_hitl?, stream?, segments?, excluded_pages? }``.
    ``dry_run`` defaults to true. Non-dry-run requires ``AIA_ALLOW_WRITES``.
    When ``stream`` is true, returns NDJSON progress events (one JSON object per line).
    """
    from pdfsplit.aia.client import AiaApClient
    from pdfsplit.aia.push import push_session
    from pdfsplit.aia.transport import redact_secrets
    from fastapi.responses import StreamingResponse

    parsed = _push_aia_body(payload)
    dry_run = parsed["dry_run"]
    erp = parsed["erp"]
    wait = parsed["wait"]
    stream = parsed["stream"]

    if not dry_run and not AiaApClient.live_writes_enabled(settings):
        raise HTTPException(
            status_code=403,
            detail=_aia_disabled_reason()
            or (
                "Live AIA push requires AIA_API_BASE and AIA_ALLOW_WRITES=true. "
                "Use dry_run=true to preview."
            ),
        )

    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    segments = parsed["segments"]
    if segments is not None:
        err = _validate_confirmed_segments(
            parsed["raw_segments"] or [],
            session.page_count,
            parsed["raw_excluded"],
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        if not segments:
            raise HTTPException(
                status_code=400,
                detail="no segments to push (all pages excluded?)",
            )

    def _run(on_progress=None):
        return push_session(
            sid,
            dry_run=dry_run,
            erp=erp,  # type: ignore[arg-type]
            settings=settings,
            store=store,
            wait_for_hitl=wait and not dry_run,
            segments=segments,
            excluded_pages=(
                [int(p) for p in (parsed["raw_excluded"] or [])]
                if parsed["raw_excluded"] is not None
                else None
            ),
            only_indices=parsed.get("only_indices"),
            on_progress=on_progress,
            source_stem=parsed.get("source_stem"),
        )

    if stream:
        import queue
        import threading

        q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        error_holder: list[BaseException] = []

        def worker() -> None:
            try:

                def on_progress(ev: dict[str, Any]) -> None:
                    q.put(ev)

                _run(on_progress=on_progress)
            except BaseException as exc:  # noqa: BLE001
                error_holder.append(exc)
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def ndjson():
            while True:
                item = q.get()
                if item is None:
                    break
                yield json.dumps(item, default=str) + "\n"
            if error_holder:
                yield json.dumps(
                    {
                        "type": "error",
                        "reason": redact_secrets(str(error_holder[0])),
                    }
                ) + "\n"

        return StreamingResponse(ndjson(), media_type="application/x-ndjson")

    try:
        outcomes = _run()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=redact_secrets(str(exc))
        ) from None

    counts = {
        "would_push": sum(1 for o in outcomes if o.action == "would_push"),
        "pushed": sum(1 for o in outcomes if o.action == "pushed"),
        "skipped_oversized": sum(
            1 for o in outcomes if o.action == "skipped_oversized"
        ),
        "skipped_duplicate": sum(
            1 for o in outcomes if o.action == "skipped_duplicate"
        ),
        "failed": sum(1 for o in outcomes if o.action == "failed"),
        "needs_review": sum(
            1 for o in outcomes if o.hitl_status == "file_hitl_success"
        ),
        "rejected": sum(
            1 for o in outcomes if o.hitl_status == "file_hitl_rejected"
        ),
    }
    base = settings.aia_api_base.strip().rstrip("/")
    return {
        "session_id": sid,
        "dry_run": dry_run,
        "erp": erp,
        "outcomes": [o.model_dump(mode="json") for o in outcomes],
        "counts": counts,
        "needs_review_url": f"{base}/accounts-payable" if base else None,
    }


@app.get("/api/providers")
def providers():
    return {
        "openrouter": {
            "status": "ready"
            if settings.has_openrouter_credentials()
            else "missing_credentials"
        }
    }


@app.get("/")
def index():
    """Serve the React build from web/dist only."""
    return FileResponse(_require_web_dist_file("index.html"))


@app.get("/favicon.svg")
def favicon_svg():
    return FileResponse(_require_web_dist_file("favicon.svg"))


@app.get("/logo.svg")
def logo_svg():
    return FileResponse(_require_web_dist_file("logo.svg"))


@app.get("/icons.svg")
def icons_svg():
    return FileResponse(_require_web_dist_file("icons.svg"))


_assets = _WEB_DIST / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="web-assets")
