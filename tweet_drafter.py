"""
tweet_drafter.py — beezy_fyi Twitter content automation
Runs as a Cloud Run job. Pulls picks/results from the beezy API,
generates tweet drafts via Gemini, posts them to Discord #tweet-queue for a 1-tap post on X (Typefully was removed
2026-10-19: Discord is the only output, nothing posts to X automatically).

Secrets (Secret Manager):
  site-api-key         -- beezy Cloud Run API key (already exists)
  gemini-api-key       -- Gemini free tier key
  discord-webhook-tweets / discord-ops-webhook-url -- #tweet-queue webhook (DISCORD_WEBHOOK_TWEETS, else _OPS)

Modes (--mode or TWEET_MODE env; the env var is what the Cloud Run Jobs set):
  picks | morning  Gemini drafts for today's card -> Discord  ("morning" is the new name for "picks")
  recap            Gemini drafts recapping the last settled slate -> Discord
  settlement       Deterministic #GamblingX receipt post for the last settled slate: tweet text +
                   /api/og/settlement-card PNG + celebration GIF -> Discord (DISCORD_WEBHOOK_TWEETS,
                   falling back to DISCORD_WEBHOOK_OPS) for a 1-tap post on X. No Gemini.
  reply_ammo       Deterministic, link-free reply snippets for the day's top book-vs-consensus price gaps (all sports),
                   each with an X live-search link, -> Discord #tweet-queue. No Gemini.
  steam            NOT BUILT: needs a public per-bet closing-line endpoint that does not exist yet.

Link policy (X throttles posts from small accounts that carry an outbound URL): a main tweet NEVER carries a URL. It ends
with the plain-text watermark `beezy.fyi`; the clickable link goes in a reply the operator posts under it (the Discord message carries the
reply text). Every mode that drafts a tweet posts to Discord with a 1-tap x.com/intent link and the OG card image.
A failed or skipped Discord post fails the job: it is the only output.

Schedule (recommended):
  Morning job (10:00 ET): picks tweet for today's slate
  Evening job (23:30 ET): recap tweet after settlement
  Pre-lock (17:15 ET): reply_ammo (not provisioned as a Job yet)
"""

import os
import re
import sys
import json
import requests
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from mlb_core.social.gif_fetcher import fetch_celebration_gif

# ── Config ────────────────────────────────────────────────────────────────────

BEEZY_API_URL = os.environ.get("BEEZY_API_URL", "https://api.beezy.fyi").rstrip("/")
BEEZY_SITE_URL = os.environ.get("BEEZY_SITE_URL", "https://beezy.fyi").rstrip("/")
BEEZY_API_KEY = os.environ["SITE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# gemini-2.0-flash was retired by Google (confirmed live 2026-09-01: 404 on
# generateContent, had been silently failing mlb-tweet-picks/mlb-tweet-recap
# for days). Using the generic "-latest" alias instead of a dated model id
# so this doesn't recur -- verified against the live /v1beta/models listing
# that this alias currently exists and supports generateContent.
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-flash-latest:generateContent"
)

MODE = os.environ.get("TWEET_MODE", "picks")  # picks|morning|recap|settlement|reply_ammo (see module docstring)
# Sports with rows in the `bets` table (bet_tracker.VALID_SPORTS). NFL is deliberately absent: NFL is shadow-only, nothing is
# logged or settled, and /api/public/picks/recent?sport=nfl answers 400. Add it here AND in the settlement-card route's SPORTS
# only once NFL bets are actually tracked.
SETTLEMENT_SPORTS = ("mlb", "nhl", "nba")

WATERMARK = "beezy.fyi"   # plain text, no scheme, no path: the only form of the site name a main tweet may carry
_CT = ZoneInfo("America/Chicago")

# ── Brand voice system prompt ─────────────────────────────────────────────────

# #GamblingX voice rules. Hard-enforced after generation by violates_voice(); also stated in the prompt.
BANNED_PHRASES = (
    "let's dive in", "lets dive in", "lock it in", "lock of the", "smash", " ai ", "ai-powered", "algorithm",
    "guaranteed", "parlay of the day", "cash it", "can't lose", "free money",
)

