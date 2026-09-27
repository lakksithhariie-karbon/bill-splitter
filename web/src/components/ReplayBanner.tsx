type Props = {
  isReplay: boolean
  fixture: string | null
}

export function ReplayBanner({ isReplay, fixture }: Props) {
  if (!isReplay) return null
  // Tinted destructive surface — theme has no --destructive-foreground yet.
  return (
    <div className="border-b-2 border-destructive bg-destructive/15 px-4 py-2.5 text-center text-base-ui font-bold text-destructive dark:bg-destructive/25">
      REPLAY — canned fixture result. Not a live split. Fixture:{" "}
      <span className="num font-semibold">{fixture || "unknown fixture"}</span>
    </div>
  )
}
