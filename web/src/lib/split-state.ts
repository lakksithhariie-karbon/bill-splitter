/**
 * Pure state logic for the boundary editor (Approach A: cuts as the single
 * source of truth for boundaries). Documents are derived from cuts + exclusions.
 *
 * state shape:
 *   cuts:            ordered set of page numbers where a new document begins,
 *                    always containing 1 (even if page 1 is excluded).
 *   labels:          map from start page -> doc_type (feeds voucher vchType).
 *   excludedPages:   pages deliberately dropped from every bill.
 *   vendorNames:     map from start page -> vendor slot (AIA filename).
 *   invoiceNumbers:  map from start page -> invoice-number slot.
 *   fileNames:       map from start page -> editable PDF filename stem.
 *   vendorEdited:    start pages the user edited — never overwritten by analyze.
 *   numberEdited:    same for invoice numbers.
 *   displayNames:    legacy alias — prefer vendorNames; kept for older tests.
 *   pageCount:       total pages in the upload.
 *   predicted:       provider's predicted documents (for correction classification).
 *
 * Coverage invariant: documents ∪ excludedPages cover pages 1..N exactly.
 */

export type PredictedDoc = {
  start_page: number
  end_page: number
  doc_type: string
  confidence?: number
  review_status?: "checked" | "needs_look"
  review_reasons?: string[]
  vendor?: string
  document_number?: string
  vendor_source?: string
  number_source?: string
}

export type DerivedDoc = {
  start_page: number
  end_page: number
  doc_type: string
  confidence: number
  /** Reviewer-facing title (vendor || number || Bill NN). */
  display_name: string
  /** Editable filename stem used for the PDF created for this document. */
  file_name: string
  vendor: string
  invoice_number: string
}

export type CutState = {
  pageCount: number
  cuts: number[]
  labels: Record<number, string>
  excludedPages: number[]
  vendorNames?: Record<number, string>
  invoiceNumbers?: Record<number, string>
  fileNames?: Record<number, string>
  fileNameEdited?: Record<number, boolean>
  vendorEdited?: Record<number, boolean>
  numberEdited?: Record<number, boolean>
  /** @deprecated Prefer vendorNames — kept for older call sites / tests. */
  displayNames?: Record<number, string>
  predicted?: PredictedDoc[]
}

export function defaultDisplayName(index: number): string {
  return `Bill ${String(index + 1).padStart(2, "0")}`
}

export function sortedUniquePages(pages: Iterable<number>, pageCount: number): number[] {
  const set = new Set<number>()
  for (const p of pages) {
    if (Number.isInteger(p) && p >= 1 && p <= pageCount) set.add(p)
  }
  return Array.from(set).sort((a, b) => a - b)
}

/** Derive documents from cuts + labels + exclusions. */
export function deriveDocuments(state: CutState): DerivedDoc[] {
  const excluded = new Set(state.excludedPages || [])
  const cutSet = new Set(state.cuts)
  const docs: DerivedDoc[] = []
  let currentStart: number | null = null
  let lastIncluded: number | null = null

  for (let p = 1; p <= state.pageCount; p++) {
    if (excluded.has(p)) {
      if (currentStart !== null && lastIncluded !== null) {
        docs.push(_makeDoc(state, docs.length, currentStart, lastIncluded))
        currentStart = null
        lastIncluded = null
      }
      continue
    }
    if (currentStart === null) {
      currentStart = p
    } else if (cutSet.has(p)) {
      docs.push(_makeDoc(state, docs.length, currentStart, lastIncluded!))
      currentStart = p
    }
    lastIncluded = p
  }
  if (currentStart !== null && lastIncluded !== null) {
    docs.push(_makeDoc(state, docs.length, currentStart, lastIncluded))
  }
  return docs
}

