const STEPS = [
  { num: '01', title: 'Two ways to find a play', desc: 'The scanner checks books, Kalshi and Novig every 15 minutes against de-vigged consensus. Models score props each morning.' },
  { num: '02', title: 'Plays publish as found', desc: 'Scanner plays post to the Odds Terminal all day. The model card publishes after the ~11:00 AM CT run.' },
  { num: '03', title: 'You place what fits', desc: 'Beezy flags the edge and a Kelly-sized stake. We never take or hold wagers.' },
  { num: '04', title: 'Results settle in public', desc: 'Every pick grades overnight at the flagged price. Wins, losses, pushes and voids stay on the record.' },
]

export function HowItWorks() {
  return (
    <section className="pt-8">
      <h2 className="mb-3 text-headline-bold text-text-primary">How it works</h2>
      <div className="steps-grid overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card" style={{ gap: 0 }}>
        {STEPS.map((s, i) => (
          <div key={s.num} className={i === 0 ? 'flex gap-2.5 px-3.5 py-3' : 'flex gap-2.5 border-l border-border-table px-3.5 py-3'}>
            <span className="mono flex size-6 shrink-0 items-center justify-center rounded-md border border-[rgba(87,242,135,0.35)] text-[10px] font-bold text-[var(--signal)]">{s.num}</span>
            <div>
              <div className="mb-0.5 text-body-2-bold text-text-primary">{s.title}</div>
              <div className="text-caption-1-regular text-text-secondary">{s.desc}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
