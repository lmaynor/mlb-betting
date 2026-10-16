import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as db from '../src/db.js';

function withDb(fn) {
  return async () => {
    db.initDb(':memory:');
    try {
      await fn();
    } finally {
      db.closeDb();
    }
  };
}

test('settings fall back to defaults and roundtrip JSON values', withDb(() => {
  assert.equal(db.getSetting('1', 'automod_enabled'), false);
  assert.equal(db.getSetting('1', 'antispam_enabled'), true);
  assert.equal(db.getSetting('1', 'no_such_key'), undefined);
  db.setSetting('1', 'banned_words', ['spam', 'scam']);
  db.setSetting('1', 'max_mentions', 4);
  assert.deepEqual(db.getSetting('1', 'banned_words'), ['spam', 'scam']);
  assert.equal(db.getSetting('1', 'max_mentions'), 4);
  // overwrite
  db.setSetting('1', 'max_mentions', 9);
  assert.equal(db.getSetting('1', 'max_mentions'), 9);
  // guilds are isolated
  assert.equal(db.getSetting('2', 'max_mentions'), 0);
  const all = db.getAllSettings('1');
  assert.equal(all.max_mentions, 9);
  assert.equal(all.antispam_enabled, true);
}));

test('snowflake ids beyond 2^53 survive as strings', withDb(() => {
  const big = '1234567890123456789';
  db.setSetting(big, 'k', 1);
  assert.equal(db.getSetting(big, 'k'), 1);
  db.upsertMemberLink(big, { email: 'a@b.co' });
  assert.equal(db.getMemberLink(big).discord_user_id, big);
}));

test('warnings: add, list per user and per guild, clear', withDb(() => {
  const a = db.addWarning('1', '42', '7', 'spamming');
  const b = db.addWarning('1', '42', '7', null);
  db.addWarning('1', '43', '7', 'other user');
  assert.ok(b > a);
  assert.equal(db.getWarnings('1', '42').length, 2);
  assert.equal(db.getWarnings('1', '42')[0].id, b); // newest first
  assert.equal(db.getWarnings('1').length, 3);
  assert.equal(db.getWarnings('2').length, 0);
  assert.equal(db.clearWarnings('1', '42'), 2);
  assert.equal(db.getWarnings('1', '42').length, 0);
  assert.equal(db.getWarnings('1', '43').length, 1);
}));

test('mod logs: add and list newest first, limit honoured', withDb(() => {
  db.addLog('1', 'ban', 'AutoMod', '42', 'reason one');
  db.addLog('1', 'automod', 'AutoMod', null, null);
  db.addLog('2', 'ban', 'AutoMod', '1', 'other guild');
  const logs = db.getLogs('1');
  assert.equal(logs.length, 2);
  assert.equal(logs[0].action, 'automod');
  assert.equal(logs[1].reason, 'reason one');
  assert.equal(db.getLogs('1', 1).length, 1);
}));

test('member_links: upsert/get/getByEmail roundtrip', withDb(() => {
  assert.equal(db.getMemberLink('100000000000000001'), null);
  const created = db.upsertMemberLink('100000000000000001', { email: '  Me@Example.COM ' });
  assert.equal(created.tier, 'public');
  assert.equal(created.email, 'me@example.com');
  assert.equal(created.sport_choice, null);
  assert.ok(created.updated_at > 0);

  const upgraded = db.upsertMemberLink('100000000000000001', {
    tier: 'single_sport', sport_choice: 'nfl', clerk_user_id: 'user_abc',
  });
  assert.equal(upgraded.tier, 'single_sport');
  assert.equal(upgraded.sport_choice, 'nfl');
  assert.equal(upgraded.clerk_user_id, 'user_abc');
  assert.equal(upgraded.email, 'me@example.com'); // untouched field preserved

  assert.equal(db.getMemberByEmail('ME@example.com').discord_user_id, '100000000000000001');
  assert.equal(db.getMemberByEmail('nobody@example.com'), null);
  assert.equal(db.getMemberByEmail(''), null);

  // /link with only an email must not downgrade a synced tier
  const relinked = db.upsertMemberLink('100000000000000001', { email: 'me@example.com' });
  assert.equal(relinked.tier, 'single_sport');

  // null clears
  const cleared = db.upsertMemberLink('100000000000000001', { sport_choice: null, tier: 'public' });
  assert.equal(cleared.sport_choice, null);
  assert.equal(cleared.tier, 'public');
}));

test('getDb throws before initDb', () => {
  db.closeDb();
  assert.throws(() => db.getDb(), /not initialised/);
});