function _makeDoc(
  state: CutState,
  index: number,
  start: number,
  end: number
): DerivedDoc {
  if (end < start) {
    throw new Error(
      `deriveDocuments: invalid range p${start}..p${end} (end_page < start_page). ` +
        "This is a bug in the cut/exclude state."
    )
  }
  const cls = state.labels[start] || "unknown"
  const vendor =
    (state.vendorNames && state.vendorNames[start]?.trim()) ||
    (state.displayNames && state.displayNames[start]?.trim()) ||
    ""
  const invoice_number =
    (state.invoiceNumbers && state.invoiceNumbers[start]?.trim()) || ""
  const display = vendor || invoice_number || defaultDisplayName(index)
  const fileName = state.fileNameEdited?.[start]
    ? state.fileNames?.[start] ?? ""
    : defaultDocumentFileName(vendor, invoice_number, index)
  return {
    start_page: start,
    end_page: end,
    doc_type: cls,
    confidence: 0.0,
    display_name: display,
    file_name: fileName,
    vendor,
    invoice_number,
  }
}

function defaultDocumentFileName(
  vendor: string,
  invoiceNumber: string,
  index: number
): string {
  const vendorCode = Array.from(vendor.replace(/[^\p{L}]/gu, ""))
    .slice(0, 3)
    .join("")
    .toUpperCase()
  const billReference =
    invoiceNumber || `Bill-${String(index + 1).padStart(2, "0")}`
  return [vendorCode, billReference].filter(Boolean).join("-")
}

/**
 * Pages that are neither in a derived document nor excluded — must be empty
 * when the invariant holds. Useful for UI banners.
 */
export function uncoveredPages(state: CutState): number[] {
  const covered = new Set<number>(state.excludedPages || [])
  for (const d of deriveDocuments(state)) {
    for (let p = d.start_page; p <= d.end_page; p++) covered.add(p)
  }
  const missing: number[] = []
  for (let p = 1; p <= state.pageCount; p++) {
    if (!covered.has(p)) missing.push(p)
  }
  return missing
}

/**
 * The only supported way to initialise cuts from a provider's predicted
 * documents. Guarantees page 1 appears exactly once.
 *
 * User-edited vendor/number slots survive re-analyze (edit wins).
 */
export function seedFromDocuments(
  state: CutState,
  documents:
    | Array<{
        start_page?: number
        doc_type?: string
        vendor?: string
        document_number?: string
      }>
    | null
    | undefined,
  pageCount: number
): number[] {
  state.pageCount = pageCount
  const starts = new Set<number>([1])
  for (const d of documents || []) {
    const s = Number(d.start_page)
    if (Number.isInteger(s) && s >= 1 && s <= pageCount) starts.add(s)
  }
  state.cuts = Array.from(starts).sort((a, b) => a - b)
  state.labels = {}
  for (const d of documents || []) {
    const s = Number(d.start_page)
    if (Number.isInteger(s) && s >= 1 && s <= pageCount) {
      state.labels[s] = d.doc_type || "unknown"
    }
  }
  if (!(1 in state.labels)) state.labels[1] = "unknown"
  // Keep reviewer exclusions across re-analyze; drop out-of-range pages.
  state.excludedPages = sortedUniquePages(
    state.excludedPages || [],
    pageCount
  )
  // An excluded page cannot also carry a boundary (except the page-1 sentinel).
  state.cuts = state.cuts.filter(
    (c) => c === 1 || !(state.excludedPages || []).includes(c)
  )
  for (const p of state.excludedPages || []) {
    if (p !== 1) delete state.labels[p]
  }

  if (!state.vendorNames) state.vendorNames = {}
  if (!state.invoiceNumbers) state.invoiceNumbers = {}
  if (!state.fileNames) state.fileNames = {}
  if (!state.fileNameEdited) state.fileNameEdited = {}
  if (!state.vendorEdited) state.vendorEdited = {}
  if (!state.numberEdited) state.numberEdited = {}
  if (!state.displayNames) state.displayNames = {}

  const nextVendor: Record<number, string> = {}
  const nextNumber: Record<number, string> = {}
  const nextFileNames: Record<number, string> = {}
  const nextFileNameEdited: Record<number, boolean> = {}
  const nextVendorEdited: Record<number, boolean> = {}
  const nextNumberEdited: Record<number, boolean> = {}
  const byStart = new Map<
    number,
    {
      start_page?: number
      doc_type?: string
      vendor?: string
      document_number?: string
    }
  >()
  for (const d of documents || []) {
    const s = Number(d.start_page)
    if (Number.isInteger(s)) byStart.set(s, d)
  }
  for (const c of state.cuts) {
    if (state.fileNameEdited?.[c]) {
      nextFileNameEdited[c] = true
      if (c in (state.fileNames || {})) nextFileNames[c] = state.fileNames![c]
    }
    if (state.vendorEdited?.[c]) {
      nextVendorEdited[c] = true
      if (state.vendorNames?.[c]) nextVendor[c] = state.vendorNames[c]
      else if (state.displayNames?.[c]) nextVendor[c] = state.displayNames[c]
    } else {
      const hint = byStart.get(c)
      const v = (hint?.vendor || "").trim()
      if (v) nextVendor[c] = v
    }
    if (state.numberEdited?.[c]) {
      nextNumberEdited[c] = true
      if (state.invoiceNumbers?.[c]) nextNumber[c] = state.invoiceNumbers[c]
    } else {
      const hint = byStart.get(c)
      const n = (hint?.document_number || "").trim()
      if (n) nextNumber[c] = n
    }
  }
  state.vendorNames = nextVendor
  state.invoiceNumbers = nextNumber
  state.fileNames = nextFileNames
  state.fileNameEdited = nextFileNameEdited
  state.vendorEdited = nextVendorEdited
  state.numberEdited = nextNumberEdited
  state.displayNames = { ...nextVendor }
  return state.cuts
}

