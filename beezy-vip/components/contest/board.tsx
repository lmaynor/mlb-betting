'use client'
/**
 * The odds board: ONE real <table> with a sticky <thead>. Each game is a row; spread / total / moneyline cells hold two stacked
 * odds buttons (away over home, over over under). A picked button is a flat tint plus a border colour (no glow); closed games
 * are dimmed and their buttons disabled. Section rows (round / day) are plain sub-rows, not extra tables.
 */
import { Fragment } from 'react'
import { fmtLineSigned, fmtPrice } from '@/lib/contest/odds'
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
        width: '100%', minWidth: 64, height: 40, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1,
        borderRadius: 6, cursor: off ? 'default' : 'pointer', lineHeight: 1.15,
        background: selected ? 'var(--win-wash)' : '#171a24', border: `1px solid ${selected ? 'var(--signal)' : '#1e2230'}`,
        color: off ? '#4b5469' : '#f3f4f6',
      }}>
      <span style={{ fontSize: 11, color: selected ? 'var(--signal)' : off ? '#4b5469' : '#9ca3af' }}>{line || '\u00a0'}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: selected ? 'var(--signal)' : undefined }}>{fmtPrice(price)}</span>
    </button>
  )
}

function Team({ g, side }: { g: BoardGame; side: 'away' | 'home' }) {
  const t = g[side]
  return (
    <div className="flex items-center gap-2 min-w-0" style={{ height: 40 }}>
      <ContestTeamLogo team={t} size={28} />
      <span className="min-w-0 truncate" style={{ fontSize: 13, fontWeight: 600, color: '#f3f4f6' }}>
        {t.rank ? <span className="mono" style={{ fontSize: 10, color: '#7d8596', marginRight: 4 }}>{t.rank}</span> : null}
        <span className="sm:hidden">{t.abbr}</span><span className="hidden sm:inline">{t.name}</span>
      </span>
    </div>
  )
}

function Cell({ g, market, picks, now, rules, onToggle }: { g: BoardGame; market: Market; picks: Pick[]; now: number; rules: ContestRules; onToggle: (p: Pick) => void }) {
  const closed = gameClosed(g, now, rules).closed
  const sides: [Side, Side] = market === 'total' ? ['over', 'under'] : ['away', 'home']
  const q = market === 'spread' ? g.spread : market === 'total' ? g.total : g.ml
  return (
    <td style={TD}>
      <div className="grid gap-1">
        {sides.map(side => {
          const pick = q ? makePick(g, market, side) : null
          const line = !pick ? '' : market === 'total' ? `${side === 'over' ? 'O' : 'U'} ${pick.line}` : market === 'spread' ? fmtLineSigned(pick.line) : ''
          const who = side === 'over' ? 'Over' : side === 'under' ? 'Under' : g[side].name
          return <OddsButton key={side} pick={pick} line={line} price={pick?.price ?? null} disabled={closed}
            selected={!!pick && picks.some(p => samePick(p, pick))} onToggle={onToggle}
            label={`${who} ${market === 'ml' ? 'moneyline' : market} ${line} ${fmtPrice(pick?.price)}`.replace(/\s+/g, ' ').trim()} />
        })}
      </div>
    </td>
  )
}

export function Board({ games, picks, now, rules, onToggle }: { games: BoardGame[]; picks: Pick[]; now: number; rules: ContestRules; onToggle: (p: Pick) => void }) {
  const sections: { key: string; label: string; games: BoardGame[] }[] = []
  for (const g of [...games].sort((a, b) => Date.parse(a.commence) - Date.parse(b.commence))) {
    const label = `${g.round}  \u00b7  ${dayLabel(g.commence)}`
    const last = sections[sections.length - 1]
    if (last && last.label === label) last.games.push(g)
    else sections.push({ key: `${label}-${g.id}`, label, games: [g] })
  }
  return (
    <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, tableLayout: 'fixed' }} aria-label="Odds board">
        <colgroup><col /><col style={{ width: 92 }} /><col style={{ width: 92 }} /><col style={{ width: 92 }} /></colgroup>
        <thead><tr>
          <th scope="col" style={{ ...TH, textAlign: 'left', paddingLeft: 12 }}>Matchup</th>
          <th scope="col" style={TH}>Spread</th><th scope="col" style={TH}>Total</th><th scope="col" style={TH}>Money</th>
        </tr></thead>
        <tbody>
          {sections.map(s => (
            <Fragment key={s.key}>
              <tr><td colSpan={4} className="mono" style={{ padding: '7px 12px', fontSize: 11, color: '#9ca3af', background: '#171a24', borderBottom: '1px solid #1e2230', whiteSpace: 'pre' }}>{s.label}</td></tr>
              {s.games.map(g => {
                const c = gameClosed(g, now, rules)
                return (
                  <tr key={g.id} data-game={g.id} className="contest-row" style={{ opacity: c.closed ? 0.55 : 1 }}>
                    <td style={{ ...TD, paddingLeft: 12 }}>
                      <div className="grid gap-1">
                        <Team g={g} side="away" /><Team g={g} side="home" />
                      </div>
                      <div className="mono" style={{ fontSize: 10, color: c.closed ? '#facc15' : '#7d8596', marginTop: 4 }}>
                        {c.closed ? c.reason : `${timeLabel(g.commence)} ET`}
                      </div>
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
