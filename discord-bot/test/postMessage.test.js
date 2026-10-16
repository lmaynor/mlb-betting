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
