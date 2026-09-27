"""Shared link-token gate for the demo web service.

When ``DEMO_TOKEN`` is set, every route except ``/healthz`` and ``/robots.txt``
requires either:
  - query ``?k=<token>`` (sets a long-lived cookie, then redirects to a clean URL), or
  - a matching ``demo_k`` cookie.

No match → HTTP 404 (not a login page). When ``DEMO_TOKEN`` is unset the gate
is open (local/dev). Constant-time compare for the token check.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

HEALTHZ_PATH = "/healthz"
ROBOTS_PATH = "/robots.txt"
COOKIE_NAME = "demo_k"
#: ~180 days — long enough for a stakeholder demo window.
COOKIE_MAX_AGE_S = 60 * 60 * 24 * 180


def demo_token() -> str:
    return (os.getenv("DEMO_TOKEN") or "").strip()


def token_matches(candidate: str | None) -> bool:
    expected = demo_token()
    if not expected or candidate is None:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _url_without_k(url: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "k"]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment)
    )


class LinkTokenMiddleware(BaseHTTPMiddleware):
    """404-gate when DEMO_TOKEN is configured; open when unset."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in {HEALTHZ_PATH, ROBOTS_PATH}:
            return await call_next(request)

        expected = demo_token()
        if not expected:
            return await call_next(request)

        k = request.query_params.get("k")
        if k is not None:
            if not token_matches(k):
                return PlainTextResponse("Not Found", status_code=404)
            # Valid share link: set cookie and strip ?k= from the URL.
            clean = _url_without_k(str(request.url))
            response = RedirectResponse(url=clean, status_code=302)
            response.set_cookie(
                COOKIE_NAME,
                expected,
                max_age=COOKIE_MAX_AGE_S,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/",
            )
            return response

        if token_matches(request.cookies.get(COOKIE_NAME)):
            return await call_next(request)

        return PlainTextResponse("Not Found", status_code=404)
