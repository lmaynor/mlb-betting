'use client'

import { useMemo, useState } from 'react'
import { SportLogo } from '@/components/ui/sport-assets'
import { modelSideProb, type GameBoardEntry } from '@/lib/game-board'
import { buildLadderRung, mainLineOf, orderRungs, rungVerdict, selectRung, windowRungs, type LadderSide } from '@/lib/game-ladder'
import { hasMovement, moveForSide } from '@/lib/line-move'
import { GAME_PRICES_LABEL, gameArbHedgeText, signedLine, startLabel, type GameMarket } from '@/lib/game-odds'
import { probToAmerican, formatOdds } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { BestVsRest } from './best-vs-rest'
import { HeatStrip, type HeatRung } from './heat-strip'
import { LadderChart, type ChartRung } from './ladder-chart'
import { Sparkline } from './sparkline'
import { GREEN, SKY, signedPct, usd } from './game-prices-parts'

type LogoSport = Parameters<typeof SportLogo>[0]['sport']

const MARKET_TABS: { key: GameMarket; label: string }[] = [
  { key: 'ml', label: 'Moneyline' },
  { key: 'spread', label: 'Spread' },
  { key: 'total', label: 'Total' },
]

export function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { v: T; l: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="group" aria-label={label} className="inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control">
      {options.map(o => (
        <button key={o.v} type="button" aria-pressed={value === o.v} onClick={() => onChange(o.v)}
          className="h-[26px] px-3 text-[12px] font-medium rounded-[7px] border cursor-pointer transition-colors duration-150"
          style={{
            color: value === o.v ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
            background: value === o.v ? 'var(--color-background-tertiary-default)' : 'transparent',
            borderColor: value === o.v ? 'var(--color-border-button-hover)' : 'transparent',
          }}>{o.l}</button>
      ))}
    </div>
  )
}

const fairOdds = (p: number | null) => (p == null || p <= 0 || p >= 1 ? '--' : formatOdds(probToAmerican(p)))
const pctOf = (p: number | null) => (p == null ? '--' : `${(p * 100).toFixed(1)}%`)

/** The spread / total / ML label of one rung for the active side. */
function rungLabel(market: GameMarket, line: number | null, side: LadderSide, away: string, home: string): string {
  if (market === 'ml') return `${side === 'a' ? away : home} ML`
  if (line == null) return market === 'spread' ? 'Spread' : 'Total'
  if (market === 'spread') return side === 'b' ? `${home} ${signedLine(line)}` : `${away} ${signedLine(-line)}`
  return `${side === 'a' ? 'O' : 'U'} ${line}`
}

/**
 * The game deep-dive: header, one summary strip, the model's drivers, then the alternate ladder for one market and one side, the
 * lines table (main by default, alt lines on request) and, under the selected row, every book side by side. Everything the model
 * says is UNVALIDATED; the green marks are always against the market fair value.
 */
