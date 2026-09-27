"""Extent phrases, post-judge overlay tiers, GGPL vs pkt_b06."""

from __future__ import annotations

import json
from pathlib import Path

from pdfsplit.boundary_constraints import (
    SIGNAL_RANK,
    compute_boundary_constraints,
    enforce_constraints_on_starts,
    page_footer_text,
    parse_declared_extent,
)
from pdfsplit.boundary_overlay import (
    apply_judge_overlay,
    classify_adjacent_pair,
    letterhead_vendor,
    overlay_number,
    vendors_relation,
)
from pdfsplit.boundary_benchmark import pages_from_pdf_text, run_boundaries_on_pages
from pdfsplit.config import Settings

EMPTY = Settings(mistral_api_key="")

ROOT = Path(__file__).resolve().parents[1]
GGPL = ROOT / "vendors" / "mistral" / "fixtures" / "pkt_ggpl_continuation_annexure.json"
GGPL_BARE = ROOT / "vendors" / "mistral" / "fixtures" / "pkt_ggpl_bare_number_annexure.json"
CONTRACT = (
    ROOT / "vendors" / "mistral" / "fixtures" / "pkt_contract_note_signature_page.json"
)
B06 = ROOT / "data" / "corpus" / "pkt_b06_duplicate_invoice_number.pdf"


def _ggpl_pages() -> list[dict]:
    return json.loads(GGPL.read_text())["pages"]


def _contract_pages() -> list[dict]:
    return json.loads(CONTRACT.read_text())["pages"]


def _bare_pages() -> list[dict]:
    return json.loads(GGPL_BARE.read_text())["pages"]


def test_declared_extent_rank_outranks_inferences():
    assert SIGNAL_RANK["declared_extent"] > SIGNAL_RANK["document_complete"]
    assert SIGNAL_RANK["declared_extent"] > SIGNAL_RANK["different_doc_number"]
    assert SIGNAL_RANK["declared_extent"] > SIGNAL_RANK["page_dimensions"]
    assert SIGNAL_RANK["declared_extent"] > SIGNAL_RANK["continuation_marker"]


def test_parse_extent_phrasings():
    assert parse_declared_extent(
        "This invoice is continued to page number 3.", 1, 3
    ) == [(3, "continued to page 3")]
    assert parse_declared_extent("continued on page 4", 2, 6) == [
        (4, "continued to page 4")
    ]
    assert parse_declared_extent("cont'd on page 5", 3, 8) == [
        (5, "continued to page 5")
    ]
    assert parse_declared_extent("continued on next page", 2, 5) == [
        (3, "continued on next page")
    ]
    assert parse_declared_extent("Invoice of 4 pages.", 1, 4) == [
        (4, "of 4 pages")
    ]
    # Out of packet / N <= P ignored.
    assert parse_declared_extent("continued to page 9", 1, 3) == []
    assert parse_declared_extent("continued to page 1", 1, 3) == []
    assert parse_declared_extent("continued on next page", 3, 3) == []


def test_declared_extent_ignores_body_boilerplate():
    """T&C prose in the body must not fire the highest-rank hold."""
    pages = [
        {
            "markdown": (
                "# TERMS AND CONDITIONS\n"
                "1. Goods remain our property.\n"
                "2. This agreement is continued on next page for clauses 3-19.\n"
                "3. Liability is limited.\n"
            ),
            "dimensions": {"dpi": 72, "height": 842, "width": 595},
            "blocks": [
                {"type": "title", "content": "TERMS AND CONDITIONS", "top_left_y": 24},
                {
                    "type": "text",
                    "content": "This agreement is continued on next page for clauses 3-19.",
                    "top_left_y": 320,
                },
            ],
        },
        {
            "markdown": "# TERMS AND CONDITIONS\n4. More clauses.\n",
            "dimensions": {"dpi": 72, "height": 842, "width": 595},
            "blocks": [
                {"type": "title", "content": "TERMS AND CONDITIONS", "top_left_y": 24}
            ],
        },
    ]
    assert "continued on next page" in pages[0]["markdown"]
    footer = page_footer_text(pages[0])
    assert "continued on next page" not in footer.lower()
    cons = compute_boundary_constraints(pages)
    assert not any(c.signal == "declared_extent" for c in cons)


