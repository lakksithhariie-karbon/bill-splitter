/**
 * Production AP voucher form — EXTRACT / MASTER / DERIVED only.
 * MASTER slots never show grounding badges (they are not from the document).
 */
import * as React from "react"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { EmptyState } from "@/components/EmptyState"
import type {
  ExtractedDocumentV2,
  FieldValue,
  LineItemV2,
} from "@/lib/types"
import { MONO_FIELDS } from "@/lib/types"
import { currencySymbol, isAlreadyPaid } from "@/lib/invoice-display"
import { cn } from "@/lib/utils"

const UNGROUNDED_TIP =
  "This value was not found verbatim in the page text — the model may have invented it."
const IMPRECISE_TIP =
  "Source text is not a contiguous page span — evidence is imprecise."
const MISMATCH_TIP =
  "Normalised value does not match the verbatim source text."

export type PageDataPanelProps = {
  doc: ExtractedDocumentV2 | undefined
  onJumpToPage: (page: number) => void
  onChangeFieldValue: (path: string, value: string) => void
  onChangeOtherName?: (index: number, name: string) => void
  onChangeDocType?: (value: string) => void
  fieldConfidence?: Record<string, number>
  originalValues?: Record<string, string>
  /** Controlled voucher date (DERIVED — today by default). */
  voucherDate?: string
  onChangeVoucherDate?: (value: string) => void
}

export function countUngrounded(doc: ExtractedDocumentV2): number {
  let n = (doc.ungrounded ?? []).length
  for (const it of doc.line_items ?? []) {
    n += (it.ungrounded ?? []).length
  }
  return n
}

