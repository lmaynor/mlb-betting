import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { Collection } from 'discord.js';
import * as http from '../src/httpServer.js';

const SECRET = 'post-test-secret';
const GUILD = '800000000000000001';

function makeClient({ channels = ['ev-sportsbooks', 'ev-exchanges'], failSend = false } = {}) {
  const sent = [];
  const cache = new Collection(channels.map((name, i) => [`c${i}`, {
    id: `c${i}`, name,
    send: async (msg) => { if (failSend) throw new Error('Missing Access'); sent.push({ name, msg }); return { id: `m${sent.length}` }; },
  }]));
  const guild = { id: GUILD, channels: { fetch: async () => cache } };
  return { sent, client: { user: { tag: 'beezy#1' }, guilds: { cache: new Collection([[GUILD, guild]]), fetch: async () => guild } } };
}

let server; let base; let ctx;
async function start(opts = {}, serverOpts = {}) {
  ctx = makeClient(opts);
  server = http.createHttpServer({ client: ctx.client, secret: SECRET, guildId: GUILD, ...serverOpts });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
}
before(() => start());
after(() => new Promise((r) => server.close(r)));

const post = (body, headers = { 'X-Beezy-Secret': SECRET }) => fetch(`${base}/internal/post-message`, {
  method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body),
});

test('rejects a missing or wrong secret and a wrong method', async () => {
  assert.equal((await post({ channel: 'ev_sportsbooks', content: 'x' }, {})).status, 401);
  assert.equal((await post({ channel: 'ev_sportsbooks', content: 'x' }, { 'X-Beezy-Secret': 'nope' })).status, 401);
  assert.equal((await fetch(`${base}/internal/post-message`)).status, 405);
  assert.equal(ctx.sent.length, 0);
});

test('503 when no secret is configured (never an open endpoint)', async () => {
  const s = http.createHttpServer({ client: ctx.client, secret: '', guildId: GUILD });
  await new Promise((r) => s.listen(0, '127.0.0.1', r));
  try {
    const res = await fetch(`http://127.0.0.1:${s.address().port}/internal/post-message`, { method: 'POST', headers: { 'X-Beezy-Secret': '' }, body: '{}' });
    assert.equal(res.status, 503);
  } finally { await new Promise((r) => s.close(r)); }
});

test('only allowlisted channel keys are accepted', async () => {
  assert.equal((await post({ channel: 'general', content: 'x' })).status, 400);
  assert.equal((await post({ channel: '123456789', content: 'x' })).status, 400);
  assert.equal((await post({ channel: 'constructor', content: 'x' })).status, 400);
  assert.equal(ctx.sent.length, 0);
});

test('validates content and embeds', async () => {
  assert.equal((await post({ channel: 'ev_sportsbooks' })).status, 400);
  assert.equal((await post({ channel: 'ev_sportsbooks', content: 'a'.repeat(2001) })).status, 400);
  assert.equal((await post({ channel: 'ev_sportsbooks', embeds: 'x' })).status, 400);
  assert.equal((await post({ channel: 'ev_sportsbooks', embeds: Array(11).fill({}) })).status, 400);
  assert.equal((await post('not json')).status, 400);
});

test('posts as the bot with content and embeds, never pinging anyone', async () => {
  const res = await post({ channel: 'ev_sportsbooks', content: '@everyone Jo Doe Over 24.5 Points @ FanDuel +120', embeds: [{ title: 'x' }] });
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.equal(j.ok, true);
  const m = ctx.sent.at(-1);
  assert.equal(m.name, 'ev-sportsbooks');
  assert.match(m.msg.content, /Jo Doe Over 24.5/);
  assert.deepEqual(m.msg.allowedMentions, { parse: [] });
  assert.equal(m.msg.embeds.length, 1);
});

test('404 when the channel is missing, 502 when Discord rejects the send', async () => {
  assert.equal((await post({ channel: 'nfl_card', content: 'x' })).status, 404);
  await new Promise((r) => server.close(r));
  await start({ failSend: true });
  assert.equal((await post({ channel: 'ev_exchanges', content: 'x' })).status, 502);
});

