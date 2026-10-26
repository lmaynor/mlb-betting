import { SURFACE_CARD } from '@/lib/tokens'

const STEPS = [
  { num: '01', title: 'Two ways to find a play', desc: 'The scanner checks books, Kalshi and Novig every 15 minutes against de-vigged consensus. Models score props each morning.' },
  { num: '02', title: 'Plays publish as found', desc: 'Scanner plays post to the Odds Terminal all day. The model card publishes after the ~11:00 AM CT run.' },
  { num: '03', title: 'You place what fits', desc: 'Beezy flags the edge and a Kelly-sized stake. We never take or hold wagers.' },
  { num: '04', title: 'Results settle in public', desc: 'Every pick grades overnight at the flagged price. Wins, losses, pushes and voids stay on the record.' },
]

export function HowItWorks() {
  return (
    <section style={{ paddingTop: '32px' }}>
      <h2 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 12px' }}>How it works</h2>
      <div className="steps-grid" style={{ ...SURFACE_CARD, gap: 0, overflow: 'hidden' }}>
        {STEPS.map((s, i) => (
          <div key={s.num} style={{ display: 'flex', gap: '10px', padding: '12px 14px', borderLeft: i === 0 ? 'none' : '1px solid #151822', borderTop: 'none' }}>
            <span className="mono" style={{ flexShrink: 0, width: '24px', height: '24px', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, color: 'var(--signal)', border: '1px solid rgba(87,242,135,0.35)' }}>{s.num}</span>
            <div>
              <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--chalk)', marginBottom: '2px' }}>{s.title}</div>
              <div style={{ fontSize: '12px', lineHeight: 1.45, color: '#9ca3af' }}>{s.desc}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
