'use client'

import { useEffect } from 'react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('[GlobalError]', error)
  }, [error])

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4">
      <div className="text-center">
        <p className="mono text-xs text-loss uppercase tracking-widest mb-4">Error</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-4">
          Something went wrong
        </h1>
        <p className="mono text-sm text-muted mb-8 max-w-sm mx-auto">
          {error.digest ? `Error ID: ${error.digest}` : 'An unexpected error occurred.'}
        </p>
        <div className="flex justify-center gap-3">
          <button
            onClick={reset}
            className="mono text-xs uppercase tracking-widest px-5 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
          >
            Try Again
          </button>
          <a
            href="/"
            className="mono text-xs uppercase tracking-widest px-5 py-2.5 border border-[var(--border)] text-muted hover:border-accent hover:text-accent transition-colors"
          >
            Go Home
          </a>
        </div>
      </div>
    </div>
  )
}
