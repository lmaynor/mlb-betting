import Link from 'next/link'
import { getSystemsBySport, PICK_SYSTEMS } from '@/lib/pick-systems'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'

const B = '1px solid var(--color-separator-border)'
const CHIP: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '7px', padding: '6px 12px', border: B, borderRadius: 'var(--radius)', background: 'var(--color-background-primary-default)',
  color: 'var(--color-neutral-300)', fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.04em', textDecoration: 'none', whiteSpace: 'nowrap', flex: '0 0 auto',
}
const ON: React.CSSProperties = { borderColor: 'var(--color-border-button-active)', background: 'var(--slate)', color: 'var(--color-text-primary)' }

/**
 * Every system of the sport as a tab, under the SYSTEMS tab: the index (ALL SYSTEMS) and one page per system, so moving
 * between systems never means going back to a table. `active` is a system key, or null on the index.
 */
export function SystemStrip({ sport, active }: { sport: SportSlug; active: string | null }) {
  const listed = getSystemsBySport(sport)
  // a shelved system keeps its URL; if that is the page being viewed it appears as the last tab so the strip never loses its place
  const current = active ? PICK_SYSTEMS.find(s => s.sport === sport && s.key === active) : undefined
  const systems = current && !listed.includes(current) ? [...listed, current] : listed
  return (
    <nav className="flex gap-[6px] overflow-x-auto py-[10px] px-4 bg-background-full" aria-label={`${SPORT_PILLARS[sport].shortName} systems`} style={{ borderBottom: B }}>
      <Link href={`/picks/${sport}/systems`} aria-current={active == null ? 'page' : undefined} style={{ ...CHIP, ...(active == null ? ON : {}) }}>ALL SYSTEMS</Link>
      {systems.map(s => {
        const on = s.key === active
        return (
          <Link key={s.key} href={`/picks/${sport}/${s.slug}`} aria-current={on ? 'page' : undefined} style={{ ...CHIP, ...(on ? ON : {}) }}>
            <span className="w-[6px] h-[6px] flex-none" style={{ borderRadius: '50%', background: SYSTEM_COLOR[s.key] ?? 'var(--color-text-secondary)' }} />
            {s.shortName}
          </Link>
        )
      })}
    </nav>
  )
}
