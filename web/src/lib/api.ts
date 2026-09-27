import type {
  AnalyzeResponse,
  ConfirmedSegment,
  ExtractedDocument,
  ExtractedDocumentV2,
  FieldCorrection,
  FieldValue,
  HeaderField,
  SplitDocument,
  SplitResponse,
  UploadResponse,
} from "./types"
import { HEADER_FIELDS, LINE_ITEM_FIELDS } from "./types"

async function readError(res: Response): Promise<string> {
  const text = await res.text()
  if (!text) return res.statusText || "request failed"
  try {
    const data = JSON.parse(text) as { detail?: unknown }
    if (typeof data.detail === "string" && data.detail) return data.detail
    if (Array.isArray(data.detail)) {
      // FastAPI validation errors: [{ msg, loc, ... }, ...]
      return data.detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item)
        )
        .join("; ")
    }
  } catch {
    // Non-JSON body (e.g. bare "Internal Server Error").
  }
  return text.slice(0, 300) || res.statusText || "request failed"
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text()
  if (!text) {
    if (!res.ok) throw new Error(res.statusText || "request failed")
    throw new Error("empty response")
  }
  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error(
      !res.ok
        ? text.slice(0, 300) || res.statusText || "request failed"
        : `invalid JSON response: ${text.slice(0, 120)}`
    )
  }
}

export async function uploadPdf(file: File): Promise<UploadResponse> {
  const fd = new FormData()
  fd.append("file", file)
  const res = await fetch("/api/upload", { method: "POST", body: fd })
  const data = await readJson<UploadResponse & { detail?: string }>(res)
  if (!res.ok) throw new Error(data.detail || "upload failed")
  return data
}

export async function runSplit(sessionId: string): Promise<SplitResponse> {
  const res = await fetch(`/api/session/${sessionId}/split`, { method: "POST" })
  if (!res.ok) throw new Error(await readError(res))
  return readJson<SplitResponse>(res)
}

/** Stage 1: OCR + document boundaries only (no field extraction). */
export async function analyzeSession(
  sessionId: string,
  opts?: { force?: boolean }
): Promise<AnalyzeResponse> {
  const q = opts?.force ? "?force=true" : ""
  const res = await fetch(`/api/session/${sessionId}/analyze${q}`, {
    method: "POST",
  })
  if (!res.ok) throw new Error(await readError(res))
  return readJson<AnalyzeResponse>(res)
}

