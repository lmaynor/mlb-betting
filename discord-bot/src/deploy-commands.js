// Registers slash commands with Discord.
//   npm run register
// Guild-scoped (instant) when GUILD_ID is set, otherwise global (up to ~1h).
import 'dotenv/config';
import { REST, Routes } from 'discord.js';
import { pathToFileURL } from 'node:url';
import { loadCommands } from './load-commands.js';

/** PUT the command list. Returns the registered command names. */
export async function registerCommands({
  token = process.env.DISCORD_TOKEN,
  clientId = process.env.CLIENT_ID,
  guildId = process.env.GUILD_ID,
  commands,
  rest = new REST().setToken(token),
} = {}) {
  if (!token || !clientId) throw new Error('DISCORD_TOKEN and CLIENT_ID are required to register commands');
  const map = commands ?? await loadCommands();
  const body = [...map.values()].map((c) => c.data.toJSON());
  const route = guildId
    ? Routes.applicationGuildCommands(clientId, guildId)
    : Routes.applicationCommands(clientId);
  const result = await rest.put(route, { body });
  return { scope: guildId ? `guild ${guildId}` : 'global', names: body.map((c) => c.name), count: result?.length ?? body.length };
}

// Run directly: node src/deploy-commands.js
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { scope, names, count } = await registerCommands();
    console.log(`Registered ${count} command(s) (${scope}): ${names.join(', ')}`);
  } catch (err) {
    console.error(`Command registration failed: ${err.message}`);
    process.exit(1);
  }
}
