// Welcome flow: assign the Explorer role if it exists and post a welcome
// embed to #welcome. Adapted from the nodebot reference welcome.js, with the
// per-guild template/autorole settings replaced by the fixed Beezy layout.
import { welcomeEmbed } from './embed.js';

export const EXPLORER_ROLE = 'Explorer';
export const WELCOME_CHANNEL = 'welcome';

function findByName(collection, name) {
  const wanted = name.toLowerCase();
  return collection?.find?.((item) => String(item.name).toLowerCase() === wanted) ?? null;
}

export async function handleMemberAdd(member) {
  if (!member?.guild || member.user?.bot) return;
  const guild = member.guild;

  const role = findByName(guild.roles?.cache, EXPLORER_ROLE);
  if (role) {
    try {
      await member.roles.add(role, 'Welcome: default role');
    } catch (err) {
      console.warn('[welcome] explorer role failed:', err.message);
    }
  }

  const channel = findByName(guild.channels?.cache, WELCOME_CHANNEL);
  if (channel?.isTextBased?.()) {
    try {
      await channel.send({
        embeds: [welcomeEmbed(`<@${member.id}>`)],
        allowedMentions: { users: [member.id] },
      });
    } catch (err) {
      console.warn('[welcome] welcome message failed:', err.message);
    }
  }
}
