'use client'

import { useMemo, useState, type ReactNode } from 'react'
import type { Bet } from '@/lib/types'
import type { PlayerStatus, SeasonStats } from '@/lib/betting-api'
import { SYSTEM_COLOR, SYSTEM_PILL } from '@/lib/tokens'

export interface EdgePick extends Bet {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
  modelProbPct: number | null
  marketProbPct: number | null
  edgePctValue: number | null
  position?: string | null
  status?: PlayerStatus
  season?: SeasonStats | null
  matchup?: {
    awayTeam: string; awayPitcher: string | null
    homeTeam: string; homePitcher: string | null
    startTime: string | null
  } | null
  weather?: { temp_f: number | null; wind_mph: number | null; wind_dir: string | null } | null
  recentForm?: { stat: string; line: number | null; games: { date: string; value: number; over: boolean | null }[] } | null
  spray?: { x: number; y: number; hit: boolean; ev?: number | null }[] | null
  evLa?: { ev: number; la: number; hit: boolean }[] | null
  velo?: { pitch: string; mph: number; n: number }[] | null
  release?: { x: number; z: number; pitch: string }[] | null
  zone?: Record<string, number> | null
}

// -- sport scaffolding (NBA slots in here once it has a model) ----------------
type SportKey = 'mlb' | 'nba'
const SPORTS: { key: SportKey; label: string; live: boolean }[] = [
  { key: 'mlb', label: 'MLB', live: true },
  { key: 'nba', label: 'NBA', live: false },
]

const PITCHER_SYSTEMS = new Set(['K', 'OUTS', 'PITCHER_ER'])
const BATTER_SYSTEMS = new Set(['HR', 'BATTER_HITS', 'BATTER_TB', 'BATTER_K'])
const GAME_SYSTEMS = new Set(['NRFI', 'F5', 'F1H', 'F3', 'F7', 'GAME'])

type Group = 'all' | 'batter' | 'pitcher' | 'game'
function groupOf(system: string): Group {
  if (BATTER_SYSTEMS.has(system)) return 'batter'
  if (PITCHER_SYSTEMS.has(system)) return 'pitcher'
  return 'game'
}

// -- formatting ---------------------------------------------------------------
const fmtOdds = (o: number) => (o > 0 ? `+${o}` : String(o))
const fmtPct = (p: number | null) => (p == null ? '--' : `${p.toFixed(0)}%`)
const fmtEdge = (e: number | null) => (e == null ? '--' : `${e >= 0 ? '+' : ''}${e.toFixed(1)}%`)

function americanToImplied(o: number): number {
  return o < 0 ? (-o) / (-o + 100) : 100 / (o + 100)
}

function betTypeLabel(betType: string | null | undefined, system: string): string {
  if (!betType) return system
  const prefix = system + '_'
  const cleaned = betType.toUpperCase().startsWith(prefix) ? betType.slice(prefix.length) : betType
  return cleaned.replace(/_/g, ' ')
}

function notesBullets(notes: string | null | undefined): string[] {
  if (!notes) return []
  const mid = String.fromCharCode(183) // middle dot
  return notes.split(mid).join(' / ')
    .split(' / ').map(s => s.trim()).filter(Boolean)
}

function titleOf(p: EdgePick): string {
  if (groupOf(p.system) === 'game') return `${p.away_team ?? '?'} @ ${p.home_team ?? '?'}`
  return p.player ?? betTypeLabel(p.bet_type, p.system)
}

// -- shared chart scaffolding (takeaway / legend / stat chips) ----------------
// Every chart leads with a plain-language takeaway, then shows the data as
// evidence. Legends and stat chips make each chart self-explaining for a
// casual viewer -- no assumed Statcast literacy.
function Take({ children }: { children: ReactNode }) {
  return <p className="edge-take">{children}</p>
}

