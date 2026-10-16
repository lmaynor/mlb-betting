import { test, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { Collection } from 'discord.js';
import * as db from '../src/db.js';
import * as http from '../src/httpServer.js';

const SECRET = 'test-shared-secret';
const GUILD = '800000000000000001';
const USER = '800000000000000042';
const ROLE_NAMES = ['All-Access Pro', 'NFL Pass', 'NBA Pass', 'NHL Pass', 'MLB Pass', 'Explorer'];

function makeClient({ inGuild = true, held = [] } = {}) {
  const roles = new Collection(ROLE_NAMES.map((name, i) => [`r${i}`, { id: `r${i}`, name }]));
  const mutations = [];
  const memberRoles = new Collection();
  for (const name of held) {
    const role = roles.find((r) => r.name === name);
    memberRoles.set(role.id, role);
  }
  const member = {
    id: USER,
    roles: {
      cache: memberRoles,
      add: async (role) => { memberRoles.set(role.id, role); mutations.push(['add', role.name]); },
      remove: async (role) => { memberRoles.delete(role.id); mutations.push(['remove', role.name]); },
    },
  };
  const guild = {
    id: GUILD,
    roles: { cache: roles, fetch: async () => roles },
    members: {
      fetch: async (id) => {
        if (!inGuild || id !== USER) throw Object.assign(new Error('Unknown Member'), { code: 10007 });
        return member;
      },
    },
  };
  return {
    user: { tag: 'Beezy#0001' },
    guilds: { cache: new Collection([[GUILD, guild]]), fetch: async () => guild },
    mutations,
    memberRoleNames: () => memberRoles.map((r) => r.name).sort(),
  };
}

let server;
let base;
let client;

async function start(opts = {}) {
  client = opts.client ?? makeClient();
  server = http.createHttpServer({ client, secret: SECRET, guildId: GUILD, ...opts.server });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
}

async function stop() {
  if (server) await new Promise((resolve) => { server.close(resolve); server.closeAllConnections?.(); });
  server = null;
}

beforeEach(() => { db.initDb(':memory:'); });
after(async () => { await stop(); db.closeDb(); });

async function post(body, headers = { 'X-Beezy-Secret': SECRET }, raw = false) {
  const res = await fetch(`${base}/internal/sync-role`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: raw ? body : JSON.stringify(body),
  });
  return { status: res.status, json: await res.json() };
}

test('GET /health returns ok and the bot tag', async () => {
  await start();
  const res = await fetch(`${base}/health`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, bot: 'Beezy#0001' });
  await stop();
});

test('health before the client is ready reports a null bot', async () => {
  await start({ client: { user: null, guilds: { cache: new Collection() } } });
  const res = await fetch(`${base}/health`);
  assert.deepEqual(await res.json(), { ok: true, bot: null });
  await stop();
});

test('unknown paths 404 and wrong methods 405', async () => {
  await start();
  assert.equal((await fetch(`${base}/nope`)).status, 404);
  assert.equal((await fetch(`${base}/internal/sync-role`)).status, 405);
  assert.equal((await fetch(`${base}/health`, { method: 'POST' })).status, 405);
  await stop();
});

test('sync-role: 401 without the secret header and with a wrong secret', async () => {
  await start();
  const body = { discord_user_id: USER, tier: 'pro_monthly' };
  assert.equal((await post(body, {})).status, 401);
  assert.equal((await post(body, { 'X-Beezy-Secret': 'wrong' })).status, 401);
  assert.equal((await post(body, { 'X-Beezy-Secret': '' })).status, 401);
  assert.equal((await post(body, { 'X-Beezy-Secret': `${SECRET}x` })).status, 401);
  assert.equal(db.getMemberLink(USER), null, 'nothing is persisted for unauthenticated calls');
  assert.deepEqual(client.mutations, []);
  await stop();
});