function isValidCutPage(state: CutState, page: unknown): page is number {
  return (
    typeof page === "number" &&
    Number.isInteger(page) &&
    page >= 1 &&
    page <= state.pageCount
  )
}

/** Add a cut at page P; labels[P] inherits its parent's class. No-op if excluded. */
export function addCut(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  if ((state.excludedPages || []).includes(page)) return
  if (state.cuts.includes(page)) return
  let s = 1
  for (const c of state.cuts) {
    if (c <= page) s = c
    else break
  }
  state.cuts.push(page)
  state.cuts.sort((a, b) => a - b)
  state.labels[page] = state.labels[s] || "unknown"
}

/** Remove the cut at page P. Page 1 can never be removed. */
export function removeCut(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  if (page === 1) return
  if (!state.cuts.includes(page)) return
  state.cuts = state.cuts.filter((c) => c !== page)
  delete state.labels[page]
  if (state.displayNames) delete state.displayNames[page]
  if (state.vendorNames) delete state.vendorNames[page]
  if (state.invoiceNumbers) delete state.invoiceNumbers[page]
  if (state.fileNames) delete state.fileNames[page]
  if (state.fileNameEdited) delete state.fileNameEdited[page]
  if (state.vendorEdited) delete state.vendorEdited[page]
  if (state.numberEdited) delete state.numberEdited[page]
}

export function acceptMergeSuggestion(state: CutState, rightStart: unknown): void {
  removeCut(state, rightStart)
}

export type OverlayUndoSeg = {
  page_start: number
  page_end: number
  doc_type?: string
  vendor?: string
  document_number?: string
}

/** Restore the exact pre-merge ranges and names. Exclusions are untouched. */
export function undoOverlayMerge(
  state: CutState,
  undo: OverlayUndoSeg[] | null | undefined
): void {
  if (!undo || undo.length === 0) return
  if (!state.vendorNames) state.vendorNames = {}
  if (!state.invoiceNumbers) state.invoiceNumbers = {}
  if (!state.displayNames) state.displayNames = {}
  for (const seg of undo) {
    const s = Number(seg.page_start)
    if (!Number.isInteger(s) || s < 1 || s > state.pageCount) continue
    if (s > 1 && !state.cuts.includes(s)) addCut(state, s)
    if (seg.doc_type) state.labels[s] = seg.doc_type
    const vendor = (seg.vendor || "").trim()
    if (vendor) {
      state.vendorNames[s] = vendor
      state.displayNames[s] = vendor
    }
    const number = (seg.document_number || "").trim()
    if (number) state.invoiceNumbers[s] = number
  }
}

export function toggleCut(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  if ((state.excludedPages || []).includes(page)) return
  if (state.cuts.includes(page)) removeCut(state, page)
  else addCut(state, page)
}

/** Exclude page P from every bill. Removes any cut at P (except page-1). */
export function excludePage(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  if ((state.excludedPages || []).includes(page)) return
  state.excludedPages = sortedUniquePages(
    [...(state.excludedPages || []), page],
    state.pageCount
  )
  // An excluded page cannot also carry a boundary.
  if (page !== 1 && state.cuts.includes(page)) {
    removeCut(state, page)
  }
}

