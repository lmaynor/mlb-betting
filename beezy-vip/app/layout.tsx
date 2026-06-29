import type { Metadata } from 'next'
import { Red_Hat_Display, Red_Hat_Text, Red_Hat_Mono } from 'next/font/google'
import './globals.css'

const redHatDisplay = Red_Hat_Display({ subsets: ['latin'], variable: '--font-display', display: 'swap' })
const redHatText = Red_Hat_Text({ subsets: ['latin'], variable: '--font-text', display: 'swap' })
const redHatMono = Red_Hat_Mono({ subsets: ['latin'], variable: '--font-mono', display: 'swap' })
import { ClerkProvider } from '@clerk/nextjs'
import { Nav } from '@/components/layout/nav'
import { LiveTicker } from '@/components/layout/live-ticker'
import { Footer } from '@/components/layout/footer'
import { BottomNav } from '@/components/layout/bottom-nav'

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#04040b',
}

export const metadata: Metadata = {
  title: {
    default:  'Beezy.FYI — MLB Picks Backed by Machine Learning',
    template: '%s — Beezy.FYI',
  },
  description:
    'Five XGBoost models scoring MLB games daily. NRFI, HR, F5, K, and OUTS systems. Real data, real results, no gut feelings.',
  metadataBase: new URL('https://beezy.fyi'),
  openGraph: {
    images: [{ url: '/api/og?title=MLB+Picks+%2F+Backed+by+Data', width: 1200, height: 630 }],
    siteName: 'Beezy.FYI',
    type:     'website',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider afterSignOutUrl="/" signInUrl="/login" signUpUrl="/signup">
    <html lang="en" className={`${redHatDisplay.variable} ${redHatText.variable} ${redHatMono.variable}`}>
      <body>
        <div className="page-frame">
          <Nav />
          <LiveTicker />
          <main>{children}</main>
          <Footer />
          <BottomNav />
        </div>
      </body>
    </html>
    </ClerkProvider>
  )
}
