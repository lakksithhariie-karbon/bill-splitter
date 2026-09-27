"""Push confirmed session invoices to AIA (Mode A) with dry-run default."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pdfsplit.aia.client import AiaApClient, PushStepError
from pdfsplit.aia.models import (
    BillPushOutcome,
    RejectReason,
    RejectReasonCode,
)
from pdfsplit.aia.reasons import aia_page_limit_message
from pdfsplit.app.session import Session, SessionStore
from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings
from pdfsplit.pdf import split_pdf_by_ranges
from pdfsplit.schema import SplitDocument, SplitResult

ErpName = Literal["tally", "zoho"]

#: Abort volume push after this many unrecovered Bill Uploads sticks.
MAX_UNRECOVERED_STICKS = 2

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class PlannedInvoice:
    file_name: str
    page_start: int
    page_end: int
    page_count: int
    invoice_number: str
    doc_type: str = "invoice"
    display_name: str = ""


def sanitize_display_name(name: str) -> str:
    """Filename-safe slot (compat wrapper). Empty → ``Invoice``."""
    from pdfsplit.aia.filenames import sanitize_slot

    return sanitize_slot(name, fallback="Invoice") or "Invoice"


def sanitize_source_stem(filename: str, fallback: str = "packet") -> str:
    """Sanitize the uploaded PDF stem for use as the AIA name prefix."""
    from pdfsplit.aia.filenames import sanitize_packet_stem

    return sanitize_packet_stem(filename, fallback=fallback)


def build_push_file_name(
    *,
    source_filename: str,
    vendor: str = "",
    document_number: str = "",
    display_name: str = "",
    page_start: int = 1,
    page_end: int = 1,
    fallback_stem: str = "packet",
    index: int = 1,
) -> str:
    """``<packet>-<VEN3>[-<number>].pdf`` (page range args ignored — compat).

    Prefer ``vendor`` + ``document_number``. Legacy ``display_name`` alone is
    treated as the vendor slot. Collision suffixes are applied by
    :func:`plan_from_segments` across the full set — this helper builds one
    unsuffixed base name.
    """
    from pdfsplit.aia.filenames import (
        NameSlots,
        build_base_file_name,
        positional_name,
        sanitize_packet_stem,
    )

    packet = sanitize_packet_stem(source_filename, fallback=fallback_stem)
    vend = (vendor or "").strip()
    number = (document_number or "").strip()
    if not vend and not number and (display_name or "").strip():
        vend = display_name.strip()
    if not vend and not number:
        vend = positional_name(index)
    return build_base_file_name(
        NameSlots(packet=packet, vendor=vend, number=number)
    )


def _default_display_name(index: int) -> str:
    from pdfsplit.aia.filenames import positional_name

    return positional_name(index)


def _invoice_number_from_doc(doc: Any) -> str:
    if isinstance(doc, dict):
        for key in ("invoice_number", "document_id", "supInvNo", "sup_inv_no"):
            val = doc.get(key)
            if isinstance(val, dict):
                val = val.get("value") or val.get("raw") or ""
            if val:
                return str(val).strip()
        return ""
    return str(getattr(doc, "invoice_number", "") or "").strip()


def plan_from_segments(
    session: Session,
    segments: list[dict[str, Any]],
    *,
    source_stem: str | None = None,
) -> list[PlannedInvoice]:
    """Plan pushes from confirmed segments using cached/edited name slots.

    Does **not** re-derive vendor/number from OCR — uses values on each
    segment (UI edits or analyze-time cache). Collision suffixes are assigned
    in stable document order across the full set before any upload.
    """
    from pdfsplit.aia.filenames import plan_filenames

    source = (source_stem or "").strip() or session.filename
    items: list[dict[str, Any]] = []
    meta: list[tuple[int, int, str, str, str, str]] = []
    for i, s in enumerate(segments, start=1):
        start = int(s.get("page_start") or s.get("start_page") or 0)
        end = int(s.get("page_end") or s.get("end_page") or 0)
        if start < 1 or end < start:
            continue
        dtype = str(s.get("doc_type") or "invoice")
        vendor = str(s.get("vendor") or "").strip()
        number = str(
            s.get("document_number") or s.get("invoice_number") or ""
        ).strip()
        # Legacy clients: display_name alone → vendor slot.
        if not vendor and not number:
            legacy = str(s.get("display_name") or "").strip()
            if legacy:
                vendor = legacy
        items.append(
            {
                "vendor": vendor,
                "document_number": number,
                "index": i,
            }
        )
        meta.append((start, end, number, dtype, vendor, number))

    names = plan_filenames(packet=source, items=items)
    planned: list[PlannedInvoice] = []
    for name, (start, end, inv, dtype, vendor, number) in zip(
        names, meta, strict=False
    ):
        label = vendor or number or _default_display_name(len(planned) + 1)
        planned.append(
            PlannedInvoice(
                file_name=name,
                page_start=start,
                page_end=end,
                page_count=end - start + 1,
                invoice_number=inv or number,
                doc_type=dtype,
                display_name=label,
            )
        )
    return planned


def persist_confirmed_split(
    session: Session,
    store: SessionStore,
    segments: list[dict[str, Any]],
) -> SplitResult:
    """Save confirmed boundaries as the session split (extraction optional)."""
    prior_conf: dict[tuple[int, int], float] = {}
    if session.split_result:
        for d in session.split_result.documents:
            prior_conf[(d.start_page, d.end_page)] = d.confidence
    documents = [
        SplitDocument(
            doc_type=str(s.get("doc_type") or "invoice"),
            start_page=int(s["page_start"]),
            end_page=int(s["page_end"]),
            confidence=float(
                prior_conf.get((int(s["page_start"]), int(s["page_end"])), 1.0)
            ),
            text=None,
        )
        for s in segments
    ]
    provider = (
        session.split_result.provider if session.split_result else "confirmed"
    )
    model_version = (
        session.split_result.model_version if session.split_result else "boundaries"
    )
    split_result = SplitResult(
        input_ref=str(session.pdf_path),
        provider=provider,
        model_version=model_version,
        documents=documents,
        latency_ms=0.0,
        cost_usd=0.0,
        raw_response_path=None,
        errors=[],
    )
    store.save_split(session, split_result)
    return split_result


def plan_session_invoices(session: Session) -> list[PlannedInvoice]:
    """Build one-invoice-per-file plans from split + extraction caches."""
    extraction = session.extraction_result
    docs_v2 = list(getattr(extraction, "documents_v2", None) or []) if extraction else []
    docs_v1 = list(getattr(extraction, "documents", None) or []) if extraction else []

    segments: list[dict[str, Any]] = []
    if docs_v2:
        for i, d in enumerate(docs_v2, start=1):
            if not isinstance(d, dict):
                continue
            start = int(d.get("page_start") or d.get("start_page") or 0)
            end = int(d.get("page_end") or d.get("end_page") or 0)
            if start < 1 or end < start:
                continue
            inv = _invoice_number_from_doc(d)
            dtype = str(d.get("doc_type") or "invoice")
            vendor = str(d.get("vendor") or d.get("display_name") or "").strip()
            segments.append(
                {
                    "page_start": start,
                    "page_end": end,
                    "doc_type": dtype,
                    "vendor": vendor,
                    "document_number": inv,
                    "invoice_number": inv,
                }
            )
    elif docs_v1:
        for i, d in enumerate(docs_v1, start=1):
            start = int(getattr(d, "page_start", 0) or 0)
            end = int(getattr(d, "page_end", 0) or 0)
            if start < 1 or end < start:
                continue
            inv = str(getattr(d, "invoice_number", "") or "")
            dtype = str(getattr(d, "doc_type", "invoice"))
            segments.append(
                {
                    "page_start": start,
                    "page_end": end,
                    "doc_type": dtype,
                    "document_number": inv,
                    "invoice_number": inv,
                }
            )
    elif session.split_result is not None:
        for i, d in enumerate(session.split_result.documents, start=1):
            segments.append(
                {
                    "page_start": d.start_page,
                    "page_end": d.end_page,
                    "doc_type": d.doc_type,
                    "vendor": _default_display_name(i),
                }
            )

    return plan_from_segments(session, segments)


def _ui_phase_for_status(status: str | None) -> str:
    if status == "file_hitl_success":
        return "needs_review"
    if status == "file_hitl_rejected":
        return "rejected"
    if status in {
        "file_uploaded",
        "file_data_extracted",
        "file_data_enriched",
        "file_data_partially_extracted",
    }:
        return "extracting"
    return "extracting"


def _outcome_counts(
    outcomes: list[BillPushOutcome], *, unrecovered_sticks: int
) -> dict[str, int]:
    return {
        "would_push": sum(1 for o in outcomes if o.action == "would_push"),
        "pushed": sum(1 for o in outcomes if o.action == "pushed"),
        "skipped_oversized": sum(
            1 for o in outcomes if o.action == "skipped_oversized"
        ),
        "skipped_duplicate": sum(
            1 for o in outcomes if o.action == "skipped_duplicate"
        ),
        "failed": sum(1 for o in outcomes if o.action == "failed"),
        "aborted": sum(1 for o in outcomes if o.action == "aborted"),
        "needs_review": sum(
            1 for o in outcomes if o.hitl_status == "file_hitl_success"
        ),
        "rejected": sum(
            1 for o in outcomes if o.hitl_status == "file_hitl_rejected"
        ),
        "still_processing": sum(
            1
            for o in outcomes
            if o.action == "pushed"
            and o.hitl_status
            and o.hitl_status
            not in {"file_hitl_success", "file_hitl_rejected"}
        ),
        "unrecovered_sticks": unrecovered_sticks,
    }


def push_session(
    session_id: str,
    *,
    dry_run: bool = True,
    erp: ErpName = "tally",
    client: AiaApClient | None = None,
    settings: Settings | None = None,
    store: SessionStore | None = None,
    wait_for_hitl: bool = False,
    poll_timeout_s: float = 120.0,
    segments: list[dict[str, Any]] | None = None,
    excluded_pages: list[int] | None = None,
    only_indices: list[int] | None = None,
    on_progress: ProgressCallback | None = None,
    source_stem: str | None = None,
) -> list[BillPushOutcome]:
    """Push (or dry-run) confirmed invoices with bounded concurrency.

    Phase 1 — upload (``push_bill``) with ``AIA_PUSH_CONCURRENCY`` workers.
    Phase 2 — poll all successfully uploaded invoices concurrently until
    terminal (or recovery abort). Document order is preserved in outcomes and
    progress events (index = document order).

    ``only_indices`` retries a subset (failed rows) without re-pushing
    successes. At 2 unrecovered sticks, stop launching new uploads; let
    in-flight handshakes finish.

    ``source_stem`` overrides the uploaded filename prefix used in AIA names.
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    import threading
    import time

    from pdfsplit.aia.client import AiaRateLimitError
    from pdfsplit.aia.transport import AiaAuthExpiredError

    cfg = settings or default_settings
    sess_store = store or SessionStore(cfg.output_dir / "sessions")
    session = sess_store.get(session_id)
    if session is None:
        raise FileNotFoundError(f"Session not found: {session_id}")

    excluded = [int(p) for p in (excluded_pages or [])]

    if segments is not None:
        persist_confirmed_split(session, sess_store, segments)
        session = sess_store.get(session_id) or session
        planned = plan_from_segments(session, segments, source_stem=source_stem)
    else:
        planned = plan_session_invoices(session)

    aia = client or AiaApClient(settings=cfg, erp=erp)
    cap = int(cfg.max_downstream_pages)
    concurrency = max(1, int(getattr(cfg, "aia_push_concurrency", 4) or 4))

    total = len(planned)
    outcomes: list[BillPushOutcome | None] = [None] * total
    unrecovered_sticks = 0
    stop_launching = False
    aborted = False
    abort_user_message: str | None = None
    throttle_events: list[dict[str, Any]] = []
    emit_lock = threading.Lock()
    state_lock = threading.Lock()

    def emit(event: dict[str, Any]) -> None:
        if on_progress is not None:
            with emit_lock:
                on_progress(event)

    if not planned:
        emit({"type": "summary", "counts": {}, "outcomes": []})
        return []

    retry_set: set[int] | None = None
    if only_indices is not None:
        retry_set = {int(i) for i in only_indices}

    # Materialize per-invoice PDFs only when actually pushing.
    split_paths: dict[tuple[int, int], Path] = {}
    if not dry_run:
        out_dir = session.dir / "aia_push"
        out_dir.mkdir(parents=True, exist_ok=True)
        docs = [
            SplitDocument(
                doc_type=p.doc_type or "invoice",
                start_page=p.page_start,
                end_page=p.page_end,
                confidence=1.0,
            )
            for p in planned
        ]
        splits = split_pdf_by_ranges(
            session.pdf_path,
            docs,
            out_dir,
            total_pages=session.page_count,
            excluded_pages=excluded,
        )
        for s in splits:
            split_paths[(s.doc.start_page, s.doc.end_page)] = s.path

    def base_evt(index: int, plan: PlannedInvoice) -> dict[str, Any]:
        return {
            "type": "invoice",
            "index": index,
            "total": total,
            "file_name": plan.file_name,
            "page_start": plan.page_start,
            "page_end": plan.page_end,
            "page_count": plan.page_count,
            "invoice_number": plan.invoice_number,
        }

    def preflight(index: int, plan: PlannedInvoice) -> BillPushOutcome | None:
        """Return an outcome if this invoice should not be uploaded; else None."""
        notes: list[str] = []
        outcome = BillPushOutcome(
            file_name=plan.file_name,
            page_count=plan.page_count,
            invoice_number=plan.invoice_number,
            page_start=plan.page_start,
            page_end=plan.page_end,
        )
        evt = base_evt(index, plan)
        emit({**evt, "phase": "queued"})

        if plan.page_count > cap:
            msg = aia_page_limit_message(plan.page_count, cap)
            outcome.action = "skipped_oversized"
            outcome.reject_reason = RejectReason(
                code=RejectReasonCode.OVERSIZED_PREFLIGHT,
                message=msg,
                pdf_pages=plan.page_count,
                page_limit=cap,
            )
            notes.append("oversized_preflight")
            outcome.notes = notes
            emit(
                {
                    **evt,
                    "phase": "skipped",
                    "action": "skipped_oversized",
                    "reason": msg,
                }
            )
            return outcome

        dup: bool | None = None
        if plan.invoice_number:
            try:
                dup = aia.supplier_invoice_exists(plan.invoice_number)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"duplicate_check_error:{type(exc).__name__}")
                dup = None
        outcome.duplicate_in_aia = dup
        if dup is True:
            outcome.action = "skipped_duplicate"
            outcome.reject_reason = RejectReason(
                code=RejectReasonCode.DUPLICATE,
                message=(
                    "Supplier invoice number already present in AIA — not pushing"
                ),
            )
            notes.append("duplicate_in_aia")
            outcome.notes = notes
            emit(
                {
                    **evt,
                    "phase": "skipped",
                    "action": "skipped_duplicate",
                    "reason": outcome.reject_reason.message,
                }
            )
            return outcome

        if dry_run:
            outcome.action = "would_push"
            notes.append("dry_run")
            outcome.notes = notes
            emit({**evt, "phase": "would_push", "action": "would_push"})
            return outcome

        return None

    def process_one(index: int, plan: PlannedInvoice) -> BillPushOutcome:
        """Upload one invoice, then poll to terminal when requested."""
        nonlocal unrecovered_sticks, stop_launching, aborted, concurrency
        nonlocal abort_user_message

        notes: list[str] = []
        outcome = BillPushOutcome(
            file_name=plan.file_name,
            page_count=plan.page_count,
            invoice_number=plan.invoice_number,
            page_start=plan.page_start,
            page_end=plan.page_end,
        )
        evt = base_evt(index, plan)
        pdf_path = split_paths.get((plan.page_start, plan.page_end))
        if pdf_path is None:
            outcome.action = "failed"
            notes.append("missing_split_pdf")
            outcome.notes = notes
            emit(
                {
                    **evt,
                    "phase": "failed",
                    "action": "failed",
                    "reason": "missing_split_pdf",
                }
            )
            return outcome

        emit({**evt, "phase": "uploading"})

        # Rate-limit aware upload with one soft retry after backoff.
        upload_attempts = 0
        while True:
            upload_attempts += 1
            try:
                ref = aia.push_bill(
                    pdf_path,
                    plan.file_name,
                    page_count=plan.page_count,
                    invoice_number=plan.invoice_number or None,
                )
                break
            except AiaRateLimitError as exc:
                wait_s = float(exc.retry_after_s or 2.0)
                with state_lock:
                    concurrency = max(1, concurrency - 1)
                    throttle_events.append(
                        {
                            "index": index,
                            "retry_after_s": wait_s,
                            "concurrency_now": concurrency,
                            "path": "push_bill",
                        }
                    )
                emit(
                    {
                        "type": "throttle",
                        "index": index,
                        "reason": str(exc),
                        "retry_after_s": wait_s,
                        "concurrency_now": concurrency,
                    }
                )
                if upload_attempts >= 2:
                    outcome.action = "failed"
                    notes.append("rate_limited")
                    outcome.notes = notes
                    emit(
                        {
                            **evt,
                            "phase": "failed",
                            "action": "failed",
                            "reason": str(exc),
                        }
                    )
                    return outcome
                time.sleep(wait_s)
            except AiaAuthExpiredError as exc:
                msg = AiaAuthExpiredError.USER_MESSAGE
                with state_lock:
                    stop_launching = True
                    aborted = True
                    abort_user_message = msg
                outcome.action = "failed"
                notes.append("aia_auth_expired")
                outcome.notes = notes
                emit(
                    {
                        **evt,
                        "phase": "failed",
                        "action": "failed",
                        "reason": msg,
                    }
                )
                emit(
                    {
                        "type": "abort",
                        "reason": (
                            f"{msg} Stopping new launches; invoices already "
                            "started will finish reporting."
                        ),
                        "auth_expired": True,
                    }
                )
                return outcome
            except PushStepError as exc:
                outcome.action = "failed"
                outcome.bill_ref = exc.bill_ref
                notes.append(f"push_step_{exc.bill_ref.failed_step}")
                outcome.notes = notes
                emit(
                    {
                        **evt,
                        "phase": "failed",
                        "action": "failed",
                        "reason": str(exc),
                    }
                )
                return outcome
            except Exception as exc:  # noqa: BLE001
                outcome.action = "failed"
                notes.append(type(exc).__name__)
                outcome.notes = notes
                emit(
                    {
                        **evt,
                        "phase": "failed",
                        "action": "failed",
                        "reason": type(exc).__name__,
                    }
                )
                return outcome

        outcome.bill_ref = ref
        outcome.action = "pushed"
        emit(
            {
                **evt,
                "phase": "extracting",
                "action": "pushed",
                "file_uuid": ref.file_uuid,
            }
        )

        if not (wait_for_hitl and ref.file_uuid):
            outcome.notes = notes
            return outcome

        def _on_status(row: Any, _base=evt) -> None:
            emit(
                {
                    **_base,
                    "phase": _ui_phase_for_status(row.status),
                    "hitl_status": row.status,
                    "status_message": row.status_message,
                    "file_uuid": row.file_uuid,
                }
            )

        try:
            row, meta = aia.wait_for_terminal_status(
                ref.file_uuid,
                timeout_s=poll_timeout_s,
                on_status=_on_status,
            )
        except AiaRateLimitError as exc:
            wait_s = float(exc.retry_after_s or 2.0)
            with state_lock:
                concurrency = max(1, concurrency - 1)
                throttle_events.append(
                    {
                        "index": index,
                        "retry_after_s": wait_s,
                        "concurrency_now": concurrency,
                        "path": "bill_status",
                    }
                )
            emit(
                {
                    "type": "throttle",
                    "index": index,
                    "reason": str(exc),
                    "retry_after_s": wait_s,
                    "concurrency_now": concurrency,
                }
            )
            time.sleep(wait_s)
            row, meta = aia.wait_for_terminal_status(
                ref.file_uuid,
                timeout_s=poll_timeout_s,
                on_status=_on_status,
            )

        outcome.hitl_status = row.status
        if meta.get("recovered_with_trigger"):
            notes.append("recovered_with_trigger")
        if meta.get("stuck_unrecovered"):
            notes.append("stuck_unrecovered")
            with state_lock:
                unrecovered_sticks += 1
                if unrecovered_sticks >= MAX_UNRECOVERED_STICKS:
                    stop_launching = True
                    aborted = True
            emit(
                {
                    **evt,
                    "phase": "stuck",
                    "action": "stuck",
                    "hitl_status": row.status,
                    "file_uuid": ref.file_uuid,
                    "reason": (
                        "Still Extracting on Bill Uploads after recovery "
                        "— no delete path; stopping new launches if sticks "
                        "accumulate."
                    ),
                }
            )
            if aborted:
                emit(
                    {
                        "type": "abort",
                        "reason": (
                            f"Aborting new launches: {unrecovered_sticks} "
                            "unrecovered stuck Bill Uploads rows "
                            f"(limit {MAX_UNRECOVERED_STICKS}). "
                            "In-flight handshakes will finish."
                        ),
                        "unrecovered_sticks": unrecovered_sticks,
                    }
                )
        elif row.status == "file_hitl_rejected":
            outcome.reject_reason = aia.rejection_for(row)
            emit(
                {
                    **evt,
                    "phase": "rejected",
                    "action": "pushed",
                    "hitl_status": row.status,
                    "file_uuid": ref.file_uuid,
                    "reason": (
                        outcome.reject_reason.message
                        if outcome.reject_reason
                        else (row.status_message or "rejected")
                    ),
                }
            )
        elif row.status == "file_hitl_success":
            emit(
                {
                    **evt,
                    "phase": "needs_review",
                    "action": "pushed",
                    "hitl_status": row.status,
                    "file_uuid": ref.file_uuid,
                }
            )

        outcome.notes = notes
        return outcome

    # ---- Schedule work in document order; launch with bounded concurrency ----
    to_upload: list[int] = []
    for index, plan in enumerate(planned):
        if retry_set is not None and index not in retry_set:
            continue
        pre = preflight(index, plan)
        if pre is not None:
            outcomes[index] = pre
            continue
        to_upload.append(index)

    def _load_prior_outcomes() -> list[dict[str, Any]]:
        prior_path = session.dir / "aia_push_outcomes.json"
        if not prior_path.is_file():
            return []
        try:
            data = json.loads(prior_path.read_text())
            rows = data.get("outcomes") or []
            return rows if isinstance(rows, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    if dry_run:
        for i, plan in enumerate(planned):
            if outcomes[i] is None and (retry_set is None or i in retry_set):
                outcomes[i] = BillPushOutcome(
                    file_name=plan.file_name,
                    page_count=plan.page_count,
                    invoice_number=plan.invoice_number,
                    page_start=plan.page_start,
                    page_end=plan.page_end,
                    action="would_push",
                    notes=["dry_run"],
                )
        # Merge prior successes when retrying a subset.
        if retry_set is not None:
            prior = _load_prior_outcomes()
            for i, plan in enumerate(planned):
                if outcomes[i] is not None:
                    continue
                if i < len(prior) and isinstance(prior[i], dict):
                    outcomes[i] = BillPushOutcome.model_validate(prior[i])
                else:
                    outcomes[i] = BillPushOutcome(
                        file_name=plan.file_name,
                        page_count=plan.page_count,
                        invoice_number=plan.invoice_number,
                        page_start=plan.page_start,
                        page_end=plan.page_end,
                        action="aborted",
                        notes=["not_in_retry_set"],
                    )
        final_list = [o for o in outcomes if o is not None]
        # Guarantee document-order length
        if len(final_list) != total:
            final_list = [
                outcomes[i]
                if outcomes[i] is not None
                else BillPushOutcome(
                    file_name=planned[i].file_name,
                    page_count=planned[i].page_count,
                    invoice_number=planned[i].invoice_number,
                    page_start=planned[i].page_start,
                    page_end=planned[i].page_end,
                    action="would_push",
                    notes=["dry_run"],
                )
                for i in range(total)
            ]
        counts = _outcome_counts(final_list, unrecovered_sticks=0)
        api_base = (cfg.aia_api_base or "").rstrip("/")
        emit(
            {
                "type": "summary",
                "counts": counts,
                "needs_review_url": (
                    f"{api_base}/accounts-payable" if api_base else None
                ),
                "aborted": False,
                "concurrency": concurrency,
                "outcomes": [o.model_dump(mode="json") for o in final_list],
            }
        )
        out_path = session.dir / "aia_push_outcomes.json"
        out_path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "dry_run": True,
                    "erp": erp,
                    "max_downstream_pages": cap,
                    "concurrency": concurrency,
                    "counts": counts,
                    "outcomes": [o.model_dump(mode="json") for o in final_list],
                },
                indent=2,
            )
        )
        return final_list

    # Live concurrent upload+poll. Submit in document order; gate on
    # stop_launching so sticks stop NEW launches while in-flight finish.
    next_pos = 0
    in_flight: dict[Any, int] = {}
    active_concurrency = concurrency

    with ThreadPoolExecutor(max_workers=max(concurrency, 1)) as pool:
        while next_pos < len(to_upload) or in_flight:
            with state_lock:
                launching_blocked = stop_launching
                active_concurrency = concurrency

            while (
                next_pos < len(to_upload)
                and len(in_flight) < active_concurrency
                and not launching_blocked
            ):
                index = to_upload[next_pos]
                next_pos += 1
                fut = pool.submit(process_one, index, planned[index])
                in_flight[fut] = index

            if launching_blocked and next_pos < len(to_upload):
                for index in to_upload[next_pos:]:
                    plan = planned[index]
                    evt = base_evt(index, plan)
                    outcome = BillPushOutcome(
                        file_name=plan.file_name,
                        page_count=plan.page_count,
                        invoice_number=plan.invoice_number,
                        page_start=plan.page_start,
                        page_end=plan.page_end,
                        action="aborted",
                        notes=["not_launched_after_abort"],
                    )
                    outcomes[index] = outcome
                    reason = abort_user_message or (
                        "Not launched — abort after unrecovered "
                        "stuck Bill Uploads rows."
                    )
                    emit(
                        {
                            **evt,
                            "phase": "aborted",
                            "action": "aborted",
                            "reason": reason,
                        }
                    )
                next_pos = len(to_upload)

            if not in_flight:
                break

            done, _ = wait(
                list(in_flight.keys()), return_when=FIRST_COMPLETED, timeout=1.0
            )
            for fut in done:
                index = in_flight.pop(fut)
                try:
                    outcomes[index] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    plan = planned[index]
                    outcomes[index] = BillPushOutcome(
                        file_name=plan.file_name,
                        page_count=plan.page_count,
                        invoice_number=plan.invoice_number,
                        page_start=plan.page_start,
                        page_end=plan.page_end,
                        action="failed",
                        notes=[type(exc).__name__],
                    )
                    emit(
                        {
                            **base_evt(index, plan),
                            "phase": "failed",
                            "action": "failed",
                            "reason": type(exc).__name__,
                        }
                    )

    final_list = []
    prior = _load_prior_outcomes() if retry_set is not None else []
    for i in range(total):
        if outcomes[i] is not None:
            final_list.append(outcomes[i])
            continue
        if retry_set is not None and i < len(prior) and isinstance(prior[i], dict):
            final_list.append(BillPushOutcome.model_validate(prior[i]))
            continue
        final_list.append(
            BillPushOutcome(
                file_name=planned[i].file_name,
                page_count=planned[i].page_count,
                invoice_number=planned[i].invoice_number,
                page_start=planned[i].page_start,
                page_end=planned[i].page_end,
                action="failed",
                notes=["missing_outcome"],
            )
        )

    counts = _outcome_counts(final_list, unrecovered_sticks=unrecovered_sticks)
    api_base = (cfg.aia_api_base or "").rstrip("/")
    needs_review_url = f"{api_base}/accounts-payable" if api_base else None
    emit(
        {
            "type": "summary",
            "counts": counts,
            "needs_review_url": needs_review_url,
            "aborted": aborted,
            "concurrency": concurrency,
            "throttle_events": throttle_events,
            "outcomes": [o.model_dump(mode="json") for o in final_list],
        }
    )

    out_path = session.dir / "aia_push_outcomes.json"
    out_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "dry_run": dry_run,
                "erp": erp,
                "max_downstream_pages": cap,
                "concurrency": concurrency,
                "counts": counts,
                "aborted": aborted,
                "throttle_events": throttle_events,
                "only_indices": only_indices,
                "outcomes": [o.model_dump(mode="json") for o in final_list],
            },
            indent=2,
        )
    )
    return final_list
