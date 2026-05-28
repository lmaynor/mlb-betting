export function XMark({ size = 18, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        fill={color}
        d="M18.2 2.25h3.31l-7.23 8.26 8.5 11.24h-6.66l-5.22-6.82-5.97 6.82H1.62l7.73-8.84L1.2 2.25h6.83l4.72 6.24 5.45-6.24Zm-1.16 17.52h1.83L7.03 4.13H5.06l11.98 15.64Z"
      />
    </svg>
  )
}
