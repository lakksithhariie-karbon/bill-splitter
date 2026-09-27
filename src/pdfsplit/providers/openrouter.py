"""OpenRouter model adapter used by the local, download-first web app.

Embedded PDF text is read locally. Only pages without readable text are
rasterized and sent to OpenRouter's OpenAI-compatible vision chat endpoint. Both
paths are normalized for the shared boundary-inference pipeline.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import requests
from PIL import Image

from pdfsplit.app.raster import _RENDER_LOCK
from pdfsplit.chat import ChatClient
from pdfsplit.config import Settings
from pdfsplit.config import settings as default_settings
from pdfsplit.providers.mistral import MAX_BYTES, MistralSplitterProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api"
_MAX_IMAGE_SIDE = 1600
_JPEG_QUALITY = 82

_PAGE_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "markdown": {"type": "string"},
                },
                "required": ["page", "markdown"],
            },
        }
    },
    "required": ["pages"],
}

_OCR_SYSTEM = (
    "You inspect bill and invoice pages to help split a packet. Read only "
    "visible text. Return concise page notes containing the supplier/header, "
    "document type, bill/reference number, date, total, and any continuation, "
    "annexure, or terms clue. Copy identifiers and amounts exactly. Do not "
    "transcribe line items or add explanations. Return JSON in the requested "
    "shape."
)


class OpenRouterSplitterProvider(MistralSplitterProvider):
    """Use OpenRouter chat for splits and vision only for image-only PDF pages."""

    name = "openrouter"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        chat_client: ChatClient | None = None,
        ocr_response: dict | None = None,
    ) -> None:
        openrouter_settings = settings or default_settings
        effective_settings = replace(
            openrouter_settings,
            # The shared Mistral pipeline reads these compatibility fields for
            # the chat model and its existing credential guard.
            mistral_api_key=openrouter_settings.openrouter_api_key,
            mistral_chat_model=openrouter_settings.openrouter_model,
            mistral_chat_timeout_s=openrouter_settings.openrouter_timeout_s,
            mistral_chat_concurrency=1,
        )
        client = chat_client or ChatClient(
            settings=effective_settings,
            base_url=OPENROUTER_BASE_URL,
            api_key=openrouter_settings.openrouter_api_key,
            json_mode=True,
            min_request_interval_s=openrouter_settings.openrouter_min_request_interval_s,
            max_completion_tokens=openrouter_settings.openrouter_max_completion_tokens,
        )
        super().__init__(
            settings=effective_settings,
            chat_client=client,
            ocr_response=ocr_response,
        )
        self.openrouter_settings = openrouter_settings
        self.model_version = f"openrouter-{openrouter_settings.openrouter_model}"

    def analyze(self, input_ref: str | Path | bytes) -> dict[str, Any]:
        if (
            not self.openrouter_settings.has_openrouter_credentials()
            and self._ocr_response is None
        ):
            raise ValueError(
                "openrouter: OPENROUTER_API_KEY is not set. Add it to the local "
                ".env file "
                "to analyze uploaded PDFs."
            )
        return super().analyze(input_ref)

    @staticmethod
    def _check_limits(data: bytes, name: str) -> None:
        if len(data) > MAX_BYTES:
            raise ValueError(
                f"openrouter: {name} exceeds the 50 MB input limit for this tool."
            )

    def _ocr(self, data: bytes, include_blocks: bool, annotate: bool) -> dict:
        del include_blocks, annotate  # OpenRouter returns the normalized text form.
        prompt_tokens = 0
        completion_tokens = 0
        batch_size = min(
            3, max(1, self.openrouter_settings.openrouter_max_images_per_request)
        )

        try:
            native_text = self._extract_native_text(data)
            page_count = len(native_text)
            if page_count > self.openrouter_settings.max_upload_pages:
                raise ValueError(
                    f"openrouter: PDF has {page_count} pages; the limit is "
                    f"{self.openrouter_settings.max_upload_pages}."
                )

            pages = [
                self._normalize_native_page(i + 1, text)
                for i, text in enumerate(native_text)
            ]
            vision_indices = [
                i for i, text in enumerate(native_text) if len(text.strip()) < 80
            ]

            for batch_start in range(0, len(vision_indices), batch_size):
                page_indices = vision_indices[batch_start : batch_start + batch_size]
                image_parts = self._render_pages(data, page_indices)
                page_numbers = [index + 1 for index in page_indices]
                prompt = (
                    "For each supplied image, return one concise note. The "
                    f"images correspond in order to PDF pages {page_numbers}. "
                    "Return JSON as {\"pages\":[{\"page\": number, "
                    "\"markdown\": \"supplier/header; document type; "
                    "reference; date; total; continuation clue\"}]}. Include "
                    "exactly one entry per image. Copy only visible identity "
                    "clues, use at most four short lines per page, and do not "
                    "transcribe line items. Use an empty markdown string if "
                    "the page has no legible text."
                )
                content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                for page_number, jpeg in zip(page_numbers, image_parts):
                    data_url = "data:image/jpeg;base64," + base64.b64encode(
                        jpeg
                    ).decode("ascii")
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    )

                result = (self._chat_client or ChatClient(settings=self.settings)).complete_json(
                    messages=[
                        {"role": "system", "content": _OCR_SYSTEM},
                        {"role": "user", "content": content},
                    ],
                    json_schema=_PAGE_OCR_SCHEMA,
                    schema_name="page_transcriptions",
                    model=self.openrouter_settings.openrouter_model,
                    timeout=self.openrouter_settings.openrouter_timeout_s,
                    max_completion_tokens=(
                        self.openrouter_settings.openrouter_ocr_max_completion_tokens
                    ),
                )
                prompt_tokens += int(
                    result.usage.get("prompt_tokens")
                    or result.usage.get("input_tokens")
                    or 0
                )
                completion_tokens += int(
                    result.usage.get("completion_tokens")
                    or result.usage.get("output_tokens")
                    or 0
                )
                try:
                    normalized = self._normalize_pages(result.data, page_numbers)
                except ValueError as exc:
                    raise ValueError(
                        "OpenRouter vision returned incomplete notes for scanned "
                        "pages. Please retry."
                    ) from exc
                for page in normalized:
                    pages[page["index"]] = page
        except requests.HTTPError as exc:
            response = exc.response
            status = getattr(response, "status_code", None)
            detail = (getattr(response, "text", "") or "").strip()[:200]
            if status == 429:
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                hint = (
                    f" Retry after {retry_after} seconds."
                    if retry_after
                    else " Wait briefly, then retry."
                )
                raise ValueError(
                    "OpenRouter vision failed: HTTP 429 — the OpenRouter account "
                    "rate "
                    "limit was reached." + hint
                ) from exc
            raise ValueError(
                "OpenRouter vision failed"
                + (f": HTTP {status}" if status else "")
                + (f" — {detail}" if detail else "")
            ) from exc
        except requests.RequestException as exc:
            raise ValueError(
                "OpenRouter vision failed while contacting the inference service. "
                "Please try again."
            ) from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(
                "OpenRouter vision returned an unreadable page transcription. "
                "Please try again."
            ) from exc

        return {
            "model": self.openrouter_settings.openrouter_model,
            "pages": pages,
            "usage_info": {
                "pages_processed": len(pages),
                "native_text_pages": page_count - len(vision_indices),
                "vision_pages_processed": len(vision_indices),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }

    @staticmethod
    def _extract_native_text(data: bytes) -> list[str]:
        """Read embedded PDF text; scanned pages are handled by vision OCR."""
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        output: list[str] = []
        for page in reader.pages:
            try:
                output.append((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001 — a damaged page can use vision
                output.append("")
        return output

    @staticmethod
    def _normalize_native_page(page_number: int, text: str) -> dict[str, Any]:
        """Keep identity clues at the top and totals at the tail for judging."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = lines[0] if lines else ""
        markdown = text
        if len(markdown) > 520:
            markdown = markdown[:360].rstrip() + "\n…\n" + markdown[-140:].lstrip()
        blocks = (
            [{"type": "title", "top_left_y": 100, "content": title[:240]}]
            if title
            else []
        )
        return {
            "index": page_number - 1,
            "markdown": markdown,
            "blocks": blocks,
            "confidence_scores": {"average_page_confidence_score": 0.0},
        }

    @staticmethod
    def _page_count(data: bytes) -> int:
        with _RENDER_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                return len(pdf)
            finally:
                pdf.close()

    @staticmethod
    def _render_pages(data: bytes, page_indices: list[int]) -> list[bytes]:
        """Render one small image batch while serializing PDFium access."""
        output: list[bytes] = []
        with _RENDER_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                for page_index in page_indices:
                    bitmap = pdf[page_index].render(scale=120 / 72)
                    image = bitmap.to_pil().convert("RGB")
                    image.thumbnail(
                        (_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE),
                        Image.Resampling.LANCZOS,
                    )
                    buffer = io.BytesIO()
                    image.save(
                        buffer,
                        format="JPEG",
                        quality=_JPEG_QUALITY,
                        optimize=True,
                    )
                    output.append(buffer.getvalue())
            finally:
                pdf.close()
        return output

    @staticmethod
    def _normalize_pages(
        data: dict, expected_pages: list[int]
    ) -> list[dict[str, Any]]:
        raw_pages = data.get("pages")
        if not isinstance(raw_pages, list):
            raise ValueError("Missing pages array")
        by_number: dict[int, dict] = {}
        for raw in raw_pages:
            if not isinstance(raw, dict):
                continue
            try:
                number = int(raw.get("page"))
            except (TypeError, ValueError):
                continue
            by_number[number] = raw

        normalized: list[dict[str, Any]] = []
        for page_number in expected_pages:
            raw = by_number.get(page_number)
            if raw is None:
                raise ValueError(f"Missing transcription for page {page_number}")
            markdown = raw.get("markdown")
            if not isinstance(markdown, str):
                raise ValueError(f"Invalid transcription for page {page_number}")
            title = raw.get("title")
            if not isinstance(title, str):
                title = ""
            if not title.strip():
                title = next(
                    (
                        re.sub(r"^#+\s*", "", line).strip()
                        for line in markdown.splitlines()
                        if line.strip()
                    ),
                    "",
                )
            blocks = (
                [{"type": "title", "top_left_y": 100, "content": title[:240]}]
                if title.strip()
                else []
            )
            normalized.append(
                {
                    "index": page_number - 1,
                    "markdown": markdown,
                    "blocks": blocks,
                    "confidence_scores": {
                        "average_page_confidence_score": 0.0
                    },
                }
            )
        return normalized
