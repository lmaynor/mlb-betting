const STEPS = [
  { num: '1', title: 'Models score the slate', desc: 'Each morning our systems grade every MLB game and prop, folding model edge, market price, and Kelly signal into one 0–100 Beezy Score.' },
  { num: '2', title: 'The card drops by ~11am ET', desc: 'The top-scoring plays publish to the Daily Card, ranked by edge and tagged with system, price, book, and why each made the cut.' },
  { num: '3', title: 'You place what you like', desc: 'Bet the plays that fit your book and bankroll. Beezy flags the edge and a Kelly-sized stake — we never take or hold wagers.' },
  { num: '4', title: 'Results settle in public', desc: 'Every pick grades overnight. Wins, losses, pushes, and voids stay on the permanent record that ranks each system.' },
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
