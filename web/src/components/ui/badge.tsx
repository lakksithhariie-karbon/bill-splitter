import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "group/badge inline-flex h-6 w-fit shrink-0 items-center justify-center gap-1.5 overflow-hidden border border-border bg-secondary px-[7px] text-2xs font-semibold whitespace-nowrap text-secondary-foreground [&>svg]:pointer-events-none [&>svg:not([class*='size-'])]:size-3",
  {
    variants: {
      variant: {
        default:
          "border-border bg-secondary text-secondary-foreground",
        secondary:
          "border-border bg-secondary text-secondary-foreground",
        review: "border-border bg-background text-foreground",
        done:
          "border-primary bg-primary text-primary-foreground",
        error:
          "border-[color-mix(in_oklch,var(--destructive)_30%,var(--border))] bg-background text-destructive",
        warning:
          "border-[color-mix(in_oklch,var(--warning)_35%,var(--border))] bg-background text-warning",
        destructive:
          "border-[color-mix(in_oklch,var(--destructive)_30%,var(--border))] bg-background text-destructive",
        outline: "border-border bg-background text-foreground",
        ghost:
          "border-transparent bg-transparent text-foreground",
        link: "h-auto border-transparent bg-transparent px-0 text-primary underline-offset-4",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  dot = false,
  render,
  children,
  ...props
}: useRender.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & {
    dot?: boolean
  }) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
        children: (
          <>
            {dot ? (
              <span
                aria-hidden="true"
                className="size-1.5 shrink-0 bg-current opacity-60 [border-radius:50%]"
              />
            ) : null}
            {children}
          </>
        ),
      },
      props
    ),
    render,
    state: {
      slot: "badge",
      variant,
    },
  })
}

export { Badge, badgeVariants }
