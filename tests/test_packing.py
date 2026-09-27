"""Offline tests for adaptive <=N packing, duplicate flags, and save-batched."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from tests.conftest import authed_client
from pypdf import PdfReader
from reportlab.pdfgen import canvas as rl_canvas

from pdfsplit.app import main
from pdfsplit.app.main import app
from pdfsplit.packing import (
    build_pack_manifest,
    doc_ranges_from_split_documents,
    find_duplicate_doc_groups,
    pack_documents,
)
from pdfsplit.schema import SplitDocument


def _ranges(*spans: tuple[int, int]) -> list[dict]:
    return [
        {
            "doc_id": f"doc-{start}-{end}",
            "page_start": start,
            "page_end": end,
            "doc_type": "invoice",
        }
        for start, end in spans
    ]


def _assert_invariants(batches, page_count, max_pages):
    covered = []
    for batch in batches:
        size = batch["page_end"] - batch["page_start"] + 1
        assert size <= max_pages, batch
        for p in range(batch["page_start"], batch["page_end"] + 1):
            covered.append(p)
    assert sorted(covered) == list(range(1, page_count + 1))


def _make_pdf(path: Path, pages: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(path))
    for i in range(pages):
        c.drawString(72, 720, f"Packing fixture page {i + 1}")
        c.showPage()
    c.save()
    return path


# Synthetic packing scenarios (page_count, ranges) — formerly corpus pkt_013–017.
PACK_CASES = {
    "straddle_12": (17, _ranges((1, 10), (11, 13), (14, 17))),
    "oversized_13": (13, _ranges((1, 13))),
    "exactly_12": (12, _ranges((1, 12))),
    "continuation": (15, _ranges((1, 5), (6, 10), (11, 15))),
    "uneven_pack": (20, _ranges((1, 8), (9, 12), (13, 20))),
}


@pytest.mark.parametrize("case_id", list(PACK_CASES))
def test_pack_invariants_on_corpus_packets(case_id):
    page_count, ranges = PACK_CASES[case_id]
    batches = pack_documents(page_count, ranges, max_pages=12)
    _assert_invariants(batches, page_count, 12)


def test_pack_straddle_keeps_three_page_doc_whole():
    page_count, ranges = PACK_CASES["straddle_12"]
    batches = pack_documents(page_count, ranges, 12)
    # Blind cut at 12 would slice 11-13; packing puts 1-10 then 11-17.
    assert batches[0]["page_start"] == 1 and batches[0]["page_end"] == 10
    assert any(
        d["page_start"] == 11 and d["page_end"] == 13 for d in batches[1]["documents"]
    )
    assert all(d["page_end"] - d["page_start"] + 1 <= 12 for b in batches for d in [b])


def test_pack_oversized_flags_parts():
    page_count, ranges = PACK_CASES["oversized_13"]
    batches = pack_documents(page_count, ranges, 12)
    assert len(batches) == 2
    assert batches[0]["page_end"] - batches[0]["page_start"] + 1 == 12
    assert batches[1]["page_end"] - batches[1]["page_start"] + 1 == 1
    for batch in batches:
        for doc in batch["documents"]:
            assert doc["oversized"] is True
            assert doc["parts"] == 2
            assert doc["doc_id"] == "doc-1-13"


def test_pack_exactly_n_single_batch():
    page_count, ranges = PACK_CASES["exactly_12"]
    batches = pack_documents(page_count, ranges, 12)
    assert len(batches) == 1
    assert batches[0]["documents"][0]["oversized"] is False
    assert batches[0]["page_start"] == 1 and batches[0]["page_end"] == 12


def test_pack_uneven_nonuniform_batch_sizes():
    page_count, ranges = PACK_CASES["uneven_pack"]
    batches = pack_documents(page_count, ranges, 12)
    sizes = [b["page_end"] - b["page_start"] + 1 for b in batches]
    assert sizes[0] == 12
    assert sizes[1] == 8
    assert len(set(sizes)) > 1


def test_duplicate_doc_number_grouping():
    docs = [
        {
            "page_start": 1,
            "page_end": 2,
            "document_id": {"value": "INV-100", "source_text": "INV-100", "pages": [1]},
        },
        {
            "page_start": 3,
            "page_end": 4,
            "document_id": {"value": "INV-200", "source_text": "INV-200", "pages": [3]},
        },
        {
            "page_start": 5,
            "page_end": 6,
            "document_id": {"value": " inv-100 ", "source_text": "INV-100", "pages": [5]},
        },
        {
            "page_start": 7,
            "page_end": 7,
            "document_id": {"value": "", "source_text": "", "pages": []},
        },
    ]
    errors, groups = find_duplicate_doc_groups(docs)
    assert errors == ["duplicate_doc_number:INV-100"]
    assert len(groups) == 1
    assert groups[0] == [
        {"page_start": 1, "page_end": 2},
        {"page_start": 5, "page_end": 6},
    ]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    main.store = main.SessionStore(tmp_path / "sessions")

    fixture = (
        Path(__file__).resolve().parents[1]
        / "vendors"
        / "mistral"
        / "fixtures"
        / "pkt_001_annotated.json"
    )

    def _build():
        from pdfsplit.providers.mistral import MistralSplitterProvider

        return MistralSplitterProvider(annotated_fixture_path=fixture)

    monkeypatch.setattr(main, "_build_provider", _build)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    return authed_client(app)


def test_save_batched_zip_manifest_and_invariants(client, tmp_path):
    """Inject a synthetic split covering a straddle packet and verify zip/manifest."""
    page_count, ranges = PACK_CASES["straddle_12"]
    pdf_path = _make_pdf(tmp_path / "straddle.pdf", page_count)
    with open(pdf_path, "rb") as fh:
        up = client.post(
            "/api/upload",
            files={"file": ("straddle.pdf", fh, "application/pdf")},
        )
    assert up.status_code == 200
    sid = up.json()["session_id"]

    from pdfsplit.schema import SplitResult

    session = main.store.get(sid)
    assert session is not None
    docs = [
        SplitDocument(
            doc_type=r["doc_type"],
            start_page=r["page_start"],
            end_page=r["page_end"],
            confidence=1.0,
        )
        for r in ranges
    ]
    result = SplitResult(
        input_ref=str(session.pdf_path),
        provider="test",
        model_version="test",
        documents=docs,
        latency_ms=0.0,
        cost_usd=0.0,
    )
    main.store.save_split(session, result)

    resp = client.post(f"/api/session/{sid}/save-batched?max_pages=12")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "manifest.json" in names
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["packet_pages"] == page_count
    assert manifest["max_pages"] == 12
    assert manifest["duplicate_groups"] == []

    covered = []
    reassembled = []
    for batch in manifest["batches"]:
        assert batch["file"] in names
        size = batch["page_end"] - batch["page_start"] + 1
        assert size <= 12
        with zf.open(batch["file"]) as fh:
            n = len(PdfReader(io.BytesIO(fh.read())).pages)
        assert n == size
        for p in range(batch["page_start"], batch["page_end"] + 1):
            covered.append(p)
        for d in batch["documents"]:
            reassembled.append((d["doc_id"], d["page_start"], d["page_end"]))

    assert sorted(covered) == list(range(1, page_count + 1))
    expected = [
        (r["doc_id"], r["page_start"], r["page_end"]) for r in ranges
    ]
    assert reassembled == expected