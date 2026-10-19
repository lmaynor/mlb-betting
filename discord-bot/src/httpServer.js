// HTTP surface of the bot (node:http, no framework):
//   GET  /health              -> 200 {"ok":true,"bot":<tag>}
//   POST /internal/sync-role  -> authenticated tier -> role sync (called by the site)
import http from 'node:http';
import { ChannelType } from 'discord.js';
import crypto from 'node:crypto';
import * as db from './db.js';
import {
  ALL_ACCESS_ROLE, PAID_ROLE_NAMES, SPORT_PASS_ROLES,
} from './serverSetup.js';

export const MAX_BODY_BYTES = 16 * 1024;
export const MAX_POST_BODY_BYTES = 96 * 1024;

/**
 * Channels the site/jobs may post to, key -> channel name. A fixed allowlist: the endpoint can never be pointed at an
 * arbitrary channel. Messages go out as the BOT USER (not a webhook), because other bots (GamblyBot, Playbook) read
 * real message text but ignore webhook messages.
 */
export const POST_CHANNELS = {
  ev_sportsbooks: 'ev-sportsbooks',
  ev_exchanges: 'ev-exchanges',
  // keys keep the old *_card names (the Python callers send them); the channels are now *-alerts
  mlb_card: 'mlb-alerts',
  nfl_card: 'nfl-alerts',
  nba_card: 'nba-alerts',
  nhl_card: 'nhl-alerts',
  free_plays: 'daily-free-plays',
  daily_recap: 'daily-recap',
  ops_alerts: 'ops-alerts',
  tweet_queue: 'tweet-queue',
};

/** Validate a post-message payload. @returns {{ok: true, value: object} | {ok: false, error: string}} */
export function validatePostPayload(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { ok: false, error: 'body must be a JSON object' };
  const { channel, content, embeds, thread } = body;
  const dailyThread = body.daily_thread;
  if (typeof channel !== 'string' || !Object.hasOwn(POST_CHANNELS, channel)) {
    return { ok: false, error: `channel must be one of ${Object.keys(POST_CHANNELS).join('|')}` };
  }
  if (content !== undefined && (typeof content !== 'string' || content.length > 2000)) {
    return { ok: false, error: 'content must be a string of at most 2000 characters' };
  }
  if (embeds !== undefined && (!Array.isArray(embeds) || embeds.length > 10 || !embeds.every((e) => e && typeof e === 'object' && !Array.isArray(e)))) {
    return { ok: false, error: 'embeds must be an array of at most 10 objects' };
  }
  if (!content && !(embeds && embeds.length)) return { ok: false, error: 'content or embeds required' };
  if (dailyThread !== undefined && dailyThread !== null) {
    if (thread !== undefined && thread !== null) return { ok: false, error: 'thread and daily_thread cannot be combined' };
    if (typeof dailyThread !== 'object' || Array.isArray(dailyThread)) return { ok: false, error: 'daily_thread must be an object' };
    const name = typeof dailyThread.name === 'string' ? dailyThread.name.replace(/\s+/g, ' ').trim() : '';
    if (!name || name.length > 100) return { ok: false, error: 'daily_thread.name must be 1-100 characters' };
    return { ok: true, value: { channel, content: content || undefined, embeds: embeds || undefined, dailyThread: { name } } };
  }
  if (thread !== undefined && thread !== null) {
    if (typeof thread !== 'object' || Array.isArray(thread)) return { ok: false, error: 'thread must be an object' };
    const name = typeof thread.name === 'string' ? thread.name.replace(/\s+/g, ' ').trim() : '';
    if (!name || name.length > 100) return { ok: false, error: 'thread.name must be 1-100 characters' };
    if (typeof thread.content !== 'string' || !thread.content.trim() || thread.content.length > 1800) {
      return { ok: false, error: 'thread.content must be a non-empty string of at most 1800 characters' };
    }
    return { ok: true, value: { channel, content: content || undefined, embeds: embeds || undefined, thread: { name, content: thread.content } } };
  }
  return { ok: true, value: { channel, content: content || undefined, embeds: embeds || undefined } };
}

/**
 * Slip bots (GamblyBot, Playbook) only act when they are @mentioned, and only on real messages. Names come from
 * SLIP_BOT_NAMES (comma list, default "GamblyBot,Playbook"); they resolve to guild members once and are cached.
 */
const SLIP_CACHE_MS = 10 * 60 * 1000;
let slipCache = { at: 0, ids: [] };
export function resetSlipBotCache() { slipCache = { at: 0, ids: [] }; }

export async function resolveSlipBotIds(guild, names = (process.env.SLIP_BOT_NAMES || 'GamblyBot,Playbook').split(',')) {
  if (Date.now() - slipCache.at < SLIP_CACHE_MS) return slipCache.ids;
  const wanted = names.map((n) => n.trim().toLowerCase()).filter(Boolean);
  const ids = [];
  for (const name of wanted) {
    let found;
    try {
      const hits = await guild.members.fetch({ query: name, limit: 10 });
      found = [...hits.values()].find((m) => m.user?.bot && [m.user.username, m.displayName, m.nickname]
        .filter(Boolean).some((x) => String(x).toLowerCase() === name));
    } catch { found = undefined; }
    if (found) ids.push(found.id);
  }
  slipCache = { at: Date.now(), ids };
  return ids;
}

/**
 * Post into the channel's active thread named `value.dailyThread.name`, creating that thread first when there is none. One
 * thread per day keeps a busy, unvalidated feed out of the channel itself (the card stays on top, access follows the channel).
 * A thread failure fails the request: the caller falls back to a webhook, so nothing is silently dropped.
 */
export async function postToDailyThread(channel, value) {
  if (!channel.threads || typeof channel.threads.create !== 'function') return { status: 'no_threads' };
  const name = value.dailyThread.name;
  let thread;
  try {
    const { threads } = await channel.threads.fetchActive();
    thread = [...threads.values()].find((t) => t.name === name && !t.archived);
  } catch { thread = undefined; }
  let created = false;
  if (!thread) {
    thread = await channel.threads.create({ name, autoArchiveDuration: 1440, type: ChannelType.PublicThread, reason: 'beezy.fyi daily signals thread' });
    created = true;
  }
  const sent = await thread.send({ content: value.content, embeds: value.embeds, allowedMentions: { parse: [] } });
  return { status: 'ok', id: sent?.id ?? null, thread: true, created, mentioned: 0 };
}

/** Send one message to an allowlisted channel as the bot. Never pings anyone, except the slip bots inside a thread. */
export async function postMessage(client, guildId, value) {
  let guild = client.guilds?.cache?.get(guildId);
  if (!guild) {
    try { guild = await client.guilds.fetch(guildId); } catch { return { status: 'no_guild' }; }
  }
  if (!guild) return { status: 'no_guild' };
  const channels = await guild.channels.fetch();
  const name = POST_CHANNELS[value.channel];
  // Until /setup-server has renamed #<sport>-card, fall back to the legacy name so a deploy-order gap drops nothing.
  const legacy = name.endsWith('-alerts') ? name.replace(/-alerts$/, '-card') : null;
  const sendable = (c) => c && typeof c.send === 'function';
  const target = [...channels.values()].find((c) => sendable(c) && c.name === name)
    ?? (legacy ? [...channels.values()].find((c) => sendable(c) && c.name === legacy) : undefined);
  if (!target) return { status: 'no_channel' };
  if (value.dailyThread) return postToDailyThread(target, value);
  const sent = await target.send({ content: value.content, embeds: value.embeds, allowedMentions: { parse: [] } });
  const out = { status: 'ok', id: sent?.id ?? null, thread: false, mentioned: 0 };
  if (value.thread && typeof sent?.startThread === 'function') {
    // The thread is best-effort: the alert itself is already posted, so a thread failure must not fail the request.
    try {
      const thread = await sent.startThread({ name: value.thread.name, autoArchiveDuration: 1440, reason: 'beezy.fyi alert slip' });
      const ids = await resolveSlipBotIds(guild);
      const mentions = ids.map((id) => `<@${id}>`).join(' ');
      await thread.send({ content: `${mentions} ${value.thread.content}`.trim(), allowedMentions: { users: ids } });
      out.thread = true;
      out.mentioned = ids.length;
    } catch (err) {
      console.error(`[http] thread failed: ${String(err?.message || err).slice(0, 200)}`);
    }
  }
  return out;
}
export const TIERS = ['public', 'single_sport', 'pro_monthly', 'pro_annual', 'starter', 'pro', 'season'];
export const SPORT_CHOICES = Object.keys(SPORT_PASS_ROLES);
const PRO_TIERS = new Set(['pro_monthly', 'pro_annual', 'pro', 'season']);
const SNOWFLAKE_RE = /^\d{17,20}$/;
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

