'use client'
/**
 * The organiser console: who is in, their balances, the games, the invites, and the log of everything an organiser has done.
 * Dense tables and small inline forms, no cards. Every action goes to /api/contest/<slug>/admin/<op> (organiser role checked there)
 * and the page replaces its state with what the server returns. Nothing here can change a pick or a price.
 */
import { useState } from 'react'
import type { AdminState } from '@/lib/contest/backend'
import { fmtMoney, parseDollars } from '@/lib/contest/odds'

const TH: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: '#7d8596', background: '#11131a', padding: '8px 10px', borderBottom: '1px solid #1e2230', textAlign: 'left', whiteSpace: 'nowrap' }
const TD: React.CSSProperties = { fontSize: 13, color: '#f3f4f6', padding: '7px 10px', borderBottom: '1px solid #151822', verticalAlign: 'middle' }
const WRAP: React.CSSProperties = { background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, overflow: 'auto', marginBottom: 14 }
const FIELD: React.CSSProperties = { height: 44, padding: '0 10px', fontSize: 16, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6', minWidth: 0 }
const BTN: React.CSSProperties = { minHeight: 44, minWidth: 44, padding: '0 12px', fontSize: 12, fontWeight: 600, borderRadius: 6, border: '1px solid #2e3446', background: '#171a24', color: '#f3f4f6' }
const dt = (iso: string) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })

