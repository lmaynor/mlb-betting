/**
 * Single source for outbound community links.
 * Configurable via environment variables with safe production defaults.
 */
export const DISCORD_URL =
  process.env.NEXT_PUBLIC_DISCORD_INVITE_URL || 'https://discord.gg/sWNKxh6jJX'

/** Short display label without protocol (e.g. 'discord.gg/sWNKxh6jJX'), for cards and footer badges */
export const DISCORD_INVITE_LABEL = DISCORD_URL.replace(/^https?:\/\//, '')

export const X_URL =
  process.env.NEXT_PUBLIC_X_URL || 'https://x.com/beezy_fyi'

export const X_HANDLE = '@beezy_fyi'

