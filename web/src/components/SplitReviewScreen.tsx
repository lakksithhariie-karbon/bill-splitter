import * as React from "react"
import { Minus, Plus } from "lucide-react"

import {
  BoundaryStream,
  cutPageForBoundary,
} from "@/components/BoundaryStream"
import { Button } from "@/components/ui/button"
import type { DerivedDoc } from "@/lib/split-state"
import { cn } from "@/lib/utils"

const ZOOM_MIN = 0.76
const ZOOM_MAX = 1.16
const ZOOM_STEP = 0.08

/** Typewriter copy shown in the stream-head meta while detection runs. */
const DETECT_MESSAGE = "Reading every page, marking each bill's start..."

export type SplitReviewScreenProps = {
  sessionId: string
  filename: string
  pageCount: number
  cuts: number[]
  predictedCuts: ReadonlySet<number>
  excludedPages: number[]
  documents: DerivedDoc[]
  /** Predicted document count from analyze (for stream header). */
  aiDocumentCount: number
  /** Auto-excluded blank pages from analyze (page → reason). */
  autoExcludedReasonByPage?: Record<number, string>
  analyzing?: boolean
  saving?: boolean
  onSave: () => void
  analyzeElapsed?: number
  analyzeError?: string | null
  onToggleCut: (page: number) => void
  onToggleExclude: (page: number) => void
  onRenameFileName: (startPage: number, name: string) => void
  overlayMergedCuts?: ReadonlySet<number>
  overlayMergeReason?: Record<number, string>
  overlaySuggestCuts?: ReadonlySet<number>
  overlaySuggestReason?: Record<number, string>
  onUndoMerge?: (cutPage: number) => void
  onAcceptSuggest?: (cutPage: number) => void
}

function pageRangeLabel(doc: DerivedDoc): string {
  return doc.start_page === doc.end_page
    ? `Page ${doc.start_page}`
    : `Pages ${doc.start_page}–${doc.end_page}`
}

