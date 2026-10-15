import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Kelly Calculator',
  description: 'Size a bet with full and half Kelly from your bankroll, the line and your win probability.',
}

export default function ToolLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