SYSTEM_PROMPT = """
You are the voice of @beezy_fyi on X (#GamblingX): a model-driven betting
analytics account. beezy.fyi publishes quantitative picks and a +EV board with
edge %, Kelly stake, and a full public results ledger. Paper mode now.

Voice: deadpan, numbers-first, sharp market commentary, slightly irreverent.
Think @UnabatedSports / @SpankOdds / @PlusEVAnalytics. Lead with the specific
price dislocation or the closing-line move. Show exact numbers: book + price vs
the de-vigged consensus fair price, net EV %. When a day loses, say so
flatly and cite the season sample. Receipts, not hype.

BANNED: "let's dive in", "lock it in", "lock of the century", "smash",
"AI", "algorithm", "guaranteed", "parlay of the day", hype caps, more than one
emoji (zero is better).

Audience: bettors tired of tout accounts who want to see real edge.
End every variant with the plain-text watermark beezy.fyi. NEVER write a URL:
no https://, no path after beezy.fyi, no other site. The link goes in a
separate reply that is added automatically.

Format rules:
- Max 280 characters per tweet
- Return exactly 3 tweet variants as a JSON array of strings
- No markdown, no explanations outside the JSON
- Each variant should have a slightly different angle (price dislocation / season ledger / understated)

Example good tweet:
"DraftKings is 23 cents off the market on Stanton HR right now. 12-book fair
is -108, DK hanging +115 (+7.1% EV). beezy.fyi"

Example bad tweet:
"STANTON IS A LOCK TODAY 🔥🔥 BET THE HOUSE #MLB"
"""


def violates_voice(text):
    """True when a draft breaks the #GamblingX voice rules (banned phrase or more than one emoji)."""
    low = f" {text.lower()} "
    if any(b in low for b in BANNED_PHRASES):
        return True
    emoji = [c for c in text if ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF]
    return len(emoji) > 1


def filter_voice(tweets):
    """Drop drafts that violate the voice rules; raise if nothing is left rather than pushing banned copy."""
    kept = [t for t in tweets if isinstance(t, str) and not violates_voice(t)]
    if not kept:
        raise RuntimeError("every generated draft violated the #GamblingX voice rules; nothing to push")
    return kept


# -- Link policy ---------------------------------------------------------------------------------------------------------

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SITE_PATH_RE = re.compile(r"\bbeezy\.fyi/\S*", re.IGNORECASE)


def has_url(text):
    """True when text contains something X will turn into an outbound link (a scheme or www. URL, or beezy.fyi/<path>)."""
    return bool(_URL_RE.search(text) or _SITE_PATH_RE.search(text))


def strip_urls(text):
    """Make a main tweet link-free: every beezy.fyi URL (any scheme/path) becomes the plain watermark, every other URL is dropped.
    Gemini is told never to write a URL; this is the enforcement when it does anyway."""
    out = re.sub(r"(?:https?://)?(?:www\.)?beezy\.fyi\S*", WATERMARK, text, flags=re.IGNORECASE)
    out = _URL_RE.sub("", out)
    out = re.sub(rf"({re.escape(WATERMARK)})(?:\W+{re.escape(WATERMARK)})+", r"\1", out)   # a doubled watermark collapses
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()


REPLY_COPY = {
    "picks": "Full live board + model projections -> {url}",
    "recap": "Every play graded publicly, full ledger -> {url}",
    "settlement": "Every play graded publicly, full ledger -> {url}",
}


def build_link_reply(label, url):
    """Second post of the thread: the only place the clickable link lives."""
    return REPLY_COPY.get(label, "More -> {url}").format(url=url)


def build_x_intent_url(text):
    """1-tap 'post this' link: opens the X composer pre-filled with text."""
    return f"https://x.com/intent/tweet?text={quote(text, safe='')}"


def build_x_search_url(query):
    """X live-search link (newest first), used to find a fresh tweet to reply under."""
    return f"https://x.com/search?q={quote(query, safe='')}&f=live"


# ── Fetch beezy data ──────────────────────────────────────────────────────────

