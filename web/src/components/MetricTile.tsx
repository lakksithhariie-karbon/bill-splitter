import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export type MetricTileProps = {
  label: string
  value: string | number
  foot: string
  className?: string
}

export function MetricTile({ label, value, foot, className }: MetricTileProps) {
  return (
    <div
      className={cn(
        "min-h-24 border border-border bg-card p-3.5",
        className
      )}
    >
      <div className="text-[10px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 font-display text-metric-ui">{value}</div>
      <div className="mt-2 text-[10px] text-muted-foreground">{foot}</div>
    </div>
  )
}

export function MetricGrid({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "mb-3.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4",
        className
      )}
    >
      {children}
    </div>
  )
}
