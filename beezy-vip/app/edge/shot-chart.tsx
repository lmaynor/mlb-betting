'use client'

/**
 * /edge NBA shot chart: where the flagged player shoots and how well, against the league, plus the opponent's defence.
 *
 * Views of one half court (real SVG, hex colours): Shots (every attempt), Hex (cell size = frequency in three percentile bins, colour =
 * FG% vs league / FG% / points per shot, small cells shrunk toward the league), Heat (kernel density, optionally without the rim),
 * Zones (14 zones, FG% and difference), Matchup (his cells coloured by what the opponent allows in each zone) and Assists (the baskets
 * he set up, in seasons that carry passer data). Filters re-ask the backend for his last N games, home/away, one opponent or one
 * quarter. Under the chart: zone / shot-type / distance / quarter / home-away tables, shot volume and the opponent's defence.
 * Every rate carries its attempt count. ASCII only (escapes for typographic characters).
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { apiGetShotChart, type ShotChartQuery } from '@/lib/betting-api'
import type { CockpitRow, ShotChartOk, ShotChartResult, ShotGameRow } from '@/lib/types'
import { HEX_METRICS, MAKE, diffColor, diffPoints, distanceRows, matchupShift, pct1, shotQuality, signed1, type HexMetric } from '@/lib/shot-chart'
import { AssistsView, HeatLegend, HeatView, HexView, MatchupView, MetricLegend, ShotsView, SizeLegend, ZonesView, pct0 } from './shot-views'

type View = 'shots' | 'hex' | 'heat' | 'zones' | 'matchup' | 'assists'

const TEXT = '#d1d5db'
const MUTED = '#7d8596'
const SKY = '#38bdf8'
const THIN = 20            // attempts: below this a zone / type row is greyed as a small sample

/** "NYK @ BOS" + team "NYK" -> "BOS"; null when the matchup cannot be read. */
export function opponentOf(matchup: string | null, team: string | null): string | null {
  const m = /^\s*([A-Za-z.]{2,5})\s*(?:@|vs\.?|v)\s*([A-Za-z.]{2,5})\s*$/.exec(matchup ?? '')
  if (!m) return null
  const [a, b] = [m[1].toUpperCase(), m[2].toUpperCase()]
  const t = (team ?? '').toUpperCase()
  return t && t === a ? b : t && t === b ? a : null
}

const avg = (xs: number[]) => (xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : 0)

// -- tables -------------------------------------------------------------------------------------------------------------
const TH = 'ui-heading text-text-secondary px-1 pb-1.5 text-right first:text-left font-semibold'
const TD = 'mono px-1 py-[5px] text-right first:text-left text-body-2-regular border-t border-separator-border'

function diffCell(d: number | null) {
  if (d == null) return <span className="text-text-secondary">--</span>
  return <span style={{ color: diffColor(d) }}>{signed1(d)}</span>
}

