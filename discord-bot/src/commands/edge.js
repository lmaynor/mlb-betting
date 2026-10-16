import { SlashCommandBuilder } from 'discord.js';
import { fetchCockpit } from '../beezyApi.js';
import { edgeEmbed, errorEmbed, evButtons, noticeEmbed } from '../embed.js';
import {
  EPHEMERAL, SPORT_CHOICES, isPublicChannel, logFailure, userMessage,
} from '../commandUtils.js';

export const data = new SlashCommandBuilder()
  .setName('edge')
  .setDescription('Player cockpit: L10 game log vs the line, usage trend, multi-book odds')
  .addStringOption((o) => o.setName('player').setDescription('Player name (partial ok)').setRequired(true).setMaxLength(60))
  .addStringOption((o) => o.setName('sport').setDescription('Sport (default: all)').addChoices(...SPORT_CHOICES));

export async function execute(interaction, deps = {}) {
  // Ephemeral in public channels so the cockpit does not flood them.
  await interaction.deferReply(isPublicChannel(interaction) ? EPHEMERAL : {});
  try {
    const player = interaction.options.getString('player', true);
    const sport = interaction.options.getString('sport') ?? 'all';
    const data = await fetchCockpit({ sport, player }, deps.beezy);
    if (!data.rows.length) {
      await interaction.editReply({ embeds: [noticeEmbed(`No cockpit rows for "${player}" right now. Try a fuller name or another sport.`, 'No match')] });
      return;
    }
    // Prefer rows that carry a game log, then the highest EV.
    const ranked = [...data.rows].sort((a, b) => {
      const form = Number(Boolean(b.recentForm?.games?.length)) - Number(Boolean(a.recentForm?.games?.length));
      if (form) return form;
      return (Number(b.ev) || -Infinity) - (Number(a.ev) || -Infinity);
    });
    const embeds = ranked.slice(0, 2).map(edgeEmbed);
    await interaction.editReply({ embeds, components: [evButtons()] });
  } catch (err) {
    logFailure('edge', err);
    await interaction.editReply({ embeds: [errorEmbed(userMessage(err))] });
  }
}
