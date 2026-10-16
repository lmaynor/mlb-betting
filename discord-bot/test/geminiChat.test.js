import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as gem from '../src/geminiChat.js';

const GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';
const BEEZY = { baseUrl: 'https://api.example.test', apiKey: 'beezy-key' };

function res(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) };
}

function toolCallMsg(name, args, id = 'call_1') {
  return {
    role: 'assistant',
    content: null,
    tool_calls: [{ id, type: 'function', function: { name, arguments: JSON.stringify(args) } }],
  };
}

const finalMsg = (content) => ({ role: 'assistant', content });
const choice = (message) => ({ choices: [{ message }] });

/** One fetch that serves both Gemini and the Beezy API so a single mock can see everything. */
function router({ gemini, beezy }) {
  const calls = { gemini: [], beezy: [] };
  const fn = async (url, init) => {
    if (String(url).startsWith(GEMINI_ENDPOINT)) {
      calls.gemini.push({ url, init, body: JSON.parse(init.body) });
      return gemini(calls.gemini.length, calls.gemini.at(-1));
    }
    calls.beezy.push({ url, init });
    return beezy(url, init);
  };
  fn.calls = calls;
  return fn;
}

test('tool round then final answer: auth header, default model, tool schema names, tool executed', async () => {
  delete process.env.GEMINI_MODEL;
  const f = router({
    gemini: (n) => (n === 1
      ? res(choice(toolCallMsg('get_live_ev_plays', { sport: 'nfl', min_ev: 0.03 })))
      : res(choice(finalMsg('Top play: Mahomes OVER at kalshi 42c. https://beezy.fyi/ev')))),
    beezy: () => res({
      as_of: 'now',
      mlb: { rows: [] },
      nfl: { validated: false, rows: [{ venue: 'kalshi', player: 'Mahomes', stat: 'pass_yds', strike: 275.5, side: 'OVER', ask: 0.42, net_ev: 0.08, fair_prob: 0.52, n_books: 5 }] },
    }),
  });

  const answer = await gem.askBeezy('best nfl play?', {
    userId: 'u1', limiter: gem.createRateLimiter(), beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'g-key' },
  });
  assert.match(answer, /Mahomes/);

  // request 1
  const first = f.calls.gemini[0];
  assert.equal(first.init.headers.Authorization, 'Bearer g-key');
  assert.equal(first.body.model, 'gemini-2.5-flash');
  assert.deepEqual(first.body.tools.map((t) => t.function.name), [
    'get_live_ev_plays', 'get_player_projection', 'get_player_l10_cockpit', 'get_track_record_stats',
  ]);
  assert.equal(first.body.messages[0].role, 'system');
  assert.match(first.body.messages[0].content, /UNTRUSTED_TOOL_DATA/);
  assert.match(first.body.messages[0].content, /validated:false/);
  assert.match(first.body.messages[0].content, /https:\/\/beezy\.fyi\/ev/);

  // beezy tool executed with the key header and the right query
  assert.equal(f.calls.beezy.length, 1);
  const bu = new URL(f.calls.beezy[0].url);
  assert.equal(bu.pathname, '/api/public/ev/feed');
  assert.equal(bu.searchParams.get('sport'), 'nfl');
  assert.equal(f.calls.beezy[0].init.headers['X-API-Key'], 'beezy-key');

  // request 2 carries the wrapped tool result and still no secrets
  const second = f.calls.gemini[1].body;
  const toolMsg = second.messages.find((m) => m.role === 'tool');
  assert.equal(toolMsg.tool_call_id, 'call_1');
  assert.match(toolMsg.content, /^BEGIN_UNTRUSTED_TOOL_DATA tool=get_live_ev_plays/);
  assert.match(toolMsg.content, /"player":"Mahomes"/);
  assert.match(toolMsg.content, /"ev_pct":8/);
  assert.ok(!JSON.stringify(second).includes('beezy-key'));
});

test('GEMINI_MODEL env overrides the default model', async () => {
  process.env.GEMINI_MODEL = 'gemini-custom';
  try {
    const f = router({ gemini: () => res(choice(finalMsg('ok'))), beezy: () => res({}) });
    await gem.chat([{ role: 'user', content: 'hi' }], { fetchImpl: f, apiKey: 'k' });
    assert.equal(f.calls.gemini[0].body.model, 'gemini-custom');
    assert.equal('tools' in f.calls.gemini[0].body, false);
  } finally {
    delete process.env.GEMINI_MODEL;
  }
});

