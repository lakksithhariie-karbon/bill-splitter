/**
 * AIA push filename helpers — must stay byte-compatible with
 * ``pdfsplit.aia.filenames`` / ``plan_filenames``.
 *
 * Format: ``<packet>-<VEN3>[-<number>].pdf`` where VEN3 is the first 3
 * characters of the sanitized vendor (uppercased, ``NA`` when empty).
 * Collision suffixes (``-2``, ``-3``…) are applied in document order.
 */

const MAX_SLOT = 30
const MAX_FILE_NAME = 180

const RESERVED = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
])

/** Match Python ``re.UNICODE`` ``\w``: letters, marks, numbers, underscore. */
const UNSAFE = /[^\p{L}\p{N}_.\-]+/gu

function collapseHyphens(raw: string): string {
  return raw.replace(/-{2,}/g, "-").replace(/^[.\-_]+|[.\-_]+$/g, "")
}

/** Basename without directory; strip last extension like Path(...).stem. */
export function fileStem(filename: string, fallback = "packet"): string {
  const base = (filename || "").replace(/^.*[/\\]/, "") || fallback
  const dot = base.lastIndexOf(".")
  if (dot > 0) return base.slice(0, dot) || fallback
  return base || fallback
}

export function sanitizeSlot(name: string, fallback = ""): string {
  let raw = (name || "").trim()
  if (!raw) return fallback
  raw = raw.replace(/[/\\\0]/g, "-")
  raw = raw.replace(/\s+/g, "-")
  raw = raw.replace(UNSAFE, "-")
  raw = collapseHyphens(raw)
  raw = (raw.slice(0, MAX_SLOT) || "").replace(/[.\-_]+$/g, "")
  if (!raw) return fallback
  if (RESERVED.has(raw.toUpperCase())) {
    raw = `${raw}_file`.slice(0, MAX_SLOT).replace(/[.\-_]+$/g, "")
  }
  return raw || fallback
}

/** @deprecated Use sanitizeSlot — kept for call sites / tests. */
export function sanitizeDisplayName(name: string): string {
  return sanitizeSlot(name, "Bill") || "Bill"
}

export function sanitizeSourceStem(
  filename: string,
  fallback = "packet"
): string {
  return sanitizeSlot(fileStem(filename, fallback), fallback) || fallback
}

export function positionalName(index: number): string {
  return `Bill-${String(index).padStart(2, "0")}`
}

/** VEN3 segment: first 3 chars of the sanitized vendor, uppercased. */
export function vendorCode(vendor: string): string {
  const safe = sanitizeSlot(vendor, "")
  if (!safe) return "NA"
  return safe.slice(0, 3).toUpperCase()
}

export function buildBaseFileName(opts: {
  packet: string
  vendor?: string
  number?: string
  index?: number
}): string {
  const packet = sanitizeSlot(opts.packet, "packet") || "packet"
  let number = sanitizeSlot(opts.number || "", "")
  const parts = [packet, vendorCode(opts.vendor || "")]
  if (number) parts.push(number)
  let name = `${parts.join("-")}.pdf`
  if (name.length <= MAX_FILE_NAME) return name

  let overflow = name.length - MAX_FILE_NAME
  if (number && number.length > 8) {
    const trim = Math.min(overflow, number.length - 8)
    number = number.slice(0, number.length - trim).replace(/[.\-_]+$/g, "")
    const rebuilt = [packet, vendorCode(opts.vendor || "")]
    if (number) rebuilt.push(number)
    name = `${rebuilt.join("-")}.pdf`
    overflow = name.length - MAX_FILE_NAME
  }
  if (overflow > 0) {
    // Vendor code is fixed at 3 chars; trim packet from the tail instead.
    const trim = Math.min(overflow, packet.length - 3)
    const p = packet.slice(0, packet.length - trim).replace(/[.\-_]+$/g, "")
    const rebuilt = [p, vendorCode(opts.vendor || "")]
    if (number) rebuilt.push(number)
    return `${rebuilt.join("-")}.pdf`
  }
  return name
}

/** Deterministic collision suffixes in document order (matches Python). */
export function applyCollisionSuffixes(baseNames: string[]): string[] {
  const seen = new Map<string, number>()
  const out: string[] = []
  for (const name of baseNames) {
    const key = name.toLowerCase()
    const count = (seen.get(key) || 0) + 1
    seen.set(key, count)
    if (count === 1) {
      out.push(name)
      continue
    }
    if (name.toLowerCase().endsWith(".pdf")) {
      out.push(`${name.slice(0, -4)}-${count}.pdf`)
    } else {
      out.push(`${name}-${count}`)
    }
  }
  return out.map((name) => {
    if (name.length <= MAX_FILE_NAME) return name
    if (name.toLowerCase().endsWith(".pdf")) {
      const stem = name.slice(0, -4).slice(0, MAX_FILE_NAME - 4).replace(/[.\-_]+$/g, "")
      return `${stem}.pdf`
    }
    return name.slice(0, MAX_FILE_NAME)
  })
}

export type NameItem = {
  vendor?: string
  number?: string
  index?: number
}

/** Final unique filenames for a packet — byte-compatible with Python. */
export function planFilenames(packet: string, items: NameItem[]): string[] {
  const bases = items.map((item, i) =>
    buildBaseFileName({
      packet,
      vendor: item.vendor,
      number: item.number,
      index: item.index || i + 1,
    })
  )
  return applyCollisionSuffixes(bases)
}

/**
 * Single-bill preview helper. Prefer ``planFilenames`` for collision-aware
 * previews of a full packet.
 */
export function buildPushFileName(opts: {
  sourceFilename: string
  vendor?: string
  documentNumber?: string
  /** @deprecated treated as vendor when vendor/number empty */
  displayName?: string
  pageStart?: number
  pageEnd?: number
  fallbackStem?: string
  index?: number
}): string {
  const fallback = opts.fallbackStem || "packet"
  const packet = sanitizeSourceStem(opts.sourceFilename, fallback)
  let vendor = (opts.vendor || "").trim()
  let number = (opts.documentNumber || "").trim()
  if (!vendor && !number && (opts.displayName || "").trim()) {
    vendor = (opts.displayName || "").trim()
  }
  return buildBaseFileName({
    packet,
    vendor,
    number,
    index: opts.index || 1,
  })
}

/** Numbered vendor labels: ``Acme-Aug`` → ``Acme-Aug-01``, … (bulk rename). */
export function numberedDisplayNames(base: string, count: number): string[] {
  const raw = (base || "").trim() || "Bill"
  const width = Math.max(2, String(count).length)
  return Array.from({ length: count }, (_, i) => {
    const n = String(i + 1).padStart(width, "0")
    return `${raw}-${n}`
  })
}
