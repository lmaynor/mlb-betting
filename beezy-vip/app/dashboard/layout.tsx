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
    <div className="mx-auto max-w-page px-6 pb-12 pt-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h1 className="text-title-2-bold text-text-primary">Dashboard</h1>
        <span className="mono text-caption-1-regular text-text-secondary">Pro</span>
      </div>
      <DashboardTabBar />
      {children}
    </div>
  )
}
