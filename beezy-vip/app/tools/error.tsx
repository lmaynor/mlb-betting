'use client'
export default function ToolsError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="p-[40px] text-center border border-[color:var(--color-separator-border)]">
      <p className="mono text-[12px] text-text-secondary mb-[12px]">Failed to load tool.</p>
      <button onClick={reset} className="mono text-[11px] text-[var(--link)] bg-transparent bg-none border-0 cursor-pointer tracking-[0.04em]">Try again &rarr;</button>
    </div>
  )
}
