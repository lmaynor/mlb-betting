import { TEAM_ABBREV, teamLogoSrc } from '@/lib/tokens'

/**
 * Away/home matchup token: overlapping team logos + abbreviated text.
 * Pure presentational (no hooks/handlers) so it renders in server or client
 * trees. Falls back to text-only when a logo isn't shipped for a team.
 */
export function Matchup({
  away,
  home,
  size = 18,
  fontSize = '12px',
  color = 'var(--silver)',
  showText = true,
}: {
  away: string | null | undefined
  home: string | null | undefined
  size?: number
  fontSize?: string
  color?: string
  showText?: boolean
}) {
  const aSrc = teamLogoSrc(away)
  const hSrc = teamLogoSrc(home)
  const aAb = TEAM_ABBREV[away ?? ''] ?? away ?? '?'
  const hAb = TEAM_ABBREV[home ?? ''] ?? home ?? '?'
  const hasLogos = Boolean(aSrc && hSrc)

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
      {hasLogos && (
        <span style={{ display: 'inline-flex', alignItems: 'center', flexShrink: 0 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={aSrc as string} width={size} height={size} alt="" style={{ objectFit: 'contain', display: 'block' }} />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={hSrc as string} width={size} height={size} alt="" style={{ objectFit: 'contain', display: 'block', marginLeft: '-4px' }} />
        </span>
      )}
      {showText && (
        <span className="mono" style={{ fontSize, color, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {aAb} @ {hAb}
        </span>
      )}
    </span>
  )
}
