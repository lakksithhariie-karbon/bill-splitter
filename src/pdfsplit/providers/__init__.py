"""Minimal provider abstraction.

Exactly one method + identity attributes. Enough to plug another provider into
the benchmark; not enough to be a framework. See schema.py for the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pdfsplit.schema import SplitResult

InputRef = str | Path | bytes


class SplitterProvider(ABC):
    """Contract every splitter provider must implement."""

    #: Human-readable provider name (e.g. "google-documentai").
    name: str = "base"

    #: Exact model/version string, recorded in every result.
    model_version: str = "unknown"

    @abstractmethod
    def split(self, input_ref: InputRef) -> SplitResult:
        """Split an input PDF into logical documents.

        Accepts a path/identifier or raw bytes. Returns normalized SplitResult.
        """
