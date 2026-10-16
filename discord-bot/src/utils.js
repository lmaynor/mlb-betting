import { EmbedBuilder } from 'discord.js';
import * as db from './db.js';

/** True if userId is the configured bot owner. ownerId is injectable for
 * tests; real callers omit it and the OWNER_ID env var is read. An unset
 * OWNER_ID never matches anyone. */
export function isOwner(userId, ownerId = process.env.OWNER_ID) {
  return Boolean(ownerId) && String(userId) === String(ownerId);
}

const ACTION_COLORS = {
  kick: 0xF0B232,
  ban: 0xDA373C,
  automod: 0xF0B232,
  antispam_ban: 0xDA373C,
  antispam_flag: 0xF0B232,
  mention_fanout_ban: 0xDA373C,
  mention_fanout_flag: 0xF0B232,
};

/** Record a moderation action to the DB and, when a log channel is set,
 * post an embed there. */
export async function logAction(guild, action, actor, target = null, reason = null) {
  db.addLog(guild.id, action, String(actor), target !== null ? String(target) : null, reason);
  const channelId = db.getSetting(guild.id, 'log_channel');
  if (!channelId) return;
  const channel = guild.channels?.cache?.get(String(channelId));
  if (!channel) return;
  const embed = new EmbedBuilder()
    .setTitle(action.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()))
    .setColor(ACTION_COLORS[action] ?? 0x5865F2)
    .setTimestamp()
    .addFields({ name: 'Actor', value: String(actor) });
  if (target !== null) embed.addFields({ name: 'Target', value: String(target) });
  if (reason) embed.addFields({ name: 'Reason', value: String(reason).slice(0, 1000) });
  try {
    await channel.send({ embeds: [embed] });
  } catch (err) {
    console.warn('[utils] logAction post failed:', err.message);
  }
}
