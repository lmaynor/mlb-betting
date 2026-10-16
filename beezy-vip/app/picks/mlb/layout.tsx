import { MlbTabs } from '@/components/picks/mlb-tabs'

export default function MlbLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <MlbTabs />
      {children}
    </>
  )
}
