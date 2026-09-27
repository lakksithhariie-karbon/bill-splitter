"""AIA HTTP / fixture transports.

Transport callable signature mirrors ``ChatTransport``: one dict in, one dict out.
Request dict keys: ``method``, ``path``, ``json``, ``params``.
Response dict keys: ``status``, ``json`` (parsed body or None), ``headers``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from pdfsplit.config import PROJECT_ROOT
from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings

AiaTransport = Callable[[dict[str, Any]], dict[str, Any]]

#: Fixture root (synthetic shapes only — never customer data).
DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "vendors" / "aia" / "fixtures"

logger = logging.getLogger(__name__)

#: Env var holding the same JSON array shape as ``.secrets/aia_session_cookies.json``.
AIA_SESSION_COOKIES_ENV = "AIA_SESSION_COOKIES"
SESSION_COOKIE_NAME = "__Secure-next-auth.session-token"


class AiaLiveDisabledError(RuntimeError):
    """Raised when live AIA HTTP is attempted without explicit opt-in."""


def redact_secrets(text: str) -> str:
    """Strip cookie/token-like substrings from strings leaving the process."""
    if not text:
        return text
    out = re.sub(
        r"(__Secure-next-auth\.session-token|__Host-next-auth\.csrf-token)"
        r"=[^;\s]+",
        r"\1=[REDACTED]",
        text,
        flags=re.I,
    )
    out = re.sub(
        r"(Bearer\s+)[A-Za-z0-9._\-+/=]+",
        r"\1[REDACTED]",
        out,
        flags=re.I,
    )
    out = re.sub(
        r"(csrfToken|session.token|presignedUrl)(\"?\s*[:=]\s*\"?)[^\"\s,}]+",
        r"\1\2[REDACTED]",
        out,
        flags=re.I,
    )
    return out


class AiaAuthExpiredError(RuntimeError):
    """AIA returned 401/403 — session cookie needs refresh."""

    USER_MESSAGE = "AI Accountant session expired — needs a fresh cookie"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(redact_secrets(message or self.USER_MESSAGE))


def assert_live_allowed(*, api_base: str, allow_writes: bool) -> str:
    """Return a sanitized base URL or raise if live calls are not permitted."""
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise AiaLiveDisabledError(
            "AIA live calls require AIA_API_BASE (explicit; no production default) "
            "and AIA_ALLOW_WRITES=true"
        )
    if not allow_writes:
        raise AiaLiveDisabledError(
            "AIA live calls require AIA_ALLOW_WRITES=true in addition to AIA_API_BASE"
        )
    lower = base.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        raise AiaLiveDisabledError(
            "AIA_API_BASE must be an absolute http(s) URL when live writes are enabled"
        )
    return base


def _load_cookie_list(secrets_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load cookie objects from ``AIA_SESSION_COOKIES`` env, else ``.secrets`` file.

    Never writes cookies to disk. Callers must not log raw values.
    """
    env_raw = (os.getenv(AIA_SESSION_COOKIES_ENV) or "").strip()
    if env_raw:
        raw = json.loads(env_raw)
        if not isinstance(raw, list):
            raise ValueError(f"{AIA_SESSION_COOKIES_ENV} must be a JSON array")
        return [c for c in raw if isinstance(c, dict)]

    root = secrets_dir or (PROJECT_ROOT / ".secrets")
    path = root / "aia_session_cookies.json"
    if not path.is_file():
        raise FileNotFoundError(
            "AIA credentials missing: set AIA_SESSION_COOKIES or place "
            "aia_session_cookies.json under .secrets/"
        )
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("AIA credentials file has unexpected shape")
    return [c for c in raw if isinstance(c, dict)]


