/**
 * Step 3 — Bill review (split-first extract screen).
 * Layout mirrors docs/prototype/03-split-first.html #screen-extract.
 * Two independently scrolling panes; the page itself does not scroll.
 */
import * as React from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { PagePreview } from "@/components/PagePreview"
import {
  PageDataPanel,
  countUngrounded,
} from "@/components/PageDataPanel"
import { Button } from "@/components/ui/button"
import {
  defaultDisplayName,
  type DerivedDoc,
} from "@/lib/split-state"
import type {
  ExtractedDocumentV2,
  SegmentDisagreement,
} from "@/lib/types"
import { cn } from "@/lib/utils"

export type InvoiceReviewScreenProps = {
  sessionId: string
  filename: string
  pageCount: number
  /** Confirmed split ranges (optional display / fallback nav). */
  documents?: DerivedDoc[]
  extractionV2: ExtractedDocumentV2[]
  /** start_page → reviewer display name (not doc_type). */
  displayNames?: Record<number, string>
  selectedDocIndex: number
  setSelectedDocIndex: (index: number) => void
  selectedPage: number
  setSelectedPage: (page: number) => void
  segmentDisagreements?: SegmentDisagreement[]
  onChangeField: (docIndex: number, path: string, value: string) => void
  onChangeOtherName?: (docIndex: number, index: number, name: string) => void
  onChangeDocType?: (docIndex: number, value: string) => void
  /** Local UI only — do not call a production AP endpoint. */
  onApproveNext: () => void
  onSave?: () => void
  saving?: boolean
  /** Optional per-field confidence map for the current document. */
  fieldConfidence?: Record<string, number>
  /** Push confirmed bills to AIA (Mode A). Disabled unless allowWrites. */
  onPushAia?: () => void
  pushingAia?: boolean
  aiaAllowWrites?: boolean
  aiaDisabledReason?: string | null
  aiaPushSummary?: string | null
}

function formatProposedSplits(
  d: SegmentDisagreement
): string {
  return d.proposed_splits
    .map((s) => {
      const range =
        s.page_start === s.page_end
          ? `p${s.page_start}`
          : `p${s.page_start}–${s.page_end}`
      return `${range} (${s.doc_type || "unknown"})`
    })
    .join(", ")
}

function disagreementForDoc(
  doc: ExtractedDocumentV2,
  list: SegmentDisagreement[] | undefined
): SegmentDisagreement | undefined {
  if (!list?.length) return undefined
  return list.find(
    (d) =>
      (d.page_start <= doc.page_start && d.page_end >= doc.page_end) ||
      (doc.page_start <= d.page_start && doc.page_end >= d.page_end) ||
      (d.page_start === doc.page_start && d.page_end === doc.page_end)
  )
}

function invoiceTitle(
  doc: ExtractedDocumentV2 | undefined,
  index: number,
  documents: DerivedDoc[] | undefined,
  displayNames: Record<number, string> | undefined
): string {
  if (!doc) return defaultDisplayName(index)
  const derived = documents?.find(
    (d) => d.start_page === doc.page_start && d.end_page === doc.page_end
  )
  if (derived?.display_name) return derived.display_name
  const named = displayNames?.[doc.page_start]?.trim()
  if (named) return named
  return defaultDisplayName(index)
}

