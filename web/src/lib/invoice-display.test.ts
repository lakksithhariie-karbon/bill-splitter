import { describe, expect, it } from "vitest"
import { isAlreadyPaid } from "./invoice-display"
import type { ExtractedDocumentV2, FieldValue } from "./types"

function fv(value: string): FieldValue {
  return { value, source_text: value, pages: [1] }
}

function emptyFv(): FieldValue {
  return { value: "", source_text: "", pages: [] }
}

function baseDoc(
  totals: ExtractedDocumentV2["totals"]
): ExtractedDocumentV2 {
  return {
    page_start: 1,
    page_end: 1,
    doc_type: "invoice",
    confidence: 1,
    seller: { name: emptyFv(), address: emptyFv(), tax_id: emptyFv() },
    buyer: { name: emptyFv(), address: emptyFv(), tax_id: emptyFv() },
    document_id: emptyFv(),
    issue_date: emptyFv(),
    due_date: emptyFv(),
    currency: fv("INR"),
    po_number: emptyFv(),
    payment_terms: emptyFv(),
    billing_period: emptyFv(),
    totals,
    line_items: [],
    other_fields: [],
    ungrounded: [],
  }
}

describe("isAlreadyPaid", () => {
  it("flags zero amount_due with non-zero total", () => {
    expect(
      isAlreadyPaid(
        baseDoc({
          subtotal: emptyFv(),
          tax_lines: [],
          total: fv("4999.00"),
          amount_due: fv("0.00"),
        })
      )
    ).toBe(true)
  })

  it("does not flag when amount_due matches total", () => {
    expect(
      isAlreadyPaid(
        baseDoc({
          subtotal: emptyFv(),
          tax_lines: [],
          total: fv("100"),
          amount_due: fv("100"),
        })
      )
    ).toBe(false)
  })
})
