// /cleanup planner and executors. The planner is pure (plain snapshots in,
// plan out) so it can be unit-tested without Discord.
//
// Snapshot shape:
//   { guildId, botHighest, archiveCategoryId?,
//     categories: [{id, name}],
//     channels:   [{id, name, parentId}],          // non-category channels
//     roles:      [{id, name, managed, position, memberCount, isAdmin, isEveryone}] }
import { ChannelType, OverwriteType, PermissionFlagsBits } from 'discord.js';
import { ARCHIVE_CATEGORY, LEGACY, norm } from './legacy.js';
import {
  NEW_CATEGORY_NAMES, NEW_CHANNEL_NAMES, categoryRefusal, channelRefusal, roleInfo, roleRefusal,
} from './protect.js';

const set = (list) => new Set(list.map(norm));

/**
 * @returns {{
 *   archiveCategoryExists: boolean,
 *   archiveChannels: {id, name, from}[],
 *   deleteCategories: {id, name, reason}[],
 *   keep: {kind, name, reason}[],
 * }}
 */
export function planChannelCleanup(snapshot, legacy = LEGACY) {
  const legacyChannels = set(legacy.channels);
  const legacyCategories = set(legacy.categories);
  const categoryById = new Map(snapshot.categories.map((c) => [c.id, c]));
  const archive = snapshot.categories.find((c) => norm(c.name) === norm(ARCHIVE_CATEGORY));

  const archiveChannels = [];
  const keep = [];
  for (const ch of snapshot.channels) {
    if (!legacyChannels.has(norm(ch.name))) continue; // not legacy: never touched
    if (NEW_CHANNEL_NAMES.has(norm(ch.name)) || channelRefusal(ch)) continue; // new set: never touched
    const parent = ch.parentId ? categoryById.get(ch.parentId) : null;
    if (archive && ch.parentId === archive.id) continue; // already archived (idempotent)
    if (parent && NEW_CATEGORY_NAMES.has(norm(parent.name))) {
      keep.push({ kind: 'channel', name: ch.name, reason: `inside new category ${parent.name}` });
      continue;
    }
    archiveChannels.push({ id: ch.id, name: ch.name, from: parent?.name ?? null });
  }

  const archivedIds = new Set(archiveChannels.map((c) => c.id));
  const deleteCategories = [];
  for (const cat of snapshot.categories) {
    if (!legacyCategories.has(norm(cat.name))) continue; // not legacy: never touched
    if (categoryRefusal(cat)) continue; // new set / ARCHIVE: never touched
    const children = snapshot.channels.filter((c) => c.parentId === cat.id);
    const remaining = children.filter((c) => !archivedIds.has(c.id));
    if (remaining.length === 0) {
      deleteCategories.push({
        id: cat.id,
        name: cat.name,
        reason: children.length === 0 ? 'empty' : 'empty once its legacy channels are archived',
      });
    } else {
      keep.push({
        kind: 'category',
        name: cat.name,
        reason: `still holds ${remaining.map((c) => `#${c.name}`).join(', ')}`,
      });
    }
  }

  return {
    archiveCategoryExists: Boolean(archive),
    archiveChannels,
    deleteCategories,
    keep,
  };
}

/**
 * Legacy roles as REMOVE-CANDIDATEs. Protected, managed, new-layout and
 * Administrator roles never appear. A role with members is `needs-force` and
 * only becomes `remove` when force is true. A role at or above the bot's
 * highest role is `blocked-hierarchy`.
 * @returns {{id, name, memberCount, action: 'remove'|'needs-force'|'blocked-hierarchy'}[]}
 */
export function planRoleCleanup(snapshot, { force = false, legacy = LEGACY } = {}) {
  const legacyRoles = set(legacy.roles);
  const out = [];
  for (const role of snapshot.roles) {
    if (!legacyRoles.has(norm(role.name))) continue;
    // Hierarchy is reported separately below, so check the rest first.
    if (roleRefusal({ ...role, position: -1 })) continue;
    const memberCount = role.memberCount ?? 0;
    let action = 'remove';
    if (Number.isFinite(snapshot.botHighest) && role.position >= snapshot.botHighest) action = 'blocked-hierarchy';
    else if (memberCount > 0 && !force) action = 'needs-force';
    out.push({ id: role.id, name: role.name, memberCount, action });
  }
  return out;
}

export function formatChannelPlan(plan) {
  const lines = [];
  if (plan.archiveChannels.length) {
    lines.push(`**ARCHIVE** (move to hidden ${ARCHIVE_CATEGORY}${plan.archiveCategoryExists ? '' : ', created first'}; history is kept):`);
    for (const c of plan.archiveChannels) lines.push(`- #${c.name}${c.from ? ` (from ${c.from})` : ''}`);
  }
  if (plan.deleteCategories.length) {
    lines.push('**DELETE-EMPTY** categories:');
    for (const c of plan.deleteCategories) lines.push(`- ${c.name} (${c.reason})`);
  }
  if (plan.keep.length) {
    lines.push('**KEEP**:');
    for (const k of plan.keep) lines.push(`- ${k.kind} ${k.name}: ${k.reason}`);
  }
  if (!plan.archiveChannels.length && !plan.deleteCategories.length) {
    lines.unshift('Nothing to do: no legacy channels or empty legacy categories remain.');
  }
  return lines.join('\n');
}

