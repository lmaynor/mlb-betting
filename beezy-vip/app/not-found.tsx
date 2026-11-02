import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
export default function NotFound() {
  return (
    <div style={{ maxWidth: '600px', margin: '96px auto', padding: '0 24px', textAlign: 'center' }}>
      <div className="flex justify-center mb-[28px]"><BrandWordmark size="md" /></div>
      <div className="mono text-[72px] font-bold text-[var(--color-border-button-default)] mb-[16px] leading-[1]">404</div>
      <h1 className="dell-display text-[26px] text-text-primary mb-[10px]">Page not found</h1>
      <p className="times text-[15px] text-text-secondary mb-[28px]">The page you&apos;re looking for doesn&apos;t exist or has moved.</p>
      <Link href="/" className="btn btn-primary">Go home</Link>
    </div>
  )
}