export function includePage(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  state.excludedPages = (state.excludedPages || []).filter((p) => p !== page)
}

export function toggleExclude(state: CutState, page: unknown): void {
  if (!isValidCutPage(state, page)) return
  if ((state.excludedPages || []).includes(page)) includePage(state, page)
  else excludePage(state, page)
}

/** Set reviewer display name — alias for vendor slot (legacy). */
export function setDisplayName(
  state: CutState,
  startPage: number,
  name: string
): void {
  setVendorName(state, startPage, name)
}

/** Editable vendor slot — marks the start page as user-edited. */
export function setVendorName(
  state: CutState,
  startPage: number,
  name: string
): void {
  if (!state.vendorNames) state.vendorNames = {}
  if (!state.vendorEdited) state.vendorEdited = {}
  if (!state.displayNames) state.displayNames = {}
  const trimmed = name.trim()
  state.vendorEdited[startPage] = true
  if (!trimmed) {
    delete state.vendorNames[startPage]
    delete state.displayNames[startPage]
    return
  }
  state.vendorNames[startPage] = trimmed
  state.displayNames[startPage] = trimmed
}

/** Editable invoice-number slot — marks the start page as user-edited. */
export function setInvoiceNumber(
  state: CutState,
  startPage: number,
  name: string
): void {
  if (!state.invoiceNumbers) state.invoiceNumbers = {}
  if (!state.numberEdited) state.numberEdited = {}
  const trimmed = name.trim()
  state.numberEdited[startPage] = true
  if (!trimmed) {
    delete state.invoiceNumbers[startPage]
    return
  }
  state.invoiceNumbers[startPage] = trimmed
}

/** Set the editable output PDF filename for a document. */
export function setDocumentFileName(
  state: CutState,
  startPage: number,
  name: string
): void {
  if (!state.fileNames) state.fileNames = {}
  if (!state.fileNameEdited) state.fileNameEdited = {}
  state.fileNames[startPage] = name
  state.fileNameEdited[startPage] = true
}

/**
 * Classify each corrected doc as BOUNDARY (cut moved/added/removed) or CLASS
 * (label changed on an unchanged range) against the provider's prediction.
 * Appends one EXCLUDE entry per excluded page.
 */
export function computeCorrectionTypes(
  state: CutState,
  corrected: Array<Pick<DerivedDoc, "start_page" | "end_page" | "doc_type">>
): Array<"BOUNDARY" | "CLASS" | "EXCLUDE"> {
  const pred = state.predicted || []
  const types: Array<"BOUNDARY" | "CLASS" | "EXCLUDE"> = corrected.map((doc) => {
    const match = pred.find(
      (p) => p.start_page === doc.start_page && p.end_page === doc.end_page
    )
    if (match && match.doc_type !== doc.doc_type) return "CLASS"
    return "BOUNDARY"
  })
  for (const _p of state.excludedPages || []) {
    types.push("EXCLUDE")
  }
  return types
}

/** Project monthly credit/cost at a given number of UPLOADS (not pages). */
export function projectMonthlyCost(
  creditsPerPage: number,
  pagesPerUpload: number,
  creditUsd: number,
  uploadsPerMonth: number
): { monthlyCredits: number; monthlyUsd: number } | null {
  if (!(creditsPerPage > 0) || !(pagesPerUpload > 0)) return null
  const monthlyCredits = creditsPerPage * pagesPerUpload * uploadsPerMonth
  return {
    monthlyCredits,
    monthlyUsd: monthlyCredits * creditUsd,
  }
}

/** Project monthly cost in DOLLARS at a given number of UPLOADS (not pages). */
export function projectMonthlyDollars(
  pagesPerUpload: number,
  dollarsPerPage: number,
  uploadsPerMonth: number
): { pagesPerMonth: number; monthlyUsd: number } | null {
  if (!(pagesPerUpload > 0) || !(dollarsPerPage > 0)) return null
  const pagesPerMonth = pagesPerUpload * uploadsPerMonth
  return {
    pagesPerMonth,
    monthlyUsd: pagesPerMonth * dollarsPerPage,
  }
}
