'use client'
/**
 * The bet slip: four modes (Straight / Parlay / Teaser / Round robin), the picks with their prices, a stake box with quick
 * amounts, and a live Risk / To win / Payout line. The numbers come from lib/contest/slip.ts (a mirror of the engine) and the
 * server re-validates on placement. A flat panel: no glow, no gradient.
 */
import { useState } from 'react'
import { fmtLineSigned, fmtMoney, fmtPrice, parseDollars } from '@/lib/contest/odds'
import {
  previewParlay, previewRobin, previewStraight, previewTeaser, robinCount, slipIssue, teaserPointOptions, teasedLine, type Preview, type SlipState,
} from '@/lib/contest/slip'
import type { BetKind, BoardGame, ContestRules, Pick } from '@/lib/contest/types'

const KINDS: { kind: BetKind; label: string }[] = [
  { kind: 'straight', label: 'Straight' }, { kind: 'parlay', label: 'Parlay' }, { kind: 'teaser', label: 'Teaser' }, { kind: 'round_robin', label: 'Round robin' },
]
const QUICK = [2_500, 5_000, 10_000, 25_000]
const PANEL: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8 }

function describe(p: Pick, g: BoardGame | undefined, points = 0): { who: string; what: string } {
  const team = g ? (p.side === 'home' ? g.home.name : g.away.name) : '?'
  const line = points ? teasedLine(p, points) : p.line
  if (p.market === 'total') return { who: `${p.side === 'over' ? 'Over' : 'Under'} ${line}`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
  if (p.market === 'spread') return { who: `${team} ${fmtLineSigned(line)}`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
  return { who: `${team} to win`, what: g ? `${g.away.abbr} @ ${g.home.abbr}` : '' }
}

export function SlipPanel({ slip, games, rules, balanceCents, onKind, onPoints, onSizes, onRemove, onClear, onPlace }: {
  slip: SlipState; games: BoardGame[]; rules: ContestRules; balanceCents: number
  onKind: (k: BetKind) => void; onPoints: (p: number) => void; onSizes: (s: number[]) => void
  onRemove: (p: Pick) => void; onClear: () => void
  onPlace: (stakeCents: number) => void
}) {
  const [raw, setRaw] = useState('')
  const byId = new Map(games.map(g => [g.id, g]))
  const stake = parseDollars(raw)
  const n = slip.picks.length
  const issue = stake == null ? (n === 0 ? 'Add a pick' : 'Enter a stake') : slipIssue(slip, stake, balanceCents, rules)
  const sizeOptions = Array.from({ length: Math.max(0, Math.min(n, rules.parlayMaxLegs) - 1) }, (_, i) => i + 2)

  let preview: Preview | null = null
  let bets = 1
  if (stake != null && n > 0) {
    if (slip.kind === 'straight') { bets = n; preview = n === 1 ? previewStraight(slip.picks[0], stake) : { riskCents: stake * n, payoutCents: slip.picks.reduce((s, p) => s + previewStraight(p, stake).payoutCents, 0), toWinCents: 0 } }
    else if (slip.kind === 'parlay') preview = previewParlay(slip.picks, stake)
    else if (slip.kind === 'teaser') preview = previewTeaser(slip.picks, stake, slip.teaserPoints, rules)
    else { bets = robinCount(n, slip.robinSizes); preview = previewRobin(slip.picks, slip.robinSizes, stake) }
    if (preview && preview.toWinCents === 0) preview = { ...preview, toWinCents: preview.payoutCents - preview.riskCents }
  }
  const ready = !issue && stake != null && preview != null

  return (
    <section aria-label="Bet slip" style={PANEL}>
      <div className="flex items-center justify-between" style={{ padding: '10px 12px', borderBottom: '1px solid #1e2230' }}>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Bet slip <span className="mono" style={{ color: '#7d8596', fontWeight: 500 }}>{n}</span></h2>
        {n > 0 && <button type="button" onClick={() => { onClear(); setRaw('') }} style={{ fontSize: 12, color: '#7d8596' }}>Clear</button>}
      </div>
      <div role="tablist" aria-label="Bet type" className="flex" style={{ borderBottom: '1px solid #1e2230' }}>
        {KINDS.map(k => (
          <button key={k.kind} role="tab" type="button" aria-selected={slip.kind === k.kind} onClick={() => onKind(k.kind)} className="contest-tab"
            style={{ flex: 1, padding: '9px 4px', fontSize: 12, fontWeight: 600, color: slip.kind === k.kind ? '#f3f4f6' : '#7d8596',
              borderBottom: `2px solid ${slip.kind === k.kind ? 'var(--signal)' : 'transparent'}`, marginBottom: -1 }}>{k.label}</button>
        ))}
      </div>

      {n === 0 ? (
        <p style={{ padding: '24px 14px', fontSize: 13, color: '#7d8596', margin: 0, textAlign: 'center' }}>Select a price on the board to start a slip.</p>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {slip.picks.map(p => {
            const d = describe(p, byId.get(p.gameId), slip.kind === 'teaser' ? slip.teaserPoints : 0)
            return (
              <li key={`${p.gameId}-${p.market}-${p.side}`} className="flex items-center justify-between gap-2" style={{ padding: '9px 12px', borderBottom: '1px solid #151822' }}>
                <div className="min-w-0">
                  <div style={{ fontSize: 13, fontWeight: 600 }} className="truncate">{d.who}</div>
                  <div className="mono" style={{ fontSize: 11, color: '#7d8596' }}>{d.what}{slip.kind === 'teaser' && p.market !== 'ml' ? `  \u00b7  was ${p.market === 'total' ? p.line : fmtLineSigned(p.line)}` : ''}</div>
                </div>
                <div className="flex items-center gap-3 flex-none">
                  <span className="mono" style={{ fontSize: 13, color: slip.kind === 'teaser' ? '#7d8596' : '#f3f4f6' }}>{slip.kind === 'teaser' ? '' : fmtPrice(p.price)}</span>
                  <button type="button" aria-label={`Remove ${d.who}`} onClick={() => onRemove(p)} style={{ fontSize: 16, lineHeight: 1, color: '#7d8596', padding: 2 }}>&times;</button>
                </div>
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
              style={{ fontSize: 12, padding: '4px 9px', borderRadius: 6, border: `1px solid ${slip.teaserPoints === pt ? 'var(--signal)' : '#1e2230'}`, background: slip.teaserPoints === pt ? 'var(--win-wash)' : '#171a24', color: slip.teaserPoints === pt ? 'var(--signal)' : '#f3f4f6' }}>{pt}</button>
          ))}
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: '#9ca3af' }} title="Teaser price for this many legs">{fmtPrice(rules.teaser[slip.teaserPoints.toFixed(1)]?.[String(n)] ?? null)}</span>
        </div>
      )}

      {n > 0 && slip.kind === 'round_robin' && (
        <div className="flex flex-wrap items-center gap-2" style={{ padding: '10px 12px', borderBottom: '1px solid #151822' }}>
          <span style={{ fontSize: 12, color: '#7d8596' }}>Parlays of</span>
          {sizeOptions.length === 0 && <span style={{ fontSize: 12, color: '#7d8596' }}>add 3+ picks</span>}
          {sizeOptions.map(k => {
            const on = slip.robinSizes.includes(k)
            return (
              <button key={k} type="button" aria-pressed={on} onClick={() => onSizes(on ? slip.robinSizes.filter(x => x !== k) : [...slip.robinSizes, k].sort((a, b) => a - b))} className="mono contest-chip"
                style={{ fontSize: 12, padding: '4px 9px', borderRadius: 6, border: `1px solid ${on ? 'var(--signal)' : '#1e2230'}`, background: on ? 'var(--win-wash)' : '#171a24', color: on ? 'var(--signal)' : '#f3f4f6' }}>by {k}s</button>
            )
          })}
        </div>
      )}

      {n > 0 && (
        <div style={{ padding: '12px' }}>
          <label htmlFor="contest-stake" style={{ fontSize: 12, color: '#7d8596', display: 'block', marginBottom: 6 }}>
            {slip.kind === 'round_robin' ? 'Stake per parlay' : slip.kind === 'straight' && n > 1 ? 'Stake per bet' : 'Stake'}
          </label>
          <div className="flex items-center gap-2">
            <input id="contest-stake" inputMode="decimal" autoComplete="off" value={raw} onChange={e => setRaw(e.target.value)} placeholder="0.00" className="mono"
              style={{ flex: 1, minWidth: 0, height: 38, padding: '0 10px', fontSize: 15, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }} />
          </div>
          <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
            {QUICK.map(c => (
              <button key={c} type="button" onClick={() => setRaw(String(c / 100))} className="mono contest-chip"
                style={{ fontSize: 12, padding: '4px 9px', borderRadius: 6, border: '1px solid #1e2230', background: '#171a24', color: '#f3f4f6' }}>${c / 100}</button>
            ))}
          </div>

          <dl className="mono" style={{ margin: '14px 0 0', display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 6, fontSize: 13 }}>
            {(slip.kind === 'round_robin' || (slip.kind === 'straight' && n > 1)) && (<><dt style={{ color: '#7d8596' }}>{slip.kind === 'round_robin' ? 'Parlays' : 'Bets'}</dt><dd style={{ margin: 0, textAlign: 'right' }}>{bets}</dd></>)}
            <dt style={{ color: '#7d8596' }}>Risk</dt><dd style={{ margin: 0, textAlign: 'right' }}>{preview ? fmtMoney(preview.riskCents) : '--'}</dd>
            <dt style={{ color: '#7d8596' }}>{slip.kind === 'round_robin' ? 'To win (all picks)' : 'To win'}</dt><dd style={{ margin: 0, textAlign: 'right', color: preview ? 'var(--signal)' : undefined }}>{preview ? fmtMoney(preview.toWinCents) : '--'}</dd>
            <dt style={{ color: '#7d8596' }}>Payout</dt><dd style={{ margin: 0, textAlign: 'right' }}>{preview ? fmtMoney(preview.payoutCents) : '--'}</dd>
          </dl>

          <button type="button" disabled={!ready} onClick={() => ready && stake != null && (onPlace(stake), setRaw(''))} className="btn-primary"
            style={{ width: '100%', height: 42, marginTop: 14, fontSize: 14, fontWeight: 600, opacity: ready ? 1 : 0.45, cursor: ready ? 'pointer' : 'not-allowed' }}>
            {ready ? `Place bet \u00b7 ${fmtMoney(preview!.riskCents)}` : 'Place bet'}
          </button>
          <p role="status" className="mono" style={{ fontSize: 11, color: ready ? '#7d8596' : '#facc15', margin: '8px 0 0', minHeight: 14 }}>{ready ? 'Picks stay hidden from others until kickoff.' : issue}</p>
        </div>
      )}
    </section>
  )
}
