import Link from 'next/link'
import { apiGetStats, apiGetSparkline } from '@/lib/betting-api'
import { HeroSparkline } from '@/components/landing/hero-sparkline'
import { PICK_SYSTEMS } from '@/lib/pick-systems'

const B = '0.5px solid #1f1f24'

async function getStats() {
  try {
    const raw = await apiGetStats().then(s => s.overall)
    return {
      total_bets: raw.total_bets,
      win_rate:   parseFloat(raw.win_rate).toFixed(1),
      roi:        parseFloat(raw.roi).toFixed(2),
      avg_edge:   parseFloat(raw.avg_edge).toFixed(1),
    }
  } catch {
    return { total_bets: '--', win_rate: '--', roi: '--', avg_edge: '--' }
  }
}

export async function Hero() {
  const [stats, sparkline] = await Promise.all([
    getStats(),
    apiGetSparkline(30).catch(() => []),
  ])
  const roiAvail = stats.roi !== '--'
  const roiNum = roiAvail ? parseFloat(stats.roi) : 0
  const roiPos = roiNum >= 0

  return (
    <section style={{ borderBottom: B, borderTop: B }}>
      <div className="hero-grid" style={{ gridTemplateColumns: 'minmax(0,7fr) minmax(0,5fr)' }}>

        {/* Left: headline + copy + CTAs */}
        <div className="hero-left" style={{ padding: '48px 20px 40px', borderRight: B }}>
          <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '12px' }}>
            Daily MLB card / score-ranked picks / {stats.total_bets} settled bets
          </div>
          <h1 className="hero-h1" style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.12, color: 'var(--text)', marginBottom: '12px' }}>
            Today&apos;s MLB card,<br />ranked by conviction.
          </h1>
          <p style={{ fontSize: '13px', lineHeight: 1.65, color: 'var(--sec)', marginBottom: '22px', maxWidth: '400px' }}>
            Beezy turns model edge, market price, and Kelly signal into a simple
            0-100 score so the best plays are easy to scan, screenshot, and track.
          </p>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <Link href="/cheat-sheet"
              className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '7px 16px', background: 'var(--accent)', color: 'var(--bg)', textDecoration: 'none' }}>
              Open Daily Card
            </Link>
            <Link href="/models"
              className="mono" style={{ fontSize: '11px', letterSpacing: '0.02em', padding: '7px 16px', border: B, color: 'var(--sec)', textDecoration: 'none' }}>
              View methodology
            </Link>
          </div>
        </div>

        {/* Right: 2x2 stat grid */}
        <div className="hero-stats" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <div style={{ padding: '24px 18px', borderBottom: B, borderRight: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Season ROI</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: roiAvail ? (roiPos ? 'var(--win)' : 'var(--loss)') : 'var(--muted)' }}>
              {roiAvail ? (roiPos ? '+' : '') + stats.roi + '%' : stats.roi}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>{stats.total_bets} settled bets</div>
          </div>
          <div style={{ padding: '24px 18px', borderBottom: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Win Rate</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--text)' }}>
              {stats.win_rate !== '--' ? stats.win_rate + '%' : stats.win_rate}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>W / (W+L)</div>
          </div>
          <div style={{ padding: '24px 18px', borderRight: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Settled Bets</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--text)' }}>
              {stats.total_bets}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>{PICK_SYSTEMS.length} systems &middot; MLB</div>
          </div>
          <div style={{ padding: '24px 18px' }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Avg Edge</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: stats.avg_edge !== '--' ? 'var(--win)' : 'var(--muted)' }}>
              {stats.avg_edge !== '--' ? '+' + stats.avg_edge + '%' : stats.avg_edge}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Model vs implied</div>
          </div>
          <HeroSparkline data={sparkline} />
        </div>

      </div>
    </section>
  )
}
