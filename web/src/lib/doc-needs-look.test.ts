import { describe, expect, it } from "vitest"

import {
  cutPageForHeldAfter,
  dividerAfterPageForDoc,
  docNeedsLook,
  isHoldClassReason,
  isStartClassReason,
  pruneConfirmedStarts,
} from "./doc-needs-look"
import { deriveDocuments, toggleCut, toggleExclude } from "./split-state"
import type { CutState } from "./split-state"

function b06State(): CutState {
  return {
    pageCount: 4,
    cuts: [1, 3],
    labels: { 1: "invoice", 3: "invoice" },
    excludedPages: [],
  }
}

const reviewStart3 = { 3: "needs_look" as const }
const predictedEnd3 = { 3: 4 }
const b06Reasons = ["hard signals disagreed on this start"]
const b05Reasons = [
  "only the model — no hard signal on this cut",
  "pages 4–5 held with no hard hold signal",
]

describe("reason class", () => {
  it("treats cut wording as start-class and hold wording as hold-class", () => {
    expect(isStartClassReason("hard signals disagreed on this start")).toBe(
      true
    )
    expect(
      isStartClassReason("only the model — no hard signal on this cut")
    ).toBe(true)
    expect(
      isHoldClassReason("pages 4–5 held with no hard hold signal")
    ).toBe(true)
    expect(isHoldClassReason("hard signals disagreed on this start")).toBe(
      false
    )
  })
})

describe("dividerAfterPageForDoc", () => {
  it("points start-class at the start divider, not the internal gap", () => {
    expect(
      dividerAfterPageForDoc({ start_page: 3, end_page: 4 }, undefined, b06Reasons)
    ).toBe(2)
  })

  it("points hold-class at the in-document hold", () => {
    expect(
      dividerAfterPageForDoc(
        { start_page: 4, end_page: 5 },
        undefined,
        b05Reasons
      )
    ).toBe(4)
  })
})

describe("docNeedsLook", () => {
  it("keeps a start-disagreement flag after excluding the trailing page (pkt_b06)", () => {
    const state = b06State()
    const before = deriveDocuments(state).find((d) => d.start_page === 3)
    expect(before?.end_page).toBe(4)
    expect(
      docNeedsLook(
        before!,
        reviewStart3,
        predictedEnd3,
        state.cuts,
        undefined,
        b06Reasons
      )
    ).toBe(true)

    toggleExclude(state, 4)
    const after = deriveDocuments(state).find((d) => d.start_page === 3)
    expect(after?.end_page).toBe(3)
    expect(
      docNeedsLook(
        after!,
        reviewStart3,
        predictedEnd3,
        state.cuts,
        undefined,
        b06Reasons
      )
    ).toBe(true)
  })

  it("does not clear a start-class flag on an internal split", () => {
    const state = b06State()
    toggleCut(state, 4)
    const left = deriveDocuments(state).find((d) => d.start_page === 3)
    expect(left?.end_page).toBe(3)
    expect(
      docNeedsLook(
        left!,
        reviewStart3,
        predictedEnd3,
        state.cuts,
        undefined,
        b06Reasons
      )
    ).toBe(true)
  })

  it("keeps a hold flag after excluding the trailing page (pkt_b05 shape)", () => {
    const state: CutState = {
      pageCount: 5,
      cuts: [1, 4],
      labels: { 1: "invoice", 4: "invoice" },
      excludedPages: [],
    }
    const review = { 4: "needs_look" as const }
    const predictedEnd = { 4: 5 }
    toggleExclude(state, 5)
    const after = deriveDocuments(state).find((d) => d.start_page === 4)
    expect(after?.end_page).toBe(4)
    expect(
      docNeedsLook(
        after!,
        review,
        predictedEnd,
        state.cuts,
        undefined,
        b05Reasons
      )
    ).toBe(true)
  })

  it("clears hold-class when a cut exists at the held divider", () => {
    const state: CutState = {
      pageCount: 5,
      cuts: [1, 4],
      labels: { 1: "invoice", 4: "invoice" },
      excludedPages: [],
    }
    const review = { 4: "needs_look" as const }
    const predictedEnd = { 4: 5 }
    const holdOnly = ["pages 4–5 held with no hard hold signal"]
    const heldAfter = dividerAfterPageForDoc(
      { start_page: 4, end_page: 5 },
      undefined,
      holdOnly
    )
    expect(heldAfter).toBe(4)
    toggleCut(state, cutPageForHeldAfter(heldAfter!))
    const left = deriveDocuments(state).find((d) => d.start_page === 4)
    expect(left?.end_page).toBe(4)
    expect(
      docNeedsLook(
        left!,
        review,
        predictedEnd,
        state.cuts,
        undefined,
        holdOnly
      )
    ).toBe(false)
  })

  it("clears a start-class flag only after acknowledgement", () => {
    const state = b06State()
    const doc = deriveDocuments(state).find((d) => d.start_page === 3)!
    const confirmed = new Set([3])
    expect(
      docNeedsLook(
        doc,
        reviewStart3,
        predictedEnd3,
        state.cuts,
        undefined,
        b06Reasons,
        confirmed
      )
    ).toBe(false)
  })

  it("drops an ack when the start cut is removed, so re-splitting is unconfirmed", () => {
    expect(pruneConfirmedStarts([3], [1, 3])).toEqual([3])
    expect(pruneConfirmedStarts([3], [1])).toEqual([])
    expect(pruneConfirmedStarts([3], [1, 3, 4])).toEqual([3])
  })

  it("falls back to end_page only when no held page is in the predicted span", () => {
    const onePage = { start_page: 9, end_page: 9 }
    expect(dividerAfterPageForDoc(onePage)).toBe(8)
    expect(
      docNeedsLook(onePage, { 9: "needs_look" }, { 9: 9 }, [1, 9])
    ).toBe(true)
    expect(
      docNeedsLook(
        { start_page: 9, end_page: 8 },
        { 9: "needs_look" },
        { 9: 9 },
        [1, 9]
      )
    ).toBe(false)
  })
})