test('tool rounds are capped at 4, then a final request is sent with no tools', async () => {
  let executed = 0;
  const f = router({
    gemini: (n, call) => (call.body.tools
      ? res(choice(toolCallMsg('get_track_record_stats', {}, `c${n}`)))
      : res(choice(finalMsg('Giving the best answer I can. https://beezy.fyi/ev')))),
    beezy: () => { executed += 1; return res({ ev_headline: { graded: 10 }, edge_buckets: [], book_leaderboard: [] }); },
  });
  const text = await gem.chat([{ role: 'user', content: 'x' }], {
    fetchImpl: f, apiKey: 'k', tools: gem.TOOLS, toolHandler: gem.createToolHandler({ ...BEEZY, fetchImpl: f }),
  });
  assert.equal(executed, gem.MAX_TOOL_ROUNDS);
  assert.equal(f.calls.gemini.length, gem.MAX_TOOL_ROUNDS + 1);
  assert.ok(f.calls.gemini.slice(0, 4).every((c) => Array.isArray(c.body.tools)));
  assert.equal('tools' in f.calls.gemini[4].body, false);
  assert.match(text, /best answer/);
});

test('a model that never stops asking for tools yields a fallback, not a hang', async () => {
  const f = router({
    gemini: (n) => res(choice(toolCallMsg('get_track_record_stats', {}, `c${n}`))),
    beezy: () => res({ ev_headline: null }),
  });
  const answer = await gem.askBeezy('loop', {
    userId: 'u2', limiter: gem.createRateLimiter(), beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'k' },
  });
  assert.equal(f.calls.gemini.length, 5);
  assert.match(answer, /could not finish/);
});

test('tool errors are returned to the model as tool messages, never thrown', async () => {
  const f = router({
    gemini: (n) => (n === 1
      ? res(choice(toolCallMsg('get_player_projection', { sport: 'mlb', player_name: 'Judge' })))
      : res(choice(finalMsg('The projection tool returned nothing. https://beezy.fyi/edge')))),
    beezy: () => res({ error: 'down' }, 503),
  });
  const answer = await gem.askBeezy('judge?', {
    userId: 'u3', limiter: gem.createRateLimiter(), beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'k' },
  });
  assert.match(answer, /returned nothing/);
  const tool = f.calls.gemini[1].body.messages.find((m) => m.role === 'tool');
  assert.match(tool.content, /"error":"The Beezy API had a server error/);
  assert.match(tool.content, /Say so plainly/);
});

test('unknown tool and malformed arguments are handled without throwing', async () => {
  const handler = gem.createToolHandler({ ...BEEZY, fetchImpl: async () => res({}) });
  const unknown = await handler('drop_database', {});
  assert.match(unknown, /unknown tool/);
  const f = router({
    gemini: (n) => (n === 1
      ? res(choice({ role: 'assistant', content: null, tool_calls: [{ id: 'x', type: 'function', function: { name: 'get_track_record_stats', arguments: '{not json' } }] }))
      : res(choice(finalMsg('fine')))),
    beezy: () => res({ ev_headline: { graded: 1 } }),
  });
  const text = await gem.chat([{ role: 'user', content: 'x' }], {
    fetchImpl: f, apiKey: 'k', tools: gem.TOOLS, toolHandler: gem.createToolHandler({ ...BEEZY, fetchImpl: f }),
  });
  assert.equal(text, 'fine');
});

test('tool results are hard-capped in size', async () => {
  const huge = { rows: Array.from({ length: 500 }, (_, i) => ({ player: `P${i}`, note: 'x'.repeat(100) })) };
  const wrapped = gem.wrapToolResult('get_player_projection', huge, 1000);
  assert.ok(wrapped.length < 1000 + 200);
  assert.match(wrapped, /truncated=true/);
  assert.match(wrapped, /END_UNTRUSTED_TOOL_DATA$/);
  const small = gem.wrapToolResult('t', { a: 1 });
  assert.ok(!small.includes('truncated'));
});

test('final replies are clamped to 2000 characters', async () => {
  const long = 'x'.repeat(5000);
  const f = router({ gemini: () => res(choice(finalMsg(long))), beezy: () => res({}) });
  const answer = await gem.askBeezy('long', {
    userId: 'u4', limiter: gem.createRateLimiter(), beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'k' },
  });
  assert.equal(answer.length, 2000);
  assert.match(answer, /\[truncated\]$/);
  assert.equal(gem.fitDiscord('short'), 'short');
  assert.equal(gem.fitDiscord('y'.repeat(2000)).length, 2000);
});

