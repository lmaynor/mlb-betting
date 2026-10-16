import { readdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const COMMANDS_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'commands');

/**
 * Load every command module in src/commands/. Each must export
 * { data: SlashCommandBuilder, execute: (interaction, deps?) => Promise<void> }.
 * Adding a command is just: drop a new file in that folder.
 * @returns {Promise<Map<string, {data: object, execute: Function}>>}
 */
export async function loadCommands(dir = COMMANDS_DIR) {
  const commands = new Map();
  for (const file of readdirSync(dir).sort()) {
    if (!file.endsWith('.js') || file.startsWith('_')) continue;
    const mod = await import(pathToFileURL(path.join(dir, file)).href);
    if (!mod.data || !mod.execute) {
      console.warn(`[commands] skipping ${file}: missing data/execute export`);
      continue;
    }
    commands.set(mod.data.name, mod);
  }
  return commands;
}
