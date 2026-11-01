/** A padlock as an inline SVG (literal hex: var() does not work in SVG attributes; no emoji or escaped glyph in the UI). */
export function LockGlyph({ size, color }: { size: number; color: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" role="img" aria-label="Locked" style={{ flexShrink: 0 }}>
      <rect x="3" y="7" width="10" height="7" rx="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
    </svg>
  )
}
