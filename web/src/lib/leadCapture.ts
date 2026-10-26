/**
 * AiA Free Tool Lead Capture & Attribution Utilities
 * Tool: Invoice Splitter
 */

export const TOOL_NAME = "Invoice Splitter"
export const TOOL_NAME_SNAKE = "invoice_splitter"
export const CONTACT_SOURCE = `Website tool - ${TOOL_NAME}`
export const WEBHOOK_URL =
  "https://n8n.aiaccountant.com/webhook/ce246056-bdd0-4199-b363-3b44ed255f96"

export const SESSION_LEAD_CAPTURED_KEY = "aia_tool_lead_captured"
export const SESSION_CLICK_IDS_KEY = "__clickids"
export const SESSION_LEAD_DATA_KEY = "aia_tool_lead"

let inMemoryLeadCaptured = false
let inMemoryLeadData: { name: string; email: string; phone: string } | null = null
let lastSentPayloadJson: string | null = null

export interface ClickIds {
  hs_google_click_id?: string
  hs_facebook_click_id?: string
  hs_linkedin_click_id?: string
}

export interface LeadFormData {
  name: string
  phone: string
  email: string
  accountingTool: string
}

/**
 * Check whether lead capture has already succeeded for this browser session.
 */
export function isLeadCaptured(): boolean {
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const val = window.sessionStorage.getItem(SESSION_LEAD_CAPTURED_KEY)
      if (val === "1") return true
    }
  } catch {
    // sessionStorage might be restricted (e.g. cross-origin iframe or private mode)
  }
  return inMemoryLeadCaptured
}

/**
 * Mark lead as captured for this browser session.
 */
export function markLeadCaptured(): void {
  inMemoryLeadCaptured = true
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      window.sessionStorage.setItem(SESSION_LEAD_CAPTURED_KEY, "1")
    }
  } catch {
    // sessionStorage fallback
  }
}

/**
 * Save lead contact info for repeat runs in session.
 */
export function saveLeadContact(data: { name: string; email: string; phone: string }): void {
  inMemoryLeadData = data
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      window.sessionStorage.setItem(SESSION_LEAD_DATA_KEY, JSON.stringify(data))
    }
  } catch {
    // sessionStorage fallback
  }
}

/**
 * Read saved lead contact info if any.
 */
export function getSavedLeadContact(): { name: string; email: string; phone: string } | null {
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const raw = window.sessionStorage.getItem(SESSION_LEAD_DATA_KEY)
      if (raw) return JSON.parse(raw)
    }
  } catch {
    // sessionStorage fallback
  }
  return inMemoryLeadData
}

/**
 * Persist and retrieve click IDs from the URL and sessionStorage.
 */
export function getAttributionData(): {
  utms: Record<string, string>
  clickIds: ClickIds
} {
  const utms: Record<string, string> = {}
  let storedClickIds: ClickIds = {}

  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const raw = window.sessionStorage.getItem(SESSION_CLICK_IDS_KEY)
      if (raw) {
        storedClickIds = JSON.parse(raw)
      }
    }
  } catch {
    // fallback
  }

  if (typeof window !== "undefined" && window.location) {
    const params = new URLSearchParams(window.location.search)

    const utmKeys = [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
    ]
    for (const key of utmKeys) {
      const val = params.get(key)
      if (val) utms[key] = val
    }

    const gclid = params.get("gclid")
    const fbclid = params.get("fbclid")
    const li_fat_id = params.get("li_fat_id")

    let changed = false
    if (gclid && storedClickIds.hs_google_click_id !== gclid) {
      storedClickIds.hs_google_click_id = gclid
      changed = true
    }
    if (fbclid && storedClickIds.hs_facebook_click_id !== fbclid) {
      storedClickIds.hs_facebook_click_id = fbclid
      changed = true
    }
    if (li_fat_id && storedClickIds.hs_linkedin_click_id !== li_fat_id) {
      storedClickIds.hs_linkedin_click_id = li_fat_id
      changed = true
    }

    if (changed) {
      try {
        if (window.sessionStorage) {
          window.sessionStorage.setItem(
            SESSION_CLICK_IDS_KEY,
            JSON.stringify(storedClickIds)
          )
        }
      } catch {
        // fallback
      }
    }
  }

  return { utms, clickIds: storedClickIds }
}

