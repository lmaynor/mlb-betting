import { SlashCommandBuilder } from 'discord.js';
import { fetchEvFeed, normalizeEvFeed } from '../beezyApi.js';
import { errorEmbed, evButtons, evEmbed } from '../embed.js';
import { SPORT_CHOICES, logFailure, userMessage } from '../commandUtils.js';

const MIN_EV = 0.02;
const TOP_N = 6;

export const data = new SlashCommandBuilder()
  .setName('ev')
  .setDescription('Top live +EV dislocations vs the de-vigged consensus')
  .addStringOption((o) => o.setName('sport').setDescription('Sport filter (default: all)').addChoices(...SPORT_CHOICES))
  .addStringOption((o) => o.setName('book').setDescription('Only this book or exchange (name contains)').setMaxLength(40));

export async function execute(interaction, deps = {}) {
  await interaction.deferReply();
  try {
    const sport = interaction.options.getString('sport') ?? 'all';
    const book = interaction.options.getString('book');
    const feed = await fetchEvFeed({ sport, minEv: MIN_EV, limit: 100 }, deps.beezy);
    const plays = normalizeEvFeed(feed, { book, sport, minEv: MIN_EV })
      .filter((p) => p.flag !== false)
      .slice(0, TOP_N);
    await interaction.editReply({
      embeds: [evEmbed(plays, { sport, book, asOf: feed?.as_of ?? null })],
      components: [evButtons()],
    });
  } catch (err) {
    logFailure('ev', err);
    await interaction.editReply({ embeds: [errorEmbed(userMessage(err))], components: [] });
  }
}
