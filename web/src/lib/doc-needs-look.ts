/**
 * When a bill is "Needs a look", the flag is about a specific divider — not
 * about the span's end page. Excluding a trailing page moves end_page and
 * used to clear the badge while the disputed start/hold was untouched.
 *
 * Start-class reasons are about a cut that already exists. They stay until
 * the reviewer acks them or that start cut is removed. Hold-class reasons
 * still clear when a cut appears at the held divider.
 */

export type NeedsLookDoc = {
  start_page: number
  end_page: number
}

const START_REASON_RE =
  /this start|this cut|same bill|letterhead weak/i
const HOLD_REASON_RE = /held|after-check merged/i

export function isStartClassReason(reason: string): boolean {
  return START_REASON_RE.test(reason)
}

export function isHoldClassReason(reason: string): boolean {
  return HOLD_REASON_RE.test(reason)
}

export function startClassReasons(reasons?: readonly string[]): string[] {
  return (reasons || []).filter(isStartClassReason)
}

export function holdClassReasons(reasons?: readonly string[]): string[] {
  return (reasons || []).filter(isHoldClassReason)
}

export function pruneConfirmedStarts(
  confirmed: Iterable<number>,
  cuts: readonly number[]
): number[] {
  const cutSet = new Set(cuts)
  return Array.from(confirmed).filter((p) => p === 1 || cutSet.has(p))
}

/** Divider to the left of a flagged hold, or the document's start boundary. */
export function dividerAfterPageForDoc(
  doc: NeedsLookDoc,
  overlayMergedCuts?: ReadonlySet<number>,
  reasons?: readonly string[]
): number | null {
  const hold = holdClassReasons(reasons).length > 0
  const start = startClassReasons(reasons).length > 0
  const heldCut = Array.from(overlayMergedCuts || []).find(
    (p) => p > doc.start_page && p <= doc.end_page
  )
  if (hold && heldCut && heldCut > 1) return heldCut - 1
  if (hold && doc.end_page > doc.start_page) return doc.start_page
  if (start && doc.start_page > 1) return doc.start_page - 1
  if (heldCut && heldCut > 1) return heldCut - 1
  if (doc.end_page > doc.start_page) return doc.start_page
  if (doc.start_page > 1) return doc.start_page - 1
  return null
}

/** Cut page that a split at `dividerAfterPageForDoc` would create. */
export function cutPageForHeldAfter(heldAfter: number): number {
  return heldAfter + 1
}

export function docNeedsLook(
  doc: NeedsLookDoc,
  reviewByStartPage?: Record<number, "checked" | "needs_look">,
  predictedEndByStartPage?: Record<number, number>,
  cuts?: readonly number[],
  overlayMergedCuts?: ReadonlySet<number>,
  reasons?: readonly string[],
  confirmedStarts?: ReadonlySet<number>
): boolean {
  if (reviewByStartPage?.[doc.start_page] !== "needs_look") return false

  const startAcked = confirmedStarts?.has(doc.start_page) === true
  const hasStart = startClassReasons(reasons).length > 0 && !startAcked
  const hasHold = holdClassReasons(reasons).length > 0

  if (hasHold) {
    const predictedEnd = predictedEndByStartPage?.[doc.start_page]
    const heldPage = dividerAfterPageForDoc(
      {
        start_page: doc.start_page,
        end_page: predictedEnd ?? doc.end_page,
      },
      overlayMergedCuts,
      holdClassReasons(reasons)
    )
    const spanEnd = predictedEnd ?? doc.end_page
    const heldInPredicted =
      heldPage != null &&
      doc.start_page <= heldPage &&
      heldPage <= spanEnd
    let holdOpen = true
    if (heldInPredicted && heldPage != null) {
      const cutAtHeld = (cuts || []).includes(cutPageForHeldAfter(heldPage))
      if (cutAtHeld) holdOpen = false
      else if (doc.start_page > heldPage || doc.end_page < heldPage) {
        holdOpen = false
      }
    } else if (
      predictedEnd != null &&
      predictedEnd !== doc.end_page
    ) {
      holdOpen = false
    }
    if (holdOpen) return true
  }

  if (hasStart) return true
  if (startAcked) return false

  const predictedEnd = predictedEndByStartPage?.[doc.start_page]
  if (predictedEnd != null && predictedEnd !== doc.end_page) return false
  return true
}
