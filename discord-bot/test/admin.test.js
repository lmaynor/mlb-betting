import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  ChannelType, Collection, MessageFlags, OverwriteType, PermissionFlagsBits,
} from 'discord.js';
import * as db from '../src/db.js';
import { loadCommands } from '../src/load-commands.js';
import { LEGACY, norm } from '../src/legacy.js';
import { LAYOUT, ROLE_SPECS } from '../src/serverSetup.js';
import * as cleanup from '../src/cleanup.js';
import * as ops from '../src/adminOps.js';
import { askConfirm } from '../src/confirm.js';
import { ownerGuard } from '../src/commandUtils.js';
import { buildStatus, formatStatus } from '../src/serverStatus.js';

beforeEach(() => db.initDb(':memory:'));
afterEach(() => db.closeDb());

const OWNER = '900000000000000001';
const NEW_CHANNELS = LAYOUT.flatMap((c) => c.channels.map((ch) => ch.name));
const NEW_CATEGORIES = LAYOUT.map((c) => c.name);
const NEW_ROLES = ROLE_SPECS.map((r) => r.name);

// -- plain snapshot fixture (planner is pure) -----------------------------------------------

function liveInventorySnapshot(over = {}) {
  let n = 0;
  const id = () => `id${++n}`;
  const cats = {};
  const categories = [];
  for (const name of [...LEGACY.categories, ...NEW_CATEGORIES]) {
    cats[name] = { id: id(), name };
    categories.push(cats[name]);
  }
  const channels = [];
  const put = (name, parent) => channels.push({ id: id(), name, parentId: parent ? cats[parent].id : null });
  put('verify', 'ONBOARDING'); put('preferences', 'ONBOARDING');
  put('rules', 'INFO');
  put('daily-picks', 'PICKS'); put('performance', 'PICKS');
  put('general', 'COMMUNITY'); put('paper-feedback', 'COMMUNITY');
  for (const cat of LAYOUT) for (const ch of cat.channels) put(ch.name, cat.name);
  const roles = [
    ...['Member', 'Member Pro', 'Paper Tester', 'Playbook', 'DraftKings', 'FanDuel', 'Caesars', 'BetMGM', 'theScore', 'PointsBet']
      .map((name, i) => ({ id: id(), name, managed: false, position: 5 + i, memberCount: i === 0 ? 40 : 0, isAdmin: false })),
    ...['Admin', 'Moderator', 'Bot', 'GamblyBot', 'carl-bot', 'beezy-bot']
      .map((name, i) => ({ id: id(), name, managed: i >= 2, position: 60 + i, memberCount: 3, isAdmin: name === 'Admin' })),
    ...NEW_ROLES.map((name, i) => ({ id: id(), name, managed: false, position: 20 + i, memberCount: 1, isAdmin: false })),
    { id: 'GUILD', name: '@everyone', managed: false, position: 0, memberCount: 100, isAdmin: false, isEveryone: true },
  ];
  return { guildId: 'GUILD', botHighest: 50, categories, channels, roles, cats, ...over };
}

test('channel plan: every legacy channel is ARCHIVE, emptied legacy categories are DELETE-EMPTY', () => {
  const snap = liveInventorySnapshot();
  const plan = cleanup.planChannelCleanup(snap);
  assert.deepEqual(plan.archiveChannels.map((c) => c.name).sort(), [...LEGACY.channels].sort());
  assert.deepEqual(plan.deleteCategories.map((c) => c.name).sort(), [...LEGACY.categories].sort());
  assert.equal(plan.archiveCategoryExists, false);
  assert.deepEqual(plan.keep, []);
  const ops = plan.deleteCategories.find((c) => c.name === 'OPS');
  assert.equal(ops.reason, 'empty');
  assert.match(plan.deleteCategories.find((c) => c.name === 'INFO').reason, /once its legacy channels are archived/);
});

test('channel plan: the new layout never appears in a plan (not even if listed as legacy)', () => {
  const snap = liveInventorySnapshot();
  const poisoned = {
    categories: [...LEGACY.categories, ...NEW_CATEGORIES, 'ARCHIVE'],
    channels: [...LEGACY.channels, ...NEW_CHANNELS],
    roles: LEGACY.roles,
  };
  const plan = cleanup.planChannelCleanup(snap, poisoned);
  const named = [
    ...plan.archiveChannels.map((c) => c.name),
    ...plan.deleteCategories.map((c) => c.name),
    ...plan.keep.map((k) => k.name),
  ].map(norm);
  for (const name of [...NEW_CHANNELS, ...NEW_CATEGORIES, 'ARCHIVE']) {
    assert.ok(!named.includes(norm(name)), `${name} must not be in the plan`);
  }
  // legacy "OPS" must not be confused with the new "OPS & TWEET QUEUE"
  assert.ok(plan.deleteCategories.some((c) => c.name === 'OPS'));
  assert.ok(!plan.deleteCategories.some((c) => /tweet/i.test(c.name)));
});

