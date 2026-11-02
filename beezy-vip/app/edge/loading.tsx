// Instant route-transition skeleton (no-slop-ui: flat dark table rows, no spinner, no animation).
const ROWS = Array.from({ length: 10 }, (_, i) => i)

export default function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading prop cockpit" style={{ maxWidth: 1200, margin: '0 auto', padding: '16px' }}>
      <div style={{ height: 28, width: 220, background: 'var(--color-background-tertiary-default)', marginBottom: 16 }} />
      <div style={{ border: '1px solid #232634', background: 'var(--color-background-primary-default)' }}>
        {ROWS.map(i => (
          <div key={i} style={{ height: 40, borderBottom: i === ROWS.length - 1 ? 'none' : '1px solid var(--color-background-tertiary-default)', display: 'flex', alignItems: 'center', gap: 16, padding: '0 12px' }}>
            <div  className="h-[10px] w-[40px] bg-[var(--color-background-tertiary-default)]"/>
            <div  className="h-[10px] w-[30%] bg-[var(--color-background-tertiary-default)]"/>
            <div  className="h-[10px] w-[20%] bg-[var(--color-background-tertiary-default)]"/>
          </div>
        ))}
      </div>
    </div>
  )
}
