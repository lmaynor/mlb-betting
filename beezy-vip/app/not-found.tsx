import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
export default function NotFound() {
  return (
    <div className="max-w-[600px] py-0 px-6 text-center" style={{ margin: '96px auto' }}>
      <div className="flex justify-center mb-7"><BrandWordmark size="md" /></div>
      <div className="mono text-[72px] font-semibold text-border-button-default mb-4 leading-[1]">404</div>
      <h1 className="ui-display text-[26px] text-text-primary mb-2.5">Page not found</h1>
      <p className="times text-[15px] text-text-secondary mb-7">The page you&apos;re looking for doesn&apos;t exist or has moved.</p>
      <Link href="/" className="btn btn-primary">Go home</Link>
    </div>
  )
}
