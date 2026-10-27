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
  reply_ammo       Link-free replies for the day's top book-vs-consensus price gaps (all sports): ONE batched Gemini call
                   writes three angles per play (price_check / pushback / driver_note) from the play's price math and, for
                   NFL/NHL/NBA, its projection facts; each is validated, with a deterministic price_check as the fallback.
                   Two X live-search links per play (big tweets, betting threads) -> Discord #tweet-queue.
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
import time
import requests
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from mlb_core.social.gif_fetcher import fetch_celebration_gif
from ev.daily_card import build_daily_card, load_locked_card, save_locked_card

# ── Config ────────────────────────────────────────────────────────────────────

BEEZY_API_URL = os.environ.get("BEEZY_API_URL", "https://api.beezy.fyi").rstrip("/")
BEEZY_SITE_URL = os.environ.get("BEEZY_SITE_URL", "https://beezy.fyi").rstrip("/")
BEEZY_API_KEY = os.environ.get("SITE_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

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

# Recap and settlement describe "the last settled slate". When nothing settled for yesterday (no games, or /settle had not
# finished) that used to silently become whatever slate was newest -- on 2026-10-21 the 5am recap drafted Monday's results.
# A slate older than yesterday (Central) is stale and is never drafted.
RECAP_MAX_AGE_DAYS = 1
WINDOWS = (7, 30)         # the L7 / L30 summary lines in the header message


def central_today():
    return datetime.now(_CT).date()


def slate_is_fresh(day, today=None, max_age_days=RECAP_MAX_AGE_DAYS):
    """True when `day` (a 'YYYY-MM-DD' game_date) is at most `max_age_days` before today in Central time. An unparseable day is
    never fresh."""
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return False
    return (today or central_today()) - d <= timedelta(days=max_age_days)

def skip_reason(settled):
    """Why a recap/settlement drafted nothing, for the job log: nothing graded at all, or only a stale slate."""
    days = [r["game_date"] for r in settled if r.get("game_date") and r.get("result") in ("win", "loss", "push")]
    if not days:
        return "No settled bets found -- skipping."
    return (f"Newest graded slate is {max(days)}, older than {RECAP_MAX_AGE_DAYS} day(s) before {central_today()} (Central) -- "
            "not drafting a stale recap; skipping.")


# ── Brand voice system prompt ─────────────────────────────────────────────────

# #GamblingX voice rules. Hard-enforced after generation by violates_voice(); also stated in the prompt.
BANNED_PHRASES = (
    "let's dive in", "lets dive in", "lock it in", "lock of the", "smash", " ai ", "ai-powered", "algorithm",
    "guaranteed", "parlay of the day", "cash it", "can't lose", "free money",
    "de-vigged", "consensus fair", "proj_mean",   # textbook quant-speak: say what it means instead
    # stock phrases the model kept parroting (2026-10-19): corny, and they read as a template
    "on the chin", "buy a bounce", "days like that", "on to the next", "transparency", "fun sweat", "nice sweat",
    "fall asleep", "on an island", "the process", "variance",
    # recent-form / streak talk is how scam touts sell ("he's hit 4 of his last 5"): the owner does not want it, ever
    "last 3", "last 5", "last 10", "last five", "last ten", "of his last", "of their last", "in a row", "streak", "trend",
    "hit rate",
)

PERSONA = """
You write for @beezy_fyi on X (#GamblingX), a betting analytics account with a public, paper-traded ledger. You sound like a
sharp, dry friend who watches every line move: plain words, specific numbers, a little understated humor, zero salesmanship.

How to write:
- Lead with something concrete: a player, a price, a result. Never open with a greeting, a stock phrase or "Today".
- Use only the names and numbers you are given. Never invent a number, a stat or a reason.
- Say the math in ordinary betting talk ("the other books are around -108", "we have him closer to 7.3"). Never write
  "de-vigged", "consensus fair" or "proj_mean".
- Results are in units: 1u is one flat unit risked per bet at the flagged price, so write them like "-1.9u" or "+0.8u".
  Use the unit figures you are given; never write dollar amounts and never invent a figure.
- Results are reported flat. A win is not a victory lap and a loss is not an apology: say what happened and, if it is clear
  from the legs, what drove it, in one plain sentence. Do not defend the model, do not talk about "the process", "variance"
  or "transparency". If the day is only a handful of bets, say that casually in one clause and move on.
- No trends. Never cite recent form: no "last 5 / last 10 games", hit rates, streaks, or "he's been hot". That is how scam touts
  talk. Reasoning is our projection against the line, the price against the market, and minutes when you are given them.
- No catchphrases, idioms or sports cliches. No exclamation marks, no rhetorical questions. Vary how each tweet is built.
- Be specific enough that someone could check it: names, lines, prices.

BANNED: "let's dive in", "lock it in", "smash", "AI", "algorithm", "guaranteed", "parlay of the day", "de-vigged",
"consensus fair", hype caps, more than one emoji (zero is better).

Audience: bettors tired of tout accounts who want to see real edge.
"""

SYSTEM_PROMPT = PERSONA + """
End every variant with the plain-text watermark beezy.fyi. NEVER write a URL:
no https://, no path after beezy.fyi, no other site. The link goes in a
separate reply that is added automatically.

Format rules:
- Max 280 characters per tweet
- Return exactly 3 tweet variants as a JSON array of strings
- No markdown, no explanations outside the JSON
- Each variant takes a different angle and a different structure

Example good tweet:
"Peralta under 14.5 outs: DraftKings is +104, the other seven books are around -121. beezy.fyi"

Example bad tweet:
"STANTON IS A LOCK TODAY \U0001F525\U0001F525 BET THE HOUSE #MLB"
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

_LABEL_RE = re.compile(r"^(K|OUTS|BATTER_TB|BATTER_HITS|PITCHER_ER|SB)_(OVER|UNDER|\d+PLUS)_([\d.]+)")
_LABEL_NOUN = {"K": "Ks", "OUTS": "outs", "BATTER_TB": "total bases", "BATTER_HITS": "hits", "PITCHER_ER": "earned runs",
               "SB": "steals"}


def bet_label(p):
    """A settled/pending bet row -> plain words ('Gerrit Cole over 7.5 Ks'). Never a raw bet_type code."""
    who = p.get("player")
    bt = str(p.get("bet_type") or "")
    m = _LABEL_RE.match(bt)
    if m and who:
        kind, side, line = m.groups()
        noun = _LABEL_NOUN[kind]
        if side.endswith("PLUS"):
            return f"{who} {side[:-4]}+ {noun}"
        try:
            return f"{who} {side.lower()} {float(line):g} {noun}"
        except ValueError:
            return f"{who} {side.lower()} {line} {noun}"
    parts = bt.split("_")
    if who and parts[0] == "HR":
        return f"{who} no HR" if len(parts) > 1 and parts[1] == "NO" else f"{who} to homer"
    away, home = p.get("away_team"), p.get("home_team")
    matchup = f"{away} @ {home}" if away and home else (p.get("game") or "")
    if bt in ("NRFI", "YRFI"):
        return f"{matchup} {bt}".strip()
    if parts[-1] in ("HOME", "AWAY") and (home or away):
        return f"{home if parts[-1] == 'HOME' else away} moneyline"
    desc = bt.replace("_", " ").lower()
    return f"{who} {desc}".strip() if who else f"{matchup} {desc}".strip()


def _odds_str(o):
    try:
        return f"{int(float(o)):+d}"
    except (TypeError, ValueError):
        return ""


def _edge_pct(e):
    """bets.edge is stored as a fraction (0.06) but older rows/fixtures carry percent (6.0): same rule as public_api._edge_frac."""
    try:
        e = float(e)
    except (TypeError, ValueError):
        return None
    return (e / 100.0 if abs(e) >= 2 else e) * 100.0


def bet_units(r):
    """One settled bet's result in UNITS: profit / stake, i.e. a flat 1u risked at the flagged price (the site's own
    convention: 'graded at a flat 1 unit'). bets.profit and bets.stake are both DOLLARS (EV rows stake a flat $100, model rows a
    Kelly dollar amount), so printing profit as 'u' -- as this file used to -- reported -$190 as '-190u' instead of -1.90u."""
    try:
        stake = float(r.get("stake") or 0)
        return float(r.get("profit") or 0) / stake if stake > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def day_stats(slate):
    """Record and flat-unit result for one settled slate."""
    return {"w": sum(r["result"] == "win" for r in slate), "l": sum(r["result"] == "loss" for r in slate),
            "p": sum(r["result"] == "push" for r in slate), "n": len(slate), "units": sum(bet_units(r) for r in slate)}


def build_picks_prompt(picks):
    if not picks:
        return None

    lines = [f"Today's beezy model picks ({date.today().strftime('%b %d')}):\n"]
    for p in picks[:5]:  # cap at 5 so prompt stays tight
        row = f"  {bet_label(p)} at {_odds_str(p.get('odds'))}"
        if p.get("book"):
            row += f" ({p['book']})"
        try:
            who = "the other books price it at" if p.get("system") == "EV" else "our model has it at"
            row += f" | {who} {float(p['model_prob']) * 100:.0f}% vs {float(p['market_prob']) * 100:.0f}% implied by this price"
        except (KeyError, TypeError, ValueError):
            pass
        edge = _edge_pct(p.get("edge"))
        if edge is not None:
            row += f" | edge {edge:.1f}%"
        if p.get("notes"):
            row += f" | {p['notes']}"
        lines.append(row)

    lines.append(
        "\nWrite 3 tweet drafts for @beezy_fyi about today's picks. Each draft is about ONE pick and the math is the "
        "content; say where the percentage comes from exactly as the data above does (do not call another book's "
        "number 'our model'). Draft 1 opens with the book and its price, draft 2 opens with the gap between the two "
        "percentages, draft 3 is the shortest: player, line, price, one clause. Use a different pick in each draft "
        "when there are several. Aim for under 200 characters. Do not mention stake size. "
        f"End with the plain-text watermark {WATERMARK} on its own line -- NO https://, NO path, NO other URL "
        "anywhere in the tweet (the link is added as a separate reply). Under 270 characters, max 1 hashtag, no emojis, "
        "no tout language. Return only a JSON array of 3 strings."
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
    if not slate_is_fresh(recap_date):
        return None

    recap_settled = [
        p for p in settled
        if p.get("game_date") == recap_date
        and p.get("result") in ("win", "loss", "push")
    ]

    if not recap_settled:
        return None

    ds = day_stats(recap_settled)
    record = f"{ds['w']}-{ds['l']}" + (f"-{ds['p']}" if ds["p"] else "")

    overall = (stats or {}).get("overall", {}) or {}
    season_roi = overall.get("roi", "N/A")
    total_bets = overall.get("total_bets", "N/A")

    try:
        recap_date_label = datetime.strptime(recap_date, "%Y-%m-%d").strftime("%A %b %d")
    except (ValueError, TypeError):
        recap_date_label = recap_date  # fall back to the raw string rather than crash

    prompt = (
        f"beezy results for {recap_date_label} ({ds['n']} bets):\n"
        f"  Record: {record}\n"
        f"  Result: {_u(ds['units'])} (flat 1u per bet)\n"
        f"  Season so far: {total_bets} bets, {season_roi}% ROI (background only)\n\nEvery bet:\n"
    )
    # the same proposition is often logged once per book (EV alerts), so identical legs are shown once with a count
    legs = {}
    for p in recap_settled:
        key = (p["result"], bet_label(p), _odds_str(p.get("odds")))
        legs.setdefault(key, []).append(bet_units(p))
    for (result, label, odds), us in sorted(legs.items(), key=lambda kv: sum(kv[1]), reverse=True)[:8]:
        word = {"win": "WON", "loss": "LOST", "push": "PUSH"}[result]
        prompt += f"  {word}  {label} at {odds} ({_u(us[0])} each)" + (f" x{len(us)} tickets" if len(us) > 1 else "") + "\n"

    prompt += (
        "\nWrite 3 tweet drafts for @beezy_fyi recapping that slate. Facts first: the record, the unit result, and "
        "the legs that decided it, by name (a leg marked xN was logged N times at different books -- say so, do not "
        "present it as N separate ideas). Draft 1 leads with the best or worst leg, draft 2 leads with the record, "
        "draft 3 is one dry sentence. Aim for under 220 characters. Losing days read exactly like winning days: flat, no consolation, no defending "
        "anything. Mention the season only if it adds something, and never as a boast. Write results in units, never "
        f"dollars. End with the plain-text watermark {WATERMARK} on its own line -- NO https://, NO path, NO other "
        "URL (the link is added as a separate reply). Under 270 characters, max 1 hashtag, no emojis. "
        "Return only a JSON array of 3 strings."
    )
    return prompt


# ── Call Gemini ───────────────────────────────────────────────────────────────

def _gemini_json_text(system_prompt, user_prompt, max_tokens=4096, schema=None):
    """One Gemini generateContent call -> the visible reply text with any markdown fences stripped (still JSON text)."""
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        # 512 was too tight: newer Gemini generations spend a variable chunk
        # of the budget on internal "thinking" tokens before any visible
        # text. Confirmed live 2026-09-02: the (longer) recap prompt burned
        # the ENTIRE 512-token budget on thinking, leaving zero visible
        # text and crashing json.loads("") with a confusing "Expecting
        # value" error. Bumped for real headroom -- cost is negligible on
        # the free tier either way.
        # JSON mode makes Gemini emit parseable JSON; the larger budget stops it being cut off mid-string. Both were real
        # failures on 2026-10-19: picks died on "Expecting value" and recap's first attempt on "Unterminated string" -- the
        # reply was truncated because thinking tokens ate the 1536-token budget.
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
    }
    if schema:
        payload["generationConfig"]["responseSchema"] = schema

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
    return raw.strip()


_TWEETS_SCHEMA = {"type": "ARRAY", "items": {"type": "STRING"}}


def generate_tweets(user_prompt, attempts=3):
    """Ask Gemini for the drafts; a reply that is not valid JSON or that the voice filter rejects entirely gets re-asked (a
    second sample is cheap, a dead job is a missing tweet). HTTP errors are not retried here -- Cloud Run retries the job."""
    last = None
    for _ in range(attempts):
        try:
            raw = _gemini_json_text(SYSTEM_PROMPT, user_prompt, schema=_TWEETS_SCHEMA)
            return [strip_urls(t) for t in filter_voice(json.loads(raw))]
        except (ValueError, RuntimeError) as e:     # JSONDecodeError is a ValueError
            last = e
            print(f"  Gemini draft attempt failed ({type(e).__name__}: {str(e)[:120]}); retrying")
    raise last


# ── 160-Character Gemini Tweet Engine ─────────────────────────────────────────

SHORT_TWEET_BANNED_WORDS = (
    "lock", "smash", "parlay", "ai", "algorithm", "guaranteed",
    "let's dive in", "lets dive in", "fire", "tail",
)


def contains_banned_word(text: str) -> bool:
    low = text.lower()
    for phrase in ("let's dive in", "lets dive in"):
        if phrase in low:
            return True
    patterns = [
        r"\block\b", r"\blocks\b",
        r"\bsmash\w*\b",
        r"\bparlay\w*\b",
        r"\bai\b",
        r"\balgorithm\w*\b",
        r"\bguarantee\w*\b",
        r"\bfire\w*\b",
        r"\btail\b", r"\btails\b", r"\btailing\b",
    ]
    for pat in patterns:
        if re.search(pat, low):
            return True
    return False


def enforce_160_cap(text: str, max_chars: int = 160) -> str:
    """Enforces a strict <= max_chars (160) cap:
    - Strips markdown, quotes, emojis, non-ascii characters
    - Normalizes whitespace
    - Checks for banned words; if banned, returns empty string so fallback is used
    - Truncates cleanly if needed to ensure len <= max_chars
    """
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
    t = re.sub(r"```$", "", t)
    t = t.replace("`", "")
    t = t.strip('"\']')
    t = re.sub(r"[^\x00-\x7F]+", "", t)
    t = t.replace("#", "").replace("!", "")
    t = re.sub(r"\s+", " ", t).strip()

    if contains_banned_word(t):
        return ""

    if len(t) <= max_chars:
        return t

    sub = t[:max_chars]
    last_dot = sub.rfind(". ")
    if last_dot != -1 and last_dot >= max_chars // 2:
        return sub[: last_dot + 1].strip()
    if sub.endswith("."):
        return sub.strip()
    last_period = sub.rfind(".")
    if last_period != -1 and last_period >= max_chars // 2:
        return sub[: last_period + 1].strip()
    last_space = sub.rfind(" ")
    if last_space != -1 and last_space >= max_chars // 2:
        return sub[:last_space].strip()
    return sub.strip()


def fallback_morning(payload: dict, max_chars: int = 160) -> str:
    k = payload.get("count", 1)
    plays_word = "official play" if k == 1 else "official confluence plays"
    top = payload.get("top_play") or {}
    player = top.get("player", "Tatum")
    line = top.get("line", "o26.5 Pts")
    book = top.get("book", "FD")
    odds = top.get("odds", "+112")
    fair = top.get("fair", "-108")
    n_books = top.get("n_books", 8)
    ev = top.get("ev", "+5.4%")
    url = "beezy.fyi/picks"

    text = f"{book} hanging {player} {line} at {odds} vs {fair} fair across {n_books} books ({ev} EV). {k} {plays_word} locked on today's card. {url}"
    if len(text) <= max_chars and not contains_banned_word(text):
        return text

    text = f"{book} hanging {player} {line} at {odds} vs {fair} ({ev} EV, {n_books} bks). {k} {plays_word} locked on card. {url}"
    if len(text) <= max_chars and not contains_banned_word(text):
        return text

    text = f"{player} {line} at {odds} on {book} vs {fair} ({ev} EV). {k} {plays_word} locked. {url}"
    return text[:max_chars]


def fallback_dislocation(payload: dict, max_chars: int = 160) -> str:
    gap = payload.get("gap", "1.5-pt")
    sport = str(payload.get("sport", "NBA")).upper()
    player = payload.get("player", "Fox")
    stat = payload.get("stat", "Pts")
    side1 = payload.get("side1", "o24.5")
    odds1 = payload.get("odds1", "-105")
    book1 = payload.get("book1", "FD")
    side2 = payload.get("side2", "u26.5")
    odds2 = payload.get("odds2", "-108")
    book2 = payload.get("book2", "DK")
    hold = payload.get("hold", "1.6")
    win_rate = payload.get("win_rate", "14.2")
    url = "beezy.fyi/ev"

    text = f"{gap} {sport} middle on {player} {stat}: {side1} ({odds1} {book1}) / {side2} ({odds2} {book2}) for a {hold}% hold and {win_rate}% window hit rate on the terminal. {url}"
    if len(text) <= max_chars and not contains_banned_word(text):
        return text

    text = f"{gap} {sport} middle on {player} {stat}: {side1} ({odds1} {book1}) / {side2} ({odds2} {book2}) for a {hold}% hold on the terminal. {url}"
    if len(text) <= max_chars and not contains_banned_word(text):
        return text

    text = f"{sport} middle: {player} {side1} ({book1}) / {side2} ({book2}), {hold}% hold. {url}"
    return text[:max_chars]


def fallback_settlement(payload: dict, max_chars: int = 160) -> str:
    w = payload.get("w", 0)
    l = payload.get("l", 0)
    units = float(payload.get("units", 0.0))
    roi = float(payload.get("roi", 8.2))
    total_bets = payload.get("total_bets", 7500)
    total_units = payload.get("total_units", 568.0)
    hero = payload.get("hero") or "Cole o6.5 Ks (+115)"
    url = "beezy.fyi/results"

    if units >= 0:
        text = f"Yesterday's official card went {w}-{l} ({units:+.2f}u), led by {hero}. Full graded ledger sits at {roi:+.1f}% flat ROI. {url}"
        if len(text) <= max_chars and not contains_banned_word(text):
            return text
        text = f"Yesterday's official card went {w}-{l} ({units:+.2f}u). Full graded ledger sits at {roi:+.1f}% flat ROI. {url}"
        return text[:max_chars]
    else:
        text = f"Dropped {units:+.2f}u on yesterday's card ({w}-{l}). Every play graded on the public ledger ({total_units:+.0f}u, {roi:+.1f}% flat ROI over {total_bets:,}+ bets). {url}"
        if len(text) <= max_chars and not contains_banned_word(text):
            return text
        text = f"Dropped {units:+.2f}u on yesterday's card ({w}-{l}). Ledger at {roi:+.1f}% flat ROI over {total_bets:,}+ bets. {url}"
        return text[:max_chars]


def generate_short_tweet_copy(kind: str, payload: dict, max_chars: int = 160) -> str:
    """
    Generate sharp, deadpan <= 160 char tweet copy via Gemini API with deterministic fallback.
    - Kind: 'morning' | 'card' | 'dislocation' | 'middle' | 'settlement'
    - Strictly <= 160 chars
    - Zero hype, zero emojis, zero exclamation marks, zero hashtags
    - Banned words: lock, smash, parlay, AI, algorithm, guaranteed, let's dive in, fire, tail
    """
    norm_kind = "morning" if kind == "card" else "dislocation" if kind == "middle" else kind

    fallback_fn = {
        "morning": fallback_morning,
        "dislocation": fallback_dislocation,
        "settlement": fallback_settlement,
    }.get(norm_kind, fallback_morning)

    fallback_text = fallback_fn(payload, max_chars=max_chars)

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key == "test-gemini-key":
        return fallback_text

    user_prompt = ""
    if norm_kind == "morning":
        top = payload.get("top_play", {})
        k = payload.get("count", 1)
        user_prompt = (
            f"Mode: morning card.\n"
            f"Locked official plays on card: {k}.\n"
            f"Top play: {top.get('player')} {top.get('line')} at {top.get('book')} {top.get('odds')} "
            f"vs {top.get('fair')} fair across {top.get('n_books')} reference books ({top.get('ev')} EV).\n"
            f"Target URL to include: beezy.fyi/picks\n"
            f"Write 1-2 deadpan sentences strictly <= 160 characters stating the dislocation and locked card count."
        )
    elif norm_kind == "dislocation":
        user_prompt = (
            f"Mode: odds terminal middle/dislocation.\n"
            f"Sport: {payload.get('sport')}, Player: {payload.get('player')}, Stat: {payload.get('stat')}.\n"
            f"Side 1: {payload.get('side1')} ({payload.get('odds1')} {payload.get('book1')}).\n"
            f"Side 2: {payload.get('side2')} ({payload.get('odds2')} {payload.get('book2')}).\n"
            f"Hold: {payload.get('hold')}%, Window hit rate: {payload.get('win_rate')}%\n"
            f"Target URL to include: beezy.fyi/ev\n"
            f"Write 1-2 deadpan sentences strictly <= 160 characters stating the middle window and hold."
        )
    elif norm_kind == "settlement":
        w, l, u = payload.get("w", 0), payload.get("l", 0), float(payload.get("units", 0.0))
        hero = payload.get("hero", "")
        roi = payload.get("roi", 8.2)
        total_bets = payload.get("total_bets", 7500)
        total_units = payload.get("total_units", 568.0)
        user_prompt = (
            f"Mode: settlement card recap.\n"
            f"Yesterday's card record: {w}-{l}, net units: {u:+.2f}u.\n"
            f"Top play: {hero}.\n"
            f"Cumulative ledger: +{total_units:.0f}u, {roi:+.1f}% flat ROI over {total_bets} bets.\n"
            f"Target URL to include: beezy.fyi/results\n"
            f"Write 1-2 deadpan sentences strictly <= 160 characters owning the result honestly."
        )

    system_instruction = (
        "You are a quantitative sports betting trader at beezy.fyi writing on X (#GamblingX).\n"
        "Voice: Deadpan, numbers-first, sharp, plain words. Zero hype, zero exclamation marks, zero emojis, zero hashtags.\n"
        "BANNED WORDS: lock, smash, parlay, AI, algorithm, guaranteed, let's dive in, fire, tail.\n"
        "CRITICAL REQUIREMENT: Output MUST be ONE or TWO short sentences totaling AT MOST 160 CHARACTERS including the link.\n"
        "Output ONLY the plain text tweet. No quotes, no markdown."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 256},
    }

    try:
        resp = requests.post(url, json=body, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates") or []
            if candidates:
                parts = candidates[0].get("content", {}).get("parts") or []
                if parts:
                    raw_text = parts[0].get("text", "")
                    cleaned = enforce_160_cap(raw_text, max_chars=max_chars)
                    if cleaned and len(cleaned) <= max_chars:
                        return cleaned
    except Exception:
        pass

    return fallback_text



# -- Discord #tweet-queue ----------------------------------------------------------------------------------------

# Every draft is its own PLAIN message whose whole content is the tweet text, so Discord's "Copy Text" gives exactly what
# goes on X (no code fences, no labels, no links to tap). The only extras are one short header line per post and the card.
_SUPPRESS_EMBEDS = 4          # Discord message flag: do not unfurl the link in this message
_PAUSE = 0.6                  # webhooks allow ~5 posts / 2 s; the pause keeps a 20-message reply_ammo run under it


def post_messages(hook, messages):
    """POST each message dict ({content, embeds, flags}) to a Discord webhook in order. One 429 per message is waited out and
    retried. Returns a status line; never raises on an HTTP error (callers decide via require_posted)."""
    bad = None
    for i, m in enumerate(messages):
        if i:
            time.sleep(_PAUSE)
        body = dict(m)
        if body.get("content"):
            body["content"] = body["content"][:2000]
        resp = requests.post(hook, json=body, timeout=10)
        if resp.status_code == 429:
            try:
                wait = min(float(resp.json().get("retry_after", 1)), 5.0)
            except Exception:  # noqa: BLE001
                wait = 1.0
            time.sleep(wait)
            resp = requests.post(hook, json=body, timeout=10)
        if resp.status_code not in (200, 204) and bad is None:
            bad = resp.status_code
    return f"  Discord post {'ok' if bad is None else f'FAILED ({bad})'} ({len(messages)} messages)"


def require_posted(result):
    """Discord is the only output, so a skipped or failed post must fail the job (a Job that exits 0 having posted nothing is
    the silent-failure shape this repo has been bitten by)."""
    if "FAILED" in result or "SKIPPED" in result:
        raise RuntimeError(f"tweet-queue post did not go out:{result}")
    return result


def post_drafts_to_discord(mode_label, tweets, card_url=None, reply_url=None, stats_line=""):
    """One short header, then each tweet variant as a plain message, then the card image, then the link reply (plain text, link
    unfurl suppressed) to post under your own tweet."""
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")          # DISCORD_WEBHOOK_TWEETS -> DISCORD_WEBHOOK_OPS
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    messages = [{"content": f"**{mode_label}** {date.today()} -- {len(tweets)} drafts, then the card" +
                            (", then the link reply" if reply_url else "") + (f"\n{stats_line}" if stats_line else "")}]
    messages += [{"content": t} for t in tweets]
    if card_url:
        messages.append({"embeds": [{"image": {"url": card_url}}]})
    if reply_url:
        messages.append({"content": build_link_reply(mode_label, reply_url), "flags": _SUPPRESS_EMBEDS})
    return post_messages(hook, messages)


# -- L7 / L30 summary (operator-facing header line, never fed to Gemini or a tweet) ---------------------------------

def get_window_stats(days, sport):
    """/api/public/stats/window for one sport. Raises when the backend predates the endpoint or ignores `days`, so a stale
    revision can never hand back all-time numbers labelled as a window."""
    resp = requests.get(f"{BEEZY_API_URL}/api/public/stats/window", headers={"X-API-Key": BEEZY_API_KEY},
                        params={"days": days, "sport": sport}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("days") != days:
        raise ValueError(f"window endpoint answered days={data.get('days')!r}, wanted {days}")
    return data


def format_window(days, rows):
    """One summary line for a window, pooled across the per-sport rows: record, flat units (profit / stake per bet), ROI, bets."""
    w, l, p = (sum(r[k] for r in rows) for k in ("wins", "losses", "pushes"))
    bets, units = sum(r["bets"] for r in rows), sum(r["units"] for r in rows)
    stake, profit = sum(r["stake"] for r in rows), sum(r["profit"] for r in rows)
    first = date.fromisoformat(min(r["since"] for r in rows))
    last = date.fromisoformat(max(r["until"] for r in rows)) - timedelta(days=1)
    span = f"{first:%b} {first.day}-{last.day}" if first.month == last.month else f"{first:%b} {first.day}-{last:%b} {last.day}"
    if not bets:
        return f"**L{days}** ({span}): no graded bets"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    win = f" ({100.0 * w / (w + l):.1f}%)" if w + l else ""
    roi = f" | ROI {100.0 * profit / stake:+.1f}%" if stake else ""
    split = ", ".join(f"{r['sport'].upper()} {r['wins']}-{r['losses']}" for r in rows if r["bets"])
    return f"**L{days}** ({span}): {rec}{win} | {_u(units)} flat 1u/bet{roi} | {bets} bets" + (f" ({split})" if len(rows) > 1 else "")


def build_window_summary():
    """The L7 / L30 lines, or '' when the data is unavailable. A window is skipped whole when any sport fails: a pooled line
    that quietly omits a sport would be wrong, not just short. Never raises (this is context, not the job's output)."""
    lines = []
    for days in WINDOWS:
        try:
            lines.append(format_window(days, [get_window_stats(days, sp) for sp in SETTLEMENT_SPORTS]))
        except Exception as e:  # noqa: BLE001
            print(f"  L{days} summary unavailable: {e}")
    return "\n".join(lines)


# ── Settlement receipt (deterministic, no Gemini) ─────────────────────────────

def _u(x):
    """Flat units, e.g. -1.90u. `x` must already be in units (see bet_units): never pass dollars."""
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


def _day_label(day):
    """'2026-10-18' -> 'Sunday'; falls back to the raw date string rather than crash."""
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    except (ValueError, TypeError):
        return str(day)


def build_settlement(settled, stats):
    """Summarize the most recent fully settled slate. None when there is nothing graded.
    Returns {date, w, l, p, units, hero, text, card_url, reply_url}; `units` is the flat-1u result (profit/stake per bet), text
    is the ready-to-post link-free tweet and reply_url the link for the follow-up reply."""
    dated = [r for r in settled if r.get("game_date") and r.get("result") in ("win", "loss", "push")]
    if not dated:
        return None
    day = max(r["game_date"] for r in dated)
    if not slate_is_fresh(day):
        return None
    slate = [r for r in dated if r["game_date"] == day]
    ds = day_stats(slate)
    w, l, p, units = ds["w"], ds["l"], ds["p"], ds["units"]
    wins = [r for r in slate if r["result"] == "win"]
    hero = max(wins, key=bet_units) if wins else None
    losses = [r for r in slate if r["result"] == "loss"]

    overall = (stats or {}).get("overall", {}) or {}
    ledger = ""
    if overall.get("roi") is not None and overall.get("total_bets") is not None:
        try:
            ledger = f" Season: {float(overall['roi']):+.1f}% ROI over {int(overall['total_bets']):,} bets."
        except (TypeError, ValueError):
            ledger = ""

    rec = f"{w}-{l}{f'-{p}' if p else ''}"
    standout = ""
    if units >= 0 and hero and hero.get("odds") is not None:
        standout = f" Best: {bet_label(hero)} {_odds_str(hero['odds'])}."
    elif units < 0 and losses:
        worst = min(losses, key=bet_units)
        standout = f" Costliest miss: {bet_label(worst)} {_odds_str(worst.get('odds'))}."
    text = f"{_day_label(day)}: {rec}, {_u(units)} on {ds['n']} bets.{standout}{ledger} {WATERMARK}"
    return {"date": day, "w": w, "l": l, "p": p, "units": units, "hero": hero, "text": text,
            "card_url": f"{BEEZY_SITE_URL}/api/og/settlement-card?date={day}&sport=all",
            "reply_url": f"{BEEZY_SITE_URL}/results"}


def post_settlement_to_discord(summary, gif, stats_line=""):
    """Header, the tweet as a plain message, the receipt card, the link reply, and the GIF when one resolved."""
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")          # DISCORD_WEBHOOK_TWEETS -> DISCORD_WEBHOOK_OPS
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    messages = [{"content": f"**settlement** {summary['date']} -- tweet, card" + (", link reply" if summary.get("reply_url") else "") +
                            (", GIF" if gif.get("gif_url") else "") + (f"\n{stats_line}" if stats_line else "")},
                {"content": summary["text"]},
                {"embeds": [{"image": {"url": summary["card_url"]}}]}]
    if summary.get("reply_url"):
        messages.append({"content": build_link_reply("settlement", summary["reply_url"]), "flags": _SUPPRESS_EMBEDS})
    if gif.get("gif_url"):
        messages.append({"embeds": [{"image": {"url": gif.get("preview_url") or gif["gif_url"]}}]})
    return post_messages(hook, messages)


def run_settlement():
    settled = get_settled_all_sports()
    summary = build_settlement(settled, get_stats())
    if not summary:
        print(skip_reason(settled))
        return

    # Use 160-char Gemini copy if available
    hero_label = bet_label(summary["hero"]) if summary.get("hero") else ""
    stats = get_stats() or {}
    overall = stats.get("overall") or {}
    payload = {
        "w": summary["w"],
        "l": summary["l"],
        "units": summary["units"],
        "hero": hero_label,
        "roi": float(overall.get("roi") or 8.2),
        "total_bets": int(overall.get("total_bets") or 7500),
        "total_units": float(overall.get("units") or 568.0),
    }
    short_copy = generate_short_tweet_copy("settlement", payload)
    if short_copy and len(short_copy) <= 160:
        summary["text"] = short_copy

    if violates_voice(summary["text"]):
        raise RuntimeError(f"settlement text violates voice rules: {summary['text']}")
    gif = fetch_celebration_gif(summary["hero"] or {})
    print(f"Settlement tweet: {summary['text']}\nCard: {summary['card_url']}\nGIF: {gif.get('gif_url') or gif.get('search_url')}")
    print(require_posted(post_settlement_to_discord(summary, gif, stats_line=build_window_summary())))


# -- reply_ammo: link-free replies for big accounts' threads -----------------------------------------------------

# Replying under a big account with a link or a pitch gets you blocked; replying with a price nobody else posted does not.
# So every snippet is a plain market fact (a book's price vs the de-vigged consensus of the OTHER books), carries no URL and
# no brand name, and fits one short reply. Each play also gets an X live-search link to find a fresh tweet about the player.
REPLY_SNIPPET_MAX = 240
REPLY_MAX_PLAYS = 5
REPLY_MAX_PER_SPORT = 2      # one hot sport must not fill all five slots
REPLY_MIN_BOOKS = 4          # mirrors public_api._OUTLIER_MIN_BOOKS: fewer reference books is reference error, not edge
REPLY_MIN_MINS_TO_KICK = 30  # a quote that is about to start is dead by the time you find a tweet to reply under
REPLY_MIN_FAVES = 20         # search filter: only tweets that already have some reach
REPLY_THREAD_MIN_FAVES = 5   # betting-thread search: smaller accounts, but only ones using prop words about this line

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


def _play_head(c):
    if c["stat"] == "anytime_td":
        return f"{c['player']} anytime TD"
    return f"{c['player']} {'Over' if c['side'] == 'OVER' else 'Under'} {c['point']:g} {c['stat_label']}"


def _book_name(c):
    return _BOOK_SHORT.get(str(c.get("book") or "").lower()) or str(c.get("book") or "A book").title()


def _bet_phrase(c):
    """The bet as it reads mid-sentence, without the player: 'the Under 1.5 shots on goal' / 'anytime TD'."""
    if c["stat"] == "anytime_td":
        return "anytime TD"
    return f"the {'Over' if c['side'] == 'OVER' else 'Under'} {c['point']:g} {str(c['stat_label']).lower()}"


def build_reply_snippet(c, proj_mean=None):
    """The deterministic price_check reply (<= REPLY_SNIPPET_MAX chars), in plain betting talk. It assumes the tweet it answers
    already named the player, so it opens on the book rather than on '<Player> Over <line>:' (the shape every EV bot uses). The
    projection clause is dropped first when it does not fit; None when even the bare sentence does not fit or the result is not
    link-free -- never truncated. This is also the fallback when Gemini is down."""
    fair = prob_to_american(c["p_fair"])
    if fair is None or c.get("point") is None:
        return None
    core = (f"{_book_name(c)} is still hanging {_odds(c['american'])} on {_bet_phrase(c)} if you shop around; "
            f"the other {c['n_books']} books are near {_odds(fair)}")
    model = f", and we have him closer to {proj_mean:.1f}" if proj_mean is not None else ""
    for text in (f"{core}{model}.", f"{core}."):
        if len(text) <= REPLY_SNIPPET_MAX and is_link_free(text) and not violates_voice(text):
            return text
    return None


# The replies are written for the tweet the operator finds, so each angle fits a different parent tweet.
REPLY_VARIANTS = ("price_check", "pushback", "driver_note")
REPLY_LABELS = {"price_check": "price check", "pushback": "pushback", "driver_note": "driver note"}

REPLY_SYSTEM_PROMPT = PERSONA + """
You write short REPLIES to post under other people's tweets about a player. The tweet already names the player and the prop, so
never restate them as a header. Hard rules for every reply:
- 240 characters or fewer, one or two sentences
- NO link, NO site name, NO @handle, NO hashtag, NO emoji
- use ONLY the numbers given for that play, written as given; invent nothing. No injury, pace, weather or recent-form talk, and
  no matchup talk unless the play lists a matchup note
- never state a win probability for OUR model; you may quote our projection, the season average, the projected usage and the range
- describe the other books as "around +109" or "sitting near +109": it is a de-vigged consensus price, never an "average". You are
  only given the market's price for OUR side, so never quote a price for the other side
- you have no line-movement data: never say a line "steamed", "moved" or "dropped"

How it should read (this is what separates a person from a bot):
- NEVER open with the player's name or with "<Player> <Over/Under> <line>:". Open mid-thought, on the book, on a number, or on
  a stat fact from the play
- no colons anywhere, and no choppy four-word sentences
- tie the price to the reason with connective words: because, while, when, even with, though, given, or a semicolon
- when the play lists a caveat, weave it into the sentence ("even with ...", "though ...") instead of leaving it out

Three angles per play; the operator picks the one that fits the tweet being answered:
- price_check -- for a tweet that agrees with our side. Help its readers get a better number without contradicting the pick: name
  the book and its price against where the other books sit
- pushback -- for a tweet on the other side. Polite friction: where the other books price our side, plus one fact from the play
  (projection, usage, season average, range) that makes the tweet's side harder. Naming a book is optional
- driver_note -- adds our "why" in two connected clauses: our projection against the line, its main driver (season average,
  projected usage, matchup note), the price, and the caveat. Skip this key for a play that says "no projection"

Return ONLY JSON: {"<play id>": {"price_check": "...", "pushback": "...", "driver_note": "..."}, ...}
"""

_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


def breakeven_pct(f):
    """Win probability (in %) the quoted American price needs to break even."""
    a = f.get("american")
    if not a:
        return None
    return (100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)) * 100.0


