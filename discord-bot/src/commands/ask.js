import { SlashCommandBuilder } from 'discord.js';
import { askBeezy } from '../geminiChat.js';
import { errorEmbed } from '../embed.js';
import { logFailure, userMessage } from '../commandUtils.js';

export const data = new SlashCommandBuilder()
  .setName('ask')
  .setDescription('Ask the Beezy analyst about live +EV plays, projections or the track record')
  .addStringOption((o) => o.setName('question').setDescription('Your question').setRequired(true).setMaxLength(500));

export async function execute(interaction, deps = {}) {
  await interaction.deferReply();
  try {
    const question = interaction.options.getString('question', true);
    const answer = await askBeezy(question, { userId: interaction.user.id, ...deps.ask });
    // LLM output must never be able to ping @everyone or roles.
    await interaction.editReply({ content: answer, allowedMentions: { parse: [] } });
  } catch (err) {
    logFailure('ask', err);
    await interaction.editReply({ content: '', embeds: [errorEmbed(userMessage(err), 'Could not answer')] });
  }
}
