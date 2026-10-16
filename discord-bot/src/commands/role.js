import { SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import * as ops from '../adminOps.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import {
  adminMessage, audit, confirmOrInjected, logFailure, ownerGuard,
} from '../commandUtils.js';

const roleOpt = (o) => o.setName('role').setDescription('Role').setRequired(true);
const userOpt = (o) => o.setName('user').setDescription('Member').setRequired(true);

export const data = new SlashCommandBuilder()
  .setName('role')
  .setDescription('Owner only: create, edit, delete and assign roles')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addSubcommand((s) => s.setName('create').setDescription('Create a role')
    .addStringOption((o) => o.setName('name').setDescription('Role name').setRequired(true).setMaxLength(100))
    .addStringOption((o) => o.setName('color').setDescription('Hex color, e.g. #5865F2').setMaxLength(7)))
  .addSubcommand((s) => s.setName('delete').setDescription('Delete a role (asks to confirm)').addRoleOption(roleOpt))
  .addSubcommand((s) => s.setName('rename').setDescription('Rename a role')
    .addRoleOption(roleOpt)
    .addStringOption((o) => o.setName('name').setDescription('New name').setRequired(true).setMaxLength(100)))
  .addSubcommand((s) => s.setName('color').setDescription('Change a role color')
    .addRoleOption(roleOpt)
    .addStringOption((o) => o.setName('color').setDescription('Hex color, e.g. #5865F2').setRequired(true).setMaxLength(7)))
  .addSubcommand((s) => s.setName('give').setDescription('Give a role to a member')
    .addRoleOption(roleOpt).addUserOption(userOpt))
  .addSubcommand((s) => s.setName('take').setDescription('Take a role from a member')
    .addRoleOption(roleOpt).addUserOption(userOpt));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  const guild = interaction.guild;
  const sub = interaction.options.getSubcommand();
  const reason = `beezy /role ${sub} by ${interaction.user.id}`;
  const done = (text, action, target) => audit(interaction, action, target, text)
    .then(() => interaction.editReply({ content: '', embeds: [noticeEmbed(text, 'Done')], components: [] }));
  try {
    if (sub === 'create') {
      const role = await ops.createRole(guild, {
        name: interaction.options.getString('name', true),
        color: interaction.options.getString('color'),
      }, reason);
      return await done(`Created role ${role.name} (id ${role.id}).`, 'role_create', role.name);
    }
    const role = interaction.options.getRole('role', true);
    switch (sub) {
      case 'delete': {
        ops.assertRoleManageable(guild, role);
        const verdict = await confirmOrInjected(interaction, deps, `Delete role **${role.name}** (${role.members?.size ?? '?'} member(s))? This cannot be undone.`);
        if (verdict !== 'confirmed') return undefined;
        const name = role.name;
        await role.delete(reason);
        return await done(`Deleted role ${name}.`, 'role_delete', name);
      }
      case 'rename': {
        const old = role.name;
        const name = await ops.renameRole(guild, role, interaction.options.getString('name', true), reason);
        return await done(`Renamed role ${old} to ${name}.`, 'role_rename', name);
      }
      case 'color':
        await ops.recolorRole(guild, role, interaction.options.getString('color', true), reason);
        return await done(`Recolored ${role.name}.`, 'role_color', role.name);
      case 'give':
      case 'take': {
        const member = interaction.options.getMember('user');
        if (!member) throw new ops.AdminError('That user is not in this server.');
        await (sub === 'give' ? ops.giveRole : ops.takeRole)(guild, member, role, reason);
        return await done(`${sub === 'give' ? 'Gave' : 'Took'} ${role.name} ${sub === 'give' ? 'to' : 'from'} ${member.user?.tag ?? member.id}.`, `role_${sub}`, role.name);
      }
      default:
        throw new ops.AdminError('Unknown subcommand.');
    }
  } catch (err) {
    logFailure(`role ${sub}`, err);
    await interaction.editReply({ content: '', embeds: [errorEmbed(adminMessage(err))], components: [] });
    return undefined;
  }
}
