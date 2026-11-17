'use client'
/** My bets (round-robin parlays fold into one row that opens as a sub-row) and the Pool: everyone else's open bets, with every
 *  leg whose game has not kicked off shown as a lock. The hiding is done by the server (contest.store.visible_bets); this only renders it. */
import { Fragment, useState } from 'react'
import { LockGlyph } from '@/components/ui/lock-glyph'
import { fmtLineSigned, fmtMoney, fmtPrice } from '@/lib/contest/odds'
import type { BetKind, BetStatus, BoardGame, PlacedBet, PoolBet } from '@/lib/contest/types'

const TH: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#7d8596', background: '#11131a', padding: '9px 10px', borderBottom: '1px solid #1e2230', position: 'sticky', top: 0, textAlign: 'left', whiteSpace: 'nowrap' }
const TD: React.CSSProperties = { fontSize: 13, color: '#f3f4f6', padding: '9px 10px', borderBottom: '1px solid #151822', verticalAlign: 'top' }
const WRAP: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'hidden' }
export const KIND_LABEL: Record<BetKind, string> = { straight: 'Straight', parlay: 'Parlay', teaser: 'Teaser', round_robin: 'Round Robin' }
const STATUS_TONE: Record<BetStatus, string> = { pending: '#9ca3af', won: '#57f287', lost: '#ef4444', push: '#94a3b8', void: '#94a3b8' }

export const StatusText = ({ s, label }: { s: BetStatus; label?: string }) => <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: STATUS_TONE[s] }}>{label ?? s.toUpperCase()}</span>

type LegLike = { gameId: number; market: 'spread' | 'total' | 'ml'; side: 'home' | 'away' | 'over' | 'under'; line: number | null; price: number }
export function legText(l: LegLike, byId: Map<number, BoardGame>): string {
  const g = byId.get(l.gameId)
  const team = g ? (l.side === 'home' ? g.home.abbr : g.away.abbr) : '?'
  if (l.market === 'total') return `${l.side === 'over' ? 'Over' : 'Under'} ${l.line} (${g ? `${g.away.abbr}@${g.home.abbr}` : '?'}) ${fmtPrice(l.price)}`
  if (l.market === 'spread') return `${team} ${fmtLineSigned(l.line)} ${fmtPrice(l.price)}`
  return `${team} ML ${fmtPrice(l.price)}`
}

interface Row { key: string; kind: BetKind; label: string; stake: number; toWin: number; status: BetStatus; statusLabel?: string; legs: PlacedBet['legs']; children?: PlacedBet[] }

function fold(bets: PlacedBet[]): Row[] {
  const rows: Row[] = []
  const groups = new Map<string, PlacedBet[]>()
  for (const b of bets) {
    if (b.groupId) { (groups.get(b.groupId) ?? groups.set(b.groupId, []).get(b.groupId)!).push(b); continue }
    rows.push({ key: b.id, kind: b.kind, label: KIND_LABEL[b.kind], stake: b.stakeCents, toWin: b.toWinCents, status: b.status, legs: b.legs })
  }
  for (const [id, kids] of groups) {
    const won = kids.filter(k => k.status === 'won').length, lost = kids.filter(k => k.status === 'lost').length, open = kids.filter(k => k.status === 'pending').length
    const uniq = new Map(kids.flatMap(k => k.legs).map(l => [`${l.gameId}${l.market}${l.side}`, l]))
    rows.push({ key: id, kind: 'round_robin', label: `Round Robin \u00b7 ${kids.length} parlays`, stake: kids.reduce((s, k) => s + k.stakeCents, 0), toWin: kids.reduce((s, k) => s + k.toWinCents, 0),
      status: open ? 'pending' : won ? 'won' : 'lost', statusLabel: open ? `${open} OPEN` : `${won} WON ${lost} LOST`, legs: [...uniq.values()], children: kids })
  }
  return rows
}

export function MyBets({ bets, games }: { bets: PlacedBet[]; games: BoardGame[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const byId = new Map(games.map(g => [g.id, g]))
  const rows = fold(bets)
  if (rows.length === 0) return <p style={{ ...WRAP, padding: 20, fontSize: 13, color: '#7d8596', margin: 0 }}>No bets yet. Your picks are always visible to you and hidden from everyone else until kickoff.</p>
  return (
    <div style={WRAP}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="My bets">
        <thead><tr><th style={TH}>Bet</th><th style={TH}>Selections</th><th style={{ ...TH, textAlign: 'right' }}>Risk</th><th style={{ ...TH, textAlign: 'right' }}>To win</th><th style={TH}>Status</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <Fragment key={r.key}>
              <tr className="contest-row" onClick={r.children ? () => setOpen(open === r.key ? null : r.key) : undefined} style={{ cursor: r.children ? 'pointer' : 'default' }}>
                <td style={{ ...TD, whiteSpace: 'nowrap' }}>{r.label}{r.children && <span className="mono" style={{ color: '#7d8596', marginLeft: 6 }}>{open === r.key ? '-' : '+'}</span>}</td>
                <td style={TD}>{r.legs.map((l, i) => <div key={i} className="mono" style={{ fontSize: 12, color: '#d1d5db' }}>{legText(l, byId)}</div>)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right' }}>{fmtMoney(r.stake)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right', color: '#57f287' }}>{fmtMoney(r.toWin)}</td>
                <td style={TD}><StatusText s={r.status} label={r.statusLabel} /></td>
              </tr>
              {r.children && open === r.key && (
                <tr><td colSpan={5} style={{ ...TD, background: '#0e1017', padding: '8px 10px 8px 24px' }}>
                  {r.children.map(k => (
                    <div key={k.id} className="mono flex flex-wrap gap-x-4" style={{ fontSize: 12, color: '#9ca3af', padding: '2px 0' }}>
                      <span style={{ flex: '1 1 260px' }}>{k.legs.map(l => legText(l, byId)).join('  +  ')}</span>
                      <span>{fmtMoney(k.stakeCents)}</span><StatusText s={k.status} />
                    </div>
                  ))}
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Pool({ bets, games }: { bets: PoolBet[]; games: BoardGame[] }) {
  const byId = new Map(games.map(g => [g.id, g]))
  return (
    <div style={WRAP}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Pool">
        <thead><tr><th style={TH}>Player</th><th style={TH}>Bet</th><th style={TH}>Selections</th><th style={{ ...TH, textAlign: 'right' }}>Risk</th></tr></thead>
        <tbody>
          {bets.map(b => (
            <tr key={b.id} className="contest-row">
              <td style={TD}>{b.owner}</td>
              <td style={{ ...TD, whiteSpace: 'nowrap' }}>{KIND_LABEL[b.kind]}</td>
              <td style={TD}>{b.legs.map((l, i) => l
                ? <div key={i} className="mono" style={{ fontSize: 12, color: '#d1d5db' }}>{legText(l, byId)}</div>
                : <div key={i} className="mono flex items-center gap-1.5" style={{ fontSize: 12, color: '#7d8596' }}><LockGlyph size={12} color="#7d8596" />Hidden until kickoff</div>)}</td>
              <td className="mono" style={{ ...TD, textAlign: 'right', color: b.stakeCents == null ? '#7d8596' : undefined }}>{b.stakeCents == null ? 'Hidden' : fmtMoney(b.stakeCents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
