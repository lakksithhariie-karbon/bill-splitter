/** UI-only helpers for invoice review (not the Tally voucher mapper). */

import type { ExtractedDocumentV2, FieldValue } from "./types"

function val(fv: FieldValue | undefined | null): string {
  if (!fv) return ""
  return (fv.value || fv.source_text || "").trim()
}

function parseMoney(text: string): number | null {
  const cleaned = text
    .replace(/[₹$€£¥]/gi, "")
    .replace(/(?:INR|USD|EUR|GBP|Rs\.?|rupees?)/gi, "")
    .replace(/,/g, "")
    .replace(/\s/g, "")
    .replace(/[()]/g, "")
    .trim()
  if (!cleaned || !/\d/.test(cleaned)) return null
  const n = Number(cleaned)
  return Number.isFinite(n) ? n : null
}

/** True when amount_due is present and zero while total is non-zero. */
export function isAlreadyPaid(doc: ExtractedDocumentV2): boolean {
  const totalRaw = val(doc.totals?.total)
  const dueRaw = val(doc.totals?.amount_due)
  if (!totalRaw || !dueRaw) return false
  const total = parseMoney(totalRaw)
  const due = parseMoney(dueRaw)
  if (total === null || due === null) return false
  return due === 0 && total !== 0
}

/** Currency symbol for amount units (not a form field). */
export function currencySymbol(doc: ExtractedDocumentV2 | undefined): string {
  const c = val(doc?.currency).toUpperCase()
  if (c.includes("USD") || c === "$") return "$"
  if (c.includes("EUR") || c === "€") return "€"
  if (c.includes("GBP") || c === "£") return "£"
  if (c.includes("INR") || c.includes("RS") || c.includes("₹") || !c) return "₹"
  return c.slice(0, 3) || "₹"
}
