/**
 * The model's number against the line, drawn: the 10th-90th percentile range the model itself gives for the stat (a light bar), the model's
 * expected value (lambda, the bright tick) and the book line (the amber dashed rule). Where the tick sits relative to the rule is the whole
 * story: right of it the model leans over, left of it under; the bar's width is how unsure the model is. Quantiles are the model's own and are
 * not a validated interval. SVG attributes take literal colors (var() does not resolve there), hence the hex values.
 */
import type { BoardRow } from '@/lib/board-rows'

const W = 132
const H = 26
const PAD_X = 5
const GREEN = '#57F287'
const AMBER = '#facc15'
const SLATE = '#9ca3af'
const TRACK = '#2a2f3d'
const RANGE = '#38bdf8'

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2))

/** Whether the model's number sits on the listed side of the line (over: above it, under: below it). */
export const leansListedSide = (mean: number, line: number, side: 'over' | 'under' | null) => (side === 'under' ? mean < line : mean > line)

export function LambdaBar({ mean, p10, p90, line, side }: {
  mean: number | null; p10?: number | null; p90?: number | null; line: number | null; side: 'over' | 'under' | null
}) {
  if (mean == null) return <span className="text-text-secondary">--</span>
  const lo0 = Math.min(mean, p10 ?? mean, line ?? mean)
  const hi0 = Math.max(mean, p90 ?? mean, line ?? mean)
  const span = hi0 - lo0 || Math.max(Math.abs(mean) * 0.2, 1)
  const lo = lo0 - span * 0.1
  const hi = hi0 + span * 0.1
  const x = (v: number) => PAD_X + ((v - lo) / (hi - lo)) * (W - 2 * PAD_X)
  const agree = line != null && leansListedSide(mean, line, side)
  const tick = line == null ? SLATE : agree ? GREEN : SLATE
  const cy = H / 2
  const has = p10 != null && p90 != null && p90 >= p10
  const label = `Model expects ${fmt(mean)}${has ? `, 10th to 90th percentile ${fmt(p10!)} to ${fmt(p90!)}` : ''}${line != null ? `; the line is ${fmt(line)}` : ''}`

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block">
      <title>{label}</title>
      <line x1={PAD_X} x2={W - PAD_X} y1={cy} y2={cy} stroke={TRACK} strokeWidth={1} />
      {has && <rect x={x(p10!)} y={cy - 5} width={Math.max(x(p90!) - x(p10!), 2)} height={10} rx={5} fill={RANGE} fillOpacity={0.3} stroke={RANGE} strokeOpacity={0.55} strokeWidth={0.75} />}
      {line != null && <line x1={x(line)} x2={x(line)} y1={2} y2={H - 2} stroke={AMBER} strokeWidth={1.5} strokeDasharray="2.5 2" />}
      <rect x={x(mean) - 1.5} y={cy - 8} width={3} height={16} rx={1.5} fill={tick} />
    </svg>
  )
}

/** Two thin bars, the model's probability for the listed side over the market's fair probability: the gap is the model edge, drawn. */
export function ProbPair({ model, market }: { model: number | null; market: number | null }) {
  if (model == null && market == null) return <span className="text-text-secondary">--</span>
  const bar = (v: number | null, color: string, name: string) => (
    <span className="flex items-center gap-1.5">
      <span className="relative block h-[5px] w-[74px] rounded-full" style={{ background: TRACK }} aria-hidden>
        {v != null && <span className="absolute left-0 top-0 block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: color }} />}
      </span>
      <span className="mono text-micro text-neutral-300" title={`${name} probability for the listed side`}>{v == null ? '--' : `${(v * 100).toFixed(0)}%`}</span>
    </span>
  )
  const edge = model != null && market != null ? model - market : null
  return (
    <span className="inline-flex flex-col gap-[3px]" aria-label={`Model ${model == null ? 'n/a' : `${(model * 100).toFixed(0)}%`}, market ${market == null ? 'n/a' : `${(market * 100).toFixed(0)}%`}`}>
      {bar(model, edge != null && edge > 0 ? GREEN : SLATE, 'Model')}
      {bar(market, RANGE, 'Market')}
    </span>
  )
}

export const LAMBDA_LEGEND = [
  { color: RANGE, label: 'Model 10th-90th percentile' },
  { color: GREEN, label: 'Model expectation (lambda), on the listed side of the line' },
  { color: SLATE, label: 'Model expectation, not on the listed side' },
  { color: AMBER, label: 'Book line' },
] as const

/**
 * A game's expected score, drawn: the two teams' expected scoring (NHL: the model's goal rates, lambda; NBA: the total split by the modelled
 * margin) as one bar split in proportion. Wider away or home segment = the model expects that team to score more. Not a win probability.
 */
export function ExpectedScoreBar({ away, home, awayMean, homeMean, digits = 1 }: {
  away: string; home: string; awayMean: number | null | undefined; homeMean: number | null | undefined; digits?: number
}) {
  if (awayMean == null || homeMean == null || !isFinite(awayMean) || !isFinite(homeMean) || awayMean + homeMean <= 0) return null
  const share = awayMean / (awayMean + homeMean)
  const label = `Expected score: ${away} ${awayMean.toFixed(digits)}, ${home} ${homeMean.toFixed(digits)}`
  return (
    <div role="img" aria-label={label} title={label}>
      <div className="flex h-[8px] overflow-hidden rounded-full" style={{ background: TRACK }}>
        <span className="block h-full" style={{ width: `${share * 100}%`, background: '#f97316' }} />
        <span className="block h-full flex-1" style={{ background: RANGE }} />
      </div>
      <div className="mono mt-1 flex justify-between text-micro text-neutral-300">
        <span>{away} {awayMean.toFixed(digits)}</span>
        <span>{home} {homeMean.toFixed(digits)}</span>
      </div>
    </div>
  )
}
