import { SlashCommandBuilder } from 'discord.js';
import * as db from '../db.js';
import { errorEmbed, noticeEmbed, siteUrl } from '../embed.js';
import { EPHEMERAL, logFailure, userMessage } from '../commandUtils.js';

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

export const data = new SlashCommandBuilder()
  .setName('link')
  .setDescription('Link your beezy.fyi account email so the site can sync your subscription to your roles')
  .addStringOption((o) => o.setName('email').setDescription('Email you use on beezy.fyi').setRequired(true).setMaxLength(254));

export async function execute(interaction) {
  await interaction.deferReply(EPHEMERAL);
  try {
    const email = interaction.options.getString('email', true).trim().toLowerCase();
    if (!EMAIL_RE.test(email)) {
      await interaction.editReply({ embeds: [errorEmbed('That does not look like a valid email address.', 'Invalid email')] });
      return;
    }
    // tier is left undefined: new links start as 'public', and an existing
    // tier already synced by the site is preserved.
    const link = db.upsertMemberLink(interaction.user.id, { email });
    await interaction.editReply({
      embeds: [noticeEmbed(
        `Linked ${email}. Your current tier is **${link.tier}**.\n\n`
        + 'The beezy.fyi site is the source of truth for your tier: your roles update when the site syncs your '
        + `subscription (usually right after checkout). Manage your plan at ${siteUrl()}.`,
        'Account linked',
      )],
    });
  } catch (err) {
    logFailure('link', err);
    await interaction.editReply({ embeds: [errorEmbed(userMessage(err))] });
  }
}
