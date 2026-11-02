'use client'

import { useState } from 'react'

const B = '1px solid var(--color-separator-border)'

export function CopyBetButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <button
      onClick={handleCopy}
      className="mono text-micro py-[3px] px-[9px] cursor-pointer"
      style={{
        color: copied ? 'var(--signal)' : 'var(--color-text-secondary)',
        border: copied ? '1px solid var(--win-border)' : B,
        borderRadius: 'var(--radius-sm)',
        background: copied ? 'var(--win-wash)' : 'transparent',
        transition: 'all 0.15s',
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