/**
 * Format phone numbers on type/paste:
 * - Digits only
 * - Strips +91 prefix (12 digits starting with 91)
 * - Strips 0 prefix (11 digits starting with 0)
 * - Slices to max 10 digits
 */
export function sanitizePhoneNumber(input: string): string {
  let digits = input.replace(/\D/g, "")
  if (digits.length === 12 && digits.startsWith("91")) {
    digits = digits.slice(2)
  } else if (digits.length === 11 && digits.startsWith("0")) {
    digits = digits.slice(1)
  }
  return digits.slice(0, 10)
}

/**
 * Validate phone number with Indian mobile regex: ^[6-9]\d{9}$
 */
export function isValidPhone(phone: string): boolean {
  return /^[6-9]\d{9}$/.test(phone)
}

/**
 * Validate email address: ^[^\s@]+@[^\s@]+\.[^\s@]+$
 */
export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
}

/**
 * Validate full name: trimmed length >= 2
 */
export function isValidName(name: string): boolean {
  return name.trim().length >= 2
}

/**
 * Fire-and-forget submission to the n8n webhook.
 */
export async function submitLead(formData: LeadFormData): Promise<void> {
  const { utms, clickIds } = getAttributionData()
  const currentUrl = typeof window !== "undefined" ? window.location.href : ""

  const payload: Record<string, string> = {
    first_name: formData.name.trim(),
    lg__phone: formData.phone.trim(),
    email: formData.email.trim().toLowerCase(),
    which_accounting_tool_do_you_use: formData.accountingTool,
    contact_source: CONTACT_SOURCE,
    ft_landing_page: currentUrl,
    lt_page: currentUrl,
    __page: currentUrl,
    __event: `form_submit_${TOOL_NAME_SNAKE}`,
  }

  // Attribution UTMs
  for (const [key, val] of Object.entries(utms)) {
    if (val) payload[key] = val
  }

  // Attribution Click IDs
  if (clickIds.hs_google_click_id) {
    payload.hs_google_click_id = clickIds.hs_google_click_id
    payload.gclid = clickIds.hs_google_click_id
  }
  if (clickIds.hs_facebook_click_id) {
    payload.hs_facebook_click_id = clickIds.hs_facebook_click_id
  }
  if (clickIds.hs_linkedin_click_id) {
    payload.hs_linkedin_click_id = clickIds.hs_linkedin_click_id
    payload.li_fat_id = clickIds.hs_linkedin_click_id
  }

  const payloadJson = JSON.stringify(payload)
  if (lastSentPayloadJson === payloadJson) {
    return
  }
  lastSentPayloadJson = payloadJson

  // Push to dataLayer if present
  try {
    if (typeof window !== "undefined") {
      const win = window as unknown as { dataLayer?: Array<Record<string, unknown>> }
      if (Array.isArray(win.dataLayer)) {
        win.dataLayer.push({
          event: `${TOOL_NAME_SNAKE}_lead`,
          contact_source: CONTACT_SOURCE,
        })
      }
    }
  } catch (err) {
    console.error(`[${TOOL_NAME}] dataLayer push error:`, err)
  }

  // Fire and forget POST with keepalive: true
  try {
    const res = await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payloadJson,
      keepalive: true,
      mode: "cors",
    })
    if (!res.ok) {
      lastSentPayloadJson = null // allow resend on next submit
      console.error(
        `[${TOOL_NAME}] Lead webhook returned ${res.status} ${res.statusText}`
      )
    }
  } catch (err) {
    lastSentPayloadJson = null // allow resend on next submit
    console.error(`[${TOOL_NAME}] Lead webhook failed:`, err)
  }
}
