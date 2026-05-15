import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Edge Finder (Beta)',
  description: 'Find edges between model probability and book lines. Pro feature.',
  robots:      { index: false, follow: false },
}

export default function EdgeFinderLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}
