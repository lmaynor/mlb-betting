// @mention -> Gemini answer. Rate limited per user via geminiChat's limiter.
import { askBeezy, GeminiError, RateLimitError } from './geminiChat.js';

/** Strip the bot mention(s) from the content and return the question text. */
export function extractQuestion(content, botId) {
  return String(content || '')
    .replaceAll(`<@${botId}>`, ' ')
    .replaceAll(`<@!${botId}>`, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** @returns {Promise<boolean>} true when the message was handled as a question */
export async function handleMention(message, client, deps = {}) {
  if (!message.guild || message.author?.bot) return false;
  const botId = client.user?.id;
  if (!botId || !message.mentions?.users?.has(botId)) return false;
  // Ignore @everyone/@here and role pings that merely include the bot.
  const question = extractQuestion(message.content, botId);
  if (!question) {
    await message.reply({
      content: 'Ask me about live +EV plays, a player projection, or the track record. Try /ev or /edge.',
      allowedMentions: { parse: [], repliedUser: false },
    });
    return true;
  }
  try {
    await message.channel.sendTyping?.();
    const answer = await askBeezy(question, { userId: message.author.id, ...deps });
    await message.reply({ content: answer, allowedMentions: { parse: [], repliedUser: false } });
  } catch (err) {
    const text = err instanceof RateLimitError || err instanceof GeminiError
      ? err.message
      : 'I could not answer that right now.';
    console.warn(`[mention] ${err?.name || 'Error'}: ${String(err?.message || err).slice(0, 160)}`);
    try {
      await message.reply({ content: text, allowedMentions: { parse: [], repliedUser: false } });
    } catch { /* channel may be gone */ }
  }
  return true;
}