def get_today_picks():
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/picks/today",
        headers={"X-API-Key": BEEZY_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_recent_settled(limit=10, sport=None):
    params = {"limit": limit}
    if sport:
        params["sport"] = sport
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/picks/recent",
        headers={"X-API-Key": BEEZY_API_KEY},
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_stats():
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/stats/summary",
        headers={"X-API-Key": BEEZY_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ── Build prompt from data ────────────────────────────────────────────────────

def build_picks_prompt(picks):
    if not picks:
        return None

    lines = [f"Today's beezy model picks ({date.today().strftime('%b %d')}):\n"]
    for p in picks[:5]:  # cap at 5 so prompt stays tight
        system = p.get("system", "")
        game = p.get("game", "")
        bet_type = p.get("bet_type", "")
        odds = p.get("odds", "")
        edge = p.get("edge", "")
        stake = p.get("stake", "")
        notes = p.get("notes", "")

        line = f"  {system} | {game} | {bet_type} {odds} | edge {edge}% | {stake}u Kelly"
        if notes:
            line += f" | {notes}"
        lines.append(line)

    lines.append(
        "\nWrite 3 tweet drafts for @beezy_fyi announcing today's picks. "
        "EVERY draft must: (1) state the model probability vs the market's "
        "implied probability, or the edge %, on at least one pick -- the EV "
        "math IS the content; (2) end with the plain-text watermark "
        f"{WATERMARK} on its own line -- NO https://, NO path, NO other URL "
        "anywhere in the tweet (the link is added as a separate reply); "
        "(3) stay under 270 characters. No hashtag spam (max 1), no emojis, "
        "no 'lock'/'smash' tout language. "
        "Return only a JSON array of 3 strings."
    )
    return "\n".join(lines)


def build_recap_prompt(settled, stats):
    if not settled:
        return None

    # NOTE: this used to filter on `game_date == str(date.today())`, which
    # can never match -- /settle always settles YESTERDAY's slate (runs
    # 09:00 UTC, this job runs 10:00 UTC, so date.today() has already rolled
    # past every bet's game_date by the time this runs). Produced zero rows
    # every single day. See
    # docs/solutions/logic-errors/tweet-recap-date-filter-never-matches.md.
    # get_recent_settled() orders `ORDER BY game_date DESC, ...` (see
    # mlb/runners/public_api.py get_recent_settled), so the max game_date
    # present IS the most recently completed, fully-settled slate -- that's
    # what "today's results" actually means for a 5am-ET recap job.
    recap_date = max(p.get("game_date") for p in settled if p.get("game_date"))

    recap_settled = [
        p for p in settled
        if p.get("game_date") == recap_date
        and p.get("result") in ("win", "loss", "push")
    ]

    if not recap_settled:
        return None

    wins = [p for p in recap_settled if p["result"] == "win"]
    losses = [p for p in recap_settled if p["result"] == "loss"]
    total_profit = sum(float(p.get("profit", 0)) for p in recap_settled)
    record = f"{len(wins)}-{len(losses)}"

    overall = stats.get("overall", {})
    season_roi = overall.get("roi", "N/A")
    total_bets = overall.get("total_bets", "N/A")

    try:
        recap_date_label = datetime.strptime(recap_date, "%Y-%m-%d").strftime("%b %d")
    except (ValueError, TypeError):
        recap_date_label = recap_date  # fall back to the raw string rather than crash

    prompt = (
        f"beezy results for {recap_date_label}:\n"
        f"  Record: {record}\n"
        f"  P&L: {total_profit:+.2f}u\n"
        f"  Season: {total_bets} bets, {season_roi}% ROI\n\n"
    )

    for p in recap_settled[:3]:
        icon = "✓" if p["result"] == "win" else "✗"
        prompt += (
            f"  {icon} {p.get('system')} | {p.get('game')} | "
            f"{p.get('bet_type')} {p.get('odds')} | "
            f"{float(p.get('profit', 0)):+.2f}u\n"
        )

    prompt += (
        "\nWrite 3 tweet drafts for @beezy_fyi recapping today's results. "
        "Report the record and units honestly -- LOSING days get posted with "
        "the same tone as winning days; the transparency is the brand. Cite "
        f"the season sample size. End with the plain-text watermark {WATERMARK} "
        "on its own line -- NO https://, NO path, NO other URL (the link is "
        "added as a separate reply). Under 270 characters, max 1 hashtag, no emojis. "
        "Return only a JSON array of 3 strings."
    )
    return prompt


# ── Call Gemini ───────────────────────────────────────────────────────────────

def generate_tweets(user_prompt):
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        # 512 was too tight: newer Gemini generations spend a variable chunk
        # of the budget on internal "thinking" tokens before any visible
        # text. Confirmed live 2026-09-02: the (longer) recap prompt burned
        # the ENTIRE 512-token budget on thinking, leaving zero visible
        # text and crashing json.loads("") with a confusing "Expecting
        # value" error. Bumped for real headroom -- cost is negligible on
        # the free tier either way.
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1536},
    }

    resp = requests.post(
        GEMINI_URL,
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": GEMINI_API_KEY,
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()

    candidate = body["candidates"][0]
    parts = candidate.get("content", {}).get("parts") or []
    raw = (parts[0].get("text") or "").strip() if parts else ""

    if not raw:
        # Surface WHY there was no visible text (usually finishReason=
        # MAX_TOKENS with the whole budget spent on thinking) instead of a
        # bare, confusing JSONDecodeError three frames down.
        raise RuntimeError(
            f"Gemini returned no visible text (finishReason="
            f"{candidate.get('finishReason', 'unknown')}, usageMetadata="
            f"{body.get('usageMetadata', {})}). Prompt was "
            f"{len(user_prompt)} chars -- consider raising maxOutputTokens "
            "further."
        )

    # Strip any markdown fences Gemini sometimes adds
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]

    return [strip_urls(t) for t in filter_voice(json.loads(raw.strip()))]


