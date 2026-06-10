'use client'
export default function ResultsError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ padding: '40px', textAlign: 'center', border: '1px solid #1f1f24' }}>
      <p className="mono" style={{ fontSize: '12px', color: '#888890', marginBottom: '12px' }}>Failed to load results.</p>
      <button onClick={reset} className="mono" style={{ fontSize: '11px', color: '#9999ff', background: 'none', border: 'none', cursor: 'pointer', letterSpacing: '0.04em' }}>Try again &rarr;</button>
    </div>
  )
}