test('every allowlisted key maps to a plain channel name', () => {
  assert.equal(Object.keys(http.POST_CHANNELS).length, 9);
  for (const name of Object.values(http.POST_CHANNELS)) assert.match(name, /^[a-z0-9-]+$/);
});

// ---- threads with @slip-bot mentions --------------------------------------------------------------------------
function makeThreadClient({ botNames = ['GamblyBot', 'Playbook'], failThread = false } = {}) {
  const threads = [];
  const members = new Collection([
    ['111111111111111111', { id: '111111111111111111', displayName: 'GamblyBot', nickname: null, user: { bot: true, username: 'GamblyBot' } }],
    ['222222222222222222', { id: '222222222222222222', displayName: 'Playbook', nickname: null, user: { bot: true, username: 'Playbook' } }],
    ['333333333333333333', { id: '333333333333333333', displayName: 'Playbook', nickname: null, user: { bot: false, username: 'Playbook' } }],
  ]);
  const channel = {
    id: 'c1', name: 'ev-sportsbooks',
    send: async () => ({
      id: 'm1',
      startThread: async (opts) => {
        if (failThread) throw new Error('Missing Permissions');
        const t = { opts, sent: [], send: async (m) => { t.sent.push(m); } };
        threads.push(t);
        return t;
      },
    }),
  };
  const guild = {
    id: GUILD,
    channels: { fetch: async () => new Collection([['c1', channel]]) },
    members: { fetch: async ({ query }) => new Collection([...members].filter(([, m]) => m.displayName.toLowerCase().includes(query.toLowerCase()) && botNames.includes(m.displayName))) },
  };
  return { threads, client: { user: { tag: 'b#1' }, guilds: { cache: new Collection([[GUILD, guild]]), fetch: async () => guild } } };
}

test('thread payload validation', () => {
  const ok = { channel: 'ev_sportsbooks', content: 'x', thread: { name: '  Jo   Doe Over 24.5  ', content: 'slip' } };
  assert.deepEqual(http.validatePostPayload(ok).value.thread, { name: 'Jo Doe Over 24.5', content: 'slip' });
  assert.equal(http.validatePostPayload({ ...ok, thread: 'x' }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, thread: { name: '', content: 'slip' } }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, thread: { name: 'n'.repeat(101), content: 'slip' } }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, thread: { name: 'n', content: '' } }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, thread: { name: 'n', content: 'c'.repeat(1801) } }).ok, false);
});

test('opens a thread and @mentions only the real slip bots (never a human with the same name)', async () => {
  http.resetSlipBotCache();
  const { client, threads } = makeThreadClient();
  const out = await http.postMessage(client, GUILD, { channel: 'ev_sportsbooks', content: 'Jo Doe Over 24.5 Points @ FanDuel +120',
    thread: { name: 'Jo Doe Over 24.5 Points', content: 'Jo Doe Over 24.5 Points @ FanDuel +120' } });
  assert.deepEqual([out.status, out.thread, out.mentioned], ['ok', true, 2]);
  const t = threads[0];
  assert.equal(t.opts.name, 'Jo Doe Over 24.5 Points');
  assert.equal(t.sent[0].content, '<@111111111111111111> <@222222222222222222> Jo Doe Over 24.5 Points @ FanDuel +120');
  assert.deepEqual(t.sent[0].allowedMentions, { users: ['111111111111111111', '222222222222222222'] });
});

test('a thread failure does not fail the alert', async () => {
  http.resetSlipBotCache();
  const { client } = makeThreadClient({ failThread: true });
  const out = await http.postMessage(client, GUILD, { channel: 'ev_sportsbooks', content: 'x', thread: { name: 'n', content: 'c' } });
  assert.deepEqual([out.status, out.thread], ['ok', false]);
});

test('no slip bots in the guild: thread still posts, nobody mentioned', async () => {
  http.resetSlipBotCache();
  const { client, threads } = makeThreadClient({ botNames: [] });
  const out = await http.postMessage(client, GUILD, { channel: 'ev_sportsbooks', content: 'x', thread: { name: 'n', content: 'slip' } });
  assert.equal(out.mentioned, 0);
  assert.equal(threads[0].sent[0].content, 'slip');
});

