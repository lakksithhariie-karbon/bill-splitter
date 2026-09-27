import * as React from "react"
import { cn } from "@/lib/utils"
import { pageImageUrl } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { Button } from "@/components/ui/button"
import { Minus, Plus } from "lucide-react"

const ZOOM_MIN = 0.5
const ZOOM_MAX = 2
const ZOOM_STEP = 0.1

export type PagePreviewProps = {
  sessionId: string | null
  selectedPage: number
  pageCount: number
  zoom?: number
  onZoomChange?: (zoom: number) => void
  showToolbar?: boolean
}

function clampZoom(value: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 10) / 10))
}

export function PagePreview({
  sessionId,
  selectedPage,
  pageCount,
  zoom: zoomProp,
  onZoomChange,
  showToolbar = false,
}: PagePreviewProps) {
  const [internalZoom, setInternalZoom] = React.useState(1)
  const zoom = zoomProp ?? internalZoom
  const setZoom = onZoomChange ?? setInternalZoom

  const adjustZoom = (delta: number) => {
    setZoom(clampZoom(zoom + delta))
  }

  if (!sessionId || pageCount === 0) {
    return (
      <EmptyState
        title="No page selected"
        body="Upload a PDF, then click a thumbnail to view it larger."
      />
    )
  }
  if (!selectedPage) {
    return (
      <EmptyState
        title="Pick a page"
        body="Click a thumbnail in the filmstrip to open it here."
      />
    )
  }

  const zoomPct = Math.round(zoom * 100)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {showToolbar && (
        <div className="canvas-toolbar flex h-[54px] shrink-0 items-center justify-between gap-2.5 border-b border-border bg-background px-2.5">
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="rounded-md"
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
              className="rounded-md"
              aria-label="Zoom in"
              disabled={zoom >= ZOOM_MAX}
              onClick={() => adjustZoom(ZOOM_STEP)}
            >
              <Plus aria-hidden="true" />
            </Button>
          </div>
          <span className="inline-flex h-6 items-center gap-1.5 border border-border bg-background px-1.5 text-[10px] font-semibold">
            Page {selectedPage} of {pageCount}
          </span>
        </div>
      )}

      <div
        className={cn(
          "doc-stage flex min-h-[620px] flex-1 items-start justify-center p-[34px]",
          // Parent .pdf-scroll owns overflow; avoid a second nested scroller.
          showToolbar ? "min-h-0" : "overflow-auto"
        )}
        style={{
          background: `
            linear-gradient(45deg, color-mix(in oklch, var(--muted) 80%, white) 25%, transparent 25%),
            linear-gradient(-45deg, color-mix(in oklch, var(--muted) 80%, white) 25%, transparent 25%),
            linear-gradient(45deg, transparent 75%, color-mix(in oklch, var(--muted) 80%, white) 75%),
            linear-gradient(-45deg, transparent 75%, color-mix(in oklch, var(--muted) 80%, white) 75%)
          `,
          backgroundSize: "20px 20px",
          backgroundPosition: "0 0, 0 10px, 10px -10px, -10px 0px",
        }}
      >
        <img
          src={pageImageUrl(sessionId, selectedPage, "view")}
          alt={`Page ${selectedPage}`}
          className="max-w-full border border-border bg-white"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: "top center",
          }}
        />
      </div>
    </div>
  )
}

export { ZOOM_MIN, ZOOM_MAX, ZOOM_STEP, clampZoom }
