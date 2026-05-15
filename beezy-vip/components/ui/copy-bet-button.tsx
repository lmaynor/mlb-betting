'use client'

import { useState } from 'react'

export function CopyBetButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Fallback for browsers that block clipboard
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="mono text-xs text-muted border border-[var(--border)] px-2 py-0.5 hover:border-accent hover:text-accent transition-colors"
    >
      {copied ? '✓' : 'Copy'}
    </button>
  )
}