test('channel plan: channels and categories outside the legacy list are never touched', () => {
  const snap = liveInventorySnapshot();
  const extra = { id: 'x1', name: 'random-chat', parentId: snap.cats.COMMUNITY.id };
  snap.channels.push(extra);
  snap.categories.push({ id: 'x2', name: 'VIP LOUNGE' }); // empty, but not legacy-listed
  const plan = cleanup.planChannelCleanup(snap);
  assert.ok(!plan.archiveChannels.some((c) => c.name === 'random-chat'));
  assert.ok(!plan.deleteCategories.some((c) => c.name === 'VIP LOUNGE'), 'empty non-legacy category is left alone');
  // COMMUNITY still holds a non-legacy channel -> KEEP, not deleted
  assert.ok(!plan.deleteCategories.some((c) => c.name === 'COMMUNITY'));
  const keep = plan.keep.find((k) => k.name === 'COMMUNITY');
  assert.match(keep.reason, /#random-chat/);
  // its legacy children are still archived
  assert.ok(plan.archiveChannels.some((c) => c.name === 'general'));
});

test('channel plan: empty category rule and idempotency after the legacy set is gone', () => {
  const snap = liveInventorySnapshot();
  const plan = cleanup.planChannelCleanup(snap);
  // simulate apply: ARCHIVE exists holding the legacy channels, legacy categories deleted
  const archive = { id: 'arch', name: 'ARCHIVE' };
  const moved = new Set(plan.archiveChannels.map((c) => c.id));
  const after = {
    ...snap,
    categories: [...snap.categories.filter((c) => !LEGACY.categories.includes(c.name)), archive],
    channels: snap.channels.map((c) => (moved.has(c.id) ? { ...c, parentId: archive.id } : c)),
  };
  const again = cleanup.planChannelCleanup(after);
  assert.deepEqual(again.archiveChannels, []);
  assert.deepEqual(again.deleteCategories, []);
  assert.match(cleanup.formatChannelPlan(again), /Nothing to do/);
});

test('channel plan: a legacy-named channel inside a new category is kept, not archived', () => {
  const snap = liveInventorySnapshot();
  snap.channels.push({ id: 'z1', name: 'rules', parentId: snap.cats['START HERE'].id });
  const plan = cleanup.planChannelCleanup(snap);
  assert.ok(!plan.archiveChannels.some((c) => c.id === 'z1'));
  assert.ok(plan.keep.some((k) => k.name === 'rules' && /new category/.test(k.reason)));
});

test('role plan: members flag a role; force is required to remove it', () => {
  const snap = liveInventorySnapshot();
  const normal = cleanup.planRoleCleanup(snap);
  assert.deepEqual(normal.map((r) => r.name).sort(), [...LEGACY.roles].sort());
  const member = normal.find((r) => r.name === 'Member');
  assert.equal(member.memberCount, 40);
  assert.equal(member.action, 'needs-force');
  assert.ok(normal.filter((r) => r.name !== 'Member').every((r) => r.action === 'remove'));
  const forced = cleanup.planRoleCleanup(snap, { force: true });
  assert.equal(forced.find((r) => r.name === 'Member').action, 'remove');
  assert.match(cleanup.formatRolePlan(normal), /Member: 40 members - FLAGGED/);
});

test('role plan: protected, managed, new-layout and Administrator roles never appear, even if listed', () => {
  const snap = liveInventorySnapshot();
  snap.roles.push({ id: 'm1', name: 'Member', managed: true, position: 3, memberCount: 0, isAdmin: false }); // managed duplicate
  snap.roles.push({ id: 'a1', name: 'PointsBet', managed: false, position: 4, memberCount: 0, isAdmin: true }); // admin
  const poisoned = {
    ...LEGACY,
    roles: [...LEGACY.roles, '@everyone', 'Admin', 'Moderator', 'Bot', 'GamblyBot', 'carl-bot', 'beezy-bot', ...NEW_ROLES],
  };
  const plan = cleanup.planRoleCleanup(snap, { force: true, legacy: poisoned });
  const names = plan.map((r) => r.name);
  for (const banned of ['@everyone', 'Admin', 'Moderator', 'Bot', 'GamblyBot', 'carl-bot', 'beezy-bot', ...NEW_ROLES]) {
    assert.ok(!names.includes(banned), `${banned} must not appear`);
  }
  assert.ok(!plan.some((r) => r.id === 'm1' || r.id === 'a1'));
});

test('role plan: a role at or above the bot role is blocked, not removable', () => {
  const snap = liveInventorySnapshot();
  snap.roles.find((r) => r.name === 'Caesars').position = 50;
  snap.roles.find((r) => r.name === 'FanDuel').position = 70;
  const plan = cleanup.planRoleCleanup(snap, { force: true });
  assert.equal(plan.find((r) => r.name === 'Caesars').action, 'blocked-hierarchy');
  assert.equal(plan.find((r) => r.name === 'FanDuel').action, 'blocked-hierarchy');
});

// -- live-guild fake -----------------------------------------------------------------------

function makeGuild({ botPos = 50, legacyRoles = [], extraRoles = [], layout = true, failMove = [] } = {}) {
  let seq = 0;
  const nid = () => `n${++seq}`;
  const log = [];
  const guild = { id: 'GUILD', log };
  guild.roles = { cache: new Collection(), everyone: { id: 'GUILD', name: '@everyone' }, fetch: async () => guild.roles.cache };
  guild.channels = { cache: new Collection(), fetch: async () => guild.channels.cache };
  const addRole = (name, position, { managed = false, isAdmin = false, members = 0 } = {}) => {
    const role = {
      id: nid(), name, position, managed, members: new Collection(Array.from({ length: members }, (_, i) => [`m${i}`, {}])),
      permissions: { has: (p) => isAdmin && p === PermissionFlagsBits.Administrator },
      delete: async () => { log.push(['role.delete', name]); guild.roles.cache.delete(role.id); },
      setName: async (n) => { log.push(['role.setName', n]); role.name = n; },
      setColor: async (c) => { log.push(['role.setColor', c]); },
    };
    guild.roles.cache.set(role.id, role);
    return role;
  };
  const addChannel = (name, type, parentId = null) => {
    const ch = {
      id: nid(), name, type, parentId,
      permissionOverwrites: { edit: async (target, edits) => { log.push(['overwrites.edit', name, edits]); } },
      setParent: async (pid, o) => {
        if (failMove.includes(name)) throw new Error('Missing Permissions');
        log.push(['setParent', name, pid, o?.lockPermissions]); ch.parentId = pid;
      },
      setName: async (n) => { log.push(['setName', n]); ch.name = n; },
      setTopic: async (t) => { log.push(['setTopic', name, t]); },
      setRateLimitPerUser: async (s) => { log.push(['slowmode', name, s]); },
      bulkDelete: async (n) => { log.push(['bulkDelete', name, n]); return new Collection(Array.from({ length: n }, (_, i) => [i, {}])); },
      delete: async () => { log.push(['channel.delete', name]); guild.channels.cache.delete(ch.id); },
    };
    guild.channels.cache.set(ch.id, ch);
    return ch;
  };
  guild.channels.create = async (o) => {
    log.push(['channels.create', o.name, o.type, o.permissionOverwrites]);
    return addChannel(o.name, o.type, o.parent ?? null);
  };
  guild.roles.create = async (o) => { log.push(['roles.create', o.name, o.color]); return addRole(o.name, 1); };
  const bot = addRole('Beezy Bot', botPos, { managed: true });
  guild.members = { me: { roles: { highest: bot } }, fetch: async () => new Collection() };
  guild.addRole = addRole;
  guild.addChannel = addChannel;

  for (const [name, pos, extra] of [['Admin', 90, { isAdmin: true }], ['Moderator', 80, {}], ['GamblyBot', 40, { managed: true }], ['carl-bot', 41, { managed: true }]]) addRole(name, pos, extra);
  for (const [i, name] of ['Member', 'Member Pro', 'DraftKings'].entries()) addRole(name, 5 + i, { members: i === 0 ? 2 : 0 });
  for (const name of legacyRoles) addRole(name, 9);
  for (const r of extraRoles) addRole(r.name, r.position, r);
  if (layout) {
    for (const name of NEW_ROLES) addRole(name, 20);
    for (const cat of LAYOUT) {
      const c = addChannel(cat.name, ChannelType.GuildCategory);
      for (const ch of cat.channels) addChannel(ch.name, ChannelType.GuildText, c.id);
    }
  }
  const cats = {};
  for (const name of LEGACY.categories) cats[name] = addChannel(name, ChannelType.GuildCategory);
  const put = (name, parent) => addChannel(name, ChannelType.GuildText, cats[parent].id);
  put('verify', 'ONBOARDING'); put('preferences', 'ONBOARDING'); put('rules', 'INFO');
  put('daily-picks', 'PICKS'); put('performance', 'PICKS'); put('general', 'COMMUNITY'); put('paper-feedback', 'COMMUNITY');
  guild.client = { user: { id: 'BOT' } };
  return guild;
}

test('snapshotGuild reads categories, channels, roles with member counts and the bot position', async () => {
  const guild = makeGuild();
  const snap = await cleanup.snapshotGuild(guild);
  assert.equal(snap.botHighest, 50);
  assert.equal(snap.categories.length, LAYOUT.length + LEGACY.categories.length);
  assert.equal(snap.roles.find((r) => r.name === 'Member').memberCount, 2);
  assert.equal(snap.roles.find((r) => r.name === 'Beezy Bot').managed, true);
});

test('applyChannelPlan: creates a hidden ARCHIVE, archives with lockPermissions, deletes emptied categories, is idempotent', async () => {
  const guild = makeGuild();
  const plan = cleanup.planChannelCleanup(await cleanup.snapshotGuild(guild));
  const results = await cleanup.applyChannelPlan(guild, plan, { ownerId: OWNER, botUserId: 'BOT' });
  assert.ok(results.every((r) => r.ok), JSON.stringify(results.filter((r) => !r.ok)));
  assert.equal(results.length, 1 + 7 + 5);

  const create = guild.log.find((l) => l[0] === 'channels.create' && l[1] === 'ARCHIVE');
  assert.equal(create[2], ChannelType.GuildCategory);
  const everyone = create[3].find((o) => o.id === 'GUILD');
  assert.deepEqual(everyone.deny, [PermissionFlagsBits.ViewChannel]);
  assert.ok(create[3].some((o) => o.id === OWNER && o.type === OverwriteType.Member));

  const moves = guild.log.filter((l) => l[0] === 'setParent');
  assert.equal(moves.length, 7);
  assert.ok(moves.every((m) => m[3] === true), 'moved with lockPermissions so they inherit the hidden category');
  assert.deepEqual(guild.log.filter((l) => l[0] === 'channel.delete').map((l) => l[1]).sort(), [...LEGACY.categories].sort());
  assert.equal(guild.log.some((l) => l[0] === 'channel.delete' && (NEW_CHANNELS.includes(l[1]) || NEW_CATEGORIES.includes(l[1]))), false);
  // nothing was deleted except empty categories: no legacy channel was destroyed
  assert.equal([...guild.channels.cache.values()].filter((c) => LEGACY.channels.includes(c.name)).length, 7);

  const again = cleanup.planChannelCleanup(await cleanup.snapshotGuild(guild));
  assert.deepEqual(again.archiveChannels, []);
  assert.deepEqual(again.deleteCategories, []);
  assert.equal(again.archiveCategoryExists, true);
});

test('applyChannelPlan reports per-step failures and keeps going; a failed move keeps its category', async () => {
  const guild = makeGuild({ failMove: ['rules'] });
  const plan = cleanup.planChannelCleanup(await cleanup.snapshotGuild(guild));
  const results = await cleanup.applyChannelPlan(guild, plan, { ownerId: OWNER });
  const failed = results.filter((r) => !r.ok);
  assert.deepEqual(failed.map((r) => r.label).sort(), ['archive #rules', 'delete empty category INFO']);
  assert.match(failed.find((r) => r.label === 'archive #rules').error, /Missing Permissions/);
  assert.match(failed.find((r) => r.label.startsWith('delete empty')).error, /no longer empty/);
  assert.match(cleanup.formatResults(results), /FAIL archive #rules/);
});

test('applyRolePlan removes only actionable roles and reports the rest', async () => {
  const guild = makeGuild();
  const candidates = cleanup.planRoleCleanup(await cleanup.snapshotGuild(guild));
  const results = await cleanup.applyRolePlan(guild, candidates);
  assert.deepEqual(guild.log.filter((l) => l[0] === 'role.delete').map((l) => l[1]).sort(), ['DraftKings', 'Member Pro']);
  const member = results.find((r) => r.label === 'remove role Member');
  assert.equal(member.ok, false);
  assert.match(member.error, /force:true/);
  for (const keep of ['Admin', 'Moderator', 'GamblyBot', 'carl-bot', 'Beezy Bot', ...NEW_ROLES]) {
    assert.ok([...guild.roles.cache.values()].some((r) => r.name === keep), `${keep} still exists`);
  }
  // force removes the role that has members
  const forced = cleanup.planRoleCleanup(await cleanup.snapshotGuild(guild), { force: true });
  await cleanup.applyRolePlan(guild, forced);
  assert.ok(guild.log.some((l) => l[0] === 'role.delete' && l[1] === 'Member'));
  assert.deepEqual(cleanup.planRoleCleanup(await cleanup.snapshotGuild(guild), { force: true }), [], 'idempotent');
});

// -- fake interaction ----------------------------------------------------------------------------

function fakeInteraction(guild, { userId = OWNER, sub, values = {} } = {}) {
  const calls = { defer: null, edits: [], replies: [] };
  return {
    calls,
    guild,
    client: guild?.client,
    channel: values.__channel ?? null,
    user: { id: userId, tag: 'owner#1' },
    options: {
      getSubcommand: () => sub,
      getString: (n, req) => { const v = values[n] ?? null; if (req && v === null) throw new Error(`missing ${n}`); return v; },
      getChannel: (n, req) => { const v = values[n] ?? null; if (req && v === null) throw new Error(`missing ${n}`); return v; },
      getRole: (n, req) => { const v = values[n] ?? null; if (req && v === null) throw new Error(`missing ${n}`); return v; },
      getMember: (n) => values[n] ?? null,
      getBoolean: (n) => values[n] ?? null,
      getInteger: (n, req) => { const v = values[n] ?? null; if (req && v === null) throw new Error(`missing ${n}`); return v; },
    },
    deferReply: async (a) => { calls.defer = a ?? {}; },
    editReply: async (a) => { calls.edits.push(a); return { awaitMessageComponent: async () => { throw new Error('no click'); } }; },
    followUp: async (a) => { calls.replies.push(a); },
  };
}

const lastText = (i) => {
  const e = i.calls.edits.at(-1);
  if (!e) return '';
  return `${e.content ?? ''} ${JSON.stringify(e.embeds?.map((x) => x.toJSON()) ?? [])}`;
};

test('owner guard: ephemeral defer, rejects non-owners and an unset OWNER_ID, accepts the owner', async () => {
  const guild = makeGuild();
  const stranger = fakeInteraction(guild, { userId: '111' });
  assert.equal(await ownerGuard(stranger, { ownerId: OWNER }), false);
  assert.equal(stranger.calls.defer.flags, MessageFlags.Ephemeral);
  assert.match(lastText(stranger), /Owner-only/);

  const saved = process.env.OWNER_ID;
  delete process.env.OWNER_ID;
  try {
    assert.equal(await ownerGuard(fakeInteraction(guild, { userId: OWNER }), {}), false, 'unset OWNER_ID authorises nobody');
  } finally {
    if (saved !== undefined) process.env.OWNER_ID = saved;
  }

  assert.equal(await ownerGuard(fakeInteraction(guild), { ownerId: OWNER }), true);
  assert.equal(await ownerGuard(fakeInteraction(null), { ownerId: OWNER }), false);
});

test('every management command refuses non-owners and mutates nothing', async () => {
  const commands = await loadCommands();
  const subs = { channel: 'delete', category: 'create', role: 'create', purge: undefined, cleanup: 'apply', server: 'status' };
  for (const [name, sub] of Object.entries(subs)) {
    const guild = makeGuild();
    const before = guild.log.length;
    const i = fakeInteraction(guild, { userId: '111', sub, values: { name: 'x', count: 5 } });
    await commands.get(name).execute(i, { ownerId: OWNER, confirm: async () => { throw new Error('must not ask'); } });
    assert.equal(i.calls.defer.flags, MessageFlags.Ephemeral, name);
    assert.match(lastText(i), /Owner-only/, name);
    assert.equal(guild.log.length, before, `${name} changed nothing`);
  }
});

test('every management command defaults to Administrator-only and guild-only', async () => {
  const commands = await loadCommands();
  for (const name of ['channel', 'category', 'role', 'purge', 'server', 'cleanup', 'setup-server']) {
    const json = commands.get(name).data.toJSON();
    assert.equal(json.default_member_permissions, String(PermissionFlagsBits.Administrator), name);
    assert.deepEqual(json.contexts, [0], name);
  }
});

test('command list and count', async () => {
  const commands = await loadCommands();
  assert.equal(commands.size, 13);
  assert.ok(commands.size <= 100);
  assert.deepEqual([...commands.keys()].sort(), [
    'ask', 'category', 'channel', 'cleanup', 'edge', 'ev', 'link', 'proj', 'purge', 'record', 'role', 'server', 'setup-server',
  ]);
  const subNames = (n) => commands.get(n).data.toJSON().options.map((o) => o.name);
  assert.deepEqual(subNames('channel'), ['create', 'delete', 'rename', 'topic', 'move', 'lock', 'unlock', 'slowmode', 'access']);
  assert.deepEqual(subNames('category'), ['create', 'delete', 'rename']);
  assert.deepEqual(subNames('role'), ['create', 'delete', 'rename', 'color', 'give', 'take']);
  assert.deepEqual(subNames('cleanup'), ['plan', 'apply', 'roles-plan', 'roles-apply']);
  assert.deepEqual(subNames('server'), ['status']);
  assert.deepEqual(subNames('purge'), ['count']);
});

// -- protection through the commands ------------------------------------------------------------

async function run(name, guild, sub, values, deps = {}) {
  const commands = await loadCommands();
  const i = fakeInteraction(guild, { sub, values });
  const asked = [];
  await commands.get(name).execute(i, {
    ownerId: OWNER,
    confirm: async (_i, content) => { asked.push(content); return deps.verdict ?? 'confirmed'; },
    ...deps,
  });
  return { i, asked, text: lastText(i) };
}

const roleOf = (guild, name) => [...guild.roles.cache.values()].find((r) => r.name === name);
const chanOf = (guild, name) => [...guild.channels.cache.values()].find((c) => c.name === name);

test('protected roles are refused for delete, rename, color, give and take', async () => {
  const guild = makeGuild();
  const member = { id: '5', user: { tag: 'm#1' }, roles: { add: async () => { guild.log.push(['member.add']); }, remove: async () => { guild.log.push(['member.remove']); } } };
  for (const name of ['Admin', 'Moderator', 'GamblyBot', 'carl-bot', 'Beezy Bot']) {
    const role = roleOf(guild, name);
    for (const [sub, values] of [
      ['delete', { role }], ['rename', { role, name: 'x' }], ['color', { role, color: '#ffffff' }],
      ['give', { role, user: member }], ['take', { role, user: member }],
    ]) {
      const r = await run('role', guild, sub, values);
      assert.match(r.text, /Refusing/, `${name} ${sub}`);
      assert.deepEqual(r.asked, [], 'refused before any confirm prompt');
    }
  }
  assert.equal(guild.log.filter((l) => /^(role\.|member\.)/.test(l[0])).length, 0);
});

test('new-layout roles, managed roles and roles at/above the bot role are refused', async () => {
  const guild = makeGuild({ extraRoles: [{ name: 'Above Bot', position: 70 }, { name: 'Same As Bot', position: 50 }, { name: 'Integration Role', position: 3, managed: true }] });
  for (const name of [...NEW_ROLES, 'Above Bot', 'Same As Bot', 'Integration Role']) {
    const r = await run('role', guild, 'delete', { role: roleOf(guild, name) });
    assert.match(r.text, /Refusing/, name);
  }
  assert.equal(guild.log.some((l) => l[0] === 'role.delete'), false);
  // creating a role that collides with a new/protected name is refused too
  assert.match((await run('role', guild, 'create', { name: 'All-Access Pro' })).text, /Refusing/);
  assert.match((await run('role', guild, 'create', { name: 'admin' })).text, /Refusing/);
});

test('an ordinary legacy role can be managed; delete needs the confirm button', async () => {
  const guild = makeGuild();
  const role = roleOf(guild, 'DraftKings');
  const cancelled = await run('role', guild, 'delete', { role }, { verdict: 'cancelled' });
  assert.equal(cancelled.asked.length, 1);
  assert.ok(!guild.log.some((l) => l[0] === 'role.delete'));
  const expired = await run('role', guild, 'delete', { role }, { verdict: 'expired' });
  assert.ok(!guild.log.some((l) => l[0] === 'role.delete'));
  assert.equal(expired.asked.length, 1);
  const ok = await run('role', guild, 'delete', { role });
  assert.deepEqual(guild.log.find((l) => l[0] === 'role.delete'), ['role.delete', 'DraftKings']);
  assert.match(ok.text, /Deleted role DraftKings/);

  const color = await run('role', guild, 'color', { role: roleOf(guild, 'Member Pro'), color: '#5865F2' });
  assert.match(color.text, /Recolored/);
  assert.deepEqual(guild.log.find((l) => l[0] === 'role.setColor'), ['role.setColor', 0x5865f2]);
  assert.match((await run('role', guild, 'color', { role: roleOf(guild, 'Member Pro'), color: 'blue' })).text, /Invalid color/);
  const created = await run('role', guild, 'create', { name: 'Tester', color: '#00ff00' });
  assert.match(created.text, /Created role Tester/);
});

test('new-layout channels and categories are refused by every channel/category/purge operation', async () => {
  const guild = makeGuild();
  const evChannel = chanOf(guild, 'ev-sportsbooks');
  const startCat = chanOf(guild, 'START HERE');
  const legacyCat = chanOf(guild, 'PICKS');
  const role = roleOf(guild, 'Member Pro');
  for (const [sub, values] of [
    ['delete', { channel: evChannel }], ['rename', { channel: evChannel, name: 'x' }], ['topic', { channel: evChannel, topic: 'x' }],
    ['move', { channel: evChannel, category: legacyCat }], ['lock', { channel: evChannel }], ['unlock', { channel: evChannel }],
    ['slowmode', { channel: evChannel, seconds: 5 }], ['access', { channel: evChannel, role, mode: 'view' }],
    ['create', { name: 'welcome' }], ['create', { name: 'ok-name', category: startCat }],
    ['move', { channel: chanOf(guild, 'general'), category: startCat }],
  ]) {
    const r = await run('channel', guild, sub, values);
    assert.match(r.text, /Refusing/, `${sub}`);
    assert.deepEqual(r.asked, []);
  }
  for (const [sub, values] of [['delete', { category: startCat }], ['rename', { category: startCat, name: 'x' }], ['create', { name: 'ARCHIVE' }], ['create', { name: 'start here' }]]) {
    assert.match((await run('category', guild, sub, values)).text, /Refusing/, `category ${sub}`);
  }
  const commands = await loadCommands();
  const i = fakeInteraction(guild, { values: { count: 5 } });
  i.channel = evChannel;
  await commands.get('purge').execute(i, { ownerId: OWNER, confirm: async () => 'confirmed' });
  assert.match(lastText(i), /Refusing/);
  assert.equal(guild.log.some((l) => ['channel.delete', 'setName', 'setTopic', 'setParent', 'overwrites.edit', 'slowmode', 'bulkDelete'].includes(l[0])), false);
});

test('channel primitives work on non-protected channels and validate their input', async () => {
  const guild = makeGuild();
  const general = chanOf(guild, 'general');
  assert.match((await run('channel', guild, 'topic', { channel: general, topic: 'hello' })).text, /Updated the topic/);
  assert.match((await run('channel', guild, 'rename', { channel: general, name: 'Lounge Chat' })).text, /to #lounge-chat/);
  assert.match((await run('channel', guild, 'rename', { channel: general, name: 'bad name!' })).text, /Channel names must be/);
  assert.match((await run('channel', guild, 'slowmode', { channel: general, seconds: 30 })).text, /30s/);
  assert.match((await run('channel', guild, 'slowmode', { channel: general, seconds: 99999 })).text, /whole number from 0 to 21600/);
  assert.match((await run('channel', guild, 'lock', { channel: general })).text, /Locked/);
  assert.deepEqual(guild.log.find((l) => l[0] === 'overwrites.edit')[2], { SendMessages: false });
  await run('channel', guild, 'unlock', { channel: general });
  assert.deepEqual(guild.log.filter((l) => l[0] === 'overwrites.edit').at(-1)[2], { SendMessages: null });
  assert.match((await run('channel', guild, 'access', { channel: general, role: roleOf(guild, 'Member Pro'), mode: 'read-only' })).text, /read-only/);
  assert.deepEqual(guild.log.filter((l) => l[0] === 'overwrites.edit').at(-1)[2], { ViewChannel: true, SendMessages: false });
  assert.match((await run('channel', guild, 'access', { channel: general, role: roleOf(guild, 'Admin'), mode: 'hide' })).text, /Refusing/);
  const made = await run('channel', guild, 'create', { name: 'New Room', topic: 't', private: true, category: chanOf(guild, 'PICKS') });
  assert.match(made.text, /Created #new-room/);
  assert.deepEqual(guild.log.find((l) => l[0] === 'channels.create' && l[1] === 'new-room')[3][0].deny, [PermissionFlagsBits.ViewChannel]);
  const moved = await run('channel', guild, 'move', { channel: general, category: chanOf(guild, 'PICKS') });
  assert.match(moved.text, /Moved #lounge-chat into PICKS/);
});

test('channel delete and category delete need the confirm button; categories must be empty', async () => {
  const guild = makeGuild();
  const general = chanOf(guild, 'general');
  const cancelled = await run('channel', guild, 'delete', { channel: general }, { verdict: 'cancelled' });
  assert.equal(cancelled.asked.length, 1);
  assert.ok(chanOf(guild, 'general'));
  await run('channel', guild, 'delete', { channel: general });
  assert.equal(chanOf(guild, 'general'), undefined);

  const community = chanOf(guild, 'COMMUNITY');
  const nonEmpty = await run('category', guild, 'delete', { category: chanOf(guild, 'PICKS') });
  assert.match(nonEmpty.text, /still holds 2 channel/);
  assert.deepEqual(nonEmpty.asked, [], 'refused before prompting');
  assert.match((await run('category', guild, 'delete', { category: community })).text, /still holds 1 channel/);
  const opsCat = chanOf(guild, 'OPS');
  const cancelledCat = await run('category', guild, 'delete', { category: opsCat }, { verdict: 'cancelled' });
  assert.equal(cancelledCat.asked.length, 1);
  assert.ok(chanOf(guild, 'OPS'));
  assert.match((await run('category', guild, 'delete', { category: opsCat })).text, /Deleted category OPS/);
  assert.equal(chanOf(guild, 'OPS'), undefined);
});

test('purge validates the count, needs confirmation, and refuses protected channels', async () => {
  const guild = makeGuild();
  const commands = await loadCommands();
  const mk = (count, ch) => {
    const i = fakeInteraction(guild, { values: { count } });
    i.channel = ch;
    return i;
  };
  const general = chanOf(guild, 'general');
  for (const bad of [0, 101, 2.5]) {
    const i = mk(bad, general);
    await commands.get('purge').execute(i, { ownerId: OWNER, confirm: async () => 'confirmed' });
    assert.match(lastText(i), /whole number from 1 to 100/, String(bad));
  }
  const cancelled = mk(10, general);
  await commands.get('purge').execute(cancelled, { ownerId: OWNER, confirm: async () => 'cancelled' });
  assert.ok(!guild.log.some((l) => l[0] === 'bulkDelete'));
  const ok = mk(10, general);
  await commands.get('purge').execute(ok, { ownerId: OWNER, confirm: async () => 'confirmed' });
  assert.deepEqual(guild.log.find((l) => l[0] === 'bulkDelete'), ['bulkDelete', 'general', 10]);
  assert.match(lastText(ok), /Deleted 10 message/);
});

test('/cleanup plan is a dry run; apply asks first and applies a fresh plan; roles-apply honours force', async () => {
  const guild = makeGuild();
  const plan = await run('cleanup', guild, 'plan', {});
  assert.match(plan.text, /ARCHIVE/);
  assert.match(plan.text, /#verify/);
  assert.deepEqual(plan.asked, []);
  assert.equal(guild.log.filter((l) => l[0] !== 'noop').length, 0, 'plan changed nothing');

  const declined = await run('cleanup', guild, 'apply', {}, { verdict: 'cancelled' });
  assert.equal(declined.asked.length, 1);
  assert.equal(guild.log.length, 0);

  const applied = await run('cleanup', guild, 'apply', {});
  assert.match(applied.text, /13\/13 steps succeeded/);
  const second = await run('cleanup', guild, 'apply', {});
  assert.match(second.text, /Nothing to do/);
  assert.deepEqual(second.asked, [], 'nothing to do means no prompt');

  const rolesPlan = await run('cleanup', guild, 'roles-plan', {});
  assert.match(rolesPlan.text, /Member: 2 members - FLAGGED/);
  const noForce = await run('cleanup', guild, 'roles-apply', {});
  assert.deepEqual(guild.log.filter((l) => l[0] === 'role.delete').map((l) => l[1]).sort(), ['DraftKings', 'Member Pro']);
  assert.doesNotMatch(noForce.asked[0], /WARNING/);
  const withForce = await run('cleanup', guild, 'roles-apply', { force: true });
  assert.match(withForce.asked[0], /WARNING: force is on; 1 role/);
  assert.ok(guild.log.some((l) => l[0] === 'role.delete' && l[1] === 'Member'));
  for (const keep of ['Admin', 'Moderator', 'GamblyBot', 'carl-bot', 'Beezy Bot', ...NEW_ROLES]) assert.ok(roleOf(guild, keep), keep);
});

// -- confirm buttons --------------------------------------------------------------------------------

function confirmHarness(script) {
  const calls = { edits: [], clickReplies: [], clickUpdates: [] };
  let payload;
  const message = {
    awaitMessageComponent: async ({ filter, time }) => {
      const step = script.shift();
      if (!step || step === 'timeout') throw new Error(`timed out after ${time}`);
      const buttons = payload.components[0].toJSON().components;
      const customId = buttons.find((b) => b.custom_id.endsWith(`:${step.press}`)).custom_id;
      assert.ok(filter({ customId }), 'filter accepts our buttons');
      assert.ok(!filter({ customId: 'something-else' }), 'filter rejects foreign buttons');
      return {
        customId,
        user: { id: step.user },
        reply: async (r) => { calls.clickReplies.push(r); },
        update: async (u) => { calls.clickUpdates.push(u); },
      };
    },
  };
  const interaction = {
    user: { id: OWNER },
    editReply: async (p) => { payload = p; calls.edits.push(p); return message; },
  };
  return { interaction, calls };
}

test('confirm: the owner pressing Confirm resolves confirmed; buttons are Confirm/Cancel', async () => {
  const { interaction, calls } = confirmHarness([{ user: OWNER, press: 'yes' }]);
  const verdict = await askConfirm(interaction, { content: 'Delete it?' });
  assert.equal(verdict, 'confirmed');
  const buttons = calls.edits[0].components[0].toJSON().components;
  assert.deepEqual(buttons.map((b) => b.label), ['Confirm', 'Cancel']);
  assert.match(calls.edits[0].content, /Confirm within 60s/);
  assert.equal(calls.clickUpdates[0].components.length, 0, 'buttons are removed after the press');
});

test('confirm: a press by another user is rejected and does not consume the prompt', async () => {
  const { interaction, calls } = confirmHarness([
    { user: '222', press: 'yes' }, // stranger tries to confirm
    { user: OWNER, press: 'no' }, // owner cancels
  ]);
  const verdict = await askConfirm(interaction, { content: 'Delete it?' });
  assert.equal(verdict, 'cancelled');
  assert.equal(calls.clickReplies.length, 1);
  assert.equal(calls.clickReplies[0].flags, MessageFlags.Ephemeral);
  assert.match(calls.clickReplies[0].content, /Only the owner/);
  assert.equal(calls.clickUpdates.length, 1, 'only the owner press updated the prompt');
  assert.match(calls.clickUpdates[0].content, /Cancelled/);
});

test('confirm: expires after the timeout and clears the buttons', async () => {
  const { interaction, calls } = confirmHarness(['timeout']);
  let t = 0;
  const verdict = await askConfirm(interaction, { content: 'x', timeoutMs: 60_000, now: () => t });
  assert.equal(verdict, 'expired');
  assert.deepEqual(calls.edits.at(-1).components, []);
  assert.match(calls.edits.at(-1).content, /Expired/);

  // a stranger press right at the deadline does not extend it
  const h = confirmHarness([{ user: '222', press: 'yes' }]);
  let clock = 0;
  const stepper = () => { const v = clock; clock += 40_000; return v; };
  const verdict2 = await askConfirm(h.interaction, { content: 'x', timeoutMs: 60_000, now: stepper });
  assert.equal(verdict2, 'expired');
});

// -- /server status -------------------------------------------------------------------------------------

test('server status diffs against the exported layout and checks the role hierarchy', async () => {
  const full = buildStatus(liveInventorySnapshot());
  assert.deepEqual(full.missingRoles, []);
  assert.deepEqual(full.missingCategories, []);
  assert.deepEqual(full.missingChannels, []);
  assert.deepEqual(full.legacyChannels.sort(), [...LEGACY.channels].sort());
  assert.deepEqual(full.legacyCategories.sort(), [...LEGACY.categories].sort());
  assert.equal(full.legacyRoles.find((r) => r.name === 'Member').memberCount, 40);
  assert.equal(full.botAbovePaidRoles, true);
  assert.equal(full.counts.categories, 11);

  const snap = liveInventorySnapshot();
  snap.roles = snap.roles.filter((r) => r.name !== 'NHL Pass');
  snap.categories = snap.categories.filter((c) => c.name !== 'PUBLIC LEDGER & RECAPS');
  snap.channels = snap.channels.filter((c) => c.name !== 'daily-recap' && c.name !== 'mlb-alerts');
  snap.channels.find((c) => c.name === 'nfl-alerts').parentId = snap.cats['START HERE'].id;
  snap.roles.find((r) => r.name === 'NFL Pass').position = 55; // above the bot
  const status = buildStatus(snap);
  assert.deepEqual(status.missingRoles, ['NHL Pass']);
  assert.deepEqual(status.missingCategories, ['PUBLIC LEDGER & RECAPS']);
  assert.deepEqual(status.missingChannels.sort(), ['daily-recap', 'mlb-alerts']);
  assert.deepEqual(status.misplacedChannels, ['#nfl-alerts (expected in DAILY MODEL CARDS)']);
  assert.equal(status.botAbovePaidRoles, false);
  assert.deepEqual(status.rolesAtOrAboveBot, ['NFL Pass']);
  const text = formatStatus(status);
  assert.match(text, /NO, move the bot role above: NFL Pass/);
  assert.match(text, /Missing roles: NHL Pass/);

  assert.equal(buildStatus({ ...liveInventorySnapshot(), botHighest: undefined }).botAbovePaidRoles, null);
});

test('/server status is read-only and reports through the command', async () => {
  const guild = makeGuild();
  const before = guild.log.length;
  const r = await run('server', guild, 'status', {});
  assert.match(r.text, /Server status/);
  assert.match(r.text, /Legacy remaining/);
  assert.match(r.text, /yes, tier sync can assign every paid role/);
  assert.equal(guild.log.length, before);
});

test('validators', () => {
  assert.equal(ops.validateChannelName('  Hello World '), 'hello-world');
  assert.throws(() => ops.validateChannelName(''), /Channel names/);
  assert.throws(() => ops.validateChannelName('a'.repeat(101)), /Channel names/);
  assert.throws(() => ops.validateChannelName('no!'), /Channel names/);
  assert.equal(ops.validateLabel(' Pro Role '), 'Pro Role');
  assert.throws(() => ops.validateLabel(''), /1-100/);
  assert.throws(() => ops.validateLabel('a\nb'), /printable/);
  assert.equal(ops.parseColor('#5865F2'), 0x5865f2);
  assert.equal(ops.parseColor('ff0000'), 0xff0000);
  assert.throws(() => ops.parseColor('#fff'), /Invalid color/);
  assert.throws(() => ops.parseColor('#gggggg'), /Invalid color/);
  assert.equal(ops.validateIntRange(5, 1, 10, 'x'), 5);
  assert.throws(() => ops.validateIntRange(11, 1, 10, 'x'), /1 to 10/);
  assert.throws(() => ops.validateIntRange('abc', 1, 10, 'x'), /1 to 10/);
  assert.throws(() => ops.validateTopic('x'.repeat(1025)), /1024/);
});
