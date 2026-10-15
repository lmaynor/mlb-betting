export const dynamic = 'force-dynamic'

import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Betting Tools',
  description: 'Odds and vig, Kelly and boost EV calculators, plus the daily slate and closing-line tracker.',
}

type Access = 'Free' | 'Pro' | 'Coming soon'

const TOOLS: { href: string; title: string; description: string; access: Access }[] = [
  { href: '/tools/odds-calculator',  title: 'Odds & Vig Calculator',  description: 'American odds for both sides to implied probability, vig and fair odds.', access: 'Free' },
  { href: '/tools/kelly-calculator', title: 'Kelly Calculator',       description: 'Bankroll, line and your win probability to edge and full or half Kelly stake.', access: 'Free' },
  { href: '/tools/boost-ev',         title: 'Boost EV Calculator',    description: "A boosted price against the market's normal two-sided prices to true expected value and Kelly stake.", access: 'Free' },
  { href: '/tools/slate',            title: "Today's Slate",     description: 'Every MLB game today with starters, start times and Beezy picks. Filter by system.', access: 'Pro' },
  { href: '/tools/clv-tracker',      title: 'CLV Tracker',            description: 'Each settled pick plotted by edge at bet time against closing line value.', access: 'Pro' },
  { href: '/tools/bet-tracker',      title: 'Personal Bet Tracker',   description: 'Log your own bets and compare your ROI to the model.', access: 'Coming soon' },
]

const ACCESS_COLOR: Record<Access, string> = { Free: 'var(--signal)', Pro: 'var(--lilac)', 'Coming soon': 'var(--fog)' }

const th: React.CSSProperties = { textAlign: 'left', padding: '9px 12px', fontSize: '10px', fontWeight: 600, letterSpacing: '0.06em', color: 'var(--fog)', background: '#11131a', borderBottom: '1px solid #1e2230' }
const td: React.CSSProperties = { padding: '12px', borderBottom: '1px solid #151822', verticalAlign: 'middle' }

export default function ToolsPage() {
  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Tools</h1>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>3 free calculators &middot; 2 Pro dashboards &middot; 1 coming soon</span>
      </div>
      <div style={{ overflowX: 'auto', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '560px' }}>
          <thead>
            <tr><th style={th}>TOOL</th><th style={th}>WHAT IT DOES</th><th style={th}>ACCESS</th></tr>
          </thead>
          <tbody>
            {TOOLS.map(t => (
              <tr key={t.href} className="sys-row">
                <td style={{ ...td, whiteSpace: 'nowrap' }}>
                  <Link href={t.href} style={{ color: 'var(--chalk)', fontWeight: 600, fontSize: '14px', textDecoration: 'none' }}>{t.title}</Link>
                </td>
                <td style={{ ...td, fontSize: '13px', color: 'var(--silver)', lineHeight: 1.5 }}>{t.description}</td>
                <td className="mono" style={{ ...td, fontSize: '11px', fontWeight: 600, color: ACCESS_COLOR[t.access], whiteSpace: 'nowrap' }}>{t.access.toUpperCase()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
