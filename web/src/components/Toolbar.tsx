import { Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export type ToolbarSelect = {
  id: string
  label: string
  options: string[]
  value?: string
  onChange?: (value: string) => void
}

export type ToolbarProps = {
  searchPlaceholder?: string
  searchValue?: string
  onSearchChange?: (value: string) => void
  selects?: ToolbarSelect[]
  actionLabel?: string
  onAction?: () => void
  className?: string
}

const selectClassName =
  "h-[34px] min-w-[132px] border border-input bg-background px-2.5 text-xs text-muted-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

export function Toolbar({
  searchPlaceholder = "Search…",
  searchValue = "",
  onSearchChange,
  selects = [],
  actionLabel,
  onAction,
  className,
}: ToolbarProps) {
  return (
    <div
      className={cn(
        "flex min-h-[54px] flex-wrap items-center gap-2 border-b border-border p-2.5",
        className
      )}
    >
      <div className="relative max-w-[420px] min-w-0 flex-[1_1_260px]">
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
          strokeWidth={1.7}
          aria-hidden="true"
        />
        <Input
          type="search"
          value={searchValue}
          onChange={(e) => onSearchChange?.(e.target.value)}
          placeholder={searchPlaceholder}
          className="h-[34px] w-full pl-8 text-xs"
          aria-label={searchPlaceholder}
        />
      </div>

      {selects.map((select) => (
        <select
          key={select.id}
          id={select.id}
          aria-label={select.label}
          value={select.value ?? select.options[0]}
          onChange={(e) => select.onChange?.(e.target.value)}
          className={selectClassName}
        >
          {select.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ))}

      {actionLabel ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-[34px] text-xs"
          onClick={onAction}
        >
          {actionLabel}
        </Button>
      ) : null}
    </div>
  )
}
