// Idempotent provisioning of the Beezy Discord server: roles, categories,
// channels, permission overwrites and the per-channel "Beezy Terminal"
// webhooks. Adapted from the nodebot reference agentTools.js createChannel /
// createRole / setChannelAccess / setChannelTopic.
//
// The layout and permission matrix are exported as plain data (ROLE_SPECS,
// LAYOUT, WEBHOOK_TARGETS) and pure functions (overwritesFor, effectiveAccess)
// so tests can check them without a live Discord.
import { ChannelType, OverwriteType, PermissionFlagsBits } from 'discord.js';

export const EVERYONE = '@everyone';
/** Hidden from @everyone; visible only to the owner and members with Administrator. */
export const OWNER_ONLY = '@owner';
export const WEBHOOK_NAME = 'Beezy Terminal';

export const ALL_ACCESS_ROLE = 'All-Access Pro';
export const SPORT_PASS_ROLES = {
  nfl: 'NFL Pass',
  nba: 'NBA Pass',
  nhl: 'NHL Pass',
  mlb: 'MLB Pass',
};
export const EXPLORER_ROLE = 'Explorer';

export const ROLE_SPECS = [
  { name: ALL_ACCESS_ROLE, color: '#57F287' },
  { name: SPORT_PASS_ROLES.nfl, color: '#f59e0b' },
  { name: SPORT_PASS_ROLES.nba, color: '#818cf8' },
  { name: SPORT_PASS_ROLES.nhl, color: '#38bdf8' },
  { name: SPORT_PASS_ROLES.mlb, color: '#34d399' },
  { name: EXPLORER_ROLE, color: '#9ca3af' },
];

/** Every role a paid tier can grant (everything except Explorer). */
export const PAID_ROLE_NAMES = [ALL_ACCESS_ROLE, ...Object.values(SPORT_PASS_ROLES)];

const SITE = 'https://beezy.fyi';

/**
 * Category layout. Per-channel `visibleTo` / `readOnly` override the
 * category's. visibleTo entries: EVERYONE, OWNER_ONLY, or a role name.
 */
export const LAYOUT = [
  {
    name: 'START HERE',
    visibleTo: [EVERYONE],
    readOnly: true,
    channels: [
      { name: 'welcome', topic: `Start here: what BEEZY.FYI is and how to use the bot -> ${SITE}` },
      { name: 'announcements', topic: 'Product updates and model changes' },
      { name: 'link-account', topic: `Run /link here to sync your beezy.fyi subscription -> ${SITE}` },
    ],
  },
  {
    name: 'LIVE +EV & EXCHANGE ALERTS',
    visibleTo: [EVERYONE],
    readOnly: true,
    channels: [
      {
        name: 'ev-sportsbooks',
        visibleTo: [ALL_ACCESS_ROLE],
        topic: `Live 15-min soft-book +EV dislocations (DK, FD, MGM, Caesars, Bet365) -> ${SITE}/ev`,
      },
      {
        name: 'ev-exchanges',
        visibleTo: [ALL_ACCESS_ROLE],
        topic: `Zero-fee Novig & Kalshi order-book dislocations -> ${SITE}/ev`,
      },
      {
        name: 'daily-free-plays',
        visibleTo: [EVERYONE],
        topic: `2 free daily Watch/Confluence plays + public audit -> ${SITE}`,
      },
    ],
  },
  {
    name: 'DAILY MODEL CARDS',
    visibleTo: [EVERYONE],
    readOnly: true,
    channels: [
      { name: 'mlb-card', visibleTo: [ALL_ACCESS_ROLE, SPORT_PASS_ROLES.mlb], topic: `Daily MLB model card -> ${SITE}/edge` },
      { name: 'nfl-card', visibleTo: [ALL_ACCESS_ROLE, SPORT_PASS_ROLES.nfl], topic: `Daily NFL model card -> ${SITE}/edge` },
      { name: 'nba-card', visibleTo: [ALL_ACCESS_ROLE, SPORT_PASS_ROLES.nba], topic: `Daily NBA model card -> ${SITE}/edge` },
      { name: 'nhl-card', visibleTo: [ALL_ACCESS_ROLE, SPORT_PASS_ROLES.nhl], topic: `Daily NHL model card -> ${SITE}/edge` },
    ],
  },
  {
    name: 'PUBLIC LEDGER & RECAPS',
    visibleTo: [EVERYONE],
    readOnly: true,
    channels: [
      { name: 'daily-recap', topic: `Nightly graded settlement receipt cards + season ROI ledger -> ${SITE}/results` },
    ],
  },
  {
    name: 'SHARP COMMUNITY',
    visibleTo: [EVERYONE],
    readOnly: false,
    channels: [
      { name: 'general-sweat', topic: 'Talk the slate. No guarantees, no locks.' },
      { name: 'betting-theory', topic: 'Pricing, de-vig, CLV, sizing, model critique' },
    ],
  },
  {
    name: 'OPS & TWEET QUEUE',
    visibleTo: [OWNER_ONLY],
    readOnly: false,
    channels: [
      { name: 'ops-alerts', topic: 'Pipeline and job alerts (owner only)' },
      { name: 'nfl-props-alerts', topic: 'NFL props sweep signals: shadow mode, unvalidated (owner only; widen visibleTo to share)' },
      { name: 'tweet-queue', topic: 'Draft tweets awaiting review (owner only)' },
      { name: 'deploys', topic: 'Deploy notices (owner only)' },
    ],
  },
];

