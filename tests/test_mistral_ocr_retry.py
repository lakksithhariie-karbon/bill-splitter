"""Mistral OCR retries transient 5xx instead of raw HTTPError."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pdfsplit.config import Settings
from pdfsplit.providers.mistral import MistralSplitterProvider


def _resp(status: int, text: str = "", url: str = "https://api.mistral.ai/v1/ocr"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.url = url
    r.json.return_value = {"pages": [], "usage_info": {"pages_processed": 0}}
    return r


def test_ocr_retries_then_succeeds():
    settings = Settings(mistral_api_key="test-key")
    provider = MistralSplitterProvider(settings=settings)
    with patch("pdfsplit.providers.mistral.requests.post") as post:
        post.side_effect = [_resp(500), _resp(500), _resp(200)]
        with patch("pdfsplit.providers.mistral.time.sleep"):
            out = provider._ocr(b"%PDF-1.4", include_blocks=True, annotate=False)
    assert out["pages"] == []
    assert post.call_count == 3


def test_ocr_exhausted_retries_raises_value_error():
    settings = Settings(mistral_api_key="test-key")
    provider = MistralSplitterProvider(settings=settings)
    with patch("pdfsplit.providers.mistral.requests.post") as post:
        post.side_effect = [_resp(500), _resp(503), _resp(502)]
        with patch("pdfsplit.providers.mistral.time.sleep"):
            with pytest.raises(ValueError, match="mistral OCR failed after retries"):
                provider._ocr(b"%PDF-1.4", include_blocks=True, annotate=False)
    assert post.call_count == 3
