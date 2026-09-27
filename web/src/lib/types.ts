/** Types matching the FastAPI / extraction.py response shapes. */

export type LineItem = {
  page: number
  description: string
  quantity: string
  unit_price: string
  amount: string
  ungrounded: string[]
}

export type ExtractedDocument = {
  page_start: number
  page_end: number
  doc_type: string
  vendor_name: string
  invoice_number: string
  invoice_date: string
  currency: string
  gstin: string
  total_amount: string
  tax_amount: string
  line_items: LineItem[]
  confidence: number
  ungrounded: string[]
}

/** Architecture B open extraction contract (mirrors ExtractedDocumentV2). */
export type FieldValue = {
  value: string
  source_text: string
  pages: number[]
}

export type PartyInfo = {
  name: FieldValue
  address: FieldValue
  tax_id: FieldValue
}

export type TaxLineV2 = {
  name: FieldValue
  rate: FieldValue
  base: FieldValue
  amount: FieldValue
  kind?: FieldValue
}

export type TotalsV2 = {
  subtotal: FieldValue
  tax_lines: TaxLineV2[]
  total: FieldValue
  amount_due: FieldValue
  tds?: FieldValue
  other_taxes?: FieldValue
}

export type LineItemV2 = {
  page: number
  description: FieldValue
  quantity: FieldValue
  unit_price: FieldValue
  amount: FieldValue
  hsn_sac?: FieldValue
  discount?: FieldValue
  /** CHECK A: source_text not found on the page. */
  ungrounded: string[]
  /** CHECK C: source_text not a contiguous page span. */
  evidence_imprecise?: string[]
  /** CHECK B: value is not a faithful transform of source_text. */
  value_mismatch?: string[]
}

export type OtherField = {
  name: string
  value: FieldValue
}

export type ExtractedDocumentV2 = {
  page_start: number
  page_end: number
  doc_type: string
  confidence: number
  seller: PartyInfo
  buyer: PartyInfo
  document_id: FieldValue
  issue_date: FieldValue
  due_date: FieldValue
  currency: FieldValue
  po_number: FieldValue
  payment_terms: FieldValue
  billing_period: FieldValue
  narration?: FieldValue
  shipping_address?: FieldValue
  source_of_supply?: FieldValue
  destination_of_supply?: FieldValue
  reverse_charge?: FieldValue
  totals: TotalsV2
  line_items: LineItemV2[]
  other_fields: OtherField[]
  /** CHECK A: source_text not found on the page. */
  ungrounded: string[]
  /** CHECK C: source_text not a contiguous page span. */
  evidence_imprecise?: string[]
  /** CHECK B: value is not a faithful transform of source_text. */
  value_mismatch?: string[]
  /** Arithmetic: subtotal + tax_lines ≉ total. */
  totals_mismatch?: boolean
}

/** Extraction subdivided a human-confirmed segment (prompt only — do not auto-split). */
export type SegmentDisagreement = {
  page_start: number
  page_end: number
  proposed_splits: Array<{
    page_start: number
    page_end: number
    doc_type: string
  }>
}

export type CostBreakdown = {
  ocr_usd?: number
  chat_usd_estimated?: number
  total_usd?: number
  chat_model?: string
  chat_raw_paths?: string[]
}

export type ExtractionResult = {
  provider: string
  model_version: string
  documents: ExtractedDocument[]
  documents_v2?: ExtractedDocumentV2[]
  pages_processed: number
  cost_usd: number
  cost_breakdown?: CostBreakdown | null
  raw_response_path: string | null
  errors: string[]
}

export type SplitDocument = {
  doc_type: string
  start_page: number
  end_page: number
  confidence: number
  text?: string | null
}