/** Channels that get a "Beezy Terminal" webhook, with the Secret Manager secret and env var each maps to. */
export const WEBHOOK_TARGETS = [
  { channel: 'ev-sportsbooks', secret: 'discord-webhook-ev-books', env: 'DISCORD_WEBHOOK_EV_BOOKS' },
  { channel: 'ev-exchanges', secret: 'discord-webhook-ev-exchanges', env: 'DISCORD_WEBHOOK_EV_EXCHANGES' },
  { channel: 'mlb-card', secret: 'discord-webhook-card-mlb', env: 'DISCORD_WEBHOOK_CARD_MLB' },
  { channel: 'nfl-card', secret: 'discord-webhook-card-nfl', env: 'DISCORD_WEBHOOK_CARD_NFL' },
  { channel: 'nba-card', secret: 'discord-webhook-card-nba', env: 'DISCORD_WEBHOOK_CARD_NBA' },
  { channel: 'nhl-card', secret: 'discord-webhook-card-nhl', env: 'DISCORD_WEBHOOK_CARD_NHL' },
  { channel: 'daily-recap', secret: 'discord-webhook-recap', env: 'DISCORD_WEBHOOK_RECAP' },
  { channel: 'ops-alerts', secret: 'discord-ops-webhook-url', env: 'DISCORD_WEBHOOK_OPS' },
  { channel: 'nfl-props-alerts', secret: 'discord-webhook-props-nfl', env: 'DISCORD_WEBHOOK_PROPS_NFL' },
  { channel: 'tweet-queue', secret: 'discord-webhook-tweets', env: 'DISCORD_WEBHOOK_TWEETS' },
];

// -- pure permission spec -------------------------------------------------

const VIEW = 'ViewChannel';
const SEND = 'SendMessages';

/**
 * Overwrite spec for one channel (or, with an empty channel, the category).
 * @returns {Array<{target: string, allow: string[], deny: string[]}>} target is
 *   EVERYONE or a role name; allow/deny are PermissionFlagsBits keys.
 */
export function overwritesFor(category, channel = {}) {
  const visibleTo = channel.visibleTo ?? category.visibleTo;
  const readOnly = channel.readOnly ?? category.readOnly;
  const isPublic = visibleTo.includes(EVERYONE);
  const out = [];

  if (isPublic) {
    out.push({
      target: EVERYONE,
      allow: readOnly ? [VIEW] : [VIEW, SEND],
      deny: readOnly ? [SEND] : [],
    });
    return out;
  }

  out.push({ target: EVERYONE, allow: [], deny: [VIEW, SEND] });
  for (const entry of visibleTo) {
    if (entry === OWNER_ONLY) continue; // owner/admins are handled at apply time
    out.push({
      target: entry,
      allow: readOnly ? [VIEW] : [VIEW, SEND],
      deny: readOnly ? [SEND] : [],
    });
  }
  return out;
}

/** The set of role names (and EVERYONE / OWNER_ONLY) that can view a channel. */
export function visibleSet(category, channel = {}) {
  return [...(channel.visibleTo ?? category.visibleTo)];
}

/**
 * Evaluate an overwrite spec for a member holding `roleNames`, using Discord's
 * order: @everyone base (default allow), @everyone overwrite, then the union
 * of the member's role overwrites (allow beats deny).
 * @returns {{view: boolean, send: boolean}}
 */
