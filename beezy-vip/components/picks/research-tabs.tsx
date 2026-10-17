import Link from 'next/link'

export type ResearchView = 'cockpit' | 'projections'

const TABS: { id: ResearchView; label: string; href: string; title: string }[] = [
  { id: 'cockpit', label: 'PROP COCKPIT', href: '/edge', title: 'Flagged lines with last-10 game logs and every book price' },
  { id: 'projections', label: 'ALL PROJECTIONS', href: '/edge/projections', title: 'The model number for every player prop on the slate, priced or not' },
]

/** The two halves of Prop Research: the flagged-line cockpit, and the model's projection for every player prop. */
export function ResearchTabs({ active }: { active: ResearchView }) {
  return (
    <nav aria-label="Prop research views" style={{ display: 'inline-flex', gap: '2px', padding: '3px', background: '#090a0f', border: '1px solid #1e2230', borderRadius: '8px' }}>
      {TABS.map(t => (
        <Link key={t.id} href={t.href} title={t.title} aria-current={active === t.id ? 'page' : undefined}
          style={{ fontSize: '12px', fontWeight: 700, letterSpacing: '0.05em', padding: '7px 13px', borderRadius: '6px', textDecoration: 'none', whiteSpace: 'nowrap',
            color: active === t.id ? '#ffffff' : 'var(--silver)', background: active === t.id ? '#1b1f2b' : 'transparent', border: `1px solid ${active === t.id ? '#3a4156' : 'transparent'}` }}>
          {t.label}
        </Link>
      ))}
    </nav>
  )
}
