import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ChannelType, Collection, OverwriteType, PermissionFlagsBits } from 'discord.js';
import * as setup from '../src/serverSetup.js';

const allCategories = setup.LAYOUT;
const allChannels = allCategories.flatMap((c) => c.channels.map((ch) => ({ category: c, channel: ch })));
const find = (name) => allChannels.find((x) => x.channel.name === name);
const access = (name, roles = []) => {
  const { category, channel } = find(name);
  return setup.effectiveAccess(setup.overwritesFor(category, channel), roles);
};

test('six roles with the specified colors', () => {
  assert.deepEqual(setup.ROLE_SPECS.map((r) => [r.name, r.color]), [
    ['All-Access Pro', '#57F287'],
    ['NFL Pass', '#f59e0b'],
    ['NBA Pass', '#818cf8'],
    ['NHL Pass', '#38bdf8'],
    ['MLB Pass', '#34d399'],
    ['Explorer', '#9ca3af'],
  ]);
  assert.deepEqual(setup.PAID_ROLE_NAMES, ['All-Access Pro', 'NFL Pass', 'NBA Pass', 'NHL Pass', 'MLB Pass']);
});

test('categories and channels match the specified layout', () => {
  assert.deepEqual(setup.LAYOUT.map((c) => [c.name, c.channels.map((ch) => ch.name)]), [
    ['START HERE', ['welcome', 'announcements', 'link-account']],
    ['LIVE +EV & EXCHANGE ALERTS', ['ev-sportsbooks', 'ev-exchanges', 'daily-free-plays']],
    ['DAILY MODEL CARDS', ['mlb-alerts', 'nfl-alerts', 'nba-alerts', 'nhl-alerts']],
    ['PUBLIC LEDGER & RECAPS', ['daily-recap']],
    ['SHARP COMMUNITY', ['general-sweat', 'betting-theory']],
    ['OPS & TWEET QUEUE', ['ops-alerts', 'tweet-queue', 'deploys']],
  ]);
});

test('exact channel topics for the specified channels', () => {
  const topic = (n) => find(n).channel.topic;
  assert.equal(topic('ev-sportsbooks'), 'Live 15-min soft-book +EV dislocations (DK, FD, MGM, Caesars, Bet365) -> https://beezy.fyi/ev');
  assert.equal(topic('ev-exchanges'), 'Zero-fee Novig & Kalshi order-book dislocations -> https://beezy.fyi/ev');
  assert.equal(topic('daily-free-plays'), '2 free daily Watch/Confluence plays + public audit -> https://beezy.fyi');
  assert.equal(topic('daily-recap'), 'Nightly graded settlement receipt cards + season ROI ledger -> https://beezy.fyi/results');
});

test('visibility matrix: public read-only START HERE', () => {
  for (const n of ['welcome', 'announcements', 'link-account']) {
    assert.deepEqual(access(n), { view: true, send: false }, n);
    assert.deepEqual(access(n, ['Explorer']), { view: true, send: false }, n);
  }
});

test('visibility matrix: +EV channels are All-Access Pro only, free plays are public', () => {
  for (const n of ['ev-sportsbooks', 'ev-exchanges']) {
    assert.deepEqual(access(n), { view: false, send: false }, n);
    assert.deepEqual(access(n, ['Explorer']), { view: false, send: false }, n);
    for (const pass of ['NFL Pass', 'NBA Pass', 'NHL Pass', 'MLB Pass']) {
      assert.equal(access(n, [pass]).view, false, `${n} / ${pass}`);
    }
    assert.deepEqual(access(n, ['All-Access Pro']), { view: true, send: false }, n);
  }
  assert.deepEqual(access('daily-free-plays'), { view: true, send: false });
});

test('visibility matrix: each model card is All-Access Pro + that sport pass only', () => {
  const passes = { 'mlb-alerts': 'MLB Pass', 'nfl-alerts': 'NFL Pass', 'nba-alerts': 'NBA Pass', 'nhl-alerts': 'NHL Pass' };
  for (const [channel, pass] of Object.entries(passes)) {
    assert.equal(access(channel).view, false, `${channel} hidden from everyone`);
    assert.deepEqual(access(channel, ['All-Access Pro']), { view: true, send: false }, channel);
    assert.deepEqual(access(channel, [pass]), { view: true, send: false }, channel);
    for (const other of Object.values(passes).filter((p) => p !== pass)) {
      assert.equal(access(channel, [other]).view, false, `${channel} / ${other}`);
    }
    assert.deepEqual(setup.visibleSet(find(channel).category, find(channel).channel).sort(), ['All-Access Pro', pass].sort());
  }
});

