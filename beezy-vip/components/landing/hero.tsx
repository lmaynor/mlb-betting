import Link from 'next/link'
import { apiGetStats } from '@/lib/betting-api'

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
    <section className="border-b" style={{ borderColor: 'var(--border)' }}>
      <div
        className="grid md:grid-cols-[7fr_5fr]"
        style={{ borderColor: 'var(--border)' }}
      >
        {/* Left: headline + copy + CTAs */}
        <div
          className="px-5 py-10 md:py-14 md:border-r"
          style={{ borderColor: 'var(--border)' }}
        >
          {/* Eyebrow */}
          <div className="mono text-[10px] tracking-widest uppercase mb-3" style={{ color: 'var(--muted)' }}>
            5 models live &middot; Paper mode &middot; {stats.total_bets} settled bets
          </div>

          {/* H1 — confident, not screaming */}
          <h1
            className="font-semibold leading-tight mb-3"
            style={{ fontSize: 'clamp(26px, 3.5vw, 40px)', letterSpacing: '-0.02em', color: 'var(--text)' }}
          >
            MLB betting models<br className="hidden sm:block" /> that show their work.
          </h1>

          {/* Sub */}
          <p className="text-sm leading-relaxed mb-6 max-w-md" style={{ color: 'var(--sec)' }}>
            Five XGBoost models built on 946k+ pitch rows. Every bet logged.
            Every loss shown. Paid access opens after the first model clears a
            200-bet validation gate.
          </p>

          {/* CTAs */}
          <div className="flex flex-wrap gap-3">
            <a
              href="https://discord.gg/beezy"
              target="_blank"
              rel="noopener noreferrer"
              className="mono text-xs font-semibold tracking-widest uppercase px-5 py-2.5 transition-colors"
              style={{ background: 'var(--accent)', color: 'var(--bg)' }}
            >
              Join Discord
            </a>
            <Link
              href="/models"
              className="mono text-xs font-medium tracking-wide px-5 py-2.5 border transition-colors"
              style={{ borderColor: 'var(--border-bright)', color: 'var(--sec)' }}
            >
              View methodology &rarr;
            </Link>
          </div>
        </div>

        {/* Right: 2x2 stat grid */}
        <div className="grid grid-cols-2" style={{ borderColor: 'var(--border)' }}>
          {/* Season ROI */}
          <div
            className="p-5 md:p-6 border-b border-r"
            style={{ borderColor: 'var(--border)' }}
          >
            <div className="mono text-[9px] tracking-widest uppercase mb-2" style={{ color: 'var(--muted)' }}>
              Season ROI
            </div>
            <div
              className="mono font-semibold leading-none mb-1"
              style={{ fontSize: 'clamp(22px, 2.5vw, 30px)', color: roiPos ? 'var(--win)' : 'var(--loss)' }}
            >
              {roiPos ? '+' : ''}{stats.roi}%
            </div>
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{stats.total_bets} settled bets</div>
          </div>

          {/* Win Rate */}
          <div
            className="p-5 md:p-6 border-b"
            style={{ borderColor: 'var(--border)' }}
          >
            <div className="mono text-[9px] tracking-widest uppercase mb-2" style={{ color: 'var(--muted)' }}>
              Win Rate
            </div>
            <div
              className="mono font-semibold leading-none mb-1"
              style={{ fontSize: 'clamp(22px, 2.5vw, 30px)', color: 'var(--text)' }}
            >
              {stats.win_rate}%
            </div>
            <div className="text-xs" style={{ color: 'var(--muted)' }}>W / (W+L)</div>
          </div>

          {/* Settled Bets */}
          <div
            className="p-5 md:p-6 border-r"
            style={{ borderColor: 'var(--border)' }}
          >
            <div className="mono text-[9px] tracking-widest uppercase mb-2" style={{ color: 'var(--muted)' }}>
              Settled Bets
            </div>
            <div
              className="mono font-semibold leading-none mb-1"
              style={{ fontSize: 'clamp(22px, 2.5vw, 30px)', color: 'var(--text)' }}
            >
              {stats.total_bets}
            </div>
            <div className="text-xs" style={{ color: 'var(--muted)' }}>5 systems &middot; MLB</div>
          </div>

          {/* Avg Edge */}
          <div className="p-5 md:p-6">
            <div className="mono text-[9px] tracking-widest uppercase mb-2" style={{ color: 'var(--muted)' }}>
              Avg Edge
            </div>
            <div
              className="mono font-semibold leading-none mb-1"
              style={{ fontSize: 'clamp(22px, 2.5vw, 30px)', color: 'var(--win)' }}
            >
              +{stats.avg_edge}%
            </div>
            <div className="text-xs" style={{ color: 'var(--muted)' }}>Model vs implied</div>
          </div>
        </div>
      </div>
    </section>
  )
}
