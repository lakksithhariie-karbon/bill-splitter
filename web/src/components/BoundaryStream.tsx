import * as React from "react"
import { Check, Plus, Ban } from "lucide-react"

import { pageImageUrl } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/**
 * Boundary ↔ cut mapping (verified against lib/split-state + prototype JS):
 *   Boundary index i (0-based) sits between page (i+1) and page (i+2).
 *   Boundary i is split  ↔  a cut at page (i+2)  (new document starts there).
 *   Prototype splits at indices 1, 2, 5 → cuts at 3, 4, 7 → docs 1-2, 3, 4-6, 7-8.
 */

export type BoundaryKind =
  | "ai-kept"
  | "ai-removed"
  | "user-added"
  | "untouched"
  | "overlay-merged"

export function cutPageForBoundary(boundaryIndex: number): number {
  return boundaryIndex + 2
}

export function boundaryKind(
  boundaryIndex: number,
  cuts: number[],
  predictedCuts: ReadonlySet<number>,
  overlayMergedCuts?: ReadonlySet<number>
): BoundaryKind {
  const cutPage = cutPageForBoundary(boundaryIndex)
  const isSplit = cuts.includes(cutPage)
  const wasPredicted = predictedCuts.has(cutPage)
  if (!isSplit && overlayMergedCuts?.has(cutPage)) return "overlay-merged"
  if (isSplit && wasPredicted) return "ai-kept"
  if (!isSplit && wasPredicted) return "ai-removed"
  if (isSplit && !wasPredicted) return "user-added"
  return "untouched"
}

export type BoundaryStreamProps = {
  sessionId: string
  pageCount: number
  cuts: number[]
  /** Predicted cut pages (start pages > 1 from analyze). */
  predictedCuts: ReadonlySet<number>
  excludedPages?: ReadonlySet<number> | number[]
  zoom: number
  onToggleBoundary: (boundaryIndex: number) => void
  onToggleExclude?: (page: number) => void
  /** Pages auto-excluded as blank separators (page → reason). */
  autoExcludedReasonByPage?: Record<number, string>
  /** Cut pages held together by the overlay merge (right-start of the pair). */
  overlayMergedCuts?: ReadonlySet<number>
  overlayMergeReason?: Record<number, string>
  onUndoMerge?: (cutPage: number) => void
  /** Cut pages that stay split, with a join suggestion. */
  overlaySuggestCuts?: ReadonlySet<number>
  overlaySuggestReason?: Record<number, string>
  onAcceptSuggest?: (cutPage: number) => void
}

function LazyPageImage({
  sessionId,
  page,
  zoom,
  excluded,
}: {
  sessionId: string
  page: number
  zoom: number
  excluded?: boolean
}) {
  const wrapRef = React.useRef<HTMLDivElement>(null)
  const [near, setNear] = React.useState(page <= 2)
  const [useView, setUseView] = React.useState(false)
  const [loaded, setLoaded] = React.useState(false)

  React.useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setNear(true)
            if (entry.intersectionRatio > 0.05) setUseView(true)
          }
        }
      },
      {
        root: null,
        rootMargin: "600px 0px",
        threshold: [0, 0.05, 0.25],
      }
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  const src = near
    ? pageImageUrl(sessionId, page, useView ? "view" : "thumb")
    : null

  return (
    <div ref={wrapRef} className="relative w-full">
      {!loaded ? (
        <Skeleton className="absolute inset-0 min-h-[420px] w-full" />
      ) : null}
      {src ? (
        <img
          src={src}
          alt={`Page ${page}${excluded ? " (excluded)" : ""}`}
          className={cn(
            "block w-full border border-border bg-white shadow-[0_8px_22px_rgb(0_0_0_/_0.06)] dark:shadow-[0_8px_22px_rgb(0_0_0_/_0.35)]",
            !loaded && "opacity-0",
            excluded && "opacity-45 grayscale"
          )}
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "top center",
            marginBottom: zoom !== 1 ? `${(zoom - 1) * 640}px` : undefined,
          }}
          loading={page <= 2 ? "eager" : "lazy"}
          onLoad={() => setLoaded(true)}
        />
      ) : (
        <div className="min-h-[420px] w-full border border-border bg-muted" />
      )}
      {excluded ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-[8%] top-1/2 z-[3] h-0.5 -translate-y-1/2 bg-foreground/70"
        />
      ) : null}
    </div>
  )
}

