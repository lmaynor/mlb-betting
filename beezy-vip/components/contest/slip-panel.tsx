'use client'
/**
 * The bet slip: four modes (Straight / Parlay / Teaser / Round Robin), the picks with their prices, a stake box with quick
 * amounts, and a live Risk / To win / Payout line. The numbers come from lib/contest/slip.ts (a mirror of the engine) and the
 * server re-validates on placement. A flat panel: no glow, no gradient.
 */
import { useState } from 'react'
import { stepLine } from '@/lib/contest/margin'
import { sgpPrice } from '@/lib/contest/sgp'
import { fmtLineSigned, fmtMoney, parseDollars } from '@/lib/contest/odds'
import { useFmtPrice } from './odds-format'
import {
  makePick, previewParlay, previewRobin, previewSgp, previewStraight, previewTeaser, robinBreakdown, robinCount, slipIssue, teaserPointOptions, teasedLine, type Preview, type SlipState,
} from '@/lib/contest/slip'
import type { BetKind, BoardGame, ContestRules, Pick, Receipt } from '@/lib/contest/types'
import { BetReceipt } from './receipt'

const KINDS: { kind: BetKind; label: string }[] = [
  { kind: 'straight', label: 'Straight' }, { kind: 'parlay', label: 'Parlay' }, { kind: 'teaser', label: 'Teaser' }, { kind: 'round_robin', label: 'Round Robin' }, { kind: 'sgp', label: 'SGP' },
]
// quick amounts ADD to the stake (+$25 twice is $50); a round robin is staked per parlay, so its steps are smaller
const QUICK = [2_500, 5_000, 10_000, 25_000]
const QUICK_ROBIN = [500, 1_000, 2_500, 5_000]
const toInput = (cents: number) => (cents / 100).toFixed(2).replace(/\.?0+$/, '')
const PANEL: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8 }

