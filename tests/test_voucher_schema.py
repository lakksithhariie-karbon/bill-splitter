"""Tests for voucher projection and additive V2 fields."""

from __future__ import annotations

from pdfsplit.extraction import (
    DOCUMENT_V2_JSON_SCHEMA,
    ExtractedDocumentV2,
    FieldValue,
    LineItemV2,
    PartyInfo,
    TaxLineV2,
    TotalsV2,
    apply_grounding_v2,
    document_v2_from_dict,
    to_voucher_payload,
)


def _fv(value: str, pages: list[int] | None = None, source: str | None = None) -> FieldValue:
    return FieldValue(
        value=value,
        source_text=source if source is not None else value,
        pages=pages or [],
    )


def test_document_v2_rehydrates_without_new_fields():
    """Old caches / fixtures omit additive fields — defaults apply."""
    raw = {
        "page_start": 1,
        "page_end": 1,
        "doc_type": "invoice",
        "seller": {"name": "Acme", "address": "", "tax_id": ""},
        "buyer": {"name": "", "address": "", "tax_id": ""},
        "document_id": {"value": "A-1", "source_text": "A-1", "pages": [1]},
        "issue_date": {"value": "", "source_text": "", "pages": []},
        "due_date": {"value": "", "source_text": "", "pages": []},
        "currency": {"value": "", "source_text": "", "pages": []},
        "po_number": {"value": "", "source_text": "", "pages": []},
        "payment_terms": {"value": "", "source_text": "", "pages": []},
        "billing_period": {"value": "", "source_text": "", "pages": []},
        "totals": {
            "subtotal": {"value": "", "source_text": "", "pages": []},
            "tax_lines": [],
            "total": {"value": "10", "source_text": "10", "pages": [1]},
            "amount_due": {"value": "", "source_text": "", "pages": []},
        },
        "line_items": [
            {
                "page": 1,
                "description": {"value": "Widget", "source_text": "Widget", "pages": [1]},
                "quantity": {"value": "1", "source_text": "1", "pages": [1]},
                "unit_price": {"value": "10", "source_text": "10", "pages": [1]},
                "amount": {"value": "10", "source_text": "10", "pages": [1]},
            }
        ],
        "other_fields": [],
    }
    doc = document_v2_from_dict(raw, 1, 1)
    assert doc.narration.value == ""
    assert doc.totals.tds.value == ""
    assert doc.line_items[0].hsn_sac.value == ""
    assert doc.totals.tax_lines == []


def test_schema_lists_new_fields_but_not_required():
    props = DOCUMENT_V2_JSON_SCHEMA["properties"]
    for key in (
        "narration",
        "shipping_address",
        "source_of_supply",
        "destination_of_supply",
        "reverse_charge",
    ):
        assert key in props
        assert key not in DOCUMENT_V2_JSON_SCHEMA["required"]

    totals_props = props["totals"]["properties"]
    assert "tds" in totals_props and "other_taxes" in totals_props
    assert "tds" not in props["totals"]["required"]

    tax_props = props["totals"]["properties"]["tax_lines"]["items"]["properties"]
    assert "kind" in tax_props
    assert "kind" not in props["totals"]["properties"]["tax_lines"]["items"]["required"]

    line_props = props["line_items"]["items"]["properties"]
    for key in ("hsn_sac", "discount"):
        assert key in line_props
        assert key not in props["line_items"]["items"]["required"]
    for key in ("uom", "sku", "item_type"):
        assert key not in line_props
    assert "bank" not in props


def test_grounding_flags_new_header_and_line_paths():
    doc = ExtractedDocumentV2(
        page_start=1,
        page_end=1,
        doc_type="invoice",
        narration=_fv("Paid later", pages=[1], source="NOT ON PAGE"),
        source_of_supply=_fv("Karnataka", pages=[1]),
        line_items=[
            LineItemV2(
                page=1,
                description=_fv("Item", pages=[1]),
                hsn_sac=_fv("9983", pages=[1], source="HSN-MISSING"),
                discount=_fv("0", pages=[1]),
            )
        ],
        totals=TotalsV2(
            tds=_fv("100", pages=[1], source="TDS-X"),
            tax_lines=[
                TaxLineV2(
                    name=_fv("CGST", pages=[1]),
                    kind=_fv("gst", pages=[1], source="kind-missing"),
                )
            ],
        ),
    )
    md = {1: "Item NOS Karnataka CGST 0"}
    grounded = apply_grounding_v2([doc], md)[0]
    assert "narration" in grounded.ungrounded
    assert "totals.tds" in grounded.ungrounded
    # kind is a classification discriminant, not a transcription — never grounded.
    assert "totals.tax_lines[0].kind" not in grounded.ungrounded
    assert "source_of_supply" not in grounded.ungrounded
    assert "hsn_sac" in grounded.line_items[0].ungrounded