/** Stage 2: extract fields for human-confirmed segments. */
export async function extractSession(
  sessionId: string,
  segments: ConfirmedSegment[],
  excludedPages: number[] = []
): Promise<SplitResponse> {
  const res = await fetch(`/api/session/${sessionId}/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      segments,
      excluded_pages: excludedPages,
    }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return readJson<SplitResponse>(res)
}

export async function saveSession(
  sessionId: string,
  payload: {
    documents: Array<
      SplitDocument & { display_name?: string }
    >
    correction_types: string[]
    field_corrections: FieldCorrection[]
    excluded_pages?: number[]
  }
): Promise<Blob> {
  const res = await fetch(`/api/session/${sessionId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    throw new Error(await readError(res))
  }
  return res.blob()
}

/** Map current documents_v2 through the server-side to_voucher_payload. */
export async function exportVouchers(
  sessionId: string,
  documents: ExtractedDocumentV2[]
): Promise<Record<string, unknown>[]> {
  const res = await fetch(`/api/session/${sessionId}/vouchers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ documents }),
  })
  if (!res.ok) throw new Error(await readError(res))
  const data = await readJson<{ vouchers?: Record<string, unknown>[] }>(res)
  if (!Array.isArray(data.vouchers)) {
    throw new Error("invalid vouchers response")
  }
  return data.vouchers
}

export type AiaStatus = {
  allow_writes: boolean
  push_enabled?: boolean
  api_base_configured: boolean
  disabled_reason?: string | null
  session_cookie_expires_on?: string | null
  max_downstream_pages: number
  max_upload_pages?: number
  default_dry_run: boolean
  mode_b_supported: boolean
  needs_review_url?: string | null
}

export type AiaPushOutcome = {
  file_name: string
  page_count: number
  invoice_number: string
  action: string
  hitl_status?: string | null
  notes?: string[]
  reject_reason?: { code?: string; message?: string } | null
  page_start?: number | null
  page_end?: number | null
}

export type AiaPushProgressEvent = {
  type: "invoice" | "summary" | "abort" | "error"
  index?: number
  total?: number
  file_name?: string
  page_start?: number
  page_end?: number
  page_count?: number
  invoice_number?: string
  phase?: string
  action?: string
  hitl_status?: string | null
  status_message?: string | null
  file_uuid?: string | null
  reason?: string
  counts?: Record<string, number>
  needs_review_url?: string | null
  aborted?: boolean
  auth_expired?: boolean
  outcomes?: AiaPushOutcome[]
  unrecovered_sticks?: number
}

export async function fetchAiaStatus(): Promise<AiaStatus> {
  const res = await fetch("/api/aia/status")
  if (!res.ok) throw new Error(await readError(res))
  return readJson<AiaStatus>(res)
}

export async function pushSessionToAia(
  sessionId: string,
  opts: {
    dryRun?: boolean
    erp?: "tally" | "zoho"
    waitForHitl?: boolean
    segments?: ConfirmedSegment[]
    excludedPages?: number[]
    packetName?: string
  } = {}
): Promise<{
  dry_run: boolean
  outcomes: AiaPushOutcome[]
  counts: Record<string, number>
  needs_review_url?: string | null
}> {
  const res = await fetch(`/api/session/${sessionId}/push-aia`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: opts.dryRun ?? true,
      erp: opts.erp ?? "tally",
      wait_for_hitl: opts.waitForHitl ?? false,
      segments: opts.segments,
      excluded_pages: opts.excludedPages,
      packet_name: opts.packetName,
    }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return readJson(res)
}

/** Live Mode A push with NDJSON progress (one event object per line). */
export async function pushSessionToAiaStream(
  sessionId: string,
  opts: {
    dryRun?: boolean
    erp?: "tally" | "zoho"
    waitForHitl?: boolean
    segments?: ConfirmedSegment[]
    excludedPages?: number[]
    onlyIndices?: number[]
    packetName?: string
    onEvent?: (event: AiaPushProgressEvent) => void
  } = {}
): Promise<AiaPushProgressEvent | null> {
  const res = await fetch(`/api/session/${sessionId}/push-aia`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: opts.dryRun ?? false,
      erp: opts.erp ?? "tally",
      wait_for_hitl: opts.waitForHitl ?? true,
      stream: true,
      segments: opts.segments,
      excluded_pages: opts.excludedPages,
      only_indices: opts.onlyIndices,
      packet_name: opts.packetName,
    }),
  })
  if (!res.ok) throw new Error(await readError(res))
  if (!res.body) throw new Error("Push stream returned no body")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let summary: AiaPushProgressEvent | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() || ""
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue
      const event = JSON.parse(trimmed) as AiaPushProgressEvent
      opts.onEvent?.(event)
      if (event.type === "summary") summary = event
      if (event.type === "error") {
        throw new Error(event.reason || "AIA push failed")
      }
    }
  }
  if (buffer.trim()) {
    const event = JSON.parse(buffer.trim()) as AiaPushProgressEvent
    opts.onEvent?.(event)
    if (event.type === "summary") summary = event
    if (event.type === "error") {
      throw new Error(event.reason || "AIA push failed")
    }
  }
  return summary
}

export function pageImageUrl(
  sessionId: string,
  page: number,
  size: "thumb" | "view" = "view"
): string {
  return `/api/session/${sessionId}/page/${page}.png?size=${size}`
}

function fvValue(fv: FieldValue | undefined): string {
  return fv?.value ?? ""
}

function collectV2ValuePaths(
  doc: ExtractedDocumentV2
): Array<{ path: string; page: number; value: string }> {
  const page = doc.page_start
  const rows: Array<{ path: string; page: number; value: string }> = []
  const push = (path: string, fv: FieldValue | undefined, p = page) => {
    rows.push({ path, page: p, value: fvValue(fv) })
  }
  push("seller.name", doc.seller?.name)
  push("seller.address", doc.seller?.address)
  push("seller.tax_id", doc.seller?.tax_id)
  push("buyer.name", doc.buyer?.name)
  push("buyer.address", doc.buyer?.address)
  push("buyer.tax_id", doc.buyer?.tax_id)
  push("document_id", doc.document_id)
  push("issue_date", doc.issue_date)
  push("due_date", doc.due_date)
  push("po_number", doc.po_number)
  push("payment_terms", doc.payment_terms)
  push("billing_period", doc.billing_period)
  push("currency", doc.currency)
  push("totals.subtotal", doc.totals?.subtotal)
  ;(doc.totals?.tax_lines || []).forEach((tl, i) => {
    push(`totals.tax_lines[${i}].name`, tl.name)
    push(`totals.tax_lines[${i}].rate`, tl.rate)
    push(`totals.tax_lines[${i}].base`, tl.base)
    push(`totals.tax_lines[${i}].amount`, tl.amount)
  })
  push("totals.total", doc.totals?.total)
  push("totals.amount_due", doc.totals?.amount_due)
  ;(doc.line_items || []).forEach((it, li) => {
    for (const f of LINE_ITEM_FIELDS) {
      rows.push({
        path: `line_items[${li}].${f}`,
        page: it.page,
        value: fvValue(it[f]),
      })
    }
  })
  ;(doc.other_fields || []).forEach((of, i) => {
    rows.push({
      path: `other_fields[${i}].name`,
      page,
      value: of.name || "",
    })
    push(`other_fields[${i}].value`, of.value)
  })
  return rows
}

/** Diff live extraction against the original to produce FIELD corrections. */
export function computeFieldCorrections(
  extraction: ExtractedDocument[],
  original: ExtractedDocument[],
  extractionV2?: ExtractedDocumentV2[],
  originalV2?: ExtractedDocumentV2[]
): FieldCorrection[] {
  if (extractionV2 && extractionV2.length > 0 && originalV2) {
    const corrections: FieldCorrection[] = []
    extractionV2.forEach((doc, di) => {
      const origDoc = originalV2[di]
      if (!origDoc) return
      const live = collectV2ValuePaths(doc)
      const origMap = new Map(
        collectV2ValuePaths(origDoc).map((r) => [r.path, r])
      )
      for (const row of live) {
        const model = origMap.get(row.path)
        const modelValue = model?.value ?? ""
        if (modelValue !== row.value) {
          corrections.push({
            type: "FIELD",
            page: row.page,
            field: row.path,
            model_value: modelValue,
            user_value: row.value,
          })
        }
      }
      if ((doc.doc_type || "") !== (origDoc.doc_type || "")) {
        corrections.push({
          type: "FIELD",
          page: doc.page_start,
          field: "doc_type",
          model_value: origDoc.doc_type || "",
          user_value: doc.doc_type || "",
        })
      }
    })
    return corrections
  }

  const corrections: FieldCorrection[] = []
  extraction.forEach((doc, di) => {
    const origDoc = original[di]
    if (!origDoc) return
    for (const f of HEADER_FIELDS) {
      const modelValue = (origDoc[f as HeaderField] as string) || ""
      const userValue = (doc[f as HeaderField] as string) || ""
      if (modelValue !== userValue) {
        corrections.push({
          type: "FIELD",
          page: doc.page_start,
          field: f,
          model_value: modelValue,
          user_value: userValue,
        })
      }
    }
    ;(doc.line_items || []).forEach((it, li) => {
      const origItem = (origDoc.line_items || [])[li]
      if (!origItem) return
      for (const f of LINE_ITEM_FIELDS) {
        const modelValue = (origItem[f] as string) || ""
        const userValue = (it[f] as string) || ""
        if (modelValue !== userValue) {
          corrections.push({
            type: "FIELD",
            page: it.page,
            field: f,
            model_value: modelValue,
            user_value: userValue,
          })
        }
      }
    })
  })
  return corrections
}

/** Count ungrounded header + line-item fields for a legacy document. */
export function countUngrounded(doc: ExtractedDocument | undefined): number {
  if (!doc) return 0
  let n = (doc.ungrounded || []).length
  for (const it of doc.line_items || []) {
    n += (it.ungrounded || []).length
  }
  return n
}

/** Count V2 ungrounded paths (headers + line cells + other). */
export function countUngroundedV2(doc: ExtractedDocumentV2 | undefined): number {
  if (!doc) return 0
  let n = (doc.ungrounded || []).length
  for (const it of doc.line_items || []) {
    n += (it.ungrounded || []).length
  }
  return n
}
