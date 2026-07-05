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

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', minWidth: 0, whiteSpace: 'nowrap' }}>
      <TeamToken src={aSrc} ab={aAb} size={size} fontSize={fontSize} color={color} showText={showText} />
      {showText && <span className="mono" style={{ fontSize, color: 'var(--steel)' }}>@</span>}
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
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
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
