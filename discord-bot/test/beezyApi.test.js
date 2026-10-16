import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/beezyApi.js';

const BASE = 'https://api.example.test';
const KEY = 'sekret-key-123';

function mockFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init, calls.length);
  };
  fn.calls = calls;
  return fn;
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) };
}

const opts = (fetchImpl, extra = {}) => ({ fetchImpl, baseUrl: BASE, apiKey: KEY, ...extra });

test('fetchEvFeed sends X-API-Key and builds the query string', async () => {
  const f = mockFetch(() => jsonResponse({ mlb: { rows: [] } }));
  await api.fetchEvFeed({ sport: 'nfl', minEv: 0.03, limit: 50 }, opts(f));
  const { url, init } = f.calls[0];
  const u = new URL(url);
  assert.equal(`${u.origin}${u.pathname}`, `${BASE}/api/public/ev/feed`);
  assert.equal(u.searchParams.get('sport'), 'nfl');
  assert.equal(u.searchParams.get('min_ev'), '0.03');
  assert.equal(u.searchParams.get('limit'), '50');
  assert.equal(init.headers['X-API-Key'], KEY);
  assert.ok(!url.includes(KEY), 'key must never be in the URL');
});

test('sport "all" and empty params are omitted from the query', async () => {
  const f = mockFetch(() => jsonResponse({}));
  await api.fetchEvFeed({ sport: 'all' }, opts(f));
  assert.equal(f.calls[0].url, `${BASE}/api/public/ev/feed`);
  assert.equal(api.buildQuery({ a: 1, b: undefined, c: null, d: '' }), '?a=1');
  assert.equal(api.buildQuery({}), '');
});

test('trailing slash on baseUrl is tolerated', async () => {
  const f = mockFetch(() => jsonResponse({}));
  await api.fetchAnalytics({ system: 'EV' }, opts(f, { baseUrl: `${BASE}/` }));
  assert.equal(f.calls[0].url, `${BASE}/api/public/analytics/deep-dive?system=EV`);
});

test('fetchProjections hits /api/public/projections and normalises rows', async () => {
  const f = mockFetch(() => jsonResponse({ sport: 'nba', validated: false, rows: [{ player: 'A' }] }));
  const data = await api.fetchProjections({ sport: 'nba', market: 'points', limit: 20 }, opts(f));
  const u = new URL(f.calls[0].url);
  assert.equal(u.pathname, '/api/public/projections');
  assert.equal(u.searchParams.get('market'), 'points');
  assert.equal(data.rows.length, 1);
  const empty = await api.fetchProjections({ sport: 'nba' }, opts(mockFetch(() => jsonResponse({ status: 'idle' }))));
  assert.deepEqual(empty.rows, []);
});

test('fetchCockpit filters rows by case-insensitive player name client-side', async () => {
  const f = mockFetch(() => jsonResponse({
    as_of: 'x',
    rows: [
      { player: 'Aaron Judge', stat: 'hits' },
      { player: 'Patrick Mahomes', stat: 'pass_yds' },
      { player: 'AARON NOLA', stat: 'k' },
      { player: null, stat: 'z' },
    ],
  }));
  const data = await api.fetchCockpit({ sport: 'mlb', player: 'aaron judge' }, opts(f));
  assert.deepEqual(data.rows.map((r) => r.player), ['Aaron Judge']);
  const u = new URL(f.calls[0].url);
  assert.equal(u.pathname, '/api/public/edge/cockpit');
  assert.equal(u.searchParams.get('sport'), 'mlb');
  assert.ok(!u.searchParams.has('player'), 'player is filtered client-side, not sent');
  const partial = await api.fetchCockpit({ player: 'AARON' }, opts(f));
  assert.equal(partial.rows.length, 2);
  const all = await api.fetchCockpit({}, opts(f));
  assert.equal(all.rows.length, 4);
});

test('HTTP errors map to plain BeezyApiError messages without secrets', async () => {
  for (const [status, re] of [[401, /rejected the bot credentials/], [404, /not available yet/], [429, /rate limiting/], [503, /server error/]]) {
    const f = mockFetch(() => jsonResponse({ error: `boom ${KEY}` }, status));
    await assert.rejects(() => api.fetchEvFeed({}, opts(f)), (err) => {
      assert.ok(err instanceof api.BeezyApiError);
      assert.equal(err.kind, 'http');
      assert.equal(err.status, status);
      assert.match(err.message, re);
      assert.ok(!err.message.includes(KEY));
      return true;
    });
  }
});

