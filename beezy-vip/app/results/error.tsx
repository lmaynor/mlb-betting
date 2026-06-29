'use client'
export default function ResultsError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ padding: '40px', textAlign: 'center', border: '1px solid var(--basalt)' }}>
      <p className="mono" style={{ fontSize: '12px', color: 'var(--fog)', marginBottom: '12px' }}>Failed to load results.</p>
      <button onClick={reset} className="mono" style={{ fontSize: '11px', color: 'var(--link)', background: 'none', border: 'none', cursor: 'pointer', letterSpacing: '0.04em' }}>Try again &rarr;</button>
    </div>
  )
}
