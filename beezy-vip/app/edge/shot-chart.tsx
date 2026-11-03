'use client'

/**
 * /edge NBA shot chart: where the flagged player shoots and how well, against the league, plus the opponent's defence.
 *
 * Three views of one half court (all real SVG, ~10 units per foot, hex colours because var() does not resolve in SVG paint):
 *   Shots    every attempt: makes (green rings), misses (red x), with FG%.
 *   Hex      cell size = how often he shoots from there (three percentile bins), cell colour = his FG% minus the league's in
 *            that cell, on a six-step diverging scale (breaks -9 / -3 / 0 / +3 / +9 points). Small samples are shrunk toward
 *            the league rate (5 pseudo-attempts) so a 1-for-1 cell is not drawn as +50.
 *   Zones    the fourteen classic zones, tinted and labelled with FG% and the difference vs the league.
 * Under the chart: zone / shot-type tables with attempts, home/away splits, the last games' shot volume, and the opponent's
 * defence by zone. Every rate carries its attempt count. ASCII only (escapes for typographic characters).
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { apiGetShotChart } from '@/lib/betting-api'
import type { CockpitRow, ShotChartOk, ShotChartResult, ShotGameRow } from '@/lib/types'
import {
  DIFF_COLORS, DIFF_LABELS, LINE, MAKE, MISS, VIEW_H, VIEW_W, courtPaths, diffColor, diffPoints, hexMarks, hexPoints, pct1,
  shotPaths, signed1, SIZE_FRACTION, sx, sy,
} from '@/lib/shot-chart'

type View = 'shots' | 'hex' | 'zones'
const VIEWS: { id: View; label: string }[] = [{ id: 'shots', label: 'Shots' }, { id: 'hex', label: 'Hex vs league' }, { id: 'zones', label: 'Zones' }]

const TEXT = '#d1d5db'
const MUTED = '#7d8596'
const EMPTY = '#1c2030'
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

// -- court ----------------------------------------------------------------------------------------------------------------
function Court({ children, label }: { children: ReactNode; label: string }) {
  const p = useMemo(courtPaths, [])
  return (
    <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={label} style={{ width: '100%', height: 'auto', display: 'block', background: '#0d0f16', borderRadius: '6px' }}>
      {children}
      <g fill="none" stroke={LINE} strokeWidth="1.5" strokeLinejoin="round" pointerEvents="none">
        <path d={p.boundary} /><path d={p.paint} /><path d={p.ftCircle} strokeDasharray="0" /><path d={p.restricted} /><path d={p.threePoint} />
        <path d={p.backboard} strokeWidth="2.5" /><path d={p.rim} stroke="#f59e0b" />
      </g>
    </svg>
  )
}

function ShotsView({ data }: { data: ShotChartOk }) {
  const paths = useMemo(() => shotPaths(data.shots), [data.shots])
  return (
    <Court label={`${data.player} ${data.label} shot map: ${data.fgm} of ${data.fga} made`}>
      <path d={paths.missed} fill="none" stroke={MISS} strokeWidth="1.1" opacity="0.7" strokeLinecap="round" />
      <path d={paths.made} fill="none" stroke={MAKE} strokeWidth="1.3" opacity="0.85" />
    </Court>
  )
}

function HexView({ data, onFocus }: { data: ShotChartOk; onFocus: (s: string) => void }) {
  const marks = useMemo(() => hexMarks(data.hexes, data.hex_r), [data.hexes, data.hex_r])
  return (
    <Court label={`${data.player} ${data.label} hex map: shot frequency and field-goal percentage against the league`}>
      {marks.map(h => (
        <polygon key={h.key} points={h.points} fill={h.fill} opacity="0.92" onMouseEnter={() => onFocus(hexText(h))} onClick={() => onFocus(hexText(h))} style={{ cursor: 'pointer' }} />
      ))}
    </Court>
  )
}

function hexText(h: { att: number; made: number; lAtt: number; lMade: number; diff: number }): string {
  return `${h.made} of ${h.att} (${pct1(h.made, h.att)}) here; league ${pct1(h.lMade, h.lAtt)} on ${h.lAtt.toLocaleString('en-US')}. Adjusted ${signed1(h.diff)} pts.`
}

function ZonesView({ data, onFocus }: { data: ShotChartOk; onFocus: (s: string) => void }) {
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
            <text x={x} y={y - 1} textAnchor="middle" fill="#ffffff" stroke="#0d0f16" strokeWidth="3" paintOrder="stroke" style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'monospace' }}>{pct0(z.fgm, z.fga)}</text>
            <text x={x} y={y + 13} textAnchor="middle" fill={fill[i]} stroke="#0d0f16" strokeWidth="3" paintOrder="stroke" style={{ fontSize: '11px', fontFamily: 'monospace' }}>{signed1(d)}</text>
          </g>
        )
      })}
    </Court>
  )
}

const pct0 = (m: number, a: number) => (a ? `${Math.round((m / a) * 100)}%` : '--')

function zoneText(z: { label: string; fga: number; fgm: number; lfga: number; lfgm: number }): string {
  return `${z.label}: ${z.fgm} of ${z.fga} (${pct1(z.fgm, z.fga)}); league ${pct1(z.lfgm, z.lfga)}. Adjusted ${signed1(diffPoints(z.fgm, z.fga, z.lfgm, z.lfga))} pts.`
}

// -- legends ------------------------------------------------------------------------------------------------------------
function DiffLegend({ children }: { children?: ReactNode }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-mini text-text-secondary">
      <span className="inline-flex items-center gap-[3px]" aria-label="Colour scale, player minus league field-goal percentage in points">
        <span className="mr-1">Worse</span>
        {DIFF_COLORS.map((c, i) => <i key={c} title={`${DIFF_LABELS[i]} pts`} style={{ display: 'inline-block', width: 16, height: 9, background: c, borderRadius: 2 }} />)}
        <span className="ml-1">Better than league</span>
      </span>
      {children}
    </div>
  )
}

function SizeLegend() {
  return (
    <span className="inline-flex items-center gap-1" aria-label="Cell size shows how often the player shoots from there">
      Size = attempts
      {SIZE_FRACTION.map(f => <svg key={f} width="14" height="14" viewBox="-8 -8 16 16" aria-hidden="true"><polygon fill="#7d8596" points={[0, 1, 2, 3, 4, 5].map(i => `${(8 * f * Math.cos((Math.PI / 180) * (60 * i - 30))).toFixed(1)},${(8 * f * Math.sin((Math.PI / 180) * (60 * i - 30))).toFixed(1)}`).join(' ')} /></svg>)}
    </span>
  )
}

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
  const rows = data.zones.filter(z => z.fga > 0).sort((a, b) => b.fga - a.fga).slice(0, 8).map(z => {
    const o = opp.zones.find(x => x.key === z.key)
    return { z, o }
  })
  const threeShare = opp.fga ? opp.fg3a / opp.fga : null
  const lgThree = opp.lfga ? opp.lfg3a / opp.lfga : null
  return (
    <div>
      <div className="mb-2 text-compact leading-[1.5] text-neutral-300">
        {opp.team} defence, {data.label}: {threeShare != null ? `${(threeShare * 100).toFixed(1)}% of attempts it faced were threes` : ''}{lgThree != null ? ` (league ${(lgThree * 100).toFixed(1)}%)` : ''}.
        Rows are {data.player.split(' ').slice(-1)[0]}&apos;s eight busiest zones.
      </div>
      <table className="w-full border-collapse" aria-label={`${opp.team} defence in the zones the player shoots from`}>
        <thead><tr><th className={TH}>Zone</th><th className={TH}>His share</th><th className={TH}>{opp.team} allows</th><th className={TH}>Lg</th><th className={TH}>Diff</th></tr></thead>
        <tbody>
          {rows.map(({ z, o }) => {
            const oa = o?.fga ?? 0
            const od = o && z.lfga ? (o.fgm / Math.max(o.fga, 1) - z.lfgm / z.lfga) * 100 : null
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
      <div className="mt-1.5 text-mini text-text-secondary">Diff is from the shooter&apos;s side: positive = {opp.team} gives up more than the league in that zone (raw, no shrinkage; thin rows are greyed).</div>
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
              <rect x={x + bw * 0.17} y={y(g.fg3a)} width={w} height={Math.max(H - padB - y(g.fg3a), 0)} fill="#38bdf8" rx="2" />
              <text x={x + bw / 2} y={y(g.fga) - 3} textAnchor="middle" fill={TEXT} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{g.fga}</text>
              <text x={x + bw / 2} y={H - 8} textAnchor="middle" fill={MUTED} style={{ fontSize: '10px', fontFamily: 'monospace' }}>{g.date.slice(5).replace('-', '/')}</text>
            </g>
          )
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-mini text-text-secondary">
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: '#4a5066', borderRadius: 2, marginRight: 5 }} />Field-goal attempts, avg {fgaAvg.toFixed(1)}</span>
        <span><i style={{ display: 'inline-block', width: 9, height: 9, background: '#38bdf8', borderRadius: 2, marginRight: 5 }} />Threes, avg {tpaAvg.toFixed(1)}</span>
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
  return (
    <div className="mono flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-body-2-regular" aria-label="Season shooting summary">
      {items.map(([k, v]) => <span key={k}><span className="text-text-secondary">{k} </span><b className="text-text-primary">{v}</b></span>)}
    </div>
  )
}

export function ShotChartBody({ data, onSeason }: { data: ShotChartOk; onSeason?: (y: number) => void }) {
  const [view, setView] = useState<View>('hex')
  const [focus, setFocus] = useState('')
  useEffect(() => setFocus(''), [view, data.season])
  const stale = Date.now() - new Date(`${data.last_game}T12:00:00Z`).getTime() > 120 * 86_400_000
  return (
    <div className="grid gap-3">
      <MetricStrip data={data} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Shot chart view" className="inline-flex gap-0.5 rounded-lg border border-separator-border bg-background-secondary-default p-[3px]">
          {VIEWS.map(v => (
            <button key={v.id} onClick={() => setView(v.id)} aria-pressed={view === v.id}
              className={`cursor-pointer whitespace-nowrap rounded-md border px-[10px] py-1.5 text-caption-1-semibold ${view === v.id ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-transparent bg-transparent text-text-secondary hover:bg-background-primary-hover'}`}>
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

      {view === 'shots' && <ShotsView data={data} />}
      {view === 'hex' && <HexView data={data} onFocus={setFocus} />}
      {view === 'zones' && <ZonesView data={data} onFocus={setFocus} />}

      {view === 'shots' ? (
        <div className="flex flex-wrap items-center gap-x-3 text-mini text-text-secondary">
          <span><i style={{ display: 'inline-block', width: 9, height: 9, border: `1.5px solid ${MAKE}`, borderRadius: 9, marginRight: 5 }} />Made {data.fgm}</span>
          <span><b style={{ color: MISS, marginRight: 5 }}>x</b>Missed {data.fga - data.fgm}</span>
        </div>
      ) : (
        <DiffLegend>{view === 'hex' && <SizeLegend />}</DiffLegend>
      )}
      <div role="status" aria-live="polite" className="mono min-h-[32px] text-mini leading-[1.5] text-text-secondary">
        {focus || (view === 'shots' ? `${data.fga} attempts, ${data.label} regular season.` : 'Tap a cell for its numbers. Differences are shrunk toward the league rate (5 pseudo-attempts).')}
      </div>
      {stale && (
        <div role="note" className="rounded-lg border border-separator-border bg-background-secondary-default px-3 py-2 text-compact leading-[1.5] text-neutral-300">
          Shot data ends {monthDay(data.last_game)}: this is {data.label}, not the current season. Shots from games since are not loaded yet.
        </div>
      )}

      <Section title="Zones" sub={`${data.label} \u00b7 league = all players`}><ZoneTable data={data} /></Section>
      <Section title="Shot types"><GroupTable data={data} /></Section>
      {data.opponent && <Section title="Opponent defence"><OpponentTable data={data} /></Section>}
      <Section title="Home and away"><VenueTable data={data} /></Section>
      {data.games.length >= 3 && <Section title={`Shot volume, last ${data.games.length} games`} sub={`through ${monthDay(data.games[data.games.length - 1].date)}`}><VolumeBars games={data.games} /></Section>}
      <div className="text-mini leading-[1.5] text-text-secondary">
        Regular-season attempts through {monthDay(data.last_game)}, heaves past 40 ft excluded. League = every player&apos;s attempts that season.
        A zone with fewer than {THIN} attempts is greyed: its rate is mostly noise.
      </div>
    </div>
  )
}

/** Fetches and renders the chart for one cockpit row. */
export function ShotChartPanel({ row }: { row: CockpitRow }) {
  const [season, setSeason] = useState<number | undefined>(undefined)
  const [res, setRes] = useState<ShotChartResult | 'error' | null>(null)
  const opp = useMemo(() => opponentOf(row.matchup, row.team), [row.matchup, row.team])
  const player = row.player

  useEffect(() => {
    if (!player) { setRes({ status: 'no_player' }); return }
    let live = true
    setRes(null)
    Promise.resolve()
      .then(() => apiGetShotChart({ player, season, opp }))
      .then(r => { if (live) setRes(r) })
      .catch(() => { if (live) setRes('error') })
    return () => { live = false }
  }, [player, season, opp])

  if (res == null) return <div className="py-1.5 text-body-2-regular text-text-secondary" role="status">Loading shot chart...</div>
  if (res === 'error') return <div className="py-1.5 text-body-2-regular text-text-secondary">The shot chart could not be loaded. The line and price above are still live.</div>
  if (res.status === 'locked') return <div className="py-1.5 text-body-2-regular text-text-secondary">Shot charts are part of full NBA prop research.</div>
  if (res.status !== 'ok') {
    return <div className="py-1.5 text-body-2-regular text-text-secondary">{res.status === 'no_data' ? 'Shot data is not available right now.' : `No shot data for ${player ?? 'this player'}: the loaded seasons do not include enough of their attempts (rookies and recent signings first).`}</div>
  }
  return <ShotChartBody data={res} onSeason={setSeason} />
}
