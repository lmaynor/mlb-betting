'use client'

import { useState, useMemo } from 'react'
import type { TodaySlate, SlateGame, SlatePick } from '@/lib/types'
import { B, SYSTEM_COLOR, SYSTEM_PILL, pickLabel } from '@/lib/tokens'
import { ResultPill } from '@/components/ui/primitives'

// ---- constants ---------------------------------------------------------------

const ALL_FILTER_SYSTEMS = ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'PITCHER_ER', 'BATTER_TB', 'BATTER_HITS', 'BATTER_K', 'SB', 'GAME']
// NOTE: deliberately NOT adding 'EV' here (unlike results-client.tsx and
// clv-tracker) -- get_today_slate()'s SQL (mlb/runners/public_api.py) never
// selects `book`, so SlatePick has no book field at all. PickDetail above
// synthesizes a Bet-shaped object with book hardcoded to `null` for
// pickLabel(), which needs the real book to strip an EV row's "_{book}"
// suffix (see lib/tokens.ts resolveEvUnderlying) -- without it, an EV
// pick here would render with the book fragment leaking into the line
// (e.g. "7.5_draftkings") or failing the bare-string match entirely for
// game-line markets (e.g. "HOME_draftkings" =/= "HOME"). Add `book` to
// get_today_slate()'s SELECT + SlatePick's type first if this is wanted.

// ---- helpers -----------------------------------------------------------------

function edgeColor(e: number) {
  if (e >= 8)  return 'var(--signal)'
  if (e >= 5)  return 'var(--warn)'
  if (e >  0)  return 'var(--fog)'
  return 'var(--loss)'
}

