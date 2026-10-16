import { ChannelType, SlashCommandBuilder, InteractionContextType, PermissionFlagsBits } from 'discord.js';
import * as ops from '../adminOps.js';
import { errorEmbed, noticeEmbed } from '../embed.js';
import {
  adminMessage, audit, confirmOrInjected, logFailure, ownerGuard,
} from '../commandUtils.js';

const TEXTISH = [ChannelType.GuildText, ChannelType.GuildAnnouncement];
const ANY_CHANNEL = [...TEXTISH, ChannelType.GuildVoice, ChannelType.GuildStageVoice, ChannelType.GuildForum];

const channelOpt = (types) => (o) => o.setName('channel').setDescription('Target channel').setRequired(true).addChannelTypes(...types);
const categoryOpt = (required) => (o) => o.setName('category').setDescription('Category').setRequired(required).addChannelTypes(ChannelType.GuildCategory);

export const data = new SlashCommandBuilder()
  .setName('channel')
  .setDescription('Owner only: create, edit and delete channels')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .setContexts(InteractionContextType.Guild)
  .addSubcommand((s) => s.setName('create').setDescription('Create a text channel')
    .addStringOption((o) => o.setName('name').setDescription('Channel name').setRequired(true).setMaxLength(100))
    .addChannelOption(categoryOpt(false))
    .addStringOption((o) => o.setName('topic').setDescription('Topic').setMaxLength(1024))
    .addBooleanOption((o) => o.setName('private').setDescription('Hide from @everyone')))
  .addSubcommand((s) => s.setName('delete').setDescription('Delete a channel (asks to confirm)')
    .addChannelOption(channelOpt(ANY_CHANNEL)))
  .addSubcommand((s) => s.setName('rename').setDescription('Rename a channel')
    .addChannelOption(channelOpt(ANY_CHANNEL))
    .addStringOption((o) => o.setName('name').setDescription('New name').setRequired(true).setMaxLength(100)))
  .addSubcommand((s) => s.setName('topic').setDescription('Set a channel topic')
    .addChannelOption(channelOpt(TEXTISH))
    .addStringOption((o) => o.setName('topic').setDescription('New topic').setRequired(true).setMaxLength(1024)))
  .addSubcommand((s) => s.setName('move').setDescription('Move a channel to a category')
    .addChannelOption(channelOpt(ANY_CHANNEL))
    .addChannelOption(categoryOpt(true)))
  .addSubcommand((s) => s.setName('lock').setDescription('Deny @everyone from sending in a channel')
    .addChannelOption(channelOpt(TEXTISH)))
  .addSubcommand((s) => s.setName('unlock').setDescription('Remove the @everyone send denial')
    .addChannelOption(channelOpt(TEXTISH)))
  .addSubcommand((s) => s.setName('slowmode').setDescription('Set slowmode seconds (0 turns it off)')
    .addChannelOption(channelOpt(TEXTISH))
    .addIntegerOption((o) => o.setName('seconds').setDescription('0-21600').setRequired(true).setMinValue(0).setMaxValue(21600)))
  .addSubcommand((s) => s.setName('access').setDescription('Set one role\'s access to a channel')
    .addChannelOption(channelOpt(ANY_CHANNEL))
    .addRoleOption((o) => o.setName('role').setDescription('Role').setRequired(true))
    .addStringOption((o) => o.setName('mode').setDescription('Access mode').setRequired(true)
      .addChoices(
        { name: 'view', value: 'view' },
        { name: 'hide', value: 'hide' },
        { name: 'read-only', value: 'read-only' },
        { name: 'reset', value: 'reset' },
      )));

export async function execute(interaction, deps = {}) {
  if (!(await ownerGuard(interaction, deps))) return;
  const guild = interaction.guild;
  const sub = interaction.options.getSubcommand();
  const reason = `beezy /channel ${sub} by ${interaction.user.id}`;
  const done = (text, action, target) => audit(interaction, action, target, text)
    .then(() => interaction.editReply({ content: '', embeds: [noticeEmbed(text, 'Done')], components: [] }));
  try {
    const channel = sub === 'create' ? null : interaction.options.getChannel('channel', true);
    switch (sub) {
      case 'create': {
        const created = await ops.createChannel(guild, {
          name: interaction.options.getString('name', true),
          category: interaction.options.getChannel('category'),
          topic: interaction.options.getString('topic'),
          privateChannel: interaction.options.getBoolean('private') ?? false,
        }, reason);
        return await done(`Created #${created.name} (id ${created.id}).`, 'channel_create', created.name);
      }
      case 'delete': {
        ops.assertChannelManageable(channel);
        const verdict = await confirmOrInjected(interaction, deps, `Delete channel **#${channel.name}** and its history? This cannot be undone.`);
        if (verdict !== 'confirmed') return undefined;
        const name = channel.name;
        await channel.delete(reason);
        return await done(`Deleted #${name}.`, 'channel_delete', name);
      }
      case 'rename': {
        const old = channel.name;
        const name = await ops.renameChannel(channel, interaction.options.getString('name', true), reason);
        return await done(`Renamed #${old} to #${name}.`, 'channel_rename', name);
      }
      case 'topic':
        await ops.setChannelTopic(channel, interaction.options.getString('topic', true), reason);
        return await done(`Updated the topic of #${channel.name}.`, 'channel_topic', channel.name);
      case 'move': {
        const category = interaction.options.getChannel('category', true);
        await ops.moveChannel(channel, category, reason);
        return await done(`Moved #${channel.name} into ${category.name}.`, 'channel_move', channel.name);
      }
      case 'lock':
      case 'unlock':
        await ops.lockChannel(guild, channel, sub === 'lock', reason);
        return await done(`${sub === 'lock' ? 'Locked' : 'Unlocked'} #${channel.name} for @everyone.`, `channel_${sub}`, channel.name);
      case 'slowmode': {
        const s = await ops.setSlowmode(channel, interaction.options.getInteger('seconds', true), reason);
        return await done(`Slowmode on #${channel.name} is now ${s}s.`, 'channel_slowmode', channel.name);
      }
      case 'access': {
        const role = interaction.options.getRole('role', true);
        const mode = interaction.options.getString('mode', true);
        await ops.setChannelAccess(guild, channel, role, mode, reason);
        return await done(`#${channel.name}: ${role.name} set to ${mode}.`, 'channel_access', channel.name);
      }
      default:
        throw new ops.AdminError('Unknown subcommand.');
    }
  } catch (err) {
    logFailure(`channel ${sub}`, err);
    await interaction.editReply({ content: '', embeds: [errorEmbed(adminMessage(err))], components: [] });
    return undefined;
  }
}
