import type * as React from "react"
import { cn } from "@/lib/utils"

type PageHeaderProps = {
  eyebrow: string
  title: string
  description?: string
  actions?: React.ReactNode
  className?: string
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "mb-[18px] flex flex-col items-start justify-between gap-5 min-[1100px]:flex-row min-[1100px]:items-end",
        className
      )}
    >
      <div className="min-w-0">
        <p className="mb-[5px] text-2xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="brand-name text-display-ui font-semibold tracking-[-0.03em]">
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 max-w-[560px] text-md-ui leading-normal text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2 max-[1099px]:w-full">
          {actions}
        </div>
      ) : null}
    </header>
  )
}
