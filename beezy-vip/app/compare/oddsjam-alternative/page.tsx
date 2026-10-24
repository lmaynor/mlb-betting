import type { Metadata } from 'next'
import { PLANS } from '@/lib/plans'

export const metadata: Metadata = {
  title: 'beezy.fyi vs OddsJam and other sports betting tools',
  description:
    `How beezy.fyi ($${PLANS.monthly.price}/mo All-Access Pro) differs from OddsJam, Props.cash, Outlier, Unabated, Pikkit and Betstamp: method, scope and results transparency.`,
  alternates: { canonical: '/compare/oddsjam-alternative' },
}

const th = { textAlign: 'left' as const, padding: '8px 10px', fontSize: '12px', color: '#7d8596', borderBottom: '1px solid #1e2230', position: 'sticky' as const, top: 0, background: '#11131a' }
const td = { padding: '8px 10px', fontSize: '13px', color: '#d1d5db', borderBottom: '1px solid #1e2230', verticalAlign: 'top' as const }

const ROWS: { tool: string; price: string; what: string; href?: string }[] = [
  { tool: 'beezy.fyi', price: `$${PLANS.single.price}/mo one league, $${PLANS.monthly.price}/mo all four, $${PLANS.annual.price}/yr (pre-launch)`, what: 'Market-consensus +EV scanner plus projection models; public paper-tracked ledger', href: '/pricing' },
  { tool: 'OddsJam', price: 'Positive EV Global $399.99/mo shown ($499 struck through); Sharp Money $6.60/day; Platinum $13.60 to $20/day', what: 'Odds comparison and +EV / arbitrage scanning across 100+ books; results are tracked by the user', href: 'https://oddsjam.com/subscribe' },
  { tool: 'Props.cash', price: '$19.99/mo or $199.99/yr, all sports', what: 'Prop research built around hit rates and historical trends', href: 'https://props.cash/' },
  { tool: 'Outlier', price: 'Free tier; Premium $19.99/mo; Premium+ $29.99/mo; Pro $79.99/mo', what: 'Prop insights, odds comparison and line movement; EV+ indicators and arbitrage feed on higher tiers', href: 'https://www.outlier.bet/#plans' },
  { tool: 'Unabated', price: 'Props+ $99/mo, Premium $199/mo, Concierge $799/mo', what: 'Market-implied line and fair-price tools aimed at sharp bettors', href: 'https://tools.unabated.com/pricing' },
  { tool: 'Pikkit', price: 'Free core app; Pro $29.99/mo or $199.99/yr', what: 'Bet tracking, line shopping and closing line value; it does not generate picks', href: 'https://pikkit.com/pro' },
  { tool: 'Betstamp', price: 'Free app; PRO Main $249/mo ($149/mo billed annually); Props and Live tiers contact sales', what: 'Odds comparison, bet tracking with ROI and CLV, and a pro odds screen with edge detection', href: 'https://www.betstamp.com/pro' },
]

export default function CompareOddsJamAlternativePage() {
  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', padding: '32px 20px 48px' }}>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#f3f4f6', margin: '0 0 16px' }}>beezy.fyi vs OddsJam and other sports betting tools</h1>
      <div className="article-body">
        <p>
          Sports betting tools fall into three groups: odds scanners, prop research tools and bet trackers. beezy.fyi is
          a scanner that also publishes its own projections and a public results ledger. Competitor prices below link to each vendor's
          page and are list prices as publicly listed in October 2026; some vendors price per day or toggle between
          monthly and annual billing, and so confirm current pricing with the vendor. Prices were read from each vendor's page on October 24, 2026.
        </p>
      </div>
      <div style={{ overflowX: 'auto', margin: '16px 0 24px', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Tool</th><th style={th}>Price</th><th style={th}>What it is</th></tr></thead>
          <tbody>
            {ROWS.map(r => (
              <tr key={r.tool}>
                <td style={{ ...td, fontWeight: 600 }}>{r.href ? <a href={r.href}>{r.tool}</a> : r.tool}</td>
                <td style={td}>{r.price}</td><td style={td}>{r.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="article-body">
        <h2>beezy.fyi vs OddsJam</h2>
        <p>
          Both flag prices that lag the market. beezy.fyi adds its own player projections and checks Kalshi and Novig
          alongside sportsbooks. Its flagged plays are logged to the public <a href="/results">results ledger</a> as paper
          bets, so the record can be checked before you rely on it.
        </p>
        <h2>beezy.fyi vs Props.cash and Outlier</h2>
        <p>
          Those tools center on trends, hit rates and odds comparison. beezy.fyi projects tonight&apos;s outcome from
          minutes, usage and official injury reports instead of recent hit rate.
        </p>
        <h2>beezy.fyi vs Unabated</h2>
        <p>
          Unabated is built for bettors who want to construct their own numbers. beezy.fyi delivers a filtered board of
          +EV prices and projections.
        </p>
        <h2>beezy.fyi vs Pikkit and Betstamp</h2>
        <p>
          Pikkit and Betstamp track bets, ROI and closing line value, and Betstamp also offers a pro odds screen. beezy.fyi differs
          by publishing its own projections and a public ledger of every flagged play.
        </p>
        <p>Prices and descriptions reflect each vendor's public pages as of October 2026 and may have changed. <a href="/pricing">beezy.fyi pricing</a>.</p>
      </div>
    </div>
  )
}
