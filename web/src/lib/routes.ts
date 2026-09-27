import * as React from "react"

export type AppRoute =
  | "upload"
  | "documents"
  | "split"
  | "invoices"

const ROUTES: AppRoute[] = ["upload", "documents", "split", "invoices"]

export type RouteMeta = {
  breadcrumb: string
  eyebrow: string
  title: string
  description: string
}

export const ROUTE_META: Record<AppRoute, RouteMeta> = {
  upload: {
    breadcrumb: "Bill Upload",
    eyebrow: "Step 1 of 3 · Upload",
    title: "Split bill PDF",
    description:
      "Upload a combined bill PDF. Review the suggested page breaks, then download one PDF per bill in a ZIP file.",
  },
  documents: {
    breadcrumb: "Documents",
    eyebrow: "SOURCE FILES",
    title: "Documents",
    description:
      "Every uploaded source PDF, with its split status and downstream processing state.",
  },
  split: {
    breadcrumb: "Review Queue",
    eyebrow: "Step 2 of 3 · Review",
    title: "Review suggested splits",
    description:
      "Check where each bill starts and ends. Adjust any page breaks, then download the split PDFs together.",
  },
  invoices: {
    breadcrumb: "Review Queue",
    eyebrow: "Step 3 of 3 · Preview extracted bill",
    title: "Extraction preview",
    description:
      "Optional field preview for inspecting a split. You do not need this step to download your PDFs.",
  },
}

function isAppRoute(value: string): value is AppRoute {
  return ROUTES.includes(value as AppRoute)
}

/** Map legacy `#/review` hash to the split-confirm step. */
function normalizeRouteToken(value: string): string {
  if (value === "review") return "split"
  return value
}

export function parseRoute(): AppRoute {
  const hash = window.location.hash.replace(/^#\/?/, "").replace(/\/$/, "")
  const hashNorm = normalizeRouteToken(hash)
  if (hashNorm === "" || hashNorm === "upload") return "upload"
  if (isAppRoute(hashNorm)) return hashNorm

  const path = window.location.pathname.replace(/^\//, "").replace(/\/$/, "")
  const pathNorm = normalizeRouteToken(path)
  if (pathNorm === "" || pathNorm === "upload") return "upload"
  if (isAppRoute(pathNorm)) return pathNorm

  return "upload"
}

export function navigate(route: AppRoute): void {
  const next = route === "upload" ? "#/" : `#/${route}`
  if (window.location.hash === next) return
  window.location.hash = next.slice(1)
}

export function useAppRoute(): AppRoute {
  const [route, setRoute] = React.useState<AppRoute>(() => parseRoute())

  React.useEffect(() => {
    const sync = () => setRoute(parseRoute())
    window.addEventListener("hashchange", sync)
    window.addEventListener("popstate", sync)
    return () => {
      window.removeEventListener("hashchange", sync)
      window.removeEventListener("popstate", sync)
    }
  }, [])

  return route
}
