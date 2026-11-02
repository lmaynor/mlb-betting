// Instant route-transition skeleton (no-slop-ui: flat dark table rows, no spinner, no animation).
const ROWS = Array.from({ length: 10 }, (_, i) => i)

export default function Loading() {
  return (
    <div className="max-w-page mx-auto p-4" aria-busy="true" aria-label="Loading prop cockpit">
      <div  className="h-7 w-[220px] bg-background-tertiary-default mb-4"/>
      <div className="border border-[color:#232634] bg-background-primary-default">
        {ROWS.map(i => (
          <div className="h-10 flex items-center gap-4 py-0 px-3" key={i} style={{ borderBottom: i === ROWS.length - 1 ? 'none' : '1px solid var(--color-background-tertiary-default)' }}>
            <div  className="h-[10px] w-10 bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[30%] bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[20%] bg-background-tertiary-default"/>
          </div>
        ))}
      </div>
    </div>
  )
}
