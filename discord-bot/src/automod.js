// Automatic message moderation: banned words, invite links, mention spam, and
// the mention fan-out ban (a tout/raid bot pinging many distinct members in a
// short window). Ported from the nodebot reference; the hyperdimensional
// sentinel code path was removed entirely.
import { PermissionFlagsBits } from 'discord.js';
import { logAction } from './utils.js';
import * as db from './db.js';

export const INVITE_RE = /(discord\.gg\/|discord(?:app)?\.com\/invite\/)/i;

const DEFAULT_FANOUT_THRESHOLD = 5;
const DEFAULT_FANOUT_WINDOW_SECONDS = 30;
const DEFAULT_FANOUT_DELETE_SECONDS = 3600;

// guildId:userId -> Map<targetUserId, timestamp>
const fanoutTrackers = new Map();
// guildId:userId currently mid-ban, so a burst of messages arriving while
// the ban() call is in flight cannot trigger a second ban attempt.
const fanoutBanning = new Set();

/**
 * Record the distinct members one author has @mentioned and report whether
 * they just crossed the fan-out threshold. Pure state-tracking, no Discord
 * calls, so it is cheap to unit test.
 *
 * @returns {{triggered: boolean, targetCount: number}|null} null if there
 *   are no mention targets to track.
 */
export function recordMentionFanoutAndCheck(guildId, userId, targetIds, {
  threshold = DEFAULT_FANOUT_THRESHOLD,
  windowSeconds = DEFAULT_FANOUT_WINDOW_SECONDS,
} = {}, nowMs = Date.now()) {
  if (!targetIds || targetIds.length === 0) return null;

  const trackerKey = `${guildId}:${userId}`;
  const windowMs = windowSeconds * 1000;
  let targets = fanoutTrackers.get(trackerKey);
  if (!targets) targets = new Map();
  for (const [id, ts] of targets) {
    if (nowMs - ts > windowMs) targets.delete(id);
  }
  for (const id of targetIds) targets.set(id, nowMs);

  if (targets.size >= threshold) {
    fanoutTrackers.delete(trackerKey); // one trigger per burst, not one per message
    return { triggered: true, targetCount: targets.size };
  }
  fanoutTrackers.set(trackerKey, targets);
  return { triggered: false, targetCount: targets.size };
}

/**
 * Returns true if the message was actioned (banned or flagged) as mention
 * fan-out, in which case the caller should stop - nothing left to check.
 */
export async function checkMentionFanout(message) {
  if (!db.getSetting(message.guild.id, 'mention_fanout_enabled')) return false;
  if (!message.mentions?.users?.size) return false;

  const targetIds = [...message.mentions.users.keys()].filter((id) => id !== message.author.id);
  if (targetIds.length === 0) return false;

  const guildId = message.guild.id;
  const threshold = Number(db.getSetting(guildId, 'mention_fanout_threshold')) || DEFAULT_FANOUT_THRESHOLD;
  const windowSeconds = Number(db.getSetting(guildId, 'mention_fanout_window_seconds')) || DEFAULT_FANOUT_WINDOW_SECONDS;

  const result = recordMentionFanoutAndCheck(guildId, message.author.id, targetIds, { threshold, windowSeconds });
  if (!result?.triggered) return false;

  try {
    await message.delete();
  } catch (err) {
    console.warn('[automod] fan-out message delete failed:', err.message);
  }

  if (!message.member) return true; // already gone - nothing left to ban

  const banKey = `${guildId}:${message.author.id}`;
  if (fanoutBanning.has(banKey)) return true;
  fanoutBanning.add(banKey);

  try {
    const reason = `Mention fan-out: pinged ${result.targetCount} distinct members within `
      + `${windowSeconds}s (going through the member list - a scrape/raid pattern)`;
    if (!message.member.bannable) {
      console.warn(`[automod] flagged ${message.author.tag} for mention fan-out but lack permission to ban them`);
      await logAction(message.guild, 'mention_fanout_flag', 'AutoMod', message.author,
        `${reason} - ban failed, insufficient permissions`);
      return true;
    }
    const deleteSeconds = Number(db.getSetting(guildId, 'mention_fanout_delete_seconds')) || DEFAULT_FANOUT_DELETE_SECONDS;
    await message.member.ban({ reason, deleteMessageSeconds: deleteSeconds });
    await logAction(message.guild, 'mention_fanout_ban', 'AutoMod', message.author, reason);
  } catch (err) {
    console.warn('[automod] fan-out ban failed:', err.message);
  } finally {
    fanoutBanning.delete(banKey);
  }
  return true;
}

/** Returns a violation description string, or null if the message is clean. */
export function findViolation(guild, member, content, mentionCount) {
  if (!db.getSetting(guild.id, 'automod_enabled')) return null;

  const lower = String(content || '').toLowerCase();
  const bannedWords = db.getSetting(guild.id, 'banned_words') || [];
  for (const word of bannedWords) {
    if (word && lower.includes(word.toLowerCase())) return `banned word: ${word}`;
  }

  if (db.getSetting(guild.id, 'block_invites') && INVITE_RE.test(content)) return 'invite link';

  const maxMentions = Number(db.getSetting(guild.id, 'max_mentions')) || 0;
  if (maxMentions && mentionCount > maxMentions) return `mention spam (${mentionCount} mentions)`;

  return null;
}

export async function checkMessage(message) {
  if (!message.guild || message.author.bot) return;
  if (message.member?.permissions.has(PermissionFlagsBits.ManageMessages)) return;

  if (await checkMentionFanout(message)) return;

  const violation = findViolation(
    message.guild, message.member, message.content, message.mentions?.users?.size ?? 0,
  );
  if (!violation) return;

  try {
    await message.delete();
  } catch (err) {
    console.warn('[automod] delete failed:', err.message);
    return;
  }
  await logAction(message.guild, 'automod', 'AutoMod', message.author, violation);
  try {
    const warning = await message.channel.send({
      content: `${message.author}, your message was removed (${violation}).`,
      allowedMentions: { users: [message.author.id] },
    });
    setTimeout(() => warning.delete().catch(() => {}), 6000).unref();
  } catch (err) {
    console.warn('[automod] warning post failed:', err.message);
  }
}

/** Test seam: drop all in-process state. */
export function _resetForTests() {
  fanoutTrackers.clear();
  fanoutBanning.clear();
}
