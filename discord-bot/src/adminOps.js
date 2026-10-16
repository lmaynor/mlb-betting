// Admin primitives for the owner-only management commands. Adapted from the
// nodebot agentTools createChannel / createRole / setChannelAccess /
// setChannelTopic, with validation and protection added. Every function takes
// plain discord.js objects and throws AdminError with a message that is safe
// to show to the owner.
import { ChannelType, PermissionFlagsBits } from 'discord.js';
import {
  categoryRefusal, channelRefusal, roleInfo, roleRefusal,
} from './protect.js';

export class AdminError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AdminError';
  }
}

// -- validation (pure) --------------------------------------------------------

/** Text channel names: lowercase letters, digits, - and _; spaces become -. */
export function validateChannelName(raw) {
  const name = String(raw ?? '').trim().toLowerCase().replace(/\s+/g, '-');
  if (!/^[a-z0-9_-]{1,100}$/.test(name)) {
    throw new AdminError('Channel names must be 1-100 characters of letters, digits, hyphen or underscore.');
  }
  return name;
}

/** Category / role names: 1-100 printable characters (no control characters). */
export function validateLabel(raw, what = 'Name') {
  const name = String(raw ?? '').trim();
  const hasControl = [...name].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127);
  if (name.length < 1 || name.length > 100 || hasControl) {
    throw new AdminError(`${what} must be 1-100 printable characters.`);
  }
  return name;
}

export function validateTopic(raw) {
  const topic = String(raw ?? '');
  if (topic.length > 1024) throw new AdminError('Topics are limited to 1024 characters.');
  return topic;
}

/** "#5865F2" or "5865F2" -> integer. */
export function parseColor(raw) {
  const text = String(raw ?? '').trim();
  if (!/^#?[0-9a-fA-F]{6}$/.test(text)) throw new AdminError('Invalid color. Use a 6-digit hex like #5865F2.');
  return Number.parseInt(text.replace('#', ''), 16);
}

export function validateIntRange(value, min, max, what) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < min || n > max) throw new AdminError(`${what} must be a whole number from ${min} to ${max}.`);
  return n;
}

// -- protection guards (throw) ----------------------------------------------------

export function botHighestPosition(guild) {
  return guild.members?.me?.roles?.highest?.position;
}

/** Throws if the role is protected, managed, new-layout, admin, or at/above the bot's highest role. */
export function assertRoleManageable(guild, role) {
  const reason = roleRefusal(roleInfo(role, guild.id), { botHighest: botHighestPosition(guild) });
  if (reason) throw new AdminError(`Refusing: ${reason}.`);
}

export function assertChannelManageable(channel) {
  const reason = channel.type === ChannelType.GuildCategory ? categoryRefusal(channel) : channelRefusal(channel);
  if (reason) throw new AdminError(`Refusing: ${reason}.`);
}

/** Refuse to create something that collides with the protected / new set. */
export function assertNewChannelName(name) {
  const reason = channelRefusal({ name });
  if (reason) throw new AdminError(`Refusing: ${reason}.`);
}
export function assertNewCategoryName(name) {
  const reason = categoryRefusal({ name });
  if (reason) throw new AdminError(`Refusing: ${reason}.`);
}
export function assertNewRoleName(name) {
  const reason = roleRefusal({ id: 'new', name, position: -1 });
  if (reason) throw new AdminError(`Refusing: ${reason}.`);
}

// -- operations -----------------------------------------------------------------------

export async function createChannel(guild, { name, category, topic, privateChannel = false }, reason) {
  const clean = validateChannelName(name);
  assertNewChannelName(clean);
  if (category) assertChannelManageable(category);
  const everyone = guild.roles.everyone?.id ?? guild.id;
  return guild.channels.create({
    name: clean,
    type: ChannelType.GuildText,
    parent: category?.id,
    topic: topic ? validateTopic(topic) : undefined,
    permissionOverwrites: privateChannel ? [{ id: everyone, deny: [PermissionFlagsBits.ViewChannel] }] : undefined,
    reason,
  });
}

