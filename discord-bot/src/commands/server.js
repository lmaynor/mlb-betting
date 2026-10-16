import { SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import { snapshotGuild } from '../cleanup.js';
import { buildStatus, formatStatus } from '../serverStatus.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import { adminMessage, logFailure, ownerGuard } from '../commandUtils.js';

export const data = new SlashCommandBuilder()
  .setName('server')
  .setDescription('Owner only: server layout status')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addSubcommand((s) => s.setName('status').setDescription('Counts, missing layout items, legacy leftovers, role hierarchy'));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  try {
    // Read-only. Member counts are only needed for legacy roles, so fetch them.
    const snapshot = await snapshotGuild(interaction.guild, { withMembers: true });
    await interaction.editReply({ embeds: [noticeEmbed(formatStatus(buildStatus(snapshot)), 'Server status')] });
  } catch (err) {
    logFailure('server status', err);
    await interaction.editReply({ embeds: [errorEmbed(adminMessage(err))] });
  }
}