test('visibility matrix: recap is public read-only, community channels allow chat', () => {
  assert.deepEqual(access('daily-recap'), { view: true, send: false });
  for (const n of ['general-sweat', 'betting-theory']) {
    assert.deepEqual(access(n), { view: true, send: true }, n);
  }
});

test('visibility matrix: OPS channels are hidden from @everyone and from every paid role', () => {
  const everyRole = setup.ROLE_SPECS.map((r) => r.name);
  for (const n of ['ops-alerts', 'tweet-queue', 'deploys']) {
    assert.deepEqual(access(n), { view: false, send: false }, n);
    assert.equal(access(n, everyRole).view, false, n);
    const spec = setup.overwritesFor(find(n).category, find(n).channel);
    const everyone = spec.find((o) => o.target === setup.EVERYONE);
    assert.ok(everyone.deny.includes('ViewChannel'));
    assert.equal(spec.length, 1, 'no role is granted access; owner/admins are handled at apply time');
  }
});

test('read-only categories deny SendMessages for @everyone on every channel', () => {
  for (const category of allCategories.filter((c) => c.readOnly)) {
    for (const channel of category.channels) {
      const everyone = setup.overwritesFor(category, channel).find((o) => o.target === setup.EVERYONE);
      assert.ok(everyone.deny.includes('SendMessages'), `${category.name}/#${channel.name}`);
      assert.ok(!everyone.allow.includes('SendMessages'), `${category.name}/#${channel.name}`);
    }
  }
  for (const n of ['general-sweat', 'betting-theory']) {
    const everyone = setup.overwritesFor(find(n).category, find(n).channel).find((o) => o.target === setup.EVERYONE);
    assert.ok(everyone.allow.includes('SendMessages'));
  }
});

test('webhook targets map to the Python-side secret names and env vars', () => {
  assert.deepEqual(setup.WEBHOOK_TARGETS.map((t) => [t.channel, t.secret, t.env]), [
    ['ev-sportsbooks', 'discord-webhook-ev-books', 'DISCORD_WEBHOOK_EV_BOOKS'],
    ['ev-exchanges', 'discord-webhook-ev-exchanges', 'DISCORD_WEBHOOK_EV_EXCHANGES'],
    ['mlb-alerts', 'discord-webhook-card-mlb', 'DISCORD_WEBHOOK_CARD_MLB'],
    ['nfl-alerts', 'discord-webhook-card-nfl', 'DISCORD_WEBHOOK_CARD_NFL'],
    ['nba-alerts', 'discord-webhook-card-nba', 'DISCORD_WEBHOOK_CARD_NBA'],
    ['nhl-alerts', 'discord-webhook-card-nhl', 'DISCORD_WEBHOOK_CARD_NHL'],
    ['daily-recap', 'discord-webhook-recap', 'DISCORD_WEBHOOK_RECAP'],
    ['ops-alerts', 'discord-ops-webhook-url', 'DISCORD_WEBHOOK_OPS'],
    ['daily-free-plays', 'discord-webhook-free-plays', 'DISCORD_WEBHOOK_FREE_PLAYS'],
    ['tweet-queue', 'discord-webhook-tweets', 'DISCORD_WEBHOOK_TWEETS'],
  ]);
  const names = new Set(allChannels.map((x) => x.channel.name));
  for (const t of setup.WEBHOOK_TARGETS) assert.ok(names.has(t.channel));
});

