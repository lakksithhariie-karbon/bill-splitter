import { describe, expect, it } from "vitest"
import {
  addCut,
  acceptMergeSuggestion,
  computeCorrectionTypes,
  deriveDocuments,
  projectMonthlyCost,
  projectMonthlyDollars,
  removeCut,
  seedFromDocuments,
  toggleCut,
  undoOverlayMerge,
  type CutState,
} from "./split-state"

function makeState(
  pageCount: number,
  cuts: number[],
  labels: Record<number, string>
): CutState {
  return {
    pageCount,
    cuts: [...cuts],
    labels: { ...labels },
    excludedPages: [],
    displayNames: {},
    vendorNames: {},
    invoiceNumbers: {},
    vendorEdited: {},
    numberEdited: {},
    predicted: [],
  }
}

describe("split-state", () => {
  it("addCutBeforeExistingDocumentKeepsLabels", () => {
    const st = makeState(12, [1, 4, 8], {
      1: "invoice",
      4: "bank_statement",
      8: "report",
    })
    addCut(st, 2)
    const docs = deriveDocuments(st)
    expect(docs.map((d) => [d.start_page, d.end_page, d.doc_type])).toEqual([
      [1, 1, "invoice"],
      [2, 3, "invoice"],
      [4, 7, "bank_statement"],
      [8, 12, "report"],
    ])
  })

  it("addCutInheritsParentClass", () => {
    const st = makeState(10, [1, 6], { 1: "contract", 6: "form" })
    addCut(st, 3)
    const docs = deriveDocuments(st)
    expect(docs[1].doc_type).toBe("contract")
    expect(docs[1].start_page).toBe(3)
  })

  it("removeCutMergesAndDeletesLabel", () => {
    const st = makeState(12, [1, 4, 8], {
      1: "invoice",
      4: "bank_statement",
      8: "report",
    })
    removeCut(st, 4)
    const docs = deriveDocuments(st)
    expect(docs.map((d) => [d.start_page, d.end_page, d.doc_type])).toEqual([
      [1, 7, "invoice"],
      [8, 12, "report"],
    ])
    expect(st.labels[4]).toBeUndefined()
  })

  it("pageOneCutNeverRemoved", () => {
    const st = makeState(5, [1, 3], { 1: "a", 3: "b" })
    removeCut(st, 1)
    expect(st.cuts).toEqual([1, 3])
  })

  it("toggleCut", () => {
    const st = makeState(6, [1], { 1: "x" })
    toggleCut(st, 3)
    expect(st.cuts).toEqual([1, 3])
    toggleCut(st, 3)
    expect(st.cuts).toEqual([1])
  })

  it("computeCorrectionTypes", () => {
    const st = makeState(12, [1, 4, 8], {
      1: "invoice",
      4: "bank_statement",
      8: "report",
    })
    st.predicted = [
      { start_page: 1, end_page: 3, doc_type: "invoice" },
      { start_page: 4, end_page: 7, doc_type: "bank_statement" },
      { start_page: 8, end_page: 12, doc_type: "report" },
    ]
    const corrected = [
      {
        start_page: 1,
        end_page: 3,
        doc_type: "invoice",
        confidence: 0,
        display_name: "Bill 01",
        vendor: "",
        invoice_number: "",
      },
      {
        start_page: 4,
        end_page: 7,
        doc_type: "statement",
        confidence: 0,
        display_name: "Bill 02",
        vendor: "",
        invoice_number: "",
      },
      {
        start_page: 8,
        end_page: 12,
        doc_type: "report",
        confidence: 0,
        display_name: "Bill 03",
        vendor: "",
        invoice_number: "",
      },
    ]
    expect(computeCorrectionTypes(st, corrected)).toEqual([
      "BOUNDARY",
      "CLASS",
      "BOUNDARY",
    ])
  })

  it("seedFromDocumentsDeduplicatesPageOne", () => {
    const st = makeState(0, [], {})
    const docs = [
      { start_page: 1, end_page: 3, doc_type: "invoice" },
      { start_page: 4, end_page: 7, doc_type: "bank_statement" },
      { start_page: 8, end_page: 12, doc_type: "report" },
    ]
    seedFromDocuments(st, docs, 12)
    expect(st.cuts).toEqual([1, 4, 8])
    const derived = deriveDocuments(st)
    for (const d of derived) {
      expect(d.end_page).toBeGreaterThanOrEqual(d.start_page)
    }
    expect(derived.map((d) => [d.start_page, d.end_page, d.doc_type])).toEqual([
      [1, 3, "invoice"],
      [4, 7, "bank_statement"],
      [8, 12, "report"],
    ])
  })

  it("seedFromDocumentsEmpty", () => {
    const st = makeState(0, [], {})
    seedFromDocuments(st, [], 12)
    expect(st.cuts).toEqual([1])
    expect(deriveDocuments(st).map((d) => [d.start_page, d.end_page])).toEqual([
      [1, 12],
    ])
  })

  it("deriveDocumentsToleratesDuplicatePageOneCut", () => {
    // Historical bug: duplicate cut at 1 produced end < start. Walk-based
    // deriveDocuments treats cuts as a set, so this is a no-op.
    const st = makeState(12, [1, 1, 4, 8], {
      1: "invoice",
      4: "bank",
      8: "report",
    })
    expect(
      deriveDocuments(st).map((d) => [d.start_page, d.end_page, d.doc_type])
    ).toEqual([
      [1, 3, "invoice"],
      [4, 7, "bank"],
      [8, 12, "report"],
    ])
  })

  it("projectMonthlyCostUsesUploadsNotPages", () => {
    const proj = projectMonthlyCost(1.0, 29, 0.0125, 10000)
    expect(proj?.monthlyCredits).toBe(290000)
    expect(proj?.monthlyUsd).toBe(3625)
  })

  it("projectMonthlyCostRejectsBadInput", () => {
    expect(projectMonthlyCost(0, 29, 0.0125, 10000)).toBeNull()
    expect(projectMonthlyCost(1.0, 0, 0.0125, 10000)).toBeNull()
  })

  it("projectMonthlyDollarsUsesUploadsNotPages", () => {
    const proj = projectMonthlyDollars(12, 0.005, 10000)
    expect(proj?.pagesPerMonth).toBe(120000)
    expect(proj?.monthlyUsd).toBe(600)
  })

  it("projectMonthlyDollarsRejectsBadInput", () => {
    expect(projectMonthlyDollars(0, 0.005, 10000)).toBeNull()
    expect(projectMonthlyDollars(12, 0, 10000)).toBeNull()
  })

  it("cutOpsRejectInvalidInput", () => {
    const bad = [1.5, "2", {}, null, undefined, 0, 13]
    for (const op of [addCut, removeCut, toggleCut]) {
      for (const page of bad) {
        const st = makeState(12, [1, 4, 8], {
          1: "invoice",
          4: "bank",
          8: "report",
        })
        const before = JSON.stringify(st.cuts)
        op(st, page)
        expect(JSON.stringify(st.cuts)).toBe(before)
      }
    }
  })

  it("cutOpsStillAcceptValidInput", () => {
    const st = makeState(12, [1, 4, 8], {
      1: "invoice",
      4: "bank",
      8: "report",
    })
    addCut(st, 2)
    expect(st.cuts).toEqual([1, 2, 4, 8])
    removeCut(st, 2)
    expect(st.cuts).toEqual([1, 4, 8])
  })

  it("undoOverlayMergeRestoresCutsAndNames", () => {
    const st = makeState(3, [1], { 1: "invoice" })
    st.vendorNames = { 1: "Acme" }
    st.invoiceNumbers = { 1: "ZZCO/8801/26-27" }
    undoOverlayMerge(st, [
      {
        page_start: 1,
        page_end: 2,
        doc_type: "invoice",
        vendor: "Acme",
        document_number: "ZZCO/8801/26-27",
      },
      {
        page_start: 3,
        page_end: 3,
        doc_type: "other",
        vendor: "Acme",
        document_number: "ZZCO/8801/26-27",
      },
    ])
    expect(st.cuts).toEqual([1, 3])
    expect(st.labels[3]).toBe("other")
    expect(st.vendorNames?.[3]).toBe("Acme")
    expect(st.invoiceNumbers?.[3]).toBe("ZZCO/8801/26-27")
    const docs = deriveDocuments(st)
    expect(docs.map((d) => [d.start_page, d.end_page])).toEqual([
      [1, 2],
      [3, 3],
    ])
  })

  it("acceptMergeSuggestionRemovesCutAndKeepsCoverage", () => {
    const st = makeState(3, [1, 3], { 1: "invoice", 3: "invoice" })
    acceptMergeSuggestion(st, 3)
    expect(st.cuts).toEqual([1])
    expect(deriveDocuments(st).map((d) => [d.start_page, d.end_page])).toEqual([
      [1, 3],
    ])
  })
})
