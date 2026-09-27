import * as React from "react"
import { DocumentsScreen } from "@/components/DocumentsScreen"
import { InvoiceReviewScreen } from "@/components/InvoiceReviewScreen"
import { PageHeader } from "@/components/PageHeader"
import { SplitReviewScreen } from "@/components/SplitReviewScreen"
import { Topbar } from "@/components/Topbar"
import {
  UploadScreen,
  type UploadStatusVariant,
} from "@/components/UploadScreen"
import { Button } from "@/components/ui/button"
import { ProgressBar } from "@/components/ui/progress-bar"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  analyzeSession,
  computeFieldCorrections,
  exportVouchers,
  fetchAiaStatus,
  pushSessionToAiaStream,
  saveSession,
  uploadPdf,
} from "@/lib/api"
import {
  fileStem,
  sanitizeSourceStem,
} from "@/lib/aiaFilenames"
import {
  navigate,
  ROUTE_META,
  useAppRoute,
  type AppRoute,
} from "@/lib/routes"
import {
  computeCorrectionTypes,
  deriveDocuments,
  seedFromDocuments,
  setDocumentFileName,
  sortedUniquePages,
  toggleCut,
  toggleExclude,
  undoOverlayMerge,
  acceptMergeSuggestion,
  type CutState,
  type DerivedDoc,
  type PredictedDoc,
} from "@/lib/split-state"
import type {
  CostBreakdown,
  DuplicateGroupMember,
  ExtractedDocument,
  ExtractedDocumentV2,
  FieldValue,
  OverlayReport,
  OverlayUndoSeg,
  SegmentDisagreement,
} from "@/lib/types"

/** Typical wall time for boundary detection on a dense packet. */
const TYPICAL_ANALYZE_SECONDS = 90
/** Typical wall time for field extraction on a dense ~29-page packet. */
const TYPICAL_EXTRACT_SECONDS = 330

type AppState = CutState & {
  sessionId: string | null
  uploadedFilename: string | null
  /** ZIP download name is based on the uploaded file. */
  packetName: string
  thumbnails: string[]
  selectedPage: number
  selectedDocIndex: number
  analyzeDone: boolean
  extractDone: boolean
  predicted: PredictedDoc[]
  /** Blank separators auto-excluded at analyze (page → reason). */
  autoExcludedReasonByPage: Record<number, string>
  extraction: ExtractedDocument[]
  originalExtraction: ExtractedDocument[]
  extractionV2: ExtractedDocumentV2[]
  originalExtractionV2: ExtractedDocumentV2[]
  duplicateGroups: DuplicateGroupMember[][]
  segmentDisagreements: SegmentDisagreement[]
  pagesBilled: number
  annotatedRate: number
  costUsd: number
  costBreakdown: CostBreakdown | null
  isReplay: boolean
  replayFixture: string | null
  uncoveredPages: number[]
  status: string
  statusError: boolean
  analyzing: boolean
  analyzeElapsed: number
  extracting: boolean
  extractElapsed: number
  saving: boolean
  overlay: OverlayReport | null
  preOverlayStarts: number[]
  overlayUndoByCut: Record<number, OverlayUndoSeg[]>
}

