const B = '0.5px solid #1f1f24'

const STEPS = [
  { num: '01', title: 'Open the daily card', desc: "Today's plays are ranked by Beezy Score so the top edge is easy to spot." },
  { num: '02', title: 'Compare price and edge', desc: 'Each pick shows the system, odds, book, model edge, and a short reason it made the card.' },
  { num: '03', title: 'Track the result', desc: 'Settlement runs nightly. Wins, losses, pushes, and voids stay public.' },
]

export function HowItWorks() {
  return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '14px' }}>
        How it works
      </div>
      <div className="steps-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B, borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-card)' }}>
        {STEPS.map((step, i) => (
          <div key={step.num} style={{ padding: '18px', borderRight: i < STEPS.length - 1 ? B : undefined, background: '#0d0d11' }}>
            <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: '8px' }}>{step.num}</div>
            <h3 style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text)', marginBottom: '6px' }}>{step.title}</h3>
            <p style={{ fontSize: '12px', color: 'var(--muted)', lineHeight: 1.55 }}>{step.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return null
}