def test_ggpl_extent_phrase_is_in_the_footer():
    pages = _ggpl_pages()
    footer = page_footer_text(pages[0])
    assert "continued to page number 3" in footer.lower()
    # Body still has the sentence (OCR markdown includes the footer), but
    # a mid-page copy would not be enough — the footer block is what fires.
    assert any(
        (b.get("type") or "").lower() == "footer" for b in pages[0]["blocks"]
    )
    pages = _ggpl_pages()
    cons = compute_boundary_constraints(pages)
    holds = {
        (c.page, c.signal)
        for c in cons
        if c.kind == "must_not_split" and c.signal == "declared_extent"
    }
    assert (2, "declared_extent") in holds
    assert (3, "declared_extent") in holds
    # document_complete still wants to split page 3; extent wins.
    starts = enforce_constraints_on_starts([1, 3], 3, cons)
    assert starts == [1]


def test_ggpl_pipeline_one_document_caught_by_extent():
    pages = _ggpl_pages()
    segs, _errors, sigs = run_boundaries_on_pages(
        pages,
        mode="heuristic",
        layer1=True,
        use_signature=False,
        chat=None,
        settings=EMPTY,
    )
    # run_boundaries_on_pages already applied overlay; check the result.
    assert len(segs) == 1
    assert segs[0]["page_start"] == 1 and segs[0]["page_end"] == 3
    overlay = (segs[0].get("_overlay") or {})
    assert overlay.get("extent_hold_pages") == [2, 3]
    assert (overlay.get("tier_counts") or {}).get("merge", 0) == 0
    assert sigs.get("declared_extent")


def test_bare_number_fixture_overlay_merges_to_one_document():
    """No extent sentence: document_complete splits, overlay glues on ZZCO."""
    pages = _bare_pages()
    cons = compute_boundary_constraints(pages)
    assert not any(c.signal == "declared_extent" for c in cons)
    # Layer 1 regexes miss the unlabelled number.
    from pdfsplit.boundary_constraints import extract_doc_number_token

    assert extract_doc_number_token(pages[0]["markdown"] or "") is None
    splits = [c for c in cons if c.kind == "must_split" and c.page == 3]
    assert any(c.signal == "document_complete" for c in splits)

    segs, _errors, sigs = run_boundaries_on_pages(
        pages,
        mode="heuristic",
        layer1=True,
        use_signature=False,
        chat=None,
        settings=EMPTY,
    )
    assert len(segs) == 1
    assert segs[0]["page_start"] == 1 and segs[0]["page_end"] == 3
    overlay = segs[0].get("_overlay") or {}
    assert overlay.get("extent_hold_pages") == []
    assert (overlay.get("tier_counts") or {}).get("merge") == 1
    assert sigs.get("overlay_merge") == 1
    assert segs[0].get("document_number") == "ZZCO/8801/26-27"
    assert segs[0]["overlay_reason"] == "Merged: both pages carry ZZCO/8801/26-27"


def test_bare_header_number_is_not_the_gstin():
    from pdfsplit.aia.filenames import attach_names_to_segments, bare_header_doc_number

    pages = _bare_pages()
    ident = bare_header_doc_number([pages[0]["markdown"]])
    assert ident == "ZZCO/8801/26-27"
    segs = [
        {"page_start": 1, "page_end": 2, "doc_type": "invoice"},
        {"page_start": 3, "page_end": 3, "doc_type": "other"},
    ]
    attach_names_to_segments(segs, pages, packet_stem="packet")
    assert segs[0]["document_number"] == "ZZCO/8801/26-27"
    assert segs[1]["document_number"] == "ZZCO/8801/26-27"
    assert "GSTIN" not in segs[0]["document_number"]
    assert "22AAAAA" not in segs[0]["document_number"]


def test_overlay_would_also_merge_ggpl_if_still_split():
    """Root-cause pass: same grounded number + matching vendor → merge."""
    pages = _ggpl_pages()
    split = [
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
    ]
    merged, report = apply_judge_overlay(split, pages)
    assert len(merged) == 1
    assert merged[0]["page_start"] == 1 and merged[0]["page_end"] == 3
    assert report.merges[0].reason == "Merged: both pages carry ZZCO/8801/26-27"
    assert merged[0]["overlay_undo"][0]["page_end"] == 2
    assert merged[0]["overlay_undo"][1]["page_start"] == 3
    # Undo snapshot covers every page.
    undo_pages = []
    for u in merged[0]["overlay_undo"]:
        undo_pages.extend(range(u["page_start"], u["page_end"] + 1))
    assert undo_pages == [1, 2, 3]


