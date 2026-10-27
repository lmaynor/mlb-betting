import Link from 'next/link'
import { getSystemsBySport } from '@/lib/pick-systems'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'

export type SportView = 'picks' | 'projections' | 'systems'

/**
 * The one header strip every sport page shares (MLB, NFL, NHL, NBA, and any sport added to lib/sports.ts): the sport and
 * how many systems it lists on the left, PICKS & +EV / PROJECTIONS / SYSTEMS on the right. A system's own page is part of
 * SYSTEMS (it carries the strip of every system), so it passes active='systems'.
 */
export function SportTabs({ sport, active }: { sport: SportSlug; active: SportView | null }) {
  const pillar = SPORT_PILLARS[sport]
  const n = getSystemsBySport(sport).length
  const tabs: { id: SportView; label: string; href: string }[] = [
    { id: 'picks', label: 'PICKS & +EV', href: `/picks/${sport}` },
    { id: 'projections', label: 'ALL PROJECTIONS', href: `/picks/${sport}/projections` },
    { id: 'systems', label: 'SYSTEMS', href: `/picks/${sport}/systems` },
  ]
  return (
    <nav aria-label={`${pillar.shortName} views`} style={{ borderBottom: '1px solid #1e2230', background: '#090a0f' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '4px 16px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', padding: '10px 0' }}>
          <span style={{ fontSize: '13px', fontWeight: 700, color: pillar.accent }}>{pillar.shortName}</span>
          <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>&middot; {n} Systems</span>
        </div>
        <div style={{ display: 'flex', gap: '2px' }}>
          {tabs.map(t => (
            <Link key={t.id} href={t.href} aria-current={active === t.id ? 'page' : undefined}
              style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em', padding: '10px 12px', textDecoration: 'none',
                color: active === t.id ? 'var(--chalk)' : 'var(--fog)',
                borderBottom: `2px solid ${active === t.id ? pillar.accent : 'transparent'}`, marginBottom: '-1px' }}>
              {t.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  )
}
