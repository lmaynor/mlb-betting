import Link from 'next/link'

const TABS = [
  { id: 'picks', label: 'Picks', href: '/picks/mlb' },
  { id: 'projections', label: 'Projections', href: '/picks/mlb/projections' },
] as const

/** MLB's two player views: the tracked picks and the full projection board. Rendered by both pages, so no pathname lookup. */
export function MlbTabs({ active }: { active: 'picks' | 'projections' }) {
  return (
    <nav aria-label="MLB views" style={{ borderBottom: '1px solid #1e2230', background: '#090a0f' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px', display: 'flex', gap: '2px' }}>
        {TABS.map(t => (
          <Link key={t.id} href={t.href} aria-current={active === t.id ? 'page' : undefined}
            style={{ fontSize: '12.5px', fontWeight: 600, padding: '10px 12px', textDecoration: 'none', color: active === t.id ? 'var(--chalk)' : 'var(--fog)',
              borderBottom: `2px solid ${active === t.id ? 'var(--signal)' : 'transparent'}`, marginBottom: '-1px' }}>
            {t.label}
          </Link>
        ))}
      </div>
    </nav>
  )
}
