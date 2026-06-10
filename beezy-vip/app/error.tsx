'use client'
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ maxWidth: '600px', margin: '80px auto', padding: '0 20px', textAlign: 'center' }}>
      <h1 style={{ fontSize: '18px', fontWeight: 600, color: '#f5f5f7', marginBottom: '8px' }}>Something went wrong</h1>
      <p style={{ fontSize: '13px', color: '#888890', marginBottom: '24px' }}>An error occurred. Please try again.</p>
      <button onClick={reset} className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: '#fcc20f', color: '#0a0a0c', border: 'none', cursor: 'pointer' }}>Try again</button>
    </div>
  )
}
