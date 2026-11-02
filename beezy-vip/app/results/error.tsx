'use client'
export default function ResultsError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="p-[40px] text-center border border-[color:var(--color-separator-border)]">
      <p className="mono text-[12px] text-text-secondary mb-[12px]">Failed to load results.</p>
      <button onClick={reset} className="mono" style={{ fontSize: '11px', color: 'var(--link)', background: 'none', border: 'none', cursor: 'pointer', letterSpacing: '0.04em' }}>Try again &rarr;</button>
    </div>
  )
}