export function SplitReviewScreen({
  sessionId,
  filename,
  pageCount,
  cuts,
  predictedCuts,
  excludedPages,
  documents,
  aiDocumentCount,
  autoExcludedReasonByPage,
  analyzing = false,
  saving = false,
  onSave,
  analyzeError = null,
  onToggleCut,
  onToggleExclude,
  onRenameFileName,
  overlayMergedCuts,
  overlayMergeReason,
  overlaySuggestCuts,
  overlaySuggestReason,
  onUndoMerge,
  onAcceptSuggest,
}: SplitReviewScreenProps) {
  const [zoom, setZoom] = React.useState(1)
  const [typeCount, setTypeCount] = React.useState(0)

  React.useEffect(() => {
    let generation = 0
    let timeout: ReturnType<typeof setTimeout> | null = null

    const run = () => {
      const gen = ++generation
      setTypeCount(0)
      const typeStep = (count: number) => {
        if (gen !== generation) return
        setTypeCount(count)
        if (count >= DETECT_MESSAGE.length) {
          timeout = setTimeout(() => {
            if (gen !== generation) return
            setTypeCount(0)
            timeout = setTimeout(run, 350)
          }, 1400)
          return
        }
        timeout = setTimeout(() => typeStep(count + 1), 26)
      }
      typeStep(1)
    }

    if (!analyzing) return
    run()
    return () => {
      generation++
      if (timeout != null) clearTimeout(timeout)
    }
  }, [analyzing])
  const billCount = documents.length
  const excludedCount = excludedPages.length
  const zoomPct = Math.round(zoom * 100)

  const adjustZoom = (delta: number) => {
    setZoom((z) =>
      Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((z + delta) * 100) / 100))
    )
  }

  const handleToggleBoundary = (boundaryIndex: number) => {
    onToggleCut(cutPageForBoundary(boundaryIndex))
  }

  return (
    <div className="split-review-layout grid min-h-0 flex-1 grid-cols-1 items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_318px]">
      <div className="split-stream-column min-w-0">
      <div className="stream-head-wrap">
        <div className="stream-head">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="grid h-9 w-[31px] place-items-center border border-border bg-background font-display text-[8px] font-bold">
              PDF
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold">{filename}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                {analyzing ? (
                  <>
                    <span className="wave-inline" aria-hidden="true">
                      {Array.from({ length: 10 }, (_, i) => (
                        <span
                          key={i}
                          className="wave-inline__bar"
                          style={{ "--index": i } as React.CSSProperties}
                        />
                      ))}
                    </span>
                    <span className="detect-typewriter">
                      {DETECT_MESSAGE.slice(0, typeCount)}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="whitespace-nowrap">{pageCount} pages</span>
                    <span className="whitespace-nowrap">
                      {` · AI found ${aiDocumentCount} bill${
                        aiDocumentCount === 1 ? "" : "s"
                      }`}
                    </span>
                    {excludedCount > 0 ? (
                      <span className="whitespace-nowrap">
                        {` · ${excludedCount} excluded`}
                      </span>
                    ) : null}
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="Zoom out"
              disabled={zoom <= ZOOM_MIN}
              onClick={() => adjustZoom(-ZOOM_STEP)}
            >
              <Minus aria-hidden="true" />
            </Button>
            <div className="min-w-[44px] text-center text-[10px] text-muted-foreground">
              {zoomPct}%
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="Zoom in"
              disabled={zoom >= ZOOM_MAX}
              onClick={() => adjustZoom(ZOOM_STEP)}
            >
              <Plus aria-hidden="true" />
            </Button>
          </div>
        </div>
      </div>

      <div className="stream-pages">
        {analyzeError ? (
          <div
            role="alert"
            className="border-b border-destructive/30 bg-destructive/5 px-3.5 py-3 text-[12px] text-destructive"
          >
            <div className="font-semibold">Boundary detection failed</div>
            <p className="mt-1 text-[11px] leading-relaxed text-foreground/80">
              {analyzeError}
            </p>
          </div>
        ) : null}

        <BoundaryStream
          sessionId={sessionId}
          pageCount={pageCount}
          cuts={cuts}
          predictedCuts={predictedCuts}
          excludedPages={excludedPages}
          zoom={zoom}
          onToggleBoundary={handleToggleBoundary}
          onToggleExclude={onToggleExclude}
          autoExcludedReasonByPage={autoExcludedReasonByPage}
          overlayMergedCuts={overlayMergedCuts}
          overlayMergeReason={overlayMergeReason}
          onUndoMerge={onUndoMerge}
          overlaySuggestCuts={overlaySuggestCuts}
          overlaySuggestReason={overlaySuggestReason}
          onAcceptSuggest={onAcceptSuggest}
        />
      </div>
      </div>

      <div className="inspector-wrap min-h-0">
        <aside className="inspector flex min-h-0 flex-col border border-border bg-card">
        <div className="border-b border-border p-3">
          <div className="flex items-start justify-between gap-2.5">
            <div>
              <div className="text-[9px] font-semibold tracking-[0.07em] text-muted-foreground uppercase">
                Current split
              </div>
              <div className="mt-0.5 font-display text-sm font-semibold">
                {analyzing
                  ? `Working on ${pageCount} pages`
                  : `${billCount} bill${
                      billCount === 1 ? "" : "s"
                    } from ${pageCount} pages`}
              </div>
            </div>
            <span
              className={cn(
                "inline-flex h-6 items-center gap-1.5 border px-1.5 text-[10px] font-semibold whitespace-nowrap",
                analyzing
                  ? "border-border bg-secondary"
                  : "border-primary bg-primary text-primary-foreground"
              )}
            >
              <span className="size-1.5 rounded-full bg-current opacity-60" />
              {analyzing ? "Detecting" : "Suggested splits"}
            </span>
          </div>

          <p className="mt-2 text-[9px] leading-[1.4] text-muted-foreground">
            One PDF per bill will be included in the ZIP.
          </p>
        </div>

        <div className="inspector-list scroll-slim grid gap-2 p-2.5 max-lg:grid-cols-2 max-[760px]:grid-cols-1">
          {analyzing ? (
            <div className="flex min-h-0 flex-col gap-2">
              <div className="border border-border bg-muted/30 px-2.5 py-2">
                <div className="text-[8px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
                  Bills in this packet
                </div>
                <div className="mt-0.5 font-display text-[12px] font-semibold">
                  Working on {pageCount} pages
                </div>
              </div>
              <div className="border border-border bg-muted/30 px-2.5 py-2">
                <div className="text-[8px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
                  Finding where each bill starts
                </div>
                <p className="mt-0.5 text-[10px] leading-[1.4] text-muted-foreground">
                  Nothing is decided yet. Bills appear here the moment detection
                  finishes.
                </p>
              </div>
            </div>
          ) : (
            <>
          {documents.length === 0 && !analyzing ? (
            <div className="border border-border bg-muted px-2.5 py-3 text-[10px] text-muted-foreground">
              {excludedCount === pageCount
                ? "Every page is excluded — include at least one page to form a bill."
                : "No bill groups yet."}
            </div>
          ) : null}
          {documents.map((doc, docIndex) => (
            <article
              key={`${doc.start_page}-${doc.end_page}`}
              className="border border-border bg-background px-2.5 py-2"
            >
              <label className="block">
                <span className="text-[8px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
                  Bill name
                </span>
                <input
                  type="text"
                  className="mt-0.5 w-full border border-transparent bg-muted/40 px-1.5 py-1 font-mono text-[11px] outline-none hover:border-border focus:border-foreground focus:bg-background"
                  value={doc.file_name}
                  placeholder="Vendor bill reference"
                  aria-label={`Bill name for pages ${doc.start_page} to ${doc.end_page}`}
                  onChange={(e) =>
                    onRenameFileName(doc.start_page, e.target.value)
                  }
                />
              </label>
              <div className="px-1.5 pt-1 text-[9px] text-muted-foreground">
                Bill {String(docIndex + 1).padStart(2, "0")} ·{" "}
                {pageRangeLabel(doc)}
              </div>
            </article>
          ))}
            </>
          )}
        </div>
        {!analyzing && billCount > 0 ? (
          <div className="border-t border-border p-2.5">
            <Button
              type="button"
              variant="default"
              className="w-full rounded-md"
              disabled={saving}
              onClick={onSave}
            >
              {saving ? "Preparing ZIP…" : "Download split PDFs"}
            </Button>
            <p className="mt-1.5 text-center text-[9px] text-muted-foreground">
              One PDF per bill, together in a ZIP file.
            </p>
          </div>
        ) : null}
      </aside>
      </div>
    </div>
  )
}
