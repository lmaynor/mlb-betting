import { SlashCommandBuilder } from 'discord.js';
import { fetchProjections, matchesPlayer } from '../beezyApi.js';
import { errorEmbed, projEmbed } from '../embed.js';
import { SPORT_CHOICES, logFailure, userMessage } from '../commandUtils.js';

const TOP_N = 8;

export const data = new SlashCommandBuilder()
  .setName('proj')
  .setDescription("Today's model projections: win prob, L5/L10 hit rates, Beezy Score")
  .addStringOption((o) => o.setName('sport').setDescription('Sport').setRequired(true).addChoices(...SPORT_CHOICES))
  .addStringOption((o) => o.setName('player').setDescription('Player name (partial ok)').setMaxLength(60))
  .addStringOption((o) => o.setName('market').setDescription('Market / stat (e.g. strikeouts, hits)').setMaxLength(40));

function score(row) {
  const n = Number(row?.beezy_score);
  return Number.isFinite(n) ? n : -1;
}

export async function execute(interaction, deps = {}) {
  await interaction.deferReply();
  try {
    const sport = interaction.options.getString('sport', true);
    const player = interaction.options.getString('player');
    const market = interaction.options.getString('market');
    const data = await fetchProjections({ sport, market, limit: 200 }, deps.beezy);
    const rows = data.rows
      .filter((r) => (player ? matchesPlayer(r?.player, player) : true))
      .sort((a, b) => score(b) - score(a))
      .slice(0, TOP_N);
    await interaction.editReply({ embeds: [projEmbed(data, rows, { sport, player, market })] });
  } catch (err) {
    logFailure('proj', err);
    await interaction.editReply({ embeds: [errorEmbed(userMessage(err))] });
  }
}