test('gcloud command builder: printf pipe, --update-secrets only, bad URLs skipped, chunked', () => {
  const hooks = setup.WEBHOOK_TARGETS.map((t, i) => ({
    ...t, url: `https://discord.com/api/webhooks/${100000000000000000 + i}/tok-${i}_abc`,
  }));
  hooks.push({ channel: 'evil', secret: 's', env: 'E', url: "https://x.test/a'; rm -rf / #" });
  const { blocks, skipped } = setup.buildGcloudCommands(hooks, { service: 'svc', region: 'us-central1', maxChars: 900 });
  assert.deepEqual(skipped, ['evil']);
  const text = blocks.join('\n');
  assert.ok(blocks.length >= 3, 'chunked into several messages');
  assert.ok(blocks.every((b) => b.length <= 1100));
  assert.match(text, /printf %s 'https:\/\/discord\.com\/api\/webhooks\/\d+\/tok-0_abc' \| gcloud secrets versions add discord-webhook-ev-books --data-file=-/);
  assert.match(text, /--update-secrets=DISCORD_WEBHOOK_EV_BOOKS=discord-webhook-ev-books:latest,/);
  assert.match(text, /gcloud run services update svc --region us-central1/);
  assert.ok(!text.includes('--set-secrets'));
  assert.ok(!text.includes('rm -rf'));
  assert.ok(!text.includes('echo'), 'printf %s so no trailing newline lands in the secret');
});

// -- fake guild for idempotency ------------------------------------------------

function makeFakeGuild() {
  let seq = 1000;
  const nextId = () => String(++seq);
  const created = { roles: 0, categories: 0, channels: 0, webhooks: 0 };
  const everyone = { id: 'guild-1', name: '@everyone' };

  const makeChannel = (o) => {
    const ch = {
      id: nextId(), name: o.name, type: o.type, parentId: o.parent ?? null, topic: o.topic ?? null,
      overwrites: o.permissionOverwrites ?? [], hooks: [],
      permissionOverwrites: { set: async (list) => { ch.overwrites = list; } },
      setTopic: async (t) => { ch.topic = t; },
      setName: async (n) => { ch.name = n; },
      setParent: async (p) => { ch.parentId = p; },
      fetchWebhooks: async () => new Collection(ch.hooks.map((h) => [h.id, h])),
      createWebhook: async ({ name }) => {
        created.webhooks += 1;
        const id = nextId();
        const hook = { id, name, url: `https://discord.com/api/webhooks/${id}/tok${id}` };
        ch.hooks.push(hook);
        return hook;
      },
    };
    return ch;
  };

  const guild = {
    id: everyone.id,
    created,
    roles: {
      everyone,
      cache: new Collection(),
      fetch: async () => guild.roles.cache,
      create: async ({ name, color }) => {
        created.roles += 1;
        const role = { id: nextId(), name, color, setColor: async (c) => { role.color = c; } };
        guild.roles.cache.set(role.id, role);
        return role;
      },
    },
    channels: {
      cache: new Collection(),
      fetch: async () => guild.channels.cache,
      create: async (o) => {
        if (o.type === ChannelType.GuildCategory) created.categories += 1; else created.channels += 1;
        const ch = makeChannel(o);
        guild.channels.cache.set(ch.id, ch);
        return ch;
      },
    },
    members: { fetch: async (id) => ({ id }) },
  };
  return guild;
}

const OPTS = { ownerId: '900000000000000001', botUserId: '900000000000000002' };

test('provision renames a legacy #<sport>-card channel in place instead of creating a duplicate', async () => {
  const guild = makeFakeGuild();
  await setup.provisionBeezyServer(guild, OPTS);
  const nfl = guild.channels.cache.find((c) => c.name === 'nfl-alerts');
  nfl.name = 'nfl-card'; // pretend the server still has the pre-rename name
  const before = guild.channels.cache.size;
  const result = await setup.provisionBeezyServer(guild, OPTS);
  assert.equal(guild.channels.cache.size, before, 'no duplicate channel created');
  assert.equal(nfl.name, 'nfl-alerts');
  assert.deepEqual(result.channels.find((c) => c.name === 'nfl-alerts'), { name: 'nfl-alerts', status: 'renamed', from: 'nfl-card' });
});

