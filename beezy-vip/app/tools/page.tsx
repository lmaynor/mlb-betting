export const revalidate = 60

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

const ACCESS_COLOR: Record<Access, string> = { Free: 'var(--signal)', Pro: 'var(--lilac)', 'Coming soon': 'var(--color-text-secondary)' }

const TH = 'border-b border-separator-border bg-background-secondary-default px-3 py-2.5 text-left font-semibold text-text-secondary'
const TD = 'border-b border-border-table p-3 align-middle'

export default function ToolsPage() {
  return (
    <div className="mx-auto max-w-[1000px] p-6">
      <div className="mb-3.5 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-title-2-bold text-text-primary">Tools</h1>
        <span className="mono text-caption-1-regular text-text-secondary">3 free calculators &middot; 2 Pro dashboards &middot; 1 coming soon</span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        <table className="w-full min-w-[560px] border-collapse">
          <thead>
            <tr><th className={TH}>Tool</th><th className={TH}>What it does</th><th className={TH}>Access</th></tr>
          </thead>
          <tbody>
            {TOOLS.map(t => (
              <tr key={t.href} className="sys-row">
                <td className={`${TD} whitespace-nowrap`}>
                  <Link href={t.href} className="text-body-semibold text-text-primary no-underline">{t.title}</Link>
                </td>
                <td className={`${TD} text-body-2-regular text-text-secondary`}>{t.description}</td>
                <td className={`mono ${TD} whitespace-nowrap text-caption-2-semibold`} style={{ color: ACCESS_COLOR[t.access] }}>{t.access}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
