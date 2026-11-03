'use client'

import { Fragment, useMemo, useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { SportLogo } from '@/components/ui/sport-assets'
import { modelSideProb, type GameBoardEntry } from '@/lib/game-board'
import { buildLadderRung, mainLineOf, orderRungs, selectRung, type LadderRung, type LadderSide } from '@/lib/game-ladder'
import { GAME_PRICES_LABEL, gameArbHedgeText, signedLine, startLabel, type GameMarket } from '@/lib/game-odds'
import { formatOdds, probToAmerican } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { BookMatrix } from './book-matrix'
import { LadderChart, type ChartRung } from './ladder-chart'
import { GREEN, SKY, signedPct, usd } from './game-prices-parts'

type LogoSport = Parameters<typeof SportLogo>[0]['sport']

const MARKET_TABS: { key: GameMarket; label: string }[] = [
  { key: 'ml', label: 'Moneyline' },
  { key: 'spread', label: 'Spread' },
  { key: 'total', label: 'Total' },
]

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { v: T; l: string }[]; onChange: (v: T) => void }) {
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
export function GameDetail({ game, bettable, ageLabel }: { game: GameBoardEntry; bettable: ReadonlySet<string> | null; ageLabel: string }) {
  const markets = MARKET_TABS.filter(m => game.rows[m.key].length > 0)
  const [market, setMarket] = useState<GameMarket>(() => (markets.find(m => m.key === 'spread') ?? markets[0])?.key ?? 'spread')
  const [sideByMarket, setSideByMarket] = useState<Record<GameMarket, LadderSide>>({ ml: 'b', spread: 'b', total: 'a' })
  const [alts, setAlts] = useState(false)
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
  const chartRungs: ChartRung[] = built.map(b => ({ line: b.r.row.line, label: b.label, rung: b.rung }))
  const visible = built.filter(b => b.r.row.line === mainLine || market === 'ml' || alts || b.r.row.line === selected)
  const sel = built.find(b => b.r.row.line === selected) ?? null

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
    <section aria-label={`${game.away} at ${game.home}`} data-testid="game-detail" className="bg-background-primary-default border border-separator-border rounded-xl shadow-card">
      <header className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3.5 pb-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5"><SportLogo sport={sportLogo} team={game.away} size={28} /><SportLogo sport={sportLogo} team={game.home} size={28} /></span>
          <div>
            <h2 className="text-[16px] leading-[22px] font-bold text-text-primary m-0">{game.away} @ {game.home}</h2>
            <div className="mono text-[12px] text-text-secondary">{game.sport.toUpperCase()} &middot; {startLabel(game.start)}</div>
          </div>
        </div>
        <span className="mono text-[10.5px] font-bold tracking-fine text-warn border border-[color:#facc1555] rounded-chip py-0 px-[6px]" title="Prices are market data; the model is an estimate with no tested edge.">{m ? 'UNVALIDATED MODEL ESTIMATE' : GAME_PRICES_LABEL}</span>
      </header>

      <dl className="mono grid m-0 border-y border-separator-border text-compact" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }} data-testid="detail-strip">
        {[
          { k: 'Projected score', v: m?.projHome != null && m?.projAway != null ? `${game.away} ${m.projAway.toFixed(1)} – ${game.home} ${m.projHome.toFixed(1)}` : '--' },
          { k: 'Model margin', v: winText ? `${marginText} (${winText} ML)` : marginText },
          { k: 'Model total', v: m?.total != null ? m.total.toFixed(1) : '--' },
          { k: 'Market main', v: marketMain || '--' },
          { k: 'Coverage', v: `${game.nBooks} books · ${ageLabel}` },
        ].map((c, i) => (
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

      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <Seg label="Market" value={market} options={markets.map(t => ({ v: t.key, l: t.label }))} onChange={v => setMarket(v)} />
        <Seg label="Side" value={side} options={sideTabs} onChange={v => setSideByMarket(s => ({ ...s, [market]: v }))} />
      </div>

      {market !== 'ml' && (
        <div className="px-4 pt-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-text-secondary">
            <span className="text-text-primary font-semibold text-compact">{sideLabel} ladder</span>
            <span className="inline-flex items-center gap-1.5"><svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke="#9ca3af" strokeWidth="2" /></svg>Market fair</span>
            <span className="inline-flex items-center gap-1.5"><svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={SKY} strokeWidth="2" strokeDasharray="4 3" /></svg>Our projection</span>
            <span className="inline-flex items-center gap-1.5"><svg width="8" height="8" aria-hidden><circle cx="4" cy="4" r="3" fill="#7d8596" /></svg>Book break-even</span>
            <span className="inline-flex items-center gap-1.5"><svg width="10" height="10" aria-hidden><circle cx="5" cy="5" r="4" fill={GREEN} /></svg>Best price</span>
          </div>
          <LadderChart rungs={chartRungs} selected={selected} sideLabel={sideLabel} onSelect={line => setPicked(p => ({ ...p, [market]: line }))} />
        </div>
      )}

      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <span className="text-compact font-semibold text-text-primary">Lines <span className="font-normal text-text-secondary">{visible.length} of {built.length}</span></span>
        {market !== 'ml' && built.length > 1 && <Seg label="Lines shown" value={alts ? 'alts' : 'main'} options={[{ v: 'main', l: 'Main' }, { v: 'alts', l: 'With alt lines' }]} onChange={v => setAlts(v === 'alts')} />}
      </div>
      <div className="overflow-x-auto border-t border-separator-border">
        <table className="w-full border-collapse text-compact" data-testid="rung-table">
          <thead>
            <tr className="text-[11px] text-text-tertiary" style={{ background: 'var(--color-background-secondary-default)' }}>
              <th scope="col" className="text-left font-medium px-3 py-2">Line</th>
              <th scope="col" className="text-right font-medium px-3 py-2">Books</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden md:table-cell">Model</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden md:table-cell">Market fair</th>
              <th scope="col" className="text-right font-medium px-3 py-2">Best price</th>
              <th scope="col" className="text-right font-medium px-3 py-2" title="EV of the best price against the market fair value">EV vs market</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden md:table-cell" title="EV of the best price against our model. UNVALIDATED.">Model EV</th>
              <th scope="col" className="text-right font-medium px-3 py-2 hidden md:table-cell" title="1/4 Kelly on the market-fair EV">1/4 Kelly</th>
              <th scope="col" className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.map(({ r, rung, label }) => {
              const on = r.row.line === selected
              const isMain = r.row.line === mainLine
              const sp = side === 'a' ? r.a : r.b
              const evColor = rung.thin || rung.suspect ? '#facc15' : rung.evMarket != null && rung.evMarket > 0 ? GREEN : 'var(--color-text-secondary)'
              const hedge = r.arbLive ? gameArbHedgeText(r, 100) : null
              return (
                <Fragment key={r.key}>
                  <tr data-testid="rung-row" data-line={r.row.line ?? ''} aria-selected={on} onClick={() => setPicked(p => ({ ...p, [market]: r.row.line }))}
                    className="cursor-pointer border-t border-t-[color:var(--color-separator-border)] transition-colors duration-150 hover:bg-[color:var(--color-background-primary-hover)]"
                    style={{ background: on ? 'var(--color-background-primary-hover)' : undefined }}>
                    <td className="px-3 py-2 text-left font-semibold whitespace-nowrap" style={{ boxShadow: `inset 3px 0 0 ${on ? GREEN : 'transparent'}`, color: 'var(--color-text-primary)' }}>
                      {label}{isMain && <span className="mono text-[9.5px] font-semibold tracking-fine ml-2" style={{ color: GREEN }}>MAIN</span>}
                    </td>
                    <td className="mono px-3 py-2 text-right text-text-secondary">{rung.nBooks}</td>
                    <td className="mono px-3 py-2 text-right hidden md:table-cell">{pctOf(rung.pModel)} <span className="text-text-tertiary">{fairOdds(rung.pModel)}</span></td>
                    <td className="mono px-3 py-2 text-right text-text-secondary hidden md:table-cell">{pctOf(rung.pMarket)} <span className="text-text-tertiary">{fairOdds(rung.pMarket)}</span></td>
                    <td className="mono px-3 py-2 text-right whitespace-nowrap">
                      {rung.best ? (
                        <span className="inline-flex items-center gap-1.5 justify-end">
                          <BookLogo book={rung.best.book} size={16} />
                          <span className="font-semibold rounded-chip px-1.5" style={{ color: GREEN, background: 'rgba(87,242,135,0.06)' }}>{formatOdds(rung.best.american)}</span>
                          <span className="text-text-tertiary text-[11px]">{bookDisplay(rung.best.book).short}</span>
                        </span>
                      ) : '--'}
                    </td>
                    <td className="mono px-3 py-2 text-right font-semibold" style={{ color: evColor }}>{rung.evMarket != null ? signedPct(rung.evMarket) : '--'}</td>
                    <td className="mono px-3 py-2 text-right hidden md:table-cell" style={{ color: rung.evModel != null && rung.evModel > 0 ? SKY : 'var(--color-text-tertiary)' }}>{rung.evModel != null ? signedPct(rung.evModel) : '--'}</td>
                    <td className="mono px-3 py-2 text-right text-text-secondary hidden md:table-cell">{sp.stake > 0 ? usd(sp.stake) : '--'}</td>
                    <td className="px-3 py-2 text-left whitespace-nowrap">
                      {rung.thin && <span className="mono text-[10px] font-semibold tracking-fine text-warn" title="Fewer than 3 books quote this line: the price is shown, but no edge is claimed.">THIN</span>}
                      {!rung.thin && rung.suspect && <span className="mono text-[10px] font-semibold tracking-fine text-warn" title="Unusually large against the market fair value: often a stale quote. Check the price at the book.">CHECK</span>}
                      {r.arbLive && r.arb && <span className="mono text-[10px] font-semibold tracking-fine text-warn border border-[color:#facc1555] rounded-chip px-1 ml-1.5" title={hedge ?? 'Arbitrage'}>ARB {r.arb.netArbPct.toFixed(1)}%</span>}
                    </td>
                  </tr>
                  {on && sel && (
                    <tr>
                      <td colSpan={9} className="p-0" style={{ background: 'var(--color-background-secondary-default)' }}>
                        <BookMatrix rung={sel.rung} row={sel.r.row} side={side} sideLabel={sideLabel} otherLabel={otherLabel} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-text-secondary leading-[1.5] px-4 py-3 m-0">
        Green is always against the market fair value (de-vigged from the reference books); blue is our model and is unvalidated. Offshore books other than Pinnacle are not shown, and Pinnacle is a reference only, never a place to bet.
        Lines move or limit quickly: check the price at the book. Not financial advice.
      </p>
    </section>
  )
}
