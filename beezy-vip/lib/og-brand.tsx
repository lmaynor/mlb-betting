import { BRAND_EMERALD, BRAND_MONO, BRAND_PATHS, BRAND_VIEWBOX_H, BRAND_VIEWBOX_W } from '@/lib/brand-paths'
import { anyTeamLogoUrl, type LogoSport } from '@/lib/sport-assets'

const OG_BASE = process.env.NEXT_PUBLIC_BASE_URL ?? 'https://beezy.fyi'

/**
 * Absolute logo URL for a team on an OG card, or null when the (league, abbrev) pair is unknown: the card then
 * renders NO logo, never another team's. Resolution is the canonical mapping in lib/sport-assets; the league is
 * an explicit argument because an abbreviation alone collides across leagues (ATL, MIA, KC, ...).
 */
export function ogTeamLogoUrl(sport: LogoSport, abbrev: string | null | undefined): string | null {
  const path = anyTeamLogoUrl(sport, abbrev)
  if (!path) return null
  return path.startsWith('http') ? path : `${OG_BASE}${path}`
}

/**
 * Satori-safe brand lockup for the OG cards: the bare fin plus the wordmark, flexbox only, literal hex, inline <svg>.
 * `size` is the fin height in px (the wordmark scales with it). Pass `showWordmark={false}` where the card prints its own title.
 */
export function OgBrandBadge({ size = 44, showWordmark = true }: { size?: number; showWordmark?: boolean }) {
  const markW = Math.round((size * BRAND_VIEWBOX_W) / BRAND_VIEWBOX_H)
  const font = Math.round(size * 0.78)
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      <svg width={markW} height={size} viewBox={`0 0 ${BRAND_VIEWBOX_W} ${BRAND_VIEWBOX_H}`}>
        {BRAND_PATHS.map((d, i) => <path key={i} d={d} fill={BRAND_EMERALD} />)}
      </svg>
      {showWordmark && (
        <div style={{ display: 'flex', alignItems: 'baseline', marginLeft: `${Math.round(size * 0.3)}px` }}>
          <span style={{ fontSize: `${font}px`, fontWeight: 800, color: BRAND_MONO, letterSpacing: '-1px' }}>beezy</span>
          <span style={{ fontSize: `${font}px`, fontWeight: 700, color: BRAND_EMERALD, letterSpacing: '-1px' }}>.fyi</span>
        </div>
      )}
    </div>
  )
}

/** The mark alone on a transparent background, for places that draw their own tile (apple-icon). */
export function OgBrandMark({ width, color = BRAND_EMERALD }: { width: number; color?: string }) {
  const height = Math.round((width * BRAND_VIEWBOX_H) / BRAND_VIEWBOX_W)
  return (
    <svg width={width} height={height} viewBox={`0 0 ${BRAND_VIEWBOX_W} ${BRAND_VIEWBOX_H}`}>
      {BRAND_PATHS.map((d, i) => <path key={i} d={d} fill={color} />)}
    </svg>
  )
}

/** A pick row as the public picks API returns it; the OG cards read only these fields. */
export interface OgPick {
  system: string
  bet_type: string
  player?: string | null
  away_team?: string | null
  home_team?: string | null
  game?: string | null
  odds: number
  stake?: number | string | null
  edge: number | string
  book?: string | null
  sport?: string | null
  notes?: string | null
  [field: string]: unknown
}