test('timeout maps to kind=timeout', async () => {
  const f = (url, init) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  });
  await assert.rejects(() => api.fetchEvFeed({}, opts(f, { timeoutMs: 20 })), (err) => err.kind === 'timeout' && /timed out/.test(err.message));
});

test('network failure and bad JSON map to network/parse errors', async () => {
  const boom = async () => { throw new TypeError(`fetch failed for ${KEY}`); };
  await assert.rejects(() => api.fetchEvFeed({}, opts(boom)), (err) => err.kind === 'network' && !err.message.includes(KEY));
  const bad = async () => ({ ok: true, status: 200, text: async () => '<html>' });
  await assert.rejects(() => api.fetchEvFeed({}, opts(bad)), (err) => err.kind === 'parse');
});

test('missing configuration fails fast with kind=config', async () => {
  const f = mockFetch(() => jsonResponse({}));
  await assert.rejects(() => api.fetchEvFeed({}, { fetchImpl: f, baseUrl: '', apiKey: KEY }), (err) => err.kind === 'config');
  await assert.rejects(() => api.fetchEvFeed({}, { fetchImpl: f, baseUrl: BASE, apiKey: '' }), (err) => err.kind === 'config');
  assert.equal(f.calls.length, 0);
});

test('formatting helpers', () => {
  assert.equal(api.formatAmerican(110), '+110');
  assert.equal(api.formatAmerican(-120.4), '-120');
  assert.equal(api.formatAmerican(null), 'n/a');
  assert.equal(api.formatAmerican(0), 'n/a');
  assert.equal(api.formatPct(0.5132), '51.3%');
  assert.equal(api.formatPct(0.042, { signed: true }), '+4.2%');
  assert.equal(api.formatPct(-0.01, { signed: true }), '-1.0%');
  assert.equal(api.formatPct(undefined), 'n/a');
  assert.equal(api.formatPctPoints(8.24), '+8.2%');
  assert.equal(api.formatNum(24.5), '24.5');
  assert.equal(api.formatNum(3), '3');
  assert.equal(api.formatNum(null), 'n/a');
  // +110 at 55% fair: b=1.1, f=(1.1*.55-.45)/1.1 = 0.1409; quarter = 0.0352
  assert.ok(Math.abs(api.quarterKellyFraction(0.55, 110) - 0.0352) < 0.0005);
  assert.equal(api.quarterKellyFraction(0.4, 110), null);
});

test('normalizeEvFeed flattens mlb, exchange and outlier rows, sorted by EV', () => {
  const feed = {
    mlb: { rows: [{ player: 'Judge', bet_type: 'hits', edge: 0.05, odds: 120, book: 'DraftKings', model_prob: 0.5, n_books: 6 }] },
    nfl: {
      validated: false,
      rows: [{ venue: 'kalshi', player: 'Mahomes', stat: 'pass_yds', strike: 275.5, side: 'OVER', ask: 0.42, net_ev: 0.08, fair_prob: 0.52, n_books: 5 }],
      book_outliers: [{ book: 'FanDuel', player: 'Kelce', stat: 'rec_yds', point: 60.5, side: 'OVER', american: 105, ev: 0.03, p_fair: 0.51, n_books: 7, flag: true }],
    },
    nhl: { validated: false, rows: [{ book: 'Caesars', player: 'McDavid', market: 'sog', point: 3.5, side: 'OVER', american: -110, ev: 0.02, p_fair: 0.55, n_books: 5, flag: false }] },
    nba: null,
  };
  const plays = api.normalizeEvFeed(feed);
  assert.deepEqual(plays.map((p) => p.player), ['Mahomes', 'Judge', 'Kelce', 'McDavid']);
  assert.equal(plays[0].price_text, '42c');
  assert.equal(plays[0].validated, false);
  assert.equal(plays[1].validated, true);
  assert.equal(plays[3].flag, false);
  assert.deepEqual(api.normalizeEvFeed(feed, { book: 'fanduel' }).map((p) => p.player), ['Kelce']);
  assert.deepEqual(api.normalizeEvFeed(feed, { sport: 'nfl' }).map((p) => p.player), ['Mahomes', 'Kelce']);
  assert.deepEqual(api.normalizeEvFeed(feed, { minEv: 0.04 }).map((p) => p.player), ['Mahomes', 'Judge']);
  assert.deepEqual(api.normalizeEvFeed(null), []);
});
