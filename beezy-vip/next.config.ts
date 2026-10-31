import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Retired routes. The three /tools redirects were page-level before; /sports/[sport] became /picks/[sport] for every sport (see lib/ia.ts).
  async redirects() {
    return [
      { source: '/sports/:sport', destination: '/picks/:sport', permanent: true },
      // the +EV Terminal was folded into the Odds Terminal; the screen now lives at /ev
      { source: '/ev/odds', destination: '/ev', permanent: true },
      { source: '/tools/edge-finder', destination: '/tools/kelly-calculator', permanent: true },
      { source: '/tools/nrfi-conditions', destination: '/tools/slate', permanent: true },
      { source: '/tools/pitcher-matchups', destination: '/tools/slate', permanent: true },
      // Obvious aliases, Clerk standard paths and legal shortcuts
      { source: '/sign-in', destination: '/login', permanent: true },
      { source: '/sign-up', destination: '/signup', permanent: true },
      { source: '/terms', destination: '/legal/terms', permanent: true },
      { source: '/privacy', destination: '/legal/privacy', permanent: true },
      { source: '/refunds', destination: '/legal/refunds', permanent: true },
      { source: '/responsible-gambling', destination: '/legal/responsible-gambling', permanent: true },
      { source: '/contact', destination: '/about', permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "script-src 'self' 'unsafe-eval' 'unsafe-inline' https://clerk.com https://*.clerk.accounts.dev;",
          },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
