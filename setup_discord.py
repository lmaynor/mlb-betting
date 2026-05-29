#!/usr/bin/env python3
"""
Discord server setup script for beezy.fyi
Run once from Cloud Shell after bot is added to server.

Usage:
    TOKEN=$(gcloud secrets versions access latest --secret=discord-bot-token \
        --project=concrete-crow-445205-m4)
    python3 setup_discord.py --token "$TOKEN"
"""

import argparse
import asyncio
import sys
import discord
from discord import PermissionOverwrite

GUILD_ID = 1476027259956494533

# Legal sports betting states (as of 2026)
LEGAL_BETTING_STATES = [
    "Arizona", "Arkansas", "Colorado", "Connecticut", "Delaware",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Mississippi",
    "Missouri", "Montana", "Nebraska", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "Ohio", "Oregon",
    "Pennsylvania", "Rhode Island", "South Dakota", "Tennessee",
    "Vermont", "Virginia", "Washington DC", "West Virginia",
    "Wisconsin", "Wyoming",
]

BOOK_ROLES = [
    "DraftKings",
    "FanDuel",
    "Caesars",
    "BetMGM",
    "theScore",
    "PointsBet",
]


async def setup(token: str):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        try:
            guild = client.get_guild(GUILD_ID)
            if not guild:
                print(f"ERROR: Bot is not in guild {GUILD_ID}. Check invite URL.")
                await client.close()
                return

            await run_setup(guild)
        except Exception as e:
            print(f"ERROR: {e}")
            raise
        finally:
            await client.close()

    await client.start(token)


