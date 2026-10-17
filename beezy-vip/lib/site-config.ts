import { DISCORD_URL, X_URL } from '@/lib/links'

/**
 * One place for the operator identity, contact routes and standing disclaimers, so legal pages, footer and
 * pricing never drift apart. Discord and X links come from lib/links.ts (the verified invite), not a second copy.
 */
export const SITE_CONFIG = {
  name: 'beezy.fyi',
  displayName: 'BEEZY.FYI',
  tagline: 'Quantitative Sports Analytics & +EV Market Terminal',
  url: 'https://beezy.fyi',
  apiUrl: 'https://api.beezy.fyi',
  supportEmail: 'support@beezy.fyi',
  xHandle: '@beezy_fyi',
  xUrl: X_URL,
  discordInviteUrl: process.env.NEXT_PUBLIC_DISCORD_INVITE_URL || DISCORD_URL,
  operatorStatement:
    'beezy.fyi is an independent quantitative sports analytics publication operated by an individual sole proprietor.',
  riskDisclaimer:
    'For informational and educational purposes only. beezy.fyi is not a sportsbook and never accepts, holds, or facilitates wagers. Sports betting and prediction-market trading involve substantial risk of financial loss. Past model or paper-ledger performance does not guarantee future results. Must be 21+ (or 18+ where applicable) and physically located in a permitted jurisdiction. If you or someone you know has a gambling problem, call 1-800-GAMBLER or visit ncpgambling.org.',
  governingState: 'State of Illinois',
  governingVenue: 'Cook County, Illinois',
  legalUpdated: '2026-10-17',
} as const