# -- Discord #tweet-queue ----------------------------------------------------------------------------------------

# Discord caps the combined text of all embeds in one message at 6000 chars and a message at 10 embeds.
_EMBED_TEXT_BUDGET = 5500
_EMBED_MAX = 10


def _embed_len(e):
    n = len(e.get("title", "")) + len(e.get("description", ""))
    n += sum(len(f.get("name", "")) + len(f.get("value", "")) for f in e.get("fields", []))
    return n


def _code(text):
    """Discord code block (tap-and-hold copies it on mobile); a stray ``` in the text must not close the block early."""
    return "```\n" + text.replace("```", "'''") + "\n```"


def post_embeds(hook, content, embeds):
    """POST `embeds` to a Discord webhook in as few messages as the limits allow. Returns a status line (never raises on
    an HTTP error, never raises on an HTTP error; callers decide whether a failed post is fatal via require_posted)."""
    batches, cur, cur_len = [], [], 0
    for e in embeds:
        n = _embed_len(e)
        if cur and (len(cur) >= _EMBED_MAX or cur_len + n > _EMBED_TEXT_BUDGET):
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(e)
        cur_len += n
    if cur or not batches:
        batches.append(cur)
    bad = []
    for i, batch in enumerate(batches):
        body = {"embeds": batch}
        if i == 0 and content:
            body["content"] = content[:2000]
        resp = requests.post(hook, json=body, timeout=10)
        if resp.status_code not in (200, 204):
            bad.append(resp.status_code)
    return f"  Discord post {'ok' if not bad else f'FAILED ({bad[0]})'}" + (f" ({len(batches)} messages)" if len(batches) > 1 else "")


def require_posted(result):
    """Discord is the only output, so a skipped or failed post must fail the job (a Job that exits 0 having posted nothing is
    the silent-failure shape this repo has been bitten by)."""
    if "FAILED" in result or "SKIPPED" in result:
        raise RuntimeError(f"tweet-queue post did not go out:{result}")
    return result


def post_drafts_to_discord(mode_label, tweets, card_url=None, reply_url=None):
    """Mirror drafted tweets to #tweet-queue: per variant a copyable code block + a 1-tap X intent link, the OG card image on
    top so it can be saved, and (when the thread has one) the link reply to post under it."""
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")          # DISCORD_WEBHOOK_TWEETS -> DISCORD_WEBHOOK_OPS
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    reply_text = build_link_reply(mode_label, reply_url) if reply_url else None
    content = f"**{mode_label} drafts ({date.today()})** -- save the card, tap 1-Tap Post on X, attach the image."
    if reply_text:
        content += f"\nThen reply to your own post with:\n{_code(reply_text)}"
    embeds = []
    if card_url:
        embeds.append({"title": f"{mode_label} card", "image": {"url": card_url}})
    for i, tweet in enumerate(tweets, 1):
        embeds.append({
            "title": f"Variant {i}",
            "description": f"{_code(tweet)}\n[1-Tap Post on X]({build_x_intent_url(tweet)})",
        })
    return post_embeds(hook, content, embeds)


# ── Settlement receipt (deterministic, no Gemini) ─────────────────────────────

def _u(x):
    return f"{'+' if x >= 0 else '-'}{abs(x):.2f}u"