function ZoneTable({ data }: { data: ShotChartOk }) {
  const rows = data.zones.filter(z => z.fga > 0).sort((a, b) => b.fga - a.fga)
  return (
    <table className="w-full border-collapse" aria-label="Field goals by zone against the league">
      <thead><tr><th className={TH}>Zone</th><th className={TH}>FGA</th><th className={TH}>Share</th><th className={TH}>FG%</th><th className={TH}>Lg</th><th className={TH}>Diff</th></tr></thead>
      <tbody>
        {rows.map(z => (
          <tr key={z.key} style={{ opacity: z.fga < THIN ? 0.6 : 1 }} title={z.fga < THIN ? 'Small sample' : undefined}>
            <td className={`${TD} text-text-primary`}>{z.label}</td>
            <td className={TD}>{z.fga}</td>
            <td className={TD}>{pct0(z.fga, data.fga)}</td>
            <td className={TD}>{pct1(z.fgm, z.fga)}</td>
            <td className={`${TD} text-text-secondary`}>{pct1(z.lfgm, z.lfga)}</td>
            <td className={TD}>{diffCell(diffPoints(z.fgm, z.fga, z.lfgm, z.lfga))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function GroupTable({ data }: { data: ShotChartOk }) {
  const rows = data.groups.filter(g => g.fga > 0).sort((a, b) => b.fga - a.fga)
  return (
    <table className="w-full border-collapse" aria-label="Field goals by shot type against the league">
      <thead><tr><th className={TH}>Shot type</th><th className={TH}>FGA</th><th className={TH}>Share</th><th className={TH}>FG%</th><th className={TH}>Lg</th><th className={TH}>Diff</th></tr></thead>
      <tbody>
        {rows.map(g => (
          <tr key={g.group} style={{ opacity: g.fga < THIN ? 0.6 : 1 }}>
            <td className={`${TD} text-text-primary`}>{g.group}</td>
            <td className={TD}>{g.fga}</td>
            <td className={TD}>{pct0(g.fga, data.fga)}</td>
            <td className={TD}>{pct1(g.fgm, g.fga)}</td>
            <td className={`${TD} text-text-secondary`}>{pct1(g.lfgm, g.lfga)}</td>
            <td className={TD}>{diffCell(diffPoints(g.fgm, g.fga, g.lfgm, g.lfga))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function OpponentTable({ data }: { data: ShotChartOk }) {
  const opp = data.opponent
  if (!opp) return null
  const rows = data.zones.filter(z => z.fga > 0).sort((a, b) => b.fga - a.fga).slice(0, 8).map(z => ({ z, o: opp.zones.find(x => x.key === z.key) }))
  const shift = matchupShift(data.zones, opp)
  const threeShare = opp.fga ? opp.fg3a / opp.fga : null
  const lgThree = opp.lfga ? opp.lfg3a / opp.lfga : null
  return (
    <div>
      <div className="mb-2 text-compact leading-[1.5] text-neutral-300">
        {opp.team} defence, {data.label}: {threeShare != null ? `${(threeShare * 100).toFixed(1)}% of attempts it faced were threes` : ''}{lgThree != null ? ` (league ${(lgThree * 100).toFixed(1)}%)` : ''}.
        {shift != null && <> Weighted by where {data.player.split(' ').slice(-1)[0]} shoots, its zones are worth <b style={{ color: diffColor(shift) }}>{signed1(shift)}</b> points of FG% against the league average.</>}
      </div>
      <table className="w-full border-collapse" aria-label={`${opp.team} defence in the zones the player shoots from`}>
        <thead><tr><th className={TH}>Zone</th><th className={TH}>His share</th><th className={TH}>{opp.team} allows</th><th className={TH}>Lg</th><th className={TH}>Diff</th></tr></thead>
        <tbody>
          {rows.map(({ z, o }) => {
            const oa = o?.fga ?? 0
            const od = o && z.lfga && oa ? (o.fgm / oa - z.lfgm / z.lfga) * 100 : null
            return (
              <tr key={z.key} style={{ opacity: oa < 100 ? 0.6 : 1 }}>
                <td className={`${TD} text-text-primary`}>{z.label}</td>
                <td className={TD}>{pct0(z.fga, data.fga)}</td>
                <td className={TD} title={`${oa} attempts faced`}>{o && oa ? pct1(o.fgm, oa) : '--'}</td>
                <td className={`${TD} text-text-secondary`}>{pct1(z.lfgm, z.lfga)}</td>
                <td className={TD}>{diffCell(od)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="mt-1.5 text-mini text-text-secondary">Diff is from the shooter&apos;s side: positive = {opp.team} gives up more than the league in that zone (raw; thin rows are greyed).</div>
    </div>
  )
}

function VenueTable({ data }: { data: ShotChartOk }) {
  const rows = [['Home', data.venue.home], ['Away', data.venue.away]] as const
  return (
    <table className="w-full border-collapse" aria-label="Shooting at home and away">
      <thead><tr><th className={TH}>Venue</th><th className={TH}>G</th><th className={TH}>FGA/g</th><th className={TH}>FG%</th><th className={TH}>3PA/g</th><th className={TH}>3P%</th></tr></thead>
      <tbody>
        {rows.map(([label, v]) => (
          <tr key={label}>
            <td className={`${TD} text-text-primary`}>{label}</td>
            <td className={TD}>{v.games}</td>
            <td className={TD}>{v.games ? (v.fga / v.games).toFixed(1) : '--'}</td>
            <td className={TD}>{pct1(v.fgm, v.fga)}</td>
            <td className={TD}>{v.games ? (v.fg3a / v.games).toFixed(1) : '--'}</td>
            <td className={TD}>{pct1(v.fg3m, v.fg3a)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const PERIOD_LABEL = ['Q1', 'Q2', 'Q3', 'Q4', 'OT']

/** Scoring by quarter: points per game from field goals and threes (free throws are not in the shot data), with the league's FG%. */
export function QuarterTable({ data }: { data: ShotChartOk }) {
  const q = data.quarters
  if (!q || !q.some(r => r.fga)) return null
  const W = 360, H = 96, padL = 8, padR = 8, padT = 14, padB = 20
  const rows = q.filter((r, i) => i < 4 || r.fga > 0)
  const max = Math.max(1, ...rows.map(r => r.pts_pg))
  const bw = (W - padL - padR) / rows.length
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`Field-goal points per game by quarter: ${rows.map(r => `${PERIOD_LABEL[r.period - 1]} ${r.pts_pg.toFixed(1)}`).join(', ')}`}>
        {rows.map((r, i) => {
          const h = (r.pts_pg / max) * (H - padT - padB)
          const x = padL + i * bw
          return (
            <g key={r.period}>
              <rect x={x + bw * 0.2} y={H - padB - h} width={bw * 0.6} height={Math.max(h, 1)} rx="2" fill="#4a5066" />
              <text x={x + bw / 2} y={H - padB - h - 4} textAnchor="middle" fill={TEXT} style={{ fontSize: '11px', fontFamily: 'monospace' }}>{r.pts_pg.toFixed(1)}</text>
              <text x={x + bw / 2} y={H - 6} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{PERIOD_LABEL[r.period - 1]}</text>
            </g>
          )
        })}
      </svg>
      <table className="mt-1 w-full border-collapse" aria-label="Shooting by quarter">
        <thead><tr><th className={TH}>Qtr</th><th className={TH}>FGA</th><th className={TH}>FG%</th><th className={TH}>Lg</th><th className={TH}>3PA</th><th className={TH}>Pts/g</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.period} style={{ opacity: r.fga < THIN ? 0.6 : 1 }}>
              <td className={`${TD} text-text-primary`}>{PERIOD_LABEL[r.period - 1]}</td>
              <td className={TD}>{r.fga}</td>
              <td className={TD}>{pct1(r.fgm, r.fga)}</td>
              <td className={`${TD} text-text-secondary`}>{pct1(r.lfgm, r.lfga)}</td>
              <td className={TD}>{r.fg3a}</td>
              <td className={TD}>{r.pts_pg.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Assisted baskets: who finished them and where, plus how much of HIS scoring others set up against the league. */
function AssistTables({ data }: { data: ShotChartOk }) {
  const a = data.assists
  if (!a) return null
  const zr = data.zones.filter(z => z.ast != null && z.fgm > 0).sort((x, y) => y.fgm - x.fgm).slice(0, 8)
  const made = data.zones.reduce((s, z) => s + z.fgm, 0), ast = data.zones.reduce((s, z) => s + (z.ast ?? 0), 0)
  const lmade = data.zones.reduce((s, z) => s + (z.lmade ?? 0), 0), last = data.zones.reduce((s, z) => s + (z.last ?? 0), 0)
  return (
    <div className="grid gap-3">
      <div className="text-compact leading-[1.5] text-neutral-300">
        {a.n} baskets he set up in {a.games} games ({a.games ? (a.n / a.games).toFixed(1) : '--'} a game, {a.threes ?? 0} of them threes).
        These are assisted makes: passes that did not become a made shot are tracking data we do not have.
      </div>
      {a.recipients.length > 0 && (
        <table className="w-full border-collapse" aria-label="Who scored the baskets he set up">
          <thead><tr><th className={TH}>Scorer</th><th className={TH}>Baskets</th><th className={TH}>Share</th></tr></thead>
          <tbody>{a.recipients.map(r => <tr key={r.name}><td className={`${TD} text-text-primary`}>{r.name}</td><td className={TD}>{r.n}</td><td className={TD}>{pct0(r.n, a.n)}</td></tr>)}</tbody>
        </table>
      )}
      <div className="text-compact leading-[1.5] text-neutral-300">
        {pct1(ast, made)} of his own made field goals were set up by a teammate (league {pct1(last, lmade)}): the higher the number, the more he scores off others.
      </div>
      <table className="w-full border-collapse" aria-label="Share of his makes that were assisted, by zone">
        <thead><tr><th className={TH}>Zone</th><th className={TH}>Makes</th><th className={TH}>Assisted</th><th className={TH}>Lg</th></tr></thead>
        <tbody>
          {zr.map(z => (
            <tr key={z.key} style={{ opacity: z.fgm < 10 ? 0.6 : 1 }}>
              <td className={`${TD} text-text-primary`}>{z.label}</td><td className={TD}>{z.fgm}</td>
              <td className={TD}>{pct1(z.ast ?? 0, z.fgm)}</td><td className={`${TD} text-text-secondary`}>{pct1(z.last ?? 0, z.lmade ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Last games' attempts, with the three-point attempts as the bright part of each bar: the volume behind points and threes props. */
export function VolumeBars({ games }: { games: ShotGameRow[] }) {
  if (games.length < 3) return null
  const W = 360, H = 120, padL = 26, padR = 8, padT = 14, padB = 22
  const max = Math.max(...games.map(g => g.fga), 4)
  const bw = (W - padL - padR) / games.length
  const y = (v: number) => H - padB - (v / max) * (H - padT - padB)
  const fgaAvg = avg(games.map(g => g.fga)), tpaAvg = avg(games.map(g => g.fg3a))
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`Field-goal attempts over the last ${games.length} games, averaging ${fgaAvg.toFixed(1)}, ${tpaAvg.toFixed(1)} of them threes`}>
        <line x1={padL} x2={W - padR} y1={y(fgaAvg)} y2={y(fgaAvg)} stroke={MUTED} strokeWidth="1" strokeDasharray="4 4" />
        <text x={padL - 4} y={y(fgaAvg) + 3} textAnchor="end" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{fgaAvg.toFixed(0)}</text>
        {games.map((g, i) => {
          const x = padL + i * bw
          const w = bw * 0.66
          return (
            <g key={`${g.date}-${i}`}>
              <rect x={x + bw * 0.17} y={y(g.fga)} width={w} height={Math.max(H - padB - y(g.fga), 1)} fill="#4a5066" rx="2" />
              <rect x={x + bw * 0.17} y={y(g.fg3a)} width={w} height={Math.max(H - padB - y(g.fg3a), 0)} fill={SKY} rx="2" />
              <text x={x + bw / 2} y={y(g.fga) - 3} textAnchor="middle" fill={TEXT} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{g.fga}</text>
              <text x={x + bw / 2} y={H - 8} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{g.date.slice(5).replace('-', '/')}</text>
            </g>
          )
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-mini text-text-secondary">
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: '#4a5066', borderRadius: 2, marginRight: 5 }} />Field-goal attempts, avg {fgaAvg.toFixed(1)}</span>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: SKY, borderRadius: 2, marginRight: 5 }} />Threes, avg {tpaAvg.toFixed(1)}</span>
      </div>
    </div>
  )
}

/** FG% by distance from the hoop, player vs league, with the player's attempt share as the bars behind it. */
export function DistanceChart({ data }: { data: ShotChartOk }) {
  const rows = useMemo(() => distanceRows(data.dist.player, data.dist.league), [data.dist])
  const W = 360, H = 150, padL = 30, padR = 8, padT = 12, padB = 26
  const n = rows.length
  const bw = (W - padL - padR) / n
  const maxShare = Math.max(0.05, ...rows.map(r => r.att / Math.max(data.fga, 1)))
  const share = (r: { att: number }) => r.att / Math.max(data.fga, 1)
  const y = (v: number) => H - padB - v * (H - padT - padB)
  const cx = (i: number) => padL + bw * i + bw / 2
  const player = rows.map((r, i) => (r.att >= 8 ? { i, v: r.made / r.att } : null))
  const path = (pts: ({ i: number; v: number } | null)[]) => {
    let d = '', pen = false
    for (const p of pts) { if (!p) { pen = false; continue } d += `${pen ? 'L' : 'M'}${cx(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`; pen = true }
    return d
  }
  const league = rows.map((r, i) => (r.lAtt ? { i, v: r.lMade / r.lAtt } : null))
  const label = (r: { from: number; to: number | null }) => (r.to == null ? `${r.from}+` : String(r.from))
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" style={{ width: '100%', height: 'auto', display: 'block' }}
        aria-label={`Field-goal percentage by distance from the hoop for ${data.player} against the league`}>
        {[0, 0.25, 0.5, 0.75].map(t => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="#1e2230" strokeWidth="1" />
            <text x={padL - 4} y={y(t) + 3} textAnchor="end" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{Math.round(t * 100)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const h = (share(r) / maxShare) * (H - padT - padB) * 0.55
          return <rect key={i} x={padL + bw * i + bw * 0.18} y={H - padB - h} width={bw * 0.64} height={Math.max(h, 0)} fill="#2a3042" />
        })}
        <path d={path(league)} fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeDasharray="4 3" strokeLinejoin="round" />
        <path d={path(player)} fill="none" stroke={MAKE} strokeWidth="2" strokeLinejoin="round" />
        {player.map(p => p && <circle key={p.i} cx={cx(p.i)} cy={y(p.v)} r="2.8" fill={MAKE} />)}
        {rows.map((r, i) => <text key={i} x={cx(i)} y={H - 10} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{label(r)}</text>)}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-mini text-text-secondary">
        <span><i style={{ display: 'inline-block', width: 12, height: 2, background: MAKE, marginRight: 5, verticalAlign: 'middle' }} />{data.player.split(' ').slice(-1)[0]} FG%</span>
        <span><i style={{ display: 'inline-block', width: 12, height: 0, borderTop: '2px dashed #9ca3af', marginRight: 5, verticalAlign: 'middle' }} />League FG%</span>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: '#2a3042', marginRight: 5 }} />Share of his attempts</span>
        <span>Axis: feet from the hoop, in 3-ft bins; points need 8+ attempts</span>
      </div>
    </div>
  )
}

// -- panel --------------------------------------------------------------------------------------------------------------
function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div className="border-t border-separator-border pt-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="ui-heading tracking-caps text-text-secondary">{title}</h4>
        {sub && <span className="mono text-mini text-text-secondary">{sub}</span>}
      </div>
      {children}
    </div>
  )
}

function monthDay(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

function MetricStrip({ data }: { data: ShotChartOk }) {
  const l = data.league
  const items: [string, string][] = [
    ['FG', `${pct1(data.fgm, data.fga)} (${data.fgm}/${data.fga})`],
    ['League', pct1(l.fgm, l.fga)],
    ['3P', `${pct1(data.fg3m, data.fg3a)} (${data.fg3m}/${data.fg3a})`],
    ['League 3P', pct1(l.fg3m, l.fg3a)],
    ['Games', String(data.n_games)],
  ]
  const q = shotQuality(data.hexes)
  return (
    <div className="mono flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-body-2-regular" aria-label="Season shooting summary">
      {items.map(([k, v]) => <span key={k}><span className="text-text-secondary">{k} </span><b className="text-text-primary">{v}</b></span>)}
      {q && (
        <span title="Expected FG% prices each attempt at the league's FG% from that spot; shot-making is actual minus expected, in points">
          <span className="text-text-secondary">Expected </span><b className="text-text-primary">{(q.expected * 100).toFixed(1)}%</b>
          <span className="text-text-secondary"> shot-making </span><b style={{ color: diffColor(q.making) }}>{signed1(q.making)}</b>
        </span>
      )}
    </div>
  )
}

function Chip({ on, onClick, children, title }: { on: boolean; onClick: () => void; children: ReactNode; title?: string }) {
  return (
    <button onClick={onClick} aria-pressed={on} title={title}
      className={`cursor-pointer whitespace-nowrap rounded-md border px-[9px] py-1 text-caption-1-semibold ${on ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-separator-border bg-transparent text-text-secondary hover:bg-background-primary-hover'}`}>
      {children}
    </button>
  )
}

export type ShotFilterState = { last: number | null; venue: 'home' | 'away' | null; vs: boolean; period: number | null }
export const NO_FILTERS: ShotFilterState = { last: null, venue: null, vs: false, period: null }

function FilterBar({ filters, onFilters, opp }: { filters: ShotFilterState; onFilters: (f: ShotFilterState) => void; opp: string | null }) {
  const set = (patch: Partial<ShotFilterState>) => onFilters({ ...filters, ...patch })
  return (
    <div className="grid gap-1.5" role="group" aria-label="Shot chart filters">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="ui-heading w-[52px] text-text-secondary">Games</span>
        <Chip on={filters.last === 10} onClick={() => set({ last: filters.last === 10 ? null : 10 })}>Last 10</Chip>
        <Chip on={filters.venue === 'home'} onClick={() => set({ venue: filters.venue === 'home' ? null : 'home' })}>Home</Chip>
        <Chip on={filters.venue === 'away'} onClick={() => set({ venue: filters.venue === 'away' ? null : 'away' })}>Away</Chip>
        {opp && <Chip on={filters.vs} onClick={() => set({ vs: !filters.vs })} title={`Only games against ${opp} in this season`}>vs {opp}</Chip>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="ui-heading w-[52px] text-text-secondary">Period</span>
        <Chip on={filters.period == null} onClick={() => set({ period: null })}>All</Chip>
        {[1, 2, 3, 4].map(p => <Chip key={p} on={filters.period === p} onClick={() => set({ period: filters.period === p ? null : p })}>{PERIOD_LABEL[p - 1]}</Chip>)}
      </div>
    </div>
  )
}

export function ShotChartBody({ data, onSeason, filters = NO_FILTERS, onFilters, opp = null, loading = false }: {
  data: ShotChartOk
  onSeason?: (y: number) => void
  filters?: ShotFilterState
  onFilters?: (f: ShotFilterState) => void
  opp?: string | null
  loading?: boolean
}) {
  const [view, setView] = useState<View>('hex')
  const [metric, setMetric] = useState<HexMetric>('diff')
  const [skipRim, setSkipRim] = useState(false)
  const [focus, setFocus] = useState('')
  useEffect(() => setFocus(''), [view, metric, data.season])
  const newest = data.seasons[0]
  const newer = newest != null && newest > data.season ? newest : null
  const stale = !newer && Date.now() - new Date(`${data.last_game}T12:00:00Z`).getTime() > 120 * 86_400_000
  const views: { id: View; label: string }[] = [
    { id: 'shots', label: 'Shots' }, { id: 'hex', label: 'Hex' }, { id: 'heat', label: 'Heat' }, { id: 'zones', label: 'Zones' },
    ...(data.opponent ? [{ id: 'matchup' as View, label: 'Matchup' }] : []),
    ...(data.assists ? [{ id: 'assists' as View, label: 'Assists' }] : []),
  ]
  const shown = views.some(v => v.id === view) ? view : 'hex'
  const f = data.filters
  const filtered = f != null && (f.last != null || f.venue != null || f.vs != null || f.period != null)
  const shift = data.opponent ? matchupShift(data.zones, data.opponent) : null
  const hint = shown === 'shots' ? `${data.fga} attempts, ${data.label} regular season.`
    : shown === 'heat' ? 'Where the attempts cluster; brighter = more. The rim is the hottest spot for almost everyone, so try dropping it.'
    : shown === 'assists' ? 'Each mark is a basket a teammate scored off his pass (blue = two, amber = three).'
    : shown === 'matchup' ? `Cells are his shots; colour is what ${data.opponent?.team} allows in that zone against the league (green = soft for a shooter).`
    : 'Tap a cell for its numbers. Differences are shrunk toward the league rate (5 pseudo-attempts).'
  return (
    <div className="grid gap-3" aria-busy={loading || undefined}>
      <MetricStrip data={data} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Shot chart view" className="inline-flex flex-wrap gap-0.5 rounded-lg border border-separator-border bg-background-secondary-default p-[3px]">
          {views.map(v => (
            <button key={v.id} onClick={() => setView(v.id)} aria-pressed={shown === v.id}
              className={`cursor-pointer whitespace-nowrap rounded-md border px-[10px] py-1.5 text-caption-1-semibold ${shown === v.id ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-transparent bg-transparent text-text-secondary hover:bg-background-primary-hover'}`}>
              {v.label}
            </button>
          ))}
        </div>
        {data.seasons.length > 1 && onSeason ? (
          <select aria-label="Season" value={data.season} onChange={e => onSeason(Number(e.target.value))}
            className="mono h-[32px] rounded-lg border border-border-button-default bg-background-secondary-default px-2 text-caption-1-semibold text-text-primary">
            {data.seasons.map(y => <option key={y} value={y}>{`${y}-${String(y + 1).slice(-2)}`}</option>)}
          </select>
        ) : <span className="mono text-mini text-text-secondary">{data.label}</span>}
      </div>
      {onFilters && <FilterBar filters={filters} onFilters={onFilters} opp={opp} />}
      {filtered && f && (
        <div className="mono text-mini text-text-secondary" role="status">
          Filtered: {f.games} of {f.games_season} games{f.period ? `, ${PERIOD_LABEL[f.period - 1]} only` : ''}. {data.fga ? `${data.fga} attempts.` : 'No attempts match.'}
        </div>
      )}

      {shown === 'shots' && <ShotsView data={data} />}
      {shown === 'hex' && <HexView data={data} metric={metric} onFocus={setFocus} />}
      {shown === 'heat' && <HeatView data={data} skipRim={skipRim} />}
      {shown === 'zones' && <ZonesView data={data} onFocus={setFocus} />}
      {shown === 'matchup' && <MatchupView data={data} onFocus={setFocus} />}
      {shown === 'assists' && <AssistsView data={data} />}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          {shown === 'shots' ? (
            <div className="flex flex-wrap items-center gap-x-3 text-mini text-text-secondary">
              <span><i style={{ display: 'inline-block', width: 9, height: 9, border: `1.5px solid ${MAKE}`, borderRadius: 9, marginRight: 5 }} />Made {data.fgm}</span>
              <span><b style={{ color: '#ef4444', marginRight: 5 }}>x</b>Missed {data.fga - data.fgm}</span>
            </div>
          ) : shown === 'heat' ? <HeatLegend />
            : shown === 'assists' ? null
            : <MetricLegend metric={shown === 'hex' ? metric : 'diff'}>{shown === 'hex' || shown === 'matchup' ? <SizeLegend /> : null}</MetricLegend>}
        </div>
        {shown === 'hex' && (
          <select aria-label="Colour the hexes by" value={metric} onChange={e => setMetric(e.target.value as HexMetric)}
            className="mono h-[30px] rounded-lg border border-border-button-default bg-background-secondary-default px-2 text-caption-1-semibold text-text-primary">
            {HEX_METRICS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        )}
        {shown === 'heat' && (
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-mini text-text-secondary">
            <input type="checkbox" checked={skipRim} onChange={e => setSkipRim(e.target.checked)} /> Without the rim
          </label>
        )}
      </div>
      <div role="status" aria-live="polite" className="mono min-h-[32px] text-mini leading-[1.5] text-text-secondary">{focus || hint}</div>
      {shown === 'matchup' && shift != null && data.opponent && (
        <div className="text-compact leading-[1.5] text-neutral-300">
          Weighted by where he shoots, {data.opponent.team}&apos;s zones are worth <b style={{ color: diffColor(shift) }}>{signed1(shift)}</b> points of FG% against the league.
        </div>
      )}
      {newer && (
        <div role="note" className="rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-compact leading-[1.5] text-neutral-300">
          Showing {data.label}, the player&apos;s latest season with a full sample. {`${newer}-${String(newer + 1).slice(-2)}`} is in progress (pick it in the season menu): too few attempts so far for a stable map.
        </div>
      )}
      {stale && (
        <div role="note" className="rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-compact leading-[1.5] text-neutral-300">
          Shot data ends {monthDay(data.last_game)}: this is {data.label}, not the current season. Shots from games since are not loaded yet.
        </div>
      )}

      {shown === 'assists' && data.assists && <Section title="Baskets he set up"><AssistTables data={data} /></Section>}
      <Section title="By distance"><DistanceChart data={data} /></Section>
      <Section title="By quarter" sub="field-goal points only"><QuarterTable data={data} /></Section>
      <Section title="Zones" sub={`${data.label} \u00b7 league = all players`}><ZoneTable data={data} /></Section>
      <Section title="Shot types"><GroupTable data={data} /></Section>
      {data.opponent && <Section title="Opponent defence"><OpponentTable data={data} /></Section>}
      <Section title="Home and away"><VenueTable data={data} /></Section>
      {data.games.length >= 3 && <Section title={`Shot volume, last ${data.games.length} games`} sub={`through ${monthDay(data.games[data.games.length - 1].date)}`}><VolumeBars games={data.games} /></Section>}
      <div className="text-mini leading-[1.5] text-text-secondary">
        Regular-season attempts through {monthDay(data.last_game)}, heaves past 40 ft excluded. League = every player&apos;s attempts that season.
        A zone with fewer than {THIN} attempts is greyed: its rate is mostly noise. Free throws are not in the shot data.
      </div>
    </div>
  )
}

/** Fetches and renders the chart for one cockpit row. */
export function ShotChartPanel({ row }: { row: CockpitRow }) {
  const [season, setSeason] = useState<number | undefined>(undefined)
  const [filters, setFilters] = useState<ShotFilterState>(NO_FILTERS)
  const [res, setRes] = useState<ShotChartResult | 'error' | null>(null)
  const opp = useMemo(() => opponentOf(row.matchup, row.team), [row.matchup, row.team])
  const player = row.player

  useEffect(() => {
    if (!player) { setRes({ status: 'no_player' }); return }
    let live = true
    const q: ShotChartQuery = { player, season, opp, last: filters.last, venue: filters.venue, vs: filters.vs ? opp : null, period: filters.period }
    Promise.resolve()
      .then(() => apiGetShotChart(q))
      .then(r => { if (live) setRes(r) })
      .catch(() => { if (live) setRes('error') })
    return () => { live = false }
  }, [player, season, opp, filters])

  if (res == null) return <div className="py-1.5 text-body-2-regular text-text-secondary" role="status">Loading shot chart...</div>
  if (res === 'error') return <div className="py-1.5 text-body-2-regular text-text-secondary">The shot chart could not be loaded. The line and price above are still live.</div>
  if (res.status === 'locked') return <div className="py-1.5 text-body-2-regular text-text-secondary">Shot charts are part of full NBA prop research.</div>
  if (res.status !== 'ok') {
    return <div className="py-1.5 text-body-2-regular text-text-secondary">{res.status === 'no_data' ? 'Shot data is not available right now.' : `No shot data for ${player ?? 'this player'}: the loaded seasons do not include enough of their attempts (rookies and recent signings first).`}</div>
  }
  return <ShotChartBody data={res} onSeason={setSeason} filters={filters} onFilters={setFilters} opp={opp} />
}