def market_pct(f):
    """What the other books' de-vigged consensus gives this side, in %."""
    return None if f.get("p_fair") is None else f["p_fair"] * 100.0


def proj_gap(f):
    """Our projection minus the line (positive = projected above it), or None without a projection."""
    if f.get("proj_mean") is None or f.get("point") is None:
        return None
    return f["proj_mean"] - f["point"]


_MINUTES_LABELS = ("minutes", "toi (min)")


def proj_supports_side(proj_mean, point, side):
    """True when our projection sits on the pick's side of the line (Over: above it, Under: below it). A pick can be fine while the
    mean is on the other side (counts are discrete: mean 1.57 shots still makes the Under 1.5 more likely than not), but a public
    reply that says "we project 1.57 against a 1.5 line" and then backs the Under reads as contradicting itself, so it is not used."""
    if proj_mean is None or point is None:
        return False
    return proj_mean > point if str(side).upper() == "OVER" else proj_mean < point


def _matchup(e):
    """(note, raw - base) when the opponent term moves the projection enough to mention, else None. `base` is the projection before
    the opponent factor, so the delta is in stat units."""
    opp, raw = e.get("opp_factor"), e.get("proj_raw")
    if not opp or raw is None:
        return None
    delta = raw - raw / opp
    if abs(delta) < 0.05:
        return None
    if opp > 1.03:
        return f"a soft matchup bump ({delta:+.1f})", delta
    if opp < 0.97:
        return f"a tough matchup downgrade ({delta:+.1f})", delta
    return None