def get_settled_all_sports(limit=200):
    """Recent settled bets across the live sports, each row tagged with `sport`. One sport failing is not fatal."""
    rows = []
    for sp in SETTLEMENT_SPORTS:
        try:
            data = get_recent_settled(limit=limit, sport=sp)
        except Exception as e:  # noqa: BLE001
            print(f"  settled fetch failed for {sp}: {e}")
            continue
        for r in (data if isinstance(data, list) else data.get("picks", [])):
            rows.append({**r, "sport": sp})
    return rows


def build_settlement(settled, stats):
    """Summarize the most recent fully settled slate. None when there is nothing graded.
    Returns {date, w, l, p, net, hero, text, card_url, reply_url}; text is the ready-to-post, link-free #GamblingX tweet
    and reply_url is the link for the follow-up reply."""
    dated = [r for r in settled if r.get("game_date") and r.get("result") in ("win", "loss", "push")]
    if not dated:
        return None
    day = max(r["game_date"] for r in dated)
    slate = [r for r in dated if r["game_date"] == day]
    w = sum(r["result"] == "win" for r in slate)
    l = sum(r["result"] == "loss" for r in slate)
    p = sum(r["result"] == "push" for r in slate)
    net = sum(float(r.get("profit") or 0) for r in slate)
    wins = [r for r in slate if r["result"] == "win"]
    hero = max(wins, key=lambda r: float(r.get("profit") or 0)) if wins else None

    overall = (stats or {}).get("overall", {}) or {}
    ledger = ""
    if overall.get("roi") is not None and overall.get("total_bets") is not None:
        try:
            ledger = f" Season ledger {float(overall['roi']):+.1f}% ROI across {int(overall['total_bets']):,} graded bets."
        except (TypeError, ValueError):
            ledger = ""

    card_url = f"{BEEZY_SITE_URL}/api/og/settlement-card?date={day}&sport=all"
    if net < 0:
        text = f"Dropped {abs(net):.2f}u on {day} card ({w}-{l}{f'-{p}' if p else ''}).{ledger} Every play logged publicly. {WATERMARK}"
    else:
        best = ""
        if hero:
            who = hero.get("player") or hero.get("bet_type", "").replace("_", " ")
            best = f" Best price: {who} {int(hero['odds']):+d} ({_u(float(hero.get('profit') or 0))})." if hero.get("odds") is not None else ""
        text = f"{day} card: {w}-{l}{f'-{p}' if p else ''}, {_u(net)}.{best}{ledger} Receipts: {WATERMARK}"
    return {"date": day, "w": w, "l": l, "p": p, "net": net, "hero": hero, "text": text, "card_url": card_url,
            "reply_url": f"{BEEZY_SITE_URL}/results"}


def post_settlement_to_discord(summary, gif):
    """Post tweet text + receipt PNG + GIF to the tweet-queue webhook so the operator can post both to X in one go."""
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")          # DISCORD_WEBHOOK_TWEETS -> DISCORD_WEBHOOK_OPS
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    content = f"**Settlement tweet draft ({summary['date']})**\n{_code(summary['text'])}\n[1-Tap Post on X]({build_x_intent_url(summary['text'])})"
    if summary.get("reply_url"):
        content += f"\nThen reply to your own post with:\n{_code(build_link_reply('settlement', summary['reply_url']))}"
    embeds = [{"title": f"Settlement receipt {summary['date']}", "image": {"url": summary["card_url"]}}]
    if gif.get("gif_url"):
        content += f"\nCelebration GIF ({gif['query']}): {gif['gif_url']}"
        embeds.append({"title": gif["query"], "image": {"url": gif.get("preview_url") or gif["gif_url"]}})
    else:
        content += f"\nNo GIF resolved for \"{gif['query']}\" (no TENOR_API_KEY/GIPHY_API_KEY): {gif.get('search_url', '')}"
    resp = requests.post(hook, json={"content": content[:2000], "embeds": embeds}, timeout=10)
    return f"  Discord post {'ok' if resp.status_code in (200, 204) else f'FAILED ({resp.status_code})'}"


