import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Collection, MessageFlags } from 'discord.js';
import * as db from '../src/db.js';
import { loadCommands } from '../src/load-commands.js';
import { COLORS } from '../src/embed.js';
import { handleMemberAdd } from '../src/welcome.js';
import { extractQuestion, handleMention } from '../src/mention.js';

const BEEZY = { baseUrl: 'https://api.example.test', apiKey: 'beezy-secret-key' };

beforeEach(() => db.initDb(':memory:'));
afterEach(() => db.closeDb());

const res = (body, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

function fakeInteraction({ options = {}, userId = '1', isPublic = false } = {}) {
  const calls = { defer: null, edits: [], followUps: [] };
  return {
    calls,
    user: { id: userId, tag: 'user#1' },
    guild: { id: 'g1', roles: { everyone: { id: 'g1' } } },
    channel: { permissionsFor: () => ({ has: () => isPublic }) },
    inGuild: () => true,
    options: {
      getString: (name, required) => {
        const v = options[name] ?? null;
        if (required && v === null) throw new Error(`missing ${name}`);
        return v;
      },
    },
    deferReply: async (arg) => { calls.defer = arg ?? {}; },
    editReply: async (arg) => { calls.edits.push(arg); },
    followUp: async (arg) => { calls.followUps.push(arg); },
  };
}

test('all slash commands load with valid builders', async () => {
  const commands = await loadCommands();
  assert.deepEqual([...commands.keys()].sort(), ['ask', 'category', 'channel', 'cleanup', 'edge', 'ev', 'link', 'proj', 'purge', 'record', 'role', 'server', 'setup-server']);
  for (const mod of commands.values()) {
    const json = mod.data.toJSON();
    assert.ok(json.description.length > 0 && json.description.length <= 100);
    assert.equal(typeof mod.execute, 'function');
  }
  const ev = commands.get('ev').data.toJSON();
  assert.deepEqual(ev.options.map((o) => o.name), ['sport', 'book']);
  assert.deepEqual(commands.get('proj').data.toJSON().options.map((o) => [o.name, Boolean(o.required)]), [['sport', true], ['player', false], ['market', false]]);
  assert.equal(commands.get('setup-server').data.toJSON().default_member_permissions, '8');
});

test('/ev defers, shows the top 6 plays with both link buttons', async () => {
  const { execute } = (await loadCommands()).get('ev');
  const rows = Array.from({ length: 9 }, (_, i) => ({
    player: `Player ${i}`, bet_type: 'hits', edge: 0.1 - i * 0.005, odds: 110, book: 'DraftKings', model_prob: 0.5, n_books: 6,
  }));
  const fetchImpl = async () => res({ as_of: 't', mlb: { rows } });
  const i = fakeInteraction();
  await execute(i, { beezy: { ...BEEZY, fetchImpl } });
  assert.deepEqual(i.calls.defer, {});
  const reply = i.calls.edits[0];
  const embed = reply.embeds[0].toJSON();
  assert.equal(embed.color, COLORS.emerald);
  assert.equal((embed.description.match(/\*\*\d+\. /g) || []).length, 6);
  assert.match(embed.description, /DraftKings \+110 \| EV \+10\.0%/);
  const buttons = reply.components[0].toJSON().components;
  assert.deepEqual(buttons.map((b) => [b.label, b.url]), [
    ['Open Terminal', 'https://beezy.fyi/ev'],
    ['Inspect Cockpit', 'https://beezy.fyi/edge'],
  ]);
});

test('/ev on API failure shows a plain error embed with no stack or secrets', async () => {
  const { execute } = (await loadCommands()).get('ev');
  const fetchImpl = async () => res({ error: `internal ${BEEZY.apiKey} Traceback at file.py` }, 500);
  const i = fakeInteraction();
  const origError = console.error;
  console.error = () => {};
  try {
    await execute(i, { beezy: { ...BEEZY, fetchImpl } });
  } finally {
    console.error = origError;
  }
  const embed = i.calls.edits[0].embeds[0].toJSON();
  assert.equal(embed.color, COLORS.red);
  const text = JSON.stringify(embed);
  assert.ok(!text.includes(BEEZY.apiKey));
  assert.ok(!/Traceback| at /.test(text));
  assert.match(embed.description, /server error/);
});

test('/proj shows projections sorted by Beezy Score and labels unvalidated data', async () => {
  const { execute } = (await loadCommands()).get('proj');
  const fetchImpl = async () => res({
    sport: 'nba', as_of: 'now', engine: 'v1', validated: false, status: 'shadow',
    rows: [
      { player: 'Low Score', team: 'AAA', opp: 'BBB', stat: 'points', line: 20.5, p_over_model: 0.52, proj_mean: 21.1, l5_hit_rate: 0.6, l10_hit_rate: 0.5, beezy_score: 40, side: 'OVER' },
      { player: 'High Score', team: 'CCC', opp: 'DDD', stat: 'points', line: 25.5, p_over_model: 0.61, proj_mean: 27.3, l5_hit_rate: 0.8, l10_hit_rate: 0.7, beezy_score: 88, side: 'OVER' },
    ],
  });
  const i = fakeInteraction({ options: { sport: 'nba' } });
  await execute(i, { beezy: { ...BEEZY, fetchImpl } });
  const embed = i.calls.edits[0].embeds[0].toJSON();
  assert.equal(embed.color, COLORS.sky);
  assert.ok(embed.description.indexOf('High Score') < embed.description.indexOf('Low Score'));
  assert.match(embed.description, /model P\(over\) 61\.0%/);
  assert.match(embed.description, /Beezy 88\/100/);
  assert.match(JSON.stringify(embed.fields), /paper \/ unvalidated/);
});

test('/edge is ephemeral in public channels, not in private ones, and renders the L10 log + odds matrix', async () => {
  const { execute } = (await loadCommands()).get('edge');
  const games = Array.from({ length: 10 }, (_, k) => ({ date: `2026-09-${String(k + 10)}`, opp: 'BOS', val: k % 2 ? 2 : 0, usage: 3 + k, hit: Boolean(k % 2) }));
  const fetchImpl = async () => res({
    rows: [{
      player: 'Aaron Judge', stat: 'hits', line: 1.5, side: 'OVER', book: 'dk', american: 110, ev: 0.04, p_fair: 0.5,
      recentForm: { line: 1.5, games, l5_rate: 0.6, l10_rate: 0.5, szn_avg: 1.2, usage_label: 'PA' },
      all_books: [{ book: 'dk', over: 110, under: -130, fair: 0.5 }],
    }],
  });
  const pub = fakeInteraction({ options: { player: 'judge' }, isPublic: true });
  await execute(pub, { beezy: { ...BEEZY, fetchImpl } });
  assert.equal(pub.calls.defer.flags, MessageFlags.Ephemeral);
  const embed = pub.calls.edits[0].embeds[0].toJSON();
  assert.match(embed.title, /Aaron Judge/);
  assert.match(JSON.stringify(embed.fields), /HIT/);
  assert.match(JSON.stringify(embed.fields), /usage trend rising/);
  assert.match(JSON.stringify(embed.fields), /\+110/);

  const priv = fakeInteraction({ options: { player: 'judge' }, isPublic: false });
  await execute(priv, { beezy: { ...BEEZY, fetchImpl } });
  assert.equal(priv.calls.defer.flags, undefined);

  const none = fakeInteraction({ options: { player: 'zzzz' } });
  await execute(none, { beezy: { ...BEEZY, fetchImpl } });
  assert.match(none.calls.edits[0].embeds[0].toJSON().description, /No cockpit rows/);
});

test('/record shows only API-returned fields, labels paper, and breaks out sports that returned graded rows', async () => {
  const { execute } = (await loadCommands()).get('record');
  const fetchImpl = async (url) => {
    const sport = new URL(url).searchParams.get('sport');
    if (!sport) return res({ ev_headline: { graded: 6000, wins: 3000, losses: 2900, pushes: 100, flat_roi_pct: 0.26, kelly_roi_pct: -0.88, kelly_mult: 1 } });
    if (sport === 'nfl') return res({ ev_headline: { graded: 12, flat_roi_pct: 5.5, kelly_roi_pct: null } });
    return res({ ev_headline: { graded: 0 } });
  };
  const i = fakeInteraction();
  await execute(i, { beezy: { ...BEEZY, fetchImpl } });
  const embed = i.calls.edits[0].embeds[0].toJSON();
  assert.match(embed.description, /Graded: 6000 \(3000W-2900L-100P\)/);
  assert.match(embed.description, /Flat-stake ROI: \+0\.3%/);
  assert.match(embed.description, /Kelly ROI: -0\.9%/);
  assert.ok(!/Logged|Pending|Since/.test(embed.description), 'fields the API did not return are not shown');
  const fields = JSON.stringify(embed.fields);
  assert.match(fields, /By sport/);
  assert.match(fields, /NFL/);
  assert.ok(!/NHL|NBA/.test(fields));
  assert.match(fields, /PAPER/);
  assert.equal(i.calls.edits[0].components[0].toJSON().components[0].url, 'https://beezy.fyi/analytics');
});

test('/link validates the email, stores tier public, is ephemeral, and keeps a synced tier', async () => {
  const { execute } = (await loadCommands()).get('link');
  const bad = fakeInteraction({ options: { email: 'nope' }, userId: '700000000000000001' });
  await execute(bad);
  assert.equal(bad.calls.defer.flags, MessageFlags.Ephemeral);
  assert.equal(bad.calls.edits[0].embeds[0].toJSON().color, COLORS.red);
  assert.equal(db.getMemberLink('700000000000000001'), null);

  const ok = fakeInteraction({ options: { email: 'Fan@Example.com' }, userId: '700000000000000001' });
  await execute(ok);
  assert.equal(ok.calls.defer.flags, MessageFlags.Ephemeral);
  const link = db.getMemberLink('700000000000000001');
  assert.equal(link.email, 'fan@example.com');
  assert.equal(link.tier, 'public');
  assert.match(ok.calls.edits[0].embeds[0].toJSON().description, /source of truth/);

  db.upsertMemberLink('700000000000000001', { tier: 'pro_monthly' });
  await execute(fakeInteraction({ options: { email: 'fan@example.com' }, userId: '700000000000000001' }));
  assert.equal(db.getMemberLink('700000000000000001').tier, 'pro_monthly');
});

test('/ask returns the model answer with mentions disabled, and maps rate limits to a plain message', async () => {
  const { execute } = (await loadCommands()).get('ask');
  const f = async (url) => {
    if (String(url).includes('generativelanguage')) {
      return res({ choices: [{ message: { role: 'assistant', content: '@everyone no data. https://beezy.fyi/ev' } }] });
    }
    return res({});
  };
  const { createRateLimiter } = await import('../src/geminiChat.js');
  const limiter = createRateLimiter({ cooldownMs: 60_000 });
  const deps = { ask: { limiter, beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'g' } } };
  const i = fakeInteraction({ options: { question: 'anything?' }, userId: '5' });
  await execute(i, deps);
  assert.match(i.calls.edits[0].content, /no data/);
  assert.deepEqual(i.calls.edits[0].allowedMentions, { parse: [] });
  const again = fakeInteraction({ options: { question: 'again?' }, userId: '5' });
  const origError = console.error;
  console.error = () => {};
  try { await execute(again, deps); } finally { console.error = origError; }
  assert.match(again.calls.edits[0].embeds[0].toJSON().description, /Slow down/);
});