/**
 * PURE: tier + sport_choice -> the paid roles to grant and to strip.
 *   pro_monthly / pro_annual / pro / season -> All-Access Pro
 *   single_sport                            -> "{SPORT} Pass"
 *   starter                                 -> sport pass if sport_choice given, else nothing
 *   public                                  -> nothing (Explorer is never touched)
 * `remove` is every other paid role, so stale roles are always revoked.
 * @returns {{add: string[], remove: string[]}}
 */
export function resolveRoleDiff(tier, sportChoice) {
  let add = [];
  if (PRO_TIERS.has(tier)) {
    add = [ALL_ACCESS_ROLE];
  } else if ((tier === 'single_sport' || tier === 'starter') && SPORT_PASS_ROLES[sportChoice]) {
    add = [SPORT_PASS_ROLES[sportChoice]];
  }
  return { add, remove: PAID_ROLE_NAMES.filter((name) => !add.includes(name)) };
}

/** Constant-time secret comparison. Hashes first so lengths never leak. */
export function secretsMatch(provided, expected) {
  if (typeof provided !== 'string' || typeof expected !== 'string' || !expected) return false;
  const a = crypto.createHash('sha256').update(provided).digest();
  const b = crypto.createHash('sha256').update(expected).digest();
  return crypto.timingSafeEqual(a, b);
}

/** Validate a sync-role payload. @returns {{ok: true, value: object} | {ok: false, error: string}} */
export function validateSyncPayload(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { ok: false, error: 'body must be a JSON object' };
  const { discord_user_id: id, email, tier, sport_choice: sport, clerk_user_id: clerk } = body;
  if (typeof id !== 'string' || !SNOWFLAKE_RE.test(id)) return { ok: false, error: 'discord_user_id must be a snowflake string' };
  if (typeof tier !== 'string' || !TIERS.includes(tier)) return { ok: false, error: `tier must be one of ${TIERS.join('|')}` };
  if (sport !== undefined && sport !== null && !SPORT_CHOICES.includes(sport)) {
    return { ok: false, error: `sport_choice must be one of ${SPORT_CHOICES.join('|')}` };
  }
  if (tier === 'single_sport' && !sport) return { ok: false, error: 'sport_choice is required for single_sport' };
  if (email !== undefined && email !== null && (typeof email !== 'string' || !EMAIL_RE.test(email.trim()))) {
    return { ok: false, error: 'email is not a valid address' };
  }
  if (clerk !== undefined && clerk !== null && (typeof clerk !== 'string' || clerk.length > 128)) {
    return { ok: false, error: 'clerk_user_id must be a string' };
  }
  return {
    ok: true,
    value: {
      discord_user_id: id,
      email: email ?? undefined,
      tier,
      sport_choice: sport ?? null,
      clerk_user_id: clerk ?? undefined,
    },
  };
}