test('HTTP and config failures raise GeminiError without leaking the key', async () => {
  const f429 = router({ gemini: () => res({ error: { message: 'quota k-secret' } }, 429), beezy: () => res({}) });
  await assert.rejects(
    () => gem.chat([{ role: 'user', content: 'x' }], { fetchImpl: f429, apiKey: 'k-secret' }),
    (err) => err instanceof gem.GeminiError && err.status === 429 && !err.message.includes('k-secret'),
  );
  await assert.rejects(
    () => gem.chat([{ role: 'user', content: 'x' }], { fetchImpl: f429, apiKey: '' }),
    (err) => err.kind === 'config',
  );
  const slow = (url, init) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(Object.assign(new Error('x'), { name: 'AbortError' })));
  });
  await assert.rejects(
    () => gem.chat([{ role: 'user', content: 'x' }], { fetchImpl: slow, apiKey: 'k', timeoutMs: 20 }),
    (err) => err.kind === 'timeout',
  );
});

test('per-user cooldown and hourly cap', () => {
  let t = 0;
  const limiter = gem.createRateLimiter({ cooldownMs: 1000, maxPerHour: 3, now: () => t });
  assert.equal(limiter.check('a').ok, true);
  t = 500;
  const blocked = limiter.check('a');
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, 'cooldown');
  assert.equal(blocked.retryAfterMs, 500);
  assert.equal(limiter.check('b').ok, true); // other users unaffected
  t = 1500; assert.equal(limiter.check('a').ok, true);
  t = 3000; assert.equal(limiter.check('a').ok, true);
  t = 4500;
  const hourly = limiter.check('a');
  assert.equal(hourly.ok, false);
  assert.equal(hourly.reason, 'hourly');
  t = 3600_000 + 1600; // oldest call has aged out
  assert.equal(limiter.check('a').ok, true);
});

test('askBeezy raises RateLimitError before any network call', async () => {
  const f = router({ gemini: () => res(choice(finalMsg('ok'))), beezy: () => res({}) });
  const limiter = gem.createRateLimiter({ cooldownMs: 60_000 });
  const deps = { userId: 'u9', limiter, beezy: { ...BEEZY, fetchImpl: f }, gemini: { fetchImpl: f, apiKey: 'k' } };
  await gem.askBeezy('one', deps);
  await assert.rejects(() => gem.askBeezy('two', deps), (err) => err instanceof gem.RateLimitError && err.retryAfterMs > 0);
  assert.equal(f.calls.gemini.length, 1);
});

test('all four tools execute against the Beezy API and compact their output', async () => {
  const cockpitRows = [{
    player: 'Aaron Judge', stat: 'hits', line: 1.5, side: 'OVER', recentForm: { line: 1.5, n_games: 2, games: [{ date: '2026-09-01', opp: 'BOS', val: 2, usage: 4, hit: true }], l5_rate: 0.6, l10_rate: 0.7, szn_avg: 1.4 },
    all_books: [{ book: 'dk', over: 110, under: -130, fair: 0.5 }],
  }, { player: 'Someone Else', stat: 'hits' }];
  const f = async (url) => {
    const p = new URL(url).pathname;
    if (p.endsWith('/projections')) return res({ sport: 'mlb', validated: false, status: 'shadow', rows: [{ player: 'Aaron Judge', stat: 'hits', line: 1.5, proj_mean: 1.7, junk_field: 1 }, { player: 'Other' }] });
    if (p.endsWith('/edge/cockpit')) return res({ as_of: 'x', rows: cockpitRows });
    if (p.endsWith('/analytics/deep-dive')) return res({ ev_headline: { graded: 100, flat_roi_pct: 8.2 }, bankroll_curve: [1, 2, 3], edge_buckets: [], book_leaderboard: [] });
    return res({ mlb: { rows: [] } });
  };
  const o = { ...BEEZY, fetchImpl: f };
  const proj = await gem.runTool('get_player_projection', { sport: 'mlb', player_name: 'judge' }, o);
  assert.equal(proj.match_count, 1);
  assert.equal(proj.validated, false);
  assert.equal('junk_field' in proj.rows[0], false);
  const cock = await gem.runTool('get_player_l10_cockpit', { sport: 'mlb', player_name: 'judge' }, o);
  assert.equal(cock.match_count, 1);
  assert.equal(cock.rows[0].recentForm.l10_rate, 0.7);
  const rec = await gem.runTool('get_track_record_stats', { system: 'EV' }, o);
  assert.equal(rec.ev_headline.graded, 100);
  assert.equal('bankroll_curve' in rec, false);
  assert.match(rec.label, /PAPER/);
  const ev = await gem.runTool('get_live_ev_plays', { min_ev: 3 }, o); // 3 means 3 percent
  assert.deepEqual(ev.plays, []);
  await assert.rejects(() => gem.runTool('nope', {}, o), /unknown tool/);
});
