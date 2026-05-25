export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { apiGetTodayPicks } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'

export const metadata: Metadata = {
  title: 'Cheat Sheet — Beezy.VIP',
  description: "Today's MLB picks in one screenshottable card.",
  openGraph: {
    title: "Today's MLB Picks — Beezy.VIP",
    description: 'NRFI · HR · F5 · K · OUTS — edge-ranked picks backed by ML models.',
    images: [{ url: '/api/og?title=MLB+Cheat+Sheet', width: 1200, height: 630 }],
  },
}

// ── Team abbreviation → logo slug ─────────────────────────────────────────────
const TEAM_SLUG: Record<string, string> = {
  ARI:'ari', ATL:'atl', BAL:'bal', BOS:'bos', CHC:'chc', CIN:'cin', CLE:'cle',
  COL:'col', CWS:'cws', DET:'det', HOU:'hou', KC:'kc',  LAA:'laa', LAD:'lad',
  MIA:'mia', MIL:'mil', MIN:'min', NYM:'nym', NYY:'nyy', OAK:'oak', PHI:'phi',
  PIT:'pit', SD:'sd',  SEA:'sea', SF:'sf',  STL:'stl', TB:'tb',  TEX:'tex',
  TOR:'tor', WSH:'wsh',
}

const SYSTEM_COLOR: Record<string, string> = {
  NRFI:        '#10b981',
  HR:          '#f59e0b',
  F5:          '#3b82f6',
  K:           '#a78bfa',
  OUTS:        '#fb923c',
  BATTER_HITS: '#e879f9',
}

const SYSTEM_LABEL: Record<string, string> = {
  NRFI:        'NRFI',
  HR:          'HR',
  F5:          'F5',
  K:           'K',
  OUTS:        'OUTS',
  BATTER_HITS: 'HITS',
}

// Props systems have a named player; game systems use the matchup
const IS_PROP = (s: string) => ['HR', 'K', 'OUTS', 'BATTER_HITS'].includes(s)

function fmtOdds(o: number) {
  return o > 0 ? `+${o}` : String(o)
}

function fmtEdge(e: number | null) {
  if (e == null) return null
  const pct = e < 1 ? e * 100 : e
  return `+${pct.toFixed(1)}%`
}

function logoUrl(abbrev: string | null) {
  const slug = TEAM_SLUG[(abbrev ?? '').toUpperCase()] ?? null
  return slug ? `/logos/${slug}.png` : null
}

// Load player_map at module level (server only)
// eslint-disable-next-line @typescript-eslint/no-require-imports
const PLAYER_MAP: Record<string, number> = (() => {
  try { return require('@/public/headshots/player_map.json') }
  catch { return {} }
})()

