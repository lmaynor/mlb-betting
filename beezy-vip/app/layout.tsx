import type { Metadata } from 'next'
import './globals.css'
import { Nav } from '@/components/layout/nav'
import { Footer } from '@/components/layout/footer'

export const metadata: Metadata = {
  title: {
    default:  'Beezy.VIP — MLB Picks Backed by Machine Learning',
    template: '%s — Beezy.VIP',
  },
  description:
    'Five XGBoost models scoring MLB games daily. NRFI, HR, F5, K, and OUTS systems. Real data, real results, no gut feelings.',
  metadataBase: new URL('https://beezy.vip'),
  openGraph: {
    images: [{ url: '/api/og?title=MLB+Picks+%2F+Backed+by+Data', width: 1200, height: 630 }],
    siteName: 'Beezy.VIP',
    type:     'website',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  )
}
