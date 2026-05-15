'use client'

import { useState, useEffect } from 'react'
import { kellyStake, americanToImpliedProb, formatOdds } from '@/lib/odds'

interface TrackedBet {
  id:        string
  date:      string
  system:    string
  pick:      string
  odds:      number
  stake:     number
  result:    'W' | 'L' | 'P' | 'PENDING'
  pnl:       number | null
  notes:     string
  followedBeezy: boolean
}

function calcPnl(odds: number, stake: number, result: string): number | null {
  if (result === 'PENDING') return null
  if (result === 'P') return 0
  if (result === 'L') return -stake
  const dec = odds > 0 ? odds / 100 : 100 / Math.abs(odds)
  return parseFloat((stake * dec).toFixed(2))
}

const SYSTEMS = ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'OTHER']
const EMPTY_FORM = { date: new Date().toISOString().split('T')[0], system: 'NRFI', pick: '', odds: '', stake: '', result: 'PENDING' as const, notes: '', followedBeezy: true }

export default function BetTrackerPage() {
  const [bets,    setBets]    = useState<TrackedBet[]>([])
  const [form,    setForm]    = useState(EMPTY_FORM)
  const [adding,  setAdding]  = useState(false)
  const [filter,  setFilter]  = useState<'all' | 'beezy' | 'own'>('all')

  // Persist to localStorage
  useEffect(() => {
    const saved = localStorage.getItem('beezy_tracked_bets')
    if (saved) {
      try { setBets(JSON.parse(saved)) } catch {}
    }
  }, [])

  function save(updated: TrackedBet[]) {
    setBets(updated)
    localStorage.setItem('beezy_tracked_bets', JSON.stringify(updated))
  }

  function addBet() {
    const odds  = parseFloat(String(form.odds))
    const stake = parseFloat(String(form.stake))
    if (!form.pick || isNaN(odds) || isNaN(stake)) return

    const pnl: number | null = calcPnl(odds, stake, form.result)
    const bet: TrackedBet = {
      id:            crypto.randomUUID(),
      date:          form.date,
      system:        form.system,
      pick:          form.pick,
      odds,
      stake,
      result:        form.result,
      pnl,
      notes:         form.notes,
      followedBeezy: form.followedBeezy,
    }
    save([bet, ...bets])
    setForm(EMPTY_FORM)
    setAdding(false)
  }

  function updateResult(id: string, result: TrackedBet['result']) {
    save(bets.map(b => {
      if (b.id !== id) return b
      return { ...b, result, pnl: calcPnl(b.odds, b.stake, result) }
    }))
  }

  function deleteBet(id: string) {
    save(bets.filter(b => b.id !== id))
  }

  const visible = bets.filter(b =>
    filter === 'all'   ? true :
    filter === 'beezy' ? b.followedBeezy :
    !b.followedBeezy
  )

  const settled   = visible.filter(b => b.result !== 'PENDING')
  const wins      = settled.filter(b => b.result === 'W').length
  const losses    = settled.filter(b => b.result === 'L').length
  const totalPnl  = settled.reduce((s, b) => s + (b.pnl ?? 0), 0)
  const winRate   = wins + losses > 0 ? (wins / (wins + losses) * 100).toFixed(1) : '—'

  const inputClass = 'bg-[var(--bg)] border border-[var(--border)] text-text mono text-xs px-3 py-2 focus:outline-none focus:border-accent placeholder:text-muted w-full'
  const selectClass = inputClass + ' cursor-pointer'

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Tools · Members</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Personal Bet Tracker</h1>
        <p className="text-muted text-sm">
          Log your bets, track ROI, and compare &quot;followed Beezy&quot; vs your own picks.
          Data stored locally in your browser.
        </p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-6">
        {[
          { label: 'Total Bets',  value: String(visible.length) },
          { label: 'Win Rate',    value: `${winRate}%` },
          { label: 'Total P&L',   value: settled.length > 0 ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}u` : '—', accent: totalPnl > 0 },
          { label: 'Record',      value: settled.length > 0 ? `${wins}-${losses}` : '—' },
        ].map(s => (
          <div key={s.label} className="p-4 border-r last:border-r-0 border-[var(--border)]">
            <p className="mono text-xs text-muted uppercase tracking-widest mb-1">{s.label}</p>
            <p className={`mono text-2xl font-extrabold ${'accent' in s && s.accent ? 'text-win' : 'text-text'}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Controls */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-2">
          {(['all', 'beezy', 'own'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`mono text-xs uppercase tracking-widest px-3 py-1.5 border transition-colors ${
                filter === f
                  ? 'border-accent text-accent bg-accent/5'
                  : 'border-[var(--border)] text-muted hover:border-[var(--border-bright)]'
              }`}
            >
              {f === 'all' ? 'All' : f === 'beezy' ? 'Followed Beezy' : 'Own Picks'}
            </button>
          ))}
        </div>
        <button
          onClick={() => setAdding(!adding)}
          className="mono text-xs uppercase tracking-widest px-4 py-2 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
        >
          {adding ? 'Cancel' : '+ Add Bet'}
        </button>
      </div>

      {/* Add bet form */}
      {adding && (
        <div className="border border-accent/30 bg-accent/5 p-5 mb-6">
          <p className="mono text-xs uppercase tracking-widest text-accent mb-4">New Bet</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div>
              <label className="mono text-xs text-muted block mb-1">Date</label>
              <input type="date" value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} className={inputClass} />
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">System</label>
              <select value={form.system} onChange={e => setForm(f => ({ ...f, system: e.target.value }))} className={selectClass}>
                {SYSTEMS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">Pick</label>
              <input type="text" placeholder="NRFI · NYY @ BOS" value={form.pick} onChange={e => setForm(f => ({ ...f, pick: e.target.value }))} className={inputClass} />
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">Line (American)</label>
              <input type="number" placeholder="-115" value={form.odds} onChange={e => setForm(f => ({ ...f, odds: e.target.value }))} className={inputClass} />
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">Stake (units)</label>
              <input type="number" placeholder="1.0" step="0.01" value={form.stake} onChange={e => setForm(f => ({ ...f, stake: e.target.value }))} className={inputClass} />
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">Result</label>
              <select value={form.result} onChange={e => setForm(f => ({ ...f, result: e.target.value as typeof form.result }))} className={selectClass}>
                <option value="PENDING">Pending</option>
                <option value="W">Win</option>
                <option value="L">Loss</option>
                <option value="P">Push</option>
              </select>
            </div>
            <div>
              <label className="mono text-xs text-muted block mb-1">Notes</label>
              <input type="text" placeholder="Optional note" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} className={inputClass} />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.followedBeezy}
                  onChange={e => setForm(f => ({ ...f, followedBeezy: e.target.checked }))}
                  className="accent-[#00FF87]"
                />
                <span className="mono text-xs text-muted">Followed Beezy</span>
              </label>
            </div>
          </div>
          <button
            onClick={addBet}
            className="mono text-xs uppercase tracking-widest px-5 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
          >
            Add Bet
          </button>
        </div>
      )}

      {/* Bet list */}
      {visible.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center">
          <p className="mono text-xs text-muted">No bets logged yet. Add your first bet above.</p>
        </div>
      ) : (
        <div className="border border-[var(--border)] overflow-x-auto">
          <div
            className="grid min-w-[800px] border-b border-[var(--border)] bg-[var(--surface)]"
            style={{ gridTemplateColumns: '0.7fr 0.7fr 1.4fr 0.7fr 0.7fr 0.9fr 0.8fr 0.5fr' }}
          >
            {['Date', 'System', 'Pick', 'Odds', 'Stake', 'Result', 'P&L', ''].map(h => (
              <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>

          {visible.map(bet => (
            <div
              key={bet.id}
              className="grid min-w-[800px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
              style={{ gridTemplateColumns: '0.7fr 0.7fr 1.4fr 0.7fr 0.7fr 0.9fr 0.8fr 0.5fr' }}
            >
              <div className="px-3 py-3 mono text-xs text-muted">{bet.date}</div>
              <div className="px-3 py-3">
                <span className="mono text-xs font-semibold text-accent">{bet.system}</span>
                {bet.followedBeezy && (
                  <span className="mono text-xs text-muted ml-1">·B</span>
                )}
              </div>
              <div className="px-3 py-3 mono text-xs text-text truncate">{bet.pick}</div>
              <div className="px-3 py-3 mono text-xs text-text">{formatOdds(bet.odds)}</div>
              <div className="px-3 py-3 mono text-xs text-text">{bet.stake}u</div>
              <div className="px-3 py-3">
                <select
                  value={bet.result}
                  onChange={e => updateResult(bet.id, e.target.value as TrackedBet['result'])}
                  className={`mono text-xs border px-2 py-1 cursor-pointer focus:outline-none bg-[var(--bg)] ${
                    bet.result === 'W'       ? 'text-win  border-win/30'  :
                    bet.result === 'L'       ? 'text-loss border-loss/30' :
                    bet.result === 'PENDING' ? 'text-muted border-[var(--border)]' :
                    'text-muted border-[var(--border)]'
                  }`}
                >
                  <option value="PENDING">Pending</option>
                  <option value="W">Win</option>
                  <option value="L">Loss</option>
                  <option value="P">Push</option>
                </select>
              </div>
              <div className="px-3 py-3 mono text-sm font-semibold">
                {bet.pnl !== null ? (
                  <span className={bet.pnl >= 0 ? 'text-win' : 'text-loss'}>
                    {bet.pnl >= 0 ? '+' : ''}{bet.pnl.toFixed(2)}u
                  </span>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </div>
              <div className="px-3 py-3">
                <button
                  onClick={() => deleteBet(bet.id)}
                  className="mono text-xs text-muted hover:text-loss transition-colors"
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="mono text-xs text-muted mt-4">
        ·B = followed Beezy signal · Data stored in browser localStorage
      </p>
    </div>
  )
}
