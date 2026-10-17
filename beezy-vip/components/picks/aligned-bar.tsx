import type { CSSProperties, ReactNode } from 'react'

/**
 * A full-width control bar (day toggle, filters) whose content lines up with the page body below it: the bar's background and
 * hairline run edge to edge, the content sits in the same 1200px column with the same 24px gutter.
 */
export function AlignedBar({ children, style, role, label }: { children: ReactNode; style?: CSSProperties; role?: string; label?: string }) {
  return (
    <div role={role} aria-label={label} style={{ borderBottom: '1px solid var(--basalt)', background: 'var(--carbon)' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '10px 24px', ...style }}>{children}</div>
    </div>
  )
}
