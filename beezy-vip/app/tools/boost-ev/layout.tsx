import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Boost EV Calculator',
  description: 'Find the true expected value of a profit boost against the market price.',
}

export default function ToolLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
