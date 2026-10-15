import Link from 'next/link'

const CALCULATORS = [
  { href: '/tools/odds-calculator', label: 'Odds & Vig' },
  { href: '/tools/kelly-calculator', label: 'Kelly' },
  { href: '/tools/boost-ev', label: 'Boost EV' },
]

/**
 * One-row toolbar header shared by every /tools page: plain h1 on the left; on the right a hint, the three free
 * calculators as sibling tabs (so they read as one toolset) and the way back to the index.
 */
export function ToolHeader({ title, hint, current }: { title: string; hint?: string; current?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '14px' }}>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{title}</h1>
      <span style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        {hint && <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{hint}</span>}
        {current && (
          <nav aria-label="Calculators" style={{ display: 'inline-flex', gap: '2px', padding: '3px', background: '#090a0f', border: '1px solid #1e2230', borderRadius: '8px' }}>
            {CALCULATORS.map(c => (
              <Link key={c.href} href={c.href} aria-current={c.href === current ? 'page' : undefined}
                style={{ fontSize: '12px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', textDecoration: 'none', whiteSpace: 'nowrap',
                  color: c.href === current ? 'var(--chalk)' : 'var(--silver)', background: c.href === current ? '#1b1f2b' : 'transparent' }}>
                {c.label}
              </Link>
            ))}
          </nav>
        )}
        <Link href="/tools" style={{ fontSize: '12.5px', color: 'var(--link)', textDecoration: 'none' }}>All tools &rarr;</Link>
      </span>
    </div>
  )
}
