import { TEAM_ABBREV, teamLogoSrc } from '@/lib/tokens'
import { anyTeamLogoUrl, type LogoSport } from '@/lib/sport-assets'

/**
 * Away/home matchup token: overlapping team logos + abbreviated text.
 * Pure presentational (no hooks/handlers) so it renders in server or client
 * trees. Falls back to text-only when a logo isn't shipped for a team.
 *
 * Pass `sport` (the row's league) wherever the row carries one: logos then resolve through the canonical
 * (league, abbrev) mapping in lib/sport-assets, so a Chiefs row can never wear the Royals logo. Without a
 * sport the legacy MLB-only resolution (lib/tokens teamLogoSrc) applies, for callers whose rows are MLB by
 * construction and carry no league.
 */
export function Matchup({
  away,
  home,
  size = 18,
  fontSize = '12px',
  color = 'var(--color-neutral-300)',
  showText = true,
  sport,
}: {
  away: string | null | undefined
  home: string | null | undefined
  size?: number
  fontSize?: string
  color?: string
  showText?: boolean
  sport?: string | null
}) {
  const lg = sport ? (sport.toLowerCase() as LogoSport) : null
  const resolve = (team: string | null | undefined) => (lg ? anyTeamLogoUrl(lg, team) : teamLogoSrc(team))
  const label = (team: string | null | undefined) =>
    lg && lg !== 'mlb' ? (team ?? '?') : (TEAM_ABBREV[team ?? ''] ?? team ?? '?')
  const aSrc = resolve(away)
  const hSrc = resolve(home)
  const aAb = label(away)
  const hAb = label(home)

  return (
    <span className="inline-flex items-center gap-1.5 min-w-0 whitespace-nowrap">
      <TeamToken src={aSrc} ab={aAb} size={size} fontSize={fontSize} color={color} showText={showText} />
      {showText && <span className="mono" style={{ fontSize, color: 'var(--color-text-secondary)' }}>@</span>}
      <TeamToken src={hSrc} ab={hAb} size={size} fontSize={fontSize} color={color} showText={showText} />
    </span>
  )
}

// Each logo sits immediately before ITS OWN team code -- [logo]AWY @ [logo]HOM.
// (The old overlapped-logos-then-text layout read as a jumble in narrow cells.)
function TeamToken({ src, ab, size, fontSize, color, showText }: {
  src: string | null; ab: string; size: number; fontSize: string; color: string; showText: boolean
}) {
  return (
    <span className="inline-flex items-center gap-1 shrink-0">
      {src && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} width={size} height={size} alt="" style={{ objectFit: 'contain', display: 'block' }} />
      )}
      {showText && (
        <span className="mono" style={{ fontSize, color, whiteSpace: 'nowrap' }}>{ab}</span>
      )}
    </span>
  )
}