export function effectiveAccess(overwrites, roleNames = []) {
  const state = { [VIEW]: true, [SEND]: true };
  const everyone = overwrites.find((o) => o.target === EVERYONE);
  if (everyone) {
    for (const p of everyone.deny) state[p] = false;
    for (const p of everyone.allow) state[p] = true;
  }
  const roleDeny = new Set();
  const roleAllow = new Set();
  for (const o of overwrites) {
    if (o.target === EVERYONE || !roleNames.includes(o.target)) continue;
    o.deny.forEach((p) => roleDeny.add(p));
    o.allow.forEach((p) => roleAllow.add(p));
  }
  for (const p of roleDeny) state[p] = false;
  for (const p of roleAllow) state[p] = true;
  return { view: state[VIEW], send: state[SEND] };
}

// -- secrets command builder (pure) ---------------------------------------

const WEBHOOK_URL_RE = /^https:\/\/(?:discord|discordapp)\.com\/api\/webhooks\/\d+\/[A-Za-z0-9_-]+$/;

/**
 * Build gcloud commands to store each webhook URL in Secret Manager, plus the
 * Cloud Run --update-secrets fragment. URLs that do not look like Discord
 * webhook URLs are skipped (shell-quoting hygiene).
 *
 * @param {Array<{channel: string, secret: string, env: string, url: string}>} webhooks
 * @returns {{blocks: string[], skipped: string[]}} blocks are code-fence-free
 *   chunks, each <= maxChars, group-aligned.
 */
export function buildGcloudCommands(webhooks, { service = 'YOUR_SERVICE', region = 'YOUR_REGION', maxChars = 1800 } = {}) {
  const groups = [];
  const skipped = [];
  const mappings = [];
  for (const w of webhooks) {
    if (!w.url || !WEBHOOK_URL_RE.test(w.url)) {
      skipped.push(w.channel);
      continue;
    }
    groups.push([
      `# #${w.channel} -> ${w.env}`,
      `gcloud secrets describe ${w.secret} >/dev/null 2>&1 || gcloud secrets create ${w.secret} --replication-policy=automatic`,
      `printf %s '${w.url}' | gcloud secrets versions add ${w.secret} --data-file=-`,
    ].join('\n'));
    mappings.push(`${w.env}=${w.secret}:latest`);
  }

  const blocks = [];
  let current = '';
  for (const g of groups) {
    if (current && current.length + g.length + 2 > maxChars) {
      blocks.push(current);
      current = '';
    }
    current = current ? `${current}\n\n${g}` : g;
  }
  if (current) blocks.push(current);

  if (mappings.length) {
    blocks.push([
      '# Wire the secrets into the Cloud Run service (additive: other secrets stay untouched)',
      `gcloud run services update ${service} --region ${region} \\`,
      `  --update-secrets=${mappings.join(',')}`,
    ].join('\n'));
  }
  return { blocks, skipped };
}

// -- provisioning ---------------------------------------------------------

function hexToInt(hex) {
  return Number.parseInt(String(hex).replace('#', ''), 16);
}

function findRole(guild, name) {
  return guild.roles.cache.find((r) => r.name === name) ?? null;
}

function findCategory(guild, name) {
  const wanted = name.toLowerCase();
  return guild.channels.cache.find(
    (c) => c.type === ChannelType.GuildCategory && String(c.name).toLowerCase() === wanted,
  ) ?? null;
}

function findText(guild, name) {
  return guild.channels.cache.find((c) => c.type === ChannelType.GuildText && c.name === name) ?? null;
}

function toDiscordOverwrites(guild, spec, { extraMembers = [] } = {}) {
  const list = [];
  const everyoneId = guild.roles.everyone?.id ?? guild.id;
  for (const o of spec) {
    const id = o.target === EVERYONE ? everyoneId : findRole(guild, o.target)?.id;
    if (!id) continue; // role missing: cannot target it
    list.push({
      id,
      type: OverwriteType.Role,
      allow: o.allow.map((p) => PermissionFlagsBits[p]),
      deny: o.deny.map((p) => PermissionFlagsBits[p]),
    });
  }
  for (const memberId of extraMembers) {
    list.push({
      id: memberId,
      type: OverwriteType.Member,
      allow: [
        PermissionFlagsBits.ViewChannel,
        PermissionFlagsBits.SendMessages,
        PermissionFlagsBits.ReadMessageHistory,
      ],
      deny: [],
    });
  }
  return list;
}

/**
 * Create or update the full Beezy server layout. Safe to run repeatedly: finds
 * roles/categories/channels by name first and only creates what is missing;
 * topics, role colors, parents and permission overwrites are updated on
 * existing objects. permissionOverwrites.set() REPLACES a channel's
 * overwrites, so manual per-channel overrides are reset on each run.
 *
 * @param {import('discord.js').Guild} guild
 * @param {{ownerId?: string, botUserId?: string, reason?: string}} [opts]
 */