function todayISO(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function fvVal(fv: FieldValue | undefined): string {
  return (fv?.value || "").trim()
}

function SectionHead({
  title,
  note,
}: {
  title: string
  note?: string
}) {
  return (
    <div className="mb-3">
      <div className="font-display text-[13px] font-semibold">{title}</div>
      {note ? (
        <div className="mt-0.5 text-[10px] text-muted-foreground">{note}</div>
      ) : null}
    </div>
  )
}

function PageLinks({
  pages,
  onJumpToPage,
}: {
  pages: number[]
  onJumpToPage: (page: number) => void
}) {
  const unique = [...new Set(pages.filter((p) => Number.isFinite(p) && p >= 1))]
  if (unique.length === 0) {
    return <span className="text-muted-foreground">—</span>
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {unique.map((p, i) => (
        <React.Fragment key={p}>
          {i > 0 ? <span className="text-muted-foreground">·</span> : null}
          <button
            type="button"
            className="num text-foreground underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            onClick={() => onJumpToPage(p)}
          >
            p{p}
          </button>
        </React.Fragment>
      ))}
    </span>
  )
}

/** MASTER: empty slot — never grounded. */
function MasterSlot({
  label,
  placeholder = "Requires selection",
  hint,
}: {
  label: string
  placeholder?: string
  hint?: string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-[10px] font-semibold normal-case tracking-normal text-foreground">
          {label}
        </Label>
        <span className="text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
          Master
        </span>
      </div>
      <div
        className={cn(
          "flex h-9 items-center border border-dashed border-border bg-muted/40 px-2.5",
          "text-[11px] text-muted-foreground"
        )}
        aria-label={`${label}: requires selection`}
      >
        {placeholder}
      </div>
      {hint ? (
        <p className="text-[10px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

/** DERIVED: computed / system — not extracted. */
function DerivedField({
  label,
  value,
  readonly,
  onChange,
}: {
  label: string
  value: string
  readonly?: boolean
  onChange?: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-[10px] font-semibold normal-case tracking-normal text-foreground">
          {label}
        </Label>
        <span className="text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
          Derived
        </span>
      </div>
      <Input
        value={value}
        readOnly={readonly}
        disabled={readonly}
        onChange={
          onChange && !readonly
            ? (e) => onChange(e.target.value)
            : undefined
        }
        className={cn(
          "h-9 rounded-md text-[11px]",
          readonly && "bg-muted text-muted-foreground"
        )}
      />
    </div>
  )
}

function ExtractField({
  label,
  path,
  fv,
  ungrounded,
  imprecise,
  mismatch,
  mono,
  full,
  unit,
  evidenceFallback,
  onChange,
  onJumpToPage,
}: {
  label: string
  path: string
  fv: FieldValue | undefined
  ungrounded: boolean
  imprecise: boolean
  mismatch: boolean
  mono?: boolean
  full?: boolean
  unit?: string
  /** Extra source evidence (e.g. buyer.address for destination of supply). */
  evidenceFallback?: FieldValue
  onChange: (path: string, value: string) => void
  onJumpToPage: (page: number) => void
}) {
  const source =
    (fv?.source_text ?? "").trim() ||
    (evidenceFallback?.source_text ?? "").trim() ||
    (evidenceFallback?.value ?? "").trim()
  const pages =
    (fv?.pages?.length ? fv.pages : evidenceFallback?.pages) ?? []

  return (
    <div className={cn("flex flex-col gap-1.5", full && "col-span-2")}>
      <div className="flex items-center justify-between gap-2">
        <Label className="text-[10px] font-semibold normal-case tracking-normal text-foreground">
          {label}
        </Label>
        {ungrounded ? (
          <Badge
            variant="outline"
            className="border-warning bg-warning px-1.5 text-warning-foreground"
            title={UNGROUNDED_TIP}
          >
            UNGROUNDED
          </Badge>
        ) : null}
      </div>
      <div className="relative">
        {unit ? (
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-[10px] text-muted-foreground">
            {unit}
          </span>
        ) : null}
        <Input
          value={fv?.value ?? ""}
          onChange={(e) => onChange(path, e.target.value)}
          className={cn(
            "h-9 rounded-md text-[11px]",
            unit && "pl-7",
            (mono || MONO_FIELDS.has(path)) && "font-mono",
            ungrounded && "border-warning"
          )}
          aria-invalid={ungrounded || undefined}
          title={ungrounded ? UNGROUNDED_TIP : undefined}
        />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
        <span className="min-w-0 truncate" title={source || undefined}>
          {source || "—"}
        </span>
        <PageLinks pages={pages} onJumpToPage={onJumpToPage} />
      </div>
      {imprecise && !ungrounded ? (
        <p className="text-[10px] text-muted-foreground" title={IMPRECISE_TIP}>
          Evidence imprecise
        </p>
      ) : null}
      {mismatch && !ungrounded ? (
        <p className="text-[10px] text-muted-foreground" title={MISMATCH_TIP}>
          Value mismatch
        </p>
      ) : null}
    </div>
  )
}

function Collapsible({
  title,
  note,
  defaultOpen = false,
  children,
  count,
}: {
  title: string
  note?: string
  defaultOpen?: boolean
  children: React.ReactNode
  count?: number
}) {
  const [open, setOpen] = React.useState(defaultOpen)
  return (
    <section className="border-b border-border">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3.5 py-3 text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <div>
          <div className="font-display text-[13px] font-semibold">{title}</div>
          {note ? (
            <div className="mt-0.5 text-[10px] text-muted-foreground">{note}</div>
          ) : null}
        </div>
        <span className="text-[10px] text-muted-foreground">
          {count !== undefined ? `${count} · ` : ""}
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open ? <div className="px-3.5 pb-[18px]">{children}</div> : null}
    </section>
  )
}

function gstAmount(doc: ExtractedDocumentV2): FieldValue {
  const lines = (doc.totals?.tax_lines || []).filter((tl) => {
    const kind = (tl.kind?.value || tl.kind?.source_text || "")
      .trim()
      .toLowerCase()
    const name = (tl.name?.value || tl.name?.source_text || "")
      .trim()
      .toLowerCase()
    return kind === "gst" || /^(cgst|sgst|igst|gst)\b/.test(name)
  })
  if (lines.length === 0) {
    return { value: "", source_text: "", pages: [] }
  }
  if (lines.length === 1) return lines[0].amount
  // Sum display: prefer concatenated source; value as first non-empty joined.
  const values = lines.map((l) => fvVal(l.amount)).filter(Boolean)
  const sources = lines
    .map((l) => (l.amount.source_text || l.amount.value || "").trim())
    .filter(Boolean)
  const pages = lines.flatMap((l) => l.amount.pages || [])
  return {
    value: values.join(" + "),
    source_text: sources.join(" · "),
    pages,
  }
}

function taxableValue(doc: ExtractedDocumentV2): string {
  const sub = fvVal(doc.totals?.subtotal)
  if (sub) return sub
  // Derive from line amounts when subtotal empty.
  let sum = 0
  let any = false
  for (const it of doc.line_items || []) {
    const raw = fvVal(it.amount).replace(/,/g, "")
    const n = Number(raw)
    if (Number.isFinite(n) && raw) {
      sum += n
      any = true
    }
  }
  return any ? sum.toFixed(2) : ""
}

function flag(
  set: Set<string>,
  path: string
): boolean {
  return set.has(path)
}

export function PageDataPanel({
  doc,
  onJumpToPage,
  onChangeFieldValue,
  onChangeOtherName,
  voucherDate,
  onChangeVoucherDate,
}: PageDataPanelProps) {
  const [localVoucherDate, setLocalVoucherDate] = React.useState(todayISO)

  React.useEffect(() => {
    if (voucherDate === undefined) setLocalVoucherDate(todayISO())
  }, [doc?.page_start, doc?.page_end, voucherDate])

  if (!doc) {
    return (
      <EmptyState
        title="No extraction for this bill"
        body="Run extract on confirmed splits to load voucher fields."
      />
    )
  }

  const ungrounded = new Set(doc.ungrounded ?? [])
  const imprecise = new Set(doc.evidence_imprecise ?? [])
  const mismatch = new Set(doc.value_mismatch ?? [])
  const unit = currencySymbol(doc)
  const ungCount = countUngrounded(doc)
  const paid = isAlreadyPaid(doc)
  const vDate = voucherDate ?? localVoucherDate
  const setVDate = onChangeVoucherDate ?? setLocalVoucherDate
  const gst = gstAmount(doc)
  const taxable = taxableValue(doc)
  const destEvidence =
    !(doc.destination_of_supply?.source_text || "").trim() &&
    (doc.buyer?.address?.value || doc.buyer?.address?.source_text)
      ? doc.buyer.address
      : undefined

  const ex = (
    label: string,
    path: string,
    fv: FieldValue | undefined,
    opts?: {
      mono?: boolean
      full?: boolean
      unit?: string
      evidenceFallback?: FieldValue
    }
  ) => (
    <ExtractField
      label={label}
      path={path}
      fv={fv}
      ungrounded={flag(ungrounded, path)}
      imprecise={flag(imprecise, path)}
      mismatch={flag(mismatch, path)}
      mono={opts?.mono}
      full={opts?.full}
      unit={opts?.unit}
      evidenceFallback={opts?.evidenceFallback}
      onChange={onChangeFieldValue}
      onJumpToPage={onJumpToPage}
    />
  )

  return (
    <div className="flex flex-col">
      {paid ? (
        <div
          role="status"
          className="border-b border-warning/40 bg-warning/10 px-3.5 py-2.5 text-[11px] text-foreground"
        >
          This bill appears already paid (amount due is zero while grand
          total is not). Creating a purchase voucher may double-count the bill.
        </div>
      ) : null}

      {ungCount > 0 ? (
        <div className="flex items-center justify-between gap-2 border-b border-border px-3.5 py-2.5">
          <p className="text-[11px] text-muted-foreground">
            {ungCount} UNGROUNDED field{ungCount === 1 ? "" : "s"} — check
            against the page before approving.
          </p>
          <Badge variant="outline" className="border-warning text-warning">
            Grounding
          </Badge>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-2 border-b border-border px-3.5 py-2.5">
          <p className="text-[11px] text-muted-foreground">
            Extracted fields grounded against OCR.
          </p>
          <Badge variant="done" className="shrink-0">
            Grounded
          </Badge>
        </div>
      )}

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Voucher" note="Header for the purchase entry" />
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          <MasterSlot label="GST Registration (My Branch)" />
          <MasterSlot
            label="Voucher Type"
            placeholder="Purchase"
            hint="Default Purchase — select from masters"
          />
          <DerivedField label="Voucher No" value="Auto generated" readonly />
          <DerivedField
            label="Voucher Date"
            value={vDate}
            onChange={setVDate}
          />
          {ex("Bill Date", "issue_date", doc.issue_date, { mono: true })}
          {ex("Bill number", "document_id", doc.document_id, {
            mono: true,
          })}
        </div>
      </section>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Vendor details" note="Seller on the document" />
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          {ex("Vendor Name", "seller.name", doc.seller?.name)}
          {ex("Billing Address", "seller.address", doc.seller?.address, {
            full: true,
          })}
          <MasterSlot label="GST Treatment" />
          {ex("GSTIN", "seller.tax_id", doc.seller?.tax_id, { mono: true })}
          {ex("Source of Supply", "source_of_supply", doc.source_of_supply)}
          {ex(
            "Destination of Supply",
            "destination_of_supply",
            doc.destination_of_supply,
            { evidenceFallback: destEvidence }
          )}
        </div>
      </section>

      <Collapsible
        title="Additional details"
        note="2 sections available · Add details"
        count={5}
      >
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          {ex("Due date", "due_date", doc.due_date, { mono: true })}
          {ex("PO number", "po_number", doc.po_number, { mono: true })}
          {ex("Payment terms", "payment_terms", doc.payment_terms)}
          {ex("Billing period", "billing_period", doc.billing_period)}
          {ex("Shipping address", "shipping_address", doc.shipping_address, {
            full: true,
          })}
        </div>
      </Collapsible>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Item details" note="Goods and services only" />
        <div className="mb-3">
          <MasterSlot label="Purchase Ledger" />
        </div>
        <LineItemsTable
          items={doc.line_items || []}
          unit={unit}
          ungroundedDoc={ungrounded}
          impreciseDoc={imprecise}
          onChange={onChangeFieldValue}
          onJumpToPage={onJumpToPage}
        />
        <div className="mt-3 grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          <DerivedField
            label={`Taxable Value (${unit})`}
            value={taxable}
            readonly
          />
        </div>
      </section>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Ledgers" note="Coding masters — empty by default" />
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          <MasterSlot label="Ledger Name" />
          <MasterSlot label="Amount" placeholder="—" />
        </div>
      </section>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Taxes" />
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          {ex("Reverse Charges", "reverse_charge", doc.reverse_charge)}
          <MasterSlot label="Tax ledger" />
        </div>
      </section>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Narration" />
        {ex("Narration", "narration", doc.narration, { full: true })}
      </section>

      <section className="border-b border-border px-3.5 py-[18px]">
        <SectionHead title="Totals" note={`Amounts · ${unit}`} />
        {doc.totals_mismatch ? (
          <div
            role="status"
            className="mb-3 border border-warning/40 bg-warning/10 px-2.5 py-2 text-[11px] text-foreground"
          >
            Totals do not reconcile: subtotal + tax lines should equal grand
            total. A field may be misassigned (e.g. amount due written into
            total).
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1">
          {ex("Sub Total", "totals.subtotal", doc.totals?.subtotal, {
            mono: true,
            unit,
          })}
          <ExtractField
            label="GST"
            path="totals.tax_lines[0].amount"
            fv={gst}
            ungrounded={false}
            imprecise={false}
            mismatch={false}
            mono
            unit={unit}
            onChange={onChangeFieldValue}
            onJumpToPage={onJumpToPage}
          />
          {ex("TDS", "totals.tds", doc.totals?.tds, { mono: true, unit })}
          {ex("Other Taxes", "totals.other_taxes", doc.totals?.other_taxes, {
            mono: true,
            unit,
          })}
          {ex("Grand Total", "totals.total", doc.totals?.total, {
            mono: true,
            unit,
          })}
        </div>
      </section>

      <Collapsible
        title="Not mapped to the voucher"
        note="Unplaceable page content — inspect to discover missing model fields"
        count={(doc.other_fields || []).length}
      >
        {(doc.other_fields || []).length === 0 ? (
          <p className="text-[11px] text-muted-foreground">None.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {(doc.other_fields || []).map((of, i) => {
              const path = `other_fields[${i}].value`
              const namePath = `other_fields[${i}].${of.name || i}`
              return (
                <div
                  key={i}
                  className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1"
                >
                  <div className="flex flex-col gap-1.5">
                    <Label className="text-[10px] font-semibold normal-case tracking-normal">
                      Field name
                    </Label>
                    <Input
                      value={of.name || ""}
                      onChange={(e) => onChangeOtherName?.(i, e.target.value)}
                      className="h-9 rounded-md text-[11px]"
                      placeholder="Name"
                    />
                  </div>
                  <ExtractField
                    label="Value"
                    path={path}
                    fv={of.value}
                    ungrounded={
                      ungrounded.has(path) || ungrounded.has(namePath)
                    }
                    imprecise={
                      imprecise.has(path) || imprecise.has(namePath)
                    }
                    mismatch={mismatch.has(path) || mismatch.has(namePath)}
                    onChange={onChangeFieldValue}
                    onJumpToPage={onJumpToPage}
                  />
                </div>
              )
            })}
          </div>
        )}
      </Collapsible>
    </div>
  )
}

function LineItemsTable({
  items,
  unit,
  ungroundedDoc,
  impreciseDoc,
  onChange,
  onJumpToPage,
}: {
  items: LineItemV2[]
  unit: string
  ungroundedDoc: Set<string>
  impreciseDoc: Set<string>
  onChange: (path: string, value: string) => void
  onJumpToPage: (page: number) => void
}) {
  if (items.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground">
        No goods/services lines extracted.
      </p>
    )
  }

  const ordered = items
    .map((it, idx) => ({ it, idx }))
    .sort((a, b) => {
      const au = (a.it.ungrounded?.length ?? 0) > 0 ? 0 : 1
      const bu = (b.it.ungrounded?.length ?? 0) > 0 ? 0 : 1
      return au - bu
    })

  return (
    <div className="overflow-x-auto border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="min-w-[160px]">Description</TableHead>
            <TableHead className="min-w-[100px]">Item</TableHead>
            <TableHead className="min-w-[100px]">Godown/Location</TableHead>
            <TableHead className="w-[72px]">Qty</TableHead>
            <TableHead className="w-[88px]">Unit Rate</TableHead>
            <TableHead className="w-[72px]">Discount</TableHead>
            <TableHead className="w-[96px]">Amount ({unit})</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.map(({ it, idx }) => {
            const itemUngrounded = new Set(it.ungrounded || [])
            const itemImprecise = new Set(it.evidence_imprecise || [])
            const cell = (
              field: "description" | "quantity" | "unit_price" | "discount" | "amount",
              fv: FieldValue | undefined
            ) => {
              const path = `line_items[${idx}].${field}`
              const flagged =
                itemUngrounded.has(field) ||
                ungroundedDoc.has(path) ||
                ungroundedDoc.has(`line_items[${idx}].${field}`)
              const imp =
                itemImprecise.has(field) ||
                impreciseDoc.has(path) ||
                impreciseDoc.has(`line_items[${idx}].${field}`)
              return (
                <div
                  className={cn(
                    "flex flex-col gap-1",
                    flagged && "border-l-2 border-l-warning bg-warning/10 pl-1.5"
                  )}
                >
                  <Input
                    value={fv?.value ?? ""}
                    onChange={(e) => onChange(path, e.target.value)}
                    className={cn(
                      "h-8 rounded-md text-[11px]",
                      flagged && "border-warning"
                    )}
                  />
                  <div className="flex flex-wrap gap-1 text-[9px] text-muted-foreground">
                    {flagged ? (
                      <span className="font-semibold text-warning">UNG</span>
                    ) : null}
                    {imp && !flagged ? <span>imprecise</span> : null}
                    <PageLinks
                      pages={fv?.pages?.length ? fv.pages : [it.page]}
                      onJumpToPage={onJumpToPage}
                    />
                  </div>
                  {field === "description" && fvVal(it.hsn_sac) ? (
                    <div className="text-[9px] text-muted-foreground">
                      HSN/SAC {fvVal(it.hsn_sac)}
                    </div>
                  ) : null}
                </div>
              )
            }
            return (
              <TableRow key={idx}>
                <TableCell className="align-top">
                  {cell("description", it.description)}
                </TableCell>
                <TableCell className="align-top">
                  <div className="border border-dashed border-border bg-muted/40 px-2 py-1.5 text-[10px] text-muted-foreground">
                    Requires selection
                  </div>
                </TableCell>
                <TableCell className="align-top">
                  <div className="border border-dashed border-border bg-muted/40 px-2 py-1.5 text-[10px] text-muted-foreground">
                    Requires selection
                  </div>
                </TableCell>
                <TableCell className="align-top">
                  {cell("quantity", it.quantity)}
                </TableCell>
                <TableCell className="align-top">
                  {cell("unit_price", it.unit_price)}
                </TableCell>
                <TableCell className="align-top">
                  {cell("discount", it.discount)}
                </TableCell>
                <TableCell className="align-top">
                  {cell("amount", it.amount)}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
