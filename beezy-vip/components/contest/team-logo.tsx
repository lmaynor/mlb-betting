'use client'
/** College team logo from ESPN's CDN, the `500-dark` variant made for dark backgrounds (dark marks such as LSU vanish on the plain one). Keyed by ESPN team id, like the NFL / NCAAB logos elsewhere. A missing or broken image falls back to a monogram. */
import { useState } from 'react'
import { failedOnMount } from '@/components/ui/sport-assets'
import type { TeamRef } from '@/lib/contest/types'

export const teamLogoSrc = (espnId: number | null | undefined): string | null =>
  espnId ? `https://a.espncdn.com/i/teamlogos/ncaa/500-dark/${espnId}.png` : null

export function ContestTeamLogo({ team, size = 28 }: { team: TeamRef; size?: number }) {
  const [bad, setBad] = useState(false)
  const src = teamLogoSrc(team.espnId)
  if (!src || bad) {
    return (
      <span aria-hidden className="mono inline-flex items-center justify-center flex-none" style={{
        width: size, height: size, borderRadius: size / 2, background: '#1b1f2b', border: '1px solid #2e3446', color: '#9ca3af',
        fontSize: Math.max(9, Math.round(size * 0.34)), fontWeight: 600,
      }}>{team.abbr.slice(0, 3)}</span>
    )
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={failedOnMount(setBad)} src={src} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
    onError={() => setBad(true)} style={{ objectFit: 'contain', flex: '0 0 auto' }} />
}
