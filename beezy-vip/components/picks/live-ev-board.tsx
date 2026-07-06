import type { EdgeAlert } from '@/lib/betting-api'
import { SYSTEM_COLOR, systemLabel } from '@/lib/tokens'

// odds_history market key -> registry system key (for color/label)
const MARKET_SYS: Record<string, string> = {
  hr_yn: 'HR', k_ou: 'K', outs_ou: 'OUTS',
  btb_ou: 'BATTER_TB', bhits_ou: 'BATTER_HITS',
  nrfi_ou: 'NRFI', game_ml: 'GAME', f5_ml: 'F5',
}

const fmtOdds = (o: number | null) => (o == null ? '?' : o > 0 ? `+${o}` : `${o}`)

/**
 * Live +EV board: soft lines the 15-minute scanner flagged today -- a book
 * priced meaningfully better than the cross-book fair line. This is the EV
 * strategy made visible on the site (transparency), distinct from the model
 * card below it.
 */
export function LiveEvBoard({ alerts }: { alerts: EdgeAlert[] }) {
  const rows = alerts
    .filter(a => a.ev != null && a.ev > 0)
    .sort((a, b) => (b.ev ?? 0) - (a.ev ?? 0))
    .slice(0, 8)
  if (rows.length === 0) return null

  return (
    <div style={{ marginBottom: '20px', border: '1px solid color-mix(in oklab, var(--warn) 40%, var(--carbon))', borderRadius: 'var(--radius-lg)', background: 'color-mix(in oklab, var(--warn) 6%, var(--carbon))', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', padding: '12px 16px 8px' }}>
        <span className="dell-heading" style={{ fontSize: '11px', letterSpacing: '0.1em', color: 'var(--warn)' }}>
          &#9889; LIVE +EV BOARD
        </span>
        <span className="times" style={{ fontSize: '12px', color: 'var(--fog)' }}>
          Books lagging the market consensus right now &mdash; scanned every 15 minutes. Soft lines correct fast.
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '640px' }}>
          <tbody>
            {rows.map((a, i) => {
              const sys = MARKET_SYS[a.market ?? ''] ?? a.market ?? '?'
              const color = SYSTEM_COLOR[sys] ?? 'var(--silver)'
              const who = a.player_name
                || (a.away_team && a.home_team ? `${a.away_team} @ ${a.home_team}` : `game ${a.game_pk ?? '?'}`)
              const game = a.player_name && a.away_team && a.home_team
                ? `${a.away_team}@${a.home_team}` : null
              return (
                <tr key={i} style={{ borderTop: '1px solid var(--basalt)' }}>
                  <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>
                    <span className="mono" style={{ fontSize: '10px', fontWeight: 800, color, letterSpacing: '0.04em' }}>{systemLabel(sys, true)}</span>
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: '13px', fontWeight: 700, color: 'var(--ash)', whiteSpace: 'nowrap' }}>
                    {who}
                    {game && <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginLeft: '8px' }}>{game}</span>}
                  </td>
                  <td className="mono" style={{ padding: '8px 10px', fontSize: '12px', color: 'var(--silver)', whiteSpace: 'nowrap' }}>
                    {a.selection ?? ''} {a.line ?? ''}
                  </td>
                  <td className="mono" style={{ padding: '8px 10px', fontSize: '12px', color: 'var(--ash)', whiteSpace: 'nowrap' }}>
                    {a.book} {fmtOdds(a.american)}
                  </td>
                  <td className="mono" style={{ padding: '8px 16px', fontSize: '13px', fontWeight: 800, color: 'var(--warn)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    +{((a.ev ?? 0) * 100).toFixed(1)}% EV{a.anchored ? ' · pinn' : ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
