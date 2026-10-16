// /server status: pure diff of a guild snapshot against the exported layout
// spec, plus the legacy leftovers and the role-hierarchy check the tier sync
// depends on. Snapshot shape: see cleanup.js.
import { LAYOUT, PAID_ROLE_NAMES, ROLE_SPECS } from './serverSetup.js';
import { ARCHIVE_CATEGORY, LEGACY, norm } from './legacy.js';

export function buildStatus(snapshot) {
  const catByNorm = new Map(snapshot.categories.map((c) => [norm(c.name), c]));
  const roleByNorm = new Map(snapshot.roles.map((r) => [norm(r.name), r]));
  const catById = new Map(snapshot.categories.map((c) => [c.id, c]));
  const archive = catByNorm.get(norm(ARCHIVE_CATEGORY));

  const missingRoles = ROLE_SPECS.filter((r) => !roleByNorm.has(norm(r.name))).map((r) => r.name);
  const missingCategories = LAYOUT.filter((c) => !catByNorm.has(norm(c.name))).map((c) => c.name);
  const missingChannels = [];
  const misplacedChannels = [];
  for (const cat of LAYOUT) {
    for (const ch of cat.channels) {
      const found = snapshot.channels.find((c) => norm(c.name) === norm(ch.name));
      if (!found) {
        missingChannels.push(ch.name);
      } else if (norm(catById.get(found.parentId)?.name) !== norm(cat.name)) {
        misplacedChannels.push(`#${ch.name} (expected in ${cat.name})`);
      }
    }
  }

  const legacyChannels = snapshot.channels
    .filter((c) => LEGACY.channels.map(norm).includes(norm(c.name)) && !(archive && c.parentId === archive.id))
    .map((c) => c.name);
  const legacyCategories = snapshot.categories
    .filter((c) => LEGACY.categories.map(norm).includes(norm(c.name)))
    .map((c) => c.name);
  const legacyRoles = snapshot.roles
    .filter((r) => LEGACY.roles.map(norm).includes(norm(r.name)))
    .map((r) => ({ name: r.name, memberCount: r.memberCount ?? 0 }));

  const paidPresent = PAID_ROLE_NAMES.map((n) => roleByNorm.get(norm(n))).filter(Boolean);
  let botAbovePaidRoles = null;
  let rolesAtOrAboveBot = [];
  if (Number.isFinite(snapshot.botHighest)) {
    rolesAtOrAboveBot = paidPresent.filter((r) => r.position >= snapshot.botHighest).map((r) => r.name);
    botAbovePaidRoles = rolesAtOrAboveBot.length === 0;
  }

  return {
    counts: {
      categories: snapshot.categories.length,
      channels: snapshot.channels.length,
      roles: snapshot.roles.length,
    },
    missingRoles,
    missingCategories,
    missingChannels,
    misplacedChannels,
    legacyChannels,
    legacyCategories,
    legacyRoles,
    archiveExists: Boolean(archive),
    botAbovePaidRoles,
    rolesAtOrAboveBot,
  };
}

export function formatStatus(status) {
  const list = (items) => (items.length ? items.join(', ') : 'none');
  const hierarchy = status.botAbovePaidRoles === null
    ? 'unknown (bot role not visible)'
    : status.botAbovePaidRoles
      ? 'yes, tier sync can assign every paid role'
      : `NO, move the bot role above: ${status.rolesAtOrAboveBot.join(', ')}`;
  return [
    `Counts: ${status.counts.categories} categories, ${status.counts.channels} channels, ${status.counts.roles} roles`,
    '',
    '**New layout**',
    `Missing roles: ${list(status.missingRoles)}`,
    `Missing categories: ${list(status.missingCategories)}`,
    `Missing channels: ${list(status.missingChannels.map((n) => `#${n}`))}`,
    `Misplaced channels: ${list(status.misplacedChannels)}`,
    '',
    '**Legacy remaining**',
    `Categories: ${list(status.legacyCategories)}`,
    `Channels: ${list(status.legacyChannels.map((n) => `#${n}`))}`,
    `Roles: ${list(status.legacyRoles.map((r) => `${r.name} (${r.memberCount})`))}`,
    `${ARCHIVE_CATEGORY} category: ${status.archiveExists ? 'present' : 'absent'}`,
    '',
    `**Bot role above every paid role:** ${hierarchy}`,
  ].join('\n');
}
