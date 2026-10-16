// Editable data for /cleanup: what counts as "legacy" on the live guild, and
// what must never be touched. Edit these lists to change what /cleanup
// plan|apply will act on. A channel, category or role that is NOT listed here
// is never modified by /cleanup.

export const ARCHIVE_CATEGORY = 'ARCHIVE';

export const LEGACY = {
  categories: ['ONBOARDING', 'INFO', 'PICKS', 'COMMUNITY', 'OPS'],
  channels: [
    'verify', 'preferences', // ONBOARDING
    'rules', // INFO
    'daily-picks', 'performance', // PICKS
    'general', 'paper-feedback', // COMMUNITY
  ],
  roles: [
    'Member', 'Member Pro', 'Paper Tester', 'Playbook',
    'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore', 'PointsBet',
  ],
};

/** Roles no command may modify, give, take, rename, recolor or delete. */
export const PROTECTED_ROLE_NAMES = [
  '@everyone', 'Admin', 'Moderator', 'Bot', 'GamblyBot', 'carl-bot', 'beezy-bot',
];

/** Normalise a name for matching: case-, punctuation- and bracket-insensitive. */
export function norm(name) {
  return String(name ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}
