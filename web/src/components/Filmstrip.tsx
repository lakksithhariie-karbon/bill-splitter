import type { ReactNode } from "react"
import { cn } from "@/lib/utils"
import { pageImageUrl } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import type { DerivedDoc } from "@/lib/split-state"

export type FilmstripProps = {
  pageCount: number
  selectedPage: number
  onSelectPage: (page: number) => void
  thumbnails?: string[]
  /** Review column: session + metadata */
  sessionId?: string
  filename?: string
  documents?: DerivedDoc[]
  labels?: Record<number, string>
  /** Legacy horizontal filmstrip (pre-ReviewScreen) */
  cuts?: number[]
  onToggleCut?: (page: number) => void
}

function docForPage(page: number, documents: DerivedDoc[]): DerivedDoc | undefined {
  return documents.find((d) => page >= d.start_page && page <= d.end_page)
}

function docTag(
  doc: DerivedDoc | undefined,
  documents: DerivedDoc[],
  labels: Record<number, string> | undefined
): string {
  if (!doc) return ""
  const index = documents.findIndex((d) => d.start_page === doc.start_page)
  const custom = labels?.[doc.start_page]
  if (custom && custom !== "unknown") {
    const short = custom.length > 8 ? `${custom.slice(0, 7)}…` : custom
    return short
  }
  return `Doc ${index + 1}`
}

/** Legacy horizontal filmstrip with cut toggles. */
function LegacyFilmstrip({
  pageCount,
  thumbnails = [],
  cuts,
  selectedPage,
  onSelectPage,
  onToggleCut,
}: Required<Pick<FilmstripProps, "pageCount" | "selectedPage" | "onSelectPage">> &
  Pick<FilmstripProps, "thumbnails" | "cuts" | "onToggleCut">) {
  const cells: ReactNode[] = []
  for (let p = 1; p <= pageCount; p++) {
    if (p > 1 && cuts && onToggleCut) {
      const active = cuts.includes(p)
      cells.push(
        <button
          key={`cut-${p}`}
          type="button"
          title={active ? "Remove boundary" : "Add boundary"}
          aria-label={
            active
              ? `Document boundary before page ${p}. Click to remove.`
              : `Add document boundary before page ${p}`
          }
          aria-pressed={active}
          onClick={() => onToggleCut(p)}
          className={cn(
            "relative flex shrink-0 cursor-pointer items-stretch justify-center outline-none",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
            active
              ? "mx-1 w-3 bg-primary hover:bg-primary/90"
              : "w-2.5 bg-transparent hover:[&_.cut-line]:bg-primary/70"
          )}
        >
          {!active && (
            <div className="cut-line my-1 w-px self-stretch bg-border" />
          )}
          {active && (
            <span
              className="absolute top-0 left-1/2 size-1.5 -translate-x-1/2 bg-primary-foreground"
              aria-hidden="true"
            />
          )}
        </button>
      )
    }
    cells.push(
      <button
        key={`page-${p}`}
        type="button"
        className="relative shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        onClick={() => onSelectPage(p)}
        aria-label={`Page ${p}`}
        aria-pressed={p === selectedPage}
      >
        <img
          src={thumbnails[p - 1]}
          alt={`Page ${p}`}
          className={cn(
            "block h-[140px] w-auto border-2 border-transparent transition-colors",
            p === selectedPage && "border-primary",
            "hover:border-primary/50"
          )}
        />
        <div className="absolute bottom-0.5 left-0.5 bg-foreground/80 px-1.5 py-px text-label num font-normal normal-case tracking-normal text-background">
          {p}
        </div>
      </button>
    )
  }

  return (
    <div className="flex gap-0 overflow-x-auto border border-border bg-muted/30 py-1">
      {cells}
    </div>
  )
}

/** Review left column or legacy horizontal filmstrip. */
export function Filmstrip(props: FilmstripProps) {
  const {
    pageCount,
    selectedPage,
    onSelectPage,
    thumbnails,
    sessionId,
    filename = "Document",
    documents = [],
    labels,
    cuts,
    onToggleCut,
  } = props

  if (pageCount === 0) {
    return (
      <EmptyState
        title="No pages yet"
        body="Upload a PDF to see page thumbnails here."
      />
    )
  }

  if (!sessionId) {
    return (
      <LegacyFilmstrip
        pageCount={pageCount}
        thumbnails={thumbnails}
        cuts={cuts}
        selectedPage={selectedPage}
        onSelectPage={onSelectPage}
        onToggleCut={onToggleCut}
      />
    )
  }

  return (
    <aside className="review-pages min-w-0 border-r border-border bg-card">
      <div className="panel-head flex min-h-[54px] items-center justify-between gap-2 border-b border-border px-3 py-[11px]">
        <div className="min-w-0">
          <div className="text-2xs-ui font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Source PDF
          </div>
          <div
            className="panel-title mt-0.5 truncate text-xs font-semibold"
            title={filename}
          >
            {filename}
          </div>
        </div>
        <span className="inline-flex h-6 shrink-0 items-center gap-1.5 border border-border bg-secondary px-1.5 text-[10px] font-semibold text-secondary-foreground">
          {pageCount}p
        </span>
      </div>

      <div className="thumb-list grid max-h-[calc(100vh-58px-171px)] gap-[9px] overflow-auto p-2.5">
        {Array.from({ length: pageCount }, (_, i) => {
          const page = i + 1
          const doc = docForPage(page, documents)
          const tag = docTag(doc, documents, labels)
          const thumbSrc =
            thumbnails?.[i] ?? pageImageUrl(sessionId, page, "thumb")

          return (
            <button
              key={page}
              type="button"
              className={cn(
                "thumb relative cursor-pointer border border-border bg-background p-1.5 text-left outline-none",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                page === selectedPage &&
                  "border-foreground shadow-[inset_3px_0_0_0_var(--foreground)]"
              )}
              onClick={() => onSelectPage(page)}
              aria-label={`Page ${page}`}
              aria-pressed={page === selectedPage}
            >
              <div className="thumb-page aspect-[0.72] overflow-hidden border border-border bg-white p-2">
                <img
                  src={thumbSrc}
                  alt={`Page ${page}`}
                  className="block size-full object-contain object-top"
                  loading="lazy"
                />
              </div>
              <div className="flex items-center justify-between px-px pt-1.5 text-[9px] text-muted-foreground">
                <span>Page {page}</span>
                {tag ? <span className="truncate pl-1">{tag}</span> : null}
              </div>
            </button>
          )
        })}
      </div>
    </aside>
  )
}
