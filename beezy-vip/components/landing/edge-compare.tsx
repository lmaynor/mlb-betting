import { apiGetTodayPicks } from '@/lib/betting-api'
import { collapseBooks } from '@/lib/dedupe'
import { SYSTEM_PILL, pickLabel } from '@/lib/tokens'
import { americanToImpliedProb } from '@/lib/odds'
import { Matchup } from '@/components/ui/matchup'
import type { Bet } from '@/lib/types'

const B = '1px solid var(--basalt)'

function fmtOdds(o: number) { return o > 0 ? `+${o}` : `${o}` }
function impliedPct(o: number) { return `${(americanToImpliedProb(o) * 100).toFixed(0)}%` }
function edgePct(e: number | null | undefined) {
  if (e == null) return null
  const pct = Math.abs(e) < 2 ? e * 100 : e
  return pct
}

// The pick described in plain terms (no player-name duplication).
function pickShort(bet: Bet): string {
  const label = pickLabel(bet)
  if (!bet.player) return label
  return label.replace(bet.player, '').replace(/^\s*\([^)]+\)\s*/, '').trim().replace(/^[-\s]+/, '') || label
}

export async function EdgeCompare() {
  let picks: Bet[] = []
  try {
    picks = await apiGetTodayPicks()
    picks = picks.filter(p => p.kelly_triggered && p.edge != null)
  } catch { /* fall through */ }
  // No fallback to settled bets: a section titled "today" must not show yesterday's results.
  if (picks.length === 0) return null

  const rows = collapseBooks([...picks].sort((a, b) => (edgePct(b.edge) ?? 0) - (edgePct(a.edge) ?? 0)))
    .slice(0, 4)

  return (
    <section style={{ padding: '56px 0 0' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '24px', gap: '16px' }}>
        <div>
          <h2 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>Best prices today</h2>
          <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginTop: '8px' }}>
            The price on offer next to the fair price, for today&rsquo;s biggest gaps.
          </p>
        </div>
        <a href="/picks" className="times" style={{ fontSize: '14px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}>
          All picks &rarr;
        </a>
      </div>

      <div style={{ border: B, borderRadius: 'var(--radius-lg)', overflow: 'hidden', background: 'var(--graphite)', boxShadow: 'var(--shadow-card)' }}>
        {/* column header */}
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center', padding: '11px 20px', background: 'var(--obsidian)', borderBottom: B }}>
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', flex: '1 1 240px' }}>Game &amp; pick</span>
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', width: '120px', textAlign: 'right' }}>Price</span>
          <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--signal)', width: '120px', textAlign: 'right' }}>Edge</span>
        </div>

        {rows.map((bet, i) => {
          const pill = SYSTEM_PILL[bet.system] ?? SYSTEM_PILL.ALL
          const e = edgePct(bet.edge)
          return (
            <div key={bet.id ?? i} style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'center', padding: '14px 20px', borderBottom: i < rows.length - 1 ? '1px solid #151822' : undefined }}>
              {/* matchup + pick */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', flex: '1 1 240px', minWidth: 0 }}>
                <Matchup away={bet.away_team} home={bet.home_team} size={18} fontSize="12px" color="var(--fog)" />
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                  <span className="dell-heading" style={{ flexShrink: 0, fontSize: '9px', letterSpacing: '0.05em', padding: '3px 7px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>
                    {bet.system}
                  </span>
                  <span style={{ fontSize: '13.5px', color: 'var(--ash)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {bet.player ? `${bet.player} · ${pickShort(bet)}` : pickShort(bet)}
                  </span>
                </div>
              </div>

              {/* market line */}
              <div style={{ width: '120px', textAlign: 'right' }}>
                <div className="mono" style={{ fontSize: '17px', fontWeight: 600, color: 'var(--chalk)', lineHeight: 1 }}>{fmtOdds(bet.odds)}</div>
                <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginTop: '4px' }}>{impliedPct(bet.odds)} implied</div>
              </div>

              {/* beezy edge */}
              <div style={{ width: '120px', textAlign: 'right', borderLeft: B, paddingLeft: '16px' }}>
                <div className="mono" style={{ fontSize: '17px', fontWeight: 700, color: e != null && e >= 0 ? 'var(--signal)' : 'var(--loss)', lineHeight: 1 }}>
                  {e != null ? `${e >= 0 ? '+' : ''}${e.toFixed(1)}%` : '—'}
                </div>
                <div className="mono" style={{ fontSize: '10px', color: 'var(--fog)', marginTop: '4px' }}>vs fair price</div>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
