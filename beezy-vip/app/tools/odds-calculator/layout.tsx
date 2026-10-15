import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Odds & Vig Calculator',
  description: 'Convert American odds to implied probability, remove the vig and get fair odds.',
}

export default function ToolLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
