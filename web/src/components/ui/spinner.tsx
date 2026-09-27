import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/**
 * 3×3 pixel spinner — perimeter trail runs clockwise; center stays dim.
 * Discrete snap via the 37.5% / 37.6% keyframe hold (engineered, not soft fade).
 */

const SPINNER_STYLE_ID = "ui-spinner-keyframes"

const SPINNER_CSS = `
@keyframes ui-spinner-trail {
  0%, 37.5% { opacity: 1; }
  37.6%, 100% { opacity: 0.18; }
}
@media (prefers-reduced-motion: reduce) {
  [data-slot="spinner"] [data-pixel="perimeter"] {
    animation: none !important;
    opacity: 0.18 !important;
  }
  [data-slot="spinner"] [data-pixel="perimeter"][data-static-lit="true"] {
    opacity: 1 !important;
  }
}
`

function ensureSpinnerStyles() {
  if (typeof document === "undefined") return
  if (document.getElementById(SPINNER_STYLE_ID)) return
  const el = document.createElement("style")
  el.id = SPINNER_STYLE_ID
  el.textContent = SPINNER_CSS
  document.head.appendChild(el)
}

/** Clockwise perimeter order around the center cell. */
const PERIMETER: Array<{ row: number; col: number }> = [
  { row: 0, col: 0 },
  { row: 0, col: 1 },
  { row: 0, col: 2 },
  { row: 1, col: 2 },
  { row: 2, col: 2 },
  { row: 2, col: 1 },
  { row: 2, col: 0 },
  { row: 1, col: 0 },
]

const STATIC_LIT = new Set([0, 1, 2])

const spinnerVariants = cva("inline-grid shrink-0 grid-cols-3 grid-rows-3", {
  variants: {
    size: {
      sm: "size-3 gap-px",
      md: "size-4 gap-px",
      lg: "size-5 gap-0.5",
    },
    variant: {
      default: "text-foreground",
      accent: "text-primary",
      warning: "text-warning",
      fault: "text-destructive",
    },
  },
  defaultVariants: {
    size: "md",
    variant: "default",
  },
})

type SpinnerProps = React.ComponentPropsWithoutRef<"div"> &
  VariantProps<typeof spinnerVariants> & {
    label?: string
  }

const Spinner = React.forwardRef<HTMLDivElement, SpinnerProps>(
  (
    {
      className,
      size = "md",
      variant = "default",
      label = "Loading",
      ...props
    },
    ref
  ) => {
    React.useEffect(() => {
      ensureSpinnerStyles()
    }, [])

    const durationMs = size === "sm" ? 720 : size === "lg" ? 960 : 840
    const stepMs = durationMs / PERIMETER.length

    const cells: React.ReactNode[] = []
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const perimeterIndex = PERIMETER.findIndex(
          (p) => p.row === row && p.col === col
        )
        const isCenter = row === 1 && col === 1

        if (isCenter) {
          cells.push(
            <span
              key={`${row}-${col}`}
              data-pixel="center"
              aria-hidden="true"
              className="bg-current opacity-[0.18]"
            />
          )
          continue
        }

        cells.push(
          <span
            key={`${row}-${col}`}
            data-pixel="perimeter"
            data-static-lit={STATIC_LIT.has(perimeterIndex) ? "true" : undefined}
            aria-hidden="true"
            className="bg-current"
            style={{
              animationName: "ui-spinner-trail",
              animationDuration: `${durationMs}ms`,
              animationTimingFunction: "linear",
              animationIterationCount: "infinite",
              animationDelay: `${-perimeterIndex * stepMs}ms`,
            }}
          />
        )
      }
    }

    return (
      <div
        ref={ref}
        data-slot="spinner"
        role="status"
        aria-label={label}
        className={cn(spinnerVariants({ size, variant }), className)}
        {...props}
      >
        {cells}
        <span className="sr-only">{label}</span>
      </div>
    )
  }
)
Spinner.displayName = "Spinner"

export { Spinner, spinnerVariants }
export type { SpinnerProps }
