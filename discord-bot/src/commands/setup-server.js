import { PermissionFlagsBits, SlashCommandBuilder, InteractionContextType } from 'discord.js';
import { buildGcloudCommands, provisionBeezyServer } from '../serverSetup.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import { EPHEMERAL, logFailure } from '../commandUtils.js';
import { isOwner } from '../utils.js';

export const data = new SlashCommandBuilder()
  .setName('setup-server')
  .setDescription('Owner only: create/update the Beezy roles, channels, permissions and webhooks')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild);

export async function execute(interaction, deps = {}) {
  // Everything here is ephemeral: the reply contains webhook URLs (secrets).
  await interaction.deferReply(EPHEMERAL);
  if (!isOwner(interaction.user.id, deps.ownerId ?? process.env.OWNER_ID)) {
    await interaction.editReply({ embeds: [errorEmbed('Owner-only command.', 'Not allowed')] });
    return;
  }
  if (!interaction.guild) {
    await interaction.editReply({ embeds: [errorEmbed('Run this inside the server you want to set up.')] });
    return;
  }
  try {
    const provision = deps.provision ?? provisionBeezyServer;
    const result = await provision(interaction.guild, {
      ownerId: interaction.user.id,
      botUserId: interaction.client?.user?.id,
      reason: `Beezy setup by ${interaction.user.tag ?? interaction.user.id}`,
    });
    const count = (arr, status) => arr.filter((x) => x.status === status).length;
    const summary = [
      `Roles: ${count(result.roles, 'created')} created, ${count(result.roles, 'updated')} updated, ${count(result.roles, 'existing')} unchanged`,
      `Categories: ${count(result.categories, 'created')} created, ${count(result.categories, 'existing')} existing`,
      `Channels: ${count(result.channels, 'created')} created, ${count(result.channels, 'existing')} existing`,
      `Webhooks: ${count(result.webhooks, 'created')} created, ${count(result.webhooks, 'existing')} reused`,
      ...result.warnings.map((w) => `Warning: ${w}`),
    ].join('\n');
    await interaction.editReply({ embeds: [noticeEmbed(summary, 'Server setup complete')] });

    const { blocks, skipped } = buildGcloudCommands(result.webhooks);
    if (skipped.length) {
      await interaction.followUp({ ...EPHEMERAL, content: `Skipped webhooks with an unexpected URL shape: ${skipped.join(', ')}` });
    }
    if (blocks.length) {
      await interaction.followUp({
        ...EPHEMERAL,
        content: 'Webhook URLs are secrets. Run these in your own shell; this message is visible only to you.',
      });
      for (const block of blocks) {
        // eslint-disable-next-line no-await-in-loop
        await interaction.followUp({ ...EPHEMERAL, content: `\`\`\`bash\n${block}\n\`\`\`` });
      }
    }
  } catch (err) {
    logFailure('setup-server', err);
    await interaction.editReply({
      embeds: [errorEmbed('Setup failed. Check that the bot has Manage Roles, Manage Channels and Manage Webhooks, and that its role sits above the Beezy roles.')],
    });
  }
}
