type Props = {
  uncoveredPages: number[]
}

/** Persistent warning when extraction left pages uncovered. */
export function CoverageBanner({ uncoveredPages }: Props) {
  if (!uncoveredPages.length) return null
  const sorted = [...uncoveredPages].sort((a, b) => a - b)
  const ranges: string[] = []
  let start = sorted[0]
  let prev = sorted[0]
  for (const p of sorted.slice(1)) {
    if (p === prev + 1) {
      prev = p
      continue
    }
    ranges.push(start === prev ? `${start}` : `${start}-${prev}`)
    start = prev = p
  }
  ranges.push(start === prev ? `${start}` : `${start}-${prev}`)
  const label = ranges.join(", ")
  return (
    <div className="border-b-2 border-warning bg-warning px-4 py-2.5 text-center text-base-ui font-bold text-warning-foreground">
      Pages {label} were not extracted.
    </div>
  )
}
