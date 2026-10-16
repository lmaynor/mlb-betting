// SQLite storage for the Beezy bot. Adapted from the nodebot reference db.js.
//
// Uses node:sqlite (built in, no native dependency). DatabaseSync is
// synchronous by design, so none of these helpers are async.
//
// guild_id / user_id are TEXT: Discord snowflakes exceed
// Number.MAX_SAFE_INTEGER. Every helper coerces with String(...).
//
// The database path is injectable: initDb(':memory:') in tests, or
// initDb('/data/beezy-bot.db') in production (default: $DB_PATH or
// ./beezy-bot.db).
import { DatabaseSync } from 'node:sqlite';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason       TEXT,
    created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS mod_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    actor      TEXT NOT NULL,
    target     TEXT,
    reason     TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS member_links (
    discord_user_id TEXT PRIMARY KEY,
    email           TEXT,
    clerk_user_id   TEXT,
    tier            TEXT NOT NULL DEFAULT 'public',
    sport_choice    TEXT,
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings (guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_logs_guild ON mod_logs (guild_id, created_at);
CREATE INDEX IF NOT EXISTS idx_member_links_email ON member_links (email);
`;

// Moderation defaults. Staff (ManageMessages) are always exempt. The two
// auto-ban detectors are on by default (they only fire on a burst pattern a
// real member does not produce); word/invite/mention filtering is opt-in.
export const DEFAULTS = {
  antispam_enabled: true,
  antispam_channel_threshold: 4,
  antispam_window_seconds: 20,
  antispam_delete_seconds: 3600,
  mention_fanout_enabled: true,
  mention_fanout_threshold: 5,
  mention_fanout_window_seconds: 30,
  mention_fanout_delete_seconds: 3600,
  automod_enabled: false,
  banned_words: [],
  block_invites: false,
  max_mentions: 0,
  log_channel: null,
};

let db = null;

export function initDb(path = process.env.DB_PATH || 'beezy-bot.db') {
  if (db) closeDb();
  const handle = new DatabaseSync(path);
  handle.exec(SCHEMA);
  db = handle;
  return db;
}

export function closeDb() {
  db?.close();
  db = null;
}

export function getDb() {
  if (!db) throw new Error('database not initialised - call initDb() first');
  return db;
}

function now() {
  return Math.floor(Date.now() / 1000);
}

// -- settings -----------------------------------------------------------

export function getSetting(guildId, key) {
  const row = getDb().prepare('SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?')
    .get(String(guildId), key);
  if (!row) return DEFAULTS[key];
  return JSON.parse(row.value);
}

export function getAllSettings(guildId) {
  const settings = { ...DEFAULTS };
  const rows = getDb().prepare('SELECT key, value FROM guild_settings WHERE guild_id = ?').all(String(guildId));
  for (const row of rows) settings[row.key] = JSON.parse(row.value);
  return settings;
}

export function setSetting(guildId, key, value) {
  getDb().prepare(
    'INSERT INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?) '
    + 'ON CONFLICT (guild_id, key) DO UPDATE SET value = excluded.value',
  ).run(String(guildId), key, JSON.stringify(value));
}

// -- warnings -----------------------------------------------------------

export function addWarning(guildId, userId, moderatorId, reason) {
  const result = getDb().prepare(
    'INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)',
  ).run(String(guildId), String(userId), String(moderatorId), reason ?? null, now());
  return Number(result.lastInsertRowid);
}

export function getWarnings(guildId, userId = null, limit = 100) {
  if (userId === null) {
    return getDb().prepare('SELECT * FROM warnings WHERE guild_id = ? ORDER BY created_at DESC, id DESC LIMIT ?')
      .all(String(guildId), limit);
  }
  return getDb().prepare(
    'SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?',
  ).all(String(guildId), String(userId), limit);
}

export function clearWarnings(guildId, userId) {
  const result = getDb().prepare('DELETE FROM warnings WHERE guild_id = ? AND user_id = ?')
    .run(String(guildId), String(userId));
  return Number(result.changes);
}

// -- moderation logs ----------------------------------------------------

export function addLog(guildId, action, actor, target, reason) {
  getDb().prepare(
    'INSERT INTO mod_logs (guild_id, action, actor, target, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)',
  ).run(String(guildId), action, String(actor), target ?? null, reason ?? null, now());
}

export function getLogs(guildId, limit = 100) {
  return getDb().prepare('SELECT * FROM mod_logs WHERE guild_id = ? ORDER BY created_at DESC, id DESC LIMIT ?')
    .all(String(guildId), limit);
}

// -- member links (Discord <-> beezy.fyi account) ------------------------

function normalizeEmail(email) {
  if (email === undefined) return undefined;
  if (email === null) return null;
  return String(email).trim().toLowerCase();
}

function rowToLink(row) {
  return row ? { ...row } : null;
}

export function getMemberLink(discordUserId) {
  return rowToLink(getDb().prepare('SELECT * FROM member_links WHERE discord_user_id = ?')
    .get(String(discordUserId)));
}

export function getMemberByEmail(email) {
  const normalized = normalizeEmail(email);
  if (!normalized) return null;
  return rowToLink(getDb().prepare(
    'SELECT * FROM member_links WHERE email = ? ORDER BY updated_at DESC LIMIT 1',
  ).get(normalized));
}

/**
 * Insert or update a member link. Fields left undefined keep their stored
 * value (a /link that only knows the email will not wipe a tier the site
 * already synced); fields passed as null are cleared. New rows default to
 * tier 'public'.
 */
export function upsertMemberLink(discordUserId, fields = {}) {
  const id = String(discordUserId);
  const existing = getMemberLink(id);
  const pick = (key, fallback) => (fields[key] === undefined ? (existing?.[key] ?? fallback) : fields[key]);
  const next = {
    email: fields.email === undefined ? (existing?.email ?? null) : normalizeEmail(fields.email),
    clerk_user_id: pick('clerk_user_id', null),
    tier: pick('tier', 'public') ?? 'public',
    sport_choice: pick('sport_choice', null),
  };
  getDb().prepare(
    'INSERT INTO member_links (discord_user_id, email, clerk_user_id, tier, sport_choice, updated_at) '
    + 'VALUES (?, ?, ?, ?, ?, ?) '
    + 'ON CONFLICT (discord_user_id) DO UPDATE SET email = excluded.email, '
    + 'clerk_user_id = excluded.clerk_user_id, tier = excluded.tier, '
    + 'sport_choice = excluded.sport_choice, updated_at = excluded.updated_at',
  ).run(id, next.email, next.clerk_user_id, next.tier, next.sport_choice, now());
  return getMemberLink(id);
}