def run_settlement():
    settled = get_settled_all_sports()
    summary = build_settlement(settled, get_stats())
    if not summary:
        print("No settled bets found -- skipping.")
        return
    if violates_voice(summary["text"]):
        raise RuntimeError(f"settlement text violates voice rules: {summary['text']}")
    gif = fetch_celebration_gif(summary["hero"] or {})
    print(f"Settlement tweet: {summary['text']}\nCard: {summary['card_url']}\nGIF: {gif.get('gif_url') or gif.get('search_url')}")
    print(require_posted(post_settlement_to_discord(summary, gif)))


# -- reply_ammo: link-free replies for big accounts' threads -----------------------------------------------------

# Replying under a big account with a link or a pitch gets you blocked; replying with a price nobody else posted does not.
# So every snippet is a plain market fact (a book's price vs the de-vigged consensus of the OTHER books), carries no URL and
# no brand name, and fits one short reply. Each play also gets an X live-search link to find a fresh tweet about the player.
REPLY_SNIPPET_MAX = 180
REPLY_MAX_PLAYS = 5
REPLY_MAX_PER_SPORT = 2      # one hot sport must not fill all five slots
REPLY_MIN_BOOKS = 4          # mirrors public_api._OUTLIER_MIN_BOOKS: fewer reference books is reference error, not edge
REPLY_MIN_MINS_TO_KICK = 30  # a quote that is about to start is dead by the time you find a tweet to reply under
REPLY_MIN_FAVES = 20         # search filter: only tweets that already have some reach

# Mirrors ev.daily_projections.STAT_LABEL (a test pins them equal) plus the MLB markets.
STAT_LABELS = {
    "pass_yds": "Pass Yds", "rush_yds": "Rush Yds", "rec_yds": "Rec Yds", "receptions": "Receptions",
    "pass_tds": "Pass TDs", "anytime_td": "Anytime TD", "pass_completions": "Completions",
    "pass_attempts": "Pass Attempts", "rush_attempts": "Carries", "interceptions": "Interceptions",
    "sog": "Shots on Goal", "goals": "Goals", "points": "Points",
    "assists": "Assists", "saves": "Saves", "blocked_shots": "Blocked Shots",
    "rebounds": "Rebounds", "threes": "3-Pointers", "pra": "Pts+Reb+Ast", "pr": "Pts+Reb", "pa": "Pts+Ast",
    "ra": "Reb+Ast", "steals": "Steals", "blocks": "Blocks",
}
_MLB_STATS = {"K": "Ks", "OUTS": "Outs", "BATTER_TB": "Total Bases", "BATTER_HITS": "Hits", "PITCHER_ER": "Earned Runs",
              "SB": "Steals"}
_MLB_BET_RE = re.compile(r"^(K|OUTS|BATTER_TB|BATTER_HITS|PITCHER_ER|SB)_(OVER|UNDER)_([\d.]+)$")
_BOOK_SHORT = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "caesars": "Caesars", "bet365": "bet365",
               "hardrock": "Hard Rock", "betrivers": "BetRivers", "fanatics": "Fanatics", "thescore": "theScore",
               "novig": "Novig", "prophetx": "ProphetX", "espnbet": "theScore"}


