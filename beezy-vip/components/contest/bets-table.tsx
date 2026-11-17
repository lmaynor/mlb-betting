'use client'
/** My bets (round-robin parlays fold into one row that opens as a sub-row) and the Pool: everyone else's open bets, with every
 *  leg whose game has not kicked off shown as a lock. The hiding is done by the server (contest.store.visible_bets); this only renders it. */
import { Fragment, useState } from 'react'
import { LockGlyph } from '@/components/ui/lock-glyph'
import { fmtLineSigned, fmtMoney } from '@/lib/contest/odds'
import { fmtOdds, type OddsFormat } from '@/lib/contest/format'
import { useOddsFormat } from './odds-format'
import { CASHOUT_REASON_TEXT, cashoutGroup } from '@/lib/contest/cashout'
import { gameClosed, makePick } from '@/lib/contest/slip'
import type { BetKind, BetStatus, BoardGame, ContestRules, PlacedBet, PoolBet } from '@/lib/contest/types'

const TH: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#7d8596', background: '#11131a', padding: '9px 10px', borderBottom: '1px solid #1e2230', position: 'sticky', top: 0, textAlign: 'left', whiteSpace: 'nowrap' }
const TD: React.CSSProperties = { fontSize: 13, color: '#f3f4f6', padding: '9px 10px', borderBottom: '1px solid #151822', verticalAlign: 'top' }
const WRAP: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'hidden' }
export const KIND_LABEL: Record<BetKind, string> = { straight: 'Straight', parlay: 'Parlay', teaser: 'Teaser', round_robin: 'Round Robin', sgp: 'SGP' }
const STATUS_TONE: Record<BetStatus, string> = { pending: '#9ca3af', won: '#57f287', lost: '#ef4444', push: '#94a3b8', void: '#94a3b8', cashed_out: '#9ca3af' }

export const StatusText = ({ s, label }: { s: BetStatus; label?: string }) => <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: STATUS_TONE[s] }}>{label ?? s.replace('_', ' ').toUpperCase()}</span>

type LegLike = { gameId: number; market: 'spread' | 'total' | 'ml'; side: 'home' | 'away' | 'over' | 'under'; line: number | null; price: number }
export function legText(l: LegLike, byId: Map<number, BoardGame>, format: OddsFormat = 'american'): string {
  const fmtPrice = (p: number) => fmtOdds(p, format)
  const g = byId.get(l.gameId)
  const team = g ? (l.side === 'home' ? g.home.abbr : g.away.abbr) : '?'
  if (l.market === 'total') return `${l.side === 'over' ? 'Over' : 'Under'} ${l.line} (${g ? `${g.away.abbr}@${g.home.abbr}` : '?'}) ${fmtPrice(l.price)}`
  if (l.market === 'spread') return `${team} ${fmtLineSigned(l.line)} ${fmtPrice(l.price)}`
  return `${team} ML ${fmtPrice(l.price)}`
}

interface Row { key: string; kind: BetKind; label: string; stake: number; toWin: number; status: BetStatus; statusLabel?: string; legs: PlacedBet['legs']; children?: PlacedBet[]; bets: PlacedBet[] }

function fold(bets: PlacedBet[]): Row[] {
  const rows: Row[] = []
  const groups = new Map<string, PlacedBet[]>()
  for (const b of bets) {
    if (b.groupId) { (groups.get(b.groupId) ?? groups.set(b.groupId, []).get(b.groupId)!).push(b); continue }
    rows.push({ key: b.id, kind: b.kind, label: KIND_LABEL[b.kind], stake: b.stakeCents, toWin: b.toWinCents, status: b.status, legs: b.legs, bets: [b] })
  }
  for (const [id, kids] of groups) {
    const n = (s: BetStatus) => kids.filter(k => k.status === s).length
    const won = n('won'), lost = n('lost'), open = n('pending'), cashed = n('cashed_out')
    const uniq = new Map(kids.flatMap(k => k.legs).map(l => [`${l.gameId}${l.market}${l.side}`, l]))
    const tail = cashed ? ` ${cashed} CASHED OUT` : ''
    rows.push({ key: id, kind: 'round_robin', label: `Round Robin \u00b7 ${kids.length} parlays`, stake: kids.reduce((a, k) => a + k.stakeCents, 0), toWin: kids.reduce((a, k) => a + k.toWinCents, 0),
      status: open ? 'pending' : cashed === kids.length ? 'cashed_out' : won ? 'won' : 'lost', statusLabel: open ? `${open} OPEN${tail}` : cashed === kids.length ? 'CASHED OUT' : `${won} WON ${lost} LOST${tail}`,
      legs: [...uniq.values()], children: kids, bets: kids })
  }
  return rows
}

