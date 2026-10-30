import type { MetadataRoute } from 'next'

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: '/',
    name: 'beezy.fyi \u2014 Quantitative Sports Betting Terminal',
    short_name: 'beezy.fyi',
    description: 'Real-time +EV scanner, arbitrage, middles, and player prop projections across NFL, NBA, NHL, and MLB.',
    start_url: '/ev?source=pwa',
    scope: '/',
    display: 'standalone',
    display_override: ['window-controls-overlay', 'standalone', 'minimal-ui'],
    background_color: '#090a0f',
    theme_color: '#090a0f',
    categories: ['sports', 'finance', 'utilities'],
    icons: [
      { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml' },
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    shortcuts: [
      {
        name: 'Odds Terminal (+EV & Arb)',
        short_name: 'Odds',
        url: '/ev',
        description: 'Live +EV scanner, outliers, arbitrage, and middles',
      },
      {
        name: 'Daily Card',
        short_name: 'Daily Card',
        url: '/cheat-sheet',
        description: "Today's locked official confluence plays",
      },
      {
        name: 'Prop Research Cockpit',
        short_name: 'Cockpit',
        url: '/edge',
        description: 'L5/L10 hit rates, projections, and book comparison',
      },
      {
        name: 'Public Results Ledger',
        short_name: 'Results',
        url: '/results',
        description: 'Verified graded bet history and ROI',
      },
    ],
    screenshots: [
      {
        src: '/brand/og-card.jpg',
        sizes: '2000x1056',
        type: 'image/jpeg',
        form_factor: 'wide',
        label: 'beezy.fyi Quantitative Sports Betting Terminal',
      },
      {
        src: '/brand/og-card.jpg',
        sizes: '2000x1056',
        type: 'image/jpeg',
        form_factor: 'narrow',
        label: 'beezy.fyi Mobile Odds Terminal',
      },
    ],
  }
}
