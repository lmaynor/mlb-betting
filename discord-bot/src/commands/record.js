import { SlashCommandBuilder } from 'discord.js';
import { fetchAnalytics, SPORTS } from '../beezyApi.js';
import { errorEmbed, recordButtons, recordEmbed } from '../embed.js';
import { logFailure, userMessage } from '../commandUtils.js';

export const data = new SlashCommandBuilder()
  .setName('record')
  .setDescription('Live verified PAPER track record: graded count, flat ROI, 1/4-Kelly ROI');

export async function execute(interaction, deps = {}) {
  await interaction.deferReply();
  try {
    const main = await fetchAnalytics({ system: 'EV' }, deps.beezy);
    // Per-sport breakdown: ask for each non-default sport; show only the ones that return graded rows.
    const others = SPORTS.filter((s) => s !== 'mlb');
    const settled = await Promise.allSettled(others.map((sport) => fetchAnalytics({ system: 'EV', sport }, deps.beezy)));
    const perSport = [];
    if (main?.ev_headline) perSport.push({ sport: 'mlb', headline: main.ev_headline });
    settled.forEach((res, i) => {
      if (res.status === 'fulfilled' && res.value?.ev_headline?.graded > 0) {
        perSport.push({ sport: others[i], headline: res.value.ev_headline });
      }
    });
    await interaction.editReply({
      embeds: [recordEmbed(main?.ev_headline ?? null, perSport.length > 1 ? perSport : [], { system: 'EV' })],
      components: [recordButtons()],
    });
  } catch (err) {
    logFailure('record', err);
    await interaction.editReply({ embeds: [errorEmbed(userMessage(err))] });
  }
}