export async function provisionBeezyServer(guild, { ownerId, botUserId, reason = 'Beezy server setup' } = {}) {
  const result = {
    roles: [], categories: [], channels: [], webhooks: [], warnings: [],
  };

  await guild.roles.fetch();
  await guild.channels.fetch();

  // Roles
  for (const spec of ROLE_SPECS) {
    const color = hexToInt(spec.color);
    const existing = findRole(guild, spec.name);
    if (!existing) {
      const created = await guild.roles.create({ name: spec.name, color, mentionable: false, reason });
      guild.roles.cache.set?.(created.id, created);
      result.roles.push({ name: spec.name, status: 'created' });
    } else if (existing.color !== color) {
      await existing.setColor(color, reason);
      result.roles.push({ name: spec.name, status: 'updated' });
    } else {
      result.roles.push({ name: spec.name, status: 'existing' });
    }
  }

  // Resolve owner membership once (an overwrite for a non-member id fails).
  const extraForGated = [];
  if (botUserId) extraForGated.push(botUserId);
  const ownerExtra = [];
  if (ownerId) {
    try {
      const member = await guild.members.fetch(ownerId);
      if (member) ownerExtra.push(ownerId);
    } catch {
      result.warnings.push('Owner is not a member of this guild; owner-only channels rely on Administrator roles.');
    }
  }

  // Categories + channels
  for (const category of LAYOUT) {
    const catSpec = overwritesFor(category);
    const isPublicCat = category.visibleTo.includes(EVERYONE);
    const catExtras = isPublicCat ? [] : [...new Set([...extraForGated, ...ownerExtra])];
    const catOverwrites = toDiscordOverwrites(guild, catSpec, { extraMembers: catExtras });

    let cat = findCategory(guild, category.name);
    if (!cat) {
      cat = await guild.channels.create({
        name: category.name, type: ChannelType.GuildCategory, permissionOverwrites: catOverwrites, reason,
      });
      guild.channels.cache.set?.(cat.id, cat);
      result.categories.push({ name: category.name, status: 'created' });
    } else {
      await cat.permissionOverwrites.set(catOverwrites, reason);
      result.categories.push({ name: category.name, status: 'existing' });
    }

    for (const ch of category.channels) {
      const spec = overwritesFor(category, ch);
      const isPublic = visibleSet(category, ch).includes(EVERYONE);
      const hidden = visibleSet(category, ch).includes(OWNER_ONLY);
      const extras = isPublic ? [] : [...new Set([...extraForGated, ...(hidden ? ownerExtra : [])])];
      const overwrites = toDiscordOverwrites(guild, spec, { extraMembers: extras });

      let channel = findText(guild, ch.name);
      if (!channel) {
        channel = await guild.channels.create({
          name: ch.name,
          type: ChannelType.GuildText,
          parent: cat.id,
          topic: ch.topic,
          permissionOverwrites: overwrites,
          reason,
        });
        guild.channels.cache.set?.(channel.id, channel);
        result.channels.push({ name: ch.name, status: 'created' });
      } else {
        const changes = [];
        if (channel.parentId !== cat.id) {
          await channel.setParent(cat.id, { lockPermissions: false, reason });
          changes.push('moved');
        }
        if ((channel.topic ?? '') !== ch.topic) {
          await channel.setTopic(ch.topic, reason);
          changes.push('topic');
        }
        await channel.permissionOverwrites.set(overwrites, reason);
        result.channels.push({ name: ch.name, status: 'existing', changes });
      }
    }
  }

  // Webhooks
  for (const target of WEBHOOK_TARGETS) {
    const channel = findText(guild, target.channel);
    if (!channel) {
      result.warnings.push(`#${target.channel} not found; webhook skipped.`);
      continue;
    }
    try {
      const hooks = await channel.fetchWebhooks();
      let hook = hooks.find((h) => h.name === WEBHOOK_NAME && h.url);
      let status = 'existing';
      if (!hook) {
        hook = await channel.createWebhook({ name: WEBHOOK_NAME, reason });
        status = 'created';
      }
      result.webhooks.push({ ...target, url: hook.url, status });
    } catch (err) {
      result.warnings.push(`#${target.channel}: webhook failed (${String(err?.message || err).slice(0, 100)}).`);
    }
  }

  return result;
}