function initialState(): AppState {
  return {
    sessionId: null,
    uploadedFilename: null,
    packetName: "",
    pageCount: 0,
    thumbnails: [],
    cuts: [],
    labels: {},
    excludedPages: [],
    displayNames: {},
    vendorNames: {},
    invoiceNumbers: {},
    fileNames: {},
    fileNameEdited: {},
    vendorEdited: {},
    numberEdited: {},
    selectedPage: 1,
    selectedDocIndex: 0,
    analyzeDone: false,
    extractDone: false,
    predicted: [],
    autoExcludedReasonByPage: {},
    extraction: [],
    originalExtraction: [],
    extractionV2: [],
    originalExtractionV2: [],
    duplicateGroups: [],
    segmentDisagreements: [],
    pagesBilled: 0,
    annotatedRate: 0.005,
    costUsd: 0,
    costBreakdown: null,
    isReplay: false,
    replayFixture: null,
    uncoveredPages: [],
    status: "",
    statusError: false,
    analyzing: false,
    analyzeElapsed: 0,
    extracting: false,
    extractElapsed: 0,
    saving: false,
    overlay: null,
    preOverlayStarts: [],
    overlayUndoByCut: {},
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function cloneV2(docs: ExtractedDocumentV2[]): ExtractedDocumentV2[] {
  return JSON.parse(JSON.stringify(docs)) as ExtractedDocumentV2[]
}

function setFieldValueAtPath(
  doc: ExtractedDocumentV2,
  path: string,
  value: string
): void {
  const lineMatch =
    /^line_items\[(\d+)\]\.(description|quantity|unit_price|amount|hsn_sac|discount)$/.exec(
      path
    )
  if (lineMatch) {
    const idx = Number(lineMatch[1])
    const field = lineMatch[2] as
      | "description"
      | "quantity"
      | "unit_price"
      | "amount"
      | "hsn_sac"
      | "discount"
    const item = doc.line_items?.[idx]
    if (!item) return
    const fv =
      item[field] || ({ value: "", source_text: "", pages: [] } as FieldValue)
    item[field] = { ...fv, value }
    return
  }
  const otherMatch = /^other_fields\[(\d+)\]\.value$/.exec(path)
  if (otherMatch) {
    const idx = Number(otherMatch[1])
    const of = doc.other_fields?.[idx]
    if (!of) return
    const fv =
      of.value || ({ value: "", source_text: "", pages: [] } as FieldValue)
    of.value = { ...fv, value }
    return
  }
  const taxMatch =
    /^totals\.tax_lines\[(\d+)\]\.(name|rate|base|amount|kind)$/.exec(path)
  if (taxMatch) {
    const idx = Number(taxMatch[1])
    const field = taxMatch[2] as "name" | "rate" | "base" | "amount" | "kind"
    const tl = doc.totals?.tax_lines?.[idx]
    if (!tl) return
    const fv =
      tl[field] || ({ value: "", source_text: "", pages: [] } as FieldValue)
    tl[field] = { ...fv, value }
    return
  }
  if (path.startsWith("seller.")) {
    const key = path.slice("seller.".length) as "name" | "address" | "tax_id"
    if (!doc.seller) return
    const fv =
      doc.seller[key] ||
      ({ value: "", source_text: "", pages: [] } as FieldValue)
    doc.seller[key] = { ...fv, value }
    return
  }
  if (path.startsWith("buyer.")) {
    const key = path.slice("buyer.".length) as "name" | "address" | "tax_id"
    if (!doc.buyer) return
    const fv =
      doc.buyer[key] || ({ value: "", source_text: "", pages: [] } as FieldValue)
    doc.buyer[key] = { ...fv, value }
    return
  }
  if (path.startsWith("totals.")) {
    const key = path.slice("totals.".length) as
      | "subtotal"
      | "total"
      | "amount_due"
      | "tds"
      | "other_taxes"
    if (!doc.totals) return
    if (
      key !== "subtotal" &&
      key !== "total" &&
      key !== "amount_due" &&
      key !== "tds" &&
      key !== "other_taxes"
    ) {
      return
    }
    const fv =
      doc.totals[key] ||
      ({ value: "", source_text: "", pages: [] } as FieldValue)
    doc.totals[key] = { ...fv, value }
    return
  }
  const top = path as
    | "document_id"
    | "issue_date"
    | "due_date"
    | "currency"
    | "po_number"
    | "payment_terms"
    | "billing_period"
    | "narration"
    | "shipping_address"
    | "source_of_supply"
    | "destination_of_supply"
    | "reverse_charge"
  const topKeys = new Set([
    "document_id",
    "issue_date",
    "due_date",
    "currency",
    "po_number",
    "payment_terms",
    "billing_period",
    "narration",
    "shipping_address",
    "source_of_supply",
    "destination_of_supply",
    "reverse_charge",
  ])
  if (topKeys.has(top)) {
    const fv =
      doc[top] || ({ value: "", source_text: "", pages: [] } as FieldValue)
    doc[top] = { ...fv, value }
  }
}

function uploadStatus(state: AppState): {
  label: string
  variant: UploadStatusVariant
} {
  if (state.statusError) {
    return { label: state.status || "Error", variant: "error" }
  }
  if (state.analyzing) {
    return { label: "Detecting", variant: "processing" }
  }
  if (state.extracting) {
    return { label: "Extracting", variant: "processing" }
  }
  if (state.extractDone) {
    return { label: "Extracted", variant: "done" }
  }
  if (state.analyzeDone) {
    return { label: "Review", variant: "review" }
  }
  if (state.sessionId) {
    return { label: state.status || "Ready", variant: "ready" }
  }
  return { label: "Ready", variant: "ready" }
}

function PageActions({ route }: { route: AppRoute }) {
  if (route === "documents") {
    return (
      <Button type="button" onClick={() => navigate("upload")}>
        Upload PDF
      </Button>
    )
  }

  return null
}

export default function ExtractionApp() {
  const route = useAppRoute()
  const [state, setState] = React.useState<AppState>(initialState)
  const [aiaAllowWrites, setAiaAllowWrites] = React.useState(false)
  const [aiaDisabledReason, setAiaDisabledReason] = React.useState<string | null>(
    null
  )
  const [pushingAia, setPushingAia] = React.useState(false)
  const [aiaPushSummary, setAiaPushSummary] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    void fetchAiaStatus()
      .then((s) => {
        if (cancelled) return
        const enabled = !!(s.push_enabled ?? s.allow_writes)
        setAiaAllowWrites(enabled)
        setAiaDisabledReason(s.disabled_reason || null)
      })
      .catch(() => {
        if (cancelled) return
        setAiaAllowWrites(false)
        setAiaDisabledReason(
          "Could not read AIA status from the server. Is it running?"
        )
      })
    return () => {
      cancelled = true
    }
  }, [])

  const fileRef = React.useRef<HTMLInputElement>(null)
  const timerRef = React.useRef<number | null>(null)
  const extractNavRef = React.useRef(false)

  const meta = ROUTE_META[route]
  const { label: uploadStatusLabel, variant: uploadStatusVariant } =
    uploadStatus(state)

  React.useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearInterval(timerRef.current)
    }
  }, [])

  React.useEffect(() => {
    if (state.extractDone && !extractNavRef.current) {
      extractNavRef.current = true
      navigate("invoices")
    }
    if (!state.extractDone) {
      extractNavRef.current = false
    }
  }, [state.extractDone])

  const mutate = React.useCallback((fn: (s: AppState) => void) => {
    setState((prev) => {
      const next: AppState = {
        ...prev,
        cuts: [...prev.cuts],
        labels: { ...prev.labels },
        excludedPages: [...(prev.excludedPages || [])],
        displayNames: { ...(prev.displayNames || {}) },
        vendorNames: { ...(prev.vendorNames || {}) },
        invoiceNumbers: { ...(prev.invoiceNumbers || {}) },
        fileNames: { ...(prev.fileNames || {}) },
        fileNameEdited: { ...(prev.fileNameEdited || {}) },
        vendorEdited: { ...(prev.vendorEdited || {}) },
        numberEdited: { ...(prev.numberEdited || {}) },
        overlayUndoByCut: { ...prev.overlayUndoByCut },
        overlay: prev.overlay
          ? {
              ...prev.overlay,
              merges: [...(prev.overlay.merges || [])],
              suggestions: [...(prev.overlay.suggestions || [])],
              blocked: [...(prev.overlay.blocked || [])],
            }
          : null,
        extraction: prev.extraction.map((d) => ({
          ...d,
          line_items: d.line_items.map((it) => ({ ...it })),
          ungrounded: [...(d.ungrounded || [])],
        })),
        extractionV2: cloneV2(prev.extractionV2),
      }
      fn(next)
      return next
    })
  }, [])

  const docs: DerivedDoc[] = React.useMemo(() => {
    try {
      return deriveDocuments(state)
    } catch {
      return []
    }
  }, [
    state.cuts,
    state.labels,
    state.pageCount,
    state.excludedPages,
    state.displayNames,
    state.vendorNames,
    state.invoiceNumbers,
    state.fileNames,
    state.fileNameEdited,
  ])

  const predictedCuts = React.useMemo(() => {
    const set = new Set<number>()
    const starts =
      state.preOverlayStarts.length > 0
        ? state.preOverlayStarts
        : state.predicted.map((d) => d.start_page)
    for (const start of starts) {
      if (start > 1) set.add(start)
    }
    return set
  }, [state.predicted, state.preOverlayStarts])

  const overlayMergedCuts = React.useMemo(() => {
    const set = new Set<number>()
    for (const pair of state.overlay?.merges || []) {
      set.add(pair.right_start)
    }
    for (const cut of Object.keys(state.overlayUndoByCut)) {
      set.add(Number(cut))
    }
    return set
  }, [state.overlay, state.overlayUndoByCut])

  const overlayMergeReason = React.useMemo(() => {
    const map: Record<number, string> = {}
    for (const pair of state.overlay?.merges || []) {
      map[pair.right_start] = pair.reason
    }
    return map
  }, [state.overlay])

  const overlaySuggestCuts = React.useMemo(() => {
    const set = new Set<number>()
    for (const pair of state.overlay?.suggestions || []) {
      set.add(pair.right_start)
    }
    return set
  }, [state.overlay])

  const overlaySuggestReason = React.useMemo(() => {
    const map: Record<number, string> = {}
    for (const pair of state.overlay?.suggestions || []) {
      map[pair.right_start] = pair.reason
    }
    return map
  }, [state.overlay])

  const setStatus = (msg: string, isError = false) => {
    setState((s) => ({ ...s, status: msg, statusError: isError }))
  }

  const onPushAia = async (opts?: { onlyIndices?: number[] }) => {
    if (!state.sessionId || !aiaAllowWrites || pushingAia) return
    const confirmed = deriveDocuments(state).map((d) => ({
      page_start: d.start_page,
      page_end: d.end_page,
      doc_type: d.doc_type,
      vendor: d.vendor,
      document_number: d.invoice_number,
      display_name: d.display_name,
    }))
    if (confirmed.length === 0) return
    const onlyIndices = opts?.onlyIndices
    const isRetry = Array.isArray(onlyIndices) && onlyIndices.length > 0

    setPushingAia(true)
    setAiaPushSummary(null)
    setStatus(
      isRetry
        ? `Retrying ${onlyIndices!.length} failed bill${onlyIndices!.length === 1 ? "" : "s"}…`
        : "Pushing confirmed bills to AI Accountant…"
    )

    try {
      const summary = await pushSessionToAiaStream(state.sessionId, {
        dryRun: false,
        waitForHitl: true,
        segments: confirmed,
        excludedPages: state.excludedPages || [],
        onlyIndices: isRetry ? onlyIndices : undefined,
        packetName: state.packetName || undefined,
        onEvent: (event) => {
          if (event.type === "abort") {
            if (
              event.auth_expired ||
              (event.reason || "").includes("session expired")
            ) {
              setStatus(
                event.reason ||
                  "AI Accountant session expired — needs a fresh cookie",
                true
              )
            }
          }
        },
      })

      const counts = summary?.counts || {}
      const parts = [
        `${counts.needs_review || 0} in Needs Review`,
        `${(counts.rejected || 0) + (counts.skipped_oversized || 0)} rejected`,
        `${counts.still_processing || 0} still processing`,
      ]
      if (counts.failed > 0) {
        parts.push(`${counts.failed} failed`)
      }
      setAiaPushSummary(parts.join(" · "))
      setStatus(
        summary?.aborted
          ? "Push aborted after stuck Bill Uploads rows."
          : counts.failed > 0
            ? "Push finished with some failures — retry failed only if needed."
            : "Pushed bills to AI Accountant."
      )
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setAiaPushSummary(msg)
      setStatus(msg, true)
    } finally {
      setPushingAia(false)
    }
  }

  const clearTimer = () => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  const onUpload = async (file: File) => {
    try {
      const data = await uploadPdf(file)
      setState(() => {
        const next: AppState = {
          ...initialState(),
          sessionId: data.session_id,
          uploadedFilename: file.name,
          packetName: fileStem(file.name, "packet"),
          pageCount: data.page_count,
          thumbnails: data.thumbnails,
          selectedPage: 1,
          analyzing: true,
          analyzeElapsed: 0,
          statusError: false,
        }
        seedFromDocuments(next, [], data.page_count)
        return next
      })
      // Keep the user on the upload page while bill boundary detection runs.
      void runAnalyze(data.session_id, { force: false })
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err), true)
    }
  }

  const runAnalyze = async (
    sessionId: string,
    opts?: { force?: boolean }
  ) => {
    setState((s) => ({
      ...s,
      analyzing: true,
      analyzeElapsed: 0,
      analyzeDone: opts?.force ? false : s.analyzeDone,
      statusError: false,
    }))
    const started = Date.now()
    clearTimer()
    timerRef.current = window.setInterval(() => {
      setState((s) => ({
        ...s,
        analyzeElapsed: Math.floor((Date.now() - started) / 1000),
      }))
    }, 250)
    try {
      const data = await analyzeSession(sessionId, opts)
      const predicted: PredictedDoc[] = (data.segments || []).map((seg) => ({
        start_page: seg.page_start,
        end_page: seg.page_end,
        doc_type: seg.doc_type || "unknown",
        confidence: seg.confidence,
        review_status: seg.review_status,
        review_reasons: seg.review_reasons,
        vendor: seg.vendor,
        document_number: seg.document_number,
        vendor_source: seg.vendor_source,
        number_source: seg.number_source,
      }))
      const autoExcludedReasonByPage: Record<number, string> = {}
      const autoExcludedPages: number[] = []
      for (const row of data.auto_excluded_pages || []) {
        const p = Number(row.page)
        if (!Number.isInteger(p) || p < 1) continue
        autoExcludedPages.push(p)
        autoExcludedReasonByPage[p] = row.reason || "Blank separator"
      }
      const seedDocs = predicted.map((d) => ({
        start_page: d.start_page,
        doc_type: d.doc_type,
        vendor: d.vendor,
        document_number: d.document_number,
      }))
      const overlay = data.overlay || null
      const overlayUndoByCut: Record<number, OverlayUndoSeg[]> = {}
      for (const seg of data.segments || []) {
        if (seg.overlay_tier === "merge" && seg.overlay_undo?.length) {
          for (const u of seg.overlay_undo) {
            if (u.page_start > seg.page_start) {
              overlayUndoByCut[u.page_start] = seg.overlay_undo
            }
          }
        }
      }
      const preOverlayStarts = (data.pre_overlay_segments || []).map(
        (s) => s.page_start
      )
      setState((prev) => {
        const next: AppState = {
          ...prev,
          analyzing: false,
          analyzeElapsed: Math.floor((Date.now() - started) / 1000),
          predicted,
          autoExcludedReasonByPage,
          analyzeDone: true,
          isReplay: !!data.is_replay,
          replayFixture: data.replay_fixture || null,
          status: "",
          statusError: false,
          overlay,
          preOverlayStarts,
          overlayUndoByCut,
        }
        // Merge auto-blanks with any pages the reviewer already excluded.
        const mergedExcluded = sortedUniquePages(
          [...(prev.excludedPages || []), ...autoExcludedPages],
          prev.pageCount
        )
        next.excludedPages = mergedExcluded
        seedFromDocuments(next, seedDocs, prev.pageCount)
        // seedFromDocuments trims excluded to in-range; re-apply auto blanks
        // so a re-analyze cannot drop them unless the reviewer included them
        // after clearing autoExcludedReasonByPage (include clears the mark).
        next.excludedPages = sortedUniquePages(
          [
            ...(next.excludedPages || []),
            ...Object.keys(autoExcludedReasonByPage).map(Number),
          ],
          prev.pageCount
        )
        next.predicted = predicted
        return next
      })
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err)
      const msg =
        raw === "Failed to fetch"
          ? "Failed to fetch — server unreachable (it may have crashed). Refresh and try again."
          : raw
      setState((s) => ({
        ...s,
        analyzing: false,
        analyzeDone: false,
        status: msg,
        statusError: true,
      }))
    } finally {
      clearTimer()
    }
  }

  const onAnalyze = async (opts?: { force?: boolean }) => {
    if (!state.sessionId || state.analyzing || state.extracting) return
    await runAnalyze(state.sessionId, opts)
  }

  const onSave = async () => {
    if (!state.sessionId || state.saving) return
    const corrected = deriveDocuments(state)
    const correctionTypes = computeCorrectionTypes(state, corrected)
    const fieldCorrections = computeFieldCorrections(
      state.extraction,
      state.originalExtraction,
      state.extractionV2,
      state.originalExtractionV2
    )
    setState((s) => ({
      ...s,
      saving: true,
      status: "Preparing your download…",
      statusError: false,
    }))
    try {
      const blob = await saveSession(state.sessionId, {
        documents: corrected.map((d) => ({
          doc_type: d.doc_type,
          start_page: d.start_page,
          end_page: d.end_page,
          confidence: d.confidence,
          display_name: (d.file_name.trim() || d.display_name).replace(
            /\.pdf$/i,
            ""
          ),
        })),
        correction_types: correctionTypes,
        field_corrections: fieldCorrections,
        excluded_pages: state.excludedPages || [],
      })
      const zipStem = sanitizeSourceStem(
        state.packetName || state.uploadedFilename || "split-pdfs",
        "split-pdfs"
      )
      downloadBlob(blob, `${zipStem}-split-pdfs.zip`)
      setStatus("Your split PDFs are ready. The ZIP download has started.")
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err), true)
    } finally {
      setState((s) => ({ ...s, saving: false }))
    }
  }

  const onExportJson = () => {
    if (!state.sessionId || !state.extractDone) return
    if (state.extractionV2.length > 0) {
      const docsExport = state.extractionV2.map((d) => {
        const derived = docs.find(
          (x) => x.start_page <= d.page_end && x.end_page >= d.page_start
        )
        return {
          ...d,
          doc_type: derived?.doc_type ?? d.doc_type,
          page_start: derived?.start_page ?? d.page_start,
          page_end: derived?.end_page ?? d.page_end,
        }
      })
      const blob = new Blob([JSON.stringify(docsExport, null, 2)], {
        type: "application/json",
      })
      downloadBlob(blob, `${state.sessionId}_extraction.json`)
      setStatus("Extraction JSON downloaded.")
      return
    }
    const docsExport = state.extraction.map((d) => {
      const derived = docs.find(
        (x) => x.start_page <= d.page_end && x.end_page >= d.page_start
      )
      return {
        ...d,
        doc_type: derived?.doc_type ?? d.doc_type,
        page_start: derived?.start_page ?? d.page_start,
        page_end: derived?.end_page ?? d.page_end,
      }
    })
    const blob = new Blob([JSON.stringify(docsExport, null, 2)], {
      type: "application/json",
    })
    downloadBlob(blob, `${state.sessionId}_extraction.json`)
    setStatus("Extraction JSON downloaded.")
  }

  const onExportVoucherJson = async () => {
    if (
      !state.sessionId ||
      !state.extractDone ||
      state.extractionV2.length === 0
    ) {
      return
    }
    try {
      const projected = state.extractionV2.map((d) => {
        const derived = docs.find(
          (x) => x.start_page <= d.page_end && x.end_page >= d.page_start
        )
        return {
          ...d,
          doc_type: derived?.doc_type ?? d.doc_type,
          page_start: derived?.start_page ?? d.page_start,
          page_end: derived?.end_page ?? d.page_end,
        }
      })
      const payload = await exportVouchers(state.sessionId, projected)
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      })
      downloadBlob(blob, `${state.sessionId}_vouchers.json`)
      setStatus("Voucher JSON downloaded.")
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    }
  }

  const splitHeaderActions =
    route === "split" && state.sessionId && state.analyzeDone ? (
      <>
        <Button type="button" variant="outline" onClick={() => navigate("upload")}>
          Back
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={state.analyzing}
          onClick={() => void onAnalyze({ force: true })}
        >
          {state.analyzing ? "Detecting…" : "Re-run detection"}
        </Button>
      </>
    ) : null

  return (
    <TooltipProvider>
      <input
        ref={fileRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) void onUpload(f)
          e.target.value = ""
        }}
      />

      <div className="min-h-svh bg-background">
        <main className="min-h-svh min-w-0 bg-background">
          <Topbar
            onStartOver={() => {
              setState(initialState())
              navigate("upload")
            }}
          />

          <div
            className={
              route === "split"
                ? "split-workspace-page mx-auto flex w-full max-w-[var(--page-max)] flex-col p-[22px] max-[760px]:p-4"
                : "mx-auto w-full max-w-[var(--page-max)] p-[22px] max-[760px]:p-4"
            }
          >
            {route !== "invoices" ? (
              <PageHeader
                eyebrow={meta.eyebrow}
                title={meta.title}
                description={meta.description}
                actions={
                  splitHeaderActions ?? <PageActions route={route} />
                }
              />
            ) : null}

            {route === "upload" ? (
              <UploadScreen
                hasSession={!!state.sessionId}
                filename={state.uploadedFilename}
                pageCount={state.pageCount}
                documentCount={state.analyzeDone ? docs.length : 0}
                statusLabel={uploadStatusLabel}
                statusVariant={uploadStatusVariant}
                onChooseFile={() => fileRef.current?.click()}
                analyzing={state.analyzing}
                analyzeElapsed={state.analyzeElapsed}
              >
                {state.analyzing ? (
                  <div className="mt-2 w-full max-w-sm text-left">
                  <ProgressBar
                      value={Math.min(
                        95,
                        (state.analyzeElapsed / TYPICAL_ANALYZE_SECONDS) * 95
                      )}
                      max={100}
                      label="Boundary detection progress (estimated)"
                  />
                  <p className="mt-2 text-xs text-muted-foreground">
                    Detecting bill boundaries. You can review them when detection finishes.
                  </p>
                </div>
              ) : null}
                {state.sessionId && state.analyzeDone && !state.extractDone ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => navigate("split")}
                  >
                    Review suggested bill splits
                  </Button>
                ) : null}
                {state.sessionId && state.statusError && !state.extractDone ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void onAnalyze({ force: true })}
                  >
                    Retry bill detection
                  </Button>
                ) : null}
              </UploadScreen>
            ) : null}

            {route === "documents" ? <DocumentsScreen /> : null}

            {route === "split" ? (
              state.sessionId ? (
                <SplitReviewScreen
                  sessionId={state.sessionId}
                  filename={state.uploadedFilename ?? "Document"}
                  onSave={() => void onSave()}
                  saving={state.saving}
                  pageCount={state.pageCount}
                  cuts={state.cuts}
                  predictedCuts={predictedCuts}
                  excludedPages={state.excludedPages || []}
                  documents={docs}
                  aiDocumentCount={state.predicted.length || docs.length}
                  autoExcludedReasonByPage={state.autoExcludedReasonByPage}
                  overlayMergedCuts={overlayMergedCuts}
                  overlayMergeReason={overlayMergeReason}
                  overlaySuggestCuts={overlaySuggestCuts}
                  overlaySuggestReason={overlaySuggestReason}
                  onUndoMerge={(cutPage) =>
                    mutate((s) => {
                      undoOverlayMerge(s, s.overlayUndoByCut[cutPage])
                      delete s.overlayUndoByCut[cutPage]
                      if (s.overlay) {
                        s.overlay.merges = (s.overlay.merges || []).filter(
                          (p) => p.right_start !== cutPage
                        )
                      }
                    })
                  }
                  onAcceptSuggest={(cutPage) =>
                    mutate((s) => {
                      acceptMergeSuggestion(s, cutPage)
                      if (s.overlay) {
                        s.overlay.suggestions = (
                          s.overlay.suggestions || []
                        ).filter((p) => p.right_start !== cutPage)
                      }
                    })
                  }
                  analyzing={state.analyzing}
                  analyzeElapsed={state.analyzeElapsed}
                  analyzeError={
                    state.statusError && !state.analyzeDone
                      ? state.status
                      : null
                  }
                  onToggleCut={(page) =>
                    mutate((s) => toggleCut(s, page))
                  }
                  onToggleExclude={(page) =>
                    mutate((s) => {
                      const wasExcluded = (s.excludedPages || []).includes(page)
                      toggleExclude(s, page)
                      if (wasExcluded && s.autoExcludedReasonByPage?.[page]) {
                        const next = { ...s.autoExcludedReasonByPage }
                        delete next[page]
                        s.autoExcludedReasonByPage = next
                      }
                    })
                  }
                  onRenameFileName={(startPage, name) =>
                    mutate((s) => {
                      setDocumentFileName(s, startPage, name)
                    })
                  }
                />
              ) : (
                <div className="border border-border bg-card p-6 text-sm text-muted-foreground">
                  Choose a PDF on Bill Upload — boundary detection starts
                  automatically.
                </div>
              )
            ) : null}

            {route === "invoices" ? (
              state.sessionId && state.extractDone ? (
                <InvoiceReviewScreen
                  sessionId={state.sessionId}
                  filename={state.uploadedFilename ?? "Document"}
                  pageCount={state.pageCount}
                  documents={docs}
                  extractionV2={state.extractionV2}
                  displayNames={state.displayNames}
                  selectedDocIndex={state.selectedDocIndex}
                  setSelectedDocIndex={(index) =>
                    setState((s) => ({ ...s, selectedDocIndex: index }))
                  }
                  selectedPage={state.selectedPage}
                  setSelectedPage={(page) =>
                    setState((s) => ({ ...s, selectedPage: page }))
                  }
                  segmentDisagreements={state.segmentDisagreements}
                  onChangeField={(docIndex, path, value) =>
                    mutate((s) => {
                      const doc = s.extractionV2[docIndex]
                      if (doc) setFieldValueAtPath(doc, path, value)
                    })
                  }
                  onChangeOtherName={(docIndex, index, name) =>
                    mutate((s) => {
                      const of = s.extractionV2[docIndex]?.other_fields?.[index]
                      if (of) of.name = name
                    })
                  }
                  onChangeDocType={(docIndex, value) =>
                    mutate((s) => {
                      const doc = s.extractionV2[docIndex]
                      if (doc) doc.doc_type = value
                    })
                  }
                  onApproveNext={() => {
                    setStatus(
                      `Bill ${state.selectedDocIndex + 1} approved (local).`
                    )
                  }}
                  onSave={() => void onSave()}
                  saving={state.saving}
                  onPushAia={() => void onPushAia()}
                  pushingAia={pushingAia}
                  aiaAllowWrites={aiaAllowWrites}
                  aiaDisabledReason={aiaDisabledReason}
                  aiaPushSummary={aiaPushSummary}
                />
              ) : (
                <div className="border border-border bg-card p-6 text-sm text-muted-foreground">
                  Your split is ready to download. Field extraction preview is
                  optional.
                </div>
              )
            ) : null}

            {state.status ? (
              <p
                className={
                  state.statusError
                    ? "mt-4 text-sm text-destructive"
                    : "mt-4 text-sm text-muted-foreground"
                }
                role="status"
              >
                {state.status}
              </p>
            ) : null}

            {route === "invoices" && state.extractDone ? (
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!state.extractDone}
                  onClick={onExportJson}
                >
                  Export JSON
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    !state.extractDone || state.extractionV2.length === 0
                  }
                  onClick={() => void onExportVoucherJson()}
                >
                  Export voucher JSON
                </Button>
              </div>
            ) : null}

            {route === "split" && state.extracting ? (
              <div className="mt-4 max-w-md">
                <ProgressBar
                  value={Math.min(
                    95,
                    (state.extractElapsed / TYPICAL_EXTRACT_SECONDS) * 95
                  )}
                  max={100}
                  label="Extraction progress (estimated)"
                />
                <p className="mt-2 text-xs text-muted-foreground">
                  {state.extractElapsed}s elapsed — paced against a typical{" "}
                  {(TYPICAL_EXTRACT_SECONDS / 60).toFixed(1)} min dense packet.
                </p>
              </div>
            ) : null}
          </div>
        </main>
      </div>
    </TooltipProvider>
  )
}
