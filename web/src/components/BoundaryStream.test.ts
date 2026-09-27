import { describe, expect, it } from "vitest"
import {
  boundaryKind,
  cutPageForBoundary,
} from "@/components/BoundaryStream"
import {
  deriveDocuments,
  excludePage,
  seedFromDocuments,
  setDisplayName,
  toggleCut,
  type CutState,
} from "@/lib/split-state"

describe("boundary ↔ cut mapping", () => {
  it("maps boundary index i to cut at page i+2", () => {
    // Prototype: splits at indices 1, 2, 5 → cuts at 3, 4, 7
    expect([1, 2, 5].map(cutPageForBoundary)).toEqual([3, 4, 7])
  })

  it("deriveDocuments matches prototype groups for cuts [1,3,4,7]", () => {
    const state: CutState = {
      pageCount: 8,
      cuts: [],
      labels: {},
      excludedPages: [],
      displayNames: {},
    }
    seedFromDocuments(
      state,
      [
        { start_page: 1, doc_type: "invoice" },
        { start_page: 3, doc_type: "invoice" },
        { start_page: 4, doc_type: "invoice" },
        { start_page: 7, doc_type: "invoice" },
      ],
      8
    )
    expect(state.cuts).toEqual([1, 3, 4, 7])
    expect(
      deriveDocuments(state).map((d) => [d.start_page, d.end_page])
    ).toEqual([
      [1, 2],
      [3, 3],
      [4, 6],
      [7, 8],
    ])
  })

  it("classifies overlay-merged ahead of ai-removed", () => {
    const predicted = new Set([3])
    const cuts: number[] = [1]
    const merged = new Set([3])
    expect(boundaryKind(1, cuts, predicted, merged)).toBe("overlay-merged")
    expect(boundaryKind(1, [1, 3], predicted, merged)).toBe("ai-kept")
  })

  it("classifies four boundary states from predicted vs current cuts", () => {
    const predicted = new Set([3, 4, 7]) // AI suggested at boundaries 1, 2, 5
    const cuts = [1, 3, 7] // kept 3 & 7; removed 4; user could add later

    expect(boundaryKind(1, cuts, predicted)).toBe("ai-kept") // cut 3
    expect(boundaryKind(2, cuts, predicted)).toBe("ai-removed") // cut 4 gone
    expect(boundaryKind(5, cuts, predicted)).toBe("ai-kept") // cut 7
    expect(boundaryKind(0, cuts, predicted)).toBe("untouched")

    const withUser = [...cuts, 2] // user added cut at page 2 (boundary 0)
    expect(boundaryKind(0, withUser, predicted)).toBe("user-added")
  })
})

describe("excluded pages", () => {
  function fresh(): CutState {
    const state: CutState = {
      pageCount: 5,
      cuts: [],
      labels: {},
      excludedPages: [],
      displayNames: {},
    }
    seedFromDocuments(state, [{ start_page: 1, doc_type: "invoice" }], 5)
    return state
  }

  it("excluding every page yields no documents", () => {
    const state = fresh()
    for (let p = 1; p <= 5; p++) excludePage(state, p)
    expect(deriveDocuments(state)).toEqual([])
    expect(state.excludedPages).toEqual([1, 2, 3, 4, 5])
  })

  it("excluding first or last page trims the run", () => {
    const state = fresh()
    excludePage(state, 1)
    expect(deriveDocuments(state).map((d) => [d.start_page, d.end_page])).toEqual([
      [2, 5],
    ])
    excludePage(state, 5)
    expect(deriveDocuments(state).map((d) => [d.start_page, d.end_page])).toEqual([
      [2, 4],
    ])
  })

  it("excluding a middle page splits the invoice in two", () => {
    const state = fresh()
    excludePage(state, 3)
    expect(deriveDocuments(state).map((d) => [d.start_page, d.end_page])).toEqual([
      [1, 2],
      [4, 5],
    ])
  })

  it("excluding a cut page removes the boundary", () => {
    const state = fresh()
    toggleCut(state, 3)
    expect(state.cuts).toContain(3)
    excludePage(state, 3)
    expect(state.cuts).not.toContain(3)
    expect(state.excludedPages).toContain(3)
  })
})

describe("display names", () => {
  it("defaults to Bill NN and does not touch labels/doc_type", () => {
    const state: CutState = {
      pageCount: 4,
      cuts: [],
      labels: {},
      excludedPages: [],
      displayNames: {},
      vendorNames: {},
      invoiceNumbers: {},
      vendorEdited: {},
      numberEdited: {},
    }
    seedFromDocuments(
      state,
      [
        { start_page: 1, doc_type: "purchase" },
        { start_page: 3, doc_type: "invoice" },
      ],
      4
    )
    setDisplayName(state, 1, "Freshworks Sept")
    const docs = deriveDocuments(state)
    expect(docs[0].display_name).toBe("Freshworks Sept")
    expect(docs[0].doc_type).toBe("purchase")
    expect(docs[1].display_name).toBe("Bill 02")
    expect(state.labels[1]).toBe("purchase")
  })

  it("preserves vendor edits across re-analyze", () => {
    const state: CutState = {
      pageCount: 2,
      cuts: [1],
      labels: { 1: "invoice" },
      excludedPages: [],
      displayNames: {},
      vendorNames: {},
      invoiceNumbers: {},
      vendorEdited: {},
      numberEdited: {},
    }
    seedFromDocuments(
      state,
      [{ start_page: 1, doc_type: "invoice", vendor: "Auto", document_number: "A-1" }],
      2
    )
    setDisplayName(state, 1, "User Vendor")
    seedFromDocuments(
      state,
      [
        {
          start_page: 1,
          doc_type: "invoice",
          vendor: "Reanalyze Vendor",
          document_number: "B-2",
        },
      ],
      2
    )
    expect(state.vendorNames?.[1]).toBe("User Vendor")
    expect(state.invoiceNumbers?.[1]).toBe("B-2")
  })
})