def test_overlay_does_not_merge_on_gstin():
    pages = [
        {"markdown": "GSTIN: 22AAAAA0000A1Z5\nHello", "blocks": []},
        {"markdown": "GSTIN: 22AAAAA0000A1Z5\nWorld", "blocks": []},
    ]
    segs = [
        {
            "page_start": 1,
            "page_end": 1,
            "doc_type": "invoice",
            "vendor": "A",
            "document_number": "22AAAAA0000A1Z5",
        },
        {
            "page_start": 2,
            "page_end": 2,
            "doc_type": "invoice",
            "vendor": "A",
            "document_number": "22AAAAA0000A1Z5",
        },
    ]
    # Grounded GSTIN would merge if we used it as an invoice number — the
    # overlay only uses the slot the judge/naming already filled. This test
    # documents that we still require the slot; we do not scan for GSTIN.
    # If the slot IS a GSTIN (judge hallucination), grounding would allow a
    # merge. We refuse ungrounded inventions only. Empty slot → no merge:
    empty = [
        {"page_start": 1, "page_end": 1, "doc_type": "invoice"},
        {"page_start": 2, "page_end": 2, "doc_type": "invoice"},
    ]
    merged, report = apply_judge_overlay(empty, pages)
    assert len(merged) == 2
    assert report.merges == []
    assert overlay_number(empty[0], pages) == ""


def test_overlay_ungrounded_number_never_merges():
    pages = [
        {"markdown": "Vendor A\nInvoice 111", "blocks": []},
        {"markdown": "Vendor A\nInvoice 111", "blocks": []},
    ]
    segs = [
        {
            "page_start": 1,
            "page_end": 1,
            "vendor": "Vendor A",
            "document_number": "ZZ-NOT-ON-PAGE",
        },
        {
            "page_start": 2,
            "page_end": 2,
            "vendor": "Vendor A",
            "document_number": "ZZ-NOT-ON-PAGE",
        },
    ]
    merged, report = apply_judge_overlay(segs, pages)
    assert len(merged) == 2
    assert report.merges == []


def test_vendor_empty_suggests_not_merges():
    pages = [
        {"markdown": "# TAX INVOICE\nINV-1\n", "blocks": []},
        {"markdown": "# TAX INVOICE\nINV-1\n", "blocks": []},
    ]
    segs = [
        {
            "page_start": 1,
            "page_end": 1,
            "vendor": "",
            "document_number": "INV-1",
        },
        {
            "page_start": 2,
            "page_end": 2,
            "vendor": "",
            "document_number": "INV-1",
        },
    ]
    # Letterhead fallback skips TAX INVOICE → empty vendor → suggest.
    out, report = apply_judge_overlay(segs, pages)
    assert len(out) == 2
    assert report.merges == []
    assert len(report.suggestions) == 1
    assert out[1]["overlay_tier"] == "suggest"


def test_ibc_group_letterhead_variation_is_a_match():
    left_md = "Century Galaxy Developers Limited\nNumber EB/20-21/04946"
    right_md = (
        "IBC Group\nCentury Galaxy Developers Limited\nNumber EB/20-21/04946"
    )
    rel = vendors_relation(
        "Century Galaxy Developers Limited",
        "IBC Group",
        [left_md],
        [right_md],
    )
    assert rel == "match"


def test_pkt_b06_stays_two_blocked_by_vendor_not_page_of_n():
    from pdfsplit.boundary_benchmark import pages_from_pdf_text

    pages = pages_from_pdf_text(B06)
    # Force the over-merge shape: two segs, same number, different vendors.
    segs = [
        {
            "page_start": 1,
            "page_end": 2,
            "doc_type": "invoice",
            "vendor": "ZZ TEST Duplo First",
            "document_number": "INV-2026-9999",
        },
        {
            "page_start": 3,
            "page_end": 4,
            "doc_type": "invoice",
            "vendor": "ZZ TEST Duplo Second",
            "document_number": "INV-2026-9999",
        },
    ]
    pair = classify_adjacent_pair(segs[0], segs[1], pages)
    assert pair is not None
    assert pair.tier == "blocked"
    assert pair.vendor_relation == "differ"
    out, report = apply_judge_overlay(segs, pages)
    assert len(out) == 2
    assert out[0]["page_end"] == 2 and out[1]["page_start"] == 3
    assert report.merges == []
    assert len(report.blocked) == 1
    # The refusal is vendor mismatch, not Page-1-of-N.
    assert "Page 1 of" not in report.blocked[0].reason
    assert "Duplo First" in report.blocked[0].reason
    assert "Duplo Second" in report.blocked[0].reason


