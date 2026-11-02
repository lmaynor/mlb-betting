'use client'
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div style={{ maxWidth: '600px', margin: '96px auto', padding: '0 24px', textAlign: 'center' }}>
      <h1 className="ui-display text-[26px] text-text-primary mb-2.5">Something went wrong</h1>
      <p className="times text-[15px] text-text-secondary mb-7">An error occurred. Please try again.</p>
      <button onClick={reset} className="btn btn-primary">Try again</button>
    </div>
  )
}
