'use client'
/**
 * <TeamLogo> / <PlayerHeadshot> for NHL + NBA. Team logos hide themselves on a load error; player headshots are
 * hotlinked from CDNs we do not control, so a missing or broken one falls back to an empty silhouette (HeadshotFallback)
 * instead of a broken-image icon. Lazy + async-decoded so a long +EV table does not stall paint.
 */
import { useState } from 'react'
import { anyTeamLogoUrl, headshotUrlById, teamLogoUrl, type AssetSport, type HeadshotSport, type LogoSport } from '@/lib/sport-assets'

/** Callback ref: an <img> server-rendered with a dead URL fails before React hydrates, so onError never fires; catch it on mount. */
export const failedOnMount = (set: (v: boolean) => void) => (el: HTMLImageElement | null) => {
  if (el && el.complete && el.naturalWidth === 0) set(true)
}

export function TeamLogo({ sport, team, size = 24, dark = true }: {
  sport: AssetSport; team: string | null | undefined; size?: number; dark?: boolean
}) {
  const [bad, setBad] = useState(false)
  const src = teamLogoUrl(sport, team, { dark })
  if (!src || bad) return <span className="inline-block" aria-hidden style={{ width: size, height: size }} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={failedOnMount(setBad)} src={src} alt={team ?? ''} width={size} height={size} loading="lazy" decoding="async"
    onError={() => setBad(true)} style={{ objectFit: 'contain', flex: '0 0 auto' }} />
}

/** The empty head-and-shoulders shown when a player has no headshot (unmapped name, 404 from the CDN). Bottom-anchored like a real cutout. */
export function HeadshotFallback({ size = 48, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="No photo" className={className} style={{ flex: '0 0 auto' }}>
      <path fill="#5b6475" d="M50 8c-9 0-17 6-17 19v8c-3-1-5 2-5 6 0 5 2 9 5 9 1 8 7 15 11 18v6c-9 3-22 6-27 14-3 4-5 9-5 12h76c0-3-2-8-5-12-5-8-18-11-27-14v-6c4-3 10-10 11-18 3 0 5-4 5-9 0-4-2-7-5-6v-8C67 14 59 8 50 8z" />
    </svg>
  )
}

/** A headshot <img> that swaps itself for the silhouette when the URL is missing or fails. For call sites that already hold a resolved URL. */
export function HeadshotImg({ src, alt = '', size, className, style }: { src: string | null | undefined; alt?: string; size?: number; className?: string; style?: React.CSSProperties }) {
  const [bad, setBad] = useState(false)
  if (!src || bad) return <HeadshotFallback size={size ?? 48} className={className} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={failedOnMount(setBad)} src={src} alt={alt} width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
    className={className} onError={() => setBad(true)} style={style} />
}

export function PlayerHeadshot({ sport, id, name, size = 48 }: {
  sport: HeadshotSport; id?: number | string | null; name?: string; size?: number
}) {
  const [bad, setBad] = useState(false)
  const src = headshotUrlById(sport, id)
  if (!src || bad) return <HeadshotFallback size={size} />
  // NBA cutouts are 200x146 (landscape), NHL 336x336: contain inside a square, bottom-anchored like the MLB card.
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={failedOnMount(setBad)} src={src} alt={name ?? ''} width={size} height={size} loading="lazy" decoding="async"
    referrerPolicy="no-referrer" onError={() => setBad(true)}
    style={{ objectFit: 'contain', objectPosition: 'center bottom', flex: '0 0 auto' }} />
}

/** A team logo for any sport (MLB / NHL / NBA files, the NFL and NCAAB from ESPN's CDN). Renders nothing when the team is unknown or the image fails. */
export function SportLogo({ sport, team, size = 20 }: { sport: LogoSport; team: string | null | undefined; size?: number }) {
  const [bad, setBad] = useState(false)
  const src = anyTeamLogoUrl(sport, team)
  if (!src || bad) return <span className="inline-block flex-none" aria-hidden style={{ width: size, height: size }} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={failedOnMount(setBad)} src={src} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer"
    onError={() => setBad(true)} style={{ objectFit: 'contain', flex: '0 0 auto' }} />
}

/** "[logo] AWAY @ [logo] HOME" for game rows and headers. Logos hide themselves when a team is unknown, the text stays. */
export function Matchup({ sport, away, home, size = 18 }: { sport: string; away: string; home: string; size?: number }) {
  const lg = sport.toLowerCase() as LogoSport
  return (
    <span data-matchup={`${away} @ ${home}`} className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      <SportLogo sport={lg} team={away} size={size} /><span>{away}</span>
      <span className="text-text-secondary">@</span>
      <SportLogo sport={lg} team={home} size={size} /><span>{home}</span>
    </span>
  )
}
