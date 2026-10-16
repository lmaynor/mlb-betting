import { ChannelType, SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import * as ops from '../adminOps.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import {
  adminMessage, audit, confirmOrInjected, logFailure, ownerGuard,
} from '../commandUtils.js';

const categoryOpt = (o) => o.setName('category').setDescription('Category').setRequired(true).addChannelTypes(ChannelType.GuildCategory);

export const data = new SlashCommandBuilder()
  .setName('category')
  .setDescription('Owner only: create, rename and delete categories')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addSubcommand((s) => s.setName('create').setDescription('Create a category')
    .addStringOption((o) => o.setName('name').setDescription('Category name').setRequired(true).setMaxLength(100)))
  .addSubcommand((s) => s.setName('delete').setDescription('Delete an EMPTY category (asks to confirm)')
    .addChannelOption(categoryOpt))
  .addSubcommand((s) => s.setName('rename').setDescription('Rename a category')
    .addChannelOption(categoryOpt)
    .addStringOption((o) => o.setName('name').setDescription('New name').setRequired(true).setMaxLength(100)));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  const guild = interaction.guild;
  const sub = interaction.options.getSubcommand();
  const reason = `beezy /category ${sub} by ${interaction.user.id}`;
  const done = (text, action, target) => audit(interaction, action, target, text)
    .then(() => interaction.editReply({ content: '', embeds: [noticeEmbed(text, 'Done')], components: [] }));
  try {
    switch (sub) {
      case 'create': {
        const cat = await ops.createCategory(guild, interaction.options.getString('name', true), reason);
        return await done(`Created category ${cat.name}.`, 'category_create', cat.name);
      }
      case 'rename': {
        const cat = interaction.options.getChannel('category', true);
        const old = cat.name;
        const name = await ops.renameCategory(cat, interaction.options.getString('name', true), reason);
        return await done(`Renamed ${old} to ${name}.`, 'category_rename', name);
      }
      case 'delete': {
        const cat = interaction.options.getChannel('category', true);
        ops.assertChannelManageable(cat);
        ops.assertCategoryEmpty(guild, cat);
        const verdict = await confirmOrInjected(interaction, deps, `Delete the empty category **${cat.name}**?`);
        if (verdict !== 'confirmed') return undefined;
        ops.assertCategoryEmpty(guild, cat); // re-check after the wait
        const name = cat.name;
        await cat.delete(reason);
        return await done(`Deleted category ${name}.`, 'category_delete', name);
      }
      default:
        throw new ops.AdminError('Unknown subcommand.');
    }
  } catch (err) {
    logFailure(`category ${sub}`, err);
    await interaction.editReply({ content: '', embeds: [errorEmbed(adminMessage(err))], components: [] });
    return undefined;
  }
}