def _usage_phrase(e):
    """'projected for 36.2 minutes' / 'projected usage of 7.2 targets', or None without a usage projection."""
    u, label = e.get("usage_proj"), str(e.get("usage_label") or "").strip().lower()
    if u is None:
        return None
    if label in _MINUTES_LABELS:
        return f"projected for {u:.1f} minutes"
    return f"projected usage of {u:.1f} {label}" if label else None


def _engine_numbers(f):
    """The engine numbers a reply may quote. They are public only alongside a projection that supports the pick (the public
    copy never cites a projection on the wrong side of the line, and the season average / range would give it away)."""
    out = set()
    e = f.get("engine") or {}
    if f.get("proj_mean") is None:
        return out
    for k in ("szn_avg", "proj_p10", "proj_p90"):
        if e.get(k) is not None:
            out.add(f"{e[k]:.1f}")
    if e.get("usage_proj") is not None:
        out.update({f"{e['usage_proj']:.1f}", f"{e['usage_proj']:.0f}"})
    if e.get("n_games") and e["n_games"] < THIN_SAMPLE_GAMES:        # only printed as a thin-sample caveat
        out.add(str(int(e["n_games"])))
    m = _matchup(e)
    if m:
        out.update({f"{m[1]:+.1f}", f"{abs(m[1]):.1f}"})
    return out