type LegendItem = { kind: 'dot' | 'ring' | 'sq'; color?: string; opacity?: number; label: string }
function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="edge-legend">
      {items.map((it, i) => (
        <span key={i}>
          <i className={it.kind === 'dot' ? 'edge-lg-dot' : it.kind === 'ring' ? 'edge-lg-ring' : 'edge-lg-sq'}
            style={it.kind === 'ring'
              ? { borderColor: it.color ?? '#5a5a64', opacity: it.opacity }
              : { background: it.color, opacity: it.opacity }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

function Chips({ items }: { items: [string | number, string][] }) {
  return (
    <div className="edge-stats">
      {items.map(([v, l], i) => (
        <div key={i} className="edge-stat">
          <div className="edge-stat-v">{v}</div>
          <div className="edge-stat-l">{l}</div>
        </div>
      ))}
    </div>
  )
}

// -- probability track: the centerpiece ---------------------------------------
function EdgeTrack({ model, market, color }: { model: number; market: number; color: string }) {
  const lo = Math.min(model, market)
  const hi = Math.max(model, market)
  const W = 300, H = 54, x0 = 6, x1 = 294, y = 24
  const sx = (p: number) => x0 + (Math.max(0, Math.min(100, p)) / 100) * (x1 - x0)
  const ticks = [0, 25, 50, 75, 100]
  return (
    <svg className="edge-track" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`model probability ${model}% versus market ${market}%`}>
      <line x1={x0} y1={y} x2={x1} y2={y} stroke="#232329" strokeWidth="4" />
      {/* the edge band between market and model */}
      <rect x={sx(lo)} y={y - 3} width={Math.max(sx(hi) - sx(lo), 1)} height="6" fill={color} opacity="0.85" />
      {ticks.map(t => (
        <g key={t}>
          <line x1={sx(t)} y1={y - 6} x2={sx(t)} y2={y + 6} stroke="#33333a" strokeWidth="1" />
          <text className="edge-axt" x={sx(t)} y={y + 16} textAnchor="middle">{t}%</text>
        </g>
      ))}
      {/* market marker + label */}
      <rect x={sx(market) - 1.5} y={y - 9} width="3" height="18" fill="#d8d8de" />
      <text className="edge-axl" x={sx(market)} y={y - 12} textAnchor="middle" fill="#d8d8de">MARKET</text>
      {/* model marker + label */}
      <rect x={sx(model) - 5} y={y - 11} width="10" height="22" fill={color} stroke="#0d0d10" strokeWidth="1.5" />
      <text className="edge-axl" x={sx(model)} y={y + 25} textAnchor="middle" fill={color}>MODEL</text>
    </svg>
  )
}

// -- context blocks (slices 2-4) ----------------------------------------------
function WeatherChip({ w }: { w: NonNullable<EdgePick['weather']> }) {
  const parts: ReactNode[] = []
  if (w.temp_f != null) parts.push(<span key="t">{w.temp_f}&deg;F</span>)
  if (w.wind_mph != null) parts.push(<span key="w">{w.wind_mph} mph{w.wind_dir ? ` ${w.wind_dir}` : ''}</span>)
  if (parts.length === 0) return null
  return (
    <div className="edge-ctx">
      <div className="edge-ctx-k">Weather</div>
      <div className="edge-ctx-v edge-weather">{parts}</div>
    </div>
  )
}

function PitcherMatchup({ m }: { m: NonNullable<EdgePick['matchup']> }) {
  if (!m.awayPitcher && !m.homePitcher) return null
  return (
    <div className="edge-ctx">
      <div className="edge-ctx-k">Probable pitchers</div>
      <div className="edge-ctx-v">
        <span>{m.awayTeam} {m.awayPitcher ?? 'TBD'}</span>
        <span className="edge-ctx-dim"> vs </span>
        <span>{m.homeTeam} {m.homePitcher ?? 'TBD'}</span>
      </div>
    </div>
  )
}

function FormSparkline({ rf, color }: { rf: NonNullable<EdgePick['recentForm']>; color: string }) {
  const games = rf.games ?? []
  if (games.length === 0) return null
  const hasLine = rf.line != null
  const succ = (g: { value: number; over: boolean | null }) =>
    g.over != null ? g.over : hasLine ? g.value > (rf.line as number) : g.value > 0
  const W = 300, H = 92, padL = 20, padR = 8, padT = 18, padB = 18
  const max = Math.max(rf.line ?? 0, ...games.map(g => g.value), 1)
  const bw = (W - padL - padR) / games.length
  const ya = (v: number) => (H - padB) - (v / max) * (H - padT - padB)
  const lineY = hasLine ? ya(rf.line as number) : null
  const overCount = games.filter(succ).length
  const pct = Math.round((overCount / games.length) * 100)
  const n3 = Math.min(3, games.length)
  const f3 = games.slice(0, n3).reduce((a, b) => a + b.value, 0) / n3
  const l3 = games.slice(-n3).reduce((a, b) => a + b.value, 0) / n3
  const trend = l3 > f3 + 0.3 ? 'trending up' : l3 < f3 - 0.3 ? 'cooling off' : 'holding steady'
  const statL = rf.stat.replace(/_/g, ' ')
  return (
    <div className="edge-ctx edge-ctx-wide">
      <div className="edge-ctx-k">Last {games.length} games &middot; {statL}</div>
      <Take>
        {hasLine
          ? <>Cleared the <b>{rf.line}</b> line in <b>{overCount} of {games.length}</b>{' '}
              <span className="mono">({pct}%)</span> &mdash; {trend}.</>
          : <>{rf.stat === 'home_runs' ? 'Homered' : 'Produced'} in <b>{overCount} of {games.length}</b> games &mdash; {trend}.</>}
      </Take>
      <svg className="edge-spark" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`recent ${statL}: over the line in ${overCount} of ${games.length} games`}>
        {[0, Math.round(max)].map(v => (
          <text key={v} className="edge-axt" x={padL - 4} y={ya(v) + 3} textAnchor="end">{v}</text>
        ))}
        {games.map((g, i) => {
          const h = (g.value / max) * (H - padT - padB)
          const x = padL + i * bw
          const ok = succ(g)
          return (
            <g key={i}>
              <rect x={x + 1.5} y={H - padB - h} width={Math.max(bw - 3, 1)} height={Math.max(h, 0.5)}
                fill={ok ? color : '#3a3a42'} />
              <text className="edge-axt" x={x + bw / 2} y={H - padB - h - 3} textAnchor="middle"
                fill={ok ? color : '#6f6f78'} style={{ fontSize: '7.5px' }}>{g.value}</text>
            </g>
          )
        })}
        {lineY != null && <>
          <line x1={padL} y1={lineY} x2={W - padR} y2={lineY} stroke="#71d083" strokeWidth="1" strokeDasharray="3 2" />
          <text className="edge-axt" x={padL - 4} y={lineY + 3} textAnchor="end" fill="#71d083"
            style={{ fontWeight: 700 }}>{rf.line}</text>
        </>}
        <text className="edge-axt" x={padL} y={H - 4}>{games[0].date}</text>
        <text className="edge-axt" x={W - padR} y={H - 4} textAnchor="end">{games[games.length - 1].date}</text>
      </svg>
      <Legend items={hasLine
        ? [{ kind: 'sq', color, label: 'over the line' }, { kind: 'sq', color: '#3a3a42', label: 'under' }]
        : [{ kind: 'sq', color, label: rf.stat === 'home_runs' ? 'homered' : 'produced' },
           { kind: 'sq', color: '#3a3a42', label: 'none' }]} />
    </div>
  )
}

// Statcast hc coords: home plate ~ (125, 199), field opens upward (y decreases).
function SprayChart({ points, color }: { points: NonNullable<EdgePick['spray']>; color: string }) {
  if (!points.length) return null
  const S = 170
  const tx = (x: number) => (x / 250) * S
  const ty = (y: number) => (y / 250) * S
  const hits = points.filter(p => p.hit).length
  const thirds = [0, 0, 0]
  points.forEach(p => { thirds[p.x < 92 ? 0 : p.x < 158 ? 1 : 2]++ })
  const dom = thirds[0] > thirds[1] && thirds[0] > thirds[2] ? 'pulls most contact to left field'
    : thirds[2] > thirds[1] && thirds[2] > thirds[0] ? 'goes the other way to right field'
    : 'sprays the ball to all fields'
  const hp = `${tx(125)} ${ty(199)}`
  return (
    <div className="edge-ctx edge-ctx-wide">
      <div className="edge-ctx-k">Where the ball goes</div>
      <Take><b>{hits} hits</b> on {points.length} batted balls &mdash; {dom}.</Take>
      <svg className="edge-spray" viewBox={`0 0 ${S} ${S}`} role="img"
        aria-label={`batted-ball spray chart, ${hits} hits of ${points.length}`}>
        {/* outfield arc + foul lines, home plate at bottom-center */}
        <path d={`M ${hp} L ${tx(33)} ${ty(75)} A 118 118 0 0 1 ${tx(217)} ${ty(75)} Z`}
          fill="#121216" stroke="#26262c" strokeWidth="1" />
        {/* infield diamond for orientation */}
        <path d={`M ${hp} L ${tx(70)} ${ty(150)} L ${tx(125)} ${ty(120)} L ${tx(180)} ${ty(150)} Z`}
          fill="none" stroke="#26262c" strokeWidth="0.7" />
        <line x1={tx(125)} y1={ty(199)} x2={tx(33)} y2={ty(75)} stroke="#2c2c34" strokeWidth="0.7" />
        <line x1={tx(125)} y1={ty(199)} x2={tx(217)} y2={ty(75)} stroke="#2c2c34" strokeWidth="0.7" />
        <text className="edge-axt" x={tx(40)} y={ty(64)} textAnchor="middle">LF</text>
        <text className="edge-axt" x={tx(125)} y={ty(40)} textAnchor="middle">CF</text>
        <text className="edge-axt" x={tx(210)} y={ty(64)} textAnchor="middle">RF</text>
        {points.map((pt, i) => pt.hit
          ? <circle key={i} cx={tx(pt.x)} cy={ty(pt.y)} r="2.6" fill={color} opacity="0.95" />
          : <circle key={i} cx={tx(pt.x)} cy={ty(pt.y)} r="1.9" fill="none" stroke="#5a5a64" strokeWidth="1" opacity="0.6" />
        )}
      </svg>
      <Legend items={[{ kind: 'dot', color, label: 'hit' }, { kind: 'ring', label: 'out' }]} />
    </div>
  )
}

const PITCH_COLORS: Record<string, string> = {
  FF: '#ec6a6a', SI: '#ef9a52', FT: '#ef9a52', FC: '#e3b261', SL: '#a987f0', ST: '#c08cf0',
  CU: '#4ea6f5', KC: '#6f9cf5', CH: '#5fd0a0', FS: '#4fc7bd', SP: '#46c0d8',
}
const PITCH_NAME: Record<string, string> = {
  FF: '4-Seam', SI: 'Sinker', FT: '2-Seam', FC: 'Cutter', SL: 'Slider', ST: 'Sweeper',
  CU: 'Curveball', KC: 'Knuckle-curve', CH: 'Changeup', FS: 'Splitter', SP: 'Splitter',
}
const pc = (p: string) => PITCH_COLORS[p] ?? '#9aa0aa'

function EvLaScatter({ points, color }: { points: NonNullable<EdgePick['evLa']>; color: string }) {
  if (!points.length) return null
  const W = 300, H = 212, x0 = 40, x1 = 290, y0 = 12, y1 = 168
  const xa = (la: number) => x0 + ((Math.max(-30, Math.min(60, la)) + 30) / 90) * (x1 - x0)
  const ya = (ev: number) => y1 - ((Math.max(50, Math.min(120, ev)) - 50) / 70) * (y1 - y0)
  const avg = points.reduce((a, b) => a + b.ev, 0) / points.length
  const hardPct = Math.round(points.filter(p => p.ev >= 95).length / points.length * 100)
  const sweetPct = Math.round(points.filter(p => p.la >= 8 && p.la <= 32).length / points.length * 100)
  const barrels = points.filter(p => p.ev >= 98 && p.la >= 10 && p.la <= 35).length
  const verdict = avg >= 91 ? 'Crushing the ball' : avg >= 88 ? 'Solid contact' : 'Light contact lately'
  const yticks = [60, 80, 100, 120]
  const xzones: [number, number, string][] = [[-30, 10, 'Grounders'], [10, 25, 'Line drives'], [25, 50, 'Fly balls'], [50, 60, 'Pop ups']]
  return (
    <div className="edge-ctx edge-ctx-wide">
      <div className="edge-ctx-k">Quality of contact</div>
      <Take><b>{verdict}</b> &mdash; averaging <span className="mono">{avg.toFixed(1)} mph</span> off the bat, <b>{hardPct}%</b> hit hard.</Take>
      <svg className="edge-viz" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="exit velocity versus launch angle">
        {/* sweet-spot band + barrel zone */}
        <rect x={xa(8)} y={y0} width={xa(32) - xa(8)} height={y1 - y0} fill="#71d083" opacity="0.06" />
        <rect x={xa(10)} y={ya(120)} width={xa(35) - xa(10)} height={ya(98) - ya(120)} fill="#71d083" opacity="0.10" />
        <text x={(xa(10) + xa(35)) / 2} y={ya(116)} textAnchor="middle" fill="#71d083"
          style={{ fontSize: '8px', fontWeight: 800, letterSpacing: '0.04em' }}>BARRELS</text>
        {yticks.map(v => (
          <g key={v}>
            <line x1={x0} y1={ya(v)} x2={x1} y2={ya(v)} stroke="#202026" strokeWidth="0.6" />
            <text className="edge-axt" x={x0 - 4} y={ya(v) + 3} textAnchor="end">{v}</text>
          </g>
        ))}
        {[10, 25, 50].map(a => (
          <line key={a} x1={xa(a)} y1={y0} x2={xa(a)} y2={y1} stroke="#26262c" strokeWidth="0.6" strokeDasharray="2 2" />
        ))}
        {xzones.map(([a, b, l]) => (
          <text key={l} className="edge-axt" x={(xa(a) + xa(Math.min(b, 60))) / 2} y={y1 + 12} textAnchor="middle">{l}</text>
        ))}
        {points.map((p, i) => p.hit
          ? <circle key={i} cx={xa(p.la)} cy={ya(p.ev)} r="2.4" fill={color} opacity="0.95" />
          : <circle key={i} cx={xa(p.la)} cy={ya(p.ev)} r="1.7" fill="none" stroke="#5a5a64" strokeWidth="0.9" opacity="0.55" />
        )}
        <text className="edge-axl" x="6" y={(y0 + y1) / 2} textAnchor="middle"
          transform={`rotate(-90 8 ${(y0 + y1) / 2})`} fill="#9a9aa3">Exit velo (mph)</text>
        <text className="edge-axl" x={(x0 + x1) / 2} y={H - 3} textAnchor="middle" fill="#9a9aa3">Launch angle &rarr;</text>
      </svg>
      <Chips items={[[avg.toFixed(1), 'avg EV'], [`${hardPct}%`, 'hard-hit'], [`${sweetPct}%`, 'sweet spot'], [barrels, 'barrels']]} />
      <Legend items={[{ kind: 'dot', color, label: 'hit' }, { kind: 'ring', label: 'out' },
        { kind: 'sq', color: 'var(--signal)', opacity: 0.5, label: 'barrel zone = best outcomes' }]} />
    </div>
  )
}

function VeloBars({ velo }: { velo: NonNullable<EdgePick['velo']> }) {
  if (!velo.length) return null
  const max = Math.max(...velo.map(v => v.mph), 100)
  const totN = velo.reduce((a, b) => a + b.n, 0)
  const sorted = velo.slice().sort((a, b) => b.mph - a.mph)
  const top = sorted[0]
  const tier = top.mph >= 96 ? 'plus velocity' : top.mph >= 93 ? 'average velocity' : 'a finesse arm'
  return (
    <div className="edge-ctx edge-ctx-wide">
      <div className="edge-ctx-k">Pitch arsenal &amp; velocity</div>
      <Take>Leads with the <b>{PITCH_NAME[top.pitch] ?? top.pitch}</b> at <span className="mono">{top.mph.toFixed(1)} mph</span> &mdash; {tier}. {velo.length} pitches in the mix.</Take>
      <div className="edge-velo">
        {sorted.map(v => {
          const share = totN > 0 ? Math.round(v.n / totN * 100) : 0
          return (
            <div key={v.pitch} className="edge-velo-row">
              <span className="edge-velo-name">
                <b style={{ color: pc(v.pitch) }}>{v.pitch}</b>
                <span>{PITCH_NAME[v.pitch] ?? ''}</span>
              </span>
              <span className="edge-velo-bar"><span style={{ width: `${(v.mph / max) * 100}%`, background: pc(v.pitch) }} /></span>
              <span className="edge-velo-num">
                <span className="edge-velo-v">{v.mph.toFixed(1)}<small> mph</small></span>
                <span className="edge-velo-use">{share}% usage</span>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ReleasePoint({ release }: { release: NonNullable<EdgePick['release']> }) {
  if (!release.length) return null
  const W = 180, H = 150, cx = W / 2, zLo = 4, zHi = 7.5
  const xa = (x: number) => cx + (Math.max(-3, Math.min(3, x)) / 3) * (W / 2 - 16)
  const za = (z: number) => H - 14 - ((Math.max(zLo, Math.min(zHi, z)) - zLo) / (zHi - zLo)) * (H - 28)
  const mx = release.reduce((a, b) => a + b.x, 0) / release.length
  const mz = release.reduce((a, b) => a + b.z, 0) / release.length
  const side = mx < -0.3 ? 'third-base side' : mx > 0.3 ? 'first-base side' : 'over the middle'
  return (
    <div className="edge-ctx">
      <div className="edge-ctx-k">Release point <span className="edge-ctx-dim">(catcher&apos;s view)</span></div>
      <Take>Lets go at <span className="mono">{mz.toFixed(1)} ft</span> high, from the {side} &mdash; a repeatable slot.</Take>
      <svg className="edge-viz-sq" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`release point, ${mz.toFixed(1)} feet`}>
        <line x1={cx} y1={6} x2={cx} y2={H - 14} stroke="#202026" strokeWidth="0.7" strokeDasharray="2 3" />
        {[4, 5, 6, 7].map(z => (
          <g key={z}>
            <line x1={14} y1={za(z)} x2={W - 8} y2={za(z)} stroke="#202026" strokeWidth="0.6" />
            <text className="edge-axt" x={11} y={za(z) + 3} textAnchor="end">{z}&apos;</text>
          </g>
        ))}
        {release.map((r, i) => <circle key={i} cx={xa(r.x)} cy={za(r.z)} r="1.5" fill={pc(r.pitch)} opacity="0.4" />)}
        <circle cx={xa(mx)} cy={za(mz)} r="4" fill="none" stroke="#fff" strokeWidth="1.3" />
        <circle cx={xa(mx)} cy={za(mz)} r="0.9" fill="#fff" />
        <text className="edge-axt" x={16} y={H - 3}>3B side</text>
        <text className="edge-axt" x={W - 8} y={H - 3} textAnchor="end">1B side</text>
      </svg>
    </div>
  )
}

function ZoneGrid({ zone }: { zone: NonNullable<EdgePick['zone']> }) {
  const cells = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(z => zone[String(z)] ?? 0)
  const total = cells.reduce((a, b) => a + b, 0)
  if (total < 5) return null
  const max = Math.max(...cells, 1)
  const hot = cells.indexOf(Math.max(...cells))
  const row = Math.floor(hot / 3), col = hot % 3
  const vert = row === 0 ? 'high' : row === 1 ? 'middle' : 'low'
  const horiz = col === 0 ? 'to the left' : col === 1 ? 'over the middle' : 'to the right'
  const spot = row === 1 && col === 1 ? 'right down the middle'
    : row === 1 ? `middle, ${horiz}` : col === 1 ? `${vert} over the middle` : `${vert} and ${horiz}`
  return (
    <div className="edge-ctx">
      <div className="edge-ctx-k">Pitch location <span className="edge-ctx-dim">(catcher&apos;s view)</span></div>
      <Take>Lives <b>{spot}</b> &mdash; that&apos;s where most pitches cross the zone.</Take>
      <div className="edge-zone-wrap">
        <div className="edge-zone-side"><span>HIGH</span><span>LOW</span></div>
        <div>
          <div className="edge-zone">
            {cells.map((c, i) => (
              <div key={i} className={`edge-zone-cell${i === hot ? ' is-hot' : ''}`}
                style={{ background: `rgba(252,194,15,${0.05 + (c / max) * 0.62})` }}>
                {Math.round((c / total) * 100)}
              </div>
            ))}
          </div>
          <div className="edge-zone-x"><span>LEFT</span><span>RIGHT</span></div>
        </div>
      </div>
      <div className="edge-zone-scale">
        <span>fewer</span>
        <span className="edge-zone-ramp">
          {[0.1, 0.28, 0.46, 0.64, 0.82].map(o => <i key={o} style={{ background: `rgba(252,194,15,${o})` }} />)}
        </span>
        <span>more pitches</span>
      </div>
    </div>
  )
}

// -- detail panel -------------------------------------------------------------
// -- lineup/active status chip ------------------------------------------------
const STATUS_CFG: Record<PlayerStatus, { label: string; color: string; mark: string }> = {
  confirmed: { label: 'Confirmed',  color: 'var(--signal)', mark: '✓' },
  expected:  { label: 'Expected',   color: 'var(--warn)',   mark: '∼' },
  out:       { label: 'Out · IL', color: 'var(--loss)', mark: '✕' },
  unknown:   { label: 'Lineup TBD', color: 'var(--fog)',    mark: '·' },
}
function StatusChip({ status, compact = false }: { status?: PlayerStatus; compact?: boolean }) {
  const s = STATUS_CFG[status ?? 'unknown']
  return (
    <span className="edge-status" style={{
      color: s.color,
      borderColor: `color-mix(in oklab, ${s.color} 42%, var(--carbon))`,
      background: `color-mix(in oklab, ${s.color} 14%, var(--carbon))`,
    }}>{s.mark}{compact ? '' : ` ${s.label}`}</span>
  )
}

// note phrase -> pill color, by keyword (preconfigured rationale -> pills)
const NOTE_RULES: [RegExp, string][] = [
  [/barrel|exit velo|hard.?hit|power|slug/i, '#ee6fae'],
  [/matchup|platoon|split|vs\.? (l|r)h/i,    'var(--signal)'],
  [/whiff|strikeout|\bk%|\bk\b|swing/i,       '#a987f0'],
  [/wind|weather|temp|park|altitude|rain/i,   'var(--link)'],
  [/form|streak|hot|cold|last \d|recent/i,    '#e3b261'],
]
function notePillColor(text: string): string {
  for (const [re, c] of NOTE_RULES) if (re.test(text)) return c
  return 'var(--silver)'
}

// American odds from a model win probability (fair line)
function fairFromProb(pct: number | null | undefined): number | null {
  if (pct == null) return null
  const dp = pct / 100
  if (dp <= 0 || dp >= 1) return null
  return dp >= 0.5 ? -Math.round((dp / (1 - dp)) * 100) : Math.round(((1 - dp) / dp) * 100)
}

// Half-Kelly stake in dollars from bankroll, decimal model prob, american odds.
function halfKellyDollars(bankroll: number, modelPct: number | null, odds: number): number | null {
  if (modelPct == null || !bankroll) return null
  const p = modelPct / 100
  const b = odds > 0 ? odds / 100 : 100 / -odds
  const f = (b * p - (1 - p)) / b
  if (!isFinite(f) || f <= 0) return null
  return bankroll * f * 0.5
}

function SeasonLine({ season }: { season?: SeasonStats | null }) {
  if (!season || (!season.realized?.length && !season.expected?.length)) return null
  const Row = ({ items, dim }: { items: { label: string; value: string }[]; dim?: boolean }) => (
    <div className="edge-season-row">
      {items.map(s => (
        <span key={s.label} className="edge-season-cell">
          <span className="edge-season-k">{s.label}</span>
          <span className="edge-season-v" style={dim ? { color: 'var(--silver)' } : undefined}>{s.value}</span>
        </span>
      ))}
    </div>
  )
  return (
    <div className="edge-season">
      {season.realized?.length ? <Row items={season.realized} /> : null}
      {season.expected?.length ? (
        <div className="edge-season-x">
          <span className="edge-season-xk">Expected</span>
          <Row items={season.expected} dim />
        </div>
      ) : null}
    </div>
  )
}

function Detail({ p, bankroll }: { p: EdgePick; bankroll: number }) {
  const color = SYSTEM_COLOR[p.system] ?? '#9aa0aa'
  const pill = SYSTEM_PILL[p.system] ?? SYSTEM_PILL.ALL
  const isGame = groupOf(p.system) === 'game'
  const model = p.modelProbPct ?? 0
  // Editable odds: prefilled from the latest snapshot line, user can override to
  // watch the gap move. Remounts per pick (key on parent) so it resets cleanly.
  const [oddsStr, setOddsStr] = useState<string>(String(p.odds))
  const enteredOdds = Number(oddsStr)
  const oddsValid = oddsStr.trim() !== '' && isFinite(enteredOdds) && enteredOdds !== 0
  const liveImplied = oddsValid ? americanToImplied(enteredOdds) * 100 : (p.marketProbPct ?? americanToImplied(p.odds) * 100)
  const gap = p.modelProbPct != null ? p.modelProbPct - liveImplied : null
  const kelly = halfKellyDollars(bankroll, p.modelProbPct, oddsValid ? enteredOdds : p.odds)
  const market = p.marketProbPct ?? americanToImplied(p.odds) * 100
  const fairOdds = (() => {
    if (p.modelProbPct == null) return null
    const dp = p.modelProbPct / 100
    if (dp <= 0 || dp >= 1) return null
    return dp >= 0.5 ? -Math.round((dp / (1 - dp)) * 100) : Math.round(((1 - dp) / dp) * 100)
  })()
  const bullets = notesBullets(p.notes)

  return (
    <div className="edge-detail">
      <div className="edge-detail-head">
        <div className="edge-portrait" style={{ background: `${color}14`, borderColor: `${color}40` }}>
          {!isGame && p.headshotUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={p.headshotUrl} alt={p.player ?? ''} className="edge-portrait-img"
              onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
          ) : (
            <div className="edge-logos">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {p.awayLogoUrl && <img src={p.awayLogoUrl} alt="" className="edge-logo" />}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {p.homeLogoUrl && <img src={p.homeLogoUrl} alt="" className="edge-logo" />}
            </div>
          )}
        </div>
        <div className="edge-detail-id">
          <div className="edge-detail-tags">
            <span className="edge-pill" style={{ background: pill.bg, color: pill.color, border: pill.border }}>
              {p.system}
            </span>
            {!isGame && p.position && <span className="edge-pos">{p.position}</span>}
            {!isGame && <StatusChip status={p.status} />}
          </div>
          <h2 className="edge-detail-name">{titleOf(p)}</h2>
          <div className="edge-detail-sub">
            {isGame ? null : <span>{p.away_team} @ {p.home_team}</span>}
            <span className="edge-strong" style={{ color }}>{betTypeLabel(p.bet_type, p.system)}</span>
            {p.matchup?.startTime && <span>{p.matchup.startTime}</span>}
            {p.book && <span className="edge-book">{p.book}</span>}
          </div>
        </div>
      </div>

      <SeasonLine season={p.season} />

      {/* THE EDGE block — live: model − implied(odds) = gap */}
      <div className="edge-block">
        <div className="edge-block-top">
          <div className="edge-hero" style={{ color: gap != null && gap < 0 ? 'var(--loss)' : color }}>
            {gap != null ? `${gap >= 0 ? '+' : ''}${gap.toFixed(1)}%` : fmtEdge(p.edgePctValue)}
          </div>
          <div className="edge-hero-label">model edge at this line</div>
        </div>
        <EdgeTrack model={model} market={liveImplied} color={color} />
        <div className="edge-readout">
          <div className="edge-read">
            <span className="edge-read-dot" style={{ background: color }} />
            <span className="edge-read-k">Our model</span>
            <span className="edge-read-v">{fmtPct(p.modelProbPct)}</span>
          </div>
          <div className="edge-read edge-read-odds">
            <span className="edge-read-dot edge-read-dot-market" />
            <span className="edge-read-k">Live line</span>
            <span className="edge-odds-in">
              <input
                inputMode="numeric"
                value={oddsStr}
                onChange={e => setOddsStr(e.target.value)}
                aria-label="Live odds (American)"
                className="edge-odds-field"
              />
              <span className="edge-odds-imp">{oddsValid ? `${liveImplied.toFixed(1)}% implied` : '—'}</span>
            </span>
          </div>
        </div>
        <div className="edge-meta">
          <div className="edge-meta-cell">
            <span className="edge-meta-k">Kelly stake</span>
            <span className="edge-meta-v" style={{ color: 'var(--signal)' }}>
              {kelly != null ? `$${kelly.toFixed(0)}` : '—'}
              <span className="edge-meta-dim"> ½K</span>
            </span>
          </div>
          {fairOdds != null && (
            <div className="edge-meta-cell">
              <span className="edge-meta-k">Fair vs offered</span>
              <span className="edge-meta-v">{fmtOdds(fairOdds)} <span className="edge-meta-dim">/ {oddsValid ? fmtOdds(enteredOdds) : fmtOdds(p.odds)}</span></span>
            </div>
          )}
        </div>
      </div>

      {(p.weather || p.matchup || p.recentForm || (p.spray?.length) || (p.evLa?.length) ||
        (p.velo?.length) || (p.release?.length) || p.zone) && (
        <div className="edge-context">
          {(p.weather || p.matchup) && (
            <div className="edge-context-row">
              {p.weather && <WeatherChip w={p.weather} />}
              {p.matchup && <PitcherMatchup m={p.matchup} />}
            </div>
          )}
          {p.recentForm && <FormSparkline rf={p.recentForm} color={color} />}
          {p.spray && p.spray.length > 0 && <SprayChart points={p.spray} color={color} />}
          {p.evLa && p.evLa.length > 0 && <EvLaScatter points={p.evLa} color={color} />}
          {p.velo && p.velo.length > 0 && <VeloBars velo={p.velo} />}
          {((p.release && p.release.length > 0) || p.zone) && (
            <div className="edge-viz-row">
              {p.release && p.release.length > 0 && <ReleasePoint release={p.release} />}
              {p.zone && <ZoneGrid zone={p.zone} />}
            </div>
          )}
        </div>
      )}

      {bullets.length > 0 && (
        <div className="edge-why">
          <div className="edge-why-h">Why we like it</div>
          <div className="edge-pills">
            {bullets.map((b, i) => {
              const c = notePillColor(b)
              return (
                <span key={i} className="edge-note-pill" style={{
                  color: c,
                  borderColor: `color-mix(in oklab, ${c} 40%, var(--carbon))`,
                  background: `color-mix(in oklab, ${c} 12%, var(--carbon))`,
                }}>{b}</span>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

type Mode = 'players' | 'games'

export function EdgeClient({ picks, updated }: { picks: EdgePick[]; updated: string }) {
  const [sport, setSport] = useState<SportKey>('mlb')
  const [mode, setMode] = useState<Mode>('players')
  const [team, setTeam] = useState<string>('all')
  const [position, setPosition] = useState<string>('all')
  const [markets, setMarkets] = useState<string[]>([])   // empty = all
  const [minEdge, setMinEdge] = useState<number>(0)
  const [bankroll, setBankroll] = useState<number>(1000)
  const [hideInactive, setHideInactive] = useState<boolean>(false)
  const [query, setQuery] = useState<string>('')
  const [topOnly, setTopOnly] = useState<boolean>(false)
  const [selectedId, setSelectedId] = useState<number | null>(picks[0]?.id ?? null)

  const inMode = (p: EdgePick) => mode === 'games' ? groupOf(p.system) === 'game' : groupOf(p.system) !== 'game'
  const modeCount = (m: Mode) => picks.filter(p => m === 'games' ? groupOf(p.system) === 'game' : groupOf(p.system) !== 'game').length

  const teams = useMemo(() => {
    const set = new Set<string>()
    picks.forEach(p => { if (p.away_team) set.add(p.away_team); if (p.home_team) set.add(p.home_team) })
    return Array.from(set).sort()
  }, [picks])

  const positions = useMemo(() => {
    const set = new Set<string>()
    picks.forEach(p => { if (groupOf(p.system) !== 'game' && p.position) set.add(p.position) })
    return Array.from(set).sort()
  }, [picks])

  const marketList = useMemo(() => {
    const set = new Set<string>()
    picks.forEach(p => { if (inMode(p)) set.add(p.system) })
    return Array.from(set).sort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picks, mode])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    let v = picks.filter(inMode)
    if (team !== 'all') v = v.filter(p => p.away_team === team || p.home_team === team)
    if (mode === 'players' && position !== 'all') v = v.filter(p => p.position === position)
    if (markets.length) v = v.filter(p => markets.includes(p.system))
    if (minEdge > 0) v = v.filter(p => (p.edgePctValue ?? 0) >= minEdge)
    if (hideInactive) v = v.filter(p => p.status !== 'out')
    if (q) v = v.filter(p =>
      (p.player ?? '').toLowerCase().includes(q) ||
      (p.away_team ?? '').toLowerCase().includes(q) ||
      (p.home_team ?? '').toLowerCase().includes(q) ||
      p.system.toLowerCase().includes(q))
    v = v.slice().sort((a, b) => (b.edgePctValue ?? 0) - (a.edgePctValue ?? 0))
    if (topOnly) v = v.slice(0, 10)
    return v
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picks, mode, team, position, markets, minEdge, hideInactive, query, topOnly])

  const selected = useMemo(
    () => visible.find(p => p.id === selectedId) ?? visible[0] ?? null,
    [visible, selectedId],
  )
  const hasFilters = team !== 'all' || position !== 'all' || markets.length > 0 || minEdge > 0 || hideInactive || query !== '' || topOnly
  const toggleMarket = (m: string) => setMarkets(cur => cur.includes(m) ? cur.filter(x => x !== m) : [...cur, m])

  return (
    <main className="edge">
      <Styles />
      <header className="edge-header">
        <div className="edge-title-row">
          <h1 className="edge-title">THE EDGE</h1>
          <span className="edge-updated">
            <span className="edge-live-dot" /> UPDATED HOURLY <span className="edge-updated-time">{updated}</span>
          </span>
        </div>
        <p className="edge-tagline">Our model probability vs the market line on every pick. The gap is your edge.</p>
        <div className="edge-sports" role="tablist" aria-label="Sport">
          {SPORTS.map(s => (
            <button key={s.key} role="tab" aria-selected={sport === s.key}
              disabled={!s.live}
              onClick={() => s.live && setSport(s.key)}
              className={`edge-sport${sport === s.key ? ' is-active' : ''}${s.live ? '' : ' is-soon'}`}>
              {s.label}{!s.live && <span className="edge-soon">soon</span>}
            </button>
          ))}
        </div>
      </header>

      {/* Players | Games mode switch */}
      <div className="edge-mode" role="tablist" aria-label="Player or game props">
        {(['players', 'games'] as Mode[]).map(m => (
          <button key={m} role="tab" aria-selected={mode === m}
            onClick={() => { setMode(m); setPosition('all'); setMarkets([]) }}
            className={`edge-modebtn${mode === m ? ' is-active' : ''}`}>
            {m === 'players' ? 'Players' : 'Games'}<span className="edge-filter-n">{modeCount(m)}</span>
          </button>
        ))}
      </div>

      {/* Control bar */}
      <div className="edge-controls">
        <label className="edge-select">
          <span className="edge-select-k">Team</span>
          <select value={team} onChange={e => setTeam(e.target.value)} aria-label="Team">
            <option value="all">All</option>
            {teams.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {mode === 'players' && positions.length > 0 && (
          <label className="edge-select">
            <span className="edge-select-k">Pos</span>
            <select value={position} onChange={e => setPosition(e.target.value)} aria-label="Position">
              <option value="all">All</option>
              {positions.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        )}
        <label className="edge-slider">
          <span>Min edge <strong>{minEdge}%</strong></span>
          <input type="range" min={0} max={20} step={1} value={minEdge}
            onChange={e => setMinEdge(Number(e.target.value))} aria-label="Minimum edge" />
        </label>
        <label className="edge-select edge-bank">
          <span className="edge-select-k">Bankroll $</span>
          <input type="number" min={0} step={50} value={bankroll}
            onChange={e => setBankroll(Math.max(0, Number(e.target.value) || 0))} aria-label="Bankroll" />
        </label>
        <input className="edge-search" type="search" placeholder="Search player / team…"
          value={query} onChange={e => setQuery(e.target.value)} aria-label="Search" />
        <button className={`edge-toggle${hideInactive ? ' is-on' : ''}`} onClick={() => setHideInactive(v => !v)}
          aria-pressed={hideInactive}>Active only</button>
        <button className={`edge-toggle${topOnly ? ' is-on' : ''}`} onClick={() => setTopOnly(v => !v)}
          aria-pressed={topOnly}>Top 10</button>
        {hasFilters && (
          <button className="edge-clear" onClick={() => { setTeam('all'); setPosition('all'); setMarkets([]); setMinEdge(0); setHideInactive(false); setQuery(''); setTopOnly(false) }}>Clear</button>
        )}
      </div>

      {/* Market chips */}
      {marketList.length > 1 && (
        <div className="edge-markets">
          {marketList.map(m => {
            const on = markets.includes(m)
            const c = SYSTEM_COLOR[m] ?? 'var(--silver)'
            return (
              <button key={m} onClick={() => toggleMarket(m)} className="edge-mchip"
                style={on ? { color: c, borderColor: `color-mix(in oklab, ${c} 45%, var(--carbon))`, background: `color-mix(in oklab, ${c} 16%, var(--carbon))` } : undefined}>
                {m}
              </button>
            )
          })}
        </div>
      )}

      {visible.length === 0 ? (
        <div className="edge-empty">
          <p className="edge-empty-h">No picks match these filters.</p>
          <p className="edge-empty-s">Picks publish through the day &mdash; loosen a filter, or see <a href="/results" className="edge-link">recent results</a>.</p>
        </div>
      ) : (
        <div className="edge-grid">
          <ol className="edge-list">
            {visible.map((p, i) => {
              const color = SYSTEM_COLOR[p.system] ?? '#9aa0aa'
              const active = selected?.id === p.id
              const isGame = groupOf(p.system) === 'game'
              return (
                <li key={p.id}>
                  <button className={`edge-row${active ? ' is-active' : ''}`} onClick={() => setSelectedId(p.id)}>
                    <span className="edge-row-rank">{i + 1}</span>
                    <span className="edge-row-thumb" style={{ background: `${color}14`, borderColor: `${color}38` }}>
                      {!isGame && p.headshotUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.headshotUrl} alt="" className="edge-row-img"
                          onError={e => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }} />
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        p.homeLogoUrl ? <img src={p.homeLogoUrl} alt="" className="edge-row-logo" /> : null
                      )}
                    </span>
                    <span className="edge-row-main">
                      <span className="edge-row-name">
                        {titleOf(p)}
                        {!isGame && <StatusChip status={p.status} compact />}
                      </span>
                      <span className="edge-row-meta">
                        <span className="edge-row-sys" style={{ color }}>{p.system}</span>
                        <span>{betTypeLabel(p.bet_type, p.system)}</span>
                        <span className="edge-row-odds">{fmtOdds(p.odds)}</span>
                      </span>
                    </span>
                    <span className="edge-row-edge" style={{ color }}>{fmtEdge(p.edgePctValue)}</span>
                  </button>
                </li>
              )
            })}
          </ol>
          <div className="edge-panel" key={selected?.id ?? 'none'}>
            {selected && <Detail p={selected} bankroll={bankroll} />}
          </div>
        </div>
      )}
    </main>
  )
}

// -- scoped styles (media queries + motion + hover; inline can't express these)
function Styles() {
  return (
    <style>{`
.edge { max-width: 1120px; margin: 0 auto; padding: 28px 16px 80px; color: var(--ash);
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
.edge-header { border-bottom: 1px solid var(--basalt); padding-bottom: 18px; margin-bottom: 18px; }
.edge-title-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.edge-title { font-size: clamp(1.9rem, 6vw, 3rem); font-weight: 860; letter-spacing: -0.04em;
  margin: 0; text-wrap: balance; }
.edge-updated { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 700;
  letter-spacing: 0.08em; color: var(--signal); }
.edge-updated-time { color: #8a8a93; font-weight: 600; letter-spacing: 0; }
.edge-live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--signal);
  box-shadow: 0 0 0 0 var(--signal)77; animation: edgepulse 2.4s ease-out infinite; }
@keyframes edgepulse { 0% { box-shadow: 0 0 0 0 var(--signal)66; } 70% { box-shadow: 0 0 0 7px var(--signal)00; } 100% { box-shadow: 0 0 0 0 var(--signal)00; } }
.edge-tagline { color: #b6b6be; font-size: 14px; margin: 8px 0 0; max-width: 60ch; line-height: 1.5; }
.edge-sports { display: flex; gap: 6px; margin-top: 16px; }
.edge-sport { background: var(--graphite); color: #9aa0aa; border: 1px solid var(--basalt); padding: 6px 14px;
  font-size: 12px; font-weight: 800; letter-spacing: 0.06em; cursor: pointer; transition: color .18s, border-color .18s, background .18s; }
.edge-sport.is-active { color: var(--ash); border-color: var(--ash); background: #17171b; }
.edge-sport.is-soon { cursor: not-allowed; opacity: 0.55; display: inline-flex; align-items: center; gap: 6px; }
.edge-soon { font-size: 8px; font-weight: 800; letter-spacing: 0.1em; color: #6f6f78; border: 1px solid var(--iron); padding: 1px 4px; }
.edge-filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 18px; }
.edge-filter { background: transparent; color: #9aa0aa; border: 1px solid var(--basalt); padding: 6px 12px;
  font-size: 12px; font-weight: 700; cursor: pointer; transition: color .18s, border-color .18s; }
.edge-filter:hover { color: #d8d8de; }
.edge-filter.is-active { color: #0b0b0d; background: var(--ash); border-color: var(--ash); }
.edge-filter-n { font-variant-numeric: tabular-nums; opacity: 0.6; margin-left: 2px; }
.edge-grid { display: grid; grid-template-columns: 1fr; gap: 16px; align-items: start; }
@media (min-width: 900px) { .edge-grid { grid-template-columns: minmax(320px, 0.92fr) 1.08fr; }
  .edge-panel { position: sticky; top: 16px; } }
.edge-list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--basalt); background: #0d0d10; }
.edge-row { width: 100%; display: flex; align-items: center; gap: 11px; padding: 11px 12px; text-align: left;
  background: var(--graphite); color: var(--ash); border: 0; border-bottom: 1px solid var(--basalt); cursor: pointer;
  transition: background .16s ease-out, transform .16s ease-out; }
.edge-row:last-child { border-bottom: 0; }
.edge-row:hover { background: #17171b; }
.edge-row.is-active { background: #1b1b20; box-shadow: inset 3px 0 0 currentColor; }
.edge-row-rank { font-size: 10px; font-weight: 800; color: #6f6f78; min-width: 14px; font-variant-numeric: tabular-nums; }
.edge-row-thumb { width: 38px; height: 38px; min-width: 38px; border: 1px solid; display: flex;
  align-items: center; justify-content: center; overflow: hidden; }
.edge-row-img { width: 38px; height: 46px; object-fit: cover; object-position: top; }
.edge-row-logo { width: 24px; height: 24px; object-fit: contain; }
.edge-row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.edge-row-name { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.edge-row-meta { display: flex; gap: 8px; font-size: 11px; color: #9aa0aa; align-items: center; }
.edge-row-sys { font-weight: 800; letter-spacing: 0.04em; }
.edge-row-odds { font-variant-numeric: tabular-nums; color: #b6b6be; }
.edge-row-edge { font-size: 18px; font-weight: 840; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.edge-panel { border: 1px solid var(--basalt); background: #0d0d10; min-height: 200px; animation: edgein .22s ease-out; }
@keyframes edgein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.edge-detail { padding: 18px; }
.edge-detail-head { display: flex; gap: 14px; align-items: flex-start; padding-bottom: 16px; border-bottom: 1px solid var(--basalt); }
.edge-portrait { width: 88px; height: 96px; min-width: 88px; border: 1px solid; display: flex;
  align-items: flex-end; justify-content: center; overflow: hidden; }
.edge-portrait-img { width: 88px; height: 100px; object-fit: cover; object-position: top; }
.edge-logos { display: flex; gap: 6px; align-items: center; height: 100%; }
.edge-logo { width: 34px; height: 34px; object-fit: contain; }
.edge-detail-id { flex: 1; min-width: 0; }
.edge-pill { display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.06em; padding: 2px 7px; }
.edge-detail-name { font-size: clamp(1.25rem, 3.4vw, 1.7rem); font-weight: 820; letter-spacing: -0.02em;
  margin: 8px 0 6px; line-height: 1.05; text-wrap: balance; }
.edge-detail-sub { display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; color: #9aa0aa; align-items: center; }
.edge-strong { font-weight: 800; }
.edge-block { padding: 18px 0 4px; }
.edge-block-top { display: flex; align-items: baseline; gap: 10px; }
.edge-hero { font-size: clamp(2.4rem, 8vw, 3.4rem); font-weight: 880; letter-spacing: -0.04em; line-height: 1; font-variant-numeric: tabular-nums; }
.edge-hero-label { font-size: 12px; color: #9aa0aa; font-weight: 600; }
.edge-track { width: 100%; max-width: 380px; height: auto; display: block; margin: 16px 0 8px; }
.edge-axt { fill: #9a9aa3; font-size: 8px; font-family: var(--font-mono), ui-monospace, monospace; }
.edge-axl { font-size: 8.5px; font-weight: 700; font-family: var(--font-mono), ui-monospace, monospace; }
.edge-readout { display: flex; gap: 20px; margin-top: 6px; }
.edge-read { display: flex; align-items: center; gap: 7px; font-size: 12px; }
.edge-read-dot { width: 9px; height: 9px; border-radius: 2px; }
.edge-read-dot-market { background: #d8d8de; }
.edge-read-k { color: #9aa0aa; }
.edge-read-v { color: var(--ash); font-weight: 800; font-variant-numeric: tabular-nums; }
.edge-meta { display: flex; gap: 26px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--basalt); }
.edge-meta-k { display: block; font-size: 11px; color: #8a8a93; letter-spacing: 0.04em; margin-bottom: 3px; }
.edge-meta-v { font-size: 16px; font-weight: 800; font-variant-numeric: tabular-nums; }
.edge-meta-dim { color: #8a8a93; font-weight: 600; }
.edge-why { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--basalt); }
.edge-why-h { font-size: 11px; font-weight: 800; letter-spacing: 0.08em; color: #8a8a93; margin-bottom: 8px; }
.edge-why-list { margin: 0; padding-left: 16px; color: #c4c4cc; font-size: 13px; line-height: 1.7; }
.edge-context { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--basalt); display: flex; flex-direction: column; gap: 16px; }
.edge-context-row { display: flex; flex-wrap: wrap; gap: 28px; }
.edge-ctx { min-width: 0; }
.edge-ctx-wide { width: 100%; }
.edge-ctx-k { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; color: #8a8a93; margin-bottom: 6px; text-transform: uppercase; }
.edge-ctx-v { font-size: 13px; color: #d8d8de; }
.edge-ctx-dim { color: #8a8a93; }
.edge-weather { display: flex; gap: 14px; }
/* plain-language takeaway: every chart leads with its insight */
.edge-take { font-size: 12.5px; line-height: 1.45; color: #d8d8de; margin: 0 0 9px; display: flex; gap: 7px; align-items: baseline; }
.edge-take::before { content: ""; flex: 0 0 auto; width: 6px; height: 6px; margin-top: 5px; background: var(--signal); }
.edge-take b { color: var(--ash); font-weight: 800; }
.edge-take .mono { color: var(--ash); }
/* self-explaining legends + summary-stat chips */
.edge-legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; font-size: 10.5px; color: var(--silver); }
.edge-legend span { display: inline-flex; align-items: center; gap: 5px; }
.edge-lg-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.edge-lg-ring { width: 9px; height: 9px; border-radius: 50%; border: 1.4px solid #5a5a64; display: inline-block; }
.edge-lg-sq { width: 9px; height: 9px; display: inline-block; }
.edge-stats { display: flex; flex-wrap: wrap; margin-top: 10px; border: 1px solid var(--basalt); }
.edge-stat { flex: 1; min-width: 64px; padding: 7px 9px; border-right: 1px solid var(--basalt); }
.edge-stat:last-child { border-right: 0; }
.edge-stat-v { font-size: 15px; font-weight: 800; font-variant-numeric: tabular-nums; font-family: var(--font-mono), ui-monospace, monospace; line-height: 1; }
.edge-stat-l { font-size: 9.5px; color: var(--fog); letter-spacing: 0.03em; margin-top: 3px; text-transform: uppercase; }
.edge-spark { width: 100%; max-width: 360px; height: auto; display: block; }
.edge-spray { width: 100%; max-width: 200px; height: auto; display: block; }
.edge-viz { width: 100%; max-width: 340px; height: auto; display: block; }
.edge-viz-sq { width: 100%; max-width: 180px; height: auto; display: block; }
.edge-viz-row { display: flex; flex-wrap: wrap; gap: 28px; }
.edge-velo { display: flex; flex-direction: column; gap: 7px; max-width: 340px; }
.edge-velo-row { display: grid; grid-template-columns: 92px 1fr 76px; align-items: center; gap: 9px; font-size: 11.5px; }
.edge-velo-name { display: flex; align-items: center; gap: 6px; min-width: 0; }
.edge-velo-name b { font-weight: 800; font-size: 11px; }
.edge-velo-name span { color: var(--fog); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.edge-velo-bar { height: 13px; background: var(--obsidian); position: relative; border: 1px solid var(--basalt); }
.edge-velo-bar > span { position: absolute; left: 0; top: 0; bottom: 0; }
.edge-velo-num { text-align: right; }
.edge-velo-v { display: block; font-family: var(--font-mono), ui-monospace, monospace; font-variant-numeric: tabular-nums; color: #e8e8ee; font-weight: 700; font-size: 12px; }
.edge-velo-v small { color: var(--fog); font-weight: 400; }
.edge-velo-use { display: block; font-size: 9px; color: var(--fog); margin-top: 1px; }
.edge-zone-wrap { display: flex; gap: 12px; align-items: flex-start; }
.edge-zone-side { display: flex; flex-direction: column; justify-content: space-between; font-size: 9px; color: var(--fog); height: 106px; }
.edge-zone { display: grid; grid-template-columns: repeat(3, 34px); grid-auto-rows: 34px; gap: 2px; }
.edge-zone-cell { display: flex; align-items: center; justify-content: center; font-size: 10px;
  font-weight: 800; color: #0b0b0d; font-variant-numeric: tabular-nums; font-family: var(--font-mono), monospace; }
.edge-zone-cell.is-hot { outline: 1.5px solid var(--signal); outline-offset: -1.5px; }
.edge-zone-x { display: flex; justify-content: space-between; font-size: 9px; color: var(--fog); margin-top: 3px; }
.edge-zone-scale { display: flex; align-items: center; gap: 6px; font-size: 9.5px; color: var(--silver); margin-top: 8px; }
.edge-zone-ramp { display: inline-flex; gap: 1px; }
.edge-zone-ramp i { width: 13px; height: 9px; display: block; }
.edge-controls { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 18px;
  padding: 10px 12px; border: 1px solid var(--basalt); background: #0d0d10; }
.edge-toggle { background: transparent; color: #9aa0aa; border: 1px solid var(--iron); padding: 6px 12px;
  font-size: 12px; font-weight: 700; cursor: pointer; transition: color .16s, border-color .16s, background .16s; }
.edge-toggle.is-on { background: var(--signal); color: #0b0b0d; border-color: var(--signal); }
.edge-slider { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: #9aa0aa; min-width: 150px; }
.edge-slider strong { color: var(--signal); font-variant-numeric: tabular-nums; }
.edge-slider input { accent-color: var(--signal); }
.edge-select { display: flex; align-items: center; gap: 7px; font-size: 12px; }
.edge-select-k { color: #9aa0aa; }
.edge-select select { background: var(--graphite); color: var(--ash); border: 1px solid var(--iron); padding: 5px 8px; font-size: 12px; }
.edge-clear { background: transparent; color: #8a8a93; border: 0; font-size: 12px; cursor: pointer; text-decoration: underline; }
.edge-empty { border: 1px solid var(--basalt); background: #0d0d10; padding: 40px 24px; text-align: center; }
.edge-empty-h { font-size: 16px; font-weight: 700; margin: 0 0 6px; }
.edge-empty-s { color: #9aa0aa; font-size: 13px; margin: 0; }
.edge-link { color: var(--signal); }

/* -- cockpit: mode switch -- */
.edge-mode { display: inline-flex; gap: 4px; padding: 4px; border: 1px solid var(--basalt); border-radius: var(--radius-pill); background: var(--graphite); margin-bottom: 16px; }
.edge-modebtn { display: inline-flex; align-items: center; gap: 7px; background: transparent; color: var(--silver); border: none; padding: 7px 16px; border-radius: var(--radius-pill); font-family: var(--font-text), sans-serif; font-size: 13px; font-weight: 600; cursor: pointer; transition: background var(--dur) var(--ease-out), color var(--dur) var(--ease-out); }
.edge-modebtn.is-active { background: var(--win-wash); color: var(--signal); }
.edge-modebtn .edge-filter-n { background: color-mix(in oklab, var(--silver) 14%, var(--carbon)); color: var(--silver); border-radius: var(--radius-pill); padding: 1px 7px; font-size: 10px; }
.edge-modebtn.is-active .edge-filter-n { background: color-mix(in oklab, var(--signal) 20%, var(--carbon)); color: var(--signal); }

/* -- cockpit: bankroll + search + markets -- */
.edge-bank input { width: 84px; background: var(--graphite); color: var(--ash); border: 1px solid var(--iron); border-radius: var(--radius-sm); padding: 5px 8px; font-size: 12px; font-family: var(--font-mono), monospace; }
.edge-search { background: var(--graphite); color: var(--ash); border: 1px solid var(--iron); border-radius: var(--radius); padding: 6px 12px; font-size: 12px; min-width: 190px; flex: 1 1 190px; max-width: 280px; }
.edge-search::placeholder { color: var(--fog); }
.edge-markets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
.edge-mchip { background: var(--graphite); color: var(--silver); border: 1px solid var(--basalt); border-radius: var(--radius-pill); padding: 5px 12px; font-family: var(--font-mono), monospace; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; cursor: pointer; transition: all var(--dur) var(--ease-out); }

/* -- scorecard: tags / status / position -- */
.edge-detail-tags { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.edge-pos { font-family: var(--font-mono), monospace; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; color: var(--silver); border: 1px solid var(--basalt); border-radius: var(--radius-sm); padding: 2px 6px; }
.edge-status { display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-text), sans-serif; font-size: 9.5px; font-weight: 600; letter-spacing: 0.04em; border: 1px solid var(--basalt); border-radius: var(--radius-pill); padding: 2px 8px; white-space: nowrap; }
.edge-row-name { display: inline-flex; align-items: center; gap: 8px; }
.edge-row-name .edge-status { font-size: 9px; padding: 1px 6px; }

/* -- scorecard: season line -- */
.edge-season { display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--basalt); border-radius: var(--radius); background: var(--obsidian); padding: 12px 14px; margin-bottom: 14px; }
.edge-season-row { display: flex; flex-wrap: wrap; gap: 18px; }
.edge-season-cell { display: flex; flex-direction: column; gap: 2px; }
.edge-season-k { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--fog); }
.edge-season-v { font-family: var(--font-mono), monospace; font-size: 16px; font-weight: 700; color: var(--chalk); }
.edge-season-x { display: flex; align-items: center; gap: 12px; border-top: 1px solid var(--basalt); padding-top: 8px; }
.edge-season-xk { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--steel); flex-shrink: 0; }
.edge-season-x .edge-season-v { font-size: 13px; }

/* -- scorecard: editable odds + Kelly -- */
.edge-read-odds { align-items: center; }
.edge-odds-in { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; }
.edge-odds-field { width: 64px; background: var(--graphite); color: var(--chalk); border: 1px solid var(--steel); border-radius: var(--radius-sm); padding: 3px 7px; font-family: var(--font-mono), monospace; font-size: 14px; font-weight: 700; text-align: right; }
.edge-odds-field:focus-visible { outline: none; border-color: var(--signal); box-shadow: 0 0 0 2px color-mix(in oklab, var(--signal) 25%, transparent); }
.edge-odds-imp { font-family: var(--font-mono), monospace; font-size: 10px; color: var(--fog); white-space: nowrap; }

/* -- scorecard: note pills -- */
.edge-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.edge-note-pill { font-family: var(--font-text), sans-serif; font-size: 11px; font-weight: 500; border: 1px solid var(--basalt); border-radius: var(--radius-pill); padding: 4px 10px; }

@media (prefers-reduced-motion: reduce) {
  .edge-live-dot { animation: none; }
  .edge-panel { animation: none; }
  .edge-row, .edge-sport, .edge-filter { transition: none; }
}
`}</style>
  )
}
