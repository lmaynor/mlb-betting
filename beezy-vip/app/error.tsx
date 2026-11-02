'use client'
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="max-w-[600px] py-0 px-6 text-center" style={{ margin: '96px auto' }}>
      <h1 className="ui-display text-[26px] text-text-primary mb-2.5">Something went wrong</h1>
      <p className="times text-[15px] text-text-secondary mb-7">An error occurred. Please try again.</p>
      <button onClick={reset} className="btn btn-primary">Try again</button>
    </div>
  )
}