def session_cookie_expiry_iso(secrets_dir: Path | None = None) -> str | None:
    """Return UTC ISO date (YYYY-MM-DD) of session-cookie expiry, if known.

    Prefers ``expirationDate`` on the session cookie object; never returns the
    cookie value.
    """
    try:
        cookies = _load_cookie_list(secrets_dir=secrets_dir)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None
    for c in cookies:
        if c.get("name") != SESSION_COOKIE_NAME:
            continue
        exp = c.get("expirationDate")
        if exp is None:
            return None
        try:
            dt = datetime.fromtimestamp(float(exp), tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        return dt.date().isoformat()
    return None


def log_session_cookie_expiry(*, secrets_dir: Path | None = None) -> str | None:
    """Log cookie expiry DATE only (never the value). Returns the date string."""
    day = session_cookie_expiry_iso(secrets_dir=secrets_dir)
    if day:
        logger.info("AIA session cookie expires on %s (UTC date only)", day)
    else:
        logger.warning("AIA session cookie expiry date unavailable")
    return day


def load_cookie_header(secrets_dir: Path | None = None) -> str:
    """Build a Cookie header from env or ``.secrets/aia_session_cookies.json``.

    Values never leave this function except as the opaque header string for
    HTTP. Callers must not log the return value. Never writes to disk.
    """
    raw = _load_cookie_list(secrets_dir=secrets_dir)
    #: Prefer the NextAuth session cookie; include sibling auth cookies by name.
    wanted = {
        SESSION_COOKIE_NAME,
        "__Host-next-auth.csrf-token",
        "__Secure-next-auth.callback-url",
    }
    parts: list[str] = []
    for c in raw:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        if name not in wanted:
            continue
        parts.append(f"{name}={value}")
    if not any(p.startswith(f"{SESSION_COOKIE_NAME}=") for p in parts):
        raise ValueError("AIA credentials missing session cookie")
    return "; ".join(parts)


class FixtureTransport:
    """Replay hand-authored fixtures keyed by method + path."""

    def __init__(
        self,
        fixture_dir: Path | None = None,
        *,
        overrides: dict[str, Path] | None = None,
        fail_steps: set[int] | None = None,
    ) -> None:
        self.fixture_dir = Path(fixture_dir or DEFAULT_FIXTURE_DIR)
        self.overrides = dict(overrides or {})
        self.fail_steps = set(fail_steps or ())
        self.calls: list[dict[str, Any]] = []

    def _key(self, method: str, path: str) -> str:
        path = path.split("?", 1)[0]
        return f"{method.upper()} {path}"

    def _fixture_path(self, method: str, path: str) -> Path:
        key = self._key(method, path)
        if key in self.overrides:
            return self.overrides[key]
        # Normalize dynamic segments.
        norm = path.split("?", 1)[0]
        norm = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{id}",
            norm,
            flags=re.I,
        )
        # Map to fixture files.
        mapping = {
            ("GET", "/api/auth/session"): "auth_session.json",
            ("POST", "/api/upload-file-to-dms"): "upload_file_to_dms_post.json",
            ("POST", "/api/upload-file"): "upload_file_post.json",
            ("PATCH", "/api/upload-file-to-dms"): "upload_file_to_dms_patch.json",
            ("POST", "/api/accounts-payable/bill-status"): "bill_status_post.json",
            ("GET", "/api/accounts-payable/bill-status"): "bill_status_get.json",
            (
                "GET",
                "/api/accounts-payable/file-status-error-details",
            ): "error_details.json",
            (
                "GET",
                "/api/accounts-payable/tally/is-sup-inv-no-exists",
            ): "is_sup_inv_no_exists_false.json",
            (
                "GET",
                "/api/accounts-payable/zoho/is-sup-inv-no-exists",
            ): "is_sup_inv_no_exists_false.json",
            (
                "GET",
                "/api/accounts-payable/tally/review-vouchers/{id}",
            ): "review_voucher.json",
            (
                "GET",
                "/api/accounts-payable/review-bills/{id}",
            ): "review_voucher.json",
        }
        # Exact path without uuid normalization for list endpoints.
        fname = mapping.get((method.upper(), norm))
        if fname is None:
            fname = mapping.get((method.upper(), path.split("?", 1)[0]))
        if fname is None and "/review-vouchers/" in norm:
            fname = "review_voucher.json"
        if fname is None and "/review-bills/" in norm:
            fname = "review_voucher.json"
        if fname is None:
            raise KeyError(f"No AIA fixture for {method.upper()} {path}")
        return self.fixture_dir / fname

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "GET").upper()
        path = str(request.get("path") or "")
        self.calls.append(
            {
                "method": method,
                "path": path.split("?", 1)[0],
                "has_json": request.get("json") is not None,
            }
        )
        # Optional step failures for tests (push pipeline steps 1..4).
        step = request.get("_push_step")
        if step is not None and int(step) in self.fail_steps:
            return {
                "status": 500,
                "json": {"error": f"fixture failure at step {step}"},
                "headers": {},
            }

        # Duplicate-check override via params.
        if "is-sup-inv-no-exists" in path:
            params = request.get("params") or {}
            inv = str(params.get("supInvNo") or params.get("sup_inv_no") or "")
            if inv.upper().startswith("DUP-") or request.get("_force_duplicate"):
                body = json.loads(
                    (self.fixture_dir / "is_sup_inv_no_exists_true.json").read_text()
                )
                return {"status": 200, "json": body, "headers": {}}

        path_obj = self._fixture_path(method, path)
        body = json.loads(path_obj.read_text())
        status = int(body.pop("__http_status__", 200))
        return {"status": status, "json": body, "headers": {}}


class HttpTransport:
    """Live cookie-authenticated transport.

    Construction enforces the live-call guardrail: ``__init__`` calls
    ``assert_live_allowed`` and reads ``allow_writes`` from settings (env /
    ``AIA_ALLOW_WRITES``). Injecting this transport cannot bypass the opt-in.
    """

    def __init__(
        self,
        *,
        api_base: str,
        cookie_header: str,
        timeout_s: float = 60.0,
        session: requests.Session | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings if settings is not None else default_settings
        self.api_base = assert_live_allowed(
            api_base=api_base,
            allow_writes=bool(cfg.aia_allow_writes),
        )
        self._cookie_header = cookie_header
        self.timeout_s = timeout_s
        # requests.Session is not thread-safe. Concurrent push polls from a
        # thread pool, so default to one Session per thread. An injected
        # session (tests) is used as-is on the calling thread only.
        self._injected_session = session
        self._local = threading.local()

    def _session_for_thread(self) -> requests.Session:
        if self._injected_session is not None:
            return self._injected_session
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            self._local.session = sess
        return sess

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "GET").upper()
        path = str(request.get("path") or "")
        params = request.get("params")
        url = self.api_base + path
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        try:
            resp = self._session_for_thread().request(
                method,
                url,
                json=request.get("json"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Cookie": self._cookie_header,
                },
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise RuntimeError(redact_secrets(str(exc))) from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        return {
            "status": resp.status_code,
            "json": body,
            "headers": {k: redact_secrets(v) for k, v in resp.headers.items()},
        }
