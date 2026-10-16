import { SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import {
  applyChannelPlan, applyRolePlan, formatChannelPlan, formatResults, formatRolePlan,
  planChannelCleanup, planRoleCleanup, snapshotGuild,
} from '../cleanup.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import {
  adminMessage, audit, confirmOrInjected, logFailure, ownerGuard,
} from '../commandUtils.js';

const forceOpt = (o) => o.setName('force').setDescription('Also remove legacy roles that still have members');

export const data = new SlashCommandBuilder()
  .setName('cleanup')
  .setDescription('Owner only: archive legacy channels and remove legacy roles')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addSubcommand((s) => s.setName('plan').setDescription('Dry run: what apply would do to legacy channels/categories'))
  .addSubcommand((s) => s.setName('apply').setDescription('Archive legacy channels, delete empty legacy categories (asks to confirm)'))
  .addSubcommand((s) => s.setName('roles-plan').setDescription('Dry run: legacy roles and their member counts').addBooleanOption(forceOpt))
  .addSubcommand((s) => s.setName('roles-apply').setDescription('Remove legacy roles (asks to confirm)').addBooleanOption(forceOpt));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  const guild = interaction.guild;
  const sub = interaction.options.getSubcommand();
  const force = interaction.options.getBoolean('force') ?? false;
  const reason = `beezy /cleanup ${sub} by ${interaction.user.id}`;
  try {
    const snapshot = await snapshotGuild(guild);

    if (sub === 'plan' || sub === 'apply') {
      const plan = planChannelCleanup(snapshot);
      const text = formatChannelPlan(plan);
      const actionable = plan.archiveChannels.length + plan.deleteCategories.length;
      if (sub === 'plan' || !actionable) {
        await interaction.editReply({ embeds: [noticeEmbed(text, sub === 'plan' ? 'Cleanup plan (dry run)' : 'Cleanup')] });
        return;
      }
      const verdict = await confirmOrInjected(interaction, deps, `Apply this cleanup?\n\n${text}`.slice(0, 1800));
      if (verdict !== 'confirmed') return;
      // Re-plan from a fresh snapshot so a stale plan is never applied.
      const fresh = planChannelCleanup(await snapshotGuild(guild));
      const results = await applyChannelPlan(guild, fresh, {
        ownerId: interaction.user.id, botUserId: interaction.client?.user?.id, reason,
      });
      await audit(interaction, 'cleanup_apply', 'channels', `${results.filter((r) => r.ok).length}/${results.length} ok`);
      await interaction.editReply({ content: '', components: [], embeds: [noticeEmbed(formatResults(results), 'Cleanup applied')] });
      return;
    }

    // roles-plan / roles-apply
    const candidates = planRoleCleanup(snapshot, { force });
    const text = formatRolePlan(candidates);
    const actionable = candidates.filter((c) => c.action === 'remove');
    if (sub === 'roles-plan' || !actionable.length) {
      await interaction.editReply({ embeds: [noticeEmbed(text, sub === 'roles-plan' ? 'Role cleanup plan (dry run)' : 'Role cleanup')] });
      return;
    }
    const withMembers = actionable.filter((c) => c.memberCount > 0);
    const warn = withMembers.length
      ? `\n\nWARNING: force is on; ${withMembers.length} role(s) still have members and they will lose them.`
      : '';
    const verdict = await confirmOrInjected(interaction, deps, `Remove these roles?\n\n${text}${warn}`.slice(0, 1800));
    if (verdict !== 'confirmed') return;
    const fresh = planRoleCleanup(await snapshotGuild(guild), { force });
    const results = await applyRolePlan(guild, fresh, { reason });
    await audit(interaction, 'cleanup_roles', 'roles', `${results.filter((r) => r.ok).length}/${results.length} ok`);
    await interaction.editReply({ content: '', components: [], embeds: [noticeEmbed(formatResults(results), 'Role cleanup applied')] });
  } catch (err) {
    logFailure(`cleanup ${sub}`, err);
    await interaction.editReply({ content: '', components: [], embeds: [errorEmbed(adminMessage(err))] });
  }
}