test('/setup-server refuses non-owners, runs for the owner, and replies ephemeral with gcloud commands', async () => {
  const { execute } = (await loadCommands()).get('setup-server');
  let provisioned = 0;
  const provision = async () => {
    provisioned += 1;
    return {
      roles: [{ name: 'a', status: 'created' }], categories: [], channels: [], warnings: [],
      webhooks: [{ channel: 'ev-sportsbooks', secret: 'discord-webhook-ev-books', env: 'DISCORD_WEBHOOK_EV_BOOKS', url: 'https://discord.com/api/webhooks/123456789012345678/abcDEF_-123', status: 'created' }],
    };
  };
  const stranger = fakeInteraction({ userId: '111' });
  await execute(stranger, { ownerId: '999', provision });
  assert.equal(provisioned, 0);
  assert.equal(stranger.calls.defer.flags, MessageFlags.Ephemeral);
  assert.match(stranger.calls.edits[0].embeds[0].toJSON().description, /Owner-only/);

  const unset = fakeInteraction({ userId: '999' });
  const saved = process.env.OWNER_ID;
  delete process.env.OWNER_ID;
  try {
    await execute(unset, { provision });
  } finally {
    if (saved !== undefined) process.env.OWNER_ID = saved;
  }
  assert.equal(provisioned, 0, 'an unset OWNER_ID never authorises anyone');

  const owner = fakeInteraction({ userId: '999' });
  await execute(owner, { ownerId: '999', provision });
  assert.equal(provisioned, 1);
  assert.equal(owner.calls.defer.flags, MessageFlags.Ephemeral);
  assert.ok(owner.calls.followUps.length >= 2);
  assert.ok(owner.calls.followUps.every((f) => f.flags === MessageFlags.Ephemeral), 'webhook URLs only in ephemeral messages');
  const all = owner.calls.followUps.map((f) => f.content).join('\n');
  assert.match(all, /printf %s 'https:\/\/discord\.com\/api\/webhooks\/123456789012345678\/abcDEF_-123' \| gcloud secrets versions add discord-webhook-ev-books --data-file=-/);
  assert.match(all, /--update-secrets=/);
  assert.ok(owner.calls.followUps.every((f) => f.content.length <= 2000));
  assert.ok(!JSON.stringify(owner.calls.edits).includes('abcDEF_-123'), 'the summary embed never contains the URL');
});

