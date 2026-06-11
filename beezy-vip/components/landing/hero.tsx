import Link from 'next/link'
import { apiGetStats, apiGetSparkline } from '@/lib/betting-api'
import { HeroSparkline } from '@/components/landing/hero-sparkline'
import { PICK_SYSTEMS } from '@/lib/pick-systems'

const B = '1px solid #000'

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

        {/* Left: dark-green CTA panel */}
        <div
          className="hero-left"
          style={{ padding: '48px 24px 40px', borderRight: B, background: '#0e1f0d', position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}
        >
          {/* Eyebrow */}
          <div
            className="dell-heading"
            style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'rgba(255,255,255,0.7)', marginBottom: '14px' }}
          >
            MLB PICKS BACKED BY MACHINE LEARNING
          </div>

          {/* Main headline -- Arial Black display */}
          <h1
            className="dell-display hero-h1"
            style={{ fontSize: '32px', lineHeight: 1.0, color: '#fff', marginBottom: '16px' }}
          >
            The Sharpest MLB Plays,<br />Ranked Every Morning.
          </h1>

          {/* Body copy -- Georgia serif for 1996 feel */}
          <p
            className="times"
            style={{ fontSize: '15px', lineHeight: 1.6, color: '#fff', marginBottom: '28px', maxWidth: '380px' }}
          >
            Our models grade every game and prop, then rank them by a single
            0&ndash;100 Beezy Score. Open the card, see the edge, track every result.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            {/* Primary CTA -- Dell black button */}
            <Link
              href="/cheat-sheet"
              style={{
                fontFamily: 'Arial, Helvetica, sans-serif',
                fontSize: '11px',
                fontWeight: 700,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                padding: '9px 20px',
                background: '#fcc20f',
                color: '#000',
                textDecoration: 'none',
                border: '1px solid #000',
                display: 'inline-block',
              }}
            >
              Open Daily Card
            </Link>
            <Link
              href="/models"
              style={{
                fontFamily: 'Arial, Helvetica, sans-serif',
                fontSize: '11px',
                fontWeight: 700,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                padding: '9px 20px',
                background: 'transparent',
                color: '#fff',
                textDecoration: 'none',
                border: '1px solid #fff',
                display: 'inline-block',
              }}
            >
              View Methodology
            </Link>
          </div>

          {/* Settled-bets count -- in flow so it doesn't overlap on mobile */}
          <span
            className="dell-heading"
            style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'rgba(255,255,255,0.45)' }}
          >
            {stats.total_bets} SETTLED BETS &middot; {PICK_SYSTEMS.length} SYSTEMS
          </span>
          </div>
        </div>

        {/* Right: ribbon-card stat grid */}
        <div className="hero-stats" style={{ gridTemplateColumns: '1fr 1fr' }}>

          {/* Ribbon title bar */}
          <div
            style={{
              gridColumn: '1 / -1',
              background: '#0a0a0c',
              borderBottom: B,
              padding: '7px 16px',
            }}
          >
            <span
              className="dell-heading"
              style={{ fontSize: '10px', letterSpacing: '0.08em', color: '#a1a1aa' }}
            >
              SEASON PERFORMANCE
            </span>
          </div>

          {/* Season ROI */}
          <div style={{ padding: '22px 18px', borderBottom: B, borderRight: B, background: '#111114' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Season ROI</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: roiAvail ? (roiPos ? '#b3bd95' : '#d77a7a') : '#888890' }}>
              {roiAvail ? (roiPos ? '+' : '') + stats.roi + '%' : stats.roi}
            </div>
            <div className="times" style={{ fontSize: '12px', color: '#888890' }}>{stats.total_bets} settled bets</div>
          </div>

          {/* Win Rate */}
          <div style={{ padding: '22px 18px', borderBottom: B, background: '#111114' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Win Rate</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: '#f5f5f7' }}>
              {stats.win_rate !== '--' ? stats.win_rate + '%' : stats.win_rate}
            </div>
            <div className="times" style={{ fontSize: '12px', color: '#888890' }}>W / (W+L)</div>
          </div>

          {/* Settled Bets */}
          <div style={{ padding: '22px 18px', borderRight: B, background: '#0f1a14' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Settled Bets</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: '#f5f5f7' }}>
              {stats.total_bets}
            </div>
            <div className="times" style={{ fontSize: '12px', color: '#888890' }}>{PICK_SYSTEMS.length} systems &middot; MLB</div>
          </div>

          {/* Avg Edge */}
          <div style={{ padding: '22px 18px', background: '#0f1a14' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890', marginBottom: '8px' }}>Avg Edge</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: stats.avg_edge !== '--' ? '#b3bd95' : '#888890' }}>
              {stats.avg_edge !== '--' ? '+' + stats.avg_edge + '%' : stats.avg_edge}
            </div>
            <div className="times" style={{ fontSize: '12px', color: '#888890' }}>Model vs implied</div>
          </div>

          <HeroSparkline data={sparkline} />
        </div>

      </div>
    </section>
  )
}
