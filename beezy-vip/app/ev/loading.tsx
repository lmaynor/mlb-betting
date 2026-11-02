// Instant route-transition skeleton (no-slop-ui: flat dark table rows, no spinner, no animation).
const ROWS = Array.from({ length: 12 }, (_, i) => i)

export default function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading odds terminal" style={{ maxWidth: 1600, margin: '0 auto', padding: '16px' }}>
      <div  className="h-[28px] w-[180px] bg-background-tertiary-default mb-4"/>
      <div className="border border-[color:#232634] bg-background-primary-default">
        {ROWS.map(i => (
          <div key={i} style={{ height: 44, borderBottom: i === ROWS.length - 1 ? 'none' : '1px solid var(--color-background-tertiary-default)', display: 'flex', alignItems: 'center', gap: 16, padding: '0 12px' }}>
            <div  className="h-[10px] w-[48px] bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[22%] bg-background-tertiary-default"/>
            <div  className="h-[10px] w-[40%] bg-background-tertiary-default"/>
          </div>
        ))}
      </div>
    </div>
  )
}