export function InvoiceReviewScreen({
  sessionId,
  filename,
  pageCount,
  documents,
  extractionV2,
  displayNames,
  selectedDocIndex,
  setSelectedDocIndex,
  selectedPage,
  setSelectedPage,
  segmentDisagreements,
  onChangeField,
  onChangeOtherName,
  onChangeDocType,
  onApproveNext,
  onSave,
  saving = false,
  fieldConfidence,
  onPushAia,
  pushingAia = false,
  aiaAllowWrites = false,
  aiaDisabledReason = null,
  aiaPushSummary = null,
}: InvoiceReviewScreenProps) {
  const [zoom, setZoom] = React.useState(1)
  const pdfScrollRef = React.useRef<HTMLDivElement>(null)
  const total = extractionV2.length
  const safeIndex =
    total === 0 ? 0 : Math.min(Math.max(selectedDocIndex, 0), total - 1)
  const doc = total > 0 ? extractionV2[safeIndex] : undefined
  const title = invoiceTitle(doc, safeIndex, documents, displayNames)

  React.useEffect(() => {
    setZoom(1)
  }, [selectedPage, safeIndex])

  React.useEffect(() => {
    if (!doc) return
    if (selectedPage < doc.page_start || selectedPage > doc.page_end) {
      setSelectedPage(doc.page_start)
    }
  }, [doc, selectedPage, setSelectedPage])

  const goToDoc = (index: number) => {
    if (index < 0 || index >= total) return
    setSelectedDocIndex(index)
    const next = extractionV2[index]
    if (next) setSelectedPage(next.page_start)
  }

  const handleApprove = () => {
    onApproveNext()
    if (safeIndex < total - 1) {
      goToDoc(safeIndex + 1)
    }
  }

  const handleJumpToPage = (page: number) => {
    setSelectedPage(page)
    // Scroll only the left PDF pane.
    const el = pdfScrollRef.current
    if (el) el.scrollTo({ top: 0, behavior: "smooth" })
  }

  const ungCount = doc ? countUngrounded(doc) : 0
  const disagreement = doc
    ? disagreementForDoc(doc, segmentDisagreements)
    : undefined

  const pageLabel = doc
    ? doc.page_start === doc.page_end
      ? `Page ${doc.page_start}`
      : `Pages ${doc.page_start}–${doc.page_end}`
    : "—"

  const paneHeight =
    "h-[calc(100vh-var(--topbar-height)-99px)] min-h-[720px] max-[1080px]:h-auto max-[1080px]:min-h-[600px]"

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden max-[1080px]:overflow-visible">
      <PageHeader
        eyebrow="Step 3 of 3 · Review extracted bill"
        title="Bill review"
        description="Each confirmed boundary becomes one bill. Review extracted fields against the source pages before approving the voucher."
      />

      <div
        className="extract-layout grid min-h-0 border border-border bg-background max-[1080px]:grid-cols-1"
        style={{
          gridTemplateColumns: "minmax(390px, 43%) minmax(0, 57%)",
        }}
      >
        {/* PDF pane ~43% */}
        <section className="pdf-pane flex min-w-0 flex-col border-r border-border bg-muted max-[1080px]:border-r-0 max-[1080px]:border-b">
          <div className="flex min-h-[54px] shrink-0 items-center justify-between gap-2.5 border-b border-border bg-background px-3 py-2.5">
            <div className="min-w-0">
              <div className="font-display truncate text-[12px] font-semibold">
                {title}
                {doc ? ` · ${pageLabel}` : ""}
              </div>
              <div className="truncate text-label font-normal normal-case tracking-normal text-muted-foreground">
                {filename || "Untitled PDF"}
              </div>
            </div>
          </div>
          <div
            ref={pdfScrollRef}
            className={cn("pdf-scroll overflow-auto", paneHeight)}
          >
            <PagePreview
              sessionId={sessionId}
              selectedPage={selectedPage}
              pageCount={pageCount}
              zoom={zoom}
              onZoomChange={setZoom}
              showToolbar
            />
          </div>
        </section>

        {/* Form pane ~57% */}
        <section className="form-pane flex min-w-0 flex-col bg-background">
          <div
            className={cn(
              "sticky top-0 z-20 flex min-h-[54px] shrink-0 items-center justify-between gap-3 border-b border-border px-3.5 py-2.5",
              "bg-[color-mix(in_oklch,var(--background)_97%,transparent)] backdrop-blur-[8px]"
            )}
          >
            <div className="min-w-0">
              <div className="font-display text-md font-semibold leading-none">
                Voucher data
              </div>
              <div className="mt-[3px] text-label font-normal normal-case tracking-normal text-muted-foreground">
                Auto-extracted · editable before approval
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1.5 text-[10px] text-muted-foreground">
              <Button
                type="button"
                variant="outline"
                size="icon-xs"
                className="rounded-md"
                aria-label="Previous bill"
                disabled={safeIndex <= 0 || total === 0}
                onClick={() => goToDoc(safeIndex - 1)}
              >
                <ChevronLeft aria-hidden="true" />
              </Button>
              <span className="num whitespace-nowrap px-1">
                {title} · {total === 0 ? 0 : safeIndex + 1} of {total}
              </span>
              <Button
                type="button"
                variant="outline"
                size="icon-xs"
                className="rounded-md"
                aria-label="Next bill"
                disabled={safeIndex >= total - 1 || total === 0}
                onClick={() => goToDoc(safeIndex + 1)}
              >
                <ChevronRight aria-hidden="true" />
              </Button>
            </div>
          </div>

          <div className={cn("form-scroll relative overflow-auto", paneHeight)}>
            {disagreement ? (
              <div
                role="status"
                className="border-b border-warning/40 bg-warning/10 px-3.5 py-2.5 text-[11px] text-foreground"
              >
                Model thinks this may be two documents — proposed splits:{" "}
                {formatProposedSplits(disagreement)}. Confirm or ignore; do not
                auto-subdivide.
              </div>
            ) : null}

            <PageDataPanel
              doc={doc}
              onJumpToPage={handleJumpToPage}
              onChangeFieldValue={(path, value) =>
                onChangeField(safeIndex, path, value)
              }
              onChangeOtherName={
                onChangeOtherName
                  ? (index, name) => onChangeOtherName(safeIndex, index, name)
                  : undefined
              }
              onChangeDocType={
                onChangeDocType
                  ? (value) => onChangeDocType(safeIndex, value)
                  : undefined
              }
              fieldConfidence={fieldConfidence}
            />

            <div
              className={cn(
                "sticky bottom-0 z-20 flex items-center justify-between gap-2.5 border-t border-border px-3.5 py-2.5",
                "bg-[color-mix(in_oklch,var(--background)_97%,transparent)] backdrop-blur-[8px]"
              )}
            >
              <p className="max-w-[420px] text-[11px] text-muted-foreground">
                {ungCount > 0
                  ? `${ungCount} field${ungCount === 1 ? "" : "s"} UNGROUNDED. You can still approve after checking them.`
                  : "Ready to approve this bill and continue."}
              </p>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                {onPushAia ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="rounded-md"
                    disabled={!aiaAllowWrites || pushingAia || total < 1}
                    title={
                      aiaAllowWrites
                        ? "Upload each confirmed bill PDF to AI Accountant"
                        : aiaDisabledReason ||
                          "Set AIA_ALLOW_WRITES=true (and AIA_API_BASE) to enable"
                    }
                    onClick={onPushAia}
                  >
                    {pushingAia ? "Pushing…" : "Push to AI Accountant"}
                  </Button>
                ) : null}
                {!aiaAllowWrites && aiaDisabledReason ? (
                  <p
                    role="status"
                    className="max-w-[280px] border border-warning/40 bg-warning/10 px-2 py-1.5 text-[10px] leading-[1.4] text-foreground"
                  >
                    {aiaDisabledReason}
                  </p>
                ) : null}
                {onSave ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="rounded-md"
                    disabled={saving || !doc}
                    onClick={onSave}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="default"
                  className="rounded-md"
                  disabled={!doc}
                  onClick={handleApprove}
                >
                  {safeIndex < total - 1 ? "Approve & next" : "Approve"}
                </Button>
              </div>
            </div>
            {aiaPushSummary ? (
              <p
                className="border-t border-border px-3.5 py-2 text-[11px] text-muted-foreground"
                role="status"
              >
                {aiaPushSummary}
              </p>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  )
}
