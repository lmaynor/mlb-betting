import Link from 'next/link'
export default function NotFound() {
  return (
    <div style={{ maxWidth: '600px', margin: '80px auto', padding: '0 20px', textAlign: 'center' }}>
      <div className="mono" style={{ fontSize: '48px', fontWeight: 700, color: 'var(--basalt)', marginBottom: '16px' }}>404</div>
      <h1 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--ash)', marginBottom: '8px' }}>Page not found</h1>
      <p style={{ fontSize: '13px', color: 'var(--fog)', marginBottom: '24px' }}>The page you&apos;re looking for doesn&apos;t exist or has moved.</p>
      <Link href="/" className="mono" style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 20px', background: 'var(--signal)', color: 'var(--carbon)', textDecoration: 'none' }}>Go home</Link>
    </div>
  )
}
