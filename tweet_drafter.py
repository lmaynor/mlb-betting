"""
tweet_drafter.py — beezy_fyi Twitter content automation
Runs as a Cloud Run job. Pulls picks/results from the beezy API,
generates tweet drafts via Gemini, pushes to Typefully as drafts.

Secrets (Secret Manager):
  site-api-key         -- beezy Cloud Run API key (already exists)
  gemini-api-key       -- Gemini free tier key
  typefully-api-key    -- Typefully API key. MUST be a v2 key (Settings ->
                           API in the Typefully dashboard) -- v1 keys are
                           rejected outright by the v2 endpoints this file
                           now calls. See TYPEFULLY_URL comment below.

Schedule (recommended):
  Morning job (10:00 ET): picks tweet for today's slate
  Evening job (23:30 ET): recap tweet after settlement
"""

import os
import json
import requests
from datetime import date, datetime

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
# migration guide (https://support.typefully.com/en/articles/13133296) --
# but this is UNVERIFIED against the real API: v1 keys cannot be used with
# v2 at all, and the only key available at migration time (`typefully-api-
# key` in Secret Manager) is a v1 key. Needs a real v2 key generated from
# the Typefully dashboard before this can be confirmed working end to end.
# See docs/solutions/integration-issues/typefully-api-v1-sunset.md.
TYPEFULLY_URL = "https://api.typefully.com/v2/social-sets"

MODE = os.environ.get("TWEET_MODE", "picks")  # "picks" or "recap"

# ── Brand voice system prompt ─────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are the voice of @beezy_fyi on Twitter — a model-driven MLB betting
analytics account. beezy.fyi publishes quantitative MLB picks across game
markets, pitcher props, and batter props with edge %, Kelly stake, and full
results history. Paper mode now; launching when validation gates are cleared.

Voice: confident but not loud. Data-first. Let the numbers do the talking.
Occasionally explain *why* the edge exists (1 sentence max). No hype, no
emojis beyond one understated use, no "LOCK", no "CASH IT 🔥". Think
Bloomberg terminal meets someone who actually knows what they're doing.

Audience: sports bettors who are tired of tout accounts, want to see real
edge, and are curious how quant models apply to betting markets.

Goal: build credibility and followers who will convert to beezy.fyi members.
Always include beezy.fyi in at least one tweet variant.

Format rules:
- Max 280 characters per tweet
- Return exactly 3 tweet variants as a JSON array of strings
- No markdown, no explanations outside the JSON
- Each variant should have a slightly different angle (data-first / educational / understated)

Example good tweet:
"HR model flagged Stanton today: 62% vs market's 54%. +8.3% edge, 0.4u Kelly. 
Running walk-forward since 2021. beezy.fyi"

Example bad tweet:
"STANTON IS A LOCK TODAY 🔥🔥 BET THE HOUSE #MLB"
"""

# ── Fetch beezy data ──────────────────────────────────────────────────────────

def get_today_picks():
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/picks/today",
        headers={"X-API-Key": BEEZY_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_recent_settled():
    resp = requests.get(
        f"{BEEZY_API_URL}/api/public/picks/recent",
        headers={"X-API-Key": BEEZY_API_KEY},
        params={"limit": 10},
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

    tweets = json.loads(raw.strip())
    return tweets


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
        payload = {"platforms": {"x": {"posts": [{"text": tweet}]}}}
        resp = requests.post(url, headers=_typefully_headers(), json=payload, timeout=10)

        if resp.status_code == 201:
            draft = resp.json()
            pushed.append(f"  Draft {i} pushed ({draft.get('private_url', 'no url')}): {tweet[:60]}...")
        else:
            pushed.append(f"  Draft {i} FAILED ({resp.status_code}): {resp.text[:200]}")

    return pushed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[tweet_drafter] mode={MODE} date={date.today()}")

    if MODE == "picks":
        print("Fetching today's picks...")
        picks = get_today_picks()
        picks_list = picks if isinstance(picks, list) else picks.get("picks", [])

        if not picks_list:
            print("No picks today — skipping.")
            return

        prompt = build_picks_prompt(picks_list)
        label = "picks"

    elif MODE == "recap":
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
        raise ValueError(f"Unknown TWEET_MODE: {MODE}")

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