export function GameDetail({ game, bettable, ageLabel, embedded = false }: { game: GameBoardEntry; bettable: ReadonlySet<string> | null; ageLabel: string; /** inside the card, which already has the matchup header and the border */ embedded?: boolean }) {
  const markets = MARKET_TABS.filter(m => game.rows[m.key].length > 0)
  const [market, setMarket] = useState<GameMarket>(() => (markets.find(m => m.key === 'spread') ?? markets[0])?.key ?? 'spread')
  const [sideByMarket, setSideByMarket] = useState<Record<GameMarket, LadderSide>>({ ml: 'b', spread: 'b', total: 'a' })
  const [wide, setWide] = useState(false)
  const [picked, setPicked] = useState<Partial<Record<GameMarket, number | null>>>({})

  const side = sideByMarket[market]
  const rows = game.rows[market]
  const mainLine = useMemo(() => (market === 'ml' ? null : mainLineOf(rows.map(r => r.row))), [market, rows])
  const m = game.model

  const ordered = useMemo(() => orderRungs(rows.map(r => ({ line: r.row.line, r })), market, side).map(x => x.r), [rows, market, side])
  const built = useMemo(() => ordered.map(r => {
    const sp = side === 'a' ? r.a : r.b
    const rung = buildLadderRung(r.row, side, { pModel: modelSideProb(m, market, r.row.line, side), pMarket: sp.pFair, bettable })
    return { r, rung, label: rungLabel(market, r.row.line, side, game.away, game.home) }
  }), [ordered, side, m, market, bettable, game.away, game.home])

  const lines = built.map(b => b.r.row.line)
  const selected = selectRung(lines, picked[market] ?? null, market === 'ml' ? (lines[0] ?? null) : mainLine)
  const allChart: ChartRung[] = built.map(b => ({ line: b.r.row.line, label: b.label, rung: b.rung }))
  const near = new Set(windowRungs(allChart, market === 'ml' ? null : mainLine).map(c => c.line))
  const chartRungs = wide ? allChart : allChart.filter(c => near.has(c.line) || c.line === selected)
  const heatRungs: HeatRung[] = built.filter(b => chartRungs.some(c => c.line === b.r.row.line)).map(b => ({ line: b.r.row.line, label: b.label, rung: b.rung, main: market !== 'ml' && b.r.row.line === mainLine }))
  const sel = built.find(b => b.r.row.line === selected) ?? null

  const hasModel = built.some(b => b.rung.pModel != null)
  const verdict = sel ? rungVerdict(sel.rung, sel.label, b => bookDisplay(b).label) : null
  const elsewhere = built
    .filter(b => b.r.row.line !== selected && !b.rung.thin && !b.rung.suspect && b.rung.evMarket != null && b.rung.evMarket > 0.005 && (sel?.rung.evMarket ?? -1) < b.rung.evMarket)
    .sort((x, y) => (y.rung.evMarket ?? 0) - (x.rung.evMarket ?? 0))[0]

  const sportLogo = game.sport as LogoSport
  const mainSpread = game.main.spread
  const mainTotal = game.main.total
  const marketMain = [mainSpread ? `${game.home} ${signedLine(mainSpread.row.line ?? 0)}` : null, mainTotal ? `O/U ${mainTotal.row.line}` : null].filter(Boolean).join(' · ')
  const marginText = m?.margin != null ? (Math.abs(m.margin) < 0.05 ? 'Pick' : `${m.margin > 0 ? game.home : game.away} by ${Math.abs(m.margin).toFixed(1)}`) : '--'
  const winText = m?.pHome != null ? `${game.home} ${(m.pHome * 100).toFixed(1)}%` : null
  const sideTabs: { v: LadderSide; l: string }[] = market === 'total' ? [{ v: 'a', l: 'Over' }, { v: 'b', l: 'Under' }] : [{ v: 'a', l: game.away }, { v: 'b', l: game.home }]
  const otherLabel = (side === 'a' ? sideTabs[1] : sideTabs[0]).l
  const sideLabel = (side === 'a' ? sideTabs[0] : sideTabs[1]).l

  return (
    <section aria-label={`${game.away} at ${game.home}`} data-testid="game-detail" className={embedded ? 'bg-background-primary-default' : 'bg-background-primary-default border border-separator-border rounded-xl shadow-card'}>
      {!embedded && <header className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3.5 pb-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5"><SportLogo sport={sportLogo} team={game.away} size={28} /><SportLogo sport={sportLogo} team={game.home} size={28} /></span>
          <div>
            <h2 className="text-[16px] leading-[22px] font-bold text-text-primary m-0">{game.away} @ {game.home}</h2>
            <div className="mono text-[12px] text-text-secondary">{game.sport.toUpperCase()} &middot; {startLabel(game.start)}</div>
          </div>
        </div>
        <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[6px]" title="Prices are market data; the model is an estimate with no tested edge.">{m ? 'UNVALIDATED MODEL ESTIMATE' : GAME_PRICES_LABEL}</span>
      </header>}

      <dl className="mono grid m-0 border-y border-separator-border text-compact" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }} data-testid="detail-strip">
        {[
          { k: 'Projected score', v: m?.projHome != null && m?.projAway != null ? `${game.away} ${m.projAway.toFixed(1)} \u2013 ${game.home} ${m.projHome.toFixed(1)}` : null },
          { k: 'Model margin', v: m?.margin != null ? (winText ? `${marginText} (${winText} ML)` : marginText) : null },
          { k: 'Model total', v: m?.total != null ? m.total.toFixed(1) : null },
          { k: 'Market main', v: marketMain || null },
          { k: 'Coverage', v: `${game.nBooks} books \u00b7 ${ageLabel}` },
        ].filter((c): c is { k: string; v: string } => c.v != null).map((c, i) => (
          <div key={c.k} className="px-4 py-2.5" style={{ borderLeft: i === 0 ? 'none' : '1px solid var(--color-separator-border)' }}>
            <dt className="text-[10.5px] tracking-fine uppercase text-text-tertiary">{c.k}</dt>
            <dd className="m-0 font-semibold text-text-primary">{c.v}</dd>
          </div>
        ))}
      </dl>
      {m && m.drivers.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 border-b border-separator-border text-[12px] text-text-secondary" data-testid="model-drivers">
          <span className="mono text-[10.5px] tracking-fine uppercase text-text-tertiary">Model drivers</span>
          {m.drivers.map(d => <span key={d.label} className="inline-flex items-baseline gap-1.5"><span className="text-text-tertiary">{d.label}</span><span className="mono text-text-primary">{d.value}</span></span>)}
        </div>
      )}

      {verdict && (
        <p className="m-0 px-4 pt-3 text-[13.5px] leading-[1.5] text-text-primary" data-testid="verdict">
          {verdict}
          {elsewhere && (
            <button type="button" className="ml-1.5 text-link text-[12.5px] cursor-pointer bg-transparent border-0 p-0 underline-offset-2 hover:underline"
              onClick={() => setPicked(p => ({ ...p, [market]: elsewhere.r.row.line }))}>
              Largest gap nearby: {elsewhere.label} ({signedPct(elsewhere.rung.evMarket ?? 0)})
            </button>
          )}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <Seg label="Market" value={market} options={markets.map(t => ({ v: t.key, l: t.label }))} onChange={v => setMarket(v)} />
        <Seg label="Side" value={side} options={sideTabs} onChange={v => setSideByMarket(s => ({ ...s, [market]: v }))} />
      </div>

      {game.hasHistory && (() => {
        const mv = game.move[market]
        if (!hasMovement(mv)) return <p className="m-0 px-4 pt-3 text-[12px] text-text-tertiary" data-testid="movement-empty">Movement tracking started recently: not enough snapshots yet to draw how this line has moved.</p>
        const f = moveForSide(mv, side)
        const lineWord = market === 'total' ? 'O/U' : game.home
        const fmtLine = (x: number | null) => (x == null ? '--' : market === 'total' ? String(x) : signedLine(x))
        const lineSeries = mv.lineSeries.filter((x): x is number => x != null)
        return (
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2 px-4 pt-3" data-testid="movement">
            <span className="mono text-[10.5px] tracking-fine uppercase text-text-tertiary">Movement since {startLabel(mv.since)}</span>
            {market !== 'ml' && (
              <span className="inline-flex items-center gap-2 text-compact" data-testid="movement-line">
                <Sparkline values={lineSeries} step w={72} h={22} color="#9ca3af" label={`${lineWord} line movement`} />
                <span className="text-text-secondary">Line</span>
                <span className="mono text-text-primary">{mv.lineMoved ? `${lineWord} ${fmtLine(mv.lineOpen)} \u2192 ${fmtLine(mv.lineNow)}` : `${lineWord} ${fmtLine(mv.lineNow)}, unchanged`}</span>
              </span>
            )}
            <span className="inline-flex items-center gap-2 text-compact" data-testid="movement-fair">
              <Sparkline values={f.pSeries} w={72} h={22} label={`${sideLabel} fair price movement`} />
              <span className="text-text-secondary">Fair price{mv.lineMoved ? ' at the new line' : ''}</span>
              {f.pOpen != null && f.pNow != null && f.pDelta != null
                ? <span className="mono text-text-primary">{(f.pOpen * 100).toFixed(1)}% &rarr; {(f.pNow * 100).toFixed(1)}% <span className="text-text-secondary">({f.pDelta >= 0 ? '+' : ''}{(f.pDelta * 100).toFixed(1)} pts)</span></span>
                : <span className="mono text-text-tertiary">not enough comparable snapshots</span>}
            </span>
          </div>
        )
      })()}

      {market !== 'ml' && (
        <div className="px-4 pt-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-text-secondary">
            <span className="text-text-primary font-semibold text-compact">{sideLabel} ladder</span>
            <span className="inline-flex items-center gap-1.5"><svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke="#9ca3af" strokeWidth="2" /></svg>Market fair</span>
            {hasModel && <span className="inline-flex items-center gap-1.5"><svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={SKY} strokeWidth="2" strokeDasharray="4 3" /></svg>Our projection</span>}
            <span className="inline-flex items-center gap-1.5"><svg width="10" height="10" aria-hidden><circle cx="5" cy="5" r="4" fill={GREEN} /></svg>Best price</span>
          </div>
          <LadderChart rungs={chartRungs} selected={selected} sideLabel={sideLabel} onSelect={line => setPicked(p => ({ ...p, [market]: line }))} />
        </div>
      )}

      <div className="px-4 pt-4 pb-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
          <span className="text-compact font-semibold text-text-primary">Every line against every book</span>
          <span className="text-[11.5px] text-text-tertiary">Green = beats the market fair price (EV %), amber = unusually large, check it. Click to open a line.</span>
        </div>
        <HeatStrip rungs={heatRungs} selected={selected} onSelect={line => setPicked(p => ({ ...p, [market]: line }))} />
        {market !== 'ml' && (allChart.length > chartRungs.length || wide) && (
          <button type="button" onClick={() => setWide(w => !w)} data-testid="ladder-wide" className="mt-2 text-[12.5px] text-link cursor-pointer bg-transparent border-0 p-0 hover:underline">
            {wide ? 'Show nearby lines only' : `Show all ${allChart.length} lines`}
          </button>
        )}
      </div>

      {sel && (
        <div className="border-t border-separator-border mt-2" data-testid="selected-line" style={{ background: 'var(--color-background-secondary-default)' }}>
          <dl className="mono flex flex-wrap items-baseline gap-x-6 gap-y-1.5 m-0 px-4 pt-3 text-compact">
            <div className="flex items-baseline gap-2">
              <dt className="sr-only">Line</dt>
              <dd className="m-0 font-sans text-[15px] font-semibold text-text-primary">{sel.label}</dd>
              {sel.r.row.line === mainLine && market !== 'ml' && <span className="text-[9.5px] font-semibold tracking-fine" style={{ color: GREEN }}>MAIN</span>}
              {sel.rung.thin && <span className="text-[10px] font-semibold tracking-fine text-warn" title="Fewer than 3 books quote this line: the price is shown, but no edge is claimed.">THIN</span>}
              {!sel.rung.thin && sel.rung.suspect && <span className="text-[10px] font-semibold tracking-fine text-warn" title="Unusually large against the market fair value: often a stale quote. Check the price at the book.">CHECK</span>}
              {sel.r.arbLive && sel.r.arb && <span className="text-[10px] font-semibold tracking-fine text-warn border border-[color:#facc1555] rounded-chip px-1" title={gameArbHedgeText(sel.r, 100) ?? 'Arbitrage'}>ARB {sel.r.arb.netArbPct.toFixed(1)}%</span>}
            </div>
            {[
              hasModel ? { k: 'Model', v: `${pctOf(sel.rung.pModel)} ${fairOdds(sel.rung.pModel)}`, c: SKY } : null,
              hasModel ? { k: 'Model EV', v: sel.rung.evModel != null ? signedPct(sel.rung.evModel) : '--', c: SKY } : null,
              (side === 'a' ? sel.r.a : sel.r.b).stake > 0 ? { k: '1/4 Kelly', v: usd((side === 'a' ? sel.r.a : sel.r.b).stake) } : null,
            ].filter((c): c is { k: string; v: string; c?: string } => c != null).map(c => (
              <div key={c.k} className="flex items-baseline gap-1.5"><dt className="text-[10.5px] tracking-fine uppercase text-text-tertiary">{c.k}</dt><dd className="m-0 font-semibold" style={{ color: c.c ?? 'var(--color-text-primary)' }}>{c.v}</dd></div>
            ))}
          </dl>
          <BestVsRest rung={sel.rung} row={sel.r.row} side={side} sideLabel={sideLabel} otherLabel={otherLabel} />
        </div>
      )}
      <p className="text-[11.5px] text-text-tertiary leading-[1.5] px-4 py-3 m-0">
        Green is against the market fair price; blue is our model and is unvalidated. Pinnacle is a reference, never a place to bet. Lines move: check the price at the book.
      </p>
    </section>
  )
}
