import type { CSSProperties } from 'react'
import {
  BRAND_CANVAS, BRAND_EMERALD, BRAND_MONO, BRAND_PATHS, BRAND_VIEWBOX_H, BRAND_VIEWBOX_W,
} from '@/lib/brand-paths'

/** The fin mark as an inline SVG: no network fetch, crisp at any size. Literal hex only (never var() in SVG attributes). */
export function BrandMark({ size = 28, color = BRAND_EMERALD, style, title }: {
  size?: number
  color?: string
  style?: CSSProperties
  title?: string
}) {
  const height = Math.round((size * BRAND_VIEWBOX_H) / BRAND_VIEWBOX_W)
  return (
    <svg
      width={size}
      height={height}
      viewBox={`0 0 ${BRAND_VIEWBOX_W} ${BRAND_VIEWBOX_H}`}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      style={{ display: 'block', flexShrink: 0, ...style }}
    >
      {BRAND_PATHS.map((d, i) => <path key={i} d={d} fill={color} />)}
    </svg>
  )
}

/** The mark on a dark obsidian badge, for square placements (app icon, avatar). The site chrome uses the bare fin. `size` is the badge edge in px. */
export function BrandTile({ size = 32, radius = 7 }: { size?: number; radius?: number }) {
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        width: `${size}px`, height: `${size}px`, boxSizing: 'border-box',
        background: BRAND_CANVAS, border: '1px solid rgba(87, 242, 135, 0.30)', borderRadius: `${radius}px`,
      }}
    >
      <BrandMark size={Math.round(size * 0.68)} color={BRAND_EMERALD} />
    </span>
  )
}

const WORDMARK = {
  sm: { mark: 22, font: 14, gap: 8 },
  md: { mark: 28, font: 18, gap: 9 },
  lg: { mark: 38, font: 26, gap: 12 },
} as const

/** The bare fin plus the lowercase "beezy.fyi" wordmark. `showDomain={false}` drops the ".fyi" suffix. */
export function BrandWordmark({ size = 'md', showDomain = true }: {
  size?: keyof typeof WORDMARK
  showDomain?: boolean
}) {
  const m = WORDMARK[size]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: `${m.gap}px`, lineHeight: 1 }}>
      <BrandMark size={m.mark} color={BRAND_EMERALD} />
      <span style={{ fontSize: `${m.font}px`, letterSpacing: '-0.03em', whiteSpace: 'nowrap' }}>
        <span style={{ color: BRAND_MONO, fontWeight: 800 }}>beezy</span>
        {showDomain && <span style={{ color: BRAND_EMERALD, fontWeight: 700 }}>.fyi</span>}
      </span>
    </span>
  )
}
