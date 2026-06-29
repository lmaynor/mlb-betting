import Link from 'next/link'
import { apiGetStats, apiGetSparkline } from '@/lib/betting-api'
import { HeroSparkline } from '@/components/landing/hero-sparkline'
import { PICK_SYSTEMS } from '@/lib/pick-systems'

const B = '1px solid var(--basalt)'

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
    <section className="reveal" style={{ marginTop: '36px' }}>
      <div className="hero-grid" style={{ border: B, borderRadius: 'var(--radius-xl)', overflow: 'hidden', background: 'var(--graphite)', boxShadow: 'var(--shadow-md)' }}>

        {/* Left: headline + CTAs */}
        <div
          className="hero-left"
          style={{ padding: '52px 44px', borderRight: B, position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'center', background: 'linear-gradient(180deg, color-mix(in oklab, var(--signal) 5%, var(--graphite)), var(--graphite))' }}
        >
          <div
            className="dell-heading"
            style={{ fontSize: '11px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '18px' }}
          >
            MLB &middot; MACHINE-LEARNED PICKS
          </div>

          <h1
            className="dell-display hero-h1"
            style={{ fontSize: '46px', lineHeight: 1.04, color: 'var(--chalk)', marginBottom: '18px', letterSpacing: '-0.025em' }}
          >
            The sharpest MLB plays, <span style={{ color: 'var(--signal)' }}>ranked every morning.</span>
          </h1>

          <p
            className="times"
            style={{ fontSize: '17px', lineHeight: 1.6, color: 'var(--silver)', marginBottom: '28px', maxWidth: '46ch' }}
          >
            Our models grade every game and prop, then rank them by a single
            0&ndash;100 Beezy Score. Open the card, see the edge, track every result.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
              <Link href="/cheat-sheet" className="btn btn-primary">
                Open today&rsquo;s card &rarr;
              </Link>
              <Link href="/models" className="btn btn-ghost">
                How it works
              </Link>
            </div>

            <span
              className="dell-heading"
              style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)' }}
            >
              {stats.total_bets} SETTLED BETS &middot; {PICK_SYSTEMS.length} SYSTEMS
            </span>
          </div>
        </div>

        {/* Right: stat grid */}
        <div className="hero-stats">

          <div style={{ gridColumn: '1 / -1', background: 'var(--obsidian)', borderBottom: B, padding: '10px 20px' }}>
            <span className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--silver)' }}>
              SEASON PERFORMANCE
            </span>
          </div>

          <div style={{ padding: '24px 20px', borderBottom: B, borderRight: B }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Season ROI</div>
            <div className="mono" style={{ fontSize: 'clamp(22px, 2.4vw, 30px)', fontWeight: 600, lineHeight: 1, marginBottom: '6px', color: roiAvail ? (roiPos ? 'var(--signal)' : 'var(--loss)') : 'var(--fog)' }}>
              {roiAvail ? (roiPos ? '+' : '') + stats.roi + '%' : stats.roi}
            </div>
            <div className="times" style={{ fontSize: '12px', color: 'var(--fog)' }}>{stats.total_bets} settled bets</div>
          </div>

          <div style={{ padding: '24px 20px', borderBottom: B }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Win Rate</div>
            <div className="mono" style={{ fontSize: 'clamp(22px, 2.4vw, 30px)', fontWeight: 600, lineHeight: 1, marginBottom: '6px', color: 'var(--chalk)' }}>
              {stats.win_rate !== '--' ? stats.win_rate + '%' : stats.win_rate}
            </div>
            <div className="times" style={{ fontSize: '12px', color: 'var(--fog)' }}>W / (W+L)</div>
          </div>

          <div style={{ padding: '24px 20px', borderRight: B }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Settled Bets</div>
            <div className="mono" style={{ fontSize: 'clamp(22px, 2.4vw, 30px)', fontWeight: 600, lineHeight: 1, marginBottom: '6px', color: 'var(--chalk)' }}>
              {stats.total_bets}
            </div>
            <div className="times" style={{ fontSize: '12px', color: 'var(--fog)' }}>{PICK_SYSTEMS.length} systems &middot; MLB</div>
          </div>

          <div style={{ padding: '24px 20px' }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>Avg Edge</div>
            <div className="mono" style={{ fontSize: 'clamp(22px, 2.4vw, 30px)', fontWeight: 600, lineHeight: 1, marginBottom: '6px', color: stats.avg_edge !== '--' ? 'var(--signal)' : 'var(--fog)' }}>
              {stats.avg_edge !== '--' ? '+' + stats.avg_edge + '%' : stats.avg_edge}
            </div>
            <div className="times" style={{ fontSize: '12px', color: 'var(--fog)' }}>Model vs implied</div>
          </div>

          <HeroSparkline data={sparkline} />
        </div>

      </div>
    </section>
  )
}
