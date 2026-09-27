"""AI Accountant Accounts Payable client (Mode A upload seam).

Default transport is fixture replay. Live HTTP requires both
``AIA_API_BASE`` (explicit, no production default) and
``AIA_ALLOW_WRITES=true``.
"""

from __future__ import annotations

from pdfsplit.aia.client import AiaApClient, PushStepError
from pdfsplit.aia.models import (
    AiaSession,
    BillFileStatus,
    BillPushOutcome,
    BillRef,
    FileStatusCode,
)
from pdfsplit.aia.transport import AiaLiveDisabledError

__all__ = [
    "AiaApClient",
    "AiaLiveDisabledError",
    "AiaSession",
    "BillFileStatus",
    "BillPushOutcome",
    "BillRef",
    "FileStatusCode",
    "PushStepError",
]
