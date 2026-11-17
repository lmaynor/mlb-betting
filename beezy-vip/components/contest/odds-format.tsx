'use client'
/** The player's chosen odds format (American, decimal, fractional), shared by the board, the slip and My Bets. Remembered in this browser only. */
import { createContext, useContext, useEffect, useState } from 'react'
import { fmtOdds, isOddsFormat, ODDS_FORMATS, type OddsFormat } from '@/lib/contest/format'

const KEY = 'contest-odds-format'
const Ctx = createContext<{ format: OddsFormat; setFormat: (f: OddsFormat) => void }>({ format: 'american', setFormat: () => {} })

export function OddsFormatProvider({ children }: { children: React.ReactNode }) {
  const [format, setFormat] = useState<OddsFormat>('american')
  // read after mount so the server render and the first client render agree
  useEffect(() => { try { const v = localStorage.getItem(KEY); if (isOddsFormat(v)) setFormat(v) } catch { /* private window: keep the default */ } }, [])
  const choose = (f: OddsFormat) => { setFormat(f); try { localStorage.setItem(KEY, f) } catch { /* not remembered */ } }
  return <Ctx.Provider value={{ format, setFormat: choose }}>{children}</Ctx.Provider>
}

export const useOddsFormat = () => useContext(Ctx)
/** `fmt(price)` in the player's format. */
export function useFmtPrice(): (p: number | null | undefined) => string {
  const { format } = useContext(Ctx)
  return p => fmtOdds(p, format)
}

export function OddsFormatSelect() {
  const { format, setFormat } = useOddsFormat()
  return (
    <label className="flex items-center gap-2" style={{ fontSize: 12, color: '#7d8596' }}>
      Odds
      <select aria-label="Odds format" value={format} onChange={e => setFormat(e.target.value as OddsFormat)} className="mono"
        style={{ height: 44, padding: '0 8px', fontSize: 12, borderRadius: 6, background: '#1b1f2b', border: '1px solid #2e3446', color: '#f3f4f6' }}>
        {ODDS_FORMATS.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
      </select>
    </label>
  )
}