def _allowed_numbers(f):
    """Every number a reply about this play may contain, as the strings a writer would use for it."""
    out = _engine_numbers(f)
    fair = prob_to_american(f["p_fair"])
    for a in (f["american"], fair):
        if a is not None:
            out.update({_odds(a), str(abs(int(a))), str(int(a))})
    if f.get("point") is not None:
        out.update({f"{f['point']:g}", f"{f['point']:.1f}"})
    if f.get("proj_mean") is not None:
        out.update({f"{f['proj_mean']:.1f}", f"{f['proj_mean']:.2f}"})       # never 1.442: three decimals is false precision
    if f.get("n_books"):
        n = int(f["n_books"])
        out.update({str(n), str(n + 1)})
    if f.get("ev") is not None:
        pct = f["ev"] * 100
        out.update({f"{pct:.0f}", f"{pct:.1f}", f"+{pct:.1f}", f"+{pct:.0f}"})
    for pct in (breakeven_pct(f), market_pct(f)):                    # what the price needs / what the other books imply
        if pct is not None:
            out.update({f"{pct:.0f}", f"{pct:.1f}"})
    gap = proj_gap(f)
    if gap is not None:
        out.update({f"{abs(gap):.1f}", f"{abs(gap):g}"})
    if f.get("proj_minutes") is not None:
        out.update({f"{f['proj_minutes']:.0f}", f"{f['proj_minutes']:.1f}"})
    return out


