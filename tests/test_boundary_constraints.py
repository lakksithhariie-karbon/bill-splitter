"""Tests for Layer-1 boundary constraints and the boundary benchmark harness."""

from __future__ import annotations

from pdfsplit.boundary_benchmark import (
    boundary_perfect,
    pages_from_pdf_text,
    run_boundary_benchmark,
    score_boundary_packet,
    segments_to_split_result,
)
from pdfsplit.boundary_constraints import (
    collect_boundary_constraints,
    compute_boundary_constraints,
    enforce_constraints_on_starts,
    explain_constraint_conflicts,
    extract_doc_number_token,
    extract_page_of_n,
    page_dimensions,
)
from pdfsplit.config import settings
from pdfsplit.schema import SplitDocument
from pdfsplit.scoring import DocTruth


def test_page_dimensions_and_doc_number_helpers():
    page = {
        "dimensions": {"dpi": 72, "width": 748.2, "height": 1020.4},
        "markdown": "Tax Invoice\nInvoice No. INV-11892\nPage 2 of 3\n",
    }
    assert page_dimensions(page) == (748, 1020)
    assert extract_doc_number_token(page["markdown"]) == "INV-11892"
    assert extract_page_of_n(page["markdown"]) == (2, 3)


def test_must_split_on_dimension_change_and_must_not_on_shared_number():
    pages = [
        {
            "markdown": "Invoice No. AA-1\nTotal 10",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. AA-1\nPage 2 of 2\nTotal 10",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. BB-2\nPage 1 of 1",
            "dimensions": {"width": 800, "height": 1100, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    kinds = {(c.page, c.kind, c.signal) for c in cons}
    assert (2, "must_not_split", "shared_doc_number") in kinds
    assert (2, "must_not_split", "continuation_marker") in kinds
    assert (3, "must_split", "page_dimensions") in kinds
    assert (3, "must_split", "page_1_of_n") in kinds

    starts = enforce_constraints_on_starts([1, 2], 3, cons)
    assert 2 not in starts  # must not split
    assert 3 in starts  # must split


def test_conflict_prefers_must_split():
    pages = [
        {
            "markdown": "Invoice No. SAME-1",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Invoice No. SAME-1",
            "dimensions": {"width": 800, "height": 1100, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    assert any(c.kind == "must_split" and c.page == 2 for c in cons)
    assert not any(c.kind == "must_not_split" and c.page == 2 for c in cons)


def test_explain_marks_losers_suppressed_not_dropped():
    """A fired hold that lost the rank fight must still be visible."""
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
    raw = collect_boundary_constraints(pages)
    resolved = compute_boundary_constraints(pages)
    assert any(
        c.page == 3 and c.signal == "shared_doc_number" for c in raw
    )
    assert not any(
        c.page == 3 and c.signal == "shared_doc_number" for c in resolved
    )
    rows = explain_constraint_conflicts(raw)
    hold = next(
        r
        for r in rows
        if r["page"] == 3 and r["signal"] == "shared_doc_number"
    )
    assert hold["kind"] == "must_not_split"
    assert hold["rank"] == 60
    assert hold["suppressed_by"] == "page_1_of_n"
    assert hold["suppressed_by_rank"] == 85
    winner = next(
        r for r in rows if r["page"] == 3 and r["signal"] == "page_1_of_n"
    )
    assert winner["suppressed_by"] is None


def test_pkt_b06_shared_number_is_suppressed_not_absent():
    from pathlib import Path

    from pdfsplit.boundary_benchmark import pages_from_pdf_text

    b06 = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "corpus"
        / "pkt_b06_duplicate_invoice_number.pdf"
    )
    pages = pages_from_pdf_text(b06)
    raw = collect_boundary_constraints(pages)
    rows = explain_constraint_conflicts(raw)
    hold = next(
        (
            r
            for r in rows
            if r["page"] == 3 and r["signal"] == "shared_doc_number"
        ),
        None,
    )
    assert hold is not None, "shared_doc_number must fire at p3 (both INV-2026-9999)"
    assert hold["suppressed_by"] == "page_1_of_n"
    assert hold["suppressed_by_rank"] == 85
    resolved = compute_boundary_constraints(pages)
    assert not any(
        c.page == 3 and c.signal == "shared_doc_number" for c in resolved
    )


def test_must_split_on_different_own_doc_numbers():
    """Same letterhead, different Invoice No. in header → must_split."""
    pages = [
        {
            "markdown": (
                "Acme Supplies\nInvoice No: INV-2026-5501\n"
                "Line 1: Widget\nAgainst Invoice INV-OLD-9\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": (
                "Acme Supplies\nInvoice No: INV-2026-5502\n"
                "Line 1: Widget\nPO Number: PO-999\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    hits = [c for c in cons if c.signal == "different_doc_number"]
    assert len(hits) == 1
    assert hits[0].page == 2
    assert hits[0].kind == "must_split"
    assert "different invoice numbers at p2" in hits[0].detail
    assert "INV-2026-5501" in hits[0].detail
    assert "INV-2026-5502" in hits[0].detail
    starts = enforce_constraints_on_starts([1], 2, cons)
    assert starts == [1, 2]


def test_no_constraint_when_only_foreign_or_body_numbers():
    """PO / against / body-only numbers must not invent a split."""
    from pdfsplit.boundary_constraints import extract_own_doc_number

    # Own number only via PO label — reject.
    po_only = {
        "markdown": "Acme\nPO Number: PO-12345\nTotal 10\n",
        "blocks": [],
    }
    assert extract_own_doc_number(po_only) is None

    # Against invoice in header band — reject that line; no own label.
    against = {
        "markdown": "Acme\nAgainst Invoice INV-OLD-1\nTotal 10\n",
        "blocks": [],
    }
    assert extract_own_doc_number(against) is None

    # Number only deep in the body (beyond header line cap) — no constraint.
    body_only = {
        "markdown": "\n".join(
            [
                "Acme Supplies",
                "TAX INVOICE",
                "Date: 2026-01-01",
                "Bill To: Buyer",
                "Ship To: Buyer",
                "Terms: Net 30",
                "Currency: INR",
                "Warehouse: A",
                "Salesperson: X",
                "Notes: none",
                "Remark: none",
                "Footer preamble",
                "Invoice No: INV-BURIED-1",  # line 13 — past HEADER_LINE_CAP
            ]
        ),
        "blocks": [],
    }
    assert extract_own_doc_number(body_only) is None

    pages = [
        {
            "markdown": "Acme\nInvoice No: INV-1\nTotal 1\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Acme\nPO Number: PO-999\nAgainst Invoice INV-1\nTotal 2\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    assert not any(c.signal == "different_doc_number" for c in cons)


def test_continuation_marker_beats_different_doc_number():
    """Explicit precedence: Page 2 of N wins over differing invoice numbers."""
    from pdfsplit.boundary_constraints import SIGNAL_RANK

    assert (
        SIGNAL_RANK["continuation_marker"]
        > SIGNAL_RANK["different_doc_number"]
    )
    pages = [
        {
            "markdown": "Acme\nInvoice No: INV-AAAA\nTotal 10\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            # Conflicting signals: continuation + different own number.
            "markdown": (
                "Acme\nInvoice No: INV-BBBB\nPage 2 of 3\nTotal 10\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    kinds = {(c.kind, c.signal) for c in cons if c.page == 2}
    assert ("must_not_split", "continuation_marker") in kinds
    assert ("must_split", "different_doc_number") not in kinds
    starts = enforce_constraints_on_starts([1, 2], 2, cons)
    assert 2 not in starts


def test_document_complete_must_split_on_totals_then_header():
    from pdfsplit.boundary_constraints import SIGNAL_RANK

    assert SIGNAL_RANK["continuation_marker"] > SIGNAL_RANK["document_complete"]
    assert SIGNAL_RANK["shared_doc_number"] > SIGNAL_RANK["document_complete"]
    pages = [
        {
            "markdown": (
                "GreenWay Transport Inc.\nInvoice INV-001\n"
                "Freight delivery\n"
                "Subtotal: $400.00\nTax: $25.00\nGrand Total: $425.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {
                    "type": "title",
                    "content": "GreenWay Transport Inc.",
                    "top_left_y": 40,
                }
            ],
        },
        {
            "markdown": (
                "City Courier Services\nInvoice INV-002\n"
                "Local delivery\nGrand Total: $180.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {
                    "type": "title",
                    "content": "City Courier Services",
                    "top_left_y": 40,
                }
            ],
        },
    ]
    cons = compute_boundary_constraints(pages)
    hits = [c for c in cons if c.signal == "document_complete"]
    assert len(hits) == 1
    assert hits[0].page == 2 and hits[0].kind == "must_split"
    starts = enforce_constraints_on_starts([1], 2, cons)
    assert starts == [1, 2]


def test_document_complete_suppressed_by_continuation_marker():
    pages = [
        {
            "markdown": "Acme\nInvoice No: INV-1\nGrand Total: $10.00\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "Acme", "top_left_y": 20}
            ],
        },
        {
            "markdown": (
                "Acme\nInvoice No: INV-1\nPage 2 of 2\nGrand Total: $10.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "Acme", "top_left_y": 20}
            ],
        },
    ]
    cons = compute_boundary_constraints(pages)
    assert any(c.signal == "continuation_marker" and c.page == 2 for c in cons)
    assert not any(c.signal == "document_complete" and c.page == 2 for c in cons)
    starts = enforce_constraints_on_starts([1, 2], 2, cons)
    assert 2 not in starts


def test_shared_doc_number_beats_document_complete():
    """Same invoice id continuing without Page X of N — do not hard-split."""
    pages = [
        {
            "markdown": (
                "Acme\nInvoice No: INV-SHARED-1\n"
                "Line items continued next page\nAmount Due: $50.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "Acme", "top_left_y": 20}
            ],
        },
        {
            "markdown": (
                "Acme\nInvoice No: INV-SHARED-1\n"
                "More lines\nGrand Total: $50.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "Acme", "top_left_y": 20}
            ],
        },
    ]
    cons = compute_boundary_constraints(pages)
    # Both may be proposed raw, but resolution must keep must_not_split.
    assert any(c.kind == "must_not_split" and c.page == 2 for c in cons)
    assert not any(c.kind == "must_split" and c.page == 2 for c in cons)


def test_page_without_number_never_emits_different_doc_constraint():
    pages = [
        {
            "markdown": "Acme\nInvoice No: INV-1\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "Acme\nThank you for your business\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    assert not any(c.signal == "different_doc_number" for c in cons)


def test_different_doc_number_evidence_on_segment():
    from pdfsplit.boundary_constraints import annotate_segments_with_evidence

    pages = [
        {
            "markdown": "V\nInvoice No: INV-1\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": "V\nInvoice No: INV-2\n",
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [],
        },
    ]
    cons = compute_boundary_constraints(pages)
    segs = annotate_segments_with_evidence(
        [
            {"page_start": 1, "page_end": 1, "doc_type": "invoice"},
            {"page_start": 2, "page_end": 2, "doc_type": "invoice"},
        ],
        cons,
        llm_used=False,
    )
    assert "different_doc_number" in segs[1]["signals"]
    assert any("different invoice numbers at p2" in e for e in segs[1]["evidence"])


def test_boundary_perfect_ignores_class():
    truth = [DocTruth(doc_type="invoice", start_page=1, end_page=2)]
    pred = [
        SplitDocument(
            doc_type="unknown", start_page=1, end_page=2, confidence=1.0
        )
    ]
    assert boundary_perfect(truth, pred) is True


def test_mock_boundary_benchmark_runs_without_api(tmp_path):
    report = run_boundary_benchmark(
        settings.corpus_dir,
        tmp_path,
        provider="mock",
        mode="heuristic",
        layer1=True,
        use_signature=False,
        limit=3,
        compare_baseline=True,
    )
    assert report["disclaimer"].startswith("SYNTHETIC")
    assert report["summary"]["packets"] == 3
    assert "perfect_packet_rate" in report["summary"]
    assert "by_scenario_tag" in report["summary"]
    assert "baseline_without_layer1" in report
    assert (tmp_path / "boundary_mock_l1.json").is_file()


def test_pages_from_pdf_text_covers_corpus_control():
    pdf = settings.corpus_dir / "pkt_b01_varied_page_sizes.pdf"
    pages = pages_from_pdf_text(pdf)
    assert len(pages) == 6
    assert pages[0]["dimensions"]["width"] > 0
    assert isinstance(pages[0]["markdown"], str)


def test_document_complete_ignores_signature_page_after_totals():
    from pdfsplit.boundary_constraints import (
        collect_boundary_constraints,
        page_is_closing_continuation,
    )

    pages = [
        {
            "markdown": (
                "ZZ Broking\nContract Note No COMBINED/1\n"
                "| Scrip | Qty | Amount |\n| AAA | 1 | 10.00 |\n"
                "Grand Total: 10.00\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "CONTRACT NOTE", "top_left_y": 20}
            ],
        },
        {
            "markdown": (
                "ZZ Broking\nContract Note No COMBINED/1\n"
                "| Scrip | Qty | Amount |\n"
                "Yours faithfully\nAuthorised Signatory\n"
            ),
            "dimensions": {"width": 700, "height": 1000, "dpi": 72},
            "blocks": [
                {"type": "title", "content": "CONTRACT NOTE", "top_left_y": 20},
                {
                    "type": "signature",
                    "content": "Authorised Signatory",
                    "top_left_y": 640,
                },
            ],
        },
    ]
    assert page_is_closing_continuation(pages[1]) is True
    raw = collect_boundary_constraints(pages)
    assert not any(c.signal == "document_complete" and c.page == 2 for c in raw)
    assert any(c.signal == "closing_page" and c.kind == "must_not_split" for c in raw)
    cons = compute_boundary_constraints(pages)
    starts = enforce_constraints_on_starts([1, 2], 2, cons)
    assert 2 not in starts


def test_standalone_terms_page_is_not_closing_page():
    """A TERMS AND CONDITIONS document after an invoice is not a closer."""
    from pdfsplit.boundary_constraints import (
        collect_boundary_constraints,
        page_is_closing_continuation,
        page_is_standalone_exclusion,
    )

    pages = [
        {
            "markdown": (
                "ZZ TEST Volume Vendor\nTAX INVOICE\n"
                "Invoice No: INV-2026-8801\nGrand Total: INR 236.00\n"
            ),
            "dimensions": {"width": 612, "height": 792, "dpi": 72},
            "blocks": [],
        },
        {
            "markdown": (
                "ZZ TEST Volume Vendor\n"
                "TERMS AND CONDITIONS   |   packet pkt_b08_volume_push_mix\n"
                "Payment due within 30 days. Not a tax invoice.\n"
            ),
            "dimensions": {"width": 612, "height": 792, "dpi": 72},
            "blocks": [],
        },
    ]
    assert page_is_standalone_exclusion(pages[1]) is True
    assert page_is_closing_continuation(pages[1]) is False
    raw = collect_boundary_constraints(pages)
    assert not any(c.signal == "closing_page" for c in raw)


def test_extract_contract_note_number_not_client_code():
    from pdfsplit.boundary_constraints import (
        extract_doc_number_token,
        extract_own_doc_number,
    )

    page = {
        "markdown": (
            "ZZ Broking\nGSTIN: 27AAAAA0000A1Z5\n"
            "Client Code: ZZCL-0091\n"
            "Contract Note No COMBINED/14531\n"
        ),
        "blocks": [],
    }
    assert extract_doc_number_token(page["markdown"]) == "COMBINED/14531"
    assert extract_own_doc_number(page) == "COMBINED/14531"


def test_invoice_title_is_not_a_document_number():
    """A TAX INVOICE heading must not steal the packet id as the own number."""
    from pdfsplit.aia.filenames import regex_doc_number_from_pages
    from pdfsplit.boundary_constraints import extract_doc_number_token

    md = (
        "ZZ TEST Acme Supplies Pvt Ltd\n"
        "TAX INVOICE   |   packet pkt_b03_same_letterhead_bbb\n"
        "============================================================\n"
        "Invoice No: INV-B03-0001\n"
        "Vendor: ZZ TEST Acme Supplies Pvt Ltd\n"
    )
    assert extract_doc_number_token(md) == "INV-B03-0001"
    assert regex_doc_number_from_pages([md]) == "INV-B03-0001"

