'use client'
/**
 * The odds board: ONE real <table> with a sticky <thead>. Each game is a row; spread / total / moneyline cells hold two stacked
 * odds buttons (away over home, over over under). A picked button is a flat tint plus a border colour (no glow); closed games
 * are dimmed and their buttons disabled. Section rows (round / day) are plain sub-rows, not extra tables.
 */
import { Fragment } from 'react'
import { fmtLineSigned, fmtPrice } from '@/lib/contest/odds'
import { periodFirst } from '@/lib/contest/state'
import { gameClosed, makePick, samePick } from '@/lib/contest/slip'
import type { BoardGame, ContestRules, Market, Pick, Side } from '@/lib/contest/types'
import { ContestTeamLogo } from './team-logo'

const TH: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#7d8596', background: '#11131a', padding: '9px 8px', borderBottom: '1px solid #1e2230', position: 'sticky', top: 0, zIndex: 2, textAlign: 'center', whiteSpace: 'nowrap' }
const TD: React.CSSProperties = { padding: '8px 6px', borderBottom: '1px solid #151822', verticalAlign: 'middle' }

const dayLabel = (iso: string) => new Date(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York' })
const timeLabel = (iso: string) => new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })

function OddsButton({ pick, line, price, selected, disabled, label, onToggle }: {
  pick: Pick | null; line: string; price: number | null; selected: boolean; disabled: boolean; label: string; onToggle: (p: Pick) => void
}) {
  const off = disabled || !pick
  return (
    <button type="button" disabled={off} aria-pressed={selected} aria-label={label} onClick={() => pick && onToggle(pick)}
      className="mono contest-odds" style={{
        width: '100%', minWidth: 0, height: 40, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1,
        position: 'relative', borderRadius: 6, cursor: off ? 'default' : 'pointer', lineHeight: 1.15,
        background: selected ? 'var(--win-wash)' : '#171a24', border: `1px solid ${selected ? 'var(--signal)' : '#1e2230'}`,
        color: off ? '#4b5469' : '#f3f4f6',
      }}>
      <span style={{ fontSize: 11, color: selected ? 'var(--signal)' : off ? '#4b5469' : '#9ca3af' }}>{line || '\u00a0'}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: selected ? 'var(--signal)' : undefined }}>{fmtPrice(price)}</span>
      {/* a picked price is also marked by a check, so selection never depends on colour alone (hex: var() does not work in SVG attributes) */}
      {selected && <svg aria-hidden="true" data-selected-mark width="10" height="10" viewBox="0 0 10 10" style={{ position: 'absolute', top: 4, right: 4 }}><path d="M2 5.2 4.1 7.3 8 3" fill="none" stroke="#57f287" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>}
    </button>
  )
}

function Team({ g, side }: { g: BoardGame; side: 'away' | 'home' }) {
  const t = g[side]
  const st = g.state
  const score = st ? (side === 'home' ? st.homeScore : st.awayScore) : null
  const other = st ? (side === 'home' ? st.awayScore : st.homeScore) : null
  const trailingFinal = g.status === 'final' && score != null && other != null && score < other
  return (
    <div className="flex items-center gap-2 min-w-0" style={{ height: 40 }}>
      <ContestTeamLogo team={t} size={28} />
      <span className="min-w-0 truncate" style={{ fontSize: 13, fontWeight: 600, color: '#f3f4f6' }}>
        {t.rank ? <span className="mono" style={{ fontSize: 10, color: '#7d8596', marginRight: 4 }}>{t.rank}</span> : null}
        <span className="sm:hidden">{t.abbr}</span><span className="hidden sm:inline">{t.name}</span>
      </span>
      {st?.possession === side && <svg role="img" aria-label={`${t.name} has the ball`} data-possession width="8" height="8" viewBox="0 0 8 8" style={{ flex: '0 0 auto' }}><circle cx="4" cy="4" r="3" fill="#f3f4f6" /></svg>}
      {score != null && <span className="mono ml-auto flex-none" aria-label={`${t.name} score`} style={{ fontSize: 16, fontWeight: 700, paddingLeft: 6, color: trailingFinal ? '#7d8596' : '#f3f4f6' }}>{score}</span>}
    </div>
  )
}