def test_pkt_b06_pipeline_two_documents():
    pages = pages_from_pdf_text(B06)
    segs, _errors, sigs = run_boundaries_on_pages(
        pages,
        mode="heuristic",
        layer1=True,
        use_signature=False,
        chat=None,
        settings=EMPTY,
    )
    assert len(segs) == 2
    assert (segs[0]["page_start"], segs[0]["page_end"]) == (1, 2)
    assert (segs[1]["page_start"], segs[1]["page_end"]) == (3, 4)
    overlay = segs[0].get("_overlay") or {}
    assert (overlay.get("tier_counts") or {}).get("merge", 0) == 0
    assert (overlay.get("tier_counts") or {}).get("blocked", 0) == 1
    assert sigs.get("overlay_blocked") == 1


def test_letterhead_skips_tax_invoice_heading():
    page = {
        "markdown": "# TAX INVOICE\nZZ Test Components Pvt Ltd\nGSTIN: 22AAAAA0000A1Z5\n"
    }
    assert letterhead_vendor(page) == "ZZ Test Components Pvt Ltd"


def test_overlay_preserves_coverage_on_chain_merge():
    pages = [
        {"markdown": "Acme Co\nINV-9 on this page INV-9", "blocks": []},
        {"markdown": "Acme Co\nINV-9 on this page INV-9", "blocks": []},
        {"markdown": "Acme Co\nINV-9 on this page INV-9", "blocks": []},
    ]
    segs = [
        {
            "page_start": i,
            "page_end": i,
            "vendor": "Acme Co",
            "document_number": "INV-9",
        }
        for i in (1, 2, 3)
    ]
    out, report = apply_judge_overlay(segs, pages)
    assert len(out) == 1
    assert out[0]["page_start"] == 1 and out[0]["page_end"] == 3
    assert len(report.merges) == 2
    covered = []
    for u in out[0]["overlay_undo"]:
        covered.extend(range(u["page_start"], u["page_end"] + 1))
    assert covered == [1, 2, 3]


def test_contract_note_pipeline_one_document():
    pages = _contract_pages()
    from pdfsplit.boundary_constraints import (
        collect_boundary_constraints,
        extract_doc_number_token,
        page_has_transactional_content,
        page_is_closing_continuation,
    )

    assert extract_doc_number_token(pages[0]["markdown"]) == "COMBINED/14531"
    assert extract_doc_number_token(pages[1]["markdown"]) == "COMBINED/14531"
    assert page_has_transactional_content(pages[0]) is True
    assert page_has_transactional_content(pages[1]) is False
    assert page_is_closing_continuation(pages[1]) is True
    raw = collect_boundary_constraints(pages)
    assert any(c.signal == "closing_page" and c.page == 2 for c in raw)
    assert not any(c.signal == "document_complete" and c.page == 2 for c in raw)
    assert any(c.signal == "shared_doc_number" and c.page == 2 for c in raw)

    segs, _errors, sigs = run_boundaries_on_pages(
        pages,
        mode="heuristic",
        layer1=True,
        use_signature=False,
        chat=None,
        settings=EMPTY,
    )
    assert len(segs) == 1
    assert segs[0]["page_start"] == 1 and segs[0]["page_end"] == 2
    assert segs[0].get("document_number") == "COMBINED/14531"
    assert "Invoice" not in (pages[0]["markdown"] + pages[1]["markdown"])
    caught = []
    if sigs.get("closing_page"):
        caught.append("closing_page")
    if sigs.get("shared_doc_number"):
        caught.append("shared_doc_number")
    overlay = segs[0].get("_overlay") or {}
    if int((overlay.get("tier_counts") or {}).get("merge") or 0):
        caught.append("overlay_merge")
    assert "closing_page" in caught
    assert "shared_doc_number" in caught
    # Layer 1 already holds; overlay has no pair to merge.
    assert (overlay.get("tier_counts") or {}).get("merge", 0) == 0


def test_contract_note_overlay_merges_when_fix1_disabled():
    """FIX 1 off: force the document_complete over-split; overlay still glues."""
    from pdfsplit.aia.filenames import attach_names_to_segments
    from pdfsplit.boundary_overlay import apply_judge_overlay

    pages = _contract_pages()
    segs = [
        {"page_start": 1, "page_end": 1, "doc_type": "contract_note"},
        {"page_start": 2, "page_end": 2, "doc_type": "other"},
    ]
    attach_names_to_segments(segs, pages, packet_stem="packet")
    assert segs[0]["document_number"] == "COMBINED/14531"
    assert segs[1]["document_number"] == "COMBINED/14531"
    assert "ZZCL" not in segs[0]["document_number"]
    out, report = apply_judge_overlay(segs, pages)
    assert len(out) == 1
    assert out[0]["page_start"] == 1 and out[0]["page_end"] == 2
    assert len(report.merges) == 1
    assert "COMBINED/14531" in report.merges[0].reason

