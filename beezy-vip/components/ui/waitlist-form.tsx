'use client'

import { useState } from 'react'

// Pre-launch waitlist capture. The visitor types their own address into the
// site's form and submits -- that submission is the consent (single opt-in;
// the microcopy below says what they are signing up for). The address goes to
// /api/waitlist and is never stored in the browser or logged.
type Status = 'idle' | 'submitting' | 'done' | 'error'

export function WaitlistForm({ source, label = 'Join the waitlist' }: { source: string; label?: string }) {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [message, setMessage] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (status === 'submitting') return
    setStatus('submitting')
    setMessage('')
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), source }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(typeof data?.error === 'string' ? data.error : 'Something went wrong')
      setStatus('done')
      setMessage(
        data?.status === 'existing'
          ? "You're already on the list. We'll email you when paid access opens."
          : "You're on the list. We'll email you when paid access opens.",
      )
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof Error ? err.message : 'Something went wrong. Try again.')
    }
  }

  if (status === 'done') {
    return (
      <p role="status" className="text-body-regular text-signal">
        {message}
      </p>
    )
  }

  return (
    <form onSubmit={submit} noValidate>
      <div className="flex gap-2">
        <input
          type="email"
          required
          value={email}
          onChange={e => setEmail(e.target.value)}
          placeholder="you@email.com"
          aria-label="Email address"
          autoComplete="email"
          className="mono min-w-0 flex-1 px-3 py-[9px] text-cozy text-text-primary"
          style={{ background: 'var(--slate)', border: '1px solid var(--color-separator-border)', borderRadius: 'var(--radius)', outline: 'none' }}
        />
        <button type="submit" disabled={status === 'submitting'} className="btn btn-primary whitespace-nowrap">
          {status === 'submitting' ? 'Joining...' : label}
        </button>
      </div>
      {status === 'error' && (
        <p role="alert" className="mt-2 text-caption-1-regular text-[#ef4444]">
          {message}
        </p>
      )}
      <p className="mt-2 text-caption-1-regular text-text-secondary">
        Launch updates only. No spam, we never sell your address, and you can unsubscribe at any time.
      </p>
    </form>
  )
}
