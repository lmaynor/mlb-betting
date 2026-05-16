const B = '0.5px solid #1f1f24'

const STEPS = [
  { num: '01', title: 'Models score the slate',    desc: "Five XGBoost models process today's games each morning using Statcast, weather, umpire tendencies, and market odds." },
  { num: '02', title: 'Kelly-gated signals post',  desc: 'Picks clearing minimum edge and Kelly thresholds post to Discord automatically with model probability and edge shown.' },
  { num: '03', title: 'Results settle nightly',    desc: 'Settlement runs via MLB Stats API. Every result logged — wins, losses, voids. Nothing is hidden.' },
]

export function HowItWorks() {
  return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: '14px' }}>
        How it works
      </div>
      <div className="steps-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', border: B }}>
        {STEPS.map((step, i) => (
          <div key={step.num} style={{ padding: '18px', borderRight: i < STEPS.length - 1 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: '8px' }}>{step.num}</div>
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)', marginBottom: '6px' }}>{step.title}</h3>
            <p style={{ fontSize: '12px', color: 'var(--muted)', lineHeight: 1.55 }}>{step.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

export function DiscordCTA() {
  return (
    <section style={{ background: 'rgba(16,185,129,0.04)', borderTop: '0.5px solid rgba(16,185,129,0.15)', borderBottom: '0.5px solid rgba(16,185,129,0.15)' }}>
      <div style={{ maxWidth: '900px', margin: '0 auto', padding: '40px 20px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '20px' }}>
        <div>
          <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '8px' }}>Free &middot; No credit card</p>
          <h2 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text)', marginBottom: '6px' }}>The picks post here first.</h2>
          <p style={{ fontSize: '13px', color: 'var(--sec)' }}>Join the Discord for live signals, model updates, and pre-launch pricing.</p>
        </div>
        <a href="https://discord.gg/beezy" target="_blank" rel="noopener noreferrer"
          className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '10px 24px', background: '#10b981', color: '#0a0a0c', textDecoration: 'none', whiteSpace: 'nowrap' }}>
          Join Discord &rarr;
        </a>
      </div>
    </section>
  )
}
