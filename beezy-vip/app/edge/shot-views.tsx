'use client'

/**
 * The half-court SVG views of the /edge NBA shot chart (one component each): shots, hex map, heat map, zones, matchup, assisted
 * baskets. Pure rendering over the payload; all numbers come from lib/shot-chart.ts. SVG paint uses literal hex (var() does not
 * resolve in SVG attributes). ASCII only.
 */
import { useMemo, type ReactNode } from 'react'
import type { ShotChartOk } from '@/lib/types'
import {
  DIFF_COLORS, HEAT_COLORS, LINE, MAKE, METRIC_SCALE, MISS, VIEW_H, VIEW_W, courtPaths, diffColor, diffPoints, heatCells, heatColor,
  hexMarks, hexPoints, opponentZoneDiff, pct1, shotPaths, signed1, SIZE_FRACTION, sx, sy, type HexMark, type HexMetric,
} from '@/lib/shot-chart'

const MUTED = '#7d8596'
const EMPTY = '#1c2030'
const SKY = '#38bdf8'

export const pct0 = (m: number, a: number) => (a ? `${Math.round((m / a) * 100)}%` : '--')

export function Court({ children, label }: { children: ReactNode; label: string }) {
  const p = useMemo(courtPaths, [])
  return (
    <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={label} style={{ width: '100%', height: 'auto', display: 'block', background: '#0d0f16', borderRadius: '6px' }}>
      {children}
      <g fill="none" stroke={LINE} strokeWidth="1.5" strokeLinejoin="round" pointerEvents="none">
        <path d={p.boundary} /><path d={p.paint} /><path d={p.ftCircle} /><path d={p.restricted} /><path d={p.threePoint} />
        <path d={p.backboard} strokeWidth="2.5" /><path d={p.rim} stroke="#f59e0b" />
      </g>
    </svg>
  )
}

export function ShotsView({ data }: { data: ShotChartOk }) {
  const paths = useMemo(() => shotPaths(data.shots), [data.shots])
  return (
    <Court label={`${data.player} ${data.label} shot map: ${data.fgm} of ${data.fga} made`}>
      <path d={paths.missed} fill="none" stroke={MISS} strokeWidth="1.1" opacity="0.7" strokeLinecap="round" />
      <path d={paths.made} fill="none" stroke={MAKE} strokeWidth="1.3" opacity="0.85" />
    </Court>
  )
}

export function hexText(h: HexMark, metric: HexMetric): string {
  const base = `${h.made} of ${h.att} (${pct1(h.made, h.att)}) here; league ${pct1(h.lMade, h.lAtt)} on ${h.lAtt.toLocaleString('en-US')}.`
  if (metric === 'pps') return `${base} ${h.att ? (h.pts / h.att).toFixed(2) : '--'} points per shot (league ${h.lAtt ? (h.lPts / h.lAtt).toFixed(2) : '--'}).`
  return `${base} Adjusted ${signed1(h.diff)} pts.`
}

export function HexView({ data, metric, onFocus }: { data: ShotChartOk; metric: HexMetric; onFocus: (s: string) => void }) {
  const marks = useMemo(() => hexMarks(data.hexes, data.hex_r, metric), [data.hexes, data.hex_r, metric])
  return (
    <Court label={`${data.player} ${data.label} hex map: shot frequency and ${metric === 'diff' ? 'field-goal percentage against the league' : METRIC_SCALE[metric].unit}`}>
      {marks.map(h => (
        <polygon key={h.key} points={h.points} fill={h.fill} opacity="0.92" onMouseEnter={() => onFocus(hexText(h, metric))} onClick={() => onFocus(hexText(h, metric))} style={{ cursor: 'pointer' }} />
      ))}
    </Court>
  )
}

export function HeatView({ data, skipRim }: { data: ShotChartOk; skipRim: boolean }) {
  const cells = useMemo(() => heatCells(data.shots, { skipRim }), [data.shots, skipRim])
  return (
    <Court label={`${data.player} ${data.label} heat map of shot attempts${skipRim ? ' without the restricted area' : ''}`}>
      {cells.map(c => <rect key={`${c.x},${c.y}`} x={sx(c.x + 1)} y={sy(c.y + 1)} width="10.4" height="10.4" fill={heatColor(c.v)} opacity={0.35 + 0.65 * c.v} />)}
    </Court>
  )
}

export function zoneText(z: { label: string; fga: number; fgm: number; lfga: number; lfgm: number }): string {
  return `${z.label}: ${z.fgm} of ${z.fga} (${pct1(z.fgm, z.fga)}); league ${pct1(z.lfgm, z.lfga)}. Adjusted ${signed1(diffPoints(z.fgm, z.fga, z.lfgm, z.lfga))} pts.`
}

