'use client'
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ maxWidth: '600px', margin: '80px auto', padding: '0 20px', textAlign: 'center' }}>
      <h1 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--ash)', marginBottom: '8px' }}>Something went wrong</h1>
      <p style={{ fontSize: '13px', color: 'var(--fog)', marginBottom: '24px' }}>An error occurred. Please try again.</p>
      <button onClick={reset} className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: 'var(--signal)', color: 'var(--carbon)', border: 'none', cursor: 'pointer' }}>Try again</button>
    </div>
  )
}