function describe(p: Pick, g: BoardGame | undefined, points = 0): { who: string; what: string } {
  const team = g ? (p.side === 'home' ? g.home.name : g.away.name) : '?'
  const line = points ? teasedLine(p, points) : p.line
  if (p.market === 'total') return { who: `${p.side === 'over' ? 'Over' : 'Under'} ${line}`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
  if (p.market === 'spread') return { who: `${team} ${fmtLineSigned(line)}`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
  return { who: `${team} to win`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
}

/** Alt line control for one pick: step the number half a point either way (priced by the contest from the main price) or go back to the main line. */
function AltLine({ pick, game, rules, onLine }: { pick: Pick; game: BoardGame; rules: ContestRules; onLine: (from: Pick, to: Pick) => void }) {
  const main = makePick(game, pick.market, pick.side)
  const down = stepLine(game, pick, -1, rules)
  const up = stepLine(game, pick, 1, rules)
  const isAlt = main != null && main.line !== pick.line
  const btn: React.CSSProperties = { minWidth: 44, minHeight: 44, fontSize: 16, borderRadius: 6, border: '1px solid #1e2230', background: '#171a24', color: '#f3f4f6' }
  const word = pick.market === 'total' ? 'total' : 'spread'
  return (
    <div className="flex items-center gap-2" role="group" aria-label={`Alt line for ${word}`}>
      <button type="button" aria-label={`Lower the ${word} to ${down?.line ?? ''}`} disabled={!down} onClick={() => down && onLine(pick, down)} className="mono contest-chip" style={{ ...btn, opacity: down ? 1 : 0.4 }}>&minus;</button>
      <button type="button" aria-label={`Raise the ${word} to ${up?.line ?? ''}`} disabled={!up} onClick={() => up && onLine(pick, up)} className="mono contest-chip" style={{ ...btn, opacity: up ? 1 : 0.4 }}>+</button>
      <span className="mono" style={{ fontSize: 11, color: '#7d8596' }}>{isAlt ? 'Alt line' : 'Main line'} (half-point steps)</span>
      {isAlt && main && <button type="button" onClick={() => onLine(pick, main)} className="mono contest-chip" style={{ marginLeft: 'auto', fontSize: 12, color: '#9ca3af', minHeight: 44, padding: '0 8px' }}>Main</button>}
    </div>
  )
}

export function SlipPanel({ slip, games, rules, balanceCents, receipt, onDoneReceipt, onViewBets, onKind, onPoints, onSizes, onRemove, onClear, onPlace, onLine, notice, busy }: {
  slip: SlipState; games: BoardGame[]; rules: ContestRules; balanceCents: number
  receipt: Receipt | null; onDoneReceipt: () => void; onViewBets: () => void
  onKind: (k: BetKind) => void; onPoints: (p: number) => void; onSizes: (s: number[]) => void
  onRemove: (p: Pick) => void; onClear: () => void; onLine: (from: Pick, to: Pick) => void
  /** Sample mode returns nothing (the stake box clears at once). Live mode returns whether the server accepted the slip: a refusal keeps the stake. */
  onPlace: (stakeCents: number) => void | Promise<boolean>
  /** A server message to show above the Place button (a price moved, a game closed). */
  notice?: string | null
  busy?: boolean
}) {
  const [raw, setRaw] = useState('')
  const fmtPrice = useFmtPrice()
  const byId = new Map(games.map(g => [g.id, g]))
  const stake = parseDollars(raw)
  const n = slip.picks.length
  const issue = stake == null ? (n === 0 ? 'Add a pick' : 'Enter a stake') : slipIssue(slip, stake, balanceCents, rules, byId.get(slip.picks[0]?.gameId), new Set(games.filter(g => g.halftime?.open).map(g => g.id)))
  const sizeOptions = Array.from({ length: Math.max(0, Math.min(n, rules.roundRobinMaxPicks) - 1) }, (_, i) => i + 2)
  const breakdown = robinBreakdown(n, slip.robinSizes)
  const quick = slip.kind === 'round_robin' ? QUICK_ROBIN : QUICK
  const addStake = (c: number) => setRaw(toInput((stake ?? 0) + c))

  let preview: Preview | null = null
  let bets = 1
  if (stake != null && n > 0) {
    if (slip.kind === 'straight') { bets = n; preview = n === 1 ? previewStraight(slip.picks[0], stake) : { riskCents: stake * n, payoutCents: slip.picks.reduce((s, p) => s + previewStraight(p, stake).payoutCents, 0), toWinCents: 0 } }
    else if (slip.kind === 'parlay') preview = previewParlay(slip.picks, stake)
    else if (slip.kind === 'teaser') preview = previewTeaser(slip.picks, stake, slip.teaserPoints, rules)
    else if (slip.kind === 'sgp') preview = previewSgp(slip.picks, byId.get(slip.picks[0].gameId), stake, rules)
    else { bets = robinCount(n, slip.robinSizes); preview = previewRobin(slip.picks, slip.robinSizes, stake) }
    if (preview && preview.toWinCents === 0) preview = { ...preview, toWinCents: preview.payoutCents - preview.riskCents }
  }
  const ready = !issue && stake != null && preview != null && !busy

  if (receipt) return <section aria-label="Bet Slip" style={PANEL}><BetReceipt receipt={receipt} games={games} onDone={onDoneReceipt} onViewBets={onViewBets} /></section>

  return (
    <section aria-label="Bet Slip" style={PANEL}>
      <div className="flex items-center justify-between" style={{ padding: '10px 12px', borderBottom: '1px solid #1e2230' }}>
        <div className="flex items-center gap-2">
          <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Bet Slip <span className="mono" style={{ color: '#7d8596', fontWeight: 500 }}>{n}</span></h2>
          <span className="mono" title="Contest bets use play money. Nothing is wagered." style={{ fontSize: 11, color: '#9ca3af', border: '1px solid #2e3446', borderRadius: 4, padding: '1px 6px' }}>Play money</span>
        </div>
        {n > 0 && <button type="button" onClick={() => { onClear(); setRaw('') }} style={{ fontSize: 12, color: '#7d8596', minWidth: 44, minHeight: 44 }}>Clear</button>}
      </div>
      <div role="tablist" aria-label="Bet type" className="flex" style={{ borderBottom: '1px solid #1e2230' }}>
        {KINDS.map(k => (
          <button key={k.kind} role="tab" type="button" aria-selected={slip.kind === k.kind} onClick={() => onKind(k.kind)} className="contest-tab"
            style={{ flex: '1 1 auto', padding: '9px 6px', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', color: slip.kind === k.kind ? '#f3f4f6' : '#7d8596',
              borderBottom: `2px solid ${slip.kind === k.kind ? 'var(--signal)' : 'transparent'}`, marginBottom: -1 }}>{k.label}</button>
        ))}
      </div>

      {n === 0 ? (
        <p style={{ padding: '24px 14px', fontSize: 13, color: '#7d8596', margin: 0, textAlign: 'center' }}>Select a price on the board to start a slip.</p>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {slip.picks.map(p => {
            const g = byId.get(p.gameId)
            const d = describe(p, g, slip.kind === 'teaser' ? slip.teaserPoints : 0)
            const canAlt = !!g && !g.halftime?.open && rules.altLinesEnabled && slip.kind !== 'teaser' && slip.kind !== 'sgp' && (p.market === 'spread' || p.market === 'total')
            return (
              <li key={`${p.gameId}-${p.market}-${p.side}`} style={{ padding: '9px 12px', borderBottom: '1px solid #151822' }}>
               <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div style={{ fontSize: 13, fontWeight: 600 }} className="truncate">{d.who}</div>
                  <div className="mono" style={{ fontSize: 11, color: '#7d8596' }}>{d.what}{slip.kind === 'teaser' && p.market !== 'ml' ? `  \u00b7  was ${p.market === 'total' ? p.line : fmtLineSigned(p.line)}` : ''}</div>
                </div>
                <div className="flex items-center gap-3 flex-none">
                  <span className="mono" style={{ fontSize: 13, color: slip.kind === 'teaser' || slip.kind === 'sgp' ? '#7d8596' : '#f3f4f6' }}>{slip.kind === 'teaser' ? '' : fmtPrice(p.price)}</span>
                  <button type="button" aria-label={`Remove ${d.who}`} onClick={() => onRemove(p)} style={{ fontSize: 16, lineHeight: 1, color: '#7d8596', minWidth: 44, minHeight: 44 }}>&times;</button>
                </div>
               </div>
               {canAlt && g && <AltLine pick={p} game={g} rules={rules} onLine={onLine} />}
              </li>
            )
          })}
        </ul>
      )}

      {n > 0 && slip.kind === 'teaser' && (
        <div className="flex items-center gap-2" style={{ padding: '10px 12px', borderBottom: '1px solid #151822' }}>
          <span style={{ fontSize: 12, color: '#7d8596' }}>Points</span>
          {teaserPointOptions(rules).map(pt => (
            <button key={pt} type="button" aria-pressed={slip.teaserPoints === pt} onClick={() => onPoints(pt)} className="mono contest-chip"
              style={{ fontSize: 12, padding: '4px 9px', minWidth: 44, borderRadius: 6, border: `1px solid ${slip.teaserPoints === pt ? 'var(--signal)' : '#1e2230'}`, background: slip.teaserPoints === pt ? 'var(--win-wash)' : '#171a24', color: slip.teaserPoints === pt ? 'var(--signal)' : '#f3f4f6' }}>{pt}</button>
          ))}
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: '#9ca3af' }} title="Teaser price for this many legs">{fmtPrice(rules.teaser[slip.teaserPoints.toFixed(1)]?.[String(n)] ?? null)}</span>
        </div>
      )}
      {slip.kind === 'sgp' && (
        <div style={{ padding: '10px 12px', borderBottom: '1px solid #151822' }}>
          <p style={{ fontSize: 12, color: '#7d8596', margin: 0 }}>
            {n < 2 ? 'Pick a spread or the moneyline, and the total, from one game. The two results move together, so the ticket is priced as a pair.' : 'Priced as a pair, not the product of the two lines. Main lines only. Not offered for cash out.'}
          </p>
          {n === 2 && (
            <div className="flex items-center justify-between mono" style={{ marginTop: 8, fontSize: 13 }}>
              <span style={{ color: '#7d8596' }}>Ticket price</span>
              <b data-testid="sgp-price">{sgpPrice(slip.picks, byId.get(slip.picks[0].gameId), rules) == null ? '--' : fmtPrice(sgpPrice(slip.picks, byId.get(slip.picks[0].gameId), rules))}</b>
            </div>
          )}
        </div>
      )}
      {n > 0 && slip.kind === 'teaser' && <p style={{ fontSize: 12, color: '#7d8596', margin: 0, padding: '8px 12px', borderBottom: '1px solid #151822' }}>Moves every line the chosen points in your favor. Pays from a fixed table, not the listed prices.</p>}

      {n > 0 && slip.kind === 'round_robin' && (
        <div style={{ padding: '10px 12px', borderBottom: '1px solid #151822' }}>
          <div style={{ fontSize: 12, color: '#7d8596', marginBottom: 8 }}>
            {sizeOptions.length === 0 ? 'Add at least 3 picks.' : `Each size bets every combination of that many of your ${n} picks as its own parlay.`}
          </div>
          <div role="group" aria-label="Parlay sizes" className="flex flex-wrap gap-2">
            {sizeOptions.map(k => {
              const on = slip.robinSizes.includes(k)
              const count = robinBreakdown(n, [k])[0].count
              return (
                <button key={k} type="button" aria-pressed={on} aria-label={`By ${k}s, ${count} ${count === 1 ? 'bet' : 'bets'}`}
                  onClick={() => onSizes(on ? slip.robinSizes.filter(x => x !== k) : [...slip.robinSizes, k].sort((a, b) => a - b))} className="mono contest-chip"
                  style={{ minWidth: 64, padding: '5px 9px', borderRadius: 6, lineHeight: 1.25, textAlign: 'center', border: `1px solid ${on ? 'var(--signal)' : '#1e2230'}`, background: on ? 'var(--win-wash)' : '#171a24', color: on ? 'var(--signal)' : '#f3f4f6' }}>
                  <span style={{ display: 'block', fontSize: 12, fontWeight: 600 }}>By {k}s</span>
                  <span style={{ display: 'block', fontSize: 10, color: on ? 'var(--signal)' : '#7d8596' }}>{count} {count === 1 ? 'bet' : 'bets'}</span>
                </button>
              )
            })}
          </div>
          {breakdown.length > 0 && (
            <p className="mono" data-testid="robin-breakdown" style={{ margin: '10px 0 0', fontSize: 11, color: '#9ca3af', lineHeight: 1.5 }}>
              {breakdown.map(b => `By ${b.k}s = ${b.count} ${b.count === 1 ? 'parlay' : 'parlays'}`).join('  +  ')}
              {breakdown.length > 1 ? `  =  ${breakdown.reduce((t, b) => t + b.count, 0)} parlays in all` : ''}
            </p>
          )}
        </div>
      )}

      {n > 0 && (
        <div style={{ padding: '12px' }}>
          <label htmlFor="contest-stake" style={{ fontSize: 12, color: '#7d8596', display: 'block', marginBottom: 6 }}>
            {slip.kind === 'round_robin' ? 'Stake per parlay' : slip.kind === 'straight' && n > 1 ? 'Stake per bet' : 'Stake'}
          </label>
          <div className="flex items-center gap-2">
            <input id="contest-stake" inputMode="decimal" autoComplete="off" value={raw} onChange={e => setRaw(e.target.value)} placeholder="0.00" className="mono"
              style={{ flex: 1, minWidth: 0, height: 38, padding: '0 10px', fontSize: 16, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }} />
          </div>
          <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
            {quick.map(c => (
              <button key={c} type="button" aria-label={`Add $${c / 100} to stake`} onClick={() => addStake(c)} className="mono contest-chip"
                style={{ fontSize: 12, padding: '4px 9px', minWidth: 44, borderRadius: 6, border: '1px solid #1e2230', background: '#171a24', color: '#f3f4f6' }}>+${c / 100}</button>
            ))}
            {raw !== '' && <button type="button" onClick={() => setRaw('')} className="mono contest-chip"
              style={{ fontSize: 12, padding: '4px 9px', minWidth: 44, borderRadius: 6, border: '1px solid #1e2230', background: 'transparent', color: '#7d8596' }}>Reset</button>}
          </div>

          <dl className="mono" style={{ margin: '14px 0 0', display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 6, fontSize: 13 }}>
            {(slip.kind === 'round_robin' || (slip.kind === 'straight' && n > 1)) && (<><dt style={{ color: '#7d8596' }}>{slip.kind === 'round_robin' ? 'Parlays' : 'Bets'}</dt><dd style={{ margin: 0, textAlign: 'right' }}>{bets}</dd></>)}
            <dt style={{ color: '#7d8596' }}>{slip.kind === 'round_robin' ? 'Total risk' : 'Risk'}</dt><dd style={{ margin: 0, textAlign: 'right' }}>{preview ? fmtMoney(preview.riskCents) : '--'}</dd>
            <dt style={{ color: '#7d8596' }}>{slip.kind === 'round_robin' ? 'To win (all picks)' : 'To win'}</dt><dd style={{ margin: 0, textAlign: 'right', color: preview ? 'var(--signal)' : undefined }}>{preview ? fmtMoney(preview.toWinCents) : '--'}</dd>
            <dt style={{ color: '#7d8596' }}>Payout</dt><dd style={{ margin: 0, textAlign: 'right' }}>{preview ? fmtMoney(preview.payoutCents) : '--'}</dd>
          </dl>

          <button type="button" disabled={!ready} onClick={() => { if (!ready || stake == null) return; const r = onPlace(stake); if (r instanceof Promise) { void r.then(ok => { if (ok) setRaw('') }) } else setRaw('') }} className="btn-primary"
            style={{ width: '100%', height: 42, marginTop: 14, fontSize: 14, fontWeight: 600, opacity: ready ? 1 : 0.45, cursor: ready ? 'pointer' : 'not-allowed' }}>
            {busy ? 'Placing' : ready ? `Place Bet \u00b7 ${fmtMoney(preview!.riskCents)}` : 'Place Bet'}
          </button>
          <p role="status" className="mono" style={{ fontSize: 12, color: notice || !ready ? '#facc15' : '#7d8596', margin: '8px 0 0', minHeight: 16 }}>{notice ?? (ready ? 'Picks stay hidden from others until kickoff.' : issue)}</p>
        </div>
      )}
    </section>
  )
}