def test_to_voucher_payload_mapping():
    doc = ExtractedDocumentV2(
        page_start=2,
        page_end=3,
        doc_type="purchase",
        seller=PartyInfo(
            name=_fv("Vendor Co"),
            tax_id=_fv("29AAAAA0000A1Z5"),
        ),
        document_id=_fv("INV-9"),
        issue_date=_fv("2025-07-08"),
        due_date=_fv("2025-08-08"),
        narration=_fv("Being purchase"),
        source_of_supply=_fv("KA"),
        destination_of_supply=_fv("MH"),
        shipping_address=_fv("Warehouse 1"),
        reverse_charge=_fv("No"),
        currency=_fv("INR"),
        totals=TotalsV2(
            subtotal=_fv("1000"),
            total=_fv("1180"),
            amount_due=_fv("1180"),
            tds=_fv("10"),
            other_taxes=_fv("0"),
            tax_lines=[
                TaxLineV2(
                    name=_fv("CGST"),
                    rate=_fv("9"),
                    amount=_fv("90"),
                    kind=_fv("gst"),
                )
            ],
        ),
        line_items=[
            LineItemV2(
                page=2,
                description=_fv("Widget"),
                quantity=_fv("2"),
                unit_price=_fv("500"),
                amount=_fv("1000"),
                hsn_sac=_fv("8471"),
                discount=_fv("0"),
            )
        ],
    )
    payload = to_voucher_payload(doc)
    assert payload["vendorName"] == "Vendor Co"
    assert payload["gstin"] == "29AAAAA0000A1Z5"
    assert payload["supInvNo"] == "INV-9"
    assert payload["billDate"] == "2025-07-08"
    assert payload["dueDate"] == "2025-08-08"
    assert payload["vchType"] == "purchase"
    assert payload["narration"] == "Being purchase"
    assert payload["sourceOfSupply"] == "KA"
    assert payload["destinationOfSupply"] == "MH"
    assert payload["shippingAddress"] == "Warehouse 1"
    assert payload["isReverseChargeApplied"] == "No"
    assert payload["vchAmt"] == "1180"
    assert payload["tds"] == "10"
    assert payload["otherTaxes"] == "0"
    assert payload["alreadyPaid"] is False
    assert payload["purchase_account"] is None
    assert payload["cost_centre"] is None
    assert payload["godown"] is None
    item = payload["lines"]["items"][0]
    assert item["hsnSac"] == "8471"
    assert "uom" not in item
    assert "skuCode" not in item
    assert "itemType" not in item
    assert payload["lines"]["taxes"][0]["kind"] == "gst"


def test_already_paid_flag():
    from pdfsplit.extraction import is_already_paid

    paid = ExtractedDocumentV2(
        page_start=1,
        page_end=1,
        totals=TotalsV2(
            total=_fv("4999.00"),
            amount_due=_fv("0.00"),
        ),
    )
    assert is_already_paid(paid) is True
    unpaid = ExtractedDocumentV2(
        page_start=1,
        page_end=1,
        totals=TotalsV2(
            total=_fv("4999.00"),
            amount_due=_fv("4999.00"),
        ),
    )
    assert is_already_paid(unpaid) is False
    missing = ExtractedDocumentV2(
        page_start=1,
        page_end=1,
        totals=TotalsV2(total=_fv("100"), amount_due=_fv("")),
    )
    assert is_already_paid(missing) is False


def test_fold_tax_named_line_items():
    from pdfsplit.extraction import fold_tax_named_line_items

    doc = ExtractedDocumentV2(
        page_start=17,
        page_end=17,
        line_items=[
            LineItemV2(page=17, description=_fv("Consulting"), amount=_fv("100")),
            LineItemV2(page=17, description=_fv("GST"), amount=_fv("18")),
            LineItemV2(page=17, description=_fv("TDS"), amount=_fv("10")),
        ],
        totals=TotalsV2(subtotal=_fv("100"), total=_fv("118")),
    )
    out = fold_tax_named_line_items(doc)
    assert len(out.line_items) == 1
    assert out.line_items[0].description.value == "Consulting"
    kinds = [(tl.name.value, tl.kind.value) for tl in out.totals.tax_lines]
    assert ("GST", "gst") in kinds
    assert ("TDS", "tds") in kinds
    assert out.totals.tds.value == "10"


