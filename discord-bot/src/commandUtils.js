import { MessageFlags, PermissionFlagsBits } from 'discord.js';
import { BeezyApiError } from './beezyApi.js';
import { GeminiError, RateLimitError } from './geminiChat.js';

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
