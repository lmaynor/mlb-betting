'use client'
/**
 * Joining a contest: the invite code and the name other players will see. Validates the name as the player types (the server has the
 * last word: uniqueness, rename count). Until the player has joined this is the whole page body.
 */
import { useState } from 'react'
import { NAME_MAX, validateName } from '@/lib/contest/names'

export type JoinResult = { ok: true } | { ok: false; message: string }

const FIELD: React.CSSProperties = { width: '100%', height: 44, padding: '0 12px', fontSize: 16, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }

export function JoinPanel({ contestName, suggested, note, onJoin }: { contestName: string; suggested: string; note?: string; onJoin: (input: { inviteCode: string; displayName: string }) => Promise<JoinResult> | JoinResult }) {
  const [code, setCode] = useState('')
  const [name, setName] = useState(suggested)
  const [busy, setBusy] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const check = validateName(name)
  const ready = check.ok && code.trim().length > 0 && !busy
  const hint = !check.ok && name.length > 0 ? check.message : serverError

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ready || !check.ok) return
    setBusy(true)
    setServerError(null)
    const r = await onJoin({ inviteCode: code.trim(), displayName: check.name })
    setBusy(false)
    if (!r.ok) setServerError(r.message)
  }

  return (
    <form onSubmit={submit} aria-label={`Join ${contestName}`} style={{ maxWidth: 420, background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, padding: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}>Join {contestName}</h2>
      <p style={{ fontSize: 13, color: '#9ca3af', margin: '0 0 14px' }}>Play money only. Enter your invite code and choose the name other players will see.</p>

      <label htmlFor="join-code" style={{ fontSize: 12, color: '#7d8596', display: 'block', marginBottom: 6 }}>Invite code</label>
      <input id="join-code" value={code} onChange={e => { setCode(e.target.value); setServerError(null) }} autoComplete="off" autoCapitalize="characters" className="mono" style={FIELD} />

      <label htmlFor="join-name" style={{ fontSize: 12, color: '#7d8596', display: 'block', margin: '14px 0 6px' }}>Display name</label>
      <input id="join-name" value={name} onChange={e => { setName(e.target.value); setServerError(null) }} maxLength={NAME_MAX + 10} autoComplete="nickname" aria-describedby="join-name-hint" aria-invalid={!check.ok && name.length > 0} style={FIELD} />
      <p id="join-name-hint" role="status" style={{ fontSize: 12, margin: '6px 0 0', minHeight: 16, color: hint ? '#facc15' : '#7d8596' }}>
        {hint ?? 'Shown on the leaderboard and next to your bets once their games kick off. You can change it up to 3 times.'}
      </p>

      <button type="submit" disabled={!ready} className="btn-primary" style={{ width: '100%', height: 44, marginTop: 14, fontSize: 14, fontWeight: 600, opacity: ready ? 1 : 0.45, cursor: ready ? 'pointer' : 'not-allowed' }}>
        {busy ? 'Joining' : 'Join contest'}
      </button>
      {note && <p style={{ fontSize: 12, color: '#7d8596', margin: '10px 0 0' }}>{note}</p>}
    </form>
  )
}
