"""AiaApClient — narrow Mode A seam for AI Accountant AP uploads."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Literal

from pdfsplit.aia.models import (
    AiaSession,
    BillFileStatus,
    BillRef,
    BillStatusList,
    DmsUploadResponse,
    DuplicateCheck,
    RejectReason,
    ReviewVoucherEnvelope,
    TERMINAL_FAILURE,
    TERMINAL_STATUSES,
)
from pdfsplit.aia.reasons import parse_reject_reason
from pdfsplit.aia.transport import (
    AiaAuthExpiredError,
    AiaLiveDisabledError,
    AiaTransport,
    FixtureTransport,
    HttpTransport,
    assert_live_allowed,
    load_cookie_header,
    redact_secrets,
)
from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings

ErpName = Literal["tally", "zoho"]

#: Bill-status poll interval (docs/AIA_STUCK_ROW.md volume procedure).
DEFAULT_POLL_INTERVAL_S = 5.0
#: First-pass poll budget before one recovery PATCH.
DEFAULT_POLL_TIMEOUT_S = 120.0
#: After recovery PATCH, poll again this long.
RECOVERY_POLL_TIMEOUT_S = 90.0


class PushStepError(RuntimeError):
    """A Mode A pipeline step failed; ``bill_ref`` records how far it got."""

    def __init__(self, message: str, bill_ref: BillRef) -> None:
        super().__init__(redact_secrets(message))
        self.bill_ref = bill_ref


class AiaRateLimitError(RuntimeError):
    """AIA returned HTTP 429 — callers should back off and drop concurrency."""

    def __init__(self, message: str = "AIA rate limited (HTTP 429)") -> None:
        super().__init__(redact_secrets(message))
        self.retry_after_s: float | None = None


class AiaApClient:
    """Injectable AIA AP client. Default transport is fixture replay."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: AiaTransport | None = None,
        erp: ErpName = "tally",
        *,
        fixture_dir: Path | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.erp: ErpName = "zoho" if erp == "zoho" else "tally"
        self._transport = transport or self._default_transport(fixture_dir=fixture_dir)
        self._session_cache: AiaSession | None = None

    @staticmethod
    def live_writes_enabled(settings: Settings | None = None) -> bool:
        cfg = settings or default_settings
        return bool(cfg.aia_api_base.strip()) and bool(cfg.aia_allow_writes)

    def _default_transport(self, *, fixture_dir: Path | None) -> AiaTransport:
        cfg = self.settings
        if self.live_writes_enabled(cfg):
            base = assert_live_allowed(
                api_base=cfg.aia_api_base, allow_writes=cfg.aia_allow_writes
            )
            cookies = load_cookie_header()
            return HttpTransport(
                api_base=base, cookie_header=cookies, settings=cfg
            )
        # Fixture replay — never invent a production host.
        return FixtureTransport(fixture_dir=fixture_dir)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        push_step: int | None = None,
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "method": method,
            "path": path,
            "json": json_body,
            "params": params,
        }
        if push_step is not None:
            req["_push_step"] = push_step
        try:
            resp = self._transport(req)
        except AiaLiveDisabledError:
            raise
        except Exception as exc:  # noqa: BLE001 — redact on the way out
            raise RuntimeError(redact_secrets(str(exc))) from None
        if not isinstance(resp, dict):
            raise RuntimeError("AIA transport returned a non-dict response")
        status = int(resp.get("status") or 0)
        if status == 429:
            err = AiaRateLimitError(
                f"AIA rate limited: HTTP 429 on {method} {path}"
            )
            headers = resp.get("headers") or {}
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
            if retry_after is not None:
                try:
                    err.retry_after_s = float(retry_after)
                except (TypeError, ValueError):
                    err.retry_after_s = None
            raise err
        return resp

    def _erp_product(self) -> str:
        return "zoho" if self.erp == "zoho" else "tally"

    def _review_base(self) -> str:
        if self.erp == "zoho":
            return "/api/accounts-payable/review-bills"
        return "/api/accounts-payable/tally/review-vouchers"

    def _duplicate_path(self) -> str:
        if self.erp == "zoho":
            # Zoho parity path guessed from tally naming; fixture covers both.
            return "/api/accounts-payable/zoho/is-sup-inv-no-exists"
        return "/api/accounts-payable/tally/is-sup-inv-no-exists"

    # ----- public API -----------------------------------------------------

    @staticmethod
    def _raise_if_auth_failed(status: int, *, where: str) -> None:
        if int(status) in (401, 403):
            raise AiaAuthExpiredError(
                f"{AiaAuthExpiredError.USER_MESSAGE} ({where}: HTTP {status})"
            )

    def session(self) -> AiaSession:
        if self._session_cache is not None:
            return self._session_cache
        resp = self._request("GET", "/api/auth/session")
        status = int(resp.get("status") or 0)
        self._raise_if_auth_failed(status, where="session")
        body = resp.get("json") or {}
        if status >= 400:
            raise RuntimeError(
                redact_secrets(f"AIA session failed: HTTP {status}")
            )
        # Empty / logged-out session often returns 200 with no user.
        if not isinstance(body, dict) or not body.get("user"):
            raise AiaAuthExpiredError(
                f"{AiaAuthExpiredError.USER_MESSAGE} (session has no user)"
            )
        self._session_cache = AiaSession.model_validate(body)
        return self._session_cache

    def push_bill(
        self,
        pdf_path: str | Path,
        file_name: str,
        *,
        page_count: int | None = None,
        invoice_number: str | None = None,
    ) -> BillRef:
        """Mode A: one invoice PDF → DMS → bill-status (extraction starts)."""
        path = Path(pdf_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF not found: {path}")
        data = path.read_bytes()
        size = len(data)
        ext = path.suffix.lstrip(".").lower() or "pdf"
        content_type = "application/pdf" if ext == "pdf" else f"application/{ext}"
        sess = self.session()
        company_uuid = sess.company_uuid
        uc_uuid = sess.uc_uuid or ""
        product = self._erp_product()
        # Two different fileCategory namespaces:
        # - DMS upload-file-to-dms only accepts enum values like "bill"
        #   (live 422 if "voucher" is sent).
        # - bill-status uses "voucher" when toolConnected=tally so the
        #   prediction lands in tally/review-vouchers (not review-bills).
        # Frontend: DMS default "bill"; bill-status N = tally?"voucher":"bill".
        dms_file_category = "bill"
        ap_file_category = "voucher" if product == "tally" else "bill"

        ref = BillRef(
            file_name=file_name,
            page_count=page_count,
            invoice_number=invoice_number,
            company_uuid=company_uuid,
            step_completed=0,
        )

        # Step 1 — obtain fileUuid + presignedUrl
        # Live AIA requires ucUuid/companyUuid/fileSize. accountNumber/bankName
        # are also required by the API; the web client hardcodes them as
        # string literals on every DMS upload (not session/company bank data):
        #   accountNumber: "87651676591"
        #   bankName: "-1"
        # Verified in bundle static__chunks__pages___app-*.js (upload-file-to-dms
        # body builder). Treat as API placeholders/sentinels, not customer PII.
        resp1 = self._request(
            "POST",
            "/api/upload-file-to-dms",
            json_body={
                "ucUuid": uc_uuid,
                "companyUuid": company_uuid,
                "accountNumber": "87651676591",
                "fileSize": size,
                "bankName": "-1",
                "fileName": file_name,
                "fileCategory": dms_file_category,
                "fileExtension": ext,
                "description": "",
                "additionalMetadata": {"ContentType": content_type},
            },
            push_step=1,
        )
        st1 = int(resp1.get("status") or 0)
        self._raise_if_auth_failed(st1, where="upload-file-to-dms")
        if st1 >= 400:
            ref.failed_step = 1
            ref.error = f"upload-file-to-dms failed: HTTP {st1}"
            raise PushStepError(ref.error, ref)
        dms = DmsUploadResponse.model_validate(resp1.get("json") or {})
        file_uuid = dms.data.file_uuid if dms.data else None
        presigned = dms.data.presigned_url if dms.data else None
        if not file_uuid or not presigned:
            ref.failed_step = 1
            ref.error = "upload-file-to-dms response missing fileUuid/presignedUrl"
            raise PushStepError(ref.error, ref)
        ref.file_uuid = file_uuid
        ref.presigned_url_present = True
        ref.step_completed = 1

        # Step 2 — app-mediated storage upload (base64 body).
        # Never log fileContent or the presigned URL.
        b64 = base64.b64encode(data).decode("ascii")
        resp2 = self._request(
            "POST",
            "/api/upload-file",
            json_body={
                "presignedUrl": presigned,
                "fileType": content_type,
                "fileName": file_name,
                "fileContent": b64,
            },
            push_step=2,
        )
        st2 = int(resp2.get("status") or 0)
        self._raise_if_auth_failed(st2, where="upload-file")
        if st2 >= 400:
            ref.failed_step = 2
            ref.error = f"upload-file failed: HTTP {st2}"
            raise PushStepError(ref.error, ref)
        ref.step_completed = 2

        # Step 3 — mark uploaded (orphan risk if step 4 fails).
        # Live run: without triggerEvent=true the bill-status row stayed
        # file_uploaded indefinitely; PATCH with triggerEvent kicked extraction.
        resp3 = self._request(
            "PATCH",
            "/api/upload-file-to-dms",
            json_body={
                "fileUuid": file_uuid,
                "status": "uploaded",
                "thirdPartyProduct": product,
                "triggerEvent": True,
            },
            push_step=3,
        )
        st3 = int(resp3.get("status") or 0)
        self._raise_if_auth_failed(st3, where="upload-file-to-dms PATCH")
        if st3 >= 400:
            ref.failed_step = 3
            ref.error = f"upload-file-to-dms PATCH failed: HTTP {st3}"
            raise PushStepError(ref.error, ref)
        ref.step_completed = 3

        # Step 4 — register for AP extraction
        resp4 = self._request(
            "POST",
            "/api/accounts-payable/bill-status",
            json_body={
                "fileUuid": file_uuid,
                "fileCategory": ap_file_category,
                "fileName": file_name,
                "thirdPartyProduct": product,
                "companyUuid": company_uuid,
                "fileSize": size,
                "fileExtension": ext,
            },
            push_step=4,
        )
        st4 = int(resp4.get("status") or 0)
        self._raise_if_auth_failed(st4, where="bill-status")
        if st4 >= 400:
            ref.failed_step = 4
            ref.error = (
                f"bill-status register failed: HTTP {st4} "
                "(DMS object may be orphaned)"
            )
            raise PushStepError(ref.error, ref)
        ref.step_completed = 4
        return ref

    def bill_status(
        self,
        file_uuid: str | None = None,
        page: int = 1,
        *,
        page_size: int = 50,
        status: str | None = None,
    ) -> list[BillFileStatus]:
        params: dict[str, Any] = {
            "companyId": self.session().company_uuid,
            "page": page,
            "pageSize": page_size,
        }
        if status:
            params["status"] = status
        resp = self._request(
            "GET", "/api/accounts-payable/bill-status", params=params
        )
        body = resp.get("json") or {}
        listing = BillStatusList.model_validate(body)
        rows = listing.results
        if file_uuid:
            rows = [r for r in rows if r.file_uuid == file_uuid]
        return rows

    def retrigger_extraction(self, file_uuid: str) -> dict[str, Any]:
        """One-shot stuck-row recovery PATCH (docs/AIA_STUCK_ROW.md).

        Never register a second bill-status row for the same fileUuid.
        """
        return self._request(
            "PATCH",
            "/api/upload-file-to-dms",
            json_body={
                "fileUuid": file_uuid,
                "status": "uploaded",
                "thirdPartyProduct": self._erp_product(),
                "triggerEvent": True,
            },
        )

    def wait_for_terminal_status(
        self,
        file_uuid: str,
        *,
        timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        sleep: Any = time.sleep,
        on_status: Any | None = None,
        allow_recovery: bool = True,
        recovery_timeout_s: float = RECOVERY_POLL_TIMEOUT_S,
    ) -> tuple[BillFileStatus, dict[str, Any]]:
        """Poll bill-status until hitl success/reject or timeout.

        Returns ``(last_row, meta)`` where meta may include
        ``recovered_with_trigger`` / ``stuck_unrecovered``.

        Ably ``ap_bill_file_status_changed`` is the upgrade path; not built here.
        """

        def _poll(budget_s: float) -> BillFileStatus | None:
            deadline = time.monotonic() + budget_s
            last: BillFileStatus | None = None
            while time.monotonic() < deadline:
                rows = self.bill_status(file_uuid=file_uuid)
                if rows:
                    last = rows[0]
                    if on_status is not None:
                        on_status(last)
                    if last.status in TERMINAL_STATUSES:
                        return last
                sleep(poll_interval_s)
            return last

        meta: dict[str, Any] = {
            "recovered_with_trigger": False,
            "stuck_unrecovered": False,
        }
        last = _poll(timeout_s)
        if last is not None and last.status in TERMINAL_STATUSES:
            return last, meta

        if (
            allow_recovery
            and last is not None
            and last.status == "file_uploaded"
        ):
            try:
                self.retrigger_extraction(file_uuid)
                meta["recovered_with_trigger"] = True
            except Exception:  # noqa: BLE001
                meta["recovery_patch_failed"] = True
            last = _poll(recovery_timeout_s) or last
            if last is not None and last.status in TERMINAL_STATUSES:
                return last, meta

        if last is not None and last.status not in TERMINAL_STATUSES:
            meta["stuck_unrecovered"] = True
            return last, meta
        if last is not None:
            return last, meta
        raise TimeoutError(
            "Timed out waiting for AIA bill status for file (uuid redacted)"
        )

    def error_details(self, file_uuid: str) -> dict[str, Any]:
        resp = self._request(
            "GET",
            "/api/accounts-payable/file-status-error-details",
            params={
                "companyUuid": self.session().company_uuid,
                "fileUuid": file_uuid,
            },
        )
        body = resp.get("json")
        return body if isinstance(body, dict) else {"raw": body}

    def rejection_for(self, row: BillFileStatus) -> RejectReason | None:
        if row.status not in TERMINAL_FAILURE:
            return None
        details: dict[str, Any] | None = None
        if row.file_uuid and row.is_error_details:
            try:
                details = self.error_details(row.file_uuid)
            except Exception:  # noqa: BLE001
                details = None
        elif row.file_uuid and row.status == "file_hitl_rejected":
            try:
                details = self.error_details(row.file_uuid)
            except Exception:  # noqa: BLE001
                details = None
        return parse_reject_reason(row.status_message, error_details=details)

    def supplier_invoice_exists(self, sup_inv_no: str) -> bool:
        inv = (sup_inv_no or "").strip()
        if not inv:
            return False
        params = {
            "companyId": self.session().company_uuid,
            "supInvNo": inv,
        }
        resp = self._request("GET", self._duplicate_path(), params=params)
        body = resp.get("json") or {}
        return bool(DuplicateCheck.model_validate(body).is_present)

    def review_voucher(self, bill_id: str) -> dict[str, Any]:
        """Read-only verification fetch of a Needs Review prediction envelope."""
        resp = self._request(
            "GET",
            f"{self._review_base()}/{bill_id}",
            params={"companyId": self.session().company_uuid},
        )
        body = resp.get("json") or {}
        env = ReviewVoucherEnvelope.model_validate(body)
        return env.model_dump()

    def list_needs_review(self, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """List Needs Review rows (Tally path by default)."""
        resp = self._request(
            "GET",
            self._review_base(),
            params={
                "companyId": self.session().company_uuid,
                "page": page,
                "pageSize": page_size,
            },
        )
        body = resp.get("json")
        return body if isinstance(body, dict) else {"raw": body}

    def delete_needs_review(self, bill_id: str) -> dict[str, Any]:
        """DELETE a Needs Review row. Never calls tally/bill Approve."""
        resp = self._request(
            "DELETE",
            f"{self._review_base()}/{bill_id}",
            params={"companyId": self.session().company_uuid},
        )
        status = int(resp.get("status") or 0)
        self._raise_if_auth_failed(status, where="delete needs-review")
        body = resp.get("json")
        if status >= 400:
            raise RuntimeError(
                redact_secrets(f"AIA delete failed: HTTP {status}")
            )
        return body if isinstance(body, dict) else {"status": status, "raw": body}