const BTN: React.CSSProperties = { fontSize: 12, fontWeight: 600, padding: '6px 10px', minHeight: 44, borderRadius: 6, border: '1px solid #2e3446', background: '#171a24', color: '#f3f4f6' }

/** The cash-out control under an open bet's status: the value now, or the reason it is not offered; asking opens an inline Confirm / Cancel (no modal). */
function CashoutControl({ row, rules, byId, now, confirming, setConfirming, onConfirm }: {
  row: Row; rules: ContestRules; byId: Map<number, BoardGame>; now: number
  confirming: boolean; setConfirming: (key: string | null) => void; onConfirm: (row: Row, offers: { id: string; cents: number }[], cents: number, stake: number) => void | Promise<void>
}) {
  const c = cashoutGroup(row.bets, byId, rules, now)
  if (!c.ok) return c.reason === 'disabled' || c.reason === 'settled' ? null : <div className="mono" style={{ fontSize: 12, color: '#7d8596', marginTop: 6 }}>{CASHOUT_REASON_TEXT[c.reason]}</div>
  const delta = c.cents - c.stakeCents
  return (
    <div style={{ marginTop: 6 }}>
      {confirming ? (
        <div role="group" aria-label="Confirm cash out">
          <div className="mono" style={{ fontSize: 12, color: '#f3f4f6', marginBottom: 6 }}>Cash out for {fmtMoney(c.cents)}?</div>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" style={{ ...BTN, border: undefined, background: undefined, color: undefined }} onClick={e => { e.stopPropagation(); onConfirm(row, c.offers, c.cents, c.stakeCents) }}>Confirm {fmtMoney(c.cents)}</button>
            <button type="button" className="btn-ghost" style={BTN} onClick={e => { e.stopPropagation(); setConfirming(null) }}>Cancel</button>
          </div>
        </div>
      ) : (
        <button type="button" className="contest-chip" style={BTN} aria-label={`Cash out ${fmtMoney(c.cents)}`} onClick={e => { e.stopPropagation(); setConfirming(row.key) }}>Cash Out {fmtMoney(c.cents)}</button>
      )}
      <div className="mono" style={{ fontSize: 12, marginTop: 4, color: delta >= 0 ? '#57f287' : '#9ca3af' }}>{fmtMoney(delta, { sign: true })} vs risk</div>
    </div>
  )
}

