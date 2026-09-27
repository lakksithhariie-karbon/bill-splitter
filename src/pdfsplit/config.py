"""Environment configuration.

Never hardcode credentials. All settings come from the environment
(.env at project root, or exported vars).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore[assignment]
except ImportError:  # pragma: no cover - dotenv always installed
    load_dotenv = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """Validated, env-backed configuration for the POC."""

    # OpenRouter chat API key. Read from the local environment only.
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )
    openrouter_model: str = field(
        default_factory=lambda: os.getenv(
            "OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it"
        )
    )
    openrouter_max_images_per_request: int = field(
        default_factory=lambda: min(
            3, max(1, int(os.getenv("OPENROUTER_MAX_IMAGES_PER_REQUEST", "3")))
        )
    )
    openrouter_ocr_max_completion_tokens: int = field(
        default_factory=lambda: min(
            900,
            max(64, int(os.getenv("OPENROUTER_OCR_MAX_COMPLETION_TOKENS", "220"))),
        )
    )
    openrouter_max_completion_tokens: int = field(
        default_factory=lambda: min(
            900, max(128, int(os.getenv("OPENROUTER_MAX_COMPLETION_TOKENS", "900")))
        )
    )
    openrouter_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("OPENROUTER_TIMEOUT_S", "120"))
    )
    openrouter_min_request_interval_s: float = field(
        default_factory=lambda: max(
            0.0, float(os.getenv("OPENROUTER_MIN_REQUEST_INTERVAL_S", "0"))
        )
    )

    # Mistral OCR API key (Bearer token). Read from environment only.
    mistral_api_key: str = field(default_factory=lambda: os.getenv("MISTRAL_API_KEY", ""))
    # Mistral OCR per-page USD rate. OCR 4 is $4/1000 pages; Document AI
    # (annotated) is $5/1000 pages. Used to convert pages_processed -> cost.
    mistral_ocr_usd_per_page: float = field(
        default_factory=lambda: float(os.getenv("MISTRAL_OCR_USD_PER_PAGE", "0.004"))
    )
    mistral_docai_usd_per_page: float = field(
        default_factory=lambda: float(os.getenv("MISTRAL_DOCAI_USD_PER_PAGE", "0.005"))
    )
    # Extraction mode: "chat" (default) or "annotated" (legacy single-call).
    mistral_extraction_mode: str = field(
        default_factory=lambda: os.getenv("MISTRAL_EXTRACTION_MODE", "chat").strip().lower()
        or "chat"
    )
    mistral_chat_model: str = field(
        default_factory=lambda: os.getenv("MISTRAL_CHAT_MODEL", "mistral-small-latest")
    )
    mistral_chat_concurrency: int = field(
        default_factory=lambda: int(os.getenv("MISTRAL_CHAT_CONCURRENCY", "4"))
    )
    mistral_chat_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("MISTRAL_CHAT_TIMEOUT_S", "120"))
    )
    # Chat token rates for cost estimates (Small defaults; override via env).
    mistral_chat_input_usd_per_mtok: float = field(
        default_factory=lambda: float(
            os.getenv("MISTRAL_CHAT_INPUT_USD_PER_MTOK", "0.15")
        )
    )
    mistral_chat_output_usd_per_mtok: float = field(
        default_factory=lambda: float(
            os.getenv("MISTRAL_CHAT_OUTPUT_USD_PER_MTOK", "0.6")
        )
    )
    # Downstream AP hard page cap (production review-voucher limit is 12).
    max_downstream_pages: int = field(
        default_factory=lambda: int(os.getenv("MAX_DOWNSTREAM_PAGES", "12"))
    )
    # Upload hard cap so a single request cannot run away on OCR cost.
    max_upload_pages: int = field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_PAGES", "100"))
    )
    # AIA AP integration. NO production default for the base URL.
    # Live HTTP requires BOTH aia_api_base (explicit) AND aia_allow_writes=true.
    aia_api_base: str = field(
        default_factory=lambda: (os.getenv("AIA_API_BASE") or "").strip()
    )
    aia_allow_writes: bool = field(
        default_factory=lambda: (os.getenv("AIA_ALLOW_WRITES") or "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    # Bounded concurrency for Mode A push (upload + poll). Uploads are
    # sub-second; the cap mainly avoids flooding a live tenant.
    aia_push_concurrency: int = field(
        default_factory=lambda: max(
            1, int(os.getenv("AIA_PUSH_CONCURRENCY", "4") or "4")
        )
    )
    output_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("OUTPUT_DIR")
            or os.getenv("PDFSPLIT_OUTPUT_DIR")
            or os.getenv("DOCAI_OUTPUT_DIR")
            or str(PROJECT_ROOT / "output")
        )
    )
    corpus_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("PDFSPLIT_CORPUS_DIR")
            or os.getenv("DOCAI_CORPUS_DIR")
            or str(PROJECT_ROOT / "data" / "corpus")
        )
    )

    def has_mistral_credentials(self) -> bool:
        return bool(self.mistral_api_key)

    def has_openrouter_credentials(self) -> bool:
        return bool(self.openrouter_api_key)


settings = Settings()