/**
 * Apply a role diff to a guild member through an injectable discord client.
 * Only touches roles that actually need to change.
 * @returns {Promise<{status: 'ok', applied: {add: string[], remove: string[]}, missing_roles: string[]} | {status: 'not_in_guild'} | {status: 'no_guild'}>}
 */
export async function applyRoleDiff(client, guildId, discordUserId, diff) {
  let guild = client.guilds?.cache?.get(guildId);
  if (!guild) {
    try {
      guild = await client.guilds.fetch(guildId);
    } catch {
      return { status: 'no_guild' };
    }
  }
  if (!guild) return { status: 'no_guild' };

  let member;
  try {
    member = await guild.members.fetch(discordUserId);
  } catch (err) {
    if (err?.code === 10007 || err?.code === 10013 || err?.status === 404) return { status: 'not_in_guild' };
    throw err;
  }
  if (!member) return { status: 'not_in_guild' };

  await guild.roles.fetch();
  const byName = (name) => guild.roles.cache.find((r) => r.name === name);
  const held = (role) => member.roles.cache.has(role.id);
  const applied = { add: [], remove: [] };
  const missing = [];

  for (const name of diff.add) {
    const role = byName(name);
    if (!role) { missing.push(name); continue; }
    if (!held(role)) {
      await member.roles.add(role, 'beezy.fyi subscription sync');
      applied.add.push(name);
    }
  }
  for (const name of diff.remove) {
    const role = byName(name);
    if (!role) continue; // nothing to strip
    if (held(role)) {
      await member.roles.remove(role, 'beezy.fyi subscription sync');
      applied.remove.push(name);
    }
  }
  return { status: 'ok', applied, missing_roles: missing };
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let rejected = false;
    const chunks = [];
    req.on('data', (chunk) => {
      if (rejected) return; // drain; the caller answers 413 and closes the connection
      size += chunk.length;
      if (size > limit) {
        rejected = true;
        chunks.length = 0;
        reject(Object.assign(new Error('payload too large'), { code: 'TOO_LARGE' }));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => { if (!rejected) resolve(Buffer.concat(chunks).toString('utf8')); });
    req.on('error', (err) => { if (!rejected) reject(err); });
  });
}

/**
 * Build the HTTP server (not listening). Options:
 *   client    discord client (or a fake): needs .user?.tag and .guilds
 *   secret    BEEZY_BOT_SECRET (defaults to the env var, read per request)
 *   guildId   target guild (defaults to GUILD_ID env)
 */