export function MyBets({ bets, games, rules, now, onCashOut, onRebet }: {
  bets: PlacedBet[]; games: BoardGame[]; rules: ContestRules; now: number
  /** Put a settled bet's picks back on the slip at today's prices. */
  onRebet?: (bet: PlacedBet) => void
  /** Sample mode returns nothing. Live mode returns a message when the server refused (the value moved, the game closed). */
  onCashOut: (offers: { id: string; cents: number }[]) => void | string | null | Promise<void | string | null>
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'open' | 'settled'>('all')
  const { format } = useOddsFormat()
  const byId = new Map(games.map(g => [g.id, g]))
  const all = fold(bets)
  const rows = all.filter(r => filter === 'all' || (filter === 'open') === (r.status === 'pending'))
  /** A bet can be placed again while every leg's game is still open and its market is still on the board. */
  const rebettable = (b: PlacedBet) => !b.groupId && b.kind !== 'round_robin' && b.legs.every(l => { const g = byId.get(l.gameId); return !!g && !gameClosed(g, now, rules).closed && makePick(g, l.market, l.side) !== null })
  if (all.length === 0) return <p style={{ ...WRAP, padding: 20, fontSize: 13, color: '#7d8596', margin: 0 }}>No bets yet. Your picks are always visible to you and hidden from everyone else until kickoff.</p>
  const confirm = async (row: Row, offers: { id: string; cents: number }[], cents: number, stake: number) => {
    const r = onCashOut(offers)
    const refused = r instanceof Promise ? await r : r                          // sample mode answers at once: the confirm closes in the same click
    setConfirming(null)
    if (refused) { setNotice(refused); return }
    setNotice(`Cashed out ${row.label} for ${fmtMoney(cents)} (${fmtMoney(cents - stake, { sign: true })} vs risk).`)
  }
  const paid = (r: Row) => r.bets.filter(b => b.status === 'cashed_out').reduce((a, b) => a + (b.payoutCents ?? 0), 0)
  return (
    <div style={WRAP}>
      <div role="group" aria-label="Filter bets" className="flex gap-2" style={{ padding: '8px 10px', borderBottom: '1px solid #1e2230' }}>
        {(['all', 'open', 'settled'] as const).map(f => {
          const n = all.filter(r => f === 'all' || (f === 'open') === (r.status === 'pending')).length
          return <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)} className="mono contest-chip" style={{ fontSize: 12, minHeight: 44, minWidth: 44, padding: '0 10px', borderRadius: 6, border: `1px solid ${filter === f ? 'var(--signal)' : '#1e2230'}`, background: filter === f ? 'var(--win-wash)' : '#171a24', color: filter === f ? 'var(--signal)' : '#f3f4f6' }}>{f === 'all' ? 'All' : f === 'open' ? 'Open' : 'Settled'} {n}</button>
        })}
      </div>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="My Bets">
        <thead><tr><th style={TH}>Bet</th><th style={TH}>Selections</th><th style={{ ...TH, textAlign: 'right' }}>Risk</th><th style={{ ...TH, textAlign: 'right' }}>To win</th><th style={TH}>Status</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <Fragment key={r.key}>
              <tr className="contest-row" onClick={r.children ? () => setOpen(open === r.key ? null : r.key) : undefined} style={{ cursor: r.children ? 'pointer' : 'default' }}>
                <td style={{ ...TD, whiteSpace: 'nowrap' }}>{r.label}{r.children && <span className="mono" style={{ color: '#7d8596', marginLeft: 6 }}>{open === r.key ? '-' : '+'}</span>}</td>
                <td style={TD}>{r.legs.map((l, i) => <div key={i} className="mono" style={{ fontSize: 12, color: '#d1d5db' }}>{legText(l, byId, format)}</div>)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right' }}>{fmtMoney(r.stake)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right', color: r.status === 'cashed_out' ? '#7d8596' : '#57f287' }}>{r.status === 'cashed_out' ? '--' : fmtMoney(r.toWin)}</td>
                <td style={TD}>
                  <StatusText s={r.status} label={r.statusLabel} />
                  {paid(r) > 0 && <div className="mono" style={{ fontSize: 12, marginTop: 4, color: paid(r) >= r.stake ? '#57f287' : '#9ca3af' }}>{fmtMoney(paid(r))} ({fmtMoney(paid(r) - r.stake, { sign: true })})</div>}
                  {r.status !== 'pending' && onRebet && r.bets.length === 1 && !r.bets[0].groupId && (rebettable(r.bets[0])
                    ? <button type="button" onClick={e => { e.stopPropagation(); onRebet(r.bets[0]) }} className="contest-chip" style={{ ...BTN, marginTop: 6 }}>Bet Again</button>
                    : <div className="mono" style={{ fontSize: 12, color: '#7d8596', marginTop: 6 }}>Games closed</div>)}
                  {r.status === 'pending' && <CashoutControl row={r} rules={rules} byId={byId} now={now} confirming={confirming === r.key} setConfirming={setConfirming} onConfirm={confirm} />}
                </td>
              </tr>
              {r.children && open === r.key && (
                <tr><td colSpan={5} style={{ ...TD, background: '#0e1017', padding: '8px 10px 8px 24px' }}>
                  {r.children.map(k => (
                    <div key={k.id} className="mono flex flex-wrap gap-x-4" style={{ fontSize: 12, color: '#9ca3af', padding: '2px 0' }}>
                      <span style={{ flex: '1 1 260px' }}>{k.legs.map(l => legText(l, byId, format)).join('  +  ')}</span>
                      <span>{fmtMoney(k.stakeCents)}</span><StatusText s={k.status} />
                    </div>
                  ))}
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {notice && <p role="status" className="mono" style={{ margin: 0, padding: '8px 12px', fontSize: 12, color: '#9ca3af', borderTop: '1px solid #151822' }}>{notice}</p>}
    </div>
  )
}

function PoolLegs({ legs, byId }: { legs: PoolBet['legs']; byId: Map<number, BoardGame> }) {
  const { format } = useOddsFormat()
  const shown = legs.filter((l): l is NonNullable<typeof l> => l !== null)
  const hidden = legs.length - shown.length
  return (
    <>
      {shown.map((l, i) => <div key={i} className="mono" style={{ fontSize: 12, color: '#d1d5db' }}>{legText(l, byId, format)}</div>)}
      {hidden > 0 && (
        <div className="mono flex items-center gap-1.5" style={{ fontSize: 12, color: '#7d8596' }}>
          <LockGlyph size={12} color="#7d8596" />{shown.length > 0 ? `${hidden} more ` : `${hidden} `}{hidden === 1 ? 'leg' : 'legs'} hidden until kickoff
        </div>
      )}
    </>
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
              <td style={TD}><PoolLegs legs={b.legs} byId={byId} /></td>
              <td className="mono" style={{ ...TD, textAlign: 'right', color: b.stakeCents == null ? '#7d8596' : undefined }}>{b.stakeCents == null ? 'Hidden' : fmtMoney(b.stakeCents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mono" style={{ margin: 0, padding: '8px 12px', fontSize: 12, color: '#7d8596', borderTop: '1px solid #151822' }}>Open bets from every player. A pick appears when its game kicks off.</p>
    </div>
  )
}