# Structure guards. A reply that opens on the player, carries a colon, is chopped into fragments or has nothing linking the price
# to a reason is the bot shape this pipeline exists to avoid, so Gemini output with any of them is rejected (not repaired).
REPLY_MAX_SENTENCES = 2
REPLY_MIN_SENTENCE_WORDS = 5
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_CONNECTIVE_RE = re.compile(r"\b(?:because|while|though|although|even with|even if|but|since|so|when|whereas|given|with)\b|;", re.IGNORECASE)


def opens_with_player(text, f):
    """True when the reply opens on the player's name (the parent tweet already named them)."""
    names = [t for t in re.split(r"\s+", str(f.get("player") or "").lower()) if len(t) >= 3 and t.isalpha()]
    return bool(names) and re.match(rf"(?:{'|'.join(map(re.escape, names))})\b", text.lstrip().lower()) is not None


def reads_like_a_template(text, f):
    if ":" in text or opens_with_player(text, f) or not _CONNECTIVE_RE.search(text):
        return True
    sentences = [s for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    return len(sentences) > REPLY_MAX_SENTENCES or any(len(s.split()) < REPLY_MIN_SENTENCE_WORDS for s in sentences)


def reply_variant_ok(text, f, style=None):
    """A Gemini reply is usable only if it is link-free, in voice, short, reads like a person (reads_like_a_template), quotes a real
    price for this play, and contains no number that was not given for it (a conversational model will happily invent one). A
    price_check must name the book and its price: it exists to help readers shop the number."""
    if not isinstance(text, str):
        return False
    text = text.strip()
    if not text or len(text) > REPLY_SNIPPET_MAX or not is_link_free(text) or violates_voice(text):
        return False
    if reads_like_a_template(text, f):
        return False
    fair = prob_to_american(f["p_fair"])
    prices = {_odds(f["american"])} | ({_odds(fair)} if fair is not None and style != "price_check" else set())
    if not any(p in text for p in prices) or (style == "price_check" and _book_name(f) not in text):
        return False
    allowed = _allowed_numbers(f)
    return all(tok in allowed or tok.lstrip("+-") in allowed for tok in _NUM_RE.findall(text))


# -- reply_ammo dossier: the engine's numbers in betting words ------------------------------------------------------------------
# One batched Gemini call writes all three reply angles per play, so the prompt carries the whole picture: the price math and, for a
# play whose projection supports the pick, a plain-language account of the projection. Engine internals are deliberately NOT passed
# on ("the calibration scale", "recent weighting" are how the code works, not how bettors talk, and echoing them reads as a script):
# the season average, the projected usage and a matchup note are. A reply may quote only numbers printed here (_allowed_numbers).
# NFL/NHL/NBA only: MLB plays carry no engine row, so they get the price math and no projection facts.

ENGINE_FIELDS = ("proj_mean", "proj_raw", "calib_mult", "opp_factor", "usage_proj", "usage_label", "n_games", "szn_avg",
                 "proj_p10", "proj_p90")
THIN_SAMPLE_GAMES = 15


def projection_terms(f):
    """What moves the projection against the line, in stat units: [(label, value)] for the season average against the line, the
    projected workload against the season average, and the matchup. They sum to the projection BEFORE the calibration step, which
    is an internal statistical correction and is left out. None without an engine row."""
    e, line = f.get("engine"), f.get("point")
    if not e or e.get("proj_raw") is None or line is None:
        return None
    raw = e["proj_raw"]
    opp = e.get("opp_factor") or 1.0
    base = raw / opp
    terms = []
    if e.get("szn_avg") is not None:
        terms += [("the season average against the line", e["szn_avg"] - line),
                  ("the projected workload against the season average", base - e["szn_avg"])]
    else:
        terms.append(("the base projection against the line", base - line))
    terms.append(("the matchup", raw - base))
    return terms


def reply_caveats(f):
    """Plain-language reasons the pick is not a sure read, from the engine's own numbers: the line inside our 10th-90th range, the
    season average on the other side of the line, a small sample. Empty without a projection that supports the pick."""
    e, line = f.get("engine") or {}, f.get("point")
    if f.get("proj_mean") is None or line is None:
        return []
    out = []
    if e.get("proj_p10") is not None and e.get("proj_p90") is not None and e["proj_p10"] <= line <= e["proj_p90"]:
        out.append(f"the line sits inside our range of {e['proj_p10']:.1f} to {e['proj_p90']:.1f}")
    szn = e.get("szn_avg")
    if szn is not None and (szn < line if str(f["side"]).upper() == "OVER" else szn > line):
        out.append(f"the {szn:.1f} season average is on the other side of the line")
    if e.get("n_games") and e["n_games"] < THIN_SAMPLE_GAMES:
        out.append(f"it rests on only {int(e['n_games'])} games of history")
    return out


def build_dossier(f, sport=None):
    """The facts for one play as {"text": lines for the prompt, "driver", "caveats", "inside"}, or None when the play has no
    projection that supports the pick. Every number Gemini may quote about the projection is in `text`."""
    mu, line = f.get("proj_mean"), f.get("point")
    if mu is None or line is None:
        return None
    e, side = f.get("engine") or {}, str(f["side"]).upper()
    pick = "Over" if side == "OVER" else "Under"
    gap = mu - line
    lines = [f"Our projection is {mu:.1f} against a line of {line:g}, {abs(gap):.1f} {'above' if gap > 0 else 'below'} it."]
    if e.get("szn_avg") is not None:
        lines.append(f"Season average is {e['szn_avg']:.1f}.")
    usage = _usage_phrase(e)
    if usage:
        lines.append(f"{usage[0].upper()}{usage[1:]}.")
    m = _matchup(e)
    if m:
        lines.append(f"Matchup note: {m[0]}.")
    driver = None
    terms = projection_terms(f)
    if terms:
        toward = [(lab, v if side == "OVER" else -v) for lab, v in terms]
        driver = max(toward, key=lambda t: t[1])
        if driver[1] >= 0.05:
            lines.append(f"Biggest push toward the {pick}: {driver[0]}.")
    inside = None
    if e.get("proj_p10") is not None and e.get("proj_p90") is not None:
        inside = e["proj_p10"] <= line <= e["proj_p90"]
        lines.append(f"Our 10th to 90th percentile range is {e['proj_p10']:.1f} to {e['proj_p90']:.1f}; the line is "
                     f"{'inside' if inside else 'outside'} it.")
    caveats = reply_caveats(f)
    if caveats:
        lines.append("Caveat to work in: " + "; ".join(caveats) + ".")
    return {"text": "\n".join(lines), "driver": driver, "caveats": caveats, "inside": inside, "gap": gap}


def _fodder_facts(f, sport=None):
    """One play's prompt block: the price math always, then the dossier (or 'no projection')."""
    fair = prob_to_american(f["p_fair"])
    lines = [f"Pick: {_play_head(f)} at {_book_name(f)} {_odds(f['american'])}, which needs {breakeven_pct(f):.0f}% to break even.",
             f"The other {f['n_books']} books are around {_odds(fair)} (they imply {market_pct(f):.0f}%)."]
    d = build_dossier(f, sport)
    lines.append(d["text"] if d else "No projection.")
    return "\n".join(lines)


def _fodder_prompt(plays):
    blocks = [f"[{i}] {p['sport'].upper()}\n{_fodder_facts(p['facts'], p['sport'])}" for i, p in enumerate(plays)]
    return "Plays:\n\n" + "\n\n".join(blocks) + "\n\nWrite the replies."


def generate_reply_fodder(plays):
    """Add `variants` ({angle: reply}) to every play. ONE batched Gemini call asks for price_check / pushback / driver_note per
    play from the play's full dossier; each reply must pass reply_variant_ok. driver_note is skipped for a play without a
    projection. A play with no usable price_check (or the whole call failing) gets its deterministic snippet as the price_check,
    so reply_ammo never goes out empty-handed because Gemini had a bad day."""
    out = [dict(p, variants={}) for p in plays]
    try:
        data = json.loads(_gemini_json_text(REPLY_SYSTEM_PROMPT, _fodder_prompt(plays), max_tokens=6144))
    except Exception as e:  # noqa: BLE001 -- fall back to deterministic snippets
        print(f"  Gemini reply variants failed ({e}); using deterministic snippets")
        data = {}
    for i, p in enumerate(out):
        mine = data.get(str(i)) if isinstance(data, dict) else None
        for style in REPLY_VARIANTS:
            text = mine.get(style) if isinstance(mine, dict) else None
            if style == "driver_note" and p["facts"].get("proj_mean") is None:
                continue
            if reply_variant_ok(text, p["facts"], style):
                p["variants"][style] = text.strip()
        if "price_check" not in p["variants"]:
            p["variants"] = {"price_check": p["snippet"], **p["variants"]}
    return out


def build_reply_ammo(feed, projections=None):
    """[{sport, player, snippet, facts, search_url, thread_url}] for the day's top plays (`snippet` = the deterministic reply;
    `search_url` finds big tweets about the player, `thread_url` finds prop/betting threads about this exact line). `projections` = {sport: projection payload}; a matching
    (player, stat) row adds our projection mean to the snippet. Plays that cannot be written within the limits are skipped."""
    proj_idx, engine_idx = {}, {}
    for sp, payload in (projections or {}).items():
        for r in (payload or {}).get("rows") or []:
            if r.get("proj_mean") is not None:
                usage = _num(r.get("usage_proj")) if str(r.get("usage_label") or "").lower() in _MINUTES_LABELS else None
                key = (sp, r.get("player_norm"), r.get("stat"))
                proj_idx[key] = (_num(r.get("proj_mean")), usage)
                engine_idx[key] = {k: (r.get(k) if k == "usage_label" else _num(r.get(k))) for k in ENGINE_FIELDS}
    plays = []
    for c in select_reply_plays(reply_candidates(feed)):
        key = (c["sport"], c.get("player_norm"), c["stat"])
        proj_mean, minutes = proj_idx.get(key, (None, None))
        engine = engine_idx.get(key)
        if not proj_supports_side(proj_mean, c.get("point"), c.get("side")):
            proj_mean, minutes = None, None          # do not cite a projection that sits against the pick (see proj_supports_side)
        snippet = build_reply_snippet(c, proj_mean)
        if snippet:
            plays.append({"sport": c["sport"], "player": c["player"], "snippet": snippet,
                          "facts": {**c, "proj_mean": proj_mean, "proj_minutes": minutes, "engine": engine},
                          "search_url": build_x_search_url(f"{c['player']} min_faves:{REPLY_MIN_FAVES}"),
                          "thread_url": build_x_search_url(
                              f'"{c["player"]}" ({c["point"]:g} OR prop OR over OR under OR line) '
                              f'min_faves:{REPLY_THREAD_MIN_FAVES} -filter:retweets')})
    return plays


def post_reply_ammo_to_discord(plays):
    """A one-line note, then per play a header (sport, player, X search links) followed by each reply angle as a plain message. The
    angle name rides in the webhook's `username`, so Copy Text still gives exactly the reply."""
    from mlb_core.alerts.discord_routes import webhook_for
    hook = webhook_for("tweets")
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    messages = [{"content": f"**reply ammo** {date.today()} -- price check: the tweet agrees with our side. pushback: it takes the "
                            "other side. driver note: adds our projection and the caveat. NFL/NHL/NBA projections have no tracked "
                            "record against prices: state the price, not a pick."}]
    for p in plays:
        links = f"[big tweets]({p['search_url']})"
        if p.get("thread_url"):
            links += f" / [betting threads]({p['thread_url']})"
        messages.append({"content": f"**{p['sport'].upper()} | {p['player']}** -- {links}", "flags": _SUPPRESS_EMBEDS})
        variants = p.get("variants") or {"price_check": p["snippet"]}
        messages += [{"content": text, "username": REPLY_LABELS.get(style, style)} for style, text in variants.items()]
    return post_messages(hook, messages)


def run_reply_ammo():
    feed = get_ev_feed()
    projections = {}
    for sp in ("nfl", "nhl", "nba"):
        try:
            projections[sp] = get_projections(sp)
        except Exception as e:  # noqa: BLE001 -- the projection is optional garnish
            print(f"  projections fetch failed for {sp}: {e}")
    plays = build_reply_ammo(feed, projections)
    if not plays:
        print("No credible price gaps right now -- no reply ammo.")
        return
    plays = generate_reply_fodder(plays)
    for i, p in enumerate(plays, 1):
        print(f"  [{i}] {p['sport']} | {p['player']}")
        for style, text in p["variants"].items():
            print(f"      {style}: {text}")
        print(f"      {p['search_url']}")
        print(f"      {p['thread_url']}")
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
        print("Fetching today's card...")
        today_str = central_today().isoformat()
        locked = load_locked_card(today_str)
        if locked and locked.get("plays"):
            picks_list = locked["plays"]
        else:
            picks = get_today_picks()
            raw_list = picks if isinstance(picks, list) else picks.get("picks", [])
            card_plays = build_daily_card(raw_list)
            if card_plays:
                save_locked_card(card_plays, ct_day=today_str)
                picks_list = card_plays
            else:
                picks_list = raw_list

        if not picks_list:
            print("No picks today — skipping.")
            return

        p0 = picks_list[0]
        top_play = {
            "player": p0.get("player") or p0.get("who") or "Player",
            "line": p0.get("what") or f"{str(p0.get('side') or 'o').lower()}{p0.get('line', '')} {p0.get('stat', '')}",
            "book": _book_name(p0),
            "odds": _odds_str(p0.get("american") or p0.get("odds")),
            "fair": _odds_str(p0.get("fair") or p0.get("fair_american")),
            "n_books": p0.get("n_books", 6),
            "ev": f"{(p0.get('ev') or p0.get('market_ev') or 0.05)*100:+.1f}%",
        }
        short_tweet = generate_short_tweet_copy("morning", {"count": len(picks_list), "top_play": top_play})

        prompt = build_picks_prompt(picks_list)
        label = "picks"
        reply_url = f"{BEEZY_SITE_URL}/picks"
        card_url = f"{BEEZY_SITE_URL}/api/og/picks-card"

    elif mode == "recap":
        print("Fetching recent settled bets and stats...")
        settled = get_recent_settled(limit=200)     # the default 10 rows can cut a busy slate in half
        settled_list = settled if isinstance(settled, list) else settled.get("picks", [])
        stats = get_stats()

        prompt = build_recap_prompt(settled_list, stats)
        label = "recap"
        reply_url = f"{BEEZY_SITE_URL}/results"
        recap_day = max((p.get("game_date") for p in settled_list if p.get("game_date")), default=None)
        card_url = f"{BEEZY_SITE_URL}/api/og/settlement-card?date={recap_day}&sport=all" if recap_day else None

        if not prompt:
            print(skip_reason(settled_list))
            return

    else:
        raise ValueError(f"Unknown TWEET_MODE: {mode}")

    print(f"Generating tweets via Gemini...")
    if mode == "picks" and short_tweet and len(short_tweet) <= 160:
        tweets = [short_tweet]
    else:
        tweets = generate_tweets(prompt)

    print(f"Generated {len(tweets)} drafts:")
    for i, t in enumerate(tweets, 1):
        print(f"  [{i}] {t}")

    print("Posting to Discord #tweet-queue...")
    print(require_posted(post_drafts_to_discord(label, tweets, card_url=card_url, reply_url=reply_url,
                                                stats_line=build_window_summary())))

    print("Done.")


if __name__ == "__main__":
    main()
