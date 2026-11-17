'use client'
/**
 * The hidden lifeline: a blackjack table that exists only while a player's bankroll is under the threshold ($100). It is a rescue, not an
 * edge: winnings can never lift the bankroll above the threshold, and a rescue ends the moment the player is back there.
 *
 * SAMPLE MODE: the page deals and settles the hand itself (lib/contest/blackjack.ts, a mirror of contest/blackjack.py). The real table
 * runs on the server (contest/lifeline.py), which keeps the deck and the dealer's hole card; this component only ever shows
 * `viewOf(hand)`, so the same screen works against either.
 */
import { useState } from 'react'
import {
  canDouble, cardLabel, creditedAfterCap, deal, doubleDown, hit, newDeck, stand, totalStake, viewOf, type Hand, type Outcome,
} from '@/lib/contest/blackjack'
import { fmtMoney, parseDollars } from '@/lib/contest/odds'

const PANEL: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, padding: 16, maxWidth: 520 }
const SUIT: Record<string, React.ReactNode> = { S: <>&spades;</>, H: <>&hearts;</>, D: <>&diams;</>, C: <>&clubs;</> }
const CHIPS = [100, 500, 1_000, 2_500]
const RESULT: Record<Outcome, string> = { blackjack: 'Blackjack. Paid 3 to 2.', win: 'You win.', push: 'Push. Your stake comes back.', lose: 'Dealer wins.', bust: 'Bust.' }

function Card({ c }: { c: string }) {
  const hidden = c === '??'
  const red = c[1] === 'H' || c[1] === 'D'
  return (
    <span role="img" aria-label={cardLabel(c)} className="mono inline-flex flex-col items-center justify-center" style={{
      width: 44, height: 62, borderRadius: 6, fontSize: 16, fontWeight: 700, lineHeight: 1.1,
      background: hidden ? '#1b1f2b' : '#f3f4f6', border: `1px solid ${hidden ? '#2e3446' : '#d1d5db'}`, color: hidden ? '#4b5469' : red ? '#dc2626' : '#11131a',
    }}>
      {hidden ? '?' : <><span>{c[0] === 'T' ? '10' : c[0]}</span><span aria-hidden="true">{SUIT[c[1]]}</span></>}
    </span>
  )
}

const Row = ({ label, total, cards }: { label: string; total: number | null; cards: string[] }) => (
  <div style={{ marginBottom: 12 }}>
    <div className="mono" style={{ fontSize: 12, color: '#7d8596', marginBottom: 6 }}>{label}{total != null && <span style={{ color: '#f3f4f6' }}> {total}</span>}</div>
    <div className="flex gap-2 flex-wrap" style={{ minHeight: 62 }}>{cards.map((c, i) => <Card key={`${c}${i}`} c={c} />)}</div>
  </div>
)

