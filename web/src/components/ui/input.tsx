import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

const inputClassName =
  "h-[34px] w-full min-w-0 border border-border bg-background px-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive"

function Input({
  className,
  type,
  variant,
  icon,
  ...props
}: React.ComponentProps<"input"> & {
  variant?: "default" | "search"
  icon?: React.ReactNode
}) {
  if (variant === "search") {
    return (
      <div data-slot="input-search" className="relative w-full">
        {icon ? (
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground [&_svg]:size-3.5">
            {icon}
          </span>
        ) : null}
        <InputPrimitive
          type={type ?? "search"}
          data-slot="input"
          data-variant="search"
          className={cn(inputClassName, "pl-8", className)}
          {...props}
        />
      </div>
    )
  }

  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(inputClassName, className)}
      {...props}
    />
  )
}

export { Input }
