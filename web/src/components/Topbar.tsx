import { Button } from "@/components/ui/button"
import brandLogo from "@/assets/brand.ico"
import { cn } from "@/lib/utils"

type TopbarProps = {
  onStartOver?: () => void
  className?: string
}

export function Topbar({ onStartOver, className }: TopbarProps) {
  return (
    <header className={cn("topbar", className)}>
      <div className="brand">
        <img
          className="logo"
          src={brandLogo}
          alt="AI Accountant"
          width="28"
          height="28"
        />
        <div className="brand-name">AI Accountant</div>
        <div className="tool-divider" aria-hidden="true" />
        <div className="tool-name">Bill Splitter</div>
      </div>

      <div className="top-actions">
        <Button
          type="button"
          variant="outline"
          className="border-black bg-black text-white hover:bg-black/90 hover:text-white"
          onClick={onStartOver}
        >
          Start over
        </Button>
      </div>
    </header>
  )
}
