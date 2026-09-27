"""Offline tests for architecture B: chat client, boundaries, grounding v2."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pdfsplit.chat import ChatClient, estimate_chat_usd
from pdfsplit.config import settings as app_settings
from pdfsplit.extraction import (
    ExtractedDocument,
    ExtractionResult,
    FieldValue,
    HEADER_FIELDS_V2,
    LineItemV2,
    apply_grounding_v2,
    document_v2_from_dict,
    to_legacy_headers,
    uncovered_pages,
)
from pdfsplit.providers.mistral import (
    extract_boundary_evidence,
    find_boundaries_with_llm,
    find_document_boundaries,
    page_boundary_summaries,
    parse_boundary_llm_response,
    starts_to_segments,
    MistralSplitterProvider,
)


ROOT = Path(__file__).resolve().parents[1]
DENSE = (
    ROOT
    / "vendors"
    / "mistral"
    / "fixtures"
    / "real_dense_29p_annotated.json"
)
# KEEP: only real OCR fixture in-repo; grounding regressions depend on it.
# Do not delete output/dense_29p_ocr.json — regenerating needs a paid OCR call.
DENSE_OCR = ROOT / "output" / "dense_29p_ocr.json"
DENSE_DOCS_V2 = (
    ROOT / "vendors" / "mistral" / "fixtures" / "dense_29p_documents_v2.json"
)

#: Money FieldValue path suffixes / exact keys used by the recount helper.
_MONEY_HEADER_KEYS = frozenset(
    {
        "totals.subtotal",
        "totals.total",
        "totals.amount_due",
        "totals.tds",
        "totals.other_taxes",
    }
)
_MONEY_TAX_LINE_FIELDS = frozenset({"rate", "base", "amount"})
_MONEY_LINE_FIELDS = frozenset({"quantity", "unit_price", "amount", "discount"})


def _load_ocr_page_markdowns(path: Path = DENSE_OCR) -> dict[int, str]:
    """Map 1-indexed page -> markdown from dense OCR JSON."""
    pages = json.loads(path.read_text())["pages"]
    return {int(p.get("index", i)) + 1: p.get("markdown") or "" for i, p in enumerate(pages)}


def _is_money_path(path: str) -> bool:
    if path in _MONEY_HEADER_KEYS:
        return True
    if path.startswith("totals.tax_lines[") and any(
        path.endswith(f".{f}") for f in _MONEY_TAX_LINE_FIELDS
    ):
        return True
    if path.startswith("line_items[") and any(
        path.endswith(f".{f}") for f in _MONEY_LINE_FIELDS
    ):
        return True
    return False


def recount_tier1_money_ungrounded(
    model_result_path: Path = DENSE_DOCS_V2,
    ocr_path: Path = DENSE_OCR,
) -> list[tuple[int, int, str]]:
    """Re-run apply_grounding_v2; return (doc_idx, page_start, path) money ungrounded."""
    md = _load_ocr_page_markdowns(ocr_path)
    payload = json.loads(model_result_path.read_text())
    raw_docs = payload["documents_v2"] if isinstance(payload, dict) else payload
    hits: list[tuple[int, int, str]] = []
    for i, raw in enumerate(raw_docs):
        doc = document_v2_from_dict(raw, raw["page_start"], raw["page_end"])
        grounded = apply_grounding_v2([doc], md)[0]
        for path in grounded.ungrounded:
            if _is_money_path(path):
                hits.append((i, grounded.page_start, path))
        for li, item in enumerate(grounded.line_items):
            for f in item.ungrounded:
                path = f"line_items[{li}].{f}"
                if _is_money_path(path):
                    hits.append((i, grounded.page_start, path))
    return hits


def test_chat_client_json_schema_request_shape():
    captured = {}

    def transport(payload):
        captured["payload"] = payload
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"ok": True, "n": 1}),
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    client = ChatClient(
        settings=replace(app_settings, mistral_api_key="test-key"),
        transport=transport,
    )
    result = client.complete_json(
        messages=[{"role": "user", "content": "hi"}],
        json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}},
            "required": ["ok", "n"],
        },
        schema_name="probe",
    )
    assert result.data == {"ok": True, "n": 1}
    assert result.used_json_schema is True
    rf = captured["payload"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "probe"
    assert rf["json_schema"]["strict"] is True
    assert captured["payload"]["temperature"] == 0.0


def test_chat_client_repair_retry_on_bad_json():
    calls = {"n": 0}

    def transport(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "model": "mistral-small-latest",
                "choices": [{"message": {"content": "NOT JSON"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        return {
            "model": "mistral-small-latest",
            "choices": [{"message": {"content": '{"fixed": true}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }

    client = ChatClient(transport=transport)
    result = client.complete_json(
        messages=[{"role": "user", "content": "x"}],
        json_schema=None,
    )
    assert result.data == {"fixed": True}
    assert calls["n"] == 2


def test_chat_client_retries_twice_on_429_then_succeeds(monkeypatch):
    """HTTP 429 twice then success; default backoff is 2s then 4s (+/-jitter)."""
    import requests

    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("pdfsplit.chat.time.sleep", fake_sleep)
    monkeypatch.setattr("pdfsplit.chat.random.uniform", lambda a, b: 0.0)

    def transport(payload):
        calls["n"] += 1
        if calls["n"] <= 2:
            resp = requests.Response()
            resp.status_code = 429
            resp._content = b'{"error":"rate"}'
            raise requests.HTTPError("429 Too Many Requests", response=resp)
        return {
            "model": "mistral-small-latest",
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    client = ChatClient(transport=transport)
    result = client.complete_json(
        messages=[{"role": "user", "content": "x"}],
        json_schema=None,
    )
    assert result.data == {"ok": True}
    assert calls["n"] == 3
    assert sleeps == [2.0, 4.0]
    assert client.retries_used == 2


def test_chat_client_raises_after_four_429s(monkeypatch):
    import requests

    monkeypatch.setattr("pdfsplit.chat.time.sleep", lambda s: None)
    monkeypatch.setattr("pdfsplit.chat.random.uniform", lambda a, b: 0.0)
    calls = {"n": 0}

    def transport(payload):
        calls["n"] += 1
        resp = requests.Response()
        resp.status_code = 429
        resp._content = b"{}"
        raise requests.HTTPError("429", response=resp)

    with pytest.raises(requests.HTTPError):
        ChatClient(transport=transport).complete_json(
            messages=[{"role": "user", "content": "x"}],
            json_schema=None,
        )
    assert calls["n"] == 4


def test_chat_client_honors_retry_after_on_429(monkeypatch):
    import requests

    sleeps: list[float] = []
    monkeypatch.setattr("pdfsplit.chat.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def transport(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            resp = requests.Response()
            resp.status_code = 429
            resp.headers["Retry-After"] = "7"
            resp._content = b"{}"
            raise requests.HTTPError("429", response=resp)
        return {
            "model": "mistral-small-latest",
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {},
        }

    result = ChatClient(transport=transport).complete_json(
        messages=[{"role": "user", "content": "x"}],
        json_schema=None,
    )
    assert result.data["ok"] is True
    assert sleeps == [7.0]


def test_estimate_chat_usd():
    usd = estimate_chat_usd(
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        input_usd_per_mtok=0.15,
        output_usd_per_mtok=0.6,
    )
    assert usd == pytest.approx(0.75)


def test_parse_boundary_llm_and_fallback():
    starts, types, naming, just = parse_boundary_llm_response(
        {
            "segments": [
                {
                    "page_start": 1,
                    "page_end": 1,
                    "doc_type": "invoice",
                    "vendor": "Acme",
                    "document_number": "INV-1",
                },
                {"page_start": 2, "page_end": 2, "doc_type": "tax_invoice"},
            ]
        },
        n_pages=2,
        heuristic_starts=[1],
    )
    assert starts == [1, 2]
    assert types[2] == "tax_invoice"
    assert naming[1]["vendor"] == "Acme"
    assert naming[1]["document_number"] == "INV-1"
    assert just == {}
    with pytest.raises(ValueError):
        parse_boundary_llm_response({"segments": []}, 2, [1])


def test_boundary_summaries_carry_distinct_kudos_doc_numbers():
    """Dense fixture pages 2-5 must expose distinct invoice-number evidence."""
    pages = json.loads(DENSE.read_text())["pages"]
    summaries = page_boundary_summaries(pages)
    by_page = {s["page"]: s for s in summaries}
    nums = [by_page[p]["doc_number"] for p in (2, 3, 4, 5)]
    assert all(n for n in nums), nums
    assert len(set(nums)) == 4
    assert "KFIPL/November20/04" in nums[0]
    assert "KFIPL/November20/05" in nums[1]
    assert "KFIPL/November20/03" in nums[2]
    assert "KFIPL/November20/0" in nums[3]
    # Dup tail pages also carry evidence (for self-heal / boundary).
    for p in (26, 27, 28, 29):
        assert by_page[p]["doc_number"]
        assert by_page[p]["date_line"]
        assert by_page[p]["total_line"]


def test_parse_multi_doc_segment_response():
    """Extraction replies may return documents[] for one segment."""
    from pdfsplit.extraction import document_v2_from_dict

    payload = {
        "documents": [
            {
                "page_start": 2,
                "page_end": 2,
                "doc_type": "tax_invoice",
                "document_id": {
                    "value": "A",
                    "source_text": "Invoice No.: A",
                    "pages": [2],
                },
            },
            {
                "page_start": 3,
                "page_end": 3,
                "doc_type": "tax_invoice",
                "document_id": {
                    "value": "B",
                    "source_text": "Invoice No.: B",
                    "pages": [3],
                },
            },
        ]
    }
    docs = [
        document_v2_from_dict(raw, 2, 5) for raw in payload["documents"]
    ]
    assert [d.page_start for d in docs] == [2, 3]
    assert docs[0].document_id.value == "A"
    assert docs[1].document_id.value == "B"


def test_boundary_llm_fallback_on_bad_response():
    pages = json.loads(DENSE.read_text())["pages"][:5]

    def transport(_req):
        return {
            "model": "x",
            "choices": [{"message": {"content": '{"segments": []}'}}],
            "usage": {},
        }

    segments, errors = find_boundaries_with_llm(
        pages, chat=ChatClient(transport=transport)
    )
    assert "boundary_llm_fallback" in errors
    assert segments[0]["page_start"] == 1


def test_grounding_v2_source_text_vs_value():
    """CHECK A uses source_text; invented source is ungrounded."""
    doc = document_v2_from_dict(
        {
            "doc_type": "invoice",
            "document_id": {
                "value": "KFIPL/November20/04",
                "source_text": "Invoice No.: KFIPL/November20/04",
                "pages": [1],
            },
            "seller": {
                "name": {
                    "value": "Invented Corp",
                    "source_text": "Invented Corp",
                    "pages": [1],
                }
            },
            "line_items": [
                {
                    "page": 1,
                    "description": {
                        "value": "Fee",
                        "source_text": "Consulting Fee",
                        "pages": [1],
                    },
                    "quantity": {"value": "1", "source_text": "1", "pages": [1]},
                    "unit_price": {"value": "", "source_text": "", "pages": [1]},
                    "amount": {"value": "10", "source_text": "10.00", "pages": [1]},
                }
            ],
        },
        1,
        1,
    )
    md = {
        1: "Invoice No.: KFIPL/November20/04\nConsulting Fee\n1\n10.00"
    }
    grounded = apply_grounding_v2([doc], md)[0]
    # source_text is on the page → document_id OK (CHECK A).
    assert "document_id" not in grounded.ungrounded
    # Invented seller name is ungrounded.
    assert "seller.name" in grounded.ungrounded
    # Empty unit_price is never ungrounded.
    assert "unit_price" not in grounded.line_items[0].ungrounded
    legacy = to_legacy_headers(grounded)
    assert legacy.invoice_number == "KFIPL/November20/04"
    assert "vendor_name" in legacy.ungrounded


def test_grounding_v2_iso_date_value_not_ungrounded():
    """ISO-normalised date value must not UNGROUND when source_text is on page."""
    doc = document_v2_from_dict(
        {
            "doc_type": "invoice",
            "issue_date": {
                "value": "2020-09-27",
                "source_text": "Invoice Date Sep 27, 2020",
                "pages": [1],
            },
            "buyer": {
                "address": {
                    "value": "1 Main St, Suite 100, City",
                    "source_text": "1 Main St Suite 100 City",
                    "pages": [1],
                }
            },
            "totals": {
                "subtotal": {"value": "", "source_text": "", "pages": []},
                "tax_lines": [],
                "total": {
                    "value": "-4999.00",
                    "source_text": "Payments -Rs.4,999.00",
                    "pages": [1],
                },
                "amount_due": {"value": "", "source_text": "", "pages": []},
            },
        },
        1,
        1,
    )
    md = {
        1: (
            "Invoice Date Sep 27, 2020\n"
            "1 Main St Suite 100 City\n"
            "Payments -Rs.4,999.00\n"
        )
    }
    grounded = apply_grounding_v2([doc], md)[0]
    assert "issue_date" not in grounded.ungrounded
    assert "buyer.address" not in grounded.ungrounded
    assert "totals.total" not in grounded.ungrounded
    assert "issue_date" not in grounded.value_mismatch
    assert "totals.total" not in grounded.value_mismatch


@pytest.mark.skipif(not DENSE_OCR.is_file(), reason="dense OCR fixture missing")
def test_grounding_v2_page8_pipe_table_money_grounds():
    """Markdown table pipes must not false-flag money source_text (Cause 1)."""
    md = _load_ocr_page_markdowns()
    assert "|" in md[8] and "88,500.00" in md[8]
    doc = document_v2_from_dict(
        {
            "doc_type": "tax_invoice",
            "totals": {
                "subtotal": {
                    "value": "75000.00",
                    "source_text": "SUBTOTAL\n75,000.00",
                    "pages": [8],
                },
                "tax_lines": [],
                "total": {
                    "value": "88500.00",
                    "source_text": "TOTAL\n88,500.00",
                    "pages": [8],
                },
                "amount_due": {
                    "value": "88500.00",
                    "source_text": "BALANCE DUE\nINR 88,500.00",
                    "pages": [8],
                },
            },
        },
        8,
        8,
    )
    grounded = apply_grounding_v2([doc], md)[0]
    assert "totals.total" not in grounded.ungrounded
    assert "totals.subtotal" not in grounded.ungrounded
    assert "totals.amount_due" not in grounded.ungrounded


@pytest.mark.skipif(not DENSE_OCR.is_file(), reason="dense OCR fixture missing")
def test_grounding_v2_page17_stitched_header_tier2_only():
    """Stitched source_text → evidence_imprecise, not ungrounded (CHECK C)."""
    md = _load_ocr_page_markdowns()
    doc = document_v2_from_dict(
        {
            "doc_type": "invoice",
            "totals": {
                "subtotal": {"value": "", "source_text": "", "pages": []},
                "tax_lines": [],
                "total": {
                    "value": "2200.00",
                    "source_text": "TOTAL DUE\nINR 2,200.00",
                    "pages": [17],
                },
                "amount_due": {"value": "", "source_text": "", "pages": []},
            },
        },
        17,
        17,
    )
    grounded = apply_grounding_v2([doc], md)[0]
    assert "totals.total" not in grounded.ungrounded
    assert "totals.total" in grounded.evidence_imprecise


def test_grounding_v2_invented_number_still_ungrounded():
    """Genuinely invented money source_text remains CHECK A ungrounded."""
    doc = document_v2_from_dict(
        {
            "doc_type": "invoice",
            "totals": {
                "subtotal": {"value": "", "source_text": "", "pages": []},
                "tax_lines": [],
                "total": {
                    "value": "999999.99",
                    "source_text": "TOTAL\n999999.99",
                    "pages": [1],
                },
                "amount_due": {"value": "", "source_text": "", "pages": []},
            },
        },
        1,
        1,
    )
    md = {1: "| TOTAL | 1,234.00 |\n| BALANCE DUE | INR 1,234.00 |"}
    grounded = apply_grounding_v2([doc], md)[0]
    assert "totals.total" in grounded.ungrounded
    assert "totals.total" not in grounded.evidence_imprecise


def test_grounding_v2_skips_reverse_charge_and_tax_kind():
    """Cause 3: reverse_charge and tax_lines[].kind are never grounded."""
    assert "reverse_charge" not in HEADER_FIELDS_V2
    doc = document_v2_from_dict(
        {
            "doc_type": "invoice",
            "reverse_charge": {
                "value": "Yes",
                "source_text": "Yes",
                "pages": [1],
            },
            "totals": {
                "subtotal": {"value": "", "source_text": "", "pages": []},
                "tax_lines": [
                    {
                        "name": {
                            "value": "CGST",
                            "source_text": "CGST",
                            "pages": [1],
                        },
                        "rate": {
                            "value": "9",
                            "source_text": "9%",
                            "pages": [1],
                        },
                        "base": {
                            "value": "100",
                            "source_text": "100.00",
                            "pages": [1],
                        },
                        "amount": {
                            "value": "9",
                            "source_text": "9.00",
                            "pages": [1],
                        },
                        "kind": {
                            "value": "gst",
                            "source_text": "gst",
                            "pages": [1],
                        },
                    }
                ],
                "total": {"value": "", "source_text": "", "pages": []},
                "amount_due": {"value": "", "source_text": "", "pages": []},
            },
        },
        1,
        1,
    )
    # Page has tax fields but neither "Yes" nor discriminant "gst".
    md = {1: "CGST 9% on 100.00 = 9.00"}
    grounded = apply_grounding_v2([doc], md)[0]
    assert "reverse_charge" not in grounded.ungrounded
    assert "reverse_charge" not in grounded.evidence_imprecise
    assert not any(p.endswith(".kind") for p in grounded.ungrounded)
    assert not any(p.endswith(".kind") for p in grounded.evidence_imprecise)
    # Other tax_line fields still grounded when present.
    assert "totals.tax_lines[0].name" not in grounded.ungrounded
    assert "totals.tax_lines[0].amount" not in grounded.ungrounded


@pytest.mark.skipif(
    not (DENSE_OCR.is_file() and DENSE_DOCS_V2.is_file()),
    reason="dense OCR / documents_v2 fixtures missing",
)
def test_grounding_v2_dense_packet_check_a_near_42():
    """CHECK A over chat docs + dense OCR is near 42, not the old ~195 value-check."""
    md = _load_ocr_page_markdowns()
    raw_docs = json.loads(DENSE_DOCS_V2.read_text())["documents_v2"]
    total = 0
    for raw in raw_docs:
        doc = document_v2_from_dict(raw, raw["page_start"], raw["page_end"])
        grounded = apply_grounding_v2([doc], md)[0]
        total += len(grounded.ungrounded)
        total += sum(len(it.ungrounded) for it in grounded.line_items)
    assert 30 <= total <= 55, f"CHECK A count {total} not near 42"

    hits = recount_tier1_money_ungrounded()
    assert len(hits) < 23
    for _di, _page, path in hits:
        assert _is_money_path(path)


def test_chat_mode_coverage_full_with_mocks():
    """Chat extract with injected OCR + chat covers all pages (no gaps)."""
    ocr = {
        "pages": [
            {
                "index": 0,
                "markdown": "# INVOICE A\nTotal 10",
                "blocks": [{"type": "title", "content": "INVOICE A", "top_left_y": 10}],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            },
            {
                "index": 1,
                "markdown": "# INVOICE B\nTotal 20",
                "blocks": [{"type": "title", "content": "INVOICE B", "top_left_y": 10}],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            },
        ],
        "usage_info": {"pages_processed": 2},
        "model": "mistral-ocr-latest",
    }

    def transport(payload):
        content = payload["messages"][-1]["content"]
        # Boundary call vs extract call.
        if "Per-page summaries" in content or "Heuristic seed" in content:
            body = {
                "segments": [
                    {"page_start": 1, "page_end": 1, "doc_type": "invoice"},
                    {"page_start": 2, "page_end": 2, "doc_type": "invoice"},
                ]
            }
        elif "PAGE 1" in content:
            body = {
                "page_start": 1,
                "page_end": 1,
                "doc_type": "invoice",
                "seller": {
                    "name": {
                        "value": "A",
                        "source_text": "INVOICE A",
                        "pages": [1],
                    },
                    "address": {"value": "", "source_text": "", "pages": []},
                    "tax_id": {"value": "", "source_text": "", "pages": []},
                },
                "buyer": {
                    "name": {"value": "", "source_text": "", "pages": []},
                    "address": {"value": "", "source_text": "", "pages": []},
                    "tax_id": {"value": "", "source_text": "", "pages": []},
                },
                "document_id": {"value": "", "source_text": "", "pages": []},
                "issue_date": {"value": "", "source_text": "", "pages": []},
                "due_date": {"value": "", "source_text": "", "pages": []},
                "currency": {"value": "", "source_text": "", "pages": []},
                "po_number": {"value": "", "source_text": "", "pages": []},
                "payment_terms": {"value": "", "source_text": "", "pages": []},
                "billing_period": {"value": "", "source_text": "", "pages": []},
                "totals": {
                    "subtotal": {"value": "", "source_text": "", "pages": []},
                    "tax_lines": [],
                    "total": {
                        "value": "10",
                        "source_text": "Total 10",
                        "pages": [1],
                    },
                    "amount_due": {"value": "", "source_text": "", "pages": []},
                },
                "bank": None,
                "line_items": [],
                "other_fields": [],
            }
        else:
            body = {
                "page_start": 2,
                "page_end": 2,
                "doc_type": "invoice",
                "seller": {
                    "name": {
                        "value": "B",
                        "source_text": "INVOICE B",
                        "pages": [2],
                    },
                    "address": {"value": "", "source_text": "", "pages": []},
                    "tax_id": {"value": "", "source_text": "", "pages": []},
                },
                "buyer": {
                    "name": {"value": "", "source_text": "", "pages": []},
                    "address": {"value": "", "source_text": "", "pages": []},
                    "tax_id": {"value": "", "source_text": "", "pages": []},
                },
                "document_id": {"value": "", "source_text": "", "pages": []},
                "issue_date": {"value": "", "source_text": "", "pages": []},
                "due_date": {"value": "", "source_text": "", "pages": []},
                "currency": {"value": "", "source_text": "", "pages": []},
                "po_number": {"value": "", "source_text": "", "pages": []},
                "payment_terms": {"value": "", "source_text": "", "pages": []},
                "billing_period": {"value": "", "source_text": "", "pages": []},
                "totals": {
                    "subtotal": {"value": "", "source_text": "", "pages": []},
                    "tax_lines": [],
                    "total": {
                        "value": "20",
                        "source_text": "Total 20",
                        "pages": [2],
                    },
                    "amount_due": {"value": "", "source_text": "", "pages": []},
                },
                "bank": None,
                "line_items": [],
                "other_fields": [],
            }
        if "segments" not in body:
            body = {"documents": [body]}
        return {
            "model": "mistral-small-latest",
            "choices": [{"message": {"content": json.dumps(body)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }

    settings = replace(
        app_settings,
        mistral_extraction_mode="chat",
        mistral_api_key="test",
        mistral_chat_concurrency=2,
    )
    provider = MistralSplitterProvider(
        settings=settings,
        chat_client=ChatClient(settings=settings, transport=transport),
        ocr_response=ocr,
    )
    result = provider.extract(b"%PDF")
    assert uncovered_pages(result.documents, result.pages_processed) == []
    assert not any(e.startswith("uncovered_pages:") for e in result.errors)
    assert len(result.documents) == 2
    assert result.documents_v2
    assert result.cost_breakdown is not None
    assert result.cost_breakdown["ocr_usd"] == pytest.approx(2 * settings.mistral_ocr_usd_per_page)


def test_old_extraction_result_rehydrates():
    """Legacy cache JSON without documents_v2 / cost_breakdown still loads."""
    raw = {
        "provider": "mistral",
        "model_version": "replay-mistral-mistral-ocr-latest",
        "documents": [
            {
                "page_start": 1,
                "page_end": 1,
                "doc_type": "invoice",
                "vendor_name": "X",
                "invoice_number": "1",
                "invoice_date": "",
                "currency": "INR",
                "gstin": "",
                "total_amount": "1",
                "tax_amount": "0",
                "line_items": [],
                "confidence": 0.5,
                "ungrounded": [],
            }
        ],
        "pages_processed": 1,
        "cost_usd": 0.005,
        "raw_response_path": None,
        "errors": ["replay_fixture"],
    }
    result = ExtractionResult(**raw)
    assert result.documents_v2 == []
    assert result.cost_breakdown is None
    assert result.documents[0].vendor_name == "X"
    assert result.prompt_version == ""
    assert result.schema_version == ""


def test_chat_pipeline_json_schema_fallback_is_observable():
    """used_json_schema=False surfaces error codes and persists chat raw paths."""
    from pathlib import Path

    from pdfsplit.chat import ChatResult

    ocr = {
        "pages": [
            {
                "index": 0,
                "markdown": "# INVOICE\nTotal 1",
                "blocks": [
                    {"type": "title", "content": "INVOICE", "top_left_y": 10}
                ],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            }
        ],
        "usage_info": {"pages_processed": 1},
        "model": "mistral-ocr-latest",
    }

    empty_fv = {"value": "", "source_text": "", "pages": []}
    extract_body = {
        "page_start": 1,
        "page_end": 1,
        "doc_type": "invoice",
        "seller": {
            "name": {"value": "X", "source_text": "INVOICE", "pages": [1]},
            "address": empty_fv,
            "tax_id": empty_fv,
        },
        "buyer": {"name": empty_fv, "address": empty_fv, "tax_id": empty_fv},
        "document_id": empty_fv,
        "issue_date": empty_fv,
        "due_date": empty_fv,
        "currency": empty_fv,
        "po_number": empty_fv,
        "payment_terms": empty_fv,
        "billing_period": empty_fv,
        "totals": {
            "subtotal": empty_fv,
            "tax_lines": [],
            "total": {"value": "1", "source_text": "Total 1", "pages": [1]},
            "amount_due": empty_fv,
        },
        "bank": None,
        "line_items": [],
        "other_fields": [],
    }
    boundary_body = {
        "segments": [{"page_start": 1, "page_end": 1, "doc_type": "invoice"}]
    }

    class StubChat:
        def complete_json(self, **kwargs):
            msgs = kwargs.get("messages") or []
            user = msgs[-1]["content"] if msgs else ""
            if "Heuristic seed" in user or "Per-page summaries" in user:
                data = boundary_body
            else:
                data = extract_body
            if "segments" not in data and "documents" not in data:
                data = {"documents": [data]}
            return ChatResult(
                data=data,
                usage={"prompt_tokens": 5, "completion_tokens": 5},
                model="mistral-small-latest",
                raw_content=json.dumps(data),
                used_json_schema=False,
            )

    settings = replace(
        app_settings,
        mistral_extraction_mode="chat",
        mistral_api_key="test",
        mistral_chat_concurrency=1,
    )
    provider = MistralSplitterProvider(
        settings=settings,
        chat_client=StubChat(),  # type: ignore[arg-type]
        ocr_response=ocr,
    )
    result = provider.extract(b"%PDF")
    assert "boundary_json_schema_fallback" in result.errors
    assert "chat_json_schema_fallback" in result.errors
    paths = (result.cost_breakdown or {}).get("chat_raw_paths") or []
    assert paths, "expected chat_raw_paths to be populated"
    for p in paths:
        assert Path(p).is_file()
        saved = json.loads(Path(p).read_text())
        assert saved.get("used_json_schema") is False
        assert "data" in saved


def test_extract_one_segment_can_split_into_two_docs():
    """Stub extraction returns two docs for one merged segment; both survive."""
    from pdfsplit.chat import ChatResult

    ocr = {
        "pages": [
            {
                "index": 0,
                "markdown": "Invoice No.: A\nTotal 10",
                "blocks": [{"type": "title", "content": "KUDOS", "top_left_y": 10}],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            },
            {
                "index": 1,
                "markdown": "Invoice No.: B\nTotal 20",
                "blocks": [{"type": "title", "content": "KUDOS", "top_left_y": 10}],
                "confidence_scores": {"average_page_confidence_score": 0.9},
            },
        ],
        "usage_info": {"pages_processed": 2},
        "model": "mistral-ocr-latest",
    }
    empty = {"value": "", "source_text": "", "pages": []}

    def _doc(page, inv, total_src):
        return {
            "page_start": page,
            "page_end": page,
            "doc_type": "tax_invoice",
            "seller": {
                "name": {"value": "KUDOS", "source_text": "KUDOS", "pages": [page]},
                "address": empty,
                "tax_id": empty,
            },
            "buyer": {"name": empty, "address": empty, "tax_id": empty},
            "document_id": {
                "value": inv,
                "source_text": f"Invoice No.: {inv}",
                "pages": [page],
            },
            "issue_date": empty,
            "due_date": empty,
            "currency": empty,
            "po_number": empty,
            "payment_terms": empty,
            "billing_period": empty,
            "totals": {
                "subtotal": empty,
                "tax_lines": [],
                "total": {"value": total_src.split()[-1], "source_text": total_src, "pages": [page]},
                "amount_due": empty,
            },
            "bank": None,
            "line_items": [],
            "other_fields": [],
        }

    class StubChat:
        def complete_json(self, **kwargs):
            user = (kwargs.get("messages") or [])[-1]["content"]
            if "Heuristic seed" in user or "Per-page summaries" in user:
                # Intentionally merge both pages into one segment.
                data = {
                    "segments": [
                        {"page_start": 1, "page_end": 2, "doc_type": "tax_invoice"}
                    ]
                }
            else:
                data = {
                    "documents": [
                        _doc(1, "A", "Total 10"),
                        _doc(2, "B", "Total 20"),
                    ]
                }
            return ChatResult(
                data=data,
                usage={"prompt_tokens": 8, "completion_tokens": 8},
                model="mistral-small-latest",
                raw_content=json.dumps(data),
                used_json_schema=True,
            )

    settings = replace(
        app_settings,
        mistral_extraction_mode="chat",
        mistral_api_key="test",
        mistral_chat_concurrency=1,
    )
    provider = MistralSplitterProvider(
        settings=settings,
        chat_client=StubChat(),  # type: ignore[arg-type]
        ocr_response=ocr,
    )
    result = provider.extract(b"%PDF")
    assert uncovered_pages(result.documents, result.pages_processed) == []
    assert len(result.documents) == 2
    assert {d.invoice_number for d in result.documents} == {"A", "B"}
    assert {d.page_start for d in result.documents} == {1, 2}
