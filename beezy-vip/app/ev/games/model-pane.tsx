'use client'

import { useMemo, useState } from 'react'
import { leanOf, modelSideProb, type GameBoardEntry } from '@/lib/game-board'
import { buildLadderRung, mainLineOf, orderRungs, selectRung, windowRungs, type LadderSide } from '@/lib/game-ladder'
import { signedLine, type GameMarket } from '@/lib/game-odds'
import { Seg } from './game-detail'
import { GREEN, SKY } from './game-prices-parts'
import { LadderChart, type ChartRung } from './ladder-chart'

const MARKET_NAME: Record<GameMarket, string> = { ml: 'Moneyline', spread: 'Spread', total: 'Total' }
const pct = (p: number) => `${(p * 100).toFixed(1)}%`
const pts = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)} pts`

interface ModelRow { market: GameMarket; label: string; side: LadderSide; line: number | null; pModel: number; pMarket: number; gap: number }

/** For each market's main line, the side the model likes most relative to the market (the larger gap), with both probabilities. */
function modelRows(g: GameBoardEntry): ModelRow[] {
  const out: ModelRow[] = []
  for (const market of ['ml', 'spread', 'total'] as const) {
    const r = g.main[market]
    if (!r) continue
    let best: ModelRow | null = null
    for (const side of ['a', 'b'] as const) {
      const sp = side === 'a' ? r.a : r.b
      const pModel = modelSideProb(g.model, market, r.row.line, side)
      if (pModel == null || sp.pFair == null) continue
      const label = market === 'ml' ? `${side === 'a' ? g.away : g.home} ML`
        : market === 'total' ? `${side === 'a' ? 'O' : 'U'} ${r.row.line}`
        : side === 'b' ? `${g.home} ${signedLine(r.row.line ?? 0)}` : `${g.away} ${signedLine(-(r.row.line ?? 0))}`
      const row = { market, label, side, line: r.row.line, pModel, pMarket: sp.pFair, gap: pModel - sp.pFair }
      if (!best || row.gap > best.gap) best = row
    }
    if (best) out.push(best)
  }
  return out
}

/**
 * The card's MODEL tab: where our projection disagrees with the market and why. One sentence, the three main lines as model against
 * market fair price, the drivers behind the projection, and one chart of the same comparison across the alternate lines. Every book on
 * every line is not here: that is the "All books" row under the Spread and Total tabs. UNVALIDATED model output, never an edge claim.
 */
export function ModelPane({ game }: { game: GameBoardEntry }) {
  const m = game.model
  const rows = useMemo(() => modelRows(game), [game])
  const lean = useMemo(() => leanOf(game), [game])
  const chartMarkets = (['spread', 'total'] as const).filter(k => rows.some(r => r.market === k) && game.rows[k].length > 1)
  const [market, setMarket] = useState<'spread' | 'total'>(chartMarkets[0] ?? 'spread')
  const [picked, setPicked] = useState<Record<string, number | null>>({})

  if (!m || rows.length === 0) {
    return <p className="m-0 text-compact text-text-secondary" data-testid="model-empty">There is no model projection for this game yet. Prices and movement are on the other tabs.</p>
  }

  const active = chartMarkets.includes(market) ? market : chartMarkets[0]
  const lead = rows.find(r => r.market === active)
  const side: LadderSide = lead?.side ?? 'b'
  const sideLabel = lead ? (active === 'total' ? (side === 'a' ? 'Over' : 'Under') : (side === 'a' ? game.away : game.home)) : ''
  const priced = active ? game.rows[active] : []
  const mainLine = active ? mainLineOf(priced.map(r => r.row)) : null
  const chart: ChartRung[] = active ? orderRungs(priced.map(r => ({ line: r.row.line, r })), active, side).map(x => x.r).map(r => {
    const sp = side === 'a' ? r.a : r.b
    const label = active === 'total' ? `${side === 'a' ? 'O' : 'U'} ${r.row.line}` : side === 'b' ? `${game.home} ${signedLine(r.row.line ?? 0)}` : `${game.away} ${signedLine(-(r.row.line ?? 0))}`
    return { line: r.row.line, label, rung: buildLadderRung(r.row, side, { pModel: modelSideProb(m, active, r.row.line, side), pMarket: sp.pFair, bettable: null }) }
  }) : []
  const near = new Set(windowRungs(chart, mainLine).map(c => c.line))
  const shown = chart.filter(c => near.has(c.line))
  const selected = selectRung(chart.map(c => c.line), active ? (picked[active] ?? null) : null, mainLine)

  const score = m.projHome != null && m.projAway != null ? `${game.away} ${m.projAway.toFixed(1)} – ${game.home} ${m.projHome.toFixed(1)}` : null
  const verdict = lean
    ? `Our model leans ${lean.label}: ${pct(lean.pModel)} against the market's ${pct(lean.pMarket)} (${pts(lean.gap)}).`
    : 'On the main lines our model has no side above the market fair price.'

  return (
    <div className="grid gap-4" data-testid="model-pane">
      <div>
        <p className="m-0 text-[13.5px] leading-[1.5] text-text-primary" data-testid="model-verdict">{verdict}</p>
        {score && <p className="m-0 mt-1 mono text-[12px] text-text-secondary" data-testid="model-score">Projected score {score}</p>}
      </div>

      <table className="w-full border-collapse text-compact" data-testid="model-table">
        <thead>
          <tr className="text-left text-[10.5px] tracking-fine uppercase text-text-tertiary">
            <th scope="col" className="font-normal py-1 pr-2">Main line</th>
            <th scope="col" className="font-normal py-1 pr-2">Model side</th>
            <th scope="col" className="font-normal py-1 pr-2 text-right">Model</th>
            <th scope="col" className="font-normal py-1 pr-2 text-right">Market</th>
            <th scope="col" className="font-normal py-1 text-right">Gap</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.market} className="border-t border-separator-border" data-testid={`model-row-${r.market}`}>
              <th scope="row" className="font-normal text-text-secondary py-1.5 pr-2 text-left">{MARKET_NAME[r.market]}</th>
              <td className="py-1.5 pr-2 text-text-primary">{r.label}</td>
              <td className="mono py-1.5 pr-2 text-right text-text-primary">{pct(r.pModel)}</td>
              <td className="mono py-1.5 pr-2 text-right text-text-secondary">{pct(r.pMarket)}</td>
              <td className="mono py-1.5 text-right font-semibold" style={{ color: r.gap > 0 ? SKY : 'var(--color-text-tertiary)' }}>{pts(r.gap)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {m.drivers.length > 0 && (
        <div data-testid="model-drivers">
          <div className="text-[10.5px] tracking-fine uppercase text-text-tertiary mb-1.5">Why</div>
          <ul className="m-0 p-0 list-none flex flex-wrap gap-1.5">
            {m.drivers.map(d => (
              <li key={d.label} className="text-[12px] rounded-chip border border-separator-border px-2 py-1">
                <span className="text-text-tertiary">{d.label}</span> <span className="mono text-text-primary">{d.value}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {active && shown.length > 1 && (
        <div data-testid="model-chart">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
            <span className="text-compact font-semibold text-text-primary">{sideLabel} by line</span>
            {chartMarkets.length > 1 && <Seg label="Market" value={active} options={chartMarkets.map(k => ({ v: k, l: MARKET_NAME[k] }))} onChange={setMarket} />}
          </div>
          <LadderChart rungs={shown} selected={selected} sideLabel={sideLabel} onSelect={line => setPicked(p => ({ ...p, [active]: line }))} />
          <p className="m-0 text-[11.5px] text-text-tertiary">Grey is the market fair price, dashed blue is our projection, and a <span style={{ color: GREEN }}>green</span> dot is the best price. Model output is unvalidated.</p>
        </div>
      )}
    </div>
  )
}
