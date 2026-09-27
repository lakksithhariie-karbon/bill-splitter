"""Parse AIA rejection messages into typed reasons."""

from __future__ import annotations

import re
from typing import Any

from pdfsplit.aia.models import RejectReason, RejectReasonCode

#: Production message shapes observed live:
#: - "Extraction failed: PDF has 31 pages; limit is 12."
#: - "This file has 13 pages. AIA reads up to 12 pages per bill — …"
_PAGE_LIMIT_RE = re.compile(
    r"PDF has\s+(\d+)\s+pages?\s*;\s*limit is\s+(\d+)",
    re.IGNORECASE,
)
_PAGE_LIMIT_AIA_RE = re.compile(
    r"This file has\s+(\d+)\s+pages?\.\s*AIA reads up to\s+(\d+)\s+pages?",
    re.IGNORECASE,
)


def aia_page_limit_message(pdf_pages: int, page_limit: int = 12) -> str:
    """AIA's own Bill Uploads wording for the page-limit reject."""
    return (
        f"This file has {pdf_pages} pages. AIA reads up to {page_limit} pages "
        "per bill — split it into smaller files and upload again."
    )


def parse_reject_reason(
    status_message: str | None,
    *,
    error_details: dict[str, Any] | None = None,
) -> RejectReason:
    """Map statusMessage (+ optional error-details) to a typed RejectReason."""
    msg = (status_message or "").strip()
    details = dict(error_details or {})

    for pattern in (_PAGE_LIMIT_RE, _PAGE_LIMIT_AIA_RE):
        m = pattern.search(msg)
        if m:
            return RejectReason(
                code=RejectReasonCode.PAGE_LIMIT,
                message=msg,
                pdf_pages=int(m.group(1)),
                page_limit=int(m.group(2)),
                details=details,
            )

    # Nested detail payloads may carry the same string.
    blob = str(details)
    for pattern in (_PAGE_LIMIT_RE, _PAGE_LIMIT_AIA_RE):
        m2 = pattern.search(blob)
        if m2:
            return RejectReason(
                code=RejectReasonCode.PAGE_LIMIT,
                message=msg
                or aia_page_limit_message(int(m2.group(1)), int(m2.group(2))),
                pdf_pages=int(m2.group(1)),
                page_limit=int(m2.group(2)),
                details=details,
            )

    if msg:
        lower = msg.lower()
        if "missing mandatory" in lower or "vendor not found" in lower:
            return RejectReason(
                code=RejectReasonCode.VALIDATION,
                message=msg,
                details=details,
            )
        return RejectReason(
            code=RejectReasonCode.UNKNOWN,
            message=msg,
            details=details,
        )

    return RejectReason(
        code=RejectReasonCode.UNKNOWN,
        message="",
        details=details,
    )