def get_ev_feed(min_ev=0.03, limit=100):
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/ev/feed",
        headers={"X-API-Key": BEEZY_API_KEY},
        params={"sport": "all", "min_ev": min_ev, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_projections(sport):
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/projections",
        headers={"X-API-Key": BEEZY_API_KEY},
        params={"sport": sport, "limit": 1200},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def prob_to_american(p):
    """Fair probability -> American price (rounded), or None outside (0, 1)."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if not 0.0 < p < 1.0:
        return None
    return int(round(-100.0 * p / (1.0 - p))) if p >= 0.5 else int(round(100.0 * (1.0 - p) / p))


def _odds(a):
    return f"{int(a):+d}"


def is_link_free(text):
    """The anti-block invariant for a reply snippet: no URL, no site name, no handle."""
    low = text.lower()
    return not has_url(text) and "http" not in low and "beezy" not in low and "@" not in text


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def reply_candidates(feed):
    """Every credible book-vs-consensus quote in the EV feed as one flat shape. Fail closed: a block that is missing, stale or
    unvalidated-and-unflagged contributes nothing, and a row without a price, a fair probability or a book count is skipped."""
    out = []
    today = datetime.now(_CT).date().isoformat()
    for r in ((feed.get("mlb") or {}).get("rows") or []):
        # tracked MLB alerts: only still-pending ones from today's slate (a settled row is a result, not a live price)
        m = _MLB_BET_RE.match(str(r.get("bet_type") or ""))
        if not m or r.get("result") is not None or r.get("game_date") != today or not r.get("player"):
            continue
        out.append({"sport": "mlb", "player": r["player"], "player_norm": None, "stat": m.group(1),
                    "stat_label": _MLB_STATS[m.group(1)], "point": _num(m.group(3)), "side": m.group(2),
                    "book": r.get("book"), "american": _num(r.get("odds")), "p_fair": _num(r.get("model_prob")),
                    "ev": _num(r.get("edge")), "n_books": r.get("n_books"), "mins_to_kick": None})
    for sp in ("nfl", "nhl", "nba"):
        block = feed.get(sp) or {}
        if block.get("stale"):
            continue
        rows = block.get("book_outliers") if sp == "nfl" else block.get("rows")
        for r in rows or []:
            if r.get("venue", "book") != "book" or not r.get("flag") or not r.get("player"):
                continue
            stat = r.get("stat")
            out.append({"sport": sp, "player": r["player"], "player_norm": r.get("player_norm"), "stat": stat,
                        "stat_label": STAT_LABELS.get(stat) or str(stat or "").replace("_", " ").title(),
                        "point": _num(r.get("point")), "side": str(r.get("side") or "").upper(), "book": r.get("book"),
                        "american": _num(r.get("american")), "p_fair": _num(r.get("p_fair")), "ev": _num(r.get("ev")),
                        "n_books": r.get("n_books"), "mins_to_kick": _num(r.get("mins_to_kick"))})
    return out


def select_reply_plays(candidates, max_plays=REPLY_MAX_PLAYS, per_sport=REPLY_MAX_PER_SPORT):
    """Best EV first, at most `per_sport` per sport, one play per (sport, player, stat)."""
    ok = [c for c in candidates
          if c["american"] and c["p_fair"] and c["ev"] and c["ev"] > 0 and c["side"] in ("OVER", "UNDER")
          and (c["n_books"] or 0) >= REPLY_MIN_BOOKS
          and (c["mins_to_kick"] is None or c["mins_to_kick"] >= REPLY_MIN_MINS_TO_KICK)]
    ok.sort(key=lambda c: -c["ev"])
    picked, seen, per = [], set(), {}
    for c in ok:
        key = (c["sport"], str(c["player"]).lower(), c["stat"])
        if key in seen or per.get(c["sport"], 0) >= per_sport:
            continue
        seen.add(key)
        per[c["sport"]] = per.get(c["sport"], 0) + 1
        picked.append(c)
        if len(picked) >= max_plays:
            break
    return picked


def build_reply_snippet(c, proj_mean=None):
    """One reply (<= REPLY_SNIPPET_MAX chars): who, the line, the book's price vs the other books' de-vigged fair price.
    Optional pieces (our projection mean, then the EV figure) are dropped, in that order, to fit; None when even the bare
    sentence does not fit or the result is not link-free -- never truncated."""
    fair = prob_to_american(c["p_fair"])
    if fair is None or c.get("point") is None:
        return None
    book = _BOOK_SHORT.get(str(c.get("book") or "").lower()) or str(c.get("book") or "A book").title()
    if c["stat"] == "anytime_td":
        head = f"{c['player']} anytime TD"
    else:
        head = f"{c['player']} {'Over' if c['side'] == 'OVER' else 'Under'} {c['point']:g} {c['stat_label']}"
    core = f"{head}: {book} is hanging {_odds(c['american'])} while the {c['n_books']}-book de-vigged fair sits at {_odds(fair)}."
    ev = f" ({c['ev'] * 100:+.1f}% EV vs consensus)"
    model = f" Our projection: {proj_mean:g}." if proj_mean is not None else ""
    for text in (core[:-1] + ev + "." + model, core[:-1] + ev + ".", core):
        if len(text) <= REPLY_SNIPPET_MAX and is_link_free(text) and not violates_voice(text):
            return text
    return None


def build_reply_ammo(feed, projections=None):
    """[{sport, player, snippet, search_url}] for the day's top plays. `projections` = {sport: projection payload}; a matching
    (player, stat) row adds our projection mean to the snippet. Plays that cannot be written within the limits are skipped."""
    proj_idx = {}
    for sp, payload in (projections or {}).items():
        for r in (payload or {}).get("rows") or []:
            if r.get("proj_mean") is not None:
                proj_idx[(sp, r.get("player_norm"), r.get("stat"))] = _num(r.get("proj_mean"))
    plays = []
    for c in select_reply_plays(reply_candidates(feed)):
        snippet = build_reply_snippet(c, proj_idx.get((c["sport"], c.get("player_norm"), c["stat"])))
        if snippet:
            plays.append({"sport": c["sport"], "player": c["player"], "snippet": snippet,
                          "search_url": build_x_search_url(f"{c['player']} min_faves:{REPLY_MIN_FAVES}")})
    return plays


def post_reply_ammo_to_discord(plays):
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    content = (f"**Reply ammo ({date.today()})** -- tap Search, find a fresh tweet about the player, paste the snippet as a "
               "REPLY. No links, no pitch. NFL/NHL/NBA rows are unvalidated paper signals: state the price, not a pick.")
    embeds = [{"title": f"{p['sport'].upper()} | {p['player']}",
               "description": f"{_code(p['snippet'])}\n[Search X for {p['player']} (live, {REPLY_MIN_FAVES}+ likes)]({p['search_url']})"}
              for p in plays]
    return post_embeds(hook, content, embeds)


def run_reply_ammo():
    feed = get_ev_feed()
    projections = {}
    for sp in ("nfl", "nhl", "nba"):
        try:
            projections[sp] = get_projections(sp)
        except Exception as e:  # noqa: BLE001 -- the projection mean is optional garnish
            print(f"  projections fetch failed for {sp}: {e}")
    plays = build_reply_ammo(feed, projections)
    if not plays:
        print("No credible price gaps right now -- no reply ammo.")
        return
    for i, p in enumerate(plays, 1):
        print(f"  [{i}] {p['sport']}: {p['snippet']}\n      {p['search_url']}")
    print(require_posted(post_reply_ammo_to_discord(plays)))


# ── Main ──────────────────────────────────────────────────────────────────────

def resolve_mode(argv=None):
    """--mode wins over TWEET_MODE. 'morning' is the multi-sport-era name for 'picks'."""
    argv = sys.argv[1:] if argv is None else argv
    mode = MODE
    if "--mode" in argv:
        i = argv.index("--mode")
        if i + 1 >= len(argv):
            raise ValueError("--mode needs a value")
        mode = argv[i + 1]
    return "picks" if mode == "morning" else mode


def main():
    mode = resolve_mode()
    print(f"[tweet_drafter] mode={mode} date={date.today()}")

    if mode == "settlement":
        run_settlement()
        return
    if mode == "reply_ammo":
        run_reply_ammo()
        return
    if mode == "steam":
        raise NotImplementedError("steam mode needs a public per-bet closing-line endpoint; not built yet")

    if mode == "picks":
        print("Fetching today's picks...")
        picks = get_today_picks()
        picks_list = picks if isinstance(picks, list) else picks.get("picks", [])

        if not picks_list:
            print("No picks today — skipping.")
            return

        prompt = build_picks_prompt(picks_list)
        label = "picks"
        reply_url = f"{BEEZY_SITE_URL}/picks"
        card_url = f"{BEEZY_SITE_URL}/api/og/picks-card"

    elif mode == "recap":
        print("Fetching recent settled bets and stats...")
        settled = get_recent_settled()
        settled_list = settled if isinstance(settled, list) else settled.get("picks", [])
        stats = get_stats()

        prompt = build_recap_prompt(settled_list, stats)
        label = "recap"
        reply_url = f"{BEEZY_SITE_URL}/results"
        recap_day = max((p.get("game_date") for p in settled_list if p.get("game_date")), default=None)
        card_url = f"{BEEZY_SITE_URL}/api/og/settlement-card?date={recap_day}&sport=all" if recap_day else None

        if not prompt:
            print("No settled bets today — skipping.")
            return

    else:
        raise ValueError(f"Unknown TWEET_MODE: {mode}")

    print(f"Generating tweets via Gemini...")
    tweets = generate_tweets(prompt)

    print(f"Generated {len(tweets)} drafts:")
    for i, t in enumerate(tweets, 1):
        print(f"  [{i}] {t}")

    print("Posting to Discord #tweet-queue...")
    print(require_posted(post_drafts_to_discord(label, tweets, card_url=card_url, reply_url=reply_url)))

    print("Done.")


if __name__ == "__main__":
    main()