function headshotUrl(name: string | null): string | null {
  if (!name) return null
  const key = name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
  const id = PLAYER_MAP[key]
  if (!id) return null
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${id}/headshot/67/current`
}

function dateLabel() {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  }).toUpperCase()
}

// ── Pick card ─────────────────────────────────────────────────────────────────
function PickCard({ bet, rank }: { bet: Bet; rank: number }) {
  const color  = SYSTEM_COLOR[bet.system] ?? '#71717a'
  const isProp = IS_PROP(bet.system)
  const edge   = fmtEdge(bet.edge)
  const bullets = bet.notes ? bet.notes.split(' · ').filter(Boolean) : []

  const awayLogo = logoUrl(bet.away_team)
  const homeLogo = logoUrl(bet.home_team)
  const shot     = isProp ? headshotUrl(bet.player) : null

  // Pick label: for game picks show "Away @ Home", for props show player name
  const title = isProp
    ? (bet.player ?? bet.bet_type)
    : `${bet.away_team ?? '?'} @ ${bet.home_team ?? '?'}`

  // Sub-label: bet type / line
  const sub = bet.bet_type
    ? `${bet.bet_type} · ${fmtOdds(bet.odds)}`
    : fmtOdds(bet.odds)

  return (
    <div style={{
      background: '#0e0e14',
      borderLeft: `3px solid ${color}`,
      marginBottom: '1px',
      display: 'flex',
      alignItems: 'stretch',
      minHeight: '80px',
      overflow: 'hidden',
    }}>
      {/* Rank */}
      <div style={{
        width: '28px', minWidth: '28px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderRight: '1px solid #1a1a22',
      }}>
        <span style={{ fontFamily: 'monospace', fontSize: '10px', color: '#3a3a48', fontWeight: 700 }}>
          #{rank}
        </span>
      </div>

      {/* Logo / Headshot */}
      <div style={{
        width: '64px', minWidth: '64px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '8px 4px',
        borderRight: '1px solid #1a1a22',
        background: '#0a0a0f',
        position: 'relative',
      }}>
        {isProp && shot ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={shot}
            alt={bet.player ?? ''}
            width={52}
            height={52}
            style={{ borderRadius: '50%', objectFit: 'cover', border: `2px solid ${color}22` }}
          />
        ) : !isProp && (awayLogo || homeLogo) ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', alignItems: 'center' }}>
            {awayLogo && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={awayLogo} alt={bet.away_team ?? ''} width={26} height={26} style={{ objectFit: 'contain' }} />
            )}
            {homeLogo && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={homeLogo} alt={bet.home_team ?? ''} width={26} height={26} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          <span style={{ fontFamily: 'monospace', fontSize: '11px', color: color, fontWeight: 700 }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
        )}
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0 }}>
        {/* System badge + title row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
          <span style={{
            fontFamily: 'monospace', fontSize: '8px', fontWeight: 700,
            letterSpacing: '0.08em', padding: '2px 5px',
            background: `${color}18`, color: color, border: `1px solid ${color}44`,
          }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
          <span style={{
            fontSize: '13px', fontWeight: 700, color: '#f0f0f5',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {title}
          </span>
        </div>

        {/* Sub-label */}
        <div style={{ fontFamily: 'monospace', fontSize: '10px', color: '#52525b', marginBottom: bullets.length ? '6px' : 0 }}>
          {sub}
        </div>

        {/* Rationale bullets */}
        {bullets.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {bullets.slice(0, 3).map((b, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px' }}>
                <span style={{ color: color, fontSize: '9px', lineHeight: '14px', flexShrink: 0 }}>▸</span>
                <span style={{ fontFamily: 'monospace', fontSize: '9px', color: '#8888a0', lineHeight: '14px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Edge badge */}
      {edge && (
        <div style={{
          minWidth: '54px', display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          borderLeft: '1px solid #1a1a22', padding: '8px 6px',
          background: '#08080d',
        }}>
          <span style={{ fontFamily: 'monospace', fontSize: '7px', color: '#3a3a48', letterSpacing: '0.1em', marginBottom: '2px' }}>EDGE</span>
          <span style={{ fontFamily: 'monospace', fontSize: '13px', fontWeight: 700, color: color }}>{edge}</span>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default async function CheatSheetPage() {
  const picks = await apiGetTodayPicks().catch(() => [] as Bet[])
  const today = dateLabel()

  // Sort by edge descending
  const sorted = [...picks].sort((a, b) => {
    const ea = a.edge ?? 0
    const eb = b.edge ?? 0
    return eb - ea
  })

  return (
    <div style={{
      minHeight: '100vh',
      background: '#06060a',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'flex-start',
      padding: '24px 12px 40px',
      fontFamily: 'Inter, -apple-system, sans-serif',
    }}>
      {/* Card container — 390px matches iPhone viewport */}
      <div style={{
        width: '100%',
        maxWidth: '390px',
        border: '1px solid #22222e',
        background: '#08080d',
        boxShadow: '0 0 40px 0 #10b98112, 0 0 0 1px #10b98108',
        overflow: 'hidden',
      }}>

        {/* Header */}
        <div style={{
          background: 'linear-gradient(135deg, #0d1a12 0%, #09090f 60%)',
          padding: '20px 18px 14px',
          borderBottom: '1px solid #1a1a22',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
            <div style={{ fontSize: '22px', fontWeight: 800, letterSpacing: '-0.02em', color: '#f5f5f7' }}>
              BEEZY<span style={{ color: '#10b981' }}>.VIP</span>
            </div>
            <div style={{
              fontFamily: 'monospace', fontSize: '8px', fontWeight: 600,
              letterSpacing: '0.12em', color: '#10b981', padding: '4px 8px',
              border: '1px solid #0f6e5644', background: '#052016',
            }}>
              CHEAT SHEET
            </div>
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: '10px', color: '#52525b', letterSpacing: '0.08em' }}>
            {today} · {sorted.length} PICKS
          </div>
        </div>

        {/* Column headers */}
        <div style={{
          display: 'flex',
          borderBottom: '1px solid #1a1a22',
          background: '#0a0a0f',
          padding: '6px 0',
        }}>
          <div style={{ width: '28px', minWidth: '28px' }} />
          <div style={{ width: '64px', minWidth: '64px' }} />
          <div style={{ flex: 1, paddingLeft: '12px', fontFamily: 'monospace', fontSize: '8px', color: '#3a3a48', letterSpacing: '0.1em' }}>
            PICK
          </div>
          <div style={{ minWidth: '54px', textAlign: 'center', fontFamily: 'monospace', fontSize: '8px', color: '#3a3a48', letterSpacing: '0.1em' }}>
            EDGE
          </div>
        </div>

        {/* Picks */}
        {sorted.length === 0 ? (
          <div style={{ padding: '48px 24px', textAlign: 'center' }}>
            <div style={{ fontFamily: 'monospace', fontSize: '11px', color: '#3a3a48', letterSpacing: '0.08em' }}>
              NO PICKS TODAY
            </div>
            <div style={{ fontSize: '12px', color: '#2a2a31', marginTop: '6px' }}>
              Check back after 9 AM ET
            </div>
          </div>
        ) : (
          <div>
            {sorted.map((bet, i) => (
              <PickCard key={bet.id} bet={bet} rank={i + 1} />
            ))}
          </div>
        )}

        {/* Footer */}
        <div style={{
          borderTop: '1px solid #1a1a22',
          padding: '12px 18px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: '#06060a',
        }}>
          <span style={{ fontFamily: 'monospace', fontSize: '9px', color: '#2a2a31', letterSpacing: '0.08em' }}>
            BEEZY.VIP · ML-POWERED PICKS
          </span>
          <span style={{ fontFamily: 'monospace', fontSize: '9px', color: '#2a2a31', letterSpacing: '0.06em' }}>
            discord.gg/HfMYCmbmE
          </span>
        </div>
      </div>

      {/* Back link — outside the card, not visible in screenshot */}
      <a href="/picks" style={{
        marginTop: '20px', fontFamily: 'monospace', fontSize: '10px',
        color: '#3a3a48', textDecoration: 'none', letterSpacing: '0.06em',
      }}>
        ← FULL PICKS
      </a>
    </div>
  )
}
