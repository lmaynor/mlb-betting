"""
tweet_drafter.py — beezy_fyi Twitter content automation
Runs as a Cloud Run job. Pulls picks/results from the beezy API,
generates tweet drafts via Gemini, pushes to Typefully as drafts.

Secrets (Secret Manager):
  site-api-key         -- beezy Cloud Run API key (already exists)
  gemini-api-key       -- Gemini free tier key
  typefully-api-key    -- Typefully API key (the existing key works fine
                           against the v2 API this file now calls -- see
                           TYPEFULLY_URL comment below, no rotation needed)

Modes (--mode or TWEET_MODE env; the env var is what the Cloud Run Jobs set):
  picks | morning  Gemini drafts for today's card -> Typefully  ("morning" is the new name for "picks")
  recap            Gemini drafts recapping the last settled slate -> Typefully
  settlement       Deterministic #GamblingX receipt post for the last settled slate: tweet text +
                   /api/og/settlement-card PNG + celebration GIF -> Discord (DISCORD_WEBHOOK_TWEETS,
                   falling back to DISCORD_WEBHOOK_OPS) for a 1-tap post on X. No Gemini, no Typefully.
  steam            NOT BUILT: needs a public per-bet closing-line endpoint that does not exist yet.

Schedule (recommended):
  Morning job (10:00 ET): picks tweet for today's slate
  Evening job (23:30 ET): recap tweet after settlement
"""

import os
import re
import sys
import json
import requests
from datetime import date, datetime

from mlb_core.social.gif_fetcher import fetch_celebration_gif

# ── Config ────────────────────────────────────────────────────────────────────

BEEZY_API_URL = os.environ.get("BEEZY_API_URL", "https://api.beezy.fyi").rstrip("/")
BEEZY_SITE_URL = os.environ.get("BEEZY_SITE_URL", "https://beezy.fyi").rstrip("/")
BEEZY_API_KEY = os.environ["SITE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TYPEFULLY_API_KEY = os.environ["TYPEFULLY_API_KEY"]

# gemini-2.0-flash was retired by Google (confirmed live 2026-09-01: 404 on
# generateContent, had been silently failing mlb-tweet-picks/mlb-tweet-recap
# for days). Using the generic "-latest" alias instead of a dated model id
# so this doesn't recur -- verified against the live /v1beta/models listing
# that this alias currently exists and supports generateContent.
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-flash-latest:generateContent"
)

# Typefully disabled v1 API-key access entirely (confirmed live 2026-09-02:
# every push returned 403 "API v1 access via API keys is disabled. Please
# update your integration to API v2"). Migrated to v2 per Typefully's own
# migration guide (https://support.typefully.com/en/articles/13133296).
# The EXISTING `typefully-api-key` secret authenticates fine against v2
# despite Typefully's docs saying "v1 keys cannot be used with v2" (no new
# key needed in practice) -- confirmed live via a real GET /social-sets +
# POST /drafts round-trip. See
# docs/solutions/integration-issues/typefully-api-v1-sunset.md.
TYPEFULLY_URL = "https://api.typefully.com/v2/social-sets"

MODE = os.environ.get("TWEET_MODE", "picks")  # picks|morning|recap|settlement (see module docstring)
SETTLEMENT_SPORTS = ("mlb", "nhl", "nba")

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
Always include beezy.fyi in at least one tweet variant.

Format rules:
- Max 280 characters per tweet
- Return exactly 3 tweet variants as a JSON array of strings
- No markdown, no explanations outside the JSON
- Each variant should have a slightly different angle (price dislocation / season ledger / understated)

Example good tweet:
"DraftKings is 23 cents off the market on Stanton HR right now. 12-book fair
is -108, DK hanging +115 (+7.1% EV). Full board -> beezy.fyi/ev"

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
        "math IS the content; (2) end with the daily card link "
        f"{BEEZY_SITE_URL}/cheat-sheet on its own line (Twitter unfurls the "
        "card image from it -- do not describe the link, just include it); "
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
        f"the season sample size. Link {BEEZY_SITE_URL}/results on its own "
        "line. Under 270 characters, max 1 hashtag, no emojis. "
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

    return filter_voice(json.loads(raw.strip()))


# ── Push to Typefully ─────────────────────────────────────────────────────────

def _typefully_headers():
    return {
        "Authorization": f"Bearer {TYPEFULLY_API_KEY}",
        "Content-Type": "application/json",
    }


