"""BOUNDARY_DEBUG gate, redaction, and session trace file."""

from __future__ import annotations

from pathlib import Path

from pdfsplit.boundary_trace import (
    boundary_debug_enabled,
    contradiction_warnings,
    emit_trace,
    format_trace,
    on_render,
    redact_secrets,
)
from pdfsplit.config import Settings
from pdfsplit.providers.mistral import MistralSplitterProvider

ROOT = Path(__file__).resolve().parents[1]
GGPL = ROOT / "vendors" / "mistral" / "fixtures" / "pkt_ggpl_continuation_annexure.json"


def test_debug_off_by_default(monkeypatch):
    monkeypatch.delenv("BOUNDARY_DEBUG", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    assert boundary_debug_enabled() is False


def test_debug_on_locally(monkeypatch):
    monkeypatch.setenv("BOUNDARY_DEBUG", "1")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert boundary_debug_enabled() is True


def test_debug_forced_off_on_render(monkeypatch):
    monkeypatch.setenv("BOUNDARY_DEBUG", "1")
    monkeypatch.setenv("RENDER", "true")
    assert on_render() is True
    assert boundary_debug_enabled() is False


def test_redact_strips_key_and_bearer(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-secret-value-xyz")
    text = "Bearer sk-secret-value-xyz appeared in a log"
    out = redact_secrets(text)
    assert "sk-secret-value-xyz" not in out
    assert "Bearer [redacted]" in out or "[MISTRAL_API_KEY=redacted]" in out


def test_emit_writes_session_file_when_on(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BOUNDARY_DEBUG", "1")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    payload = {
        "session_id": "sess-1",
        "filename": "pkt.pdf",
        "page_count": 1,
        "provider": "mistral",
        "model": "test",
        "page_evidence": [],
        "constraints": [],
        "judge": {"called": False, "starts": [1], "segments": []},
        "reconcile": [],
        "overlay_pairs": [],
        "final_segments": [
            {"page_start": 1, "page_end": 1, "doc_type": "invoice"}
        ],
        "caught_by": [],
        "warnings": [],
    }
    text = emit_trace(payload, session_dir=tmp_path)
    assert "BOUNDARY TRACE" in text
    assert (tmp_path / "boundary_trace.txt").is_file()
    err = capsys.readouterr().err
    assert "BOUNDARY TRACE" in err


def test_emit_silent_when_off(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("BOUNDARY_DEBUG", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    text = emit_trace({"session_id": "x"}, session_dir=tmp_path)
    assert text == ""
    assert not (tmp_path / "boundary_trace.txt").exists()
    assert "BOUNDARY TRACE" not in capsys.readouterr().err


def test_analyze_trace_file_and_quiet_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BOUNDARY_DEBUG", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    from pdfsplit.app import main
    from fastapi.testclient import TestClient
    from pdfsplit.app.main import app
    from tests.test_e2e_local import _ggpl_pdf, _upload

    main.store = main.SessionStore(tmp_path / "sessions")
    empty = Settings(mistral_api_key="")
    monkeypatch.setattr(
        main,
        "_build_provider",
        lambda: MistralSplitterProvider(settings=empty, fixture_path=GGPL),
    )
    client = TestClient(app)
    pdf = _ggpl_pdf(tmp_path)
    data = _upload(client, pdf, "pkt_ggpl_continuation_annexure.pdf")
    sid = data["session_id"]
    analyzed = client.post(f"/api/session/{sid}/analyze")
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert "_boundary_trace" not in body
    session_dir = tmp_path / "sessions" / sid
    assert not (session_dir / "boundary_trace.txt").exists()
    # Quiet: HTTP body still has overlay, not the console dump.
    assert body.get("overlay", {}).get("extent_hold_pages") == [2, 3]


def test_analyze_writes_trace_when_debug_on(tmp_path, monkeypatch):
    monkeypatch.setenv("BOUNDARY_DEBUG", "1")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    from pdfsplit.app import main
    from fastapi.testclient import TestClient
    from pdfsplit.app.main import app
    from tests.test_e2e_local import _ggpl_pdf, _upload

    main.store = main.SessionStore(tmp_path / "sessions")
    empty = Settings(mistral_api_key="")
    monkeypatch.setattr(
        main,
        "_build_provider",
        lambda: MistralSplitterProvider(settings=empty, fixture_path=GGPL),
    )
    client = TestClient(app)
    pdf = _ggpl_pdf(tmp_path)
    data = _upload(client, pdf, "pkt_ggpl_continuation_annexure.pdf")
    sid = data["session_id"]
    analyzed = client.post(f"/api/session/{sid}/analyze")
    assert analyzed.status_code == 200
    assert "_boundary_trace" not in analyzed.json()
    trace_path = tmp_path / "sessions" / sid / "boundary_trace.txt"
    assert trace_path.is_file()
    text = trace_path.read_text()
    assert "BOUNDARY TRACE" in text
    assert "LAYER 1 CONSTRAINTS" in text
    assert "declared_extent" in text
    assert "FINAL" in text
    assert "ONE document" not in text  # we print ranges, not that phrase
    assert "p1-3" in text
    assert "MISTRAL_API_KEY" not in text or "[MISTRAL_API_KEY=redacted]" in text
    assert "Bearer " not in text or "Bearer [redacted]" in text
    # Cookie / AIA session never appear.
    assert "AIA_SESSION" not in text
    assert "Set-Cookie" not in text
    # Human sections first; the /analyze JSON is appended at the end.
    assert not text.lstrip().startswith("{")
    assert "ANALYZE RESPONSE" in text
    assert '"segments"' in text
    assert '"pre_overlay_segments"' in text
    assert '"overlay"' in text
    assert "SUPPRESSED by declared_extent" in text
    assert "document_complete" in text


def test_format_trace_marks_suppressed_shared_doc_number():
    from pdfsplit.boundary_constraints import (
        collect_boundary_constraints,
        explain_constraint_conflicts,
    )

    pages = [
        {
            "markdown": "Invoice No. INV-2026-9999\nPage 1 of 2\nGrand Total 10",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. INV-2026-9999\nPage 2 of 2",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. INV-2026-9999\nPage 1 of 2",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. INV-2026-9999\nPage 2 of 2",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    rows = explain_constraint_conflicts(collect_boundary_constraints(pages))
    text = format_trace(
        {
            "session_id": "t",
            "filename": "pkt_b06.pdf",
            "page_count": 4,
            "provider": "mistral",
            "model": "test",
            "page_evidence": [],
            "constraints": rows,
            "judge": {"called": False, "starts": [1, 3], "segments": []},
            "reconcile": [],
            "overlay_pairs": [],
            "final_segments": [],
            "caught_by": [],
            "warnings": [],
        }
    )
    assert "shared_doc_number" in text
    assert "SUPPRESSED by page_1_of_n (r85)" in text
    assert "must_not_split" in text


def test_contradiction_warning_on_judge_split_same_number():
    import json

    pages = json.loads(GGPL.read_text())["pages"]
    segs = [
        {
            "page_start": 1,
            "page_end": 2,
            "document_number": "ZZCO/8801/26-27",
            "vendor": "ZZ Test Components Pvt Ltd",
        },
        {
            "page_start": 3,
            "page_end": 3,
            "document_number": "ZZCO/8801/26-27",
            "vendor": "ZZ Test Components Pvt Ltd",
        },
    ]
    warns = contradiction_warnings(segs, pages)
    assert warns
    assert "p1-2" in warns[0] and "p3-3" in warns[0]
    assert "ZZCO/8801/26-27" in warns[0]


def test_parse_boundaries_carries_reason_and_confidence():
    from pdfsplit.providers.mistral import parse_boundary_llm_response

    starts, _types, _naming, just = parse_boundary_llm_response(
        {
            "segments": [
                {
                    "page_start": 1,
                    "page_end": 2,
                    "doc_type": "invoice",
                    "vendor": "A",
                    "document_number": "N",
                },
                {
                    "page_start": 3,
                    "page_end": 3,
                    "doc_type": "other",
                    "vendor": "A",
                    "document_number": "N",
                },
            ],
            "boundaries": [
                {"page": 1, "reason": "First page of the packet.", "confidence": 1},
                {
                    "page": 3,
                    "reason": "New heading after a grand total.",
                    "confidence": 0.55,
                },
            ],
        },
        3,
        [1],
    )
    assert starts == [1, 3]
    assert just[3]["reason"] == "New heading after a grand total."
    assert just[3]["confidence"] == 0.55


def test_parse_ignores_reason_on_segments():
    """Cut justification lives on boundaries, not on segment objects."""
    from pdfsplit.providers.mistral import parse_boundary_llm_response

    starts, _types, _naming, just = parse_boundary_llm_response(
        {
            "segments": [
                {
                    "page_start": 1,
                    "page_end": 2,
                    "doc_type": "invoice",
                    "vendor": "A",
                    "document_number": "N",
                    "reason": "same supplier and bill reference",
                    "confidence": 0.95,
                },
                {
                    "page_start": 3,
                    "page_end": 3,
                    "doc_type": "other",
                    "vendor": "A",
                    "document_number": "N",
                    "reason": "same supplier and bill reference",
                    "confidence": 0.95,
                },
            ],
            "boundaries": [
                {"page": 1, "reason": "First page of the packet.", "confidence": 1},
            ],
        },
        3,
        [1],
    )
    assert starts == [1, 3]
    assert just[1]["reason"] == "First page of the packet."
    assert 3 not in just


def test_contradiction_warning_on_continuation_reason():
    from pdfsplit.boundary_trace import contradiction_warnings

    pages = [{"markdown": "x"}] * 3
    segs = [
        {
            "page_start": 1,
            "page_end": 2,
            "document_number": "ZZCO/8801/26-27",
            "vendor": "ZZ Test Components Pvt Ltd",
            "judge_reason": "First page of the packet.",
        },
        {
            "page_start": 3,
            "page_end": 3,
            "document_number": "ZZCO/8801/26-27",
            "vendor": "ZZ Test Components Pvt Ltd",
            "judge_reason": "same supplier and bill reference as previous pages",
        },
    ]
    warns = contradiction_warnings(segs, pages)
    assert any("reason asserts continuation" in w and "p3" in w for w in warns)


def test_contradiction_warning_skips_despite_same_number_cut():
    """A cut justified *despite* a shared number is not a continuation reason."""
    from pdfsplit.boundary_trace import reason_asserts_continuation

    assert reason_asserts_continuation(
        "same supplier and bill reference as previous pages"
    )
    assert not reason_asserts_continuation(
        "Page 3 is marked Page 1 of 2, indicating a new document begins here "
        "despite the same document number."
    )


def test_contradiction_warning_skips_when_vendors_differ():
    """pkt_b06: same number + different vendors is a correct split, not a wolf."""
    from pdfsplit.boundary_trace import contradiction_warnings

    pages = [
        {
            "markdown": (
                "ZZ TEST Duplo First\nInvoice No: INV-2026-9999\n"
                "Page 1 of 2\nGrand Total 10"
            )
        },
        {
            "markdown": (
                "ZZ TEST Duplo First\nInvoice No: INV-2026-9999\nPage 2 of 2"
            )
        },
        {
            "markdown": (
                "ZZ TEST Duplo Second\nInvoice No: INV-2026-9999\n"
                "Page 1 of 2\nGrand Total 20"
            )
        },
        {
            "markdown": (
                "ZZ TEST Duplo Second\nInvoice No: INV-2026-9999\nPage 2 of 2"
            )
        },
    ]
    segs = [
        {
            "page_start": 1,
            "page_end": 2,
            "document_number": "INV-2026-9999",
            "vendor": "ZZ TEST Duplo First",
            "judge_reason": "First page of the packet.",
        },
        {
            "page_start": 3,
            "page_end": 4,
            "document_number": "INV-2026-9999",
            "vendor": "ZZ TEST Duplo Second",
            "judge_reason": (
                "Page 3 is marked Page 1 of 2 and introduces a new vendor "
                "ZZ TEST Duplo Second while retaining the same document "
                "number INV-2026-9999, indicating a new bill from a "
                "different supplier."
            ),
        },
    ]
    warns = contradiction_warnings(segs, pages)
    assert warns == []


def test_format_trace_prints_judge_reason_distinct_from_signals():
    text = format_trace(
        {
            "session_id": "t",
            "filename": "pkt.pdf",
            "page_count": 3,
            "provider": "mistral",
            "model": "test",
            "page_evidence": [],
            "constraints": [],
            "judge": {
                "called": True,
                "model": "mistral-small-latest",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "latency_ms": 12.3,
                "cost_usd": 0.0,
                "starts": [1, 3],
                "segments": [
                    {
                        "page_start": 1,
                        "page_end": 2,
                        "doc_type": "tax_invoice",
                        "vendor": "A",
                        "document_number": "N",
                        "judge_reason": "First page of the packet.",
                        "judge_confidence": 1.0,
                    },
                    {
                        "page_start": 3,
                        "page_end": 3,
                        "doc_type": "other",
                        "vendor": "A",
                        "document_number": "N",
                        "judge_reason": "New heading after a grand total.",
                        "judge_confidence": 0.55,
                    },
                ],
            },
            "reconcile": [],
            "overlay_pairs": [],
            "final_segments": [
                {
                    "page_start": 1,
                    "page_end": 3,
                    "doc_type": "tax_invoice",
                    "document_number": "N",
                    "confidence": 0.98,
                    "signals": ["declared_extent"],
                }
            ],
            "caught_by": ["declared_extent"],
            "warnings": [],
        }
    )
    assert "judge_conf=0.55" in text
    assert "New heading after a grand total." in text
    assert "signals_conf=0.98" in text
    assert "judge_conf=0.98" not in text


def test_judge_confidence_does_not_replace_signal_confidence():
    import json

    from pdfsplit.chat import ChatResult
    from pdfsplit.providers.mistral import find_boundaries_with_llm

    pages = json.loads(GGPL.read_text())["pages"]

    class FakeChat:
        def complete_json(self, **_kwargs):
            return ChatResult(
                data={
                    "segments": [
                        {
                            "page_start": 1,
                            "page_end": 2,
                            "doc_type": "tax_invoice",
                            "vendor": "ZZ Test Components Pvt Ltd",
                            "document_number": "ZZCO/8801/26-27",
                        },
                        {
                            "page_start": 3,
                            "page_end": 3,
                            "doc_type": "other",
                            "vendor": "ZZ Test Components Pvt Ltd",
                            "document_number": "ZZCO/8801/26-27",
                        },
                    ],
                    "boundaries": [
                        {
                            "page": 1,
                            "reason": "First page of the packet.",
                            "confidence": 1.0,
                        },
                        {
                            "page": 3,
                            "reason": "Annexure looks like a new document.",
                            "confidence": 0.41,
                        },
                    ],
                },
                usage={"prompt_tokens": 11, "completion_tokens": 22},
                model="fake-judge",
                raw_content="",
                used_json_schema=True,
            )

    trace: dict = {}
    segs, errors = find_boundaries_with_llm(pages, chat=FakeChat(), trace=trace)
    assert "boundary_llm_fallback" not in errors
    assert len(segs) == 1
    assert (segs[0]["page_start"], segs[0]["page_end"]) == (1, 3)
    assert segs[0]["confidence"] == 0.98
    assert "judge_confidence" not in segs[0]
    assert "judge_reason" not in segs[0]
    proposal = trace["judge"]["segments"]
    p3 = next(s for s in proposal if s["page_start"] == 3)
    assert p3["judge_confidence"] == 0.41
    assert "Annexure" in p3["judge_reason"]
    assert p3.get("confidence") is None


def test_judge_prompt_defines_document_as_one_bill():
    from pdfsplit.providers.mistral import (
        _BOUNDARY_JUDGE_SYSTEM,
        _BOUNDARY_JUDGE_USER_TAIL,
    )

    assert "ONE BILL" in _BOUNDARY_JUDGE_SYSTEM
    assert "one voucher" in _BOUNDARY_JUDGE_SYSTEM
    assert "statutory annexures" in _BOUNDARY_JUDGE_SYSTEM
    assert "DIFFERENT BILL" in _BOUNDARY_JUDGE_SYSTEM
    assert "why does a NEW document" in _BOUNDARY_JUDGE_SYSTEM
    assert "Prefer splitting over merging when unsure" not in _BOUNDARY_JUDGE_SYSTEM
    assert "each page has its own totals block" not in _BOUNDARY_JUDGE_SYSTEM
    assert "self-contained invoice page" not in _BOUNDARY_JUDGE_USER_TAIL
    assert "one voucher" in _BOUNDARY_JUDGE_USER_TAIL
    assert "justify the cut" in _BOUNDARY_JUDGE_USER_TAIL

