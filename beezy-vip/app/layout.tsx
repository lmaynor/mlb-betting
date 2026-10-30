import type { Metadata } from 'next'
import { IBM_Plex_Mono, Plus_Jakarta_Sans, Space_Grotesk } from 'next/font/google'
import './globals.css'

// Three faces. Variable names are the contract with globals.css and ~1,600 inline styles: --font-inter is the dense UI
// face (historical name, now Plus Jakarta Sans), --font-display is headings and the wordmark, --font-mono is every number.
const uiSans = Plus_Jakarta_Sans({ subsets: ['latin'], variable: '--font-inter', display: 'swap' })
const displaySans = Space_Grotesk({ subsets: ['latin'], variable: '--font-display', display: 'swap' })
const plexMono = IBM_Plex_Mono({ subsets: ['latin'], variable: '--font-mono', weight: ['400', '500', '600', '700'], display: 'swap' })
import { ClerkProvider } from '@clerk/nextjs'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { Nav } from '@/components/layout/nav'
import { LiveTicker } from '@/components/layout/live-ticker'
import { Footer } from '@/components/layout/footer'
import { BottomNav } from '@/components/layout/bottom-nav'
import { SectionTabs } from '@/components/layout/section-tabs'
import { Suspense } from 'react'
import { jsonLdString, ORGANIZATION_LD, WEBSITE_LD } from '@/lib/seo'

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#090a0f',
}

export const metadata: Metadata = {
  title: {
    default:  'beezy.fyi \u2014 Quantitative Sports Analytics',
    template: '%s \u2014 beezy.fyi',
  },
  description:
    'Quantitative models, prop research and a live +EV terminal across MLB, NFL, NHL and NBA. Paper-tracked, every result public.',
  metadataBase: new URL('https://beezy.fyi'),
  openGraph: {
    images: [{ url: '/brand/og-card.jpg?v=2', width: 2000, height: 1056, alt: 'beezy.fyi — Smarter tools. Bigger plays.' }],
    siteName: 'beezy.fyi',
    type:     'website',
  },
  twitter: {
    card:    'summary_large_image',
    site:    '@beezy_fyi',
    creator: '@beezy_fyi',
    images:  [{ url: '/brand/og-card.jpg?v=2', alt: 'beezy.fyi — Smarter tools. Bigger plays.' }],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'beezy.fyi',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const shell = (
    <html lang="en" className={`${uiSans.variable} ${displaySans.variable} ${plexMono.variable}`}>
      <body>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdString([ORGANIZATION_LD, WEBSITE_LD]) }} />
        <div className="page-frame">
          <div className="site-chrome">
            <Nav authDisabled={AUTH_DISABLED} />
            <LiveTicker />
          </div>
          {/* Outside the sticky chrome on purpose: it scrolls away so the pinned header stays ~131px. */}
          <Suspense fallback={null}><SectionTabs /></Suspense>
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
