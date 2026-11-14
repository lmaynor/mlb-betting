// Instant route-transition skeleton (no-slop-ui: flat dark table rows, no spinner, no animation).
const ROWS = Array.from({ length: 12 }, (_, i) => i)

export default function Loading() {
  return (
    <div className="max-w-[1680px] mx-auto px-6 py-5" aria-busy="true" aria-label="Loading odds terminal">
      <div  className="h-7 w-[180px] bg-background-tertiary-default mb-4"/>
      <div className="border border-[color:#232634] bg-background-primary-default">
        {ROWS.map(i => (
          <div className="h-11 flex items-center gap-4 py-0 px-3" key={i} style={{ borderBottom: i === ROWS.length - 1 ? 'none' : '1px solid var(--color-background-tertiary-default)' }}>
            <div  className="h-[10px] w-12 bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[22%] bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[40%] bg-background-tertiary-default"/>
          </div>
        ))}
      </div>
    </div>
  )
}
