"""Canned provider for local dev/tests.

Explicitly NON-evidential: model_version is prefixed 'canned-' so it can never
be mistaken for a real Google run. Used to exercise the harness without
credentials, and for unit tests. Never use this as proof Google works.
"""

from __future__ import annotations

import json
from pathlib import Path

from pdfsplit.providers import SplitterProvider
from pdfsplit.schema import SplitDocument, SplitResult


class MockSplitterProvider(SplitterProvider):
    """Reads a canned SplitResult JSON file or literal documents."""

    name = "mock"

    def __init__(
        self, documents: list[SplitDocument] | None = None, path: str | None = None
    ):
        if path is not None:
            with open(path) as fh:
                data = json.load(fh)
            self._documents = [SplitDocument(**d) for d in data["documents"]]
            self.model_version = f"canned-{data.get('model_version', 'mock')}"
        else:
            self._documents = documents or []
            self.model_version = "canned-mock"

    def split(self, input_ref: str | Path | bytes) -> SplitResult:
        return SplitResult(
            input_ref=str(input_ref),
            provider=self.name,
            model_version=self.model_version,
            documents=[d.model_copy(deep=True) for d in self._documents],
            latency_ms=0.0,
            errors=[],
        )
