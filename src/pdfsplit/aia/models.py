"""Pydantic models for AIA AP responses (docs/AIA_AP_API.md §2).

Unknown fields are tolerated (``extra='allow'``) so internal API drift does
not crash the client; callers should still prefer known attributes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class FileStatusCode(str, Enum):
    """Bill-upload API status vocabulary (AIA_AP_API.md §3)."""

    FILE_UPLOADED = "file_uploaded"
    FILE_DATA_EXTRACTED = "file_data_extracted"
    FILE_DATA_ENRICHED = "file_data_enriched"
    FILE_HITL_SUCCESS = "file_hitl_success"
    FILE_HITL_REJECTED = "file_hitl_rejected"
    FILE_DATA_PARTIALLY_EXTRACTED = "file_data_partially_extracted"


FILE_STATUS_UI_LABEL: dict[str, str] = {
    FileStatusCode.FILE_UPLOADED.value: "Extracting",
    FileStatusCode.FILE_DATA_EXTRACTED.value: "Extracting",
    FileStatusCode.FILE_DATA_ENRICHED.value: "Extracting",
    FileStatusCode.FILE_HITL_SUCCESS.value: "Completed",
    FileStatusCode.FILE_HITL_REJECTED.value: "Failed",
    FileStatusCode.FILE_DATA_PARTIALLY_EXTRACTED.value: "Partially Extracted",
}

TERMINAL_SUCCESS = frozenset({FileStatusCode.FILE_HITL_SUCCESS.value})
TERMINAL_FAILURE = frozenset({FileStatusCode.FILE_HITL_REJECTED.value})
TERMINAL_STATUSES = TERMINAL_SUCCESS | TERMINAL_FAILURE


class RejectReasonCode(str, Enum):
    PAGE_LIMIT = "page_limit"
    VALIDATION = "validation"
    DUPLICATE = "duplicate"
    OVERSIZED_PREFLIGHT = "oversized_preflight"
    UNKNOWN = "unknown"


class AiaSessionUser(_Tolerant):
    uuid: str | None = None
    company_uuid: str | None = Field(default=None, alias="companyUuid")
    uc_uuid: str | None = Field(default=None, alias="ucUuid")
    tool_connected: str | None = Field(default=None, alias="toolConnected")
    permissions: Any = None
    email: str | None = None
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")


class AiaSession(_Tolerant):
    """Normalized session view for callers (company / uc / permissions)."""

    user: AiaSessionUser | None = None
    expires: str | None = None

    @property
    def company_uuid(self) -> str:
        if self.user is None or not self.user.company_uuid:
            raise ValueError("AIA session missing user.companyUuid")
        return self.user.company_uuid

    @property
    def uc_uuid(self) -> str | None:
        if self.user is None:
            return None
        return self.user.uc_uuid

    @property
    def permissions(self) -> Any:
        if self.user is None:
            return None
        return self.user.permissions


class DmsUploadData(_Tolerant):
    file_uuid: str | None = Field(default=None, alias="fileUuid")
    presigned_url: str | None = Field(default=None, alias="presignedUrl")
    data_key: str | None = Field(default=None, alias="dataKey")


class DmsUploadResponse(_Tolerant):
    message: str | None = None
    data: DmsUploadData | None = None
    error: Any = None


class BillFileStatus(_Tolerant):
    bill_file_status_uuid: str | None = Field(default=None, alias="billFileStatusUuid")
    file_uuid: str | None = Field(default=None, alias="fileUuid")
    file_name: str | None = Field(default=None, alias="fileName")
    file_category: str | None = Field(default=None, alias="fileCategory")
    file_extension: str | None = Field(default=None, alias="fileExtension")
    status: str | None = None
    status_message: str | None = Field(default=None, alias="statusMessage")
    total_pages: int | None = Field(default=None, alias="totalPages")
    is_error_details: bool | None = Field(default=None, alias="isErrorDetails")
    company_uuid: str | None = Field(default=None, alias="companyUuid")
    third_party_product: str | None = Field(default=None, alias="thirdPartyProduct")

    @property
    def ui_label(self) -> str:
        if not self.status:
            return "Unknown"
        return FILE_STATUS_UI_LABEL.get(self.status, self.status)


class BillStatusList(_Tolerant):
    count: int | None = None
    next: Any = None
    previous: Any = None
    results: list[BillFileStatus] = Field(default_factory=list)


class DuplicateCheck(_Tolerant):
    is_present: bool = Field(default=False, alias="isPresent")


class RejectReason(_Tolerant):
    code: RejectReasonCode = RejectReasonCode.UNKNOWN
    message: str = ""
    pdf_pages: int | None = None
    page_limit: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BillRef(_Tolerant):
    """Result of Mode A push — may be partial if a mid-pipeline step failed."""

    file_name: str
    file_uuid: str | None = None
    presigned_url_present: bool = False
    step_completed: Literal[0, 1, 2, 3, 4] = 0
    failed_step: int | None = None
    error: str | None = None
    page_count: int | None = None
    invoice_number: str | None = None
    company_uuid: str | None = None

    @property
    def ok(self) -> bool:
        return self.failed_step is None and self.step_completed == 4 and bool(
            self.file_uuid
        )


class BillPushOutcome(_Tolerant):
    """Per-invoice push plan / result for aggregation (split-quality signal)."""

    file_name: str
    page_count: int
    invoice_number: str = ""
    page_start: int | None = None
    page_end: int | None = None
    action: Literal[
        "would_push",
        "pushed",
        "skipped_oversized",
        "skipped_duplicate",
        "failed",
        "aborted",
    ] = "would_push"
    bill_ref: BillRef | None = None
    reject_reason: RejectReason | None = None
    hitl_status: str | None = None
    duplicate_in_aia: bool | None = None
    notes: list[str] = Field(default_factory=list)


class ReviewVoucherEnvelope(_Tolerant):
    """GET review-vouchers/{billId} envelope — kept as tolerant dict wrapper."""

    prediction: dict[str, Any] = Field(default_factory=dict)
    navigation: dict[str, Any] = Field(default_factory=dict)