test('provision on an empty guild creates everything', async () => {
  const guild = makeFakeGuild();
  const result = await setup.provisionBeezyServer(guild, OPTS);
  assert.deepEqual(guild.created, { roles: 6, categories: 6, channels: 16, webhooks: 10 });
  assert.ok(result.roles.every((r) => r.status === 'created'));
  assert.equal(result.webhooks.length, 10);
  assert.ok(result.webhooks.every((w) => /^https:\/\/discord\.com\/api\/webhooks\/\d+\/tok\d+$/.test(w.url)));
  assert.deepEqual(result.warnings, []);

  const ch = (name) => guild.channels.cache.find((c) => c.name === name);
  const roleId = (name) => guild.roles.cache.find((r) => r.name === name).id;
  assert.equal(ch('ev-sportsbooks').parentId, guild.channels.cache.find((c) => c.name === 'LIVE +EV & EXCHANGE ALERTS').id);
  assert.equal(ch('ev-sportsbooks').topic, setup.LAYOUT[1].channels[0].topic);

  // the real discord overwrites carry the right bit flags and ids
  const evOw = ch('ev-sportsbooks').overwrites;
  const evEveryone = evOw.find((o) => o.id === guild.id);
  assert.deepEqual(evEveryone.deny.sort(), [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages].sort());
  const evPro = evOw.find((o) => o.id === roleId('All-Access Pro'));
  assert.equal(evPro.type, OverwriteType.Role);
  assert.deepEqual(evPro.allow, [PermissionFlagsBits.ViewChannel]);
  assert.deepEqual(evPro.deny, [PermissionFlagsBits.SendMessages]);
  assert.ok(evOw.some((o) => o.id === OPTS.botUserId && o.type === OverwriteType.Member), 'bot keeps access to gated channels');

  const ops = ch('ops-alerts').overwrites;
  assert.ok(ops.find((o) => o.id === guild.id).deny.includes(PermissionFlagsBits.ViewChannel));
  assert.ok(ops.some((o) => o.id === OPTS.ownerId && o.type === OverwriteType.Member), 'owner sees OPS');
  assert.equal(ops.filter((o) => o.type === OverwriteType.Role).length, 1, 'only @everyone (denied) on OPS');

  const pub = ch('daily-recap').overwrites;
  assert.equal(pub.length, 1);
  assert.deepEqual(pub[0].allow, [PermissionFlagsBits.ViewChannel]);
  assert.deepEqual(pub[0].deny, [PermissionFlagsBits.SendMessages]);
});

test('running provision twice creates nothing the second time', async () => {
  const guild = makeFakeGuild();
  const first = await setup.provisionBeezyServer(guild, OPTS);
  const snapshot = { ...guild.created };
  const second = await setup.provisionBeezyServer(guild, OPTS);
  assert.deepEqual(guild.created, snapshot, 'no role/category/channel/webhook created on rerun');
  assert.ok(second.roles.every((r) => r.status === 'existing'));
  assert.ok(second.categories.every((c) => c.status === 'existing'));
  assert.ok(second.channels.every((c) => c.status === 'existing'));
  assert.ok(second.webhooks.every((w) => w.status === 'existing'));
  // same webhook URLs are returned both times
  assert.deepEqual(second.webhooks.map((w) => w.url), first.webhooks.map((w) => w.url));
  assert.equal(guild.roles.cache.size, 6);
  assert.equal(guild.channels.cache.size, 22);
});

test('rerun repairs drifted topics, parents, role colors and overwrites on existing objects', async () => {
  const guild = makeFakeGuild();
  await setup.provisionBeezyServer(guild, OPTS);
  const snapshot = { ...guild.created };
  const ev = guild.channels.cache.find((c) => c.name === 'ev-exchanges');
  const wantTopic = ev.topic;
  const wantParent = ev.parentId;
  ev.topic = 'stale topic';
  ev.parentId = null;
  ev.overwrites = [];
  guild.roles.cache.find((r) => r.name === 'NFL Pass').color = 0x000000;

  const result = await setup.provisionBeezyServer(guild, OPTS);
  assert.deepEqual(guild.created, snapshot);
  assert.equal(ev.topic, wantTopic);
  assert.equal(ev.parentId, wantParent);
  assert.ok(ev.overwrites.length >= 2);
  assert.equal(guild.roles.cache.find((r) => r.name === 'NFL Pass').color, 0xf59e0b);
  assert.equal(result.roles.find((r) => r.name === 'NFL Pass').status, 'updated');
  assert.deepEqual(result.channels.find((c) => c.name === 'ev-exchanges').changes.sort(), ['moved', 'topic']);
});

test('a missing owner membership is a warning, not a failure', async () => {
  const guild = makeFakeGuild();
  guild.members.fetch = async () => { throw new Error('Unknown Member'); };
  const result = await setup.provisionBeezyServer(guild, OPTS);
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /Owner is not a member/);
});
