import { sparkPath } from '@/lib/line-move'

/** A tiny line of `values` with a dot on the last one. Purely visual: the numbers beside it carry the meaning. `step` draws a staircase. */
export function Sparkline({ values, w = 64, h = 20, step = false, color = '#38bdf8', label }: { values: readonly number[]; w?: number; h?: number; step?: boolean; color?: string; label: string }) {
  const s = sparkPath(values, w, h, { step })
  if (!s) return <span className="inline-block" style={{ width: w, height: h }} aria-hidden />
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label} className="block flex-none" data-testid="sparkline">
      <path d={s.d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={s.last.x} cy={s.last.y} r={2.5} fill={color} />
    </svg>
  )
}
