'use client'
/** Bet confirmation, shown in the slip's place right after a bet goes in: what was placed, what is at risk, what it can pay, and
 *  the receipt number(s). A round robin shows once (one slip) with its parlay count. Plain copy: no emoji, no exclamation points. */
import { fmtMoney } from '@/lib/contest/odds'
import type { BoardGame, Receipt } from '@/lib/contest/types'
import { KIND_LABEL, legText } from './bets-table'
import { useOddsFormat } from './odds-format'

const dt = (iso: string) => new Date(iso).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })

export function BetReceipt({ receipt, games, onDone, onViewBets }: { receipt: Receipt; games: BoardGame[]; onDone: () => void; onViewBets: () => void }) {
  const { format } = useOddsFormat()
  const byId = new Map(games.map(g => [g.id, g]))
  const n = receipt.bets.length
  const rr = receipt.kind === 'round_robin'
  const title = rr ? `Round Robin \u00b7 ${n} parlays` : receipt.kind === 'straight' && n > 1 ? `Straight \u00b7 ${n} bets` : KIND_LABEL[receipt.kind]
  // a round robin lists each pick once; every other kind lists the legs of each bet
  const lines = rr
    ? [...new Map(receipt.bets.flatMap(b => b.legs).map(l => [`${l.gameId}${l.market}${l.side}`, l])).values()]
    : receipt.bets.flatMap(b => b.legs)
  // one number for the whole slip (a round robin's parlays share it), never a per-bet id like 1-0 that reads like a score
  const slipNo = receipt.bets[0].id.replace(/^s(\d+).*$/, '$1')
  return (
    <section aria-label="Bet confirmation" role="status" style={{ padding: 14 }}>
      <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0, color: 'var(--signal)' }}>Bet placed</h2>
      <p className="mono" style={{ margin: '4px 0 12px', fontSize: 11, color: '#7d8596' }}>{dt(receipt.placedAt)} ET &middot; Slip #{slipNo} &middot; Play money</p>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{title}</div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, borderTop: '1px solid #151822' }}>
        {lines.map((l, i) => <li key={i} className="mono" style={{ fontSize: 12, color: '#d1d5db', padding: '6px 0', borderBottom: '1px solid #151822' }}>{legText(l, byId, format)}</li>)}
      </ul>
      <dl className="mono" style={{ margin: '14px 0 0', display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 6, fontSize: 13 }}>
        <dt style={{ color: '#7d8596' }}>Risk</dt><dd style={{ margin: 0, textAlign: 'right' }}>{fmtMoney(receipt.riskCents)}</dd>
        <dt style={{ color: '#7d8596' }}>{rr ? 'To win (all picks)' : 'To win'}</dt><dd style={{ margin: 0, textAlign: 'right', color: 'var(--signal)' }}>{fmtMoney(receipt.payoutCents - receipt.riskCents)}</dd>
        <dt style={{ color: '#7d8596' }}>Payout</dt><dd style={{ margin: 0, textAlign: 'right' }}>{fmtMoney(receipt.payoutCents)}</dd>
      </dl>
      <p style={{ fontSize: 12, color: '#7d8596', margin: '12px 0 0' }}>Your picks stay hidden from other players until each game kicks off.</p>
      <div className="flex gap-2" style={{ marginTop: 14 }}>
        <button type="button" onClick={onViewBets} className="btn-primary" style={{ flex: 1, height: 40, fontSize: 13, fontWeight: 600 }}>View My Bets</button>
        <button type="button" onClick={onDone} className="btn-ghost" style={{ flex: 1, height: 40, fontSize: 13, fontWeight: 600 }}>Keep Betting</button>
      </div>
    </section>
  )
}