def test_totals_reconciliation_flags_amount_due_in_total():
    from pdfsplit.extraction import (
        apply_totals_reconciliation,
        totals_reconcile,
    )

    bad = ExtractedDocumentV2(
        page_start=23,
        page_end=23,
        totals=TotalsV2(
            subtotal=_fv("4999.00"),
            total=_fv("0.00"),
            amount_due=_fv("0.00"),
        ),
    )
    assert totals_reconcile(bad) is False
    flagged = apply_totals_reconciliation([bad])[0]
    assert flagged.totals_mismatch is True

    good = ExtractedDocumentV2(
        page_start=1,
        page_end=1,
        totals=TotalsV2(
            subtotal=_fv("100.00"),
            tax_lines=[
                TaxLineV2(name=_fv("GST"), amount=_fv("18.00"), kind=_fv("gst"))
            ],
            total=_fv("118.00"),
        ),
    )
    assert totals_reconcile(good) is True
    assert apply_totals_reconciliation([good])[0].totals_mismatch is False



def test_drop_tax_summary_rows():
    from pdfsplit.extraction import drop_tax_summary_rows, totals_reconcile

    doc = ExtractedDocumentV2(
        page_start=26,
        page_end=26,
        totals=TotalsV2(
            subtotal=_fv("577292.53"),
            tax_lines=[
                TaxLineV2(name=_fv("IGST"), amount=_fv("103912.66"), kind=_fv("gst")),
                TaxLineV2(
                    name=_fv("Total GST"), amount=_fv("103912.66"), kind=_fv("gst")
                ),
            ],
            total=_fv("681205.19"),
        ),
    )
    assert totals_reconcile(doc) is False
    cleaned = drop_tax_summary_rows(doc)
    assert len(cleaned.totals.tax_lines) == 1
    assert cleaned.totals.tax_lines[0].name.value == "IGST"
    assert cleaned.tax_summary_rows_dropped
    assert totals_reconcile(cleaned) is True


def test_reconcile_subtracts_discount_not_tds():
    """Discount reduces total; TDS does not (it reduces amount_due)."""
    from pdfsplit.extraction import drop_tax_summary_rows, totals_reconcile

    discounted = ExtractedDocumentV2(
        page_start=18,
        page_end=18,
        totals=TotalsV2(
            subtotal=_fv("130000.00"),
            tax_lines=[
                TaxLineV2(
                    name=_fv("Discount"), amount=_fv("3900.00"), kind=_fv("discount")
                )
            ],
            total=_fv("126100.00"),
        ),
    )
    assert totals_reconcile(discounted) is True

    # Equal CGST/SGST must not be treated as a summary of each other.
    cgst_sgst = ExtractedDocumentV2(
        page_start=6,
        page_end=6,
        totals=TotalsV2(
            subtotal=_fv("50000.00"),
            tax_lines=[
                TaxLineV2(name=_fv("CGST @ 9%"), amount=_fv("4500.00"), kind=_fv("gst")),
                TaxLineV2(name=_fv("SGST @ 9%"), amount=_fv("4500.00"), kind=_fv("gst")),
                TaxLineV2(name=_fv("TDS"), amount=_fv("3750.00"), kind=_fv("tds")),
            ],
            tds=_fv("3750.00"),
            total=_fv("59000.00"),
            amount_due=_fv("55250.00"),
        ),
    )
    assert len(drop_tax_summary_rows(cgst_sgst).totals.tax_lines) == 3
    assert totals_reconcile(cgst_sgst) is True


def test_to_voucher_payload_vch_type_null_when_unaligned():
    doc = ExtractedDocumentV2(page_start=1, page_end=1, doc_type="tax_invoice")
    assert to_voucher_payload(doc)["vchType"] is None
    assert to_voucher_payload(doc)["totalsMismatch"] is False

def test_to_voucher_payload_vch_type_collapses_separators():
    """Live TS export collapsed whitespace/hyphen runs; Python matches that."""
    doc = ExtractedDocumentV2(page_start=1, page_end=1, doc_type="debit  note")
    assert to_voucher_payload(doc)["vchType"] == "debit  note"
    doc2 = ExtractedDocumentV2(page_start=1, page_end=1, doc_type="Debit-Note")
    assert to_voucher_payload(doc2)["vchType"] == "Debit-Note"