test('sync-role: 503 when BEEZY_BOT_SECRET is not configured (an empty secret is never accepted)', async () => {
  const saved = process.env.BEEZY_BOT_SECRET;
  delete process.env.BEEZY_BOT_SECRET;
  try {
    await start({ server: { secret: undefined } });
    const body = { discord_user_id: USER, tier: 'pro_monthly' };
    assert.equal((await post(body, {})).status, 503);
    assert.equal((await post(body, { 'X-Beezy-Secret': '' })).status, 503);
    await stop();
    await start({ server: { secret: '' } });
    assert.equal((await post(body, { 'X-Beezy-Secret': '' })).status, 503);
    await stop();
  } finally {
    if (saved !== undefined) process.env.BEEZY_BOT_SECRET = saved;
  }
});

test('sync-role: validation errors return 400', async () => {
  await start();
  const cases = [
    ['not json', /invalid JSON/, true],
    ['[]', /JSON object/, true],
    [{ tier: 'pro' }, /discord_user_id/],
    [{ discord_user_id: 'abc', tier: 'pro' }, /snowflake/],
    [{ discord_user_id: 12345678901234567, tier: 'pro' }, /snowflake/],
    [{ discord_user_id: USER, tier: 'platinum' }, /tier must be one of/],
    [{ discord_user_id: USER }, /tier/],
    [{ discord_user_id: USER, tier: 'single_sport' }, /sport_choice is required/],
    [{ discord_user_id: USER, tier: 'single_sport', sport_choice: 'cricket' }, /sport_choice must be one of/],
    [{ discord_user_id: USER, tier: 'pro', email: 'not-an-email' }, /email/],
  ];
  for (const [body, re, raw] of cases) {
    const r = await post(body, undefined, raw);
    assert.equal(r.status, 400, JSON.stringify(body));
    assert.match(r.json.error, re);
  }
  assert.equal(db.getMemberLink(USER), null);
  await stop();
});

test('sync-role: oversized bodies are rejected with 413', async () => {
  await start();
  const big = JSON.stringify({ discord_user_id: USER, tier: 'pro', pad: 'x'.repeat(http.MAX_BODY_BYTES + 100) });
  const r = await post(big, undefined, true);
  assert.equal(r.status, 413);
  await stop();
});

test('resolveRoleDiff: tier -> role diff table', () => {
  const ALL = ['All-Access Pro', 'NFL Pass', 'NBA Pass', 'NHL Pass', 'MLB Pass'];
  const expect = (add) => ({ add, remove: ALL.filter((r) => !add.includes(r)) });
  const table = [
    [['public', null], expect([])],
    [['pro_monthly', null], expect(['All-Access Pro'])],
    [['pro_annual', null], expect(['All-Access Pro'])],
    [['pro', null], expect(['All-Access Pro'])],
    [['season', 'mlb'], expect(['All-Access Pro'])],
    [['single_sport', 'mlb'], expect(['MLB Pass'])],
    [['single_sport', 'nfl'], expect(['NFL Pass'])],
    [['single_sport', 'nba'], expect(['NBA Pass'])],
    [['single_sport', 'nhl'], expect(['NHL Pass'])],
    [['starter', 'nba'], expect(['NBA Pass'])],
    [['starter', null], expect([])],
    [['starter', undefined], expect([])],
    [['single_sport', null], expect([])],
  ];
  for (const [[tier, sport], want] of table) {
    assert.deepEqual(http.resolveRoleDiff(tier, sport), want, `${tier}/${sport}`);
  }
  // Explorer is never in either list
  for (const [, want] of table) {
    assert.ok(!want.add.includes('Explorer') && !want.remove.includes('Explorer'));
  }
});

test('secretsMatch is exact and rejects empties', () => {
  assert.equal(http.secretsMatch('abc', 'abc'), true);
  assert.equal(http.secretsMatch('abd', 'abc'), false);
  assert.equal(http.secretsMatch('abcd', 'abc'), false);
  assert.equal(http.secretsMatch('', ''), false);
  assert.equal(http.secretsMatch('abc', ''), false);
  assert.equal(http.secretsMatch(undefined, 'abc'), false);
});

