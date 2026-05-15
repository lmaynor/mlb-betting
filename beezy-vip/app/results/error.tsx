'use client'

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="max-w-7xl mx-auto px-4 py-24 text-center">
      <p className="mono text-xs text-loss uppercase tracking-widest mb-3">Error</p>
      <p className="text-sm text-muted mb-6">Failed to load this page. Data may be temporarily unavailable.</p>
      <button
        onClick={reset}
        className="mono text-xs uppercase tracking-widest px-5 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
      >
        Retry
      </button>
    </div>
  )
}
