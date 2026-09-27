import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 cursor-pointer items-center justify-center gap-[7px] border border-border bg-background text-sm font-semibold whitespace-nowrap transition-opacity outline-none select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "border-primary bg-primary text-primary-foreground hover:opacity-[0.94]",
        outline:
          "border-border bg-background hover:bg-accent hover:text-accent-foreground",
        secondary:
          "border-border bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground",
        ghost:
          "border-transparent bg-transparent hover:bg-accent hover:text-accent-foreground",
        destructive:
          "border-[color-mix(in_oklch,var(--destructive)_35%,var(--border))] bg-background text-destructive hover:bg-accent",
        danger:
          "border-[color-mix(in_oklch,var(--destructive)_35%,var(--border))] bg-background text-destructive hover:bg-accent",
        link: "h-auto border-transparent bg-transparent px-0 text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-[34px] px-3 [&_svg:not([class*='size-'])]:size-[15px]",
        tiny:
          "h-7 gap-1.5 px-2 text-2xs [&_svg:not([class*='size-'])]:size-3",
        xs: "h-7 gap-1.5 px-2 text-2xs [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1.5 px-2 text-2xs [&_svg:not([class*='size-'])]:size-3",
        lg: "h-[34px] px-3 [&_svg:not([class*='size-'])]:size-[15px]",
        icon: "size-[34px] p-0 [&_svg:not([class*='size-'])]:size-[15px]",
        "icon-xs":
          "size-7 p-0 [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-[34px] p-0 [&_svg:not([class*='size-'])]:size-[15px]",
        "icon-lg":
          "size-[34px] p-0 [&_svg:not([class*='size-'])]:size-[15px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { Button, buttonVariants }
