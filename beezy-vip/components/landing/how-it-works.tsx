const STEPS = [
  { num: '1', title: 'Two ways to find a play', desc: 'The +EV scanner re-checks sportsbook, Kalshi and Novig prices every 15 minutes against the de-vigged consensus. Models grade MLB games and props each morning into a 0-100 Beezy Score.' },
  { num: '2', title: 'Plays appear as they are found', desc: 'Scanner plays post to the +EV Terminal all day. The model card publishes after the ~11:00 AM CT run, ranked by score, with system, price, book, and why each made the cut.' },
  { num: '3', title: 'You place what you like', desc: 'Bet the plays that fit your book and bankroll. Beezy flags the edge and a Kelly-sized stake. We never take or hold wagers.' },
  { num: '4', title: 'Results settle in public', desc: 'Every pick grades overnight at the flagged price. Wins, losses, pushes, and voids stay on the permanent record. All results are paper, not real money.' },
]

export function HowItWorks() {
  return (
    <section style={{ padding: '56px 0 0' }}>
      <div style={{ marginBottom: '24px' }}>
        <h2 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>How it works</h2>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginTop: '8px' }}>
          From model output to your bet slip in four steps &mdash; fully in the open.
        </p>
      </div>

      <div className="steps-grid">
        {STEPS.map((step) => (
          <div
            key={step.num}
            style={{
              background: 'var(--graphite)',
              border: '1px solid var(--basalt)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
            }}
          >
            <div style={{
              width: '32px', height: '32px', borderRadius: 'var(--radius)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'var(--win-wash)', border: '1px solid var(--win-border)',
              marginBottom: '14px',
            }}>
              <span className="mono" style={{ fontSize: '14px', fontWeight: 700, color: 'var(--signal)' }}>{step.num}</span>
            </div>
            <h3
              className="dell-display"
              style={{ fontSize: '16px', color: 'var(--chalk)', marginBottom: '8px', letterSpacing: '-0.01em' }}
            >
              {step.title}
            </h3>
            <p className="times" style={{ fontSize: '13.5px', color: 'var(--fog)', lineHeight: 1.55 }}>{step.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return null
}
