import { Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { cn } from "@/lib/utils"

export type UploadStatusVariant =
  | "ready"
  | "processing"
  | "review"
  | "done"
  | "error"

export interface UploadScreenProps {
  hasSession: boolean
  filename: string | null
  pageCount: number
  documentCount: number
  statusLabel: string
  statusVariant?: UploadStatusVariant
  onChooseFile: () => void
  analyzing?: boolean
  analyzeElapsed?: number
  children?: React.ReactNode
}

const FLOW_STEPS = [
  {
    number: "01",
    title: "Upload PDF",
    description: "Render every source page.",
    active: true,
  },
  {
    number: "02",
    title: "Confirm boundaries",
    description: "Keep, remove, or add bill breaks.",
    active: false,
  },
  {
    number: "03",
    title: "Download split PDFs",
    description: "Get one PDF per bill in a single ZIP file.",
    active: false,
  },
] as const

function StatusBadge({
  label,
  variant = "ready",
}: {
  label: string
  variant?: UploadStatusVariant
}) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1.5 border px-1.5 text-[10px] font-semibold whitespace-nowrap",
        variant === "done" &&
          "border-primary bg-primary text-primary-foreground",
        variant === "review" && "border-border bg-background",
        variant === "error" &&
          "border-destructive/30 bg-background text-destructive",
        (variant === "ready" || variant === "processing") &&
          "border-border bg-secondary text-secondary-foreground"
      )}
    >
      <span className="size-1.5 rounded-full bg-current opacity-60" />
      {label}
    </span>
  )
}

function SummaryRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid min-h-[47px] grid-cols-[1fr_auto] items-center gap-3 border-b border-border px-3 py-2.5 last:border-b-0">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="summary-value text-[13px] font-semibold">{value}</div>
    </div>
  )
}

export function UploadScreen({
  hasSession,
  filename,
  pageCount,
  documentCount,
  statusLabel,
  statusVariant = "ready",
  onChooseFile,
  analyzing = false,
  children,
}: UploadScreenProps) {
  const badgeVariant = analyzing ? "processing" : statusVariant
  const badgeLabel = analyzing ? "Detecting" : statusLabel

  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <Card className="gap-0 py-0 [--card-spacing:0px]">
        <CardHeader className="min-h-[54px] border-b border-border px-3.5 py-3">
          <div>
            <CardTitle className="font-display text-[13px] font-semibold">
              Upload source PDF
            </CardTitle>
            <CardDescription className="mt-0.5 text-[11px]">
              Multi-bill PDFs supported
            </CardDescription>
          </div>
          <CardAction>
            <StatusBadge label={badgeLabel} variant={badgeVariant} />
          </CardAction>
        </CardHeader>

        <CardContent className="p-0">
          <div
            className="grid min-h-[480px] place-items-center p-6"
            style={{
              backgroundImage: [
                "linear-gradient(var(--border) 1px, transparent 1px)",
                "linear-gradient(90deg, var(--border) 1px, transparent 1px)",
              ].join(", "),
              backgroundSize: "32px 32px",
              backgroundPosition: "-1px -1px",
            }}
          >
            <div className="w-full max-w-[440px] border border-foreground bg-background p-7 text-center">
              <div className="mx-auto mb-3.5 grid size-[38px] place-items-center bg-primary text-primary-foreground">
                <Upload className="size-[17px]" strokeWidth={1.7} aria-hidden="true" />
              </div>
              <h2 className="font-display text-base font-semibold">
                Drop a PDF here
              </h2>
              <p className="mx-auto mt-2 mb-4 max-w-[330px] text-xs leading-[1.55] text-muted-foreground">
                Upload a combined bill PDF. We’ll suggest where each bill starts
                so you can review the splits before downloading.
              </p>
              <Button
                type="button"
                size="sm"
                className="h-[34px] px-3 text-xs font-semibold"
                onClick={onChooseFile}
                disabled={analyzing}
              >
                Choose PDF
              </Button>
              <div className="mt-3 text-[10px] text-muted-foreground">
                Review the suggested splits, then download one PDF per bill in a
                ZIP file.
              </div>
              {children ? <div className="mt-4">{children}</div> : null}
            </div>
          </div>
        </CardContent>
      </Card>

      <aside className="grid gap-3.5">
        {hasSession ? (
          <Card className="gap-0 py-0 [--card-spacing:0px]">
            <CardHeader className="min-h-[54px] border-b border-border px-3.5 py-3">
              <div>
                <CardTitle className="font-display text-[13px] font-semibold">
                  Session
                </CardTitle>
                <CardDescription className="mt-0.5 text-[11px]">
                  Current upload
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="grid">
                <SummaryRow label="File" value={filename ?? "None"} />
                <SummaryRow label="Pages" value={pageCount} />
                <SummaryRow
                  label="Bills found"
                  value={documentCount > 0 ? documentCount : "—"}
                />
                <SummaryRow label="Status" value={statusLabel} />
              </div>
            </CardContent>
          </Card>
        ) : null}

        <Card className="gap-0 py-0 [--card-spacing:0px]">
          <CardHeader className="min-h-[54px] border-b border-border px-3.5 py-3">
            <div>
              <CardTitle className="font-display text-[13px] font-semibold">
                Workflow
              </CardTitle>
              <CardDescription className="mt-0.5 text-[11px]">
                Three quick steps
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="grid gap-2 p-3">
            {FLOW_STEPS.map((step) => (
              <div
                key={step.number}
                className={cn(
                  "grid grid-cols-[22px_minmax(0,1fr)] items-start gap-2 border bg-background p-2",
                  step.active ? "border-foreground" : "border-border"
                )}
              >
                <div className="grid size-[22px] place-items-center bg-secondary text-[10px] font-bold">
                  {step.number}
                </div>
                <div>
                  <strong className="block text-[11px] font-semibold">
                    {step.title}
                  </strong>
                  <span className="mt-0.5 block text-[10px] leading-[1.4] text-muted-foreground">
                    {step.description}
                  </span>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </aside>
    </div>
  )
}
