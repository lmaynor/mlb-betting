import Link from 'next/link'
import { getSystemsBySport, PICK_SYSTEMS } from '@/lib/pick-systems'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { SYSTEM_COLOR } from '@/lib/tokens'

const B = '1px solid var(--basalt)'
const CHIP: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '7px', padding: '6px 12px', border: B, borderRadius: 'var(--radius)', background: 'var(--graphite)',
  color: 'var(--silver)', fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.04em', textDecoration: 'none', whiteSpace: 'nowrap', flex: '0 0 auto',
}
const ON: React.CSSProperties = { borderColor: 'var(--steel)', background: 'var(--slate)', color: 'var(--chalk)' }

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
    <nav aria-label={`${SPORT_PILLARS[sport].shortName} systems`} style={{ display: 'flex', gap: '6px', overflowX: 'auto', padding: '10px 16px', borderBottom: B, background: 'var(--carbon)' }}>
      <Link href={`/picks/${sport}/systems`} aria-current={active == null ? 'page' : undefined} style={{ ...CHIP, ...(active == null ? ON : {}) }}>ALL SYSTEMS</Link>
      {systems.map(s => {
        const on = s.key === active
        return (
          <Link key={s.key} href={`/picks/${sport}/${s.slug}`} aria-current={on ? 'page' : undefined} style={{ ...CHIP, ...(on ? ON : {}) }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: SYSTEM_COLOR[s.key] ?? 'var(--fog)', flex: '0 0 auto' }} />
            {s.shortName}
          </Link>
        )
      })}
    </nav>
  )
}
