import { StatCard } from '@/components/ui/primitives'
import { apiGetStats } from '@/lib/betting-api'

export async function StatsStrip() {
  let stats = { total_bets: '87', win_rate: '58.2', roi: '+11.4', avg_edge: '4.8' }

  try {
    const raw = await apiGetStats().then(s => s.overall)
    stats = {
      total_bets: raw.total_bets,
      win_rate:   raw.win_rate,
      roi:        raw.roi,
      avg_edge:   raw.avg_edge,
    }
  } catch {
    // Fall back to hardcoded seed data during dev/pre-DB
  }

  return (
    <section className="border-b border-[var(--border)]">
      <div className="max-w-7xl mx-auto grid grid-cols-2 md:grid-cols-4 divide-x divide-y md:divide-y-0 divide-[var(--border)]">
        <StatCard
          label="Season ROI"
          value={`${parseFloat(stats.roi) > 0 ? '+' : ''}${parseFloat(stats.roi).toFixed(1)}%`}
          sub="On all settled bets"
          accent
        />
        <StatCard
          label="Win Rate"
          value={`${parseFloat(stats.win_rate).toFixed(1)}%`}
          sub="W / (W+L)"
        />
        <StatCard
          label="Settled Bets"
          value={stats.total_bets}
          sub="5 systems · MLB"
        />
        <StatCard
          label="Avg Edge"
          value={`+${parseFloat(stats.avg_edge).toFixed(1)}%`}
          sub="Model prob vs implied"
        />
      </div>
    </section>
  )
}
