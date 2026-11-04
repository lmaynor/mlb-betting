import Link from 'next/link'
import { getSystemsBySport } from '@/lib/pick-systems'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'

export type SportView = 'picks' | 'projections' | 'systems'

/**
 * The one header strip every sport page shares (MLB, NFL, NHL, NBA, and any sport added to lib/sports.ts): the sport and
 * how many systems it lists on the left, PICKS & +EV / SYSTEMS on the right (projections live in the Scanner, /ev/projections). A system's own page is part of
 * SYSTEMS (it carries the strip of every system), so it passes active='systems'.
 */
export function SportTabs({ sport, active }: { sport: SportSlug; active: SportView | null }) {
  const pillar = SPORT_PILLARS[sport]
  const n = getSystemsBySport(sport).length
  const tabs: { id: SportView; label: string; href: string }[] = [
    { id: 'picks', label: 'PICKS & +EV', href: `/picks/${sport}` },
    { id: 'systems', label: 'SYSTEMS', href: `/picks/${sport}/systems` },
  ]
  return (
    <nav className="border-b border-b-[color:var(--color-separator-border)] bg-background-secondary-default" aria-label={`${pillar.shortName} views`}>
      <div className="gutter-m max-w-page mx-auto py-0 px-6 flex items-center justify-between gap-y-1 gap-x-4 flex-wrap">
        <div className="flex items-baseline gap-2.5 py-2.5 px-0">
          <span className="text-cozy font-bold" style={{ color: pillar.accent }}>{pillar.shortName}</span>
          <span className="mono text-[11.5px] text-text-secondary">&middot; {n} Systems</span>
        </div>
        <div className="flex gap-0.5">
          {tabs.map(t => (
            <Link key={t.id} href={t.href} aria-current={active === t.id ? 'page' : undefined} className="tap-m inline-flex text-compact font-semibold tracking-fine py-[10px] px-3 no-underline mb-[-1px]"
              style={{
                color: active === t.id ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                borderBottom: `2px solid ${active === t.id ? pillar.accent : 'transparent'}`,
              }}>
              {t.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  )
}
