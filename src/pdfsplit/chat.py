"""Mistral chat completions client (architecture B extraction).

Verified request shape against docs.mistral.ai (Chat Completions API):
  POST https://api.mistral.ai/v1/chat/completions
  Authorization: Bearer <key>
  {
    "model": "<model>",
    "messages": [{"role": "system"|"user", "content": "..."}, ...],
    "temperature": 0,
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "...",
        "schema": { ... },
        "strict": true
      }
    }
  }

Vision (docs.mistral.ai/capabilities/vision): user content may be a list of
parts — text plus ``{"type": "image_url", "image_url": "<url|data-uri>"}``.

`mistral-small-latest` supports `response_format.type = json_schema` (docs +
community stress tests). If a call rejects json_schema, we fall back to
prompt-only JSON + one repair retry.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings

BASE_URL = "https://api.mistral.ai"

#: Initial attempt + up to this many retries on HTTP 429.
_MAX_429_RETRIES = 3
#: Backoff seconds for retry attempts 1..3 when Retry-After is absent.
_DEFAULT_429_BACKOFF_S = (2.0, 4.0, 8.0)
#: Jitter fraction applied to default backoff (+/- 25%).
_429_JITTER = 0.25

ChatTransport = Callable[[dict], dict]


@dataclass
class ChatResult:
    data: dict
    usage: dict
    model: str
    raw_content: str
    used_json_schema: bool


class ChatClient:
    """Thin wrapper around POST /v1/chat/completions."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: ChatTransport | None = None,
        *,
        base_url: str = BASE_URL,
        api_key: str | None = None,
        json_mode: bool = False,
        min_request_interval_s: float = 0.0,
        max_completion_tokens: int | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key if api_key is not None else self.settings.mistral_api_key
        self._json_mode = json_mode
        self._min_request_interval_s = max(0.0, float(min_request_interval_s))
        self._max_completion_tokens = max_completion_tokens
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        #: Cumulative 429 retries performed by this client (for AB hygiene).
        self.retries_used: int = 0

    def complete_json(
        self,
        *,
        messages: list[dict[str, Any]],
        json_schema: dict | None = None,
        schema_name: str = "response",
        temperature: float = 0.0,
        model: str | None = None,
        timeout: float | None = None,
        max_completion_tokens: int | None = None,
    ) -> ChatResult:
        model_id = model or self.settings.mistral_chat_model
        timeout_s = (
            float(timeout)
            if timeout is not None
            else float(getattr(self.settings, "mistral_chat_timeout_s", 120) or 120)
        )
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
        }
        token_limit = (
            max_completion_tokens
            if max_completion_tokens is not None
            else self._max_completion_tokens
        )
        if token_limit is not None:
            payload["max_completion_tokens"] = max(1, int(token_limit))
        used_schema = False
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        elif json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
            used_schema = True

        try:
            resp = self._post(payload, timeout=timeout_s)
        except requests.HTTPError as exc:
            # Some models / accounts may reject json_schema — fall back once.
            if (
                "response_format" in payload
                and exc.response is not None
                and exc.response.status_code in (400, 422)
            ):
                payload.pop("response_format", None)
                used_schema = False
                # Ensure the model still emits JSON.
                messages = list(messages) + [
                    {
                        "role": "user",
                        "content": (
                            "Respond with a single JSON object only. "
                            "No markdown fences."
                        ),
                    }
                ]
                payload["messages"] = messages
                resp = self._post(payload, timeout=timeout_s)
            else:
                raise

        content = _message_content(resp)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # One repair retry.
            repair_messages = list(messages) + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid JSON. "
                        "Reply again with ONLY a valid JSON object."
                    ),
                },
            ]
            repair_payload = {
                "model": model_id,
                "messages": repair_messages,
                "temperature": 0.0,
            }
            if token_limit is not None:
                repair_payload["max_completion_tokens"] = max(1, int(token_limit))
            if self._json_mode:
                repair_payload["response_format"] = {"type": "json_object"}
            resp = self._post(repair_payload, timeout=timeout_s)
            content = _message_content(resp)
            data = json.loads(content)
            used_schema = False

        usage = resp.get("usage") or {}
        return ChatResult(
            data=data if isinstance(data, dict) else {"value": data},
            usage=usage,
            model=resp.get("model") or model_id,
            raw_content=content,
            used_json_schema=used_schema,
        )

    def _post(self, payload: dict, *, timeout: float = 120.0) -> dict:
        """POST chat completions; retry up to 3 times on HTTP 429."""
        last_exc: BaseException | None = None
        for attempt in range(_MAX_429_RETRIES + 1):
            try:
                if self._transport is not None:
                    return self._transport(payload)
                self._wait_for_rate_limit()
                r = requests.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                if r.status_code == 429:
                    if attempt >= _MAX_429_RETRIES:
                        r.raise_for_status()
                    self.retries_used += 1
                    time.sleep(_429_backoff_seconds(r, attempt))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as exc:
                if (
                    exc.response is not None
                    and exc.response.status_code == 429
                    and attempt < _MAX_429_RETRIES
                ):
                    self.retries_used += 1
                    time.sleep(_429_backoff_seconds(exc.response, attempt))
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _wait_for_rate_limit(self) -> None:
        if self._min_request_interval_s <= 0:
            return
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_s = self._min_request_interval_s - elapsed
            if wait_s > 0:
                time.sleep(wait_s)
            self._last_request_at = time.monotonic()


def _429_backoff_seconds(response: requests.Response, attempt: int) -> float:
    """Honor Retry-After when present; else 2/4/8s with +/-25% jitter."""
    header = response.headers.get("Retry-After") if response is not None else None
    if header is not None:
        try:
            return max(0.0, float(header))
        except (TypeError, ValueError):
            pass
    idx = min(attempt, len(_DEFAULT_429_BACKOFF_S) - 1)
    base = _DEFAULT_429_BACKOFF_S[idx]
    jitter = 1.0 + random.uniform(-_429_JITTER, _429_JITTER)
    return max(0.0, base * jitter)


def _message_content(resp: dict) -> str:
    choices = resp.get("choices") or []
    if not choices:
        raise ValueError("chat: empty choices in response")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        # Some responses return content parts.
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text") or "")
        content = "".join(parts)
    if not isinstance(content, str):
        raise ValueError("chat: missing message content")
    content = content.strip()
    if content.startswith("```"):
        # Strip optional markdown fences.
        lines = content.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def estimate_chat_usd(
    usage: dict,
    *,
    input_usd_per_mtok: float,
    output_usd_per_mtok: float,
) -> float:
    """Estimate chat USD from usage token counts."""
    prompt = float(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = float(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    return round(
        (prompt / 1_000_000.0) * input_usd_per_mtok
        + (completion / 1_000_000.0) * output_usd_per_mtok,
        6,
    )