function fmt(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` }
function fmtOdds(n: number) { return n > 0 ? `+${n}` : String(n) }

// ---- sub-components ----------------------------------------------------------

function Chip({ label, active, color, onClick }: {
  label: string; active: boolean; color?: string; onClick: () => void
}) {
  return (
    <button onClick={onClick} style={{
      padding: '5px 11px', fontSize: '10px', fontFamily: 'var(--font-mono), monospace',
      fontWeight: active ? 600 : 500, borderRadius: 'var(--radius-pill)',
      border: `1px solid ${active ? (color ?? 'var(--silver)') : 'var(--basalt)'}`,
      background: active ? `color-mix(in oklab, ${color ?? 'var(--silver)'} 16%, var(--carbon))` : 'var(--graphite)',
      color: active ? (color ?? 'var(--silver)') : 'var(--silver)',
      cursor: 'pointer', letterSpacing: '0.04em', textTransform: 'uppercase' as const,
      transition: 'all var(--dur) var(--ease-out)',
    }}>{label}</button>
  )
}

function SystemPill({ system, edgePct }: { system: string; edgePct: number }) {
  const p = SYSTEM_PILL[system] ?? SYSTEM_PILL['ALL']
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '5px',
      padding: '3px 8px', background: p.bg, border: p.border,
      fontSize: '10px', fontFamily: 'var(--font-mono), monospace',
      fontWeight: 600, letterSpacing: '0.06em',
    }}>
      <span style={{ color: p.color }}>{system}</span>
      <span style={{ color: edgeColor(edgePct), borderLeft: `1px solid ${p.border.slice(11)}`, paddingLeft: '5px' }}>
        {fmt(edgePct)}
      </span>
    </span>
  )
}

function PickDetail({ pick }: { pick: SlatePick }) {
  const bt = {
    id:        pick.game_pk,
    system:    pick.system,
    bet_type:  pick.bet_type,
    player:    pick.player,
    away_team: pick.away_team,
    home_team: pick.home_team,
    odds:      pick.odds,
    stake: 0, model_prob: pick.model_prob_pct / 100, market_prob: pick.market_prob_pct / 100,
    edge: pick.edge_pct / 100, kelly_pct: null, kelly_triggered: true,
    result: pick.result as 'win' | 'loss' | 'push' | 'void' | null,
    profit: null, paper: null, book: null, notes: pick.notes,
    created_at: '', game_date: '', game_pk: pick.game_pk,
  }
  const label  = pickLabel(bt)
  const notes  = pick.notes?.split(' . ').filter(Boolean) ?? []

  return (
    <div style={{ padding: '14px 16px', borderTop: B, background: '#090a0f' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap', marginBottom: notes.length ? '10px' : 0 }}>
        <div>
          <div style={{ fontSize: '12px', color: 'var(--ash)', fontWeight: 600, marginBottom: '4px' }}>{label}</div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
              {fmtOdds(pick.odds)}
            </span>
            <span className="mono" style={{ fontSize: '11px', color: edgeColor(pick.edge_pct), fontWeight: 600 }}>
              edge {fmt(pick.edge_pct)}
            </span>
            <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
              model {pick.model_prob_pct.toFixed(1)}%
            </span>
          </div>
        </div>
        <ResultPill result={pick.result} />
      </div>
      {notes.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {notes.map((n, i) => (
            <span key={i} className="mono" style={{ fontSize: '10px', color: 'var(--fog)', background: 'var(--graphite)', padding: '3px 8px', border: '1px solid var(--basalt)' }}>
              {n}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function GameRow({ game }: { game: SlateGame }) {
  const [open, setOpen] = useState(false)
  const hasPicks = game.picks.length > 0

  return (
    <div style={{ borderBottom: B }}>
      {/* Game header row - clickable */}
      <div
        onClick={() => hasPicks && setOpen(o => !o)}
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          gap: '16px',
          padding: '14px 16px',
          cursor: hasPicks ? 'pointer' : 'default',
          background: open ? '#090a0f' : 'transparent',
          transition: 'background 0.1s',
        }}
      >
        {/* Left: game info */}
        <div>
          {/* Teams + time */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '4px' }}>
            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--ash)' }}>
              {game.away_team} @ {game.home_team}
            </span>
            {game.start_time && (
              <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{game.start_time}</span>
            )}
          </div>
          {/* Starters */}
          <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginBottom: '8px' }}>
            {game.away_pitcher
              ? <>{game.away_pitcher} <span style={{ color: 'var(--fog)' }}>vs</span> {game.home_pitcher ?? 'TBA'}</>
              : <span style={{ color: 'var(--fog)' }}>Starters TBA</span>
            }
          </div>
          {/* Pick pills */}
          {hasPicks ? (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {game.picks.map((pick, i) => (
                <SystemPill key={i} system={pick.system} edgePct={pick.edge_pct} />
              ))}
            </div>
          ) : (
            <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>No picks today</span>
          )}
        </div>

        {/* Right: expand toggle */}
        {hasPicks && (
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)', userSelect: 'none' }}>
              {open ? '-' : '+'}
            </span>
          </div>
        )}
      </div>

      {/* Expanded pick details */}
      {open && game.picks.map((pick, i) => (
        <PickDetail key={i} pick={pick} />
      ))}
    </div>
  )
}

// ---- main component ----------------------------------------------------------

export function SlateClient({ slate, dateLabel }: { slate: TodaySlate; dateLabel: string }) {
  const [picksOnly,       setPicksOnly]       = useState(false)
  const [systemFilter,    setSystemFilter]    = useState<string | null>(null)
  const [refreshing,      setRefreshing]      = useState(false)
  const [slateData,       setSlateData]       = useState(slate)

  async function refresh() {
    setRefreshing(true)
    try {
      const res = await fetch('/api/slate/today')
      if (res.ok) setSlateData(await res.json())
    } finally {
      setRefreshing(false)
    }
  }

  const filtered = useMemo(() => {
    let games = slateData.games
    if (picksOnly) games = games.filter(g => g.picks.length > 0)
    if (systemFilter) games = games.filter(g => g.picks.some(p => p.system === systemFilter))
    return games
  }, [slateData, picksOnly, systemFilter])

  const totalPicks = slateData.total_picks

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 20px' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '14px', flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Today&apos;s Slate</h1>
          <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{dateLabel} &middot; {slateData.total_games} games &middot; {totalPicks} active {totalPicks === 1 ? 'pick' : 'picks'}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {slateData.as_of && (
            <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>
              Updated {new Date(slateData.as_of).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Chicago' })} CT
            </span>
          )}
          <button
            onClick={() => void refresh()}
            disabled={refreshing}
            style={{ fontSize: '10px', fontFamily: 'var(--font-mono), monospace', letterSpacing: '0.06em', textTransform: 'uppercase', padding: '6px 14px', border: B, background: 'transparent', color: refreshing ? 'var(--steel)' : 'var(--fog)', cursor: refreshing ? 'default' : 'pointer' }}
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div style={{ border: B, padding: '12px 16px', marginBottom: '16px', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
          <span
            onClick={() => setPicksOnly(o => !o)}
            style={{
              width: '28px', height: '16px',
              background: picksOnly ? 'var(--signal)' : 'var(--basalt)',
              borderRadius: '8px', position: 'relative' as const, display: 'inline-block',
              flexShrink: 0, cursor: 'pointer', transition: 'background 0.15s',
            }}
          >
            <span style={{
              position: 'absolute', top: '2px',
              left: picksOnly ? '14px' : '2px',
              width: '12px', height: '12px',
              background: picksOnly ? 'var(--carbon)' : 'var(--steel)',
              borderRadius: '50%', transition: 'left 0.15s',
            }} />
          </span>
          <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.05em', textTransform: 'uppercase', userSelect: 'none' }}>Picks only</span>
        </label>

        <div style={{ width: '0.5px', background: 'var(--basalt)', height: '20px', flexShrink: 0 }} />

        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginRight: '4px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>System</span>
          <Chip label="All" active={systemFilter === null} onClick={() => setSystemFilter(null)} />
          {ALL_FILTER_SYSTEMS.map(sys => (
            <Chip
              key={sys}
              label={sys}
              active={systemFilter === sys}
              color={SYSTEM_COLOR[sys]}
              onClick={() => setSystemFilter(systemFilter === sys ? null : sys)}
            />
          ))}
        </div>
      </div>

      {/* Games list */}
      {filtered.length === 0 ? (
        <div style={{ border: B, padding: '60px 20px', textAlign: 'center' }}>
          <div className="mono" style={{ fontSize: '12px', color: 'var(--fog)', marginBottom: '8px' }}>
            {slateData.total_games === 0 ? 'No games scheduled today.' : 'No games match the current filter.'}
          </div>
          {slateData.total_games === 0 && (
            <div className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
              The slate is built from the MLB Stats API. Check back after 10 AM ET.
            </div>
          )}
        </div>
      ) : (
        <div style={{ border: B }}>
          {/* Column headers */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', padding: '8px 16px', background: 'var(--graphite)', borderBottom: B }}>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)' }}>
              Game -- Starters -- Picks
            </span>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)' }}>
              {filtered.length} games
            </span>
          </div>
          {filtered.map(game => (
            <GameRow key={game.game_pk} game={game} />
          ))}
        </div>
      )}

      {/* Footer note */}
      <div style={{ marginTop: '16px' }}>
        <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.6 }}>
          Model picks only, shown after the ~11:00 AM CT run. +EV scanner plays are in the <a href="/ev" style={{ color: 'var(--link)' }}>+EV Terminal</a>.
          Refreshes every 5 minutes.
        </p>
      </div>
    </div>
  )
}
