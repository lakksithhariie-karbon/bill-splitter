import { describe, expect, it } from "vitest"
import { computeFieldCorrections, countUngroundedV2 } from "./api"
import type { ExtractedDocumentV2 } from "./types"
import { emptyFieldValue } from "./types"

function fv(value: string): ReturnType<typeof emptyFieldValue> {
  return { value, source_text: value, pages: [1] }
}

function baseDoc(overrides: Partial<ExtractedDocumentV2> = {}): ExtractedDocumentV2 {
  return {
    page_start: 1,
    page_end: 1,
    doc_type: "invoice",
    confidence: 0.9,
    seller: {
      name: fv("Acme"),
      address: emptyFieldValue(),
      tax_id: emptyFieldValue(),
    },
    buyer: {
      name: emptyFieldValue(),
      address: emptyFieldValue(),
      tax_id: emptyFieldValue(),
    },
    document_id: fv("INV-1"),
    issue_date: emptyFieldValue(),
    due_date: emptyFieldValue(),
    currency: fv("INR"),
    po_number: emptyFieldValue(),
    payment_terms: emptyFieldValue(),
    billing_period: emptyFieldValue(),
    totals: {
      subtotal: emptyFieldValue(),
      tax_lines: [],
      total: fv("100"),
      amount_due: emptyFieldValue(),
    },
    line_items: [
      {
        page: 1,
        description: fv("Widget"),
        quantity: fv("1"),
        unit_price: fv("100"),
        amount: fv("100"),
        ungrounded: [],
      },
    ],
    other_fields: [{ name: "HSN", value: fv("1234") }],
    ungrounded: ["seller.address"],
    ...overrides,
  }
}

describe("V2 field corrections", () => {
  it("diffs by path including line items and other_fields", () => {
    const original = [baseDoc()]
    const live = [
      baseDoc({
        seller: {
          name: fv("Acme Corp"),
          address: emptyFieldValue(),
          tax_id: emptyFieldValue(),
        },
        totals: {
          subtotal: emptyFieldValue(),
          tax_lines: [],
          total: fv("110"),
          amount_due: emptyFieldValue(),
        },
        line_items: [
          {
            page: 1,
            description: fv("Widget"),
            quantity: fv("1"),
            unit_price: fv("100"),
            amount: fv("110"),
            ungrounded: [],
          },
        ],
        other_fields: [{ name: "HSN", value: fv("9999") }],
      }),
    ]
    const corrections = computeFieldCorrections([], [], live, original)
    const fields = corrections.map((c) => c.field).sort()
    expect(fields).toEqual([
      "line_items[0].amount",
      "other_fields[0].value",
      "seller.name",
      "totals.total",
    ])
  })

  it("counts V2 ungrounded header + line cells", () => {
    const doc = baseDoc({
      ungrounded: ["seller.name", "totals.total"],
      line_items: [
        {
          page: 1,
          description: fv("x"),
          quantity: fv("1"),
          unit_price: fv("1"),
          amount: fv("1"),
          ungrounded: ["amount"],
        },
      ],
    })
    expect(countUngroundedV2(doc)).toBe(3)
  })
})
