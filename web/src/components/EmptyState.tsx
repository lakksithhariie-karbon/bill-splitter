import { FileText } from "lucide-react"
import { cn } from "@/lib/utils"

export type EmptyStateProps = {
  title: string
  body: string
  className?: string
}

/** Designed empty state — muted surface, heading + body, not a loose paragraph. */
export function EmptyState({ title, body, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-4 py-10 text-center",
        "border border-dashed border-border bg-muted/50",
        className
      )}
    >
      <div className="grid size-10 place-items-center bg-primary text-primary-foreground">
        <FileText className="size-5" strokeWidth={1.7} aria-hidden="true" />
      </div>
      <p className="font-display text-base font-semibold tracking-tight text-foreground">
        {title}
      </p>
      <p className="max-w-[28rem] text-sm leading-normal text-muted-foreground">
        {body}
      </p>
    </div>
  )
}
