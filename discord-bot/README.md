# beezy-discord-bot

Discord bot for BEEZY.FYI. Pure JavaScript (ES modules), no native or voice
dependencies. Runtime deps: `discord.js` and `dotenv` only.

What it does:

- Slash commands: `/ev`, `/proj`, `/edge`, `/record`, `/ask`, `/link`, and
  owner-only `/setup-server`.
- `/setup-server` idempotently builds the roles, categories, channels,
  permission overwrites and per-channel "Beezy Terminal" webhooks, then replies
  (ephemeral) with `gcloud secrets` commands to wire the webhook URLs.
- `POST /internal/sync-role` lets the site push a member's tier; the bot
  upserts `member_links` and grants/revokes the paid roles immediately.
- Moderation: cross-channel spam and mention fan-out auto-bans, optional
  banned-word / invite / mention-limit filtering.
- Welcome embed in `#welcome` and the `Explorer` role on join.
- `@mention` or `/ask` answers via Gemini with four read-only tools.

## Environment

| Variable | Required | Notes |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | Bot exits at startup if missing. |
| `CLIENT_ID` | yes | Application id, used to register commands. |
| `GUILD_ID` | no | Set for instant guild-scoped command registration. Also the guild used by `/internal/sync-role` (falls back to the only guild the bot is in). |
| `OWNER_ID` | for `/setup-server` | Your Discord user id. Unset means nobody can run it. |
| `BETTING_API_URL` | for data commands | Cloud Run service URL. |
| `BETTING_API_KEY` | for data commands | Sent as `X-API-Key` to `/api/public/*`. |
| `GEMINI_API_KEY` | for `/ask`, @mentions | |
| `GEMINI_MODEL` | no | Default `gemini-2.5-flash`. |
| `BEEZY_BOT_SECRET` | for sync | Header `X-Beezy-Secret`. Unset gives 503, never an open endpoint. |
| `SITE_URL` | no | Default `https://beezy.fyi`. |
| `PORT` | no | Default `8080`. |
| `DB_PATH` | no | SQLite file, default `./beezy-bot.db`. |

In the Discord Developer Portal enable the **Server Members** and **Message
Content** privileged intents. Invite the bot with Manage Roles, Manage Channels,
Manage Webhooks, Ban Members and Manage Messages, and keep its role above the
Beezy roles so it can assign them.

## Run

Requires Node 22.5+ (developed and tested on 22.x and 26).

```sh
npm install
cp .env.example .env   # fill in values
npm start              # connects, registers commands, serves HTTP on $PORT
npm run register       # register commands only
npm test               # node --test, no network, no real Discord
```

`node:sqlite` is built in. The scripts pass `--experimental-sqlite`: Node 22.5
to 22.12 need it, newer versions (22.13+ and 26, where the flag is accepted and
a no-op) do not. If you run `node src/index.js` yourself on an old 22.x, add the
flag (or set `NODE_OPTIONS=--experimental-sqlite`).

## Deploy hint (Cloud Run)

The bot holds a gateway websocket, so run it with an always-allocated CPU and a
single instance:

```sh
gcloud run deploy beezy-discord-bot --source . --region REGION \
  --no-cpu-throttling --min-instances 1 --max-instances 1 \
  --set-env-vars CLIENT_ID=...,GUILD_ID=...,OWNER_ID=...,BETTING_API_URL=... \
  --update-secrets DISCORD_TOKEN=discord-bot-token:latest,BETTING_API_KEY=betting-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,BEEZY_BOT_SECRET=beezy-bot-secret:latest
```

SQLite lives on the container filesystem by default, which is ephemeral on Cloud
Run. `member_links` is re-populated by the site's sync calls; mount a volume and
set `DB_PATH` if you want moderation history to survive restarts.

## Sync endpoint

```
POST /internal/sync-role
X-Beezy-Secret: <BEEZY_BOT_SECRET>
{"discord_user_id":"123456789012345678","email":"a@b.co","tier":"single_sport","sport_choice":"nfl"}
```

`tier`: `public | single_sport | pro_monthly | pro_annual | starter | pro | season`.
`sport_choice`: `mlb | nfl | nba | nhl`. Responses: 200 with the applied role diff,
400 validation, 401 bad secret, 404 member not in the guild (link is still
saved), 413 body over 16 KB, 503 secret or guild not configured.
