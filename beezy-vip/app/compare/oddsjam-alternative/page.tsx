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

const ROWS: [string, string, string][] = [
  ['beezy.fyi', `$${PLANS.single.price}/mo one league, $${PLANS.monthly.price}/mo all four`, 'Market-consensus +EV scanner plus projection models; public paper-tracked ledger'],
  ['OddsJam', 'Several tiers; see oddsjam.com', 'Odds comparison and +EV / arbitrage scanning across books; results are tracked by the user'],
  ['Props.cash, Outlier', 'See each site', 'Prop research built around hit rates and historical trends'],
  ['Unabated', 'See unabated.com', 'Market-implied line and fair-price tools aimed at sharp bettors'],
  ['Pikkit, Betstamp', 'See each site', 'Bet tracking and closing line value measurement; they do not generate picks'],
]

export default function CompareOddsJamAlternativePage() {
  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', padding: '32px 20px 48px' }}>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#f3f4f6', margin: '0 0 16px' }}>beezy.fyi vs OddsJam and other sports betting tools</h1>
      <div className="article-body">
        <p>
          Sports betting tools fall into three groups: odds scanners, prop research tools and bet trackers. beezy.fyi is
          a scanner that also publishes its own projections and a public results ledger. Competitor pricing changes often,
          so this page links to each vendor rather than quoting their prices.
        </p>
      </div>
      <div style={{ overflowX: 'auto', margin: '16px 0 24px', border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Tool</th><th style={th}>Price</th><th style={th}>What it is</th></tr></thead>
          <tbody>
            {ROWS.map(([a, b, c]) => (
              <tr key={a}><td style={{ ...td, fontWeight: 600 }}>{a}</td><td style={td}>{b}</td><td style={td}>{c}</td></tr>
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
          Those tools show how often a player has cleared a line recently. beezy.fyi projects tonight&apos;s outcome from
          minutes, usage and official injury reports instead of recent hit rate.
        </p>
        <h2>beezy.fyi vs Unabated</h2>
        <p>
          Unabated is built for bettors who want to construct their own numbers. beezy.fyi delivers a filtered board of
          +EV prices and projections.
        </p>
        <h2>beezy.fyi vs Pikkit and Betstamp</h2>
        <p>
          Pikkit and Betstamp track bets and closing line value. They are complements: beezy.fyi finds prices, they
          measure how your bets did.
        </p>
        <p>Comparisons reflect each product as publicly described, as of October 2026. <a href="/pricing">beezy.fyi pricing</a>.</p>
      </div>
    </div>
  )
}
