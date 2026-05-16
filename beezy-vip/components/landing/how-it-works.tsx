export function HowItWorks() {
  const steps = [
    {
      num:   '01',
      title: 'Models score the slate',
      desc:  'Five XGBoost models process today\'s games each morning using Statcast, weather, umpire tendencies, and market odds.',
    },
    {
      num:   '02',
      title: 'Kelly-gated signals post',
      desc:  'Picks clearing minimum edge and Kelly thresholds post to Discord automatically with model probability and edge shown.',
    },
    {
      num:   '03',
      title: 'Results settle nightly',
      desc:  'Settlement runs via MLB Stats API. Every result logged — wins, losses, voids. Nothing is hidden.',
    },
  ]

  return (
    <section className="px-5 py-8 border-b border-[#1f1f24]" style={{ borderColor: 'var(--border)' }}>
      <div className="mono text-[10px] tracking-widest uppercase mb-4" style={{ color: 'var(--muted)' }}>
        How it works
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 border" style={{ borderColor: 'var(--border)' }}>
        {steps.map((step, i) => (
          <div
            key={step.num}
            className="p-5"
            style={{
              borderRight: i < steps.length - 1 ? `0.5px solid var(--border)` : undefined,
            }}
          >
            <div
              className="mono text-[10px] tracking-widest mb-3"
              style={{ color: 'var(--muted)' }}
            >
              {step.num}
            </div>
            <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text)' }}>
              {step.title}
            </h3>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
              {step.desc}
            </p>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return (
    <section style={{ background: 'rgba(16,185,129,0.04)', borderTop: '.5px solid rgba(16,185,129,0.15)', borderBottom: '.5px solid rgba(16,185,129,0.15)' }}>
      <div className="max-w-4xl mx-auto px-5 py-12 flex flex-col md:flex-row items-center justify-between gap-6">
        <div>
          <p className="mono text-[10px] tracking-widest uppercase mb-2" style={{ color: 'var(--accent)' }}>
            Free &middot; No credit card
          </p>
          <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text)' }}>
            The picks post here first.
          </h2>
          <p className="text-sm" style={{ color: 'var(--sec)' }}>
            Join the Discord for live signals, model updates, and pre-launch pricing.
          </p>
        </div>
        <a
          href="https://discord.gg/beezy"
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 mono text-xs font-semibold tracking-widest uppercase px-7 py-3 transition-colors"
          style={{ background: 'var(--accent)', color: 'var(--bg)' }}
        >
          Join Discord &rarr;
        </a>
      </div>
    </section>
  )
}
