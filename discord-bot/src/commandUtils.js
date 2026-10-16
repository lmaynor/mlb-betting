import { MessageFlags, PermissionFlagsBits } from 'discord.js';
import { BeezyApiError } from './beezyApi.js';
import { GeminiError, RateLimitError } from './geminiChat.js';
import { errorEmbed } from './embed.js';
import { isOwner, logAction } from './utils.js';
import { AdminError } from './adminOps.js';
import { askConfirm } from './confirm.js';

export const SPORT_CHOICES = [
  { name: 'MLB', value: 'mlb' },
  { name: 'NFL', value: 'nfl' },
  { name: 'NBA', value: 'nba' },
  { name: 'NHL', value: 'nhl' },
];

/** True when @everyone can view the interaction's channel (a "public" channel). */
export function isPublicChannel(interaction) {
  if (!interaction.inGuild?.() || !interaction.channel || !interaction.guild) return false;
  try {
    const everyone = interaction.guild.roles?.everyone;
    const perms = interaction.channel.permissionsFor?.(everyone);
    return Boolean(perms?.has(PermissionFlagsBits.ViewChannel));
  } catch {
    return false;
  }
}

export const EPHEMERAL = { flags: MessageFlags.Ephemeral };

/** A message that is safe to show a member: never a stack trace, never a secret. */
export function userMessage(err) {
  if (err instanceof BeezyApiError || err instanceof GeminiError || err instanceof RateLimitError) {
    return err.message;
  }
  return 'Something went wrong running that command. Try again in a moment.';
}

/** Log a failure without a stack trace or any request details. */
export function logFailure(name, err) {
  console.error(`[command:${name}] ${err?.name || 'Error'}: ${String(err?.message || err).slice(0, 200)}`);
}

/**
 * Shared owner-only guard for every management command. Defers (ephemeral)
 * first so nothing it says is ever public, then checks
 * interaction.user.id === OWNER_ID (an unset OWNER_ID authorises nobody) and
 * that the command ran inside a guild. Returns true when the command may go on.
 */
export async function ownerGuard(interaction, deps = {}) {
  await interaction.deferReply(EPHEMERAL);
  if (!isOwner(interaction.user.id, deps.ownerId ?? process.env.OWNER_ID)) {
    await interaction.editReply({ embeds: [errorEmbed('Owner-only command.', 'Not allowed')] });
    return false;
  }
  if (!interaction.guild) {
    await interaction.editReply({ embeds: [errorEmbed('Run this inside the server you want to manage.')] });
    return false;
  }
  return true;
}

/** Owner-facing message for a failed management action. Never a stack trace. */
export function adminMessage(err) {
  if (err instanceof AdminError) return err.message;
  const code = err?.code ?? err?.status;
  if (code === 50013 || code === 403) return 'Discord refused: the bot lacks permission (or its role is too low) for that.';
  if (code === 10003 || code === 10011 || code === 10008) return 'That channel, role or message no longer exists.';
  return 'Something went wrong running that command. Try again in a moment.';
}

/** Best-effort audit trail into mod_logs; never fails the command. */
export async function audit(interaction, action, target, detail = null) {
  try {
    await logAction(interaction.guild, action, interaction.user.tag ?? interaction.user.id, target, detail);
  } catch { /* logging must not break an admin action */ }
}

/** Ask for confirmation unless a test injected deps.confirm. */
export function confirmOrInjected(interaction, deps, content, opts = {}) {
  if (deps.confirm) return deps.confirm(interaction, content);
  return askConfirm(interaction, { content, ...opts });
}
