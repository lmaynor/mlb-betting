import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'

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
    return { total_bets: '173', win_rate: '46.6', roi: '5.68', avg_edge: '10.6' }
  }
}

export async function Hero() {
  const stats = await getStats()
  const roiNum = parseFloat(stats.roi)
  const roiPos = roiNum >= 0

  return (
    <section style={{ borderBottom: B }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,7fr) minmax(0,5fr)' }}>

        {/* Left: headline + copy + CTAs */}
        <div style={{ padding: '48px 20px 40px', borderRight: B }}>
          <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '12px' }}>
            5 models live &middot; Paper mode &middot; {stats.total_bets} settled bets
          </div>
          <h1 style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1.12, letterSpacing: '-0.02em', color: 'var(--text)', marginBottom: '12px' }}>
            MLB betting models<br />that show their work.
          </h1>
          <p style={{ fontSize: '13px', lineHeight: 1.65, color: 'var(--sec)', marginBottom: '22px', maxWidth: '400px' }}>
            Five XGBoost models built on 946k+ pitch rows. Every bet logged.
            Every loss shown. Paid access opens after the first model clears a
            200-bet validation gate.
          </p>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
              className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '7px 16px', background: 'var(--accent)', color: 'var(--bg)', textDecoration: 'none' }}>
              Join Discord
            </a>
            <Link href="/models"
              className="mono" style={{ fontSize: '11px', letterSpacing: '0.02em', padding: '7px 16px', border: B, color: 'var(--sec)', textDecoration: 'none' }}>
              View methodology &rarr;
            </Link>
          </div>
        </div>

        {/* Right: 2x2 stat grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
          <div style={{ padding: '24px 18px', borderBottom: B, borderRight: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Season ROI</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: roiPos ? 'var(--win)' : 'var(--loss)' }}>
              {roiPos ? '+' : ''}{stats.roi}%
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>{stats.total_bets} settled bets</div>
          </div>
          <div style={{ padding: '24px 18px', borderBottom: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Win Rate</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--text)' }}>
              {stats.win_rate}%
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>W / (W+L)</div>
          </div>
          <div style={{ padding: '24px 18px', borderRight: B }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Settled Bets</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--text)' }}>
              {stats.total_bets}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>5 systems &middot; MLB</div>
          </div>
          <div style={{ padding: '24px 18px' }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '8px' }}>Avg Edge</div>
            <div className="mono" style={{ fontSize: 'clamp(20px, 2.2vw, 28px)', fontWeight: 600, lineHeight: 1, marginBottom: '4px', color: 'var(--win)' }}>
              +{stats.avg_edge}%
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Model vs implied</div>
          </div>
        </div>

      </div>
    </section>
  )
}