def _get_social_set_id():
    """v2 groups connected platform accounts into "social sets"; drafts
    post under one. This account has a single connected X handle
    (@beezy_fyi) -- match on username, falling back to the first result if
    that ever misses (e.g. a rename)."""
    resp = requests.get(TYPEFULLY_URL, headers=_typefully_headers(), timeout=10)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise RuntimeError("Typefully v2: GET /social-sets returned no results")
    for r in results:
        if r.get("username") == "beezy_fyi":
            return r["id"]
    return results[0]["id"]


def push_to_typefully(tweets, label):
    """Push each tweet as a separate draft in Typefully (API v2 -- see the
    TYPEFULLY_URL comment above for why)."""
    try:
        social_set_id = _get_social_set_id()
    except Exception as e:
        return [f"  Typefully social-set lookup FAILED: {e}"]

    url = f"{TYPEFULLY_URL}/{social_set_id}/drafts"
    pushed = []
    for i, tweet in enumerate(tweets, 1):
        # "enabled" is required -- confirmed live 2026-09-02: Typefully's
        # own docs example omits it, but the real API 422s without it
        # ("Field required", field "platforms.x.enabled").
        payload = {"platforms": {"x": {"enabled": True, "posts": [{"text": tweet}]}}}
        resp = requests.post(url, headers=_typefully_headers(), json=payload, timeout=10)

        if resp.status_code == 201:
            draft = resp.json()
            pushed.append(f"  Draft {i} pushed ({draft.get('private_url', 'no url')}): {tweet[:60]}...")
        else:
            pushed.append(f"  Draft {i} FAILED ({resp.status_code}): {resp.text[:200]}")

    return pushed


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
    Returns {date, w, l, p, net, hero, text, card_url}; text is the ready-to-post #GamblingX tweet."""
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
        text = f"Dropped {abs(net):.2f}u on {day} card ({w}-{l}{f'-{p}' if p else ''}).{ledger} Every play logged publicly. {BEEZY_SITE_URL}/results"
    else:
        best = ""
        if hero:
            who = hero.get("player") or hero.get("bet_type", "").replace("_", " ")
            best = f" Best price: {who} {int(hero['odds']):+d} ({_u(float(hero.get('profit') or 0))})." if hero.get("odds") is not None else ""
        text = f"{day} card: {w}-{l}{f'-{p}' if p else ''}, {_u(net)}.{best}{ledger} Receipts: {BEEZY_SITE_URL}/results"
    return {"date": day, "w": w, "l": l, "p": p, "net": net, "hero": hero, "text": text, "card_url": card_url}


def post_settlement_to_discord(summary, gif):
    """Post tweet text + receipt PNG + GIF to the tweet-queue webhook so the operator can post both to X in one go."""
    hook = os.environ.get("DISCORD_WEBHOOK_TWEETS") or os.environ.get("DISCORD_WEBHOOK_OPS")
    if not hook:
        return "  Discord post SKIPPED: neither DISCORD_WEBHOOK_TWEETS nor DISCORD_WEBHOOK_OPS is set"
    content = f"**Settlement tweet draft ({summary['date']})**\n```\n{summary['text']}\n```"
    embeds = [{"title": f"Settlement receipt {summary['date']}", "image": {"url": summary["card_url"]}}]
    if gif.get("gif_url"):
        content += f"\nCelebration GIF ({gif['query']}): {gif['gif_url']}"
        embeds.append({"title": gif["query"], "image": {"url": gif.get("preview_url") or gif["gif_url"]}})
    else:
        content += f"\nNo GIF resolved for \"{gif['query']}\" (no TENOR_API_KEY/GIPHY_API_KEY): {gif.get('search_url', '')}"
    resp = requests.post(hook, json={"content": content, "embeds": embeds}, timeout=10)
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
    print(post_settlement_to_discord(summary, gif))


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

    elif mode == "recap":
        print("Fetching recent settled bets and stats...")
        settled = get_recent_settled()
        settled_list = settled if isinstance(settled, list) else settled.get("picks", [])
        stats = get_stats()

        prompt = build_recap_prompt(settled_list, stats)
        label = "recap"

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

    print("Pushing to Typefully...")
    results = push_to_typefully(tweets, label)
    for r in results:
        print(r)

    print("Done.")


if __name__ == "__main__":
    main()
