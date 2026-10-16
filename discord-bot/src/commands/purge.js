import { SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import * as ops from '../adminOps.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import {
  adminMessage, audit, confirmOrInjected, logFailure, ownerGuard,
} from '../commandUtils.js';

export const data = new SlashCommandBuilder()
  .setName('purge')
  .setDescription('Owner only: bulk-delete the most recent messages in this channel')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addIntegerOption((o) => o.setName('count').setDescription('How many messages (1-100)').setRequired(true).setMinValue(1).setMaxValue(100));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  try {
    const count = ops.validateIntRange(interaction.options.getInteger('count', true), 1, 100, 'Count');
    const channel = interaction.channel;
    ops.assertChannelManageable(channel);
    const verdict = await confirmOrInjected(interaction, deps, `Delete the last ${count} message(s) in **#${channel.name}**? Messages older than 14 days are skipped.`);
    if (verdict !== 'confirmed') return;
    const deleted = await ops.purgeMessages(channel, count);
    await audit(interaction, 'purge', channel.name, `${deleted} message(s)`);
    await interaction.editReply({ content: '', embeds: [noticeEmbed(`Deleted ${deleted} message(s) from #${channel.name}.`, 'Done')], components: [] });
  } catch (err) {
    logFailure('purge', err);
    await interaction.editReply({ content: '', embeds: [errorEmbed(adminMessage(err))], components: [] });
  }
}
