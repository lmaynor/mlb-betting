// Protection rules shared by /cleanup and the admin primitives. Pure: they
// work on plain {id, name, managed, position, isAdmin} snapshots.
import { PermissionFlagsBits } from 'discord.js';
import { LAYOUT, ROLE_SPECS } from './serverSetup.js';
import { ARCHIVE_CATEGORY, PROTECTED_ROLE_NAMES, norm } from './legacy.js';

const protectedRoles = new Set(PROTECTED_ROLE_NAMES.map(norm));

export const NEW_ROLE_NAMES = new Set(ROLE_SPECS.map((r) => norm(r.name)));
export const NEW_CATEGORY_NAMES = new Set([...LAYOUT.map((c) => norm(c.name)), norm(ARCHIVE_CATEGORY)]);
export const NEW_CHANNEL_NAMES = new Set(LAYOUT.flatMap((c) => c.channels.map((ch) => norm(ch.name))));

/** Convert a live discord.js Role into the plain shape used here. */
export function roleInfo(role, guildId) {
  return {
    id: role.id,
    name: role.name,
    managed: Boolean(role.managed),
    position: role.position ?? 0,
    isEveryone: role.id === guildId,
    isAdmin: Boolean(role.permissions?.has?.(PermissionFlagsBits.Administrator)),
  };
}

/**
 * Why this role must not be touched, or null if it may be.
 * @param {{id: string, name: string, managed?: boolean, position?: number, isEveryone?: boolean, isAdmin?: boolean}} role
 * @param {{botHighest?: number}} [ctx] position of the bot's highest role
 */
export function roleRefusal(role, { botHighest } = {}) {
  if (role.isEveryone || protectedRoles.has(norm(role.name))) return `${role.name} is a protected role`;
  if (role.managed) return `${role.name} is a managed (bot/integration) role`;
  if (NEW_ROLE_NAMES.has(norm(role.name))) return `${role.name} belongs to the new Beezy layout`;
  if (role.isAdmin) return `${role.name} has Administrator`;
  if (Number.isFinite(botHighest) && role.position >= botHighest) {
    return `${role.name} is at or above the bot's highest role`;
  }
  return null;
}

/** Why this channel (any non-category) must not be touched, or null. */
export function channelRefusal(channel) {
  if (NEW_CHANNEL_NAMES.has(norm(channel.name))) return `#${channel.name} belongs to the new Beezy layout`;
  return null;
}

/** Why this category must not be touched, or null. */
export function categoryRefusal(category) {
  if (NEW_CATEGORY_NAMES.has(norm(category.name))) return `${category.name} belongs to the new Beezy layout`;
  return null;
}
