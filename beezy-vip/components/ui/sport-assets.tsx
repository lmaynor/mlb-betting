'use client'
/**
 * <TeamLogo> / <PlayerHeadshot> for NHL + NBA. Pure inline styles (this repo's rule), and BOTH hide themselves on a
 * load error: team logos are local files but player headshots are hotlinked from CDNs we do not control, and a broken
 * image icon on a pick card is worse than no image. Lazy + async-decoded so a long +EV table does not stall paint.
 */
import { useState } from 'react'
import { anyTeamLogoUrl, headshotUrlById, teamLogoUrl, type AssetSport, type HeadshotSport, type LogoSport } from '@/lib/sport-assets'

export function TeamLogo({ sport, team, size = 24, dark = true }: {
  sport: AssetSport; team: string | null | undefined; size?: number; dark?: boolean
}) {
  const [bad, setBad] = useState(false)
  const src = teamLogoUrl(sport, team, { dark })
  if (!src || bad) return <span aria-hidden style={{ display: 'inline-block', width: size, height: size }} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={team ?? ''} width={size} height={size} loading="lazy" decoding="async"
    onError={() => setBad(true)} style={{ objectFit: 'contain', flex: '0 0 auto' }} />
}

export function PlayerHeadshot({ sport, id, name, size = 48 }: {
  sport: HeadshotSport; id?: number | string | null; name?: string; size?: number
}) {
  const [bad, setBad] = useState(false)
  const src = headshotUrlById(sport, id)
  if (!src || bad) return null
  // NBA cutouts are 200x146 (landscape), NHL 336x336: contain inside a square, bottom-anchored like the MLB card.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={name ?? ''} width={size} height={size} loading="lazy" decoding="async"
    referrerPolicy="no-referrer" onError={() => setBad(true)}
    style={{ objectFit: 'contain', objectPosition: 'center bottom', flex: '0 0 auto' }} />
}

/** A team logo for any sport (MLB / NHL / NBA files, the NFL from ESPN's CDN). Renders nothing when the team is unknown or the image fails. */
export function SportLogo({ sport, team, size = 20 }: { sport: LogoSport; team: string | null | undefined; size?: number }) {
  const [bad, setBad] = useState(false)
  const src = anyTeamLogoUrl(sport, team)
  if (!src || bad) return <span aria-hidden style={{ display: 'inline-block', width: size, height: size, flex: '0 0 auto' }} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
    onError={() => setBad(true)} style={{ objectFit: 'contain', flex: '0 0 auto' }} />
}
