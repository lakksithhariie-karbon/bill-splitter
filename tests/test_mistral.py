"""Tests for the Mistral provider (replay) and the extraction/grounding logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdfsplit.extraction import (
    ExtractedDocument,
    LineItem,
    apply_grounding,
)
from pdfsplit.providers.mistral import (
    MistralSplitterProvider,
    find_document_boundaries,
    _normalise_class,
)


@pytest.fixture()
def plain_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "vendors"
        / "mistral"
        / "fixtures"
        / "pkt_001_control_multi_class.json"
    )


@pytest.fixture()
def annotated_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "vendors"
        / "mistral"
        / "fixtures"
        / "pkt_001_annotated.json"
    )


def test_replay_split_requires_no_api_key(plain_fixture, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(fixture_path=plain_fixture)
    result = provider.split("data/corpus/pkt_001_control_multi_class.pdf")
    assert result.provider == "mistral"
    assert result.model_version.startswith("replay-")
    assert "replay_fixture" in result.errors


def test_replay_split_boundaries_and_confidence(plain_fixture, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(fixture_path=plain_fixture)
    result = provider.split("data/corpus/pkt_001_control_multi_class.pdf")
    ranges = [(d.start_page, d.end_page) for d in result.documents]
    assert ranges == [(1, 3), (4, 7), (8, 12)]
    # Mistral returns a real confidence signal; it must not be zeroed.
    assert all(d.confidence > 0.0 for d in result.documents)
    # doc_type normalised to snake_case.
    assert [d.doc_type for d in result.documents] == [
        "invoice",
        "bank_statement",
        "monthly_report",
    ]


def test_replay_split_cost_from_pages(plain_fixture, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(fixture_path=plain_fixture)
    result = provider.split("data/corpus/pkt_001_control_multi_class.pdf")
    # 12 pages @ $0.004/page (plain OCR rate).
    assert result.cost_usd == pytest.approx(12 * 0.004)


def test_replay_extract_returns_fields(annotated_fixture, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(annotated_fixture_path=annotated_fixture)
    result = provider.extract("data/corpus/pkt_001_control_multi_class.pdf")
    assert result.provider == "mistral"
    assert result.model_version.startswith("replay-")
    assert len(result.documents) == 3
    assert result.pages_processed == 12
    # Annotated (Document AI) rate applies.
    assert result.cost_usd == pytest.approx(12 * 0.005)
    # Real confidence, not zeroed.
    assert all(d.confidence > 0.0 for d in result.documents)


def test_replay_extract_grounding(annotated_fixture, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(annotated_fixture_path=annotated_fixture)
    result = provider.extract("data/corpus/pkt_001_control_multi_class.pdf")
    # On pkt_001 the model's line-item descriptions are verbatim page text, so
    # nothing should be flagged UNGROUNDED.
    for d in result.documents:
        assert d.ungrounded == []
        for it in d.line_items:
            assert it.ungrounded == []


def test_live_without_credentials_raises(monkeypatch):
    from dataclasses import replace

    from pdfsplit.config import settings as app_settings

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider = MistralSplitterProvider(settings=replace(app_settings, mistral_api_key=""))
    with pytest.raises(ValueError):
        provider.split("data/corpus/pkt_001_control_multi_class.pdf")
    with pytest.raises(ValueError):
        provider.extract("data/corpus/pkt_001_control_multi_class.pdf")


def test_env_fixture_var_is_ignored(monkeypatch):
    """An env var must NOT silently turn the live app into replay."""
    from dataclasses import replace

    from pdfsplit.config import settings as app_settings

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv(
        "MISTRAL_FIXTURE", "vendors/mistral/fixtures/pkt_001_annotated.json"
    )
    provider = MistralSplitterProvider(settings=replace(app_settings, mistral_api_key=""))
    with pytest.raises(ValueError):
        provider.split("data/corpus/pkt_001_control_multi_class.pdf")


def test_find_document_boundaries_ignores_heading_level(plain_fixture):
    """A heading-level change (# vs ##) must not create a false boundary."""
    data = json.loads(plain_fixture.read_text())
    starts = find_document_boundaries(data["pages"])
    assert starts == [1, 4, 8]


def test_normalise_class():
    assert _normalise_class("# INVOICE | packet x") == "invoice"
    assert _normalise_class("## MONTHLY REPORT") == "monthly_report"
    assert _normalise_class("") == "unknown"


def test_apply_grounding_flags_invented_values():
    docs = [
        ExtractedDocument(
            page_start=1,
            page_end=1,
            doc_type="invoice",
            vendor_name="ACME Corp",
            total_amount="1234.56",
            line_items=[
                LineItem(page=1, description="Widget", amount="999.99"),
            ],
        )
    ]
    page_markdowns = {
        1: "INVOICE\nACME Corp\nWidget\nTotal: 1234.56",
    }
    grounded = apply_grounding(docs, page_markdowns)
    # vendor_name and total_amount are on the page; amount 999.99 is not.
    assert grounded[0].ungrounded == []
    assert grounded[0].line_items[0].ungrounded == ["amount"]


def test_apply_grounding_empty_values_not_flagged():
    docs = [
        ExtractedDocument(
            page_start=1, page_end=1, doc_type="invoice", vendor_name=""
        )
    ]
    grounded = apply_grounding(docs, {1: "INVOICE"})
    # Empty value is "missing", not a hallucination.
    assert grounded[0].ungrounded == []