export function ZonesView({ data, onFocus }: { data: ShotChartOk; onFocus: (s: string) => void }) {
  const fill = useMemo(() => data.zones.map(z => (z.fga ? diffColor(diffPoints(z.fgm, z.fga, z.lfgm, z.lfga)) : EMPTY)), [data.zones])
  return (
    <Court label={`${data.player} ${data.label} shooting by zone against the league`}>
      {data.grid.map(([q, r, zi]) => (zi >= 0 ? <polygon key={`${q},${r}`} points={hexPoints(q, r, data.hex_r, data.hex_r * 1.02)} fill={fill[zi]} opacity="0.55" /> : null))}
      {data.zones.map((z, i) => {
        if (!z.fga) return null
        const d = diffPoints(z.fgm, z.fga, z.lfgm, z.lfga)
        const x = Math.min(Math.max(sx(z.cx), 30), VIEW_W - 30), y = sy(z.cy)
        return (
          <g key={z.key} onMouseEnter={() => onFocus(zoneText(z))} onClick={() => onFocus(zoneText(z))} style={{ cursor: 'pointer' }}>
            <text x={x} y={y - 1} textAnchor="middle" fill="#ffffff" stroke="#0d0f16" strokeWidth="3" paintOrder="stroke" style={{ fontSize: '15px', fontWeight: 600, fontFamily: 'monospace' }}>{pct0(z.fgm, z.fga)}</text>
            <text x={x} y={y + 13} textAnchor="middle" fill={fill[i]} stroke="#0d0f16" strokeWidth="3" paintOrder="stroke" style={{ fontSize: '11px', fontFamily: 'monospace' }}>{signed1(d)}</text>
          </g>
        )
      })}
    </Court>
  )
}

/** His shot cells, sized by how often he shoots there and coloured by what the opponent allows in that zone against the league. */
export function MatchupView({ data, onFocus }: { data: ShotChartOk; onFocus: (s: string) => void }) {
  const opp = data.opponent
  const marks = useMemo(() => {
    if (!opp) return []
    return hexMarks(data.hexes, data.hex_r, 'diff', c => {
      const zi = c[8]
      const z = zi != null && zi >= 0 ? data.zones[zi] : null
      const d = z ? opponentZoneDiff(z, opp) : null
      return d == null ? EMPTY : diffColor(d)
    })
  }, [data.hexes, data.hex_r, data.zones, opp])
  if (!opp) return null
  const text = (h: HexMark) => {
    const z = h.zone != null && h.zone >= 0 ? data.zones[h.zone] : null
    const o = z ? opp.zones.find(x => x.key === z.key) : null
    if (!z || !o) return 'No opponent sample for this zone.'
    return `${z.label}: ${opp.team} allows ${pct1(o.fgm, o.fga)} on ${o.fga.toLocaleString('en-US')} attempts; league ${pct1(z.lfgm, z.lfga)}. He shot ${pct1(h.made, h.att)} from this cell (${h.att}).`
  }
  return (
    <Court label={`${data.player} shot cells coloured by what ${opp.team} allows in each zone`}>
      {marks.map(h => <polygon key={h.key} points={h.points} fill={h.fill} opacity="0.92" onMouseEnter={() => onFocus(text(h))} onClick={() => onFocus(text(h))} style={{ cursor: 'pointer' }} />)}
    </Court>
  )
}

/** Where the baskets he set up were scored. These are assisted makes, not potential assists (passes that missed are tracking data). */
export function AssistsView({ data }: { data: ShotChartOk }) {
  const a = data.assists
  const paths = useMemo(() => shotPaths((a?.shots ?? []).filter(s => (s[2] & 2) === 0), 2.6), [a])
  const threes = useMemo(() => shotPaths((a?.shots ?? []).filter(s => (s[2] & 2) === 2), 2.6), [a])
  return (
    <Court label={`${data.player} ${data.label}: where the ${a?.n ?? 0} baskets he set up were scored`}>
      <path d={paths.made} fill="none" stroke={SKY} strokeWidth="1.4" opacity="0.9" />
      <path d={threes.made} fill="none" stroke="#f59e0b" strokeWidth="1.4" opacity="0.95" />
    </Court>
  )
}

// -- legends ----------------------------------------------------------------------------------------------------------------
export function MetricLegend({ metric, children }: { metric: HexMetric; children?: ReactNode }) {
  const sc = METRIC_SCALE[metric]
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-mini text-text-secondary">
      <span className="inline-flex items-center gap-[3px]" aria-label={`Colour scale, ${sc.unit}`}>
        <span className="mr-1">Lower</span>
        {DIFF_COLORS.map((c, i) => <i key={c} title={sc.labels[i]} style={{ display: 'inline-block', width: 16, height: 9, background: c, borderRadius: 2 }} />)}
        <span className="ml-1">Higher</span>
      </span>
      {children}
    </div>
  )
}

export function HeatLegend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-mini text-text-secondary">
      <span className="inline-flex items-center gap-[3px]" aria-label="Colour scale, share of attempts">
        <span className="mr-1">Fewer shots</span>
        {HEAT_COLORS.map(c => <i key={c} style={{ display: 'inline-block', width: 16, height: 9, background: c, borderRadius: 2 }} />)}
        <span className="ml-1">More</span>
      </span>
    </div>
  )
}

export function SizeLegend() {
  return (
    <span className="inline-flex items-center gap-1" aria-label="Cell size shows how often the player shoots from there">
      Size = attempts
      {SIZE_FRACTION.map(f => <svg key={f} width="14" height="14" viewBox="-8 -8 16 16" aria-hidden="true"><polygon fill={MUTED} points={[0, 1, 2, 3, 4, 5].map(i => `${(8 * f * Math.cos((Math.PI / 180) * (60 * i - 30))).toFixed(1)},${(8 * f * Math.sin((Math.PI / 180) * (60 * i - 30))).toFixed(1)}`).join(' ')} /></svg>)}
    </span>
  )
}
