import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const SEGMENT_COUNT = 30

const progressBarVariants = cva(
  "flex w-full items-stretch gap-px overflow-hidden",
  {
    variants: {
      variant: {
        default: "[&_[data-filled=true]]:bg-primary",
        warning: "[&_[data-filled=true]]:bg-warning",
        fault: "[&_[data-filled=true]]:bg-destructive",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false)
  React.useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    const sync = () => setReduced(mq.matches)
    sync()
    mq.addEventListener("change", sync)
    return () => mq.removeEventListener("change", sync)
  }, [])
  return reduced
}

/** Local once-only IntersectionObserver (threshold 0.3). No extra deps. */
function useInViewOnce<T extends Element>(
  threshold = 0.3
): [React.RefCallback<T>, boolean] {
  const [inView, setInView] = React.useState(false)
  const nodeRef = React.useRef<T | null>(null)
  const observerRef = React.useRef<IntersectionObserver | null>(null)

  const setRef = React.useCallback(
    (node: T | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect()
        observerRef.current = null
      }
      nodeRef.current = node
      if (!node || inView) return
      const io = new IntersectionObserver(
        ([entry]) => {
          if (entry?.isIntersecting) {
            setInView(true)
            io.disconnect()
            observerRef.current = null
          }
        },
        { threshold }
      )
      observerRef.current = io
      io.observe(node)
    },
    [inView, threshold]
  )

  React.useEffect(() => {
    return () => {
      observerRef.current?.disconnect()
    }
  }, [])

  return [setRef, inView]
}

function assignRef<T>(ref: React.Ref<T> | undefined, value: T | null) {
  if (!ref) return
  if (typeof ref === "function") ref(value)
  else (ref as React.MutableRefObject<T | null>).current = value
}

type ProgressBarProps = React.ComponentPropsWithoutRef<"div"> &
  VariantProps<typeof progressBarVariants> & {
    value: number
    max?: number
    label?: string
  }

const ProgressBar = React.forwardRef<HTMLDivElement, ProgressBarProps>(
  (
    {
      className,
      variant = "default",
      value,
      max = 100,
      label,
      ...props
    },
    ref
  ) => {
    const reducedMotion = usePrefersReducedMotion()
    const [inViewRef, inView] = useInViewOnce<HTMLDivElement>(0.3)
    const revealed = reducedMotion || inView

    const clampedMax = max <= 0 ? 100 : max
    const clampedValue = Math.min(Math.max(value, 0), clampedMax)
    const filledCount = Math.round((clampedValue / clampedMax) * SEGMENT_COUNT)

    const setRefs = React.useCallback(
      (node: HTMLDivElement | null) => {
        inViewRef(node)
        assignRef(ref, node)
      },
      [inViewRef, ref]
    )

    return (
      <div
        ref={setRefs}
        data-slot="progress-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={clampedMax}
        aria-valuenow={clampedValue}
        aria-label={label}
        className={cn(progressBarVariants({ variant }), className)}
        {...props}
      >
        {Array.from({ length: SEGMENT_COUNT }, (_, i) => {
          const filled = i < filledCount
          const showFilled = filled && revealed
          return (
            <span
              key={i}
              data-filled={showFilled ? "true" : undefined}
              aria-hidden="true"
              className={cn(
                "h-2.5 min-w-0 flex-1 skew-x-[-18deg] bg-muted",
                !reducedMotion && "transition-colors duration-150 ease-linear"
              )}
              style={
                !reducedMotion && showFilled
                  ? { transitionDelay: `${i * 18}ms` }
                  : undefined
              }
            />
          )
        })}
      </div>
    )
  }
)
ProgressBar.displayName = "ProgressBar"

export { ProgressBar, progressBarVariants }
export type { ProgressBarProps }
