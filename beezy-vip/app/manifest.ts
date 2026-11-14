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
    // no window-controls-overlay: the nav has no titlebar-area-* layout, so the overlay would draw under the window controls
    display_override: ['standalone', 'minimal-ui'],
    background_color: '#090a0f',
    theme_color: '#090a0f',
    categories: ['sports', 'finance', 'utilities'],
    icons: [
      { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml' },
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
      // the glyph sits inside the central 80% safe zone of a full-bleed #090a0f square (measured by eye, tight at two corners)
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [
      {
        name: 'Odds Screen (+EV & Arb)',
        short_name: 'Odds',
        url: '/ev',
        description: 'Live +EV scanner, outliers, arbitrage, and middles',
      },
      {
        name: "Today's Card",
        short_name: 'Daily Card',
        url: '/picks',
        description: "Today's locked official confluence plays",
      },
      {
        name: 'Prop Research',
        short_name: 'Props',
        url: '/edge',
        description: 'L5/L10 hit rates, projections, and book comparison',
      },
      {
        name: 'Settled Bets',
        short_name: 'Results',
        url: '/results',
        description: 'Verified graded bet history and ROI',
      },
    ],
    // one wide screenshot only: a 'narrow' entry needs a real portrait capture (the OG card is 2000x1056)
    screenshots: [
      {
        src: '/brand/og-card.jpg',
        sizes: '2000x1056',
        type: 'image/jpeg',
        form_factor: 'wide',
        label: 'beezy.fyi Quantitative Sports Betting Terminal',
      },
    ],
  }
}
