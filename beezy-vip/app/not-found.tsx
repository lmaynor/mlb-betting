import Link from 'next/link'
export default function NotFound() {
  return (
    <div style={{ maxWidth: '600px', margin: '96px auto', padding: '0 24px', textAlign: 'center' }}>
      <div className="mono" style={{ fontSize: '72px', fontWeight: 700, color: 'var(--iron)', marginBottom: '16px', lineHeight: 1 }}>404</div>
      <h1 className="dell-display" style={{ fontSize: '26px', color: 'var(--chalk)', marginBottom: '10px' }}>Page not found</h1>
      <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginBottom: '28px' }}>The page you&apos;re looking for doesn&apos;t exist or has moved.</p>
      <Link href="/" className="btn btn-primary">Go home</Link>
    </div>
  )
}
