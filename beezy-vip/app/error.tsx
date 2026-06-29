'use client'
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ maxWidth: '600px', margin: '96px auto', padding: '0 24px', textAlign: 'center' }}>
      <h1 className="dell-display" style={{ fontSize: '26px', color: 'var(--chalk)', marginBottom: '10px' }}>Something went wrong</h1>
      <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginBottom: '28px' }}>An error occurred. Please try again.</p>
      <button onClick={reset} className="btn btn-primary">Try again</button>
    </div>
  )
}
