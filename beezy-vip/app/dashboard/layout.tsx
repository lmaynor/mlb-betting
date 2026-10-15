import { getUserTier } from '@/lib/auth'
import { redirect } from 'next/navigation'
import { DashboardTabBar } from './tab-bar'

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const tier = await getUserTier()
  // /pricing, not /signup: the middleware sends signed-in users on /signup straight back to /dashboard, which looped.
  if (tier === 'public' || tier === 'starter') {
    redirect('/pricing')
  }

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '20px 24px 48px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '12px', marginBottom: '12px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Dashboard</h1>
        <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>Pro</span>
      </div>
      <DashboardTabBar />
      {children}
    </div>
  )
}
