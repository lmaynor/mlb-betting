import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: process.cwd(),
  },
  // Retired routes. The three /tools redirects were page-level before; /sports/mlb duplicated Picks (see lib/ia.ts).
  async redirects() {
    return [
      { source: '/sports/mlb', destination: '/picks', permanent: true },
      { source: '/tools/edge-finder', destination: '/tools/kelly-calculator', permanent: true },
      { source: '/tools/nrfi-conditions', destination: '/tools/slate', permanent: true },
      { source: '/tools/pitcher-matchups', destination: '/tools/slate', permanent: true },
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
        ],
      },
    ];
  },
};

export default nextConfig;
