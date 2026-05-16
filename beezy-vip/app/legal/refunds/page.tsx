import type { Metadata } from 'next'
export const metadata: Metadata = { title: 'Refunds — Beezy.VIP' }
const B = '0.5px solid #1f1f24'
export default function LegalPage() {
  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '40px 20px' }}>
      <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>Legal</p>
      <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '24px' }}>Refunds</h1>
      <div style={{ border: B, padding: '24px' }}>
        <p style={{ fontSize: '13px', color: '#71717a', lineHeight: 1.7, marginBottom: '16px' }}>This page is pending legal review before launch.</p>
        <p className="mono" style={{ fontSize: '11px', color: '#f59e0b' }}>LEGAL REVIEW REQUIRED BEFORE LAUNCH</p>
      </div>
    </div>
  )
}
