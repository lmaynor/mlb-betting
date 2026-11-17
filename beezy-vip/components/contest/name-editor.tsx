'use client'
/** "Playing as <name> - Edit name": an inline editor (no modal). The server counts renames; the page shows how many are left. */
import { useState } from 'react'
import { NAME_MAX, validateName } from '@/lib/contest/names'
import type { JoinResult } from './join-panel'

export function NameEditor({ name, renamesLeft, onRename }: { name: string; renamesLeft: number; onRename: (next: string) => Promise<JoinResult> | JoinResult }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)
  const [error, setError] = useState<string | null>(null)
  const check = validateName(draft)

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!check.ok) return
    const r = await onRename(check.name)
    if (r.ok) { setEditing(false); setError(null) } else setError(r.message)
  }

  if (!editing) {
    return (
      <span className="flex items-center gap-2">
        <span><span style={{ color: '#7d8596' }}>Playing as </span><b>{name}</b></span>
        {renamesLeft > 0
          ? <button type="button" onClick={() => { setDraft(name); setError(null); setEditing(true) }} className="contest-chip" style={{ fontSize: 12, minWidth: 44, minHeight: 44, color: '#9ca3af' }}>Edit name</button>
          : <span style={{ fontSize: 12, color: '#7d8596' }}>No renames left</span>}
      </span>
    )
  }
  return (
    <form onSubmit={save} aria-label="Edit display name" className="flex flex-wrap items-center gap-2">
      <label htmlFor="rename-input" style={{ fontSize: 12, color: '#7d8596' }}>Display name</label>
      <input id="rename-input" value={draft} onChange={e => { setDraft(e.target.value); setError(null) }} maxLength={NAME_MAX + 10} aria-invalid={!check.ok}
        style={{ height: 44, width: 180, padding: '0 10px', fontSize: 16, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }} />
      <button type="submit" disabled={!check.ok} className="btn-primary" style={{ minHeight: 44, minWidth: 64, fontSize: 12, fontWeight: 600, opacity: check.ok ? 1 : 0.45 }}>Save</button>
      <button type="button" onClick={() => setEditing(false)} className="btn-ghost" style={{ minHeight: 44, minWidth: 64, fontSize: 12, fontWeight: 600 }}>Cancel</button>
      <span role="status" style={{ fontSize: 12, color: '#facc15', flexBasis: '100%' }}>
        {error ?? (!check.ok ? check.message : `${renamesLeft} ${renamesLeft === 1 ? 'rename' : 'renames'} left. Changing the name changes it everywhere.`)}
      </span>
    </form>
  )
}
