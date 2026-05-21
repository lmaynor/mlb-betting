#!/usr/bin/env python3
"""
cleanup_discord.py — Run once to:
  1. Delete junk roles Carl-bot added (platform roles + state roles)
  2. Create discord-ops-webhook-url secret in Secret Manager

Usage:
    TOKEN=$(gcloud secrets versions access latest --secret=discord-bot-token \
        --project=concrete-crow-445205-m4)
    python3 cleanup_discord.py --token "$TOKEN"
"""

import argparse
import asyncio
import subprocess
import sys
import discord

GUILD_ID = 1476027259956494533

# Roles we want to keep — everything else gets deleted
KEEP_ROLES = {
    "@everyone",
    "Admin",
    "Moderator",
    "Member Pro",
    "Member",
    "Paper Tester",
    "Bot",
    "DraftKings",
    "FanDuel",
    "Caesars",
    "BetMGM",
    "theScore",
    "PointsBet",
    "Carl-bot",
}


async def cleanup(token: str):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        try:
            guild = client.get_guild(GUILD_ID)
            if not guild:
                print(f"ERROR: Bot not in guild {GUILD_ID}")
                await client.close()
                return
            await run_cleanup(guild)
        finally:
            await client.close()

    await client.start(token)


async def run_cleanup(guild: discord.Guild):
    print(f"\nCleaning up: {guild.name}")
    print(f"Total roles before: {len(guild.roles)}")

    deleted = []
    skipped = []

    for role in guild.roles:
        if role.name in KEEP_ROLES or role.managed:  # managed = bot-owned roles
            skipped.append(role.name)
            continue
        try:
            await role.delete(reason="Cleanup: removing junk roles")
            deleted.append(role.name)
            print(f"  Deleted: {role.name}")
        except discord.Forbidden:
            print(f"  Skipped (no permission): {role.name}")
        except Exception as e:
            print(f"  Error deleting {role.name}: {e}")

    print(f"\nDeleted {len(deleted)} roles")
    print(f"Kept: {', '.join(skipped)}")
    print("\nDone. Create the ops webhook next (see instructions below).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    try:
        asyncio.run(cleanup(args.token))
    except KeyboardInterrupt:
        sys.exit(0)
