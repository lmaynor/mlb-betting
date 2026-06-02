'use client'

import { useState, useMemo } from 'react'
import type { TodaySlate, SlateGame, SlatePick } from '@/lib/types'
import { B, SYSTEM_COLOR, SYSTEM_PILL, pickLabel } from '@/lib/tokens'
import { ResultPill } from '@/components/ui/primitives'

// ---- constants ---------------------------------------------------------------

const ALL_FILTER_SYSTEMS = ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'GAME']

// ---- helpers -----------------------------------------------------------------

function edgeColor(e: number) {
  if (e >= 8)  return '#10b981'
  if (e >= 5)  return '#f59e0b'
  if (e >  0)  return '#71717a'
  return '#ef4444'
}

function fmt(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` }
function fmtOdds(n: number) { return n > 0 ? `+${n}` : String(n) }

// ---- sub-components ----------------------------------------------------------

function Chip({ label, active, color, onClick }: {
  label: string; active: boolean; color?: string; onClick: () => void
}) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 10px', fontSize: '10px', fontFamily: 'JetBrains Mono, monospace',
      fontWeight: active ? 600 : 400,
      border: `0.5px solid ${active ? (color ?? '#10b981') : '#2a2a31'}`,
      background: active ? `${color ?? '#10b981'}18` : 'transparent',
      color: active ? (color ?? '#10b981') : '#52525b',
      cursor: 'pointer', letterSpacing: '0.05em', textTransform: 'uppercase' as const,
    }}>{label}</button>
  )
}

function SystemPill({ system, edgePct }: { system: string; edgePct: number }) {
  const p = SYSTEM_PILL[system] ?? SYSTEM_PILL['ALL']
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '5px',
      padding: '3px 8px', background: p.bg, border: p.border,
      fontSize: '9px', fontFamily: 'JetBrains Mono, monospace',
      fontWeight: 600, letterSpacing: '0.06em',
    }}>
      <span style={{ color: p.color }}>{system}</span>
      <span style={{ color: edgeColor(edgePct), borderLeft: `0.5px solid ${p.border.slice(12)}`, paddingLeft: '5px' }}>
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
    <div style={{ padding: '14px 16px', borderTop: B, background: '#0d0d10' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap', marginBottom: notes.length ? '10px' : 0 }}>
        <div>
          <div style={{ fontSize: '12px', color: '#f5f5f7', fontWeight: 600, marginBottom: '4px' }}>{label}</div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>
              {fmtOdds(pick.odds)}
            </span>
            <span className="mono" style={{ fontSize: '11px', color: edgeColor(pick.edge_pct), fontWeight: 600 }}>
              edge {fmt(pick.edge_pct)}
            </span>
            <span className="mono" style={{ fontSize: '11px', color: '#52525b' }}>
              model {pick.model_prob_pct.toFixed(1)}%
            </span>
          </div>
        </div>
        <ResultPill result={pick.result} />
      </div>
      {notes.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {notes.map((n, i) => (
            <span key={i} className="mono" style={{ fontSize: '10px', color: '#3f3f46', background: '#111114', padding: '3px 8px', border: '0.5px solid #1f1f24' }}>
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
          background: open ? '#0d0d10' : 'transparent',
          transition: 'background 0.1s',
        }}
      >
        {/* Left: game info */}
        <div>
          {/* Teams + time */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '4px' }}>
            <span style={{ fontSize: '14px', fontWeight: 600, color: '#f5f5f7' }}>
              {game.away_team} @ {game.home_team}
            </span>
            {game.start_time && (
              <span className="mono" style={{ fontSize: '10px', color: '#52525b' }}>{game.start_time}</span>
            )}
          </div>
          {/* Starters */}
          <div className="mono" style={{ fontSize: '11px', color: '#3f3f46', marginBottom: '8px' }}>
            {game.away_pitcher
              ? <>{game.away_pitcher} <span style={{ color: '#27272a' }}>vs</span> {game.home_pitcher ?? 'TBA'}</>
              : <span style={{ color: '#27272a' }}>Starters TBA</span>
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
            <span className="mono" style={{ fontSize: '10px', color: '#27272a' }}>No picks today</span>
          )}
        </div>

        {/* Right: expand toggle */}
        {hasPicks && (
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: '12px', color: '#3f3f46', userSelect: 'none' }}>
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
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>Tools -- Pro</p>
          <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px', letterSpacing: '-0.01em' }}>Slate Command Center</h1>
          <p style={{ fontSize: '13px', color: '#71717a' }}>{dateLabel} &mdash; {slateData.total_games} games &mdash; {totalPicks} active {totalPicks === 1 ? 'pick' : 'picks'}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {slateData.as_of && (
            <span className="mono" style={{ fontSize: '10px', color: '#3f3f46' }}>
              Updated {new Date(slateData.as_of).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Chicago' })} CT
            </span>
          )}
          <button
            onClick={() => void refresh()}
            disabled={refreshing}
            style={{ fontSize: '10px', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.06em', textTransform: 'uppercase', padding: '6px 14px', border: B, background: 'transparent', color: refreshing ? '#3f3f46' : '#71717a', cursor: refreshing ? 'default' : 'pointer' }}
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
              background: picksOnly ? '#10b981' : '#1f1f24',
              borderRadius: '8px', position: 'relative' as const, display: 'inline-block',
              flexShrink: 0, cursor: 'pointer', transition: 'background 0.15s',
            }}
          >
            <span style={{
              position: 'absolute', top: '2px',
              left: picksOnly ? '14px' : '2px',
              width: '12px', height: '12px',
              background: picksOnly ? '#0a0a0c' : '#52525b',
              borderRadius: '50%', transition: 'left 0.15s',
            }} />
          </span>
          <span className="mono" style={{ fontSize: '10px', color: '#71717a', letterSpacing: '0.05em', textTransform: 'uppercase', userSelect: 'none' }}>Picks only</span>
        </label>

        <div style={{ width: '0.5px', background: '#1f1f24', height: '20px', flexShrink: 0 }} />

        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', color: '#3f3f46', marginRight: '4px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>System</span>
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
          <div className="mono" style={{ fontSize: '12px', color: '#3f3f46', marginBottom: '8px' }}>
            {slateData.total_games === 0 ? 'No games scheduled today.' : 'No games match the current filter.'}
          </div>
          {slateData.total_games === 0 && (
            <div className="mono" style={{ fontSize: '11px', color: '#27272a' }}>
              The slate is built from the MLB Stats API. Check back after 10 AM ET.
            </div>
          )}
        </div>
      ) : (
        <div style={{ border: B }}>
          {/* Column headers */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', padding: '8px 16px', background: '#111114', borderBottom: B }}>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#3f3f46' }}>
              Game -- Starters -- Picks
            </span>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#3f3f46' }}>
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
        <p className="mono" style={{ fontSize: '10px', color: '#27272a' }}>
          Picks shown are kelly-triggered. Picks appear after the 11 AM ET morning scoring run.
          Data refreshes every 5 minutes automatically.
        </p>
      </div>
    </div>
  )
}
