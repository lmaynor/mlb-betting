export function HowItWorks() {
  const steps = [
    {
      num:   '01',
      title: 'Models Score the Slate',
      desc:  'Each morning, five XGBoost models process today\'s games using Statcast data, weather, umpire tendencies, and market odds.',
    },
    {
      num:   '02',
      title: 'Signal Posts to Discord',
      desc:  'Kelly-qualified picks post to Discord automatically. Model probability, implied probability, and edge shown for every pick.',
    },
    {
      num:   '03',
      title: 'You Place the Bet',
      desc:  'Log in, check your dashboard, size your stake, place the bet. Results settle automatically via MLB Stats API nightly.',
    },
  ]

  return (
    <section className="max-w-7xl mx-auto px-4 py-20 border-b border-[var(--border)]">
      <h2 className="text-2xl font-extrabold tracking-tight uppercase mb-12">How It Works</h2>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-0 border border-[var(--border)]">
        {steps.map((step, i) => (
          <div
            key={step.num}
            className={`p-8 ${i < steps.length - 1 ? 'border-b md:border-b-0 md:border-r border-[var(--border)]' : ''}`}
          >
            <span className="mono text-5xl font-extrabold text-[var(--border-bright)] block mb-6">
              {step.num}
            </span>
            <h3 className="font-bold text-sm uppercase tracking-wide mb-3">{step.title}</h3>
            <p className="text-muted text-sm leading-relaxed">{step.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return (
    <section className="bg-accent/5 border-y border-accent/20">
      <div className="max-w-7xl mx-auto px-4 py-16 flex flex-col md:flex-row items-center justify-between gap-6">
        <div>
          <p className="mono text-xs tracking-widest uppercase text-accent mb-2">Free · No credit card</p>
          <h2 className="text-2xl font-extrabold tracking-tight uppercase">
            The picks post here first.
          </h2>
          <p className="text-muted text-sm mt-2">
            Join the Discord for live signals, model updates, and pre-launch pricing.
          </p>
        </div>
        <a
          href="https://discord.gg/beezy"
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 mono text-sm tracking-widest uppercase px-8 py-4 bg-accent text-bg font-bold hover:bg-accent/90 transition-colors"
        >
          Join Discord →
        </a>
      </div>
    </section>
  )
}
