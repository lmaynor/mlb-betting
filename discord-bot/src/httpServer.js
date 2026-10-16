// HTTP surface of the bot (node:http, no framework):
//   GET  /health              -> 200 {"ok":true,"bot":<tag>}
//   POST /internal/sync-role  -> authenticated tier -> role sync (called by the site)
import http from 'node:http';
import crypto from 'node:crypto';
import * as db from './db.js';
import {
  ALL_ACCESS_ROLE, PAID_ROLE_NAMES, SPORT_PASS_ROLES,
} from './serverSetup.js';

export const MAX_BODY_BYTES = 16 * 1024;
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
