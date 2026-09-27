import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import type { DerivedDoc } from "@/lib/split-state"
import type { DuplicateGroupMember } from "@/lib/types"

export type GroupPanelProps = {
  documents: DerivedDoc[]
  labels: Record<number, string>
  selectedPage: number
  duplicateGroups?: DuplicateGroupMember[][]
  confidenceByStartPage?: Record<number, number>
  onSelectPage: (page: number) => void
  onAddCut: (page: number) => void
  onRemoveCut: (page: number) => void
  onRename: (startPage: number, label: string) => void
  onSave: () => void
  saving: boolean
  saveDisabled?: boolean
}

function pagesInDoc(doc: DerivedDoc): number[] {
  const pages: number[] = []
  for (let p = doc.start_page; p <= doc.end_page; p++) pages.push(p)
  return pages
}

function displayName(
  doc: DerivedDoc,
  index: number,
  labels: Record<number, string>
): string {
  const custom = labels[doc.start_page]
  if (custom && custom !== "unknown") return custom
  return `Doc ${index + 1}`
}

function isDuplicate(
  doc: DerivedDoc,
  groups: DuplicateGroupMember[][]
): boolean {
  for (const group of groups) {
    if (
      group.some(
        (m) =>
          m.page_start === doc.start_page && m.page_end === doc.end_page
      )
    ) {
      return true
    }
  }
  return false
}

function confidenceForDoc(
  doc: DerivedDoc,
  map?: Record<number, number>
): number {
  if (map && doc.start_page in map) return map[doc.start_page]
  return doc.confidence
}

export function GroupPanel({
  documents,
  labels,
  selectedPage,
  duplicateGroups = [],
  confidenceByStartPage,
  onSelectPage,
  onAddCut,
  onRemoveCut,
  onRename,
  onSave,
  saving,
  saveDisabled = false,
}: GroupPanelProps) {
  const selectedDoc = documents.find(
    (d) => selectedPage >= d.start_page && selectedPage <= d.end_page
  )

  const handleRename = (doc: DerivedDoc, index: number) => {
    const current = displayName(doc, index, labels)
    const next = window.prompt("Rename group", current)
    if (next != null && next.trim()) onRename(doc.start_page, next.trim())
  }

  const handleSplit = (doc: DerivedDoc) => {
    if (selectedPage > doc.start_page && selectedPage <= doc.end_page) {
      onAddCut(selectedPage)
      return
    }
    if (doc.end_page > doc.start_page) {
      onAddCut(doc.start_page + 1)
    }
  }

  const canSplit = (doc: DerivedDoc) => doc.end_page > doc.start_page

  return (
    <aside className="review-groups min-w-0 border-l border-border bg-card">
      <div className="panel-head flex min-h-[54px] items-center justify-between gap-2 border-b border-border px-3 py-[11px]">
        <div className="min-w-0">
          <div className="text-2xs-ui font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Detected boundaries
          </div>
          <div className="panel-title mt-0.5 truncate text-xs font-semibold">
            {documents.length} document group
            {documents.length === 1 ? "" : "s"}
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="tiny"
          className="shrink-0 rounded-md"
          onClick={() => onAddCut(selectedPage)}
        >
          + Group
        </Button>
      </div>

      <div className="group-list grid max-h-[calc(100vh-58px-225px)] gap-[9px] overflow-auto p-2.5">
        {documents.map((doc, index) => {
          const selected = selectedDoc?.start_page === doc.start_page
          const conf = confidenceForDoc(doc, confidenceByStartPage)
          const confPct = Math.round(conf * 100)
          const dup = isDuplicate(doc, duplicateGroups)
          const name = displayName(doc, index, labels)
          const pages = pagesInDoc(doc)

          return (
            <section
              key={`${doc.start_page}-${doc.end_page}`}
              className={cn(
                "group-card border border-border bg-background",
                selected && "border-foreground"
              )}
            >
              <div className="flex items-start justify-between gap-2 border-b border-border px-2.5 py-2">
                <div className="min-w-0">
                  <div className="group-name truncate text-[11px] font-semibold">
                    {name}
                  </div>
                  <div className="mt-0.5 text-[9px] text-muted-foreground">
                    {confPct >= 90 ? "Checked" : "Needs a look"}
                  </div>
                </div>
                {dup && (
                  <span className="inline-flex h-6 shrink-0 items-center border border-[color-mix(in_oklch,var(--destructive)_30%,var(--border))] bg-background px-1.5 text-[10px] font-semibold text-destructive">
                    Duplicate
                  </span>
                )}
              </div>

              <div className="flex flex-wrap gap-1.5 p-2">
                {pages.map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={cn(
                      "inline-flex h-[30px] min-w-[34px] cursor-pointer items-center justify-center border border-border bg-muted text-[10px] font-semibold",
                      p === selectedPage && "border-foreground bg-background"
                    )}
                    onClick={() => onSelectPage(p)}
                    aria-label={`Page ${p}`}
                    aria-pressed={p === selectedPage}
                  >
                    {p}
                  </button>
                ))}
              </div>

              <div className="flex gap-1.5 border-t border-border p-2">
                <Button
                  type="button"
                  variant="outline"
                  size="tiny"
                  className="rounded-md"
                  onClick={() => handleRename(doc, index)}
                >
                  Rename
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="tiny"
                  className="rounded-md"
                  disabled={!canSplit(doc)}
                  onClick={() => handleSplit(doc)}
                >
                  Split
                </Button>
                {index > 0 && (
                  <Button
                    type="button"
                    variant="outline"
                    size="tiny"
                    className="rounded-md"
                    onClick={() => onRemoveCut(doc.start_page)}
                  >
                    Merge up
                  </Button>
                )}
              </div>
            </section>
          )
        })}
      </div>

      <div className="grid gap-2 border-t border-border bg-background p-2.5">
        <div className="border border-border bg-muted px-2 py-2 text-[9px] leading-snug text-muted-foreground">
          Click page chips to navigate. Split adds a boundary at the selected
          page; Merge up joins this group with the one above.
        </div>
        <Button
          type="button"
          variant="default"
          className="w-full rounded-md"
          disabled={saveDisabled || saving}
          onClick={onSave}
        >
          {saving ? "Saving…" : "Save & Download"}
        </Button>
      </div>
    </aside>
  )
}
