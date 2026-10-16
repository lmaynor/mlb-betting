// Confirm / Cancel buttons that only the invoking owner can press and that
// expire after 60 seconds. Works on a deferred interaction.
import { ActionRowBuilder, ButtonBuilder, ButtonStyle, ComponentType, MessageFlags } from 'discord.js';
import crypto from 'node:crypto';

export const CONFIRM_TIMEOUT_MS = 60_000;

/**
 * @returns {Promise<'confirmed'|'cancelled'|'expired'>}
 * Presses by anyone other than ownerId get an ephemeral refusal and do not
 * consume the prompt. `now` is injectable for tests.
 */
export async function askConfirm(interaction, {
  content, ownerId = interaction.user.id, timeoutMs = CONFIRM_TIMEOUT_MS, confirmLabel = 'Confirm', now = Date.now,
}) {
  const nonce = crypto.randomBytes(6).toString('hex');
  const yes = `confirm:${nonce}:yes`;
  const no = `confirm:${nonce}:no`;
  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId(yes).setLabel(confirmLabel).setStyle(ButtonStyle.Danger),
    new ButtonBuilder().setCustomId(no).setLabel('Cancel').setStyle(ButtonStyle.Secondary),
  );
  const message = await interaction.editReply({
    content: `${content}\n\nConfirm within ${Math.round(timeoutMs / 1000)}s.`,
    embeds: [],
    components: [row],
  });
  const deadline = now() + timeoutMs;

  for (;;) {
    const remaining = deadline - now();
    let click;
    if (remaining > 0) {
      try {
        click = await message.awaitMessageComponent({
          componentType: ComponentType.Button,
          time: remaining,
          filter: (i) => i.customId === yes || i.customId === no,
        });
      } catch {
        click = null; // timed out
      }
    }
    if (!click) {
      try {
        await interaction.editReply({ content: 'Expired. Nothing was changed.', components: [] });
      } catch { /* interaction token gone */ }
      return 'expired';
    }
    if (click.user.id !== ownerId) {
      try {
        await click.reply({ content: 'Only the owner can use these buttons.', flags: MessageFlags.Ephemeral });
      } catch { /* ignore */ }
      continue;
    }
    if (click.customId === no) {
      await click.update({ content: 'Cancelled. Nothing was changed.', components: [] });
      return 'cancelled';
    }
    await click.update({ content: 'Working...', components: [] });
    return 'confirmed';
  }
}