/** The line under a matchup: the game state while it is live (clock, down and distance, red zone), Final, why betting is closed, or the kickoff time. */
function StateLine({ g, closed, reason }: { g: BoardGame; closed: boolean; reason: string }) {
  const st = g.state
  const base: React.CSSProperties = { fontSize: 11, marginTop: 4, lineHeight: 1.4 }
  if (g.status === 'live') {
    return (
      // "3rd Quarter 8:42 . 2nd & 7 at LSU 35": the period and clock lead (primary text), the situation follows (secondary); no "Live" label, the score and clock already say so
      <div className="mono" style={{ ...base, color: '#9ca3af' }}>
        <span style={{ color: '#f3f4f6' }}>{st ? periodFirst(st.detail) : 'In play'}</span>
        {st?.downDistance ? ` \u00b7 ${st.downDistance}` : ''}{st?.redZone ? ' \u00b7 Red zone' : ''}
      </div>
    )
  }
  if (g.status === 'final') return <div className="mono" style={{ ...base, color: '#7d8596' }}>{st?.detail ?? 'Final'}</div>
  return <div className="mono" style={{ ...base, color: closed ? '#facc15' : '#7d8596' }}>{closed ? reason : `${timeLabel(g.commence)} ET`}</div>
}

function Cell({ g, market, picks, now, rules, onToggle }: { g: BoardGame; market: Market; picks: Pick[]; now: number; rules: ContestRules; onToggle: (p: Pick) => void }) {
  const closed = gameClosed(g, now, rules).closed
  const sides: [Side, Side] = market === 'total' ? ['over', 'under'] : ['away', 'home']
  const q = market === 'spread' ? g.spread : market === 'total' ? g.total : g.ml
  return (
    <td style={{ ...TD, opacity: closed ? 0.6 : 1 }}>   {/* only the prices fade when betting is closed; the score and game state stay at full strength */}
      <div className="grid gap-1">
        {sides.map(side => {
          const pick = q ? makePick(g, market, side) : null
          const line = !pick ? '' : market === 'total' ? `${side === 'over' ? 'O' : 'U'} ${pick.line}` : market === 'spread' ? fmtLineSigned(pick.line) : 'ML'
          const who = side === 'over' ? 'Over' : side === 'under' ? 'Under' : g[side].name
          return <OddsButton key={side} pick={pick} line={line} price={pick?.price ?? null} disabled={closed}
            selected={!!pick && picks.some(p => samePick(p, pick))} onToggle={onToggle}
            label={`${who} ${market === 'ml' ? 'moneyline' : `${market} ${line}`} ${fmtPrice(pick?.price)}`.replace(/\s+/g, ' ').trim()} />
        })}
      </div>
    </td>
  )
}

export function Board({ games, picks, now, rules, onToggle }: { games: BoardGame[]; picks: Pick[]; now: number; rules: ContestRules; onToggle: (p: Pick) => void }) {
  const sections: { key: string; label: string; games: BoardGame[] }[] = []
  // games you can still bet come first, in kickoff order; finished and in-play games go last
  const ordered = [...games].sort((a, b) => Number(a.status !== 'scheduled') - Number(b.status !== 'scheduled') || Date.parse(a.commence) - Date.parse(b.commence))
  for (const g of ordered) {
    const label = `${g.round}  \u00b7  ${dayLabel(g.commence)}`
    const last = sections[sections.length - 1]
    if (last && last.label === label) last.games.push(g)
    else sections.push({ key: `${label}-${g.id}`, label, games: [g] })
  }
  return (
    <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, tableLayout: 'fixed' }} aria-label="Odds board">
        <colgroup><col /><col className="contest-col" /><col className="contest-col" /><col className="contest-col" /></colgroup>
        <thead><tr>
          <th scope="col" style={{ ...TH, textAlign: 'left', paddingLeft: 12 }}>Matchup</th>
          <th scope="col" style={TH}>Spread</th><th scope="col" style={TH}>Total</th><th scope="col" style={TH}>Money</th>
        </tr></thead>
        <tbody>
          {sections.map(s => (
            <Fragment key={s.key}>
              <tr><td colSpan={4} className="mono" style={{ padding: '7px 12px', fontSize: 12, color: '#9ca3af', background: '#171a24', borderBottom: '1px solid #1e2230', whiteSpace: 'pre' }}>{s.label}</td></tr>
              {s.games.map(g => {
                const c = gameClosed(g, now, rules)
                return (
                  <tr key={g.id} data-game={g.id} className="contest-row">
                    <td style={{ ...TD, paddingLeft: 12 }}>
                      <div className="grid gap-1">
                        <Team g={g} side="away" /><Team g={g} side="home" />
                      </div>
                      <StateLine g={g} closed={c.closed} reason={c.reason} />
                    </td>
                    {(['spread', 'total', 'ml'] as const).map(m => <Cell key={m} g={g} market={m} picks={picks} now={now} rules={rules} onToggle={onToggle} />)}
                  </tr>
                )
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