export function createHttpServer({ client, secret, guildId } = {}) {
  const currentSecret = () => (secret !== undefined ? secret : process.env.BEEZY_BOT_SECRET) || '';
  const currentGuild = () => guildId ?? process.env.GUILD_ID ?? (client?.guilds?.cache?.size === 1 ? client.guilds.cache.first().id : '');

  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || '/', 'http://localhost');

      if (url.pathname === '/health') {
        if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { ok: false, error: 'method not allowed' });
        return sendJson(res, 200, { ok: true, bot: client?.user?.tag ?? null });
      }

      if (url.pathname === '/internal/sync-role') {
        if (req.method !== 'POST') return sendJson(res, 405, { ok: false, error: 'method not allowed' });

        const expected = currentSecret();
        if (!expected) return sendJson(res, 503, { ok: false, error: 'sync not configured' });
        if (!secretsMatch(String(req.headers['x-beezy-secret'] ?? ''), expected)) {
          return sendJson(res, 401, { ok: false, error: 'unauthorized' });
        }

        let raw;
        try {
          raw = await readBody(req, MAX_BODY_BYTES);
        } catch (err) {
          if (err.code === 'TOO_LARGE') {
            res.setHeader('Connection', 'close');
            return sendJson(res, 413, { ok: false, error: 'payload too large' });
          }
          return sendJson(res, 400, { ok: false, error: 'could not read body' });
        }
        let body;
        try {
          body = JSON.parse(raw);
        } catch {
          return sendJson(res, 400, { ok: false, error: 'invalid JSON' });
        }
        const parsed = validateSyncPayload(body);
        if (!parsed.ok) return sendJson(res, 400, { ok: false, error: parsed.error });
        const v = parsed.value;

        // Persist first: the link must survive even if the member is not in the guild yet.
        const link = db.upsertMemberLink(v.discord_user_id, {
          email: v.email,
          clerk_user_id: v.clerk_user_id,
          tier: v.tier,
          sport_choice: v.tier === 'single_sport' || v.tier === 'starter' ? v.sport_choice : null,
        });

        const diff = resolveRoleDiff(v.tier, v.sport_choice);
        const gid = currentGuild();
        if (!gid) return sendJson(res, 503, { ok: false, error: 'guild not configured', persisted: true });

        const outcome = await applyRoleDiff(client, gid, v.discord_user_id, diff);
        if (outcome.status === 'not_in_guild') {
          return sendJson(res, 404, { ok: false, error: 'member not in guild', persisted: true, tier: link.tier });
        }
        if (outcome.status === 'no_guild') {
          return sendJson(res, 503, { ok: false, error: 'guild unavailable', persisted: true });
        }
        return sendJson(res, 200, {
          ok: true,
          tier: link.tier,
          sport_choice: link.sport_choice,
          desired: diff,
          applied: outcome.applied,
          missing_roles: outcome.missing_roles,
        });
      }

      if (url.pathname === '/internal/post-message') {
        if (req.method !== 'POST') return sendJson(res, 405, { ok: false, error: 'method not allowed' });
        const expected = currentSecret();
        if (!expected) return sendJson(res, 503, { ok: false, error: 'not configured' });
        if (!secretsMatch(String(req.headers['x-beezy-secret'] ?? ''), expected)) {
          return sendJson(res, 401, { ok: false, error: 'unauthorized' });
        }
        let raw;
        try {
          raw = await readBody(req, MAX_POST_BODY_BYTES);
        } catch (err) {
          if (err.code === 'TOO_LARGE') {
            res.setHeader('Connection', 'close');
            return sendJson(res, 413, { ok: false, error: 'payload too large' });
          }
          return sendJson(res, 400, { ok: false, error: 'could not read body' });
        }
        let body;
        try { body = JSON.parse(raw); } catch { return sendJson(res, 400, { ok: false, error: 'invalid JSON' }); }
        const parsed = validatePostPayload(body);
        if (!parsed.ok) return sendJson(res, 400, { ok: false, error: parsed.error });
        const gid = currentGuild();
        if (!gid) return sendJson(res, 503, { ok: false, error: 'guild not configured' });
        let outcome;
        try {
          outcome = await postMessage(client, gid, parsed.value);
        } catch (err) {
          console.error(`[http] post-message failed: ${String(err?.message || err).slice(0, 200)}`);
          return sendJson(res, 502, { ok: false, error: 'discord rejected the message' });
        }
        if (outcome.status === 'no_guild') return sendJson(res, 503, { ok: false, error: 'guild unavailable' });
        if (outcome.status === 'no_channel') return sendJson(res, 404, { ok: false, error: 'channel not found' });
        if (outcome.status === 'no_threads') return sendJson(res, 502, { ok: false, error: 'channel cannot hold threads' });
        return sendJson(res, 200, { ok: true, id: outcome.id, thread: outcome.thread, created: outcome.created, mentioned: outcome.mentioned });
      }

      return sendJson(res, 404, { ok: false, error: 'not found' });
    } catch (err) {
      console.error(`[http] ${err?.name || 'Error'}: ${String(err?.message || err).slice(0, 200)}`);
      if (!res.headersSent) sendJson(res, 500, { ok: false, error: 'internal error' });
      else res.end();
    }
  });
}

export function startHttpServer(opts = {}) {
  const port = Number(opts.port ?? process.env.PORT ?? 8080);
  const server = createHttpServer(opts);
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, () => resolve(server));
  });
}