export async function renameChannel(channel, name, reason) {
  assertChannelManageable(channel);
  const clean = validateChannelName(name);
  assertNewChannelName(clean);
  await channel.setName(clean, reason);
  return clean;
}

export async function setChannelTopic(channel, topic, reason) {
  assertChannelManageable(channel);
  await channel.setTopic(validateTopic(topic), reason);
}

export async function moveChannel(channel, category, reason) {
  assertChannelManageable(channel);
  assertChannelManageable(category);
  await channel.setParent(category.id, { lockPermissions: false, reason });
}

export async function lockChannel(guild, channel, locked, reason) {
  assertChannelManageable(channel);
  const everyone = guild.roles.everyone ?? guild.id;
  await channel.permissionOverwrites.edit(everyone, { SendMessages: locked ? false : null }, { reason });
}

export async function setSlowmode(channel, seconds, reason) {
  assertChannelManageable(channel);
  const s = validateIntRange(seconds, 0, 21600, 'Slowmode seconds');
  await channel.setRateLimitPerUser(s, reason);
  return s;
}

/** mode: view | hide | read-only | reset, applied to one role on one channel. */
export async function setChannelAccess(guild, channel, role, mode, reason) {
  assertChannelManageable(channel);
  assertRoleManageable(guild, role);
  const edits = {
    view: { ViewChannel: true },
    hide: { ViewChannel: false },
    'read-only': { ViewChannel: true, SendMessages: false },
    reset: { ViewChannel: null, SendMessages: null },
  };
  if (!edits[mode]) throw new AdminError('Mode must be view, hide, read-only or reset.');
  await channel.permissionOverwrites.edit(role, edits[mode], { reason });
}

export async function createCategory(guild, name, reason) {
  const clean = validateLabel(name, 'Category name');
  assertNewCategoryName(clean);
  return guild.channels.create({ name: clean, type: ChannelType.GuildCategory, reason });
}

export async function renameCategory(category, name, reason) {
  assertChannelManageable(category);
  const clean = validateLabel(name, 'Category name');
  assertNewCategoryName(clean);
  await category.setName(clean, reason);
  return clean;
}

/** Categories are only deleted when empty, so no channel is ever lost with them. */
export function assertCategoryEmpty(guild, category) {
  const kids = [...guild.channels.cache.values()].filter((c) => c.parentId === category.id);
  if (kids.length) {
    throw new AdminError(`${category.name} still holds ${kids.length} channel(s). Move or archive them first.`);
  }
}

export async function createRole(guild, { name, color }, reason) {
  const clean = validateLabel(name, 'Role name');
  assertNewRoleName(clean);
  return guild.roles.create({
    name: clean, color: color === undefined || color === null ? undefined : parseColor(color), mentionable: false, reason,
  });
}

export async function renameRole(guild, role, name, reason) {
  assertRoleManageable(guild, role);
  const clean = validateLabel(name, 'Role name');
  assertNewRoleName(clean);
  await role.setName(clean, reason);
  return clean;
}

export async function recolorRole(guild, role, color, reason) {
  assertRoleManageable(guild, role);
  await role.setColor(parseColor(color), reason);
}

export async function giveRole(guild, member, role, reason) {
  assertRoleManageable(guild, role);
  await member.roles.add(role, reason);
}

export async function takeRole(guild, member, role, reason) {
  assertRoleManageable(guild, role);
  await member.roles.remove(role, reason);
}

export async function purgeMessages(channel, count) {
  assertChannelManageable(channel);
  const n = validateIntRange(count, 1, 100, 'Count');
  // filterOld=true skips messages older than 14 days instead of throwing.
  const deleted = await channel.bulkDelete(n, true);
  return deleted?.size ?? 0;
}