function BoundaryControl({
  kind,
  disabled,
  onToggle,
  mergeReason,
  onUndoMerge,
  suggestReason,
  onAcceptSuggest,
}: {
  kind: BoundaryKind
  disabled?: boolean
  onToggle: () => void
  mergeReason?: string
  onUndoMerge?: () => void
  suggestReason?: string
  onAcceptSuggest?: () => void
}) {
  const isSplit = kind === "ai-kept" || kind === "user-added"
  const isMerged = kind === "overlay-merged"

  let label: React.ReactNode
  if (kind === "overlay-merged") {
    label = (
      <>
        Kept together{" "}
        <span className="font-medium opacity-[0.72]">· we joined these</span>
      </>
    )
  } else if (kind === "ai-kept") {
    label = (
      <>
        New bill <span className="font-medium opacity-[0.72]">· AI suggested</span>
      </>
    )
  } else if (kind === "user-added") {
    label = (
      <>
        New bill <span className="font-medium opacity-[0.72]">· added by you</span>
      </>
    )
  } else if (kind === "ai-removed") {
    label = (
      <>
        Split here{" "}
        <span className="font-medium text-warning">· AI suggested (removed)</span>
      </>
    )
  } else {
    label = "Split here"
  }

  return (
    <div
      className={cn(
        "relative grid min-h-[92px] place-items-center py-2",
        "before:absolute before:inset-x-0 before:top-1/2 before:h-px before:bg-border",
        isSplit && "before:bg-foreground",
        isMerged && "before:bg-foreground",
        kind === "ai-removed" &&
          "before:bg-[color-mix(in_oklch,var(--warning)_55%,var(--border))]",
        disabled && "opacity-40"
      )}
    >
      <div className="relative z-[2] flex flex-col items-center gap-1.5">
        <button
          type="button"
          onClick={isMerged ? onUndoMerge : onToggle}
          disabled={disabled || (isMerged && !onUndoMerge)}
          className={cn(
            "inline-flex min-h-[34px] min-w-[200px] items-center justify-center gap-[7px] border px-[11px] text-[10px] font-semibold transition-[border-color,background-color,color] duration-150",
            isSplit
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-background hover:border-foreground",
            isMerged &&
              "border-foreground bg-foreground text-background hover:border-foreground",
            kind === "ai-removed" &&
              "border-[color-mix(in_oklch,var(--warning)_45%,var(--border))] bg-background text-foreground hover:border-warning",
            kind === "user-added" &&
              "border-success bg-success text-success-foreground",
            disabled && "cursor-not-allowed hover:border-border"
          )}
          aria-pressed={isSplit || isMerged}
        >
          {isSplit || isMerged ? (
            <Check className="size-3" strokeWidth={2.2} aria-hidden="true" />
          ) : (
            <Plus className="size-3" strokeWidth={2.2} aria-hidden="true" />
          )}
          <span>{label}</span>
        </button>
        {isMerged && mergeReason ? (
          <div className="max-w-[320px] text-center text-[9px] leading-[1.35] text-foreground">
            {mergeReason}
            {onUndoMerge ? (
              <>
                {" "}
                <button
                  type="button"
                  className="underline underline-offset-2"
                  onClick={onUndoMerge}
                  disabled={disabled}
                >
                  Undo
                </button>
              </>
            ) : null}
          </div>
        ) : null}
        {isSplit && suggestReason && onAcceptSuggest ? (
          <button
            type="button"
            className="max-w-[320px] text-center text-[9px] leading-[1.35] text-foreground underline underline-offset-2"
            onClick={onAcceptSuggest}
            disabled={disabled}
          >
            these look like one bill
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function BoundaryStream({
  sessionId,
  pageCount,
  cuts,
  predictedCuts,
  excludedPages,
  zoom,
  onToggleBoundary,
  onToggleExclude,
  autoExcludedReasonByPage,
  overlayMergedCuts,
  overlayMergeReason,
  onUndoMerge,
  overlaySuggestCuts,
  overlaySuggestReason,
  onAcceptSuggest,
}: BoundaryStreamProps) {
  const pages = React.useMemo(
    () => Array.from({ length: pageCount }, (_, i) => i + 1),
    [pageCount]
  )
  const excluded = React.useMemo(() => {
    if (!excludedPages) return new Set<number>()
    return excludedPages instanceof Set
      ? excludedPages
      : new Set(excludedPages)
  }, [excludedPages])

  return (
    <div className="mx-auto w-full max-w-[812px] py-7 pb-20 pl-8 pr-0 max-[760px]:py-5">
      {pages.map((page) => {
        const boundaryIndex = page - 1
        const showBoundary = page < pageCount
        const pageExcluded = excluded.has(page)
        const nextExcluded = excluded.has(page + 1)
        const kind = showBoundary
          ? boundaryKind(
              boundaryIndex,
              cuts,
              predictedCuts,
              overlayMergedCuts
            )
          : null
        // Boundary sits before the next page — disabled if either side excluded.
        const boundaryDisabled = pageExcluded || nextExcluded
        const cutPage = cutPageForBoundary(boundaryIndex)

        return (
          <React.Fragment key={page}>
            <div className="relative">
              <div className="absolute top-2.5 -left-8 z-10 flex w-7 flex-col items-end gap-1">
                <span
                  className={cn(
                    "font-display text-[10px] font-semibold text-muted-foreground",
                    pageExcluded && "line-through opacity-70"
                  )}
                >
                  {page}
                </span>
                {onToggleExclude ? (
                  <button
                    type="button"
                    onClick={() => onToggleExclude(page)}
                    className={cn(
                      "inline-flex size-6 items-center justify-center border text-[9px]",
                      pageExcluded
                        ? "border-foreground bg-foreground text-background"
                        : "border-border bg-background text-muted-foreground hover:border-foreground"
                    )}
                    aria-pressed={pageExcluded}
                    aria-label={
                      pageExcluded
                        ? `Include page ${page}`
                        : `Exclude page ${page} from bills`
                    }
                    title={
                      pageExcluded
                        ? "Include this page"
                        : "Exclude — not a bill page"
                    }
                  >
                    <Ban className="size-3" strokeWidth={2} aria-hidden="true" />
                  </button>
                ) : null}
              </div>
              <LazyPageImage
                sessionId={sessionId}
                page={page}
                zoom={zoom}
                excluded={pageExcluded}
              />
              {pageExcluded ? (
                <div className="mt-1.5 text-center text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
                  {autoExcludedReasonByPage?.[page]
                    ? `Auto-excluded · ${autoExcludedReasonByPage[page]}`
                    : "Excluded · not a bill"}
                </div>
              ) : null}
            </div>
            {showBoundary && kind ? (
              <BoundaryControl
                kind={kind}
                disabled={boundaryDisabled}
                onToggle={() => onToggleBoundary(boundaryIndex)}
                mergeReason={overlayMergeReason?.[cutPage]}
                onUndoMerge={
                  onUndoMerge ? () => onUndoMerge(cutPage) : undefined
                }
                suggestReason={
                  overlaySuggestCuts?.has(cutPage)
                    ? overlaySuggestReason?.[cutPage]
                    : undefined
                }
                onAcceptSuggest={
                  onAcceptSuggest
                    ? () => onAcceptSuggest(cutPage)
                    : undefined
                }
              />
            ) : null}
          </React.Fragment>
        )
      })}
    </div>
  )
}