test('sync-role: pro upgrade grants All-Access Pro, strips the stale sport pass, persists the link', async () => {
  await start({ client: makeClient({ held: ['NFL Pass', 'Explorer'] }) });
  const r = await post({ discord_user_id: USER, email: 'Fan@Example.com', tier: 'pro_annual', clerk_user_id: 'user_1' });
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.deepEqual(r.json.applied, { add: ['All-Access Pro'], remove: ['NFL Pass'] });
  assert.deepEqual(client.memberRoleNames(), ['All-Access Pro', 'Explorer']);
  const link = db.getMemberLink(USER);
  assert.equal(link.tier, 'pro_annual');
  assert.equal(link.email, 'fan@example.com');
  assert.equal(link.clerk_user_id, 'user_1');
  assert.equal(db.getMemberByEmail('fan@example.com').discord_user_id, USER);
  await stop();
});

test('sync-role: single_sport grants only that sport pass; idempotent on repeat', async () => {
  await start({ client: makeClient({ held: ['Explorer', 'All-Access Pro'] }) });
  const body = { discord_user_id: USER, tier: 'single_sport', sport_choice: 'nhl' };
  const first = await post(body);
  assert.equal(first.status, 200);
  assert.deepEqual(first.json.applied, { add: ['NHL Pass'], remove: ['All-Access Pro'] });
  assert.deepEqual(client.memberRoleNames(), ['Explorer', 'NHL Pass']);
  const second = await post(body);
  assert.deepEqual(second.json.applied, { add: [], remove: [] }, 'second call changes nothing');
  assert.equal(db.getMemberLink(USER).sport_choice, 'nhl');
  await stop();
});

test('sync-role: public strips every paid role and keeps Explorer', async () => {
  await start({ client: makeClient({ held: ['MLB Pass', 'All-Access Pro', 'Explorer'] }) });
  const r = await post({ discord_user_id: USER, tier: 'public' });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.applied.add, []);
  assert.deepEqual(r.json.applied.remove.sort(), ['All-Access Pro', 'MLB Pass']);
  assert.deepEqual(client.memberRoleNames(), ['Explorer']);
  assert.equal(db.getMemberLink(USER).tier, 'public');
  assert.equal(db.getMemberLink(USER).sport_choice, null);
  await stop();
});

test('sync-role: member not in the guild gets 404 but the link is still persisted', async () => {
  await start({ client: makeClient({ inGuild: false }) });
  const r = await post({ discord_user_id: USER, email: 'late@example.com', tier: 'pro_monthly' });
  assert.equal(r.status, 404);
  assert.equal(r.json.persisted, true);
  const link = db.getMemberLink(USER);
  assert.equal(link.tier, 'pro_monthly');
  assert.equal(link.email, 'late@example.com');
  await stop();
});

test('sync-role: roles not yet provisioned are reported, not fatal', async () => {
  const c = makeClient();
  const guild = c.guilds.cache.get(GUILD);
  guild.roles.cache.delete('r0'); // no All-Access Pro role in the guild
  await start({ client: c });
  const r = await post({ discord_user_id: USER, tier: 'pro' });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.missing_roles, ['All-Access Pro']);
  await stop();
});

test('sync-role: no guild configured returns 503 (link persisted)', async () => {
  const client2 = { user: { tag: 't' }, guilds: { cache: new Collection(), fetch: async () => { throw new Error('x'); } } };
  const savedGuild = process.env.GUILD_ID;
  delete process.env.GUILD_ID;
  try {
    server = http.createHttpServer({ client: client2, secret: SECRET });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${server.address().port}`;
    const r = await post({ discord_user_id: USER, tier: 'pro' });
    assert.equal(r.status, 503);
    assert.equal(r.json.persisted, true);
  } finally {
    if (savedGuild !== undefined) process.env.GUILD_ID = savedGuild;
    await stop();
  }
});
