'use client'

/**
 * /edge NBA trends: the box-score evidence behind a prop line, for the flagged player and market.
 *
 *  - Strip: season / last-10 / last-5 averages and how often the flagged side cleared the line.
 *  - Game log: bars with the line across them and rolling L5 / L10 means (window = last 10 / 20 / this season / both seasons).
 *  - Distribution: histogram of the window with the line marked and the share of games on each side.
 *  - Splits: home / away / rest / starter / this opponent, each with its sample size.
 *  - Minutes: stat against minutes with the fitted line, so a minutes dip reads as a minutes dip.
 *  - Without a teammate: his numbers when each rotation teammate sat out.
 *  - Variance drivers: what the stat tracks game to game (correlation) and whether the recent level is unusual.
 * Potential assists, touches and drives are NBA tracking data we do not hold; the panel says so. SVG paint uses literal hex.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { apiGetTrends } from '@/lib/betting-api'
import type { CockpitRow, TrendGame, TrendsOk, TrendsResult } from '@/lib/types'
import {
  TREND_WINDOWS, driverTone, hitSide, histogram, minutesFit, num, pct, sampleStats, sideRate, windowGames, type TrendWindowId,
} from '@/lib/trends'

const GOOD = '#57F287'
const BAD = '#ef4444'
const NEUTRAL = '#4a5066'
const SKY = '#38bdf8'
const AMBER = '#f59e0b'
const MUTED = '#7d8596'
const TEXT = '#d1d5db'
const THIN = 5

const TH = 'ui-heading text-text-secondary px-1 pb-1.5 text-right first:text-left font-semibold'
const TD = 'mono px-1 py-[5px] text-right first:text-left text-body-2-regular border-t border-separator-border'

const rateColor = (x: number | null) => (x == null ? 'var(--color-text-secondary)' : x >= 0.6 ? GOOD : x >= 0.4 ? '#e5e7eb' : BAD)

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div className="border-t border-separator-border pt-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="ui-heading text-text-secondary">{title}</h4>
        {sub && <span className="mono text-mini text-text-secondary">{sub}</span>}
      </div>
      {children}
    </div>
  )
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={on}
      className={`cursor-pointer whitespace-nowrap rounded-md border px-[9px] py-1 text-caption-1-semibold ${on ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-separator-border bg-transparent text-text-secondary hover:bg-background-primary-hover'}`}>
      {children}
    </button>
  )
}

// -- summary strip ---------------------------------------------------------------------------------------------------------
function SummaryStrip({ data, under }: { data: TrendsOk; under: boolean }) {
  const line = data.line
  const rows: [string, TrendGame[]][] = [
    ['L5', windowGames(data.games, 'l10').slice(-5)], ['L10', windowGames(data.games, 'l10')],
    ['This season', windowGames(data.games, 'season')], ['Both seasons', data.games],
  ]
  return (
    <table className="w-full border-collapse" aria-label={`${data.label} against the line over several windows`}>
      <thead><tr><th className={TH}>Window</th><th className={TH}>G</th><th className={TH}>Avg</th><th className={TH}>Median</th><th className={TH}>{under ? 'Under' : 'Over'}</th></tr></thead>
      <tbody>
        {rows.map(([label, gs]) => {
          const s = sampleStats(gs, line)
          const r = sideRate(s, under)
          return (
            <tr key={label} style={{ opacity: s.n < THIN ? 0.6 : 1 }}>
              <td className={`${TD} text-text-primary`}>{label}</td>
              <td className={TD}>{s.n}</td>
              <td className={TD}>{num(s.mean)}</td>
              <td className={TD}>{num(s.median)}</td>
              <td className={TD} style={{ color: rateColor(r) }}>{r == null ? '--' : `${pct(r)} (${under ? s.under : s.over}/${s.over + s.under})`}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// -- game log --------------------------------------------------------------------------------------------------------------
export function GameLog({ games, line, under, label, rolling }: { games: TrendGame[]; line: number | null; under: boolean; label: string; rolling: boolean }) {
  const shown = games.slice(-40)
  if (shown.length < 2) return <div className="py-1.5 text-body-2-regular text-text-secondary">Not enough games in this window.</div>
  const W = 360, H = 190, padL = 28, padR = 8, padT = 18, padB = 28
  const vals = shown.map(g => g.val ?? 0)
  const rawMax = Math.max(1, ...vals, line != null ? line * 1.2 : 0)
  const step = rawMax > 60 ? 20 : rawMax > 20 ? 10 : rawMax > 8 ? 5 : 2
  const max = Math.ceil(rawMax / step) * step
  const bw = (W - padL - padR) / shown.length
  const y = (v: number) => H - padB - (v / max) * (H - padT - padB)
  const cx = (i: number) => padL + bw * i + bw / 2
  const dense = shown.length > 16
  const cleared = shown.filter(g => hitSide(g.val, line, under) === true).length
  const roll = (k: 'r5' | 'r10') => shown.map((g, i) => (g[k] == null ? null : { i, v: g[k] as number }))
  const path = (pts: ({ i: number; v: number } | null)[]) => {
    let d = '', pen = false
    for (const p of pts) { if (!p) { pen = false; continue } d += `${pen ? 'L' : 'M'}${cx(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`; pen = true }
    return d
  }
  const seasonBreaks = shown.map((g, i) => (i > 0 && g.season !== shown[i - 1].season ? i : -1)).filter(i => i > 0)
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`${label} over the last ${shown.length} games against the ${line ?? ''} line: ${under ? 'under' : 'over'} cleared in ${cleared} of ${shown.length}`}>
        {[0, max / 2, max].map(t => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="#1e2230" strokeWidth="1" />
            <text x={padL - 4} y={y(t) + 3} textAnchor="end" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{Math.round(t)}</text>
          </g>
        ))}
        {shown.map((g, i) => {
          const v = g.val ?? 0
          const hit = hitSide(g.val, line, under)
          const h = Math.max((v / max) * (H - padT - padB), 1)
          const fill = hit == null ? NEUTRAL : hit ? GOOD : BAD
          return (
            <g key={`${g.date}-${i}`}>
              <rect x={padL + bw * i + bw * 0.14} y={H - padB - h} width={bw * 0.72} height={h} rx="2" fill={fill} opacity="0.9" />
              {!dense && <text x={cx(i)} y={H - padB - h - 4} textAnchor="middle" fill={TEXT} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{Number.isInteger(v) ? v : v.toFixed(1)}</text>}
              {(!dense || i % 4 === 0) && <text x={cx(i)} y={H - 14} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{dense ? g.date.slice(5).replace('-', '/') : (g.opp ?? '').slice(0, 3)}</text>}
              {!dense && <text x={cx(i)} y={H - 3} textAnchor="middle" fill="#5b6275" style={{ fontSize: '10px', fontFamily: 'monospace' }}>{g.home == null ? '' : g.home ? 'H' : 'A'}</text>}
            </g>
          )
        })}
        {seasonBreaks.map(i => <line key={i} x1={padL + bw * i} x2={padL + bw * i} y1={padT - 6} y2={H - padB} stroke="#3a4052" strokeDasharray="2 3" />)}
        {rolling && <path d={path(roll('r10'))} fill="none" stroke={AMBER} strokeWidth="1.8" strokeLinejoin="round" />}
        {rolling && <path d={path(roll('r5'))} fill="none" stroke={SKY} strokeWidth="1.8" strokeLinejoin="round" />}
        {line != null && (
          <g>
            <line x1={padL} x2={W - padR} y1={y(line)} y2={y(line)} stroke="#f3f4f6" strokeWidth="1.4" strokeDasharray="5 4" />
            <text x={padL + 2} y={y(line) - 4} fill="#f3f4f6" style={{ fontSize: '10px', fontWeight: 600, fontFamily: 'monospace' }}>Line {line}</text>
          </g>
        )}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-mini text-text-secondary">
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: GOOD, borderRadius: 2, marginRight: 5 }} />Cleared the {under ? 'under' : 'over'}</span>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: BAD, borderRadius: 2, marginRight: 5 }} />Missed</span>
        {rolling && <span><i style={{ display: 'inline-block', width: 12, height: 2, background: SKY, marginRight: 5, verticalAlign: 'middle' }} />L5 avg</span>}
        {rolling && <span><i style={{ display: 'inline-block', width: 12, height: 2, background: AMBER, marginRight: 5, verticalAlign: 'middle' }} />L10 avg</span>}
        <span>{cleared} of {shown.length} games{!dense ? '; H / A under each bar' : ''}</span>
      </div>
    </div>
  )
}

// -- distribution ----------------------------------------------------------------------------------------------------------
export function Distribution({ games, line, under, label }: { games: TrendGame[]; line: number | null; under: boolean; label: string }) {
  const vals = games.map(g => g.val).filter((v): v is number => v != null)
  const h = useMemo(() => histogram(vals), [vals.join(',')])    // eslint-disable-line react-hooks/exhaustive-deps
  if (!h || vals.length < 8) return <div className="py-1.5 text-body-2-regular text-text-secondary">Needs at least 8 games in the window.</div>
  const W = 360, H = 138, padL = 8, padR = 8, padT = 24, padB = 22
  const bw = (W - padL - padR) / h.counts.length
  const top = Math.max(...h.counts)
  const y = (c: number) => H - padB - (c / top) * (H - padT - padB)
  const s = sampleStats(games, line)
  const lineX = line == null ? null : padL + ((line - h.lo) / h.width) * bw
  const sideOf = (binLo: number) => (line == null ? null : under ? binLo + h.width <= line : binLo > line)
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`Distribution of ${label} over ${vals.length} games: ${s.over} over and ${s.under} under the line`}>
        {h.counts.map((c, i) => {
          const lo = h.lo + i * h.width
          const on = sideOf(lo)
          return (
            <g key={i}>
              <rect x={padL + bw * i + 1} y={y(c)} width={bw - 2} height={Math.max(H - padB - y(c), c ? 1 : 0)} rx="2" fill={on == null ? NEUTRAL : on ? GOOD : '#3a4052'} opacity={on ? 0.85 : 0.9} />
              {c > 0 && <text x={padL + bw * i + bw / 2} y={y(c) - 3} textAnchor="middle" fill={TEXT} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{c}</text>}
              {(h.counts.length <= 12 || i % 2 === 0) && <text x={padL + bw * i + bw / 2} y={H - 8} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{lo}</text>}
            </g>
          )
        })}
        {lineX != null && lineX >= padL && lineX <= W - padR && (
          <g><line x1={lineX} x2={lineX} y1={12} y2={H - padB} stroke="#f3f4f6" strokeWidth="1.4" strokeDasharray="5 4" /><text x={lineX + 3} y={9} fill="#f3f4f6" style={{ fontSize: '10px', fontWeight: 600, fontFamily: 'monospace' }}>{line}</text></g>
        )}
      </svg>
      <div className="mt-1 text-compact leading-[1.5] text-neutral-300">
        {vals.length} games: median {num(s.median)}, SD {num(s.sd)}; <b style={{ color: rateColor(sideRate(s, under)) }}>{under ? s.under : s.over}</b> {under ? 'under' : 'over'} the line, {under ? s.over : s.under} the other way{s.push ? `, ${s.push} pushed` : ''}.
        Empirical frequency from this window, not a model price.
      </div>
    </div>
  )
}

// -- minutes scatter -------------------------------------------------------------------------------------------------------
export function MinutesScatter({ games, line, label, l5Min }: { games: TrendGame[]; line: number | null; label: string; l5Min: number | null }) {
  const pts = games.filter(g => g.min != null && g.val != null && g.min > 0)
  const fit = useMemo(() => minutesFit(games), [games])
  if (pts.length < 8) return <div className="py-1.5 text-body-2-regular text-text-secondary">Needs at least 8 games in the window.</div>
  const W = 360, H = 170, padL = 30, padR = 10, padT = 12, padB = 26
  const xs = pts.map(g => g.min as number), ys = pts.map(g => g.val as number)
  const x0 = Math.floor(Math.min(...xs) / 5) * 5 - 2, x1 = Math.ceil(Math.max(...xs) / 5) * 5 + 2
  const yMax = Math.ceil(Math.max(...ys, line ?? 0) / 5) * 5 + 2
  const sx = (v: number) => padL + ((v - x0) / (x1 - x0)) * (W - padL - padR)
  const sy = (v: number) => H - padB - (v / yMax) * (H - padT - padB)
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`${label} against minutes played over ${pts.length} games${fit ? `, ${fit.slope.toFixed(2)} per minute, correlation ${fit.r.toFixed(2)}` : ''}`}>
        {[0, yMax / 2, yMax].map(t => <g key={t}><line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} stroke="#1e2230" /><text x={padL - 4} y={sy(t) + 3} textAnchor="end" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{Math.round(t)}</text></g>)}
        {[x0 + 2, Math.round((x0 + x1) / 2), x1 - 2].map(t => <text key={t} x={sx(t)} y={H - 8} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{t}</text>)}
        {line != null && <line x1={padL} x2={W - padR} y1={sy(line)} y2={sy(line)} stroke="#f3f4f6" strokeWidth="1.2" strokeDasharray="5 4" />}
        {l5Min != null && <line x1={sx(l5Min)} x2={sx(l5Min)} y1={padT} y2={H - padB} stroke={SKY} strokeWidth="1" strokeDasharray="2 3" />}
        {fit && <line x1={sx(x0 + 2)} y1={sy(fit.intercept + fit.slope * (x0 + 2))} x2={sx(x1 - 2)} y2={sy(fit.intercept + fit.slope * (x1 - 2))} stroke={AMBER} strokeWidth="1.6" />}
        {pts.map((g, i) => <circle key={i} cx={sx(g.min as number)} cy={sy(g.val as number)} r="3" fill={line != null && (g.val as number) > line ? GOOD : line != null && (g.val as number) < line ? BAD : NEUTRAL} opacity="0.85" />)}
      </svg>
      <div className="mt-1 text-compact leading-[1.5] text-neutral-300">
        {fit
          ? <>Each extra minute is worth about <b>{fit.slope.toFixed(2)}</b> {label.toLowerCase()} here (correlation {fit.r.toFixed(2)}, {fit.n} games); minutes explain {(fit.r * fit.r * 100).toFixed(0)}% of the swing.</>
          : 'Not enough games for a fit.'}
        {l5Min != null && <> Last-5 minutes average {l5Min.toFixed(1)} (blue line).</>}
      </div>
    </div>
  )
}

// -- tables ----------------------------------------------------------------------------------------------------------------
function SplitsTable({ data, under }: { data: TrendsOk; under: boolean }) {
  const rows = data.splits.filter(s => s.n > 0)
  return (
    <table className="w-full border-collapse" aria-label="Splits for the market">
      <thead><tr><th className={TH}>Split</th><th className={TH}>G</th><th className={TH}>Avg</th><th className={TH}>{under ? 'Under' : 'Over'}</th><th className={TH}>Min</th></tr></thead>
      <tbody>
        {rows.map(s => {
          const rate = s.over_rate == null ? null : under ? 1 - s.over_rate : s.over_rate
          return (
            <tr key={s.key} style={{ opacity: s.n < THIN ? 0.6 : 1 }} title={s.n < THIN ? 'Small sample' : undefined}>
              <td className={`${TD} text-text-primary`}>{s.label}</td>
              <td className={TD}>{s.n}</td>
              <td className={TD}>{num(s.mean)}</td>
              <td className={TD} style={{ color: rateColor(rate) }}>{pct(rate)}</td>
              <td className={`${TD} text-text-secondary`}>{num(s.min_mean)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function TeammateTable({ data }: { data: TrendsOk }) {
  const rows = data.teammate_out
  if (!rows.length) return <div className="py-1.5 text-body-2-regular text-text-secondary">No rotation teammate has missed enough of his games to compare.</div>
  return (
    <div>
      <table className="w-full border-collapse" aria-label="His numbers when a teammate sat out">
        <thead><tr><th className={TH}>Teammate out</th><th className={TH}>G out</th><th className={TH}>With</th><th className={TH}>Without</th><th className={TH}>Min</th><th className={TH}>FGA</th></tr></thead>
        <tbody>
          {rows.map(r => {
            const d = r.mean_with != null && r.mean_without != null ? r.mean_without - r.mean_with : null
            return (
              <tr key={r.player} style={{ opacity: r.n_without < THIN ? 0.6 : 1 }} title={r.n_without < THIN ? 'Small sample' : undefined}>
                <td className={`${TD} text-text-primary`}>{r.player}</td>
                <td className={TD}>{r.n_without}</td>
                <td className={`${TD} text-text-secondary`}>{num(r.mean_with)}</td>
                <td className={TD} style={{ color: d == null ? undefined : d > 0.5 ? GOOD : d < -0.5 ? BAD : undefined }}>{num(r.mean_without)}</td>
                <td className={`${TD} whitespace-nowrap text-text-secondary`}>{r.min_without != null && r.min_with != null ? `${r.min_without.toFixed(0)}/${r.min_with.toFixed(0)}` : '--'}</td>
                <td className={`${TD} whitespace-nowrap text-text-secondary`}>{r.fga_without != null && r.fga_with != null ? `${r.fga_without.toFixed(0)}/${r.fga_with.toFixed(0)}` : '--'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="mt-1.5 text-mini text-text-secondary">With / Without = his average in games the teammate played / did not. Min and FGA read without/with. A teammate counts as out with no box-score row or 0 minutes; coach decisions are included.</div>
    </div>
  )
}

function DriversTable({ data }: { data: TrendsOk }) {
  const v = data.variance
  const rows = data.drivers.filter(d => d.corr != null)
  return (
    <div>
      <div className="mb-2 text-compact leading-[1.5] text-neutral-300">
        {v.sd != null && <>Game-to-game SD {num(v.sd)} (CV {pct(v.cv)}); minutes vary {pct(v.min_cv)}. </>}{v.note}
      </div>
      {rows.length > 0 && (
        <table className="w-full border-collapse" aria-label="What the stat tracks game to game">
          <thead><tr><th className={TH}>Driver</th><th className={TH}>Corr</th><th className={TH}>Last 5</th><th className={TH}>Season</th><th className={TH}>z</th></tr></thead>
          <tbody>
            {rows.map(d => {
              const tone = driverTone(d.corr, d.z)
              const c = d.corr as number
              return (
                <tr key={d.key}>
                  <td className={`${TD} text-text-primary`}>{d.label}</td>
                  <td className={TD}>
                    <span className="inline-flex items-center justify-end gap-1.5">
                      <span aria-hidden="true" style={{ display: 'inline-block', height: 6, width: Math.round(Math.abs(c) * 36), background: c >= 0 ? SKY : AMBER, borderRadius: 2 }} />
                      {c >= 0 ? '+' : '-'}{Math.abs(c).toFixed(2)}
                    </span>
                  </td>
                  <td className={TD}>{num(d.recent_mean)}</td>
                  <td className={`${TD} text-text-secondary`}>{num(d.season_mean)}</td>
                  <td className={TD} style={{ color: tone === 'up' ? GOOD : tone === 'down' ? BAD : undefined }} title={tone === 'up' ? 'Recent level pushes the stat above its norm' : tone === 'down' ? 'Recent level pushes the stat below its norm' : undefined}>{d.z == null ? '--' : `${d.z >= 0 ? '+' : '-'}${Math.abs(d.z).toFixed(1)}`}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      <div className="mt-1.5 text-mini leading-[1.5] text-text-secondary">
        Corr = Pearson correlation of the market with the driver across the loaded games (sky = moves with it, amber = against). z = how unusual the last-5 level of the driver is
        (green / red when |z| is 1 or more and the driver moves the stat that way). Correlation is not cause.
        Not in our data: {data.proxies.missing.slice(0, 4).join(', ')} (NBA tracking data). Teammate makes and assisted baskets are the proxies.
      </div>
    </div>
  )
}

// -- panel -----------------------------------------------------------------------------------------------------------------
export function TrendsBody({ data, under }: { data: TrendsOk; under: boolean }) {
  const [win, setWin] = useState<TrendWindowId>('l20')
  const gs = useMemo(() => windowGames(data.games, win), [data.games, win])
  const sample = sampleStats(gs, data.line)
  const first = data.games[0]?.date
  return (
    <div className="grid gap-3">
      <div className="mono flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-body-2-regular" aria-label="Sample summary">
        <span><span className="text-text-secondary">{data.label} </span><b className="text-text-primary">{under ? 'Under' : 'Over'} {data.line ?? '--'}</b></span>
        <span><span className="text-text-secondary">Window </span><b className="text-text-primary">{sample.n} g</b></span>
        <span><span className="text-text-secondary">Avg </span><b className="text-text-primary">{num(sample.mean)}</b></span>
        <span><span className="text-text-secondary">{under ? 'Under' : 'Over'} </span><b style={{ color: rateColor(sideRate(sample, under)) }}>{pct(sideRate(sample, under))}</b></span>
      </div>
      <div role="group" aria-label="Sample window" className="flex flex-wrap items-center gap-1.5">
        <span className="ui-heading w-[52px] text-text-secondary">Sample</span>
        {TREND_WINDOWS.map(w => <Chip key={w.id} on={win === w.id} onClick={() => setWin(w.id)}>{w.label}</Chip>)}
      </div>

      <Section title="Game log vs line" sub={`${data.n_loaded} games loaded since ${first ?? ''}`}>
        <GameLog games={gs} line={data.line} under={under} label={data.label} rolling={gs.length >= 12} />
      </Section>
      <Section title="Against the line"><SummaryStrip data={data} under={under} /></Section>
      <Section title="Distribution" sub={`${sample.n} games`}><Distribution games={gs} line={data.line} under={under} label={data.label} /></Section>
      <Section title="Splits" sub={`${data.scope === 'two_seasons' ? 'both seasons loaded' : data.scope}`}><SplitsTable data={data} under={under} /></Section>
      <Section title="Minutes and production" sub={`${gs.filter(g => g.min != null).length} games`}><MinutesScatter games={gs} line={data.line} label={data.label} l5Min={data.minutes.l5_mean} /></Section>
      <Section title="When a teammate sits"><TeammateTable data={data} /></Section>
      <Section title="What drives the variance"><DriversTable data={data} /></Section>
      <div className="text-mini leading-[1.5] text-text-secondary">
        Regular-season box scores through {data.as_of}{data.postseason_games_excluded ? `; ${data.postseason_games_excluded} postseason games are left out` : ''}. Rates carry their sample size and thin rows are greyed.
        Empirical history is not a forecast; it is the evidence to weigh against the price.
      </div>
    </div>
  )
}

/** Fetches and renders the trends for one cockpit row. */
export function TrendsPanel({ row, fallback }: { row: CockpitRow; fallback?: ReactNode }) {
  const [res, setRes] = useState<TrendsResult | 'error' | null>(null)
  const opp = useMemo(() => {
    const m = /^\s*([A-Za-z.]{2,5})\s*(?:@|vs\.?|v)\s*([A-Za-z.]{2,5})\s*$/.exec(row.matchup ?? '')
    if (!m) return null
    const [a, b] = [m[1].toUpperCase(), m[2].toUpperCase()]
    const t = (row.team ?? '').toUpperCase()
    return t && t === a ? b : t && t === b ? a : null
  }, [row.matchup, row.team])
  const under = String(row.side).toUpperCase() === 'UNDER'

  useEffect(() => {
    if (!row.player || !row.stat) { setRes({ status: 'no_player' }); return }
    let live = true
    setRes(null)
    Promise.resolve()
      .then(() => apiGetTrends({ player: row.player as string, stat: row.stat as string, line: row.line, opp }))
      .then(r => { if (live) setRes(r) })
      .catch(() => { if (live) setRes('error') })
    return () => { live = false }
  }, [row.player, row.stat, row.line, opp])

  if (res == null) return <div className="py-1.5 text-body-2-regular text-text-secondary" role="status">Loading trends...</div>
  if (res === 'error' || res.status !== 'ok') {
    const why = res === 'error' ? 'The trends could not be loaded.' : res.status === 'locked' ? 'Trends are part of full NBA prop research.' : res.status === 'no_data' ? 'Box-score history is not available right now.' : `No box-score history for ${row.player ?? 'this player'} in the loaded seasons.`
    return <div className="grid gap-3"><div className="py-1.5 text-body-2-regular text-text-secondary">{why}</div>{fallback}</div>
  }
  return <TrendsBody data={res} under={under} />
}