export function BlackjackTable({ balanceCents, bankrollCents, thresholdCents, minChipCents, onMoney }: {
  balanceCents: number; bankrollCents: number; thresholdCents: number; minChipCents: number
  /** Signed change to the player's balance: the stake going in (negative), a double's second stake, the credited payout (positive). */
  onMoney: (deltaCents: number) => void
}) {
  const [hand, setHand] = useState<Hand | null>(null)
  const [raw, setRaw] = useState('')
  const [before, setBefore] = useState(0)                  // the bankroll when the hand was dealt (the cap is measured from it)
  const [note, setNote] = useState<string | null>(null)
  const open = hand != null && !hand.done
  const stake = parseDollars(raw)
  const maxWager = Math.min(balanceCents, thresholdCents)
  const stakeIssue = stake == null ? 'Enter a wager' : stake < minChipCents ? `Minimum wager is ${fmtMoney(minChipCents)}` : stake > maxWager ? `Maximum wager is ${fmtMoney(maxWager)}` : null
  const view = hand ? viewOf(hand) : null
  const closed = bankrollCents >= thresholdCents && !open

  const settle = (h: Hand, bankrollBefore: number) => {
    const r = creditedAfterCap(h, bankrollBefore, thresholdCents)
    if (r.credited > 0) onMoney(r.credited)
    const base = RESULT[h.outcome!]
    setNote(r.capped ? `${base} Winnings stop at a ${fmtMoney(thresholdCents, { whole: true })} bankroll, so ${fmtMoney(r.credited)} of ${fmtMoney(r.payout)} was paid.` : r.payout > 0 ? `${base} ${fmtMoney(r.payout)} returned.` : base)
  }
  const start = () => {
    if (stake == null || stakeIssue) return
    const h = deal(stake, newDeck())                    // a fresh shuffled deck for every hand
    onMoney(-stake)
    setBefore(bankrollCents)
    setHand(h)
    setRaw('')
    if (h.done) settle(h, bankrollCents); else setNote(null)
  }
  const act = (kind: 'hit' | 'stand' | 'double') => {
    if (!hand || hand.done) return
    if (kind === 'double') {
      if (!canDouble(hand) || balanceCents < hand.stake) { setNote('You cannot double that.'); return }
      onMoney(-hand.stake)
    }
    const next: Hand = { ...hand, deck: [...hand.deck], player: [...hand.player], dealer: [...hand.dealer] }
    if (kind === 'hit') hit(next); else if (kind === 'stand') stand(next); else doubleDown(next)
    setHand(next)
    if (next.done) settle(next, before)
  }

  return (
    <section aria-label="Blackjack table" style={PANEL}>
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 2px' }}>Blackjack</h2>
      <p style={{ fontSize: 12, color: '#7d8596', margin: '0 0 14px' }}>A seat opens when your bankroll drops under {fmtMoney(thresholdCents, { whole: true })}. Play money. Winnings stop at {fmtMoney(thresholdCents, { whole: true })}.</p>

      {view && <><Row label="Dealer" total={view.dealer.includes('??') ? view.dealerTotal : view.dealerTotal} cards={view.dealer} /><Row label={`You${view.doubled ? ' (doubled)' : ''}`} total={view.playerTotal} cards={view.player} /></>}
      {!view && <p style={{ fontSize: 13, color: '#9ca3af', margin: '0 0 14px' }}>Place a wager to be dealt in.</p>}

      {open ? (
        <div className="flex gap-2" role="group" aria-label="Your move">
          <button type="button" onClick={() => act('hit')} className="btn-primary" style={{ flex: 1, minHeight: 44, fontSize: 13, fontWeight: 600 }}>Hit</button>
          <button type="button" onClick={() => act('stand')} className="btn-ghost" style={{ flex: 1, minHeight: 44, fontSize: 13, fontWeight: 600 }}>Stand</button>
          <button type="button" onClick={() => act('double')} disabled={!hand || !canDouble(hand) || balanceCents < hand.stake} className="btn-ghost"
            style={{ flex: 1, minHeight: 44, fontSize: 13, fontWeight: 600, opacity: hand && canDouble(hand) && balanceCents >= hand.stake ? 1 : 0.45 }}>Double</button>
        </div>
      ) : closed ? (
        <p role="status" style={{ fontSize: 13, color: '#9ca3af', margin: 0 }}>The table is closed. You are back at {fmtMoney(thresholdCents, { whole: true })}.</p>
      ) : (
        <div>
          <label htmlFor="bj-wager" style={{ fontSize: 12, color: '#7d8596', display: 'block', marginBottom: 6 }}>Wager</label>
          <input id="bj-wager" inputMode="decimal" autoComplete="off" value={raw} onChange={e => setRaw(e.target.value)} placeholder="0.00" className="mono"
            style={{ width: '100%', height: 44, padding: '0 12px', fontSize: 16, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }} />
          <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
            {CHIPS.map(c => (
              <button key={c} type="button" aria-label={`Add ${fmtMoney(c)} to wager`} onClick={() => setRaw(((parseDollars(raw) ?? 0) + c) / 100 + '')} className="mono contest-chip"
                style={{ fontSize: 12, padding: '4px 9px', minWidth: 44, minHeight: 44, borderRadius: 6, border: '1px solid #1e2230', background: '#171a24', color: '#f3f4f6' }}>+${c / 100}</button>
            ))}
          </div>
          <button type="button" onClick={start} disabled={!!stakeIssue} className="btn-primary"
            style={{ width: '100%', minHeight: 44, marginTop: 12, fontSize: 14, fontWeight: 600, opacity: stakeIssue ? 0.45 : 1 }}>{hand ? 'Deal again' : 'Deal'}</button>
          <p className="mono" style={{ fontSize: 12, color: stakeIssue && raw ? '#facc15' : '#7d8596', margin: '8px 0 0', minHeight: 16 }}>{raw ? stakeIssue : `Balance ${fmtMoney(balanceCents)}`}</p>
        </div>
      )}

      <p role="status" aria-live="polite" style={{ fontSize: 13, color: '#f3f4f6', margin: '12px 0 0', minHeight: 18 }}>{note}</p>
      <p style={{ fontSize: 12, color: '#7d8596', margin: '8px 0 0' }}>One deck, shuffled every hand. Dealer stands on all 17s. Blackjack pays 3 to 2. Double on your first two cards. No splits. {hand && !hand.done ? `Stake in play ${fmtMoney(totalStake(hand))}.` : ''}</p>
    </section>
  )
}