export function formatRolePlan(candidates) {
  if (!candidates.length) return 'Nothing to do: no legacy roles remain.';
  const label = {
    remove: 'REMOVE',
    'needs-force': 'FLAGGED (has members; needs force:true)',
    'blocked-hierarchy': 'BLOCKED (at or above the bot role)',
  };
  return [
    '**Legacy roles** (REMOVE-CANDIDATE):',
    ...candidates.map((r) => `- ${r.name}: ${r.memberCount} member${r.memberCount === 1 ? '' : 's'} - ${label[r.action]}`),
  ].join('\n');
}

// -- live guild glue --------------------------------------------------------

/** Build a snapshot from a live guild. Fetches members so role counts are real. */
export async function snapshotGuild(guild, { withMembers = true } = {}) {
  await guild.roles.fetch();
  await guild.channels.fetch();
  if (withMembers) {
    try {
      await guild.members.fetch();
    } catch { /* counts fall back to the cache */ }
  }
  const all = [...guild.channels.cache.values()];
  const me = guild.members.me;
  return {
    guildId: guild.id,
    botHighest: me?.roles?.highest?.position,
    categories: all.filter((c) => c.type === ChannelType.GuildCategory).map((c) => ({ id: c.id, name: c.name })),
    channels: all.filter((c) => c.type !== ChannelType.GuildCategory)
      .map((c) => ({ id: c.id, name: c.name, parentId: c.parentId ?? null })),
    roles: [...guild.roles.cache.values()].map((r) => ({
      ...roleInfo(r, guild.id),
      memberCount: r.members?.size ?? 0,
    })),
  };
}

/** Apply a channel plan. Returns one result per step; never throws on a step failure. */
export async function applyChannelPlan(guild, plan, { ownerId, botUserId, reason = 'beezy /cleanup' } = {}) {
  const results = [];
  const step = async (label, fn) => {
    try {
      await fn();
      results.push({ label, ok: true });
    } catch (err) {
      results.push({ label, ok: false, error: String(err?.message || err).slice(0, 120) });
    }
  };

  let archive = guild.channels.cache.find(
    (c) => c.type === ChannelType.GuildCategory && norm(c.name) === norm(ARCHIVE_CATEGORY),
  );
  if (!archive && plan.archiveChannels.length) {
    const overwrites = [{ id: guild.roles.everyone?.id ?? guild.id, type: OverwriteType.Role, deny: [PermissionFlagsBits.ViewChannel] }];
    for (const id of [botUserId, ownerId].filter(Boolean)) {
      overwrites.push({ id, type: OverwriteType.Member, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory] });
    }
    await step(`create ${ARCHIVE_CATEGORY} category`, async () => {
      archive = await guild.channels.create({
        name: ARCHIVE_CATEGORY, type: ChannelType.GuildCategory, permissionOverwrites: overwrites, reason,
      });
    });
  }

  for (const c of plan.archiveChannels) {
    await step(`archive #${c.name}`, async () => {
      if (!archive) throw new Error(`${ARCHIVE_CATEGORY} category is unavailable`);
      const live = guild.channels.cache.get(c.id);
      if (!live) throw new Error('channel no longer exists');
      // lockPermissions syncs the channel to the hidden category (View denied for @everyone).
      await live.setParent(archive.id, { lockPermissions: true, reason });
    });
  }

  for (const c of plan.deleteCategories) {
    await step(`delete empty category ${c.name}`, async () => {
      const live = guild.channels.cache.get(c.id);
      if (!live) throw new Error('category no longer exists');
      const stillHas = [...guild.channels.cache.values()].some((x) => x.parentId === c.id);
      if (stillHas) throw new Error('category is no longer empty');
      await live.delete(reason);
    });
  }
  return results;
}

/** Delete the roles in a role plan that are actionable ('remove'). */
export async function applyRolePlan(guild, candidates, { reason = 'beezy /cleanup' } = {}) {
  const results = [];
  for (const r of candidates) {
    const label = `remove role ${r.name}`;
    if (r.action !== 'remove') {
      results.push({ label, ok: false, error: r.action === 'needs-force' ? 'has members (use force:true)' : 'at or above the bot role' });
      continue;
    }
    try {
      const live = guild.roles.cache.get(r.id);
      if (!live) throw new Error('role no longer exists');
      await live.delete(reason);
      results.push({ label, ok: true });
    } catch (err) {
      results.push({ label, ok: false, error: String(err?.message || err).slice(0, 120) });
    }
  }
  return results;
}

export function formatResults(results) {
  if (!results.length) return 'No steps ran.';
  const ok = results.filter((r) => r.ok).length;
  return [
    `${ok}/${results.length} steps succeeded.`,
    ...results.map((r) => `${r.ok ? 'OK  ' : 'FAIL'} ${r.label}${r.ok ? '' : ` - ${r.error}`}`),
  ].join('\n');
}
