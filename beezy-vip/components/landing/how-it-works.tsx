const B = '1px solid #000'
const B_INNER = '1px solid #1f1f24'

const STEPS = [
  { num: '01', title: 'Open the Daily Card', desc: "Today's plays are ranked by Beezy Score so the top edge is easy to spot." },
  { num: '02', title: 'Compare Price and Edge', desc: 'Each pick shows the system, odds, book, model edge, and a short reason it made the card.' },
  { num: '03', title: 'Track the Result', desc: 'Settlement runs nightly. Wins, losses, pushes, and voids stay public.' },
]

export function HowItWorks() {
  return (
    <section style={{ padding: '40px 20px 32px', borderBottom: B_INNER }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '14px', gap: '12px' }}>
        <span className="dell-display" style={{ fontSize: '14px', color: 'var(--text)' }}>How It Works</span>
      </div>

      <div className="steps-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B, overflow: 'hidden' }}>
        {STEPS.map((step, i) => (
          <div
            key={step.num}
            style={{
              borderRight: i < STEPS.length - 1 ? B : undefined,
              background: '#0d0d11',
            }}
          >
            {/* Ribbon title bar */}
            <div style={{ background: '#0a0a0c', borderBottom: B, padding: '6px 12px' }}>
              <span
                className="dell-heading"
                style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#888890' }}
              >
                STEP {step.num}
              </span>
            </div>
            {/* Ribbon body */}
            <div style={{ padding: '16px' }}>
              <h3
                className="dell-heading"
                style={{ fontSize: '12px', letterSpacing: '0.04em', color: '#f5f5f7', marginBottom: '8px' }}
              >
                {step.title}
              </h3>
              <p className="times" style={{ fontSize: '13px', color: '#888890', lineHeight: 1.55 }}>{step.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return null
}