export function AdminConsole({ slug, initial }: { slug: string; initial: AdminState }) {
  const [s, setS] = useState(initial)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [rename, setRename] = useState<Record<number, string>>({})
  const [adjust, setAdjust] = useState<Record<number, { amt: string; note: string }>>({})
  const [scores, setScores] = useState<Record<number, { h: string; a: string }>>({})
  const [invite, setInvite] = useState({ code: '', uses: '10' })

  const act = async (op: string, body: unknown, done: string) => {
    setBusy(true)
    setMsg(null)
    try {
      const res = await fetch(`/api/contest/${slug}/admin/${op}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const json = await res.json().catch(() => ({}))
      if (res.ok) { setS(json as AdminState); setMsg(done) } else setMsg((json as { error?: { message?: string } }).error?.message || 'That did not go through.')
    } catch {
      setMsg('Could not reach the contest. Try again.')
    }
    setBusy(false)
  }
  const cents = (raw: string) => { const neg = raw.trim().startsWith('-'); const v = parseDollars(neg ? raw.trim().slice(1) : raw); return v == null ? null : neg ? -v : v }

  return (
    <div className="max-w-[1400px] mx-auto pt-4 px-4 sm:px-6 pb-16">
      <div className="flex flex-wrap items-center justify-between gap-3" style={{ marginBottom: 12 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{s.name} organiser</h1>
        <div className="flex items-center gap-3">
          <span className="mono" style={{ fontSize: 12, color: s.ledgerMismatches.length ? '#ef4444' : '#7d8596' }}>{s.ledgerMismatches.length ? `${s.ledgerMismatches.length} ledger mismatches` : 'Ledger balanced'}</span>
          <button type="button" disabled={busy} style={BTN} onClick={() => act('status', { status: s.status === 'open' ? 'closed' : 'open' }, s.status === 'open' ? 'Contest closed to new bets.' : 'Contest open.')}>
            {s.status === 'open' ? 'Close to new bets' : 'Reopen'}
          </button>
        </div>
      </div>
      <p role="status" className="mono" style={{ fontSize: 12, color: '#facc15', margin: '0 0 10px', minHeight: 16 }}>{msg}</p>

      <div style={WRAP}>
        <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Entries">
          <thead><tr><th style={TH}>Name</th><th style={TH}>User</th><th style={{ ...TH, textAlign: 'right' }}>Balance</th><th style={TH}>Status</th><th style={TH}>Rename</th><th style={TH}>Adjust balance</th></tr></thead>
          <tbody>
            {s.entries.map(e => (
              <tr key={e.entry_id} className="contest-row">
                <td style={TD}>{e.display_name}</td>
                <td className="mono" style={{ ...TD, color: '#7d8596', fontSize: 12 }}>{e.user_id.slice(0, 14)}</td>
                <td className="mono" style={{ ...TD, textAlign: 'right' }}>{fmtMoney(e.balance_cents)}</td>
                <td style={TD}>{e.status}</td>
                <td style={TD}>
                  <form className="flex gap-2" onSubmit={ev => { ev.preventDefault(); const n = (rename[e.entry_id] ?? '').trim(); if (n) void act('rename', { entryId: e.entry_id, displayName: n }, `Renamed to ${n}.`) }}>
                    <input aria-label={`New name for ${e.display_name}`} value={rename[e.entry_id] ?? ''} onChange={ev => setRename(r => ({ ...r, [e.entry_id]: ev.target.value }))} style={{ ...FIELD, width: 140 }} />
                    <button type="submit" disabled={busy || !(rename[e.entry_id] ?? '').trim()} style={BTN}>Rename</button>
                  </form>
                </td>
                <td style={TD}>
                  <form className="flex gap-2" onSubmit={ev => { ev.preventDefault(); const a = adjust[e.entry_id]; const c = a ? cents(a.amt) : null; if (c != null && a?.note.trim()) void act('adjust', { entryId: e.entry_id, cents: c, note: a.note }, `Adjusted ${e.display_name} by ${fmtMoney(c, { sign: true })}.`) }}>
                    <input aria-label={`Amount for ${e.display_name}`} placeholder="-50.00" inputMode="decimal" value={adjust[e.entry_id]?.amt ?? ''} onChange={ev => setAdjust(r => ({ ...r, [e.entry_id]: { amt: ev.target.value, note: r[e.entry_id]?.note ?? '' } }))} style={{ ...FIELD, width: 90 }} />
                    <input aria-label={`Reason for ${e.display_name}`} placeholder="Reason" value={adjust[e.entry_id]?.note ?? ''} onChange={ev => setAdjust(r => ({ ...r, [e.entry_id]: { amt: r[e.entry_id]?.amt ?? '', note: ev.target.value } }))} style={{ ...FIELD, width: 160 }} />
                    <button type="submit" disabled={busy} style={BTN}>Apply</button>
                  </form>
                </td>
              </tr>
            ))}
            {s.entries.length === 0 && <tr><td colSpan={6} style={{ ...TD, color: '#7d8596' }}>Nobody has joined yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={WRAP}>
        <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Games">
          <thead><tr><th style={TH}>Game</th><th style={TH}>Kickoff</th><th style={TH}>Status</th><th style={TH}>Settle by hand (home, away)</th><th style={TH}>Void</th></tr></thead>
          <tbody>
            {s.games.map(g => (
              <tr key={g.id} className="contest-row">
                <td style={TD}>{g.away} at {g.home}</td>
                <td className="mono" style={{ ...TD, fontSize: 12 }}>{dt(g.commence)}</td>
                <td className="mono" style={{ ...TD, fontSize: 12 }}>{g.status}{g.homeScore != null ? ` ${g.homeScore}-${g.awayScore}` : ''}</td>
                <td style={TD}>{g.status === 'scheduled' && (
                  <form className="flex gap-2" onSubmit={ev => { ev.preventDefault(); const sc = scores[g.id]; if (sc && sc.h !== '' && sc.a !== '') void act('settle-game', { gameId: g.id, homeScore: Number(sc.h), awayScore: Number(sc.a) }, `Settled ${g.away} at ${g.home} ${sc.h}-${sc.a}.`) }}>
                    <input aria-label={`Home score for ${g.home}`} inputMode="numeric" value={scores[g.id]?.h ?? ''} onChange={ev => setScores(r => ({ ...r, [g.id]: { h: ev.target.value.replace(/\D/g, ''), a: r[g.id]?.a ?? '' } }))} style={{ ...FIELD, width: 64 }} />
                    <input aria-label={`Away score for ${g.away}`} inputMode="numeric" value={scores[g.id]?.a ?? ''} onChange={ev => setScores(r => ({ ...r, [g.id]: { h: r[g.id]?.h ?? '', a: ev.target.value.replace(/\D/g, '') } }))} style={{ ...FIELD, width: 64 }} />
                    <button type="submit" disabled={busy} style={BTN}>Settle</button>
                  </form>
                )}</td>
                <td style={TD}>{g.status === 'scheduled' && <button type="button" disabled={busy} style={BTN} onClick={() => act('void-game', { gameId: g.id }, `Voided ${g.away} at ${g.home}; its bets are refunded.`)}>Void</button>}</td>
              </tr>
            ))}
            {s.games.length === 0 && <tr><td colSpan={5} style={{ ...TD, color: '#7d8596' }}>No games yet. Run a refresh.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={WRAP}>
        <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Invites">
          <thead><tr><th style={TH}>Code</th><th style={{ ...TH, textAlign: 'right' }}>Used</th><th style={TH}>New invite</th></tr></thead>
          <tbody>
            {s.invites.map(i => <tr key={i.code} className="contest-row"><td className="mono" style={TD}>{i.code}</td><td className="mono" style={{ ...TD, textAlign: 'right' }}>{i.uses} of {i.max_uses}</td><td style={TD} /></tr>)}
            <tr>
              <td colSpan={3} style={TD}>
                <form className="flex flex-wrap gap-2" onSubmit={ev => { ev.preventDefault(); if (invite.code.trim()) void act('invite', { code: invite.code.trim(), maxUses: Number(invite.uses) || 1 }, `Invite ${invite.code.trim()} created.`) }}>
                  <input aria-label="Invite code" placeholder="CODE" value={invite.code} onChange={ev => setInvite(i => ({ ...i, code: ev.target.value }))} className="mono" style={{ ...FIELD, width: 160 }} />
                  <input aria-label="Uses" inputMode="numeric" value={invite.uses} onChange={ev => setInvite(i => ({ ...i, uses: ev.target.value.replace(/\D/g, '') }))} style={{ ...FIELD, width: 72 }} />
                  <button type="submit" disabled={busy || !invite.code.trim()} style={BTN}>Create invite</button>
                </form>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div style={WRAP}>
        <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0 }} aria-label="Organiser log">
          <thead><tr><th style={TH}>When</th><th style={TH}>Who</th><th style={TH}>Action</th><th style={TH}>Detail</th></tr></thead>
          <tbody>
            {s.log.map((l, i) => <tr key={i}><td className="mono" style={{ ...TD, fontSize: 12 }}>{dt(l.ts)}</td><td className="mono" style={{ ...TD, fontSize: 12 }}>{l.actor.slice(0, 14)}</td><td style={TD}>{l.action}</td><td className="mono" style={{ ...TD, fontSize: 12, color: '#9ca3af' }}>{JSON.stringify(l.detail)}</td></tr>)}
            {s.log.length === 0 && <tr><td colSpan={4} style={{ ...TD, color: '#7d8596' }}>Nothing yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
