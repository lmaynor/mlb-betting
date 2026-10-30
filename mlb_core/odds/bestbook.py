"""
mlb_core.odds.bestbook -- BestBook (bestbook.app) live odds client + normalizer.

BestBook is a cross-book odds aggregator (a direct beezy.fyi comp set /
OddsJam-shape product). It covers NFL + NBA + NHL -- NO MLB slate was observed
on probe day, in-season or out -- which is why this client lives in the shared
mlb_core.odds package (sport-agnostic infra, CONTEXT.md section 0) while any
consumer/runner belongs to a sport pillar (nfl/ first).

Transport is NOT a WebSocket and NOT a scrape, despite the page showing
"Loading prices..." over a static HTML shell. Confirmed via live probes
2026-10-30 (reading the site's own /assets/js/app.js, then hitting the
endpoints directly):

  1. GET {BASE}/            -> 200 HTML; sets the `bb` token cookie (24 h).
                                The cookie is the ONLY auth. No account, no key.
  2. GET {BASE}/t           -> 204 + fresh `bb` cookie (hourly renewal in the
                               web client). 403 = token expired -> re-do (1).
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
    """BestBook request/stream failure after the retry policy gave up."""


class TokenExpired(BestBookError):
    """/t answered 403: the bb cookie outlived its 24 h. Re-bootstrap."""


# ---------------------------------------------------------------------------
# Token handling (the bb cookie is the whole auth model)
# ---------------------------------------------------------------------------

def bootstrap() -> None:
    """GET the homepage so the server sets the `bb` token cookie."""
    r = _session.get(f"{BASE}/", timeout=30)
    r.raise_for_status()
    if _session.cookies.get("bb") is None:
        raise BestBookError("homepage did not set a bb token cookie")


def renew_token() -> None:
    """GET /t (204 = renewed). Raises TokenExpired on 403."""
    if _session.cookies.get("bb") is None:
        bootstrap()
        return
    r = _session.get(f"{BASE}/t", timeout=30)
    if r.status_code == 403:
        raise TokenExpired("bb token expired")
    r.raise_for_status()


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
    r"^(?:(?P<sport>nfl|nhl|nba):)?(?P<date>\d{4}-\d{2}-\d{2})-"
    r"(?P<away>[A-Z]{2,4})-(?P<home>[A-Z]{2,4})$"
)


def parse_game_key(key: str) -> dict | None:
    """'2026-11-02-CHI-SEA' -> {sport:'nfl', date, away:'CHI', home:'SEA'}.
    NHL/NBA keys carry the sport prefix ('nhl:2026-10-30-NJD-VGK'); an
    unprefixed key is NFL (BestBook's original sport). None if unparseable."""
    m = _GAME_KEY_RE.match((key or "").strip())
    if not m:
        return None
    return {"sport": m["sport"] or "nfl", "date": m["date"],
            "away": m["away"], "home": m["home"]}


def parse_slate(status: dict) -> list[dict]:
    """Snapshot `status` -> normalized games for EVERY league present
    (status keys are 'slate' for NFL, 'slate:nhl', 'slate:nba', ...).
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

def _sse_messages(path: str, params: dict):
    """Yield parsed JSON messages from an SSE endpoint. Handles the
    reconnect policy the web client uses: on an HTTP error or a dropped
    stream, renew the token, back off 1 s doubling to a 30 s cap, reopen.
    Generator -- the caller controls lifetime (break / close to stop)."""
    backoff = 1.0
    while True:
        _ensure_token()
        try:
            # Accept-Encoding: identity is LOAD-BEARING: with requests'
            # default gzip negotiation the SSE stream stalls behind
            # Cloudflare/urllib3 buffering (verified 2026-10-30: snapshot
            # never arrived in 45 s with gzip; 0.7 s with identity).
            with _session.get(f"{BASE}{path}", params=params, stream=True,
                              timeout=(10, 60),
                              headers={"Accept-Encoding": "identity"}) as r:
                if r.status_code in (403, 429, 502):
                    raise BestBookError(f"SSE {path} -> HTTP {r.status_code}")
                r.raise_for_status()
                backoff = 1.0
                for raw in r.iter_lines(chunk_size=65536):
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            logger.warning("bestbook: undecodable SSE line "
                                           "(%d bytes) on %s", len(line), path)
        except (requests.RequestException, BestBookError) as e:
            logger.warning("bestbook: stream %s dropped (%s); reopening in "
                           "%.0fs", path, e, backoff)
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)


def fetch_slate(src: str = "direct") -> list[dict]:
    """Normalized slate across all leagues (parse_slate shape). One short
    rows=0 stream read -- returns as soon as the snapshot lands."""
    for msg in _sse_messages("/events", {"game": "all", "rows": 0, "src": src}):
        return parse_slate(msg.get("status") or {})
    return []


def fetch_game_snapshot(game_key: str, src: str = "direct",
                        more: bool = False) -> dict:
    """One game's full snapshot: {game_key, rows (raw), records (normalized
    per-quote), status}. WARNING: multi-MB -- see the module docstring."""
    params = {"game": game_key, "src": src}
    if more:
        params["more"] = 1
    for msg in _sse_messages("/events", params):
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
