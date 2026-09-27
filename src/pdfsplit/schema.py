"""Normalized provider contract.

This is the seam between the provider (Mistral, mock, ...) and
everything downstream (scoring, review UI). Page numbers are 1-indexed inclusive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SplitDocument(BaseModel):
    """One logical document predicted inside an input PDF."""

    doc_type: str = Field(description="Class label, or 'unknown' if unmappable.")
    start_page: int = Field(
        ge=1, description="First physical page (1-indexed, inclusive)."
    )
    end_page: int = Field(
        ge=1, description="Last physical page (1-indexed, inclusive)."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Per-document confidence.")
    text: str | None = Field(
        default=None, description="OCR/text if the provider returns it."
    )


class SplitResult(BaseModel):
    """Normalized output of splitting a single input document."""

    input_ref: str = Field(
        description="Human/source identifier of the input (path or id)."
    )
    provider: str = Field(description="Provider name, e.g. 'google-documentai'.")
    model_version: str = Field(description="Exact model/version string used.")
    documents: list[SplitDocument] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0, description="End-to-end provider latency.")
    cost_usd: float = Field(default=0.0, description="Estimated/actual cost.")
    raw_response_path: str | None = Field(
        default=None, description="Path to saved raw provider response."
    )
    errors: list[str] = Field(default_factory=list, description="Error/warning codes.")
    timeout: bool = Field(default=False)

    @property
    def pages(self) -> int:
        if not self.documents:
            return 0
        return max(d.end_page for d in self.documents)


ProviderStatus = Literal["ready", "missing_credentials"]