// ---- daily thread: find-or-create one thread per day under a channel ----------------------------------------------
function makeDailyClient({ existing = [], noThreads = false, failCreate = false } = {}) {
  const posts = [];
  const created = [];
  const mkThread = (name, archived = false) => ({ name, archived, send: async (m) => { posts.push({ thread: name, m }); return { id: `t${posts.length}` }; } });
  const active = new Collection(existing.map((e, i) => [`th${i}`, mkThread(e.name, e.archived)]));
  const channel = {
    id: 'c1', name: 'nfl-card',
    send: async () => ({ id: 'm1' }),
    threads: noThreads ? undefined : {
      fetchActive: async () => ({ threads: active }),
      create: async (opts) => { if (failCreate) throw new Error('Missing Permissions'); created.push(opts); const t = mkThread(opts.name); active.set(`n${created.length}`, t); return t; },
    },
  };
  const guild = { id: GUILD, channels: { fetch: async () => new Collection([['c1', channel]]) } };
  return { posts, created, client: { user: { tag: 'b#1' }, guilds: { cache: new Collection([[GUILD, guild]]), fetch: async () => guild } } };
}

test('daily_thread payload validation', () => {
  const ok = { channel: 'nfl_card', embeds: [{ title: 'x' }], daily_thread: { name: '  NFL props   signals - Sat Oct 18 ' } };
  assert.deepEqual(http.validatePostPayload(ok).value.dailyThread, { name: 'NFL props signals - Sat Oct 18' });
  assert.equal(http.validatePostPayload({ ...ok, daily_thread: 'x' }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, daily_thread: { name: '' } }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, daily_thread: { name: 'n'.repeat(101) } }).ok, false);
  assert.equal(http.validatePostPayload({ ...ok, thread: { name: 'n', content: 'c' } }).ok, false, 'cannot combine with thread');
});

test('creates the day thread once, then keeps posting into it', async () => {
  const { client, posts, created } = makeDailyClient();
  const value = { channel: 'nfl_card', embeds: [{ title: 'a' }], dailyThread: { name: 'NFL props signals - Sat Oct 18' } };
  const first = await http.postMessage(client, GUILD, value);
  assert.deepEqual([first.status, first.thread, first.created], ['ok', true, true]);
  const second = await http.postMessage(client, GUILD, { ...value, embeds: [{ title: 'b' }] });
  assert.deepEqual([second.status, second.created], ['ok', false]);
  assert.equal(created.length, 1, 'one thread for the day');
  assert.equal(created[0].autoArchiveDuration, 1440);
  assert.deepEqual(posts.map((p) => p.m.embeds[0].title), ['a', 'b']);
  assert.deepEqual(posts[0].m.allowedMentions, { parse: [] });
});

test('reuses an existing active thread of that name and makes a new one for a new day', async () => {
  const { client, posts, created } = makeDailyClient({ existing: [{ name: 'NFL props signals - Sat Oct 18' }, { name: 'NFL props signals - Fri Oct 17', archived: true }] });
  await http.postMessage(client, GUILD, { channel: 'nfl_card', content: 'x', dailyThread: { name: 'NFL props signals - Sat Oct 18' } });
  assert.equal(created.length, 0);
  await http.postMessage(client, GUILD, { channel: 'nfl_card', content: 'y', dailyThread: { name: 'NFL props signals - Sun Oct 19' } });
  assert.equal(created.length, 1);
  assert.deepEqual(posts.map((p) => p.thread), ['NFL props signals - Sat Oct 18', 'NFL props signals - Sun Oct 19']);
});

test('a channel without threads is a 502 and a failed create propagates, so the caller can fall back (nothing silently dropped)', async () => {
  assert.equal((await http.postMessage(makeDailyClient({ noThreads: true }).client, GUILD, { channel: 'nfl_card', content: 'x', dailyThread: { name: 'n' } })).status, 'no_threads');
  await assert.rejects(http.postMessage(makeDailyClient({ failCreate: true }).client, GUILD, { channel: 'nfl_card', content: 'x', dailyThread: { name: 'n' } }), /Missing Permissions/);
});