async def run_setup(guild: discord.Guild):
    print(f"\nSetting up: {guild.name}")

    # ------------------------------------------------------------------
    # 1. Delete default channels
    # ------------------------------------------------------------------
    print("\n[1/6] Cleaning default channels...")
    for channel in guild.channels:
        try:
            await channel.delete()
            print(f"  Deleted: #{channel.name}")
        except Exception as e:
            print(f"  Could not delete #{channel.name}: {e}")

    # ------------------------------------------------------------------
    # 2. Create roles (order matters — higher index = higher position)
    # ------------------------------------------------------------------
    print("\n[2/6] Creating roles...")

    # Clear existing non-default roles
    for role in guild.roles:
        if role.name not in ("@everyone", guild.me.top_role.name):
            try:
                await role.delete()
            except Exception:
                pass

    # Core roles
    role_bot = await guild.create_role(
        name="Bot",
        color=discord.Color.greyple(),
        hoist=False,
        mentionable=False,
    )
    print(f"  Created: {role_bot.name}")

    role_paper_tester = await guild.create_role(
        name="Paper Tester",
        color=discord.Color.from_str("#5865F2"),
        hoist=True,
        mentionable=True,
    )
    print(f"  Created: {role_paper_tester.name}")

    role_member = await guild.create_role(
        name="Member",
        color=discord.Color.from_str("#57F287"),
        hoist=True,
        mentionable=True,
    )
    print(f"  Created: {role_member.name}")

    role_member_pro = await guild.create_role(
        name="Member Pro",
        color=discord.Color.from_str("#FEE75C"),
        hoist=True,
        mentionable=True,
    )
    print(f"  Created: {role_member_pro.name}")

    role_moderator = await guild.create_role(
        name="Moderator",
        color=discord.Color.from_str("#EB459E"),
        hoist=True,
        mentionable=True,
        permissions=discord.Permissions(
            manage_messages=True,
            kick_members=True,
            ban_members=True,
            manage_channels=False,
            administrator=False,
        ),
    )
    print(f"  Created: {role_moderator.name}")

    role_admin = await guild.create_role(
        name="Admin",
        color=discord.Color.from_str("#ED4245"),
        hoist=True,
        mentionable=True,
        permissions=discord.Permissions(administrator=True),
    )
    print(f"  Created: {role_admin.name}")

    # Book roles
    book_role_objects = {}
    for book in BOOK_ROLES:
        r = await guild.create_role(
            name=book,
            color=discord.Color.from_str("#99AAB5"),
            hoist=False,
            mentionable=True,
        )
        book_role_objects[book] = r
        print(f"  Created book role: {r.name}")

    # State roles
    state_role_objects = {}
    for state in LEGAL_BETTING_STATES:
        r = await guild.create_role(
            name=state,
            color=discord.Color.from_str("#747F8D"),
            hoist=False,
            mentionable=False,
        )
        state_role_objects[state] = r
        print(f"  Created state role: {r.name}")

    everyone = guild.default_role

    # ------------------------------------------------------------------
    # 3. Permission helpers
    # ------------------------------------------------------------------
    def read_only(*roles):
        """Roles can read but not send."""
        overwrite = {everyone: PermissionOverwrite(read_messages=False)}
        for r in roles:
            overwrite[r] = PermissionOverwrite(read_messages=True, send_messages=False)
        return overwrite

    def members_can_post(*roles):
        """Roles can read and send."""
        overwrite = {everyone: PermissionOverwrite(read_messages=False)}
        for r in roles:
            overwrite[r] = PermissionOverwrite(read_messages=True, send_messages=True)
        return overwrite

    def admin_only():
        return {
            everyone: PermissionOverwrite(read_messages=False),
            role_admin: PermissionOverwrite(read_messages=True, send_messages=True),
        }

    all_members = [role_paper_tester, role_member, role_member_pro, role_moderator, role_admin, role_bot]
    paid_members = [role_member, role_member_pro, role_moderator, role_admin, role_bot]

    # ------------------------------------------------------------------
    # 4. Create categories and channels
    # ------------------------------------------------------------------
    print("\n[3/6] Creating categories and channels...")

    # ── ONBOARDING ──────────────────────────────────────────────────
    cat_onboarding = await guild.create_category("ONBOARDING", position=0)

    await guild.create_text_channel(
        "verify",
        category=cat_onboarding,
        topic="Complete verification to access the server.",
        overwrites={
            everyone: PermissionOverwrite(read_messages=True, send_messages=False),
            role_admin: PermissionOverwrite(read_messages=True, send_messages=True),
        },
    )
    print("  Created: #verify")

    await guild.create_text_channel(
        "preferences",
        category=cat_onboarding,
        topic="Self-assign your book and state roles to get tailored pick pings.",
        overwrites=read_only(*all_members),
    )
    print("  Created: #preferences")

    # ── INFO ────────────────────────────────────────────────────────
    cat_info = await guild.create_category("INFO", position=1)

    await guild.create_text_channel(
        "welcome",
        category=cat_info,
        topic="What is beezy.fyi and how to read picks.",
        overwrites=read_only(*all_members),
    )
    print("  Created: #welcome")

    announcements = await guild.create_text_channel(
        "announcements",
        category=cat_info,
        topic="System updates, paper mode progress, launch news.",
        overwrites=read_only(*all_members),
    )
    # Convert to News channel (requires Community mode in Server Settings)
    try:
        await announcements.edit(type=discord.ChannelType.news)
        print("  Created: #announcements (News channel)")
    except discord.HTTPException:
        print("  Created: #announcements (text -- enable Community mode for News type)")

    await guild.create_text_channel(
        "rules",
        category=cat_info,
        topic="React to confirm you have read the rules and unlock access.",
        overwrites={
            everyone: PermissionOverwrite(read_messages=True, send_messages=False),
        },
    )
    print("  Created: #rules")

    # ── PICKS ───────────────────────────────────────────────────────
    cat_picks = await guild.create_category("PICKS", position=2)

    daily_picks = await guild.create_text_channel(
        "daily-picks",
        category=cat_picks,
        topic="Live pick signals from all 5 systems. Each day is a thread.",
        overwrites={
            **read_only(*all_members),
            role_bot: PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                create_public_threads=True,
                send_messages_in_threads=True,
            ),
        },
        default_auto_archive_duration=1440,  # threads archive after 24h
    )
    print("  Created: #daily-picks")

    await guild.create_text_channel(
        "daily-recap",
        category=cat_picks,
        topic="End-of-day settlement summary across all systems.",
        overwrites={
            **read_only(*all_members),
            role_bot: PermissionOverwrite(read_messages=True, send_messages=True),
        },
    )
    print("  Created: #daily-recap")

    await guild.create_text_channel(
        "performance",
        category=cat_picks,
        topic="Rolling performance alerts and Monday weekly digest.",
        overwrites={
            **read_only(*paid_members),
            role_bot: PermissionOverwrite(read_messages=True, send_messages=True),
        },
    )
    print("  Created: #performance")

    # ── COMMUNITY ───────────────────────────────────────────────────
    cat_community = await guild.create_category("COMMUNITY", position=3)

    await guild.create_text_channel(
        "general",
        category=cat_community,
        topic="General discussion.",
        overwrites={
            **members_can_post(*paid_members),
            role_paper_tester: PermissionOverwrite(read_messages=True, send_messages=False),
        },
    )
    print("  Created: #general (Paper Testers read-only)")

    # Forum channel type (15) — requires discord.py 2.0+ with guild.create_forum_channel
    # Fall back to text channel if unsupported on this server tier
    try:
        await guild.create_forum_channel(
            "betting-theory",
            category=cat_community,
            topic="Strategy, models, line shopping, bankroll management.",
            overwrites=members_can_post(*paid_members),
        )
        print("  Created: #betting-theory (Forum)")
    except (AttributeError, discord.HTTPException):
        await guild.create_text_channel(
            "betting-theory",
            category=cat_community,
            topic="Strategy, models, line shopping, bankroll management.",
            overwrites=members_can_post(*paid_members),
        )
        print("  Created: #betting-theory (text channel — upgrade to Community server for Forum type)")

    await guild.create_text_channel(
        "paper-feedback",
        category=cat_community,
        topic="Reactions to picks during paper mode. Bad lines, missed edges, questions.",
        overwrites=members_can_post(*all_members),
    )
    print("  Created: #paper-feedback")

    # ── OPS (admin only) ────────────────────────────────────────────
    cat_ops = await guild.create_category("OPS", position=4)

    await guild.create_text_channel(
        "ops-alerts",
        category=cat_ops,
        topic="monitor_ops.py failures. Silent on clean run.",
        overwrites={
            **admin_only(),
            role_bot: PermissionOverwrite(read_messages=True, send_messages=True),
        },
    )
    print("  Created: #ops-alerts")

    await guild.create_text_channel(
        "deploys",
        category=cat_ops,
        topic="Manual deploy notes and Cloud Run job status.",
        overwrites=admin_only(),
    )
    print("  Created: #deploys")

    # ------------------------------------------------------------------
    # 5. Assign bot role to the bot user
    # ------------------------------------------------------------------
    print("\n[4/6] Assigning Bot role...")
    bot_member = guild.me
    await bot_member.add_roles(role_bot)
    print(f"  Assigned Bot role to {bot_member.name}")

    # ------------------------------------------------------------------
    # 6. Seed #welcome and #preferences
    # ------------------------------------------------------------------
    print("\n[5/6] Seeding channel content...")

    welcome_ch = discord.utils.get(guild.text_channels, name="welcome")
    if welcome_ch:
        await welcome_ch.send(
            "**Welcome to beezy.fyi**\n\n"
            "Five MLB betting systems running daily:\n"
            "• **HR Pro** — home run yes/no props\n"
            "• **NRFI Pro** — no run first inning\n"
            "• **F5 Pro** — first 5 innings moneyline\n"
            "• **K Pro** — pitcher strikeout O/U\n"
            "• **OUTS** — pitcher outs O/U\n\n"
            "All systems are in **paper mode** until each clears 200 settled bets.\n\n"
            "→ Start in <#verify> to unlock the server.\n"
            "→ Then visit <#preferences> to opt into book and state pings.\n"
            "→ Full pick history and stats at https://beezy.fyi"
        )
        print("  Seeded #welcome")

    prefs_ch = discord.utils.get(guild.text_channels, name="preferences")
    if prefs_ch:
        book_list = "\n".join(f"• {b}" for b in BOOK_ROLES)
        state_list = "\n".join(f"• {s}" for s in LEGAL_BETTING_STATES)
        await prefs_ch.send(
            "**Pick your book(s)**\n"
            "You'll only be pinged when a pick has the best line at your book.\n\n"
            f"{book_list}\n\n"
            "React or use `/role` to assign. You can pick multiple books.\n\n"
            "---\n\n"
            "**Pick your state**\n"
            "Confirms sports betting is legal where you are.\n\n"
            f"{state_list}"
        )
        print("  Seeded #preferences")

    rules_ch = discord.utils.get(guild.text_channels, name="rules")
    if rules_ch:
        await rules_ch.send(
            "**Rules**\n\n"
            "1. This is paper mode — no real money is at stake yet.\n"
            "2. Do not share picks outside this server.\n"
            "3. No financial advice. These are model signals, not guarantees.\n"
            "4. Respect all members.\n"
            "5. No spam, self-promotion, or referral links.\n\n"
            "React with ✅ below to confirm you have read the rules and unlock access."
        )
        print("  Seeded #rules")

    print("\n[6/6] Done.")
    print("\nNext steps:")
    print("  1. Assign yourself the Admin role in Discord")
    print("  2. Set up a reaction role bot (Carl-bot recommended) on #rules")
    print("  3. Configure Carl-bot reaction roles for #preferences (books + states)")
    print("  4. Update discord.py to add discord-ops-webhook-url secret for #ops-alerts")
    print(f"\nServer: {guild.name} ({guild.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True, help="Discord bot token")
    args = parser.parse_args()

    try:
        asyncio.run(setup(args.token))
    except KeyboardInterrupt:
        sys.exit(0)
