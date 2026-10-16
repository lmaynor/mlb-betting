import 'dotenv/config';
import { Client, Events, GatewayIntentBits, MessageFlags } from 'discord.js';
import * as db from './db.js';
import { loadCommands } from './load-commands.js';
import { registerCommands } from './deploy-commands.js';
import { startHttpServer } from './httpServer.js';
import { handleMemberAdd } from './welcome.js';
import { handleMention } from './mention.js';
import * as antispam from './antispam.js';
import * as automod from './automod.js';

if (!process.env.DISCORD_TOKEN) {
  console.error('DISCORD_TOKEN is required (see .env.example). Exiting.');
  process.exit(1);
}
if (!process.env.BEEZY_BOT_SECRET) {
  console.warn('[startup] BEEZY_BOT_SECRET is unset: POST /internal/sync-role will answer 503.');
}
if (!process.env.OWNER_ID) {
  console.warn('[startup] OWNER_ID is unset: /setup-server will refuse everyone.');
}
if (!process.env.BETTING_API_URL || !process.env.BETTING_API_KEY) {
  console.warn('[startup] BETTING_API_URL / BETTING_API_KEY unset: data commands will report the API as not configured.');
}
if (!process.env.GEMINI_API_KEY) {
  console.warn('[startup] GEMINI_API_KEY is unset: /ask and @mentions will report an error.');
}

db.initDb();
process.on('exit', () => db.closeDb());

// GuildMembers and MessageContent are privileged intents: enable both in the
// Discord Developer Portal (Bot tab) or login fails.
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

const commands = await loadCommands();
client.commands = commands;

client.once(Events.ClientReady, async (c) => {
  console.log(`Logged in as ${c.user.tag} - ${c.guilds.cache.size} guild(s)`);
  try {
    const { scope, names } = await registerCommands({ commands });
    console.log(`Registered ${names.length} command(s) (${scope}): ${names.join(', ')}`);
  } catch (err) {
    // Not fatal: the bot keeps serving whatever commands are already registered.
    console.error(`Command registration failed: ${err.message}`);
  }
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  const command = client.commands.get(interaction.commandName);
  if (!command) return;
  try {
    await command.execute(interaction);
  } catch (err) {
    console.error(`[interaction] ${interaction.commandName} failed: ${err?.name}: ${String(err?.message || err).slice(0, 200)}`);
    const reply = { content: 'Something went wrong running that command.', flags: MessageFlags.Ephemeral };
    try {
      if (interaction.replied || interaction.deferred) await interaction.followUp(reply);
      else await interaction.reply(reply);
    } catch { /* interaction expired; nothing more to do */ }
  }
});

client.on(Events.MessageCreate, async (message) => {
  try {
    await automod.checkMessage(message);
  } catch (err) {
    console.error('[automod] check failed:', err?.message || err);
  }
  try {
    await antispam.checkMessage(message);
  } catch (err) {
    console.error('[antispam] check failed:', err?.message || err);
  }
  try {
    await handleMention(message, client);
  } catch (err) {
    console.error('[mention] handling failed:', err?.message || err);
  }
});

client.on(Events.GuildMemberAdd, async (member) => {
  try {
    await handleMemberAdd(member);
  } catch (err) {
    console.error('[welcome] failed:', err?.message || err);
  }
});

client.on(Events.Error, (err) => console.error('[client] error:', err?.message || err));
process.on('unhandledRejection', (err) => console.error('[unhandledRejection]', err?.message || err));

// Start HTTP first so Cloud Run's health probe passes while the gateway connects.
const server = await startHttpServer({ client });
console.log(`HTTP listening on :${server.address().port}`);

async function shutdown(signal) {
  console.log(`${signal} received, shutting down`);
  server.close();
  await client.destroy();
  process.exit(0);
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

try {
  await client.login(process.env.DISCORD_TOKEN);
} catch (err) {
  // The message never contains the token. Common causes: bad token, or the
  // Server Members / Message Content privileged intents not enabled in the portal.
  console.error(`Discord login failed: ${err?.message || err}`);
  server.close();
  process.exit(1);
}
