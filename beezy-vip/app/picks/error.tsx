'use client'
export default function PicksError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="p-10 text-center border border-separator-border">
      <p className="mono text-compact text-text-secondary mb-3">Failed to load picks.</p>
      <button onClick={reset} className="mono text-mini text-link bg-transparent bg-none border-0 cursor-pointer tracking-fine">Try again &rarr;</button>
    </div>
  )
}
