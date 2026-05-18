'use client'

import { useState } from 'react'

const B = '0.5px solid #1f1f24'

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
      className="mono"
      style={{
        fontSize: '10px', color: copied ? '#10b981' : '#71717a',
        border: copied ? '0.5px solid #0f6e56' : B,
        padding: '2px 8px', background: 'transparent',
        cursor: 'pointer', transition: 'all 0.15s',
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