export type AnalyzeSegment = {
  page_start: number
  page_end: number
  doc_type: string
  confidence?: number
  /** Human-readable why this boundary was chosen (Layer 1 + judge). */
  evidence?: string[]
  signals?: string[]
  /** Reviewer scan label — checked vs needs a look (not a fake %). */
  review_status?: "checked" | "needs_look"
  review_reasons?: string[]
  /** Grounded vendor cached at analyze time. */
  vendor?: string
  document_number?: string
  vendor_source?: string
  number_source?: string
  aia_file_name?: string
  naming_fallback?: boolean
  overlay_tier?: "merge" | "suggest" | "blocked"
  overlay_reason?: string
  overlay_undo?: OverlayUndoSeg[]
  overlay_suggest?: {
    left_start: number
    right_start: number
    reason: string
    document_number: string
  }
}

export type OverlayUndoSeg = {
  page_start: number
  page_end: number
  doc_type?: string
  vendor?: string
  document_number?: string
}

export type OverlayPair = {
  left_start: number
  left_end: number
  right_start: number
  right_end: number
  tier: "merge" | "suggest" | "blocked"
  reason: string
  document_number: string
  vendor_left?: string
  vendor_right?: string
  vendor_relation?: string
}

export type OverlayReport = {
  merges?: OverlayPair[]
  suggestions?: OverlayPair[]
  blocked?: OverlayPair[]
  extent_hold_pages?: number[]
  tier_counts?: { merge?: number; suggest?: number; blocked?: number }
}

export type AnalyzeResponse = {
  segments: AnalyzeSegment[]
  pages_processed: number
  page_count: number
  errors?: string[]
  is_replay?: boolean
  replay_fixture?: string
  overlay?: OverlayReport
  pre_overlay_segments?: AnalyzeSegment[]
  auto_excluded_pages?: Array<{
    page: number
    reason?: string
    source?: string
  }>
}

export type ConfirmedSegment = {
  page_start: number
  page_end: number
  doc_type: string
  /** Reviewer vendor slot — part of the AIA file name. */
  vendor?: string
  /** Reviewer invoice-number slot. */
  document_number?: string
  /** @deprecated Prefer vendor — treated as vendor when slots empty. */
  display_name?: string
}

export type SplitResponse = {
  input_ref?: string
  provider?: string
  model_version?: string
  documents: SplitDocument[]
  latency_ms?: number
  cost_usd: number
  raw_response_path?: string | null
  errors?: string[]
  extraction: ExtractionResult | null
  pages_billed: number
  annotated_rate: number
  is_replay: boolean
  replay_fixture?: string
  uncovered_pages?: number[]
  cost_breakdown?: CostBreakdown | null
  duplicate_groups?: DuplicateGroupMember[][]
  segment_disagreements?: SegmentDisagreement[]
}

export type DuplicateGroupMember = {
  page_start: number
  page_end: number
}

export type UploadResponse = {
  session_id: string
  page_count: number
  thumbnails: string[]
  /** PDF mediabox size per page (points); used to size placeholders. */
  page_dimensions?: Array<{
    page: number
    width_pt: number
    height_pt: number
  }>
}

export type FieldCorrection = {
  type: "FIELD"
  page: number
  field: string
  model_value: string
  user_value: string
}

export const HEADER_FIELDS = [
  "vendor_name",
  "invoice_number",
  "invoice_date",
  "currency",
  "gstin",
  "total_amount",
  "tax_amount",
] as const

export type HeaderField = (typeof HEADER_FIELDS)[number]

export const HEADER_LABELS: Record<HeaderField, string> = {
  vendor_name: "Vendor",
  invoice_number: "Bill #",
  invoice_date: "Date",
  currency: "Currency",
  gstin: "GSTIN",
  total_amount: "Total",
  tax_amount: "Tax",
}

export const MONO_FIELDS: ReadonlySet<string> = new Set([
  "invoice_number",
  "invoice_date",
  "gstin",
  "total_amount",
  "tax_amount",
  "currency",
  "document_id",
  "issue_date",
  "due_date",
  "po_number",
  "seller.tax_id",
  "buyer.tax_id",
  "totals.subtotal",
  "totals.total",
  "totals.amount_due",
])

export const LINE_ITEM_FIELDS = [
  "description",
  "quantity",
  "unit_price",
  "amount",
] as const

export function emptyFieldValue(): FieldValue {
  return { value: "", source_text: "", pages: [] }
}
