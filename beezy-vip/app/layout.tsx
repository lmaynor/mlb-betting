import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' })
const jetbrainsMono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono', display: 'swap' })
import { ClerkProvider } from '@clerk/nextjs'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { Nav } from '@/components/layout/nav'
import { LiveTicker } from '@/components/layout/live-ticker'
import { Footer } from '@/components/layout/footer'
import { BottomNav } from '@/components/layout/bottom-nav'

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#090a0f',
}

export const metadata: Metadata = {
  title: {
    default:  'Beezy.FYI \u2014 +EV Scanner and MLB Models',
    template: '%s — Beezy.FYI',
  },
  description:
    'A 15-minute +EV scanner across sportsbooks, Kalshi and Novig, plus MLB models graded daily. Paper-tracked, every result public.',
  metadataBase: new URL('https://beezy.fyi'),
  openGraph: {
    images: [{ url: '/api/og?title=%2BEV+Scanner+%2F+MLB+Models&sub=Paper-tracked.+Every+result+public.', width: 1200, height: 630 }],
    siteName: 'Beezy.FYI',
    type:     'website',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const shell = (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body>
        <div className="page-frame">
          <div className="site-chrome">
            <Nav authDisabled={AUTH_DISABLED} />
            <LiveTicker />
          </div>
          <main>{children}</main>
          <Footer />
          <BottomNav />
        </div>
      </body>
    </html>
  )
  // AUTH_DISABLED (lib/auth-mode.ts): local review without Clerk keys. Never true on a Vercel production deployment.
  return AUTH_DISABLED ? shell : (
    <ClerkProvider afterSignOutUrl="/" signInUrl="/login" signUpUrl="/signup">
      {shell}
    </ClerkProvider>
  )
}
