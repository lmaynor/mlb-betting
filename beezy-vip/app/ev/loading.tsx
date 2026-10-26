// Instant route-transition skeleton (no-slop-ui: flat dark table rows, no spinner, no animation).
const ROWS = Array.from({ length: 12 }, (_, i) => i)

export default function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading odds terminal" style={{ maxWidth: 1600, margin: '0 auto', padding: '16px' }}>
      <div style={{ height: 28, width: 180, background: '#171a24', marginBottom: 16 }} />
      <div style={{ border: '1px solid #232634', background: '#11131a' }}>
        {ROWS.map(i => (
          <div key={i} style={{ height: 44, borderBottom: i === ROWS.length - 1 ? 'none' : '1px solid #171a24', display: 'flex', alignItems: 'center', gap: 16, padding: '0 12px' }}>
            <div style={{ height: 10, width: 48, background: '#171a24' }} />
            <div style={{ height: 10, width: '22%', background: '#171a24' }} />
            <div style={{ height: 10, width: '40%', background: '#171a24' }} />
          </div>
        ))}
      </div>
    </div>
  )
}