test('welcome: assigns Explorer and posts the welcome embed to #welcome', async () => {
  const added = [];
  const sent = [];
  const explorer = { id: 'r1', name: 'Explorer' };
  const member = {
    id: '123', user: { bot: false },
    roles: { add: async (role) => added.push(role.name) },
    guild: {
      roles: { cache: new Collection([['r1', explorer]]) },
      channels: { cache: new Collection([['c1', { name: 'welcome', isTextBased: () => true, send: async (m) => sent.push(m) }]]) },
    },
  };
  await handleMemberAdd(member);
  assert.deepEqual(added, ['Explorer']);
  const embed = sent[0].embeds[0].toJSON();
  assert.match(embed.description, /\+EV Consensus Terminal/);
  assert.match(embed.description, /Calibrated Prop Models/);
  assert.match(JSON.stringify(embed.fields), /\/ev.*\/proj.*\/ask.*\/link/s);
  assert.deepEqual(sent[0].allowedMentions, { users: ['123'] });

  // missing role / channel is fine; bots are ignored
  await handleMemberAdd({ ...member, guild: { roles: { cache: new Collection() }, channels: { cache: new Collection() } } });
  await handleMemberAdd({ ...member, user: { bot: true } });
  assert.equal(added.length, 1);
});

test('mention handling: extracts the question and replies with mentions disabled', async () => {
  assert.equal(extractQuestion('<@42> what is +EV?  ', '42'), 'what is +EV?');
  assert.equal(extractQuestion('<@!42>', '42'), '');
  const replies = [];
  const f = async () => res({ choices: [{ message: { role: 'assistant', content: 'answer' } }] });
  const client = { user: { id: '42' } };
  const message = {
    guild: { id: 'g' }, author: { id: '7', bot: false }, content: '<@42> hello',
    mentions: { users: new Map([['42', {}]]) },
    channel: { sendTyping: async () => {} },
    reply: async (m) => replies.push(m),
  };
  const { createRateLimiter } = await import('../src/geminiChat.js');
  const handled = await handleMention(message, client, { limiter: createRateLimiter(), beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'g' } });
  assert.equal(handled, true);
  assert.equal(replies[0].content, 'answer');
  assert.deepEqual(replies[0].allowedMentions, { parse: [], repliedUser: false });
  assert.equal(await handleMention({ ...message, mentions: { users: new Map() } }, client), false);
  assert.equal(await handleMention({ ...message, author: { id: '8', bot: true } }, client), false);
});

test('every source, test and doc file is ASCII-only', () => {
  const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
  const offenders = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (name === 'node_modules' || name === 'package-lock.json') continue;
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) { walk(full); continue; }
      if (name.endsWith('.db')) continue;
      const buf = readFileSync(full);
      if (buf.some((b) => b > 127)) offenders.push(path.relative(root, full));
    }
  };
  walk(root);
  assert.deepEqual(offenders, []);
});
