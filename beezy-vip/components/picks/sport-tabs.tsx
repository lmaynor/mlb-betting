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
    <nav aria-label={`${pillar.shortName} views`} style={{ borderBottom: '1px solid var(--color-separator-border)', background: 'var(--color-background-secondary-default)' }}>
      <div className="gutter-m max-w-page mx-auto py-0 px-6 flex items-center justify-between gap-y-1 gap-x-4 flex-wrap">
        <div className="flex items-baseline gap-2.5 py-2.5 px-0">
          <span style={{ fontSize: '13px', fontWeight: 700, color: pillar.accent }}>{pillar.shortName}</span>
          <span className="mono text-[11.5px] text-text-secondary">&middot; {n} Systems</span>
        </div>
        <div className="flex gap-0.5">
          {tabs.map(t => (
            <Link key={t.id} href={t.href} aria-current={active === t.id ? 'page' : undefined} className="tap-m"
              style={{ display: 'inline-flex', fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em', padding: '10px 12px', textDecoration: 'none',
                color: active === t.id ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                borderBottom: `2px solid ${active === t.id ? pillar.accent : 'transparent'}`, marginBottom: '-1px' }}>
              {t.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  )
}
