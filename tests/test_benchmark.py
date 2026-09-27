"""Tests for the benchmark harness (provider-agnostic)."""

from __future__ import annotations

from pdfsplit.benchmark import run_benchmark
from pdfsplit.corpus import build_corpus
from pdfsplit.providers.mock import MockSplitterProvider
from pdfsplit.schema import SplitDocument


def test_run_benchmark_with_mock_provider(tmp_path):
    corpus_dir = tmp_path / "corpus"
    build_corpus(corpus_dir, seed=42)
    provider = MockSplitterProvider(
        documents=[
            SplitDocument(doc_type="invoice", start_page=1, end_page=4, confidence=0.9),
            SplitDocument(
                doc_type="bank_statement", start_page=5, end_page=7, confidence=0.9
            ),
        ]
    )
    out_dir = tmp_path / "out"
    report = run_benchmark(provider, corpus_dir, out_dir, limit=2)
    assert report.provider == "mock"
    assert report.model_version == "canned-mock"
    assert len(report.runs) == 2
    # Report JSON written.
    report_json = out_dir / f"{report.run_id}.json"
    assert report_json.exists()
    import json

    data = json.loads(report_json.read_text())
    assert data["provider"] == "mock"
    assert "summary" in data


def test_mock_provider_not_evidential():
    from pdfsplit.providers.mock import MockSplitterProvider

    p = MockSplitterProvider(documents=[])
    assert p.model_version.startswith("canned-")


def test_run_records_corpus_version(tmp_path):
    import json

    corpus_dir = tmp_path / "corpus"
    build_corpus(corpus_dir, seed=42)
    provider = MockSplitterProvider(documents=[])
    out_dir = tmp_path / "out"
    report = run_benchmark(provider, corpus_dir, out_dir, limit=1)
    data = json.loads((out_dir / f"{report.run_id}.json").read_text())
    assert data["corpus_version"] == "2026-08-12-boundary"
