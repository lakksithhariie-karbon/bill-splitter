"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Hit live Mistral (costs money). Default e2e is fixture replay.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls Mistral; skipped unless --live")


def authed_client(app) -> TestClient:
    """TestClient helper (name kept for callers). Gate is open when DEMO_TOKEN unset."""
    return TestClient(app)


@pytest.fixture()
def corpus(tmp_path):
    from pdfsplit.corpus import build_corpus

    return build_corpus(tmp_path / "corpus", seed=42)


def make_docs(*specs):
    """specs: list of (type, start, end, conf)."""
    from pdfsplit.schema import SplitDocument

    return [
        SplitDocument(doc_type=t, start_page=s, end_page=e, confidence=c)
        for t, s, e, c in specs
    ]


from pdfsplit.corpus import build_corpus
from pdfsplit.schema import SplitDocument


def authed_client(app) -> TestClient:
    """TestClient helper (name kept for callers). Gate is open when DEMO_TOKEN unset."""
    return TestClient(app)


@pytest.fixture()
def corpus(tmp_path):
    return build_corpus(tmp_path / "corpus", seed=42)


def make_docs(*specs):
    """specs: list of (type, start, end, conf)."""
    return [
        SplitDocument(doc_type=t, start_page=s, end_page=e, confidence=c)
        for t, s, e, c in specs
    ]
