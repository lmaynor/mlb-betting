"""
mlb_core.odds.bestbook -- BestBook (bestbook.app) live odds client + normalizer.

BestBook is a cross-book odds aggregator (a direct beezy.fyi comp set /
OddsJam-shape product). It covers NFL + NBA + NHL + college basketball ('cbb', game lines only: probed 2026-11-03, no player-prop sections) -- NO MLB slate was observed
on probe day, in-season or out -- which is why this client lives in the shared
mlb_core.odds package (sport-agnostic infra, CONTEXT.md section 0) while any
consumer/runner belongs to a sport pillar (nfl/ first).

Transport is NOT a WebSocket and NOT a scrape, despite the page showing
"Loading prices..." over a static HTML shell. Confirmed via live probes
2026-10-30 (reading the site's own /assets/js/app.js, then hitting the
endpoints directly):

  1. GET {BASE}/            -> 200 HTML; set the `bb` token cookie (24 h)
                                until 2026-11. SINCE 2026-11 (OPS-01): the
                                homepage sets NO cookie for a plain HTTP
                                client. Token issuance moved behind a
                                Cloudflare Turnstile check in the browser:
                                the page POSTs the Turnstile token to /t and
                                only then is the `bb` cookie set (~1 h life,
                                per the site's own client comments). The
                                cookie is still the ONLY auth. No account, no
                                key -- but issuance is browser-only now, so
                                this client persists a browser-issued token
                                (TOKEN_STATE_KEY) and renews it via (2); see
                                the token-handling section below.
  2. GET {BASE}/t           -> 204 + fresh `bb` cookie (hourly renewal in the
                               web client). 403 = token expired -> a browser
                               must issue a new one (see 1). POST /t without
                               a valid Turnstile token also answers 403.
                                Without any cookie, /events returns the plain
                                text "Open https://bestbook.app to see this."
  3. GET {BASE}/events?...  -> text/event-stream (SSE / EventSource):
        first message  {"type": "snapshot", "rows": [...], "status": {...}}
        then           {"type": "changes",  "upsert": [...],
                                             "remove": [...], "status": {...}}
     - Slate:  /events?game=all&rows=0&src=direct
               Snapshot with rows=[] and status.slate.games[] = every game
               (key, away, home, start ISO-UTC, venues[]). ALWAYS use
               rows=0 for the slate -- the site's own comment says the full
               slate snapshot was 13 MB.
     - Game:   /events?game=<game_key>&src=direct   [&more=1 for the More tab]
               One game per stream. Snapshot is BIG: CHI@SEA 2026-11-02 was
               5.4 MB / 4,174 rows; ARI@DAL 2026-11-01 was 6,006 rows. Budget
               memory/bandwidth accordingly; never open one stream per game
               for a whole slate casually.
     - Game keys: NFL "2026-11-02-CHI-SEA" (date is the UTC game date in the
               key; the slate's `start` is authoritative), NHL keys prefixed
               "nhl:", NBA keys prefixed "nba:".
     - The SSE GET must send Accept-Encoding: identity -- with gzip
               negotiated, the stream stalls behind Cloudflare/urllib3
               buffering and the snapshot effectively never arrives
               (verified 2026-10-30). See _sse_messages().

Row shape (snapshot rows and changes upserts share it):
  {id, venue: "match", game, gameTeams: [away, home], section, family,
   familyTitle, rung, line, team, player, quotes: [...], venues,
   bestYes: {venue, price}, bestNo: {venue, price},
   arb: {yesVenue, noVenue, cost, fees, edge, ...} | None, edge, ts}
Each quote is one book's two-sided price on that rung:
  {venue, book, yes, no, yesFee, noFee, yesSz, noSz, yesNative, noNative,
   href, yesSince, noSince}
  - yes/no are IMPLIED-PROBABILITY PERCENTS (54.5454 == -120 American), NOT
    fractions and NOT American odds. Divide by 100 before utils math.
  - yesNative/noNative are the book's original odds strings ("-120");
    they may carry a Unicode minus (U+2212) -- clean_native() normalizes it.
  - yesFee/noFee: exchange fees, already modeled by BestBook (0 for books).
  - yesSz/noSz: available size on exchanges (None for books).
  - yesSince/noSince: epoch MILLISECONDS the price last changed.
Upstream note from the site's own code: FanDuel, Pinnacle and bet365 are
POLLED upstream by BestBook; every other venue streams. A quiet line from a
streaming venue means no change, not staleness -- use status feeds + *Since
before calling a price stale.

Other REST endpoints (same cookie, JSON, polled by the web client every 5 s
where noted): /prices?id=<row id> or ?id=match:<game_key> (pregame price
history for a line; stops at kickoff), /arbs (+ /arbs/history, /arbs/replay),
/outliers (+ /outliers/<id>), /freebets, /stats/data, /dfs (+ /dfs/lines,
/dfs/results). Only /prices is wrapped below; the rest are one fetch_json()
call away if a consumer ever needs them.

Deliberately OUT of v1 (do not add without a separate decision):
  - No odds_history / DB / GCS writes. This module fetches + normalizes into
    flat records; persistence mapping is a follow-up, modeled on the Kalshi
    -> history path.
  - No splicing into ParlayAPI/SGO events (the parlay_adapter pattern does
    NOT transfer: BestBook rows are already cross-book merged "match" rows,
    keyed by BestBook's own ids, not per-book events).
  - Pinnacle (pin) IS in the feed. sgo.OFFSHORE_BOOKS drops Pinnacle
    downstream; keep it here and let downstream policy decide.

Pure transforms (parse_game_key, parse_slate, normalize_row(s),
apply_changes, pct_to_*) do no network and are unit-testable offline,
matching parlay_adapter.py / kalshi.py. Network is requests-only (already in
requirements.txt); the SSE stream is just a long-lived GET, no websockets
dependency.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

BASE = "https://bestbook.app"

_session = requests.Session()
_session.headers["User-Agent"] = "beezy-bestbook/1.0"

# BestBook venue code -> (canonical book name, kind).
# Canonical names align with sgo.BOOK_CANONICAL where a name exists there
# (draftkings/fanduel/betmgm/betrivers/fanatics/hardrock/bet365/thescore/
# novig); the rest follow the same lowercase-no-spaces convention.
# kind: "book" (sportsbook) | "exchange" (prediction market / exchange --
# fees + sizes are real) | "dfs" (reference price only, NOT bettable --
# never present a DFS quote as an executable book price).
VENUES: dict[str, tuple[str, str]] = {
    "dk":         ("draftkings", "book"),
    "fd":         ("fanduel", "book"),
    "mgm":        ("betmgm", "book"),
    "pin":        ("pinnacle", "book"),
    "hr":         ("hardrock", "book"),
    "fan":        ("fanatics", "book"),
    "brs":        ("betrivers", "book"),
    "bally":      ("ballybet", "book"),
    "tsb":        ("thescore", "book"),
    "bwy":        ("betway", "book"),
    "b365":       ("bet365", "book"),
    "b105":       ("bet105", "book"),
    "rebet":      ("rebet", "book"),
    "kalshi":     ("kalshi", "exchange"),
    "kalship":    ("kalshi", "exchange"),   # Kalshi "protected" orders variant
    "polymarket": ("polymarket", "exchange"),
    "novig":      ("novig", "exchange"),
    "mm":         ("magicmarkets", "exchange"),
    "pp":         ("prizepicks", "dfs"),
    "ud":         ("underdog", "dfs"),
    "slp":        ("sleeper", "dfs"),
}

EXCHANGES = {c for c, (_, k) in VENUES.items() if k == "exchange"}
DFS_VENUES = {c for c, (_, k) in VENUES.items() if k == "dfs"}


class BestBookError(RuntimeError):
    """BestBook request/stream failure after the retry policy gave up (max attempts or overall deadline)."""


class TokenExpired(BestBookError):
    """/t answered 403: the bb cookie outlived its lifetime (~1 h since Nov 2026). Re-bootstrap."""


# ---------------------------------------------------------------------------
# Token handling (the bb cookie is the whole auth model)
# ---------------------------------------------------------------------------
#
# Since Nov 2026 the token can only be ISSUED to a browser that passes the
# site's Cloudflare Turnstile check (homepage POSTs the Turnstile token to
# /t; plain-HTTP GET / and GET /t set nothing and /t answers 403 -- the
# bb-live-* outage of 2026-11-05..13, OPS-01). Renewal of a live token is
# still a plain GET /t, so the token is persisted to a GCS state object and
# renewed across runs: one browser-issued seed keeps the hourly jobs alive
# as long as every run renews inside the token's lifetime. When the seed is
# dead, bootstrap fails with the gate named -- the remedy is a fresh seed
# planted in TOKEN_STATE_KEY (the cookie value from any browser that opened
# bestbook.app), not a code change. The token value is never logged.

TOKEN_STATE_KEY = "Odds/bestbook_live/token_state.json"
_last_persisted_token: str | None = None   # last value this process saved (write de-dup)


def _load_persisted_token() -> str | None:
    """The browser-issued bb token from the GCS state object, or None.

    Fully guarded: persistence is a route around the gated issuance, never a
    new failure mode -- any storage problem reads as 'no seed'."""
    try:
        from mlb_core.config import GCS_BUCKET
        if not GCS_BUCKET:
            return None
        from mlb_core import storage
        data = json.loads(storage.read_bytes(TOKEN_STATE_KEY).decode())
        value = data.get("bb") if isinstance(data, dict) else None
        return value if isinstance(value, str) and value else None
    except Exception as e:  # noqa: BLE001
        logger.debug("bestbook: no persisted token (%s)", type(e).__name__)
        return None


def _persist_token(value: str | None) -> None:
    """Save the live bb token so the next run renews instead of re-issuing."""
    global _last_persisted_token
    if not value or value == _last_persisted_token:
        return
    _last_persisted_token = value
    try:
        from mlb_core.config import GCS_BUCKET
        if not GCS_BUCKET:
            return
        from mlb_core import storage
        storage.write_bytes(
            json.dumps({"bb": value, "saved_at": datetime.now(timezone.utc).isoformat()}).encode(),
            TOKEN_STATE_KEY,
        )
        logger.info("bestbook: token persisted for renewal by the next run")
    except Exception as e:  # noqa: BLE001
        logger.debug("bestbook: token persist failed (%s)", type(e).__name__)


def bootstrap() -> None:
    """Get a live `bb` token cookie: renew the persisted browser-issued seed
    if there is one; else GET the homepage (which set the cookie directly
    until the Nov 2026 Turnstile gate, and remains the fallback)."""
    seed = _load_persisted_token()
    if seed is not None:
        _session.cookies.set("bb", seed, domain="bestbook.app", path="/")
        try:
            renew_token()
            return
        except BestBookError:
            _session.cookies.clear()      # dead seed: fall through to the homepage path
    r = _session.get(f"{BASE}/", timeout=30)
    r.raise_for_status()
    if _session.cookies.get("bb") is None:
        raise BestBookError(
            "homepage did not set a bb token cookie "
            "(since Nov 2026 bestbook.app issues the token only after a browser "
            "Turnstile check via POST /t; restore the feed by planting a "
            "browser-issued bb cookie in Odds/bestbook_live/token_state.json "
            "-- OPS-01)"
        )
    _persist_token(_session.cookies.get("bb"))


def renew_token() -> None:
    """GET /t (204 = renewed). Raises TokenExpired on 403."""
    if _session.cookies.get("bb") is None:
        bootstrap()
        return
    r = _session.get(f"{BASE}/t", timeout=30)
    if r.status_code == 403:
        raise TokenExpired("bb token expired")
    r.raise_for_status()
    _persist_token(_session.cookies.get("bb"))


def _ensure_token() -> None:
    try:
        renew_token()
    except TokenExpired:
        _session.cookies.clear()
        bootstrap()


# ---------------------------------------------------------------------------
# Pure helpers (no network -- unit-test these)
# ---------------------------------------------------------------------------

_GAME_KEY_RE = re.compile(
    r"^(?:(?P<sport>nfl|nhl|nba|cbb):)?(?P<date>\d{4}-\d{2}-\d{2})-"
    r"(?P<away>[A-Z]{2,6})-(?P<home>[A-Z]{2,6})$"
)


def parse_game_key(key: str) -> dict | None:
    """'2026-11-02-CHI-SEA' -> {sport:'nfl', date, away:'CHI', home:'SEA'}.
    NHL/NBA/CBB keys carry the sport prefix ('nhl:2026-10-30-NJD-VGK', 'cbb:2026-11-03-MAN-UK'); an
    unprefixed key is NFL (BestBook's original sport). None if unparseable."""
    m = _GAME_KEY_RE.match((key or "").strip())
    if not m:
        return None
    return {"sport": m["sport"] or "nfl", "date": m["date"],
            "away": m["away"], "home": m["home"]}


def parse_slate(status: dict) -> list[dict]:
    """Snapshot `status` -> normalized games for EVERY league present
    (status keys are 'slate' for NFL, 'slate:nhl', 'slate:nba', 'slate:cbb', ...).
    Each: {key, sport, date, away, home, start (ISO str), venues: [codes]}."""
    out = []
    for skey, sval in (status or {}).items():
        if not (skey == "slate" or skey.startswith("slate:")):
            continue
        for g in (sval or {}).get("games") or []:
            parsed = parse_game_key(g.get("key", ""))
            out.append({
                "key": g.get("key"),
                "sport": parsed["sport"] if parsed else None,
                "date": parsed["date"] if parsed else None,
                "away": g.get("away"), "home": g.get("home"),
                "start": g.get("start"),
                "venues": list(g.get("venues") or []),
            })
    return out


def pct_to_prob(pct) -> float | None:
    """BestBook percent (54.5454) -> probability fraction (0.545454).
    None for missing / out-of-range (0 and 100 are not real two-sided prices)."""
    try:
        p = float(pct) / 100.0
    except (TypeError, ValueError):
        return None
    return p if 0.0 < p < 1.0 else None


def prob_to_american(p: float | None) -> int | None:
    """Probability fraction -> American odds int (same shape as
    kalshi.prob_to_american). None if degenerate."""
    if p is None or p <= 0 or p >= 1:
        return None
    return round(100 * (1 - p) / p) if p < 0.5 else -round(100 * p / (1 - p))


def pct_to_american(pct) -> int | None:
    return prob_to_american(pct_to_prob(pct))


def clean_native(s) -> str | None:
    """A book's native odds string with the Unicode minus (U+2212) and
    stray whitespace normalized to ASCII ('-120'). None stays None."""
    if s is None:
        return None
    return str(s).replace("\u2212", "-").strip() or None


def _ms_to_iso(ms) -> str | None:
    """Epoch milliseconds -> ISO-8601 UTC string. None if missing/invalid."""
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0,
                                      tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def venue_info(code: str) -> tuple[str, str]:
    """(canonical, kind) for a venue code; unknown codes pass through as
    (code, 'book') -- BestBook adds venues without notice, and an unknown
    code should degrade to a labeled row, never crash the pull."""
    return VENUES.get(code or "", ((code or ""), "book"))


def normalize_row(row: dict) -> list[dict]:
    """One BestBook row -> one flat record PER QUOTE (per book).

    Record fields:
      row_id, game_key, sport, date, away, home (from gameTeams),
      section, family, family_title, rung, line, team, player,
      venue (BestBook code), book (canonical), kind (book|exchange|dfs),
      bettable (kind != 'dfs'),
      yes_prob / no_prob (fractions), yes_american / no_american (ints,
      derived -- prefer *_native for display), yes_native / no_native,
      yes_fee / no_fee, yes_size / no_size (exchange depth, else None),
      yes_since / no_since (ISO UTC), href (deep link when the book gave one),
      best_yes_venue / best_no_venue (row-level, codes), edge (row arb edge,
      negative = no arb), ts (row timestamp, ISO UTC).
    Rows without a quotes[] list (unmerged single-venue rows in 'changes'
    messages) are handled by synthesizing one quote from the row itself.
    """
    if not row:
        return []
    parsed = parse_game_key(row.get("game", "")) or {}
    teams = row.get("gameTeams") or [None, None]
    quotes = row.get("quotes")
    if quotes is None and row.get("venue") not in (None, "match"):
        quotes = [{"venue": row.get("venue"), "book": True,
                   "yes": row.get("yes"), "no": row.get("no"),
                   "yesFee": row.get("yesFee"), "noFee": row.get("noFee"),
                   "yesSz": row.get("yesSz"), "noSz": row.get("noSz"),
                   "yesNative": row.get("yesNative"),
                   "noNative": row.get("noNative"), "href": row.get("href"),
                   "yesSince": row.get("yesSince"),
                   "noSince": row.get("noSince")}]
    out = []
    for q in quotes or []:
        code = q.get("venue") or ""
        canonical, kind = venue_info(code)
        yes_p, no_p = pct_to_prob(q.get("yes")), pct_to_prob(q.get("no"))
        out.append({
            "row_id": row.get("id"),
            "game_key": row.get("game"),
            "sport": parsed.get("sport"),
            "date": parsed.get("date"),
            "away": teams[0], "home": teams[1] if len(teams) > 1 else None,
            "section": row.get("section"),
            "family": row.get("family"),
            "family_title": row.get("familyTitle"),
            "rung": row.get("rung"),
            "line": row.get("line"),
            "team": row.get("team"),
            "player": row.get("player"),
            "venue": code,
            "book": canonical,
            "kind": kind,
            "bettable": kind != "dfs",
            "yes_prob": yes_p, "no_prob": no_p,
            "yes_american": prob_to_american(yes_p),
            "no_american": prob_to_american(no_p),
            "yes_native": clean_native(q.get("yesNative")),
            "no_native": clean_native(q.get("noNative")),
            "yes_fee": q.get("yesFee"), "no_fee": q.get("noFee"),
            "yes_size": q.get("yesSz"), "no_size": q.get("noSz"),
            "yes_since": _ms_to_iso(q.get("yesSince")),
            "no_since": _ms_to_iso(q.get("noSince")),
            "href": q.get("href"),
            "best_yes_venue": (row.get("bestYes") or {}).get("venue"),
            "best_no_venue": (row.get("bestNo") or {}).get("venue"),
            "edge": row.get("edge"),
            "ts": _ms_to_iso(row.get("ts")),
        })
    return out


def normalize_rows(rows) -> list[dict]:
    """normalize_row over a snapshot/upsert list, flattened."""
    out = []
    for r in rows or []:
        out.extend(normalize_row(r))
    return out


def apply_changes(state: dict, msg: dict) -> tuple[int, int]:
    """Apply one SSE message to a {row_id: row} state dict, in place.
    A snapshot REPLACES the state; changes upsert/remove. Returns
    (n_upserted, n_removed). Pure -- the streaming loop below is a thin
    network wrapper around this."""
    if msg.get("type") == "snapshot":
        state.clear()
        for r in msg.get("rows") or []:
            if r.get("id"):
                state[r["id"]] = r
        return len(state), 0
    ups = msg.get("upsert") or []
    for r in ups:
        if r.get("id"):
            state[r["id"]] = r
    removed = 0
    for rid in msg.get("remove") or []:
        if state.pop(rid, None) is not None:
            removed += 1
    return len(ups), removed


# ---------------------------------------------------------------------------
# Network: SSE streams + REST helpers
# ---------------------------------------------------------------------------

SSE_MAX_ATTEMPTS = 6        # consecutive failed (re)connects before giving up
SSE_BACKOFF_START = 1.0
SSE_BACKOFF_CAP = 30.0
ONE_SHOT_DEADLINE_S = 180.0  # fetch_slate / fetch_game_snapshot: the whole call, retries included


def _sse_messages(path: str, params: dict, max_attempts: int = SSE_MAX_ATTEMPTS,
                  deadline_s: float | None = None):
    """Yield parsed JSON messages from an SSE endpoint.

    Reconnect policy: on an HTTP error, a dropped stream or a CLEAN end of stream, wait an exponentially growing, jittered
    backoff (1 s doubling to a 30 s cap, x0.5-1.0 random) and reopen. A 403/429 means the bb token is no longer accepted (or we
    are being throttled): the cookie is cleared and re-bootstrapped before the next attempt, and a 429 Retry-After is honoured.
    The generator GIVES UP -- raising BestBookError -- after `max_attempts` consecutive attempts that delivered nothing, or once
    `deadline_s` seconds (if set) have passed since the call started. A connection that delivered at least one message resets
    the failure count. A caller that breaks/closes the generator stops the stream (stream_game() is the intentionally
    long-lived consumer; it has no deadline but is still bounded by max_attempts)."""
    started = time.monotonic()
    backoff = SSE_BACKOFF_START
    failures = 0
    while True:
        if deadline_s is not None and time.monotonic() - started > deadline_s:
            raise BestBookError(f"SSE {path}: gave up after {deadline_s:.0f}s overall deadline")
        got_message = False
        retry_after = 0.0
        try:
            _ensure_token()
            # Accept-Encoding: identity is LOAD-BEARING: with requests'
            # default gzip negotiation the SSE stream stalls behind
            # Cloudflare/urllib3 buffering (verified 2026-10-30: snapshot
            # never arrived in 45 s with gzip; 0.7 s with identity).
            with _session.get(f"{BASE}{path}", params=params, stream=True,
                              timeout=(10, 60),
                              headers={"Accept-Encoding": "identity"}) as r:
                if r.status_code in (403, 429, 502):
                    if r.status_code in (403, 429):
                        _session.cookies.clear()          # re-bootstrap the token on the next attempt
                    if r.status_code == 429:
                        try:
                            retry_after = float(r.headers.get("Retry-After", 0) or 0)
                        except (TypeError, ValueError):
                            retry_after = 0.0
                    raise BestBookError(f"SSE {path} -> HTTP {r.status_code}")
                r.raise_for_status()
                for raw in r.iter_lines(chunk_size=65536):
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data: "):
                        try:
                            msg = json.loads(line[6:])
                        except json.JSONDecodeError:
                            logger.warning("bestbook: undecodable SSE line "
                                           "(%d bytes) on %s", len(line), path)
                            continue
                        got_message = True
                        yield msg
            why = "stream ended"
        except (requests.RequestException, BestBookError) as e:
            why = str(e)
        if got_message:
            failures, backoff = 0, SSE_BACKOFF_START      # it was healthy until it dropped
        else:
            failures += 1
            if failures >= max_attempts:
                raise BestBookError(f"SSE {path}: gave up after {failures} consecutive failed attempts (last: {why})")
        delay = max(retry_after, backoff * random.uniform(0.5, 1.0))
        logger.warning("bestbook: stream %s dropped (%s); reopening in %.1fs (attempt %d/%d)",
                       path, why, delay, failures, max_attempts)
        time.sleep(delay)
        backoff = min(SSE_BACKOFF_CAP, backoff * 2)


def fetch_slate(src: str = "direct") -> list[dict]:
    """Normalized slate across all leagues (parse_slate shape). One short
    rows=0 stream read -- returns as soon as the snapshot lands."""
    for msg in _sse_messages("/events", {"game": "all", "rows": 0, "src": src}, deadline_s=ONE_SHOT_DEADLINE_S):
        return parse_slate(msg.get("status") or {})
    return []


def fetch_game_snapshot(game_key: str, src: str = "direct",
                        more: bool = False) -> dict:
    """One game's full snapshot: {game_key, rows (raw), records (normalized
    per-quote), status}. WARNING: multi-MB -- see the module docstring."""
    params = {"game": game_key, "src": src}
    if more:
        params["more"] = 1
    for msg in _sse_messages("/events", params, deadline_s=ONE_SHOT_DEADLINE_S):
        if msg.get("type") == "snapshot":
            return {"game_key": game_key, "rows": msg.get("rows") or [],
                    "records": normalize_rows(msg.get("rows")),
                    "status": msg.get("status") or {}}
    return {"game_key": game_key, "rows": [], "records": [], "status": {}}


def stream_game(game_key: str, src: str = "direct", more: bool = False):
    """Yield raw SSE messages for one game indefinitely: the snapshot, then
    every changes batch. Feed each message through apply_changes() to keep a
    live {row_id: row} book. Caller breaks/closes to stop."""
    params = {"game": game_key, "src": src}
    if more:
        params["more"] = 1
    yield from _sse_messages("/events", params)


def fetch_json(path: str, **params):
    """GET a BestBook REST endpoint (token ensured) -> parsed JSON or None."""
    _ensure_token()
    r = _session.get(f"{BASE}{path}", params=params or None, timeout=30)
    if r.status_code >= 400:
        logger.warning("bestbook: GET %s -> HTTP %s", path, r.status_code)
        return None
    return r.json()


def fetch_prices(row_or_game_id: str):
    """Pregame price history for one row id ('match:...|total|full|54') or a
    whole game ('match:<game_key>'). -> {kickoff, prices, feeds} or None."""
    return fetch_json("/prices", id=row_or_game_id)


if __name__ == "__main__":
    # Smoke check: python -m mlb_core.odds.bestbook [game_key] [--follow]
    import sys

    logging.basicConfig(level=logging.INFO)
    follow = "--follow" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slate = fetch_slate()
    print(f"slate: {len(slate)} games")
    for g in slate:
        print(f"  {g['key']}  {g['away']} @ {g['home']}  {g['start']}")
    key = args[0] if args else (slate[0]["key"] if slate else None)
    if key:
        snap = fetch_game_snapshot(key)
        books = sorted({r["book"] for r in snap["records"]})
        print(f"{key}: {len(snap['rows'])} rows -> "
              f"{len(snap['records'])} quote records; books: {books}")
        if follow:
            state: dict = {}
            for message in stream_game(key):
                u, d = apply_changes(state, message)
                if u or d:
                    print(f"changes: +{u} -{d} (state {len(state)} rows)")
