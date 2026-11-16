"""
ev.ownbettor.propmap -- AUT-11: the verified Kalshi player-prop mapping table.

WHY THIS EXISTS.  AUT-09's resolver resolves a signal only through a verified
recorded table (the repo's strict mapping doctrine: a wrong mapping is a wrong
BET).  The recorded table AUT-09 shipped covers no player props, so every
gate-passing NBA/NHL prop signal recorded as an audited REJ_UNMAPPED skip and
the AUT-10 paper operation could not trade paper on the stream's own families.
This module builds that table from Kalshi's OWN market metadata (public,
read-only, unsigned) and banks it as data:

    OwnBettor/mapping/kalshi_props.json     (bucket; a derived artifact, put-
                                             overwritten per refresh like the
                                             AUT-01 shadow ledger)

VERIFIED ONLY, FAIL-CLOSED -- the same doctrine as mlb.execution.kalshi.mapping.
A Kalshi market enters the table only when every one of these assertions holds
(any failure is a counted reject, never an approximation):

  * the series is on the allowlist below (the "Protected" variants settle under
    different rules and the ladder/escalator series are different propositions,
    so they are never fetched, never mapped);
  * the title parses exactly as "{Player}: {N}+ {stat}" and yes_sub_title
    re-states it;
  * floor_strike == N - 0.5 with strike_type "greater", i.e. the proposition is
    exactly "stat >= N" -- the books' over/under at line N - 0.5, so a signal's
    side carries over unchanged (yes buys the over, no buys the under);
  * rules_primary re-states, word for word, the same player, threshold, stat,
    both teams, league and the game's scheduled date (Kalshi's own text is the
    identity assertion);
  * the event ticker, the event title and the sub_title codes all agree on the
    date and the two teams, and the market ticker embeds the player's own team
    code and the threshold as its final segment;
  * the market's occurrence_datetime belongs to the event's game night (its
    UTC date is the event date or the next day -- Kalshi stamps occurrence at
    a fixed +3h from the scheduled start across the whole Nov 1-16 sample, so
    it is a night check, never an exact-time identity).

DATE CONVENTIONS (measured, not assumed).  Three dates can name one game: the
Kalshi event date (arena-local), the signal's game_day (the ET day of the
first-sighting snapshot), and the ET date of the signal's commence (a late
Mountain/Pacific start lands after ET midnight).  The resolver tries all
three as key dates, requires the exact player/stat/line/team match on every
candidate, and resolves only on a single surviving ticker; a genuine tie
(same player, line and teams in back-to-back games) is broken only by the
verified occurrence offset (Kalshi occurrence == commence + 3h, within
45 minutes) and otherwise stays unmapped.

Player identity is an EXACT join, never fuzzy similarity: the resolver keys on
the stream's own normalization (ev.props.norm_name -- accent-folded, suffix-
stripped) of the player name, the stat, the exact line, the ET game day, and
then asserts the signal's matchup teams against the entry's Kalshi teams
(folded Kalshi team string must be an exact prefix of the signal's full team
name, both teams, no partial credit).  If two markets ever collide on one key
with different tickers, the key is dropped from the table and recorded as
ambiguous -- the DEC-09 D9 lesson, applied at build time.

FAMILIES.  The AUT-01 stream's market set is NBA threes / rebounds and NHL
shots on goal / saves (ev.autopilot.config.MARKETS; saves additionally flag-
gated upstream), and Kalshi lists NBA points under the same structure, so the
builder covers it for when the stream emits it.  NHL shots on goal is NOT
mapped: no Kalshi series exists for it (full-catalog scan, Nov 16, 2026 --
the only SOG series are World Cup soccer), so SOG signals stay unmapped by
construction and the table says so in its families report.  KXNHLSAVES exists
as a series but carried zero open events/markets at build time; the live
per-game goalie-saves series is KXNHLSAVE.

COST (Luc's standing compute discipline).  No new job, scheduler, or always-on
anything: the table rides inside the existing ownbettor-paper firing.  A firing
reads the banked table (one small object); only when it is older than
REFRESH_MAX_AGE_HOURS does that firing rebuild it (a handful of paginated
public-API reads, seconds) and bank the replacement.  A failed refresh falls
back to the stale table, and a missing table degrades to today's behavior
(everything unmapped) -- the mapping can never take the operation down.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev.ownbettor.resolver import ResolvedMarket
from ev.ownbettor.signals import Signal
from ev.props import norm_name
from mlb.execution.kalshi.price_grid import parse_price_ranges

ET = ZoneInfo("America/New_York")
TABLE_KEY = "OwnBettor/mapping/kalshi_props.json"
REFRESH_MAX_AGE_HOURS = 24.0
PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"

# (league, stat) -> how Kalshi expresses the family.  title_stat is the word in
# the market title ("...: 4+ threes"); rules_stat is the phrase in rules_primary
# ("records 4+ made three-pointers").  Both are asserted verbatim.
FAMILIES = {
    ("nba", "points"): {
        "series": "KXNBAPTS", "title_stat": "points", "rules_stat": "points",
        "event_suffix": "Points", "league_phrase": "Pro Basketball",
        "participant": "basketball_player",
    },
    ("nba", "rebounds"): {
        "series": "KXNBAREB", "title_stat": "rebounds", "rules_stat": "rebounds",
        "event_suffix": "Rebounds", "league_phrase": "Pro Basketball",
        "participant": "basketball_player",
    },
    ("nba", "threes"): {
        "series": "KXNBA3PT", "title_stat": "threes", "rules_stat": "made three-pointers",
        "event_suffix": "Threes", "league_phrase": "Pro Basketball",
        "participant": "basketball_player",
    },
    ("nhl", "saves"): {
        "series": "KXNHLSAVE", "title_stat": "saves", "rules_stat": "saves",
        "event_suffix": "Saves", "league_phrase": "NHL",
        "participant": "hockey_player",
    },
}

# Stream families Kalshi does not list, with the evidence (AUT-11 memo).
NOT_LISTED = {
    ("nhl", "sog"): "no Kalshi series exists for NHL player shots on goal "
                    "(full series-catalog scan, Nov 16, 2026; the only SOG "
                    "series are KXWCSOG / KXWCTEAMSOG, World Cup soccer)",
}


def prop_key(league: str, stat: str, player_norm: str, line: float, game_day: str) -> str:
    return f"prop|{league}|{stat}|{player_norm}|{line}|{game_day}"


def _fold_team(s: str) -> str:
    """Fold a team name for the prefix check: lowercase, accent-fold, drop
    punctuation (the ev.props._fold_team convention)."""
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace(".", "").strip()


# --------------------------------------------------------------------- events
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


def parse_event(event: dict, fam: dict) -> dict | None:
    """One Kalshi event -> {date, codes, teams} with every Kalshi field agreeing,
    or None (the event and all its markets stay unmapped)."""
    ticker = str(event.get("event_ticker") or "")
    title = str(event.get("title") or "")
    sub = str(event.get("sub_title") or "")
    suffix = f": {fam['event_suffix']}"
    if not title.endswith(suffix):
        return None
    teams_part = title[: -len(suffix)]
    m = re.fullmatch(r"(.+) vs (.+)", teams_part)
    if not m:
        return None
    team_a, team_b = m.group(1).strip(), m.group(2).strip()
    m = re.fullmatch(r"([A-Z0-9]+) vs ([A-Z0-9]+) \(([A-Z][a-z]{2}) (\d{1,2})\)", sub)
    if not m:
        return None
    code_a, code_b = m.group(1), m.group(2)
    sub_month = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                 "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    sub_mon, sub_day = sub_month.get(m.group(3)), int(m.group(4))
    # event ticker: {series}-{YY}{MON}{DD}{CODEA}{CODEB}
    tail = ticker.split("-", 1)[1] if "-" in ticker else ""
    m = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)", tail)
    if not m or m.group(4) != code_a + code_b:
        return None
    year, month, day = 2000 + int(m.group(1)), _MONTHS.get(m.group(2)), int(m.group(3))
    if month is None or sub_mon != month or sub_day != day:
        return None
    date = f"{year:04d}-{month:02d}-{day:02d}"
    return {"date": date, "codes": (code_a, code_b), "teams": (team_a, team_b),
            "event_ticker": ticker}


# -------------------------------------------------------------------- markets
def parse_market(mk: dict, fam: dict, event: dict) -> tuple[dict | None, str]:
    """One Kalshi market -> (table entry, "") or (None, reject reason).  Every
    assertion is on Kalshi's own metadata; see the module docstring."""
    title = str(mk.get("title") or "")
    m = re.fullmatch(r"(.+): (\d+)\+ " + re.escape(fam["title_stat"]), title)
    if not m:
        return None, "title_shape"
    player, n = m.group(1).strip(), int(m.group(2))
    if str(mk.get("yes_sub_title") or "") != f"{player}: {n}+":
        return None, "sub_title_mismatch"
    if mk.get("strike_type") != "greater" or mk.get("floor_strike") is None \
            or float(mk["floor_strike"]) != n - 0.5:
        return None, "strike_mismatch"
    # rules_primary must re-state the whole identity: every fixed word is
    # matched literally; the variable parts are captured and asserted equal.
    rules_re = re.compile(
        r"^If (?P<player>.+) records (?P<n>\d+)\+ " + re.escape(fam["rules_stat"]) +
        r" in the (?P<a>.+) vs (?P<b>.+) " + re.escape(fam["league_phrase"]) +
        r" game originally scheduled for (?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4})"
        r", then the market resolves to Yes\.$")
    rm = rules_re.fullmatch(str(mk.get("rules_primary") or ""))
    if rm is None or rm.group("player") != player or int(rm.group("n")) != n \
            or (rm.group("a"), rm.group("b")) != tuple(event["teams"]):
        return None, "rules_mismatch"
    try:
        rules_date = datetime.strptime(rm.group("date"), "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None, "rules_mismatch"
    if rules_date != event["date"]:
        return None, "rules_mismatch"
    if str(mk.get("primary_participant_key") or "") != fam["participant"]:
        return None, "participant_mismatch"
    custom = mk.get("custom_strike") or {}
    if fam["participant"] not in custom:
        return None, "participant_mismatch"
    # market ticker: {event_ticker}-{PLAYERTEAMSEGMENT}-{N}; the segment opens
    # with the player's own team code (one of the event's two codes)
    ticker = str(mk.get("ticker") or "")
    rest = ticker[len(event["event_ticker"]) + 1:] if ticker.startswith(event["event_ticker"] + "-") else ""
    seg = rest.rsplit("-", 1)
    if len(seg) != 2 or seg[1] != str(n):
        return None, "ticker_shape"
    player_team = next((c for c in event["codes"] if seg[0].startswith(c)), None)
    if player_team is None:
        return None, "ticker_shape"
    # the occurrence stamp must belong to this event's game night (see the
    # module docstring: Kalshi stamps it at commence + 3h, so its UTC date is
    # the event date or the next day -- anything else is a different game)
    occ = str(mk.get("occurrence_datetime") or "")
    try:
        occ_date = datetime.fromisoformat(occ.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        ev_date = datetime.strptime(event["date"], "%Y-%m-%d")
        if occ_date not in (event["date"], (ev_date + timedelta(days=1)).strftime("%Y-%m-%d")):
            return None, "occurrence_date_mismatch"
    except ValueError:
        return None, "occurrence_date_mismatch"
    ranges = [[r.start, r.end, r.step] for r in parse_price_ranges(mk.get("price_ranges"))]
    entry = {
        "ticker": ticker,
        "ranges": ranges,
        "exchange_index": mk.get("exchange_index"),
        "player": player,
        "player_norm": norm_name(player),
        "event_ticker": event["event_ticker"],
        "event_date": event["date"],
        "teams": list(event["teams"]),
        "team_codes": list(event["codes"]),
        "player_team_code": player_team,
        "line": float(mk["floor_strike"]),
        "occurrence": occ,
        "title": title,
    }
    return entry, ""


def build_table(markets_by_family: dict, events_by_family: dict,
                fee_multipliers: dict, built_at: str) -> dict:
    """Raw Kalshi payloads -> the banked table.  markets_by_family /
    events_by_family are keyed by (league, stat); fee_multipliers by series.
    Pure: no I/O, so tests and the historical proof drive it directly."""
    entries: dict = {}
    ambiguous: list = []
    families: dict = {}
    for fam_key, fam in FAMILIES.items():
        league, stat = fam_key
        events = {}
        for ev in events_by_family.get(fam_key, []):
            parsed = parse_event(ev, fam)
            if parsed is not None:
                events[parsed["event_ticker"]] = parsed
        seen = 0
        rejected: dict = {}
        for mk in markets_by_family.get(fam_key, []):
            seen += 1
            event = events.get(str(mk.get("event_ticker") or ""))
            if event is None:
                rejected["event_unparsed"] = rejected.get("event_unparsed", 0) + 1
                continue
            entry, reason = parse_market(mk, fam, event)
            if entry is None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            entry["fee_multiplier"] = fee_multipliers.get(fam["series"])
            key = prop_key(league, stat, entry["player_norm"], entry["line"], entry["event_date"])
            prior = entries.get(key)
            if prior is not None and prior["ticker"] != entry["ticker"]:
                ambiguous.append({"key": key, "tickers": sorted({prior["ticker"], entry["ticker"]})})
                entries.pop(key, None)
                continue
            if any(a["key"] == key for a in ambiguous):
                continue
            entries[key] = entry
        families[f"{league}|{stat}"] = {
            "series": fam["series"], "events": len(events), "markets_seen": seen,
            "mapped": sum(1 for k in entries if k.startswith(f"prop|{league}|{stat}|")),
            "rejected": rejected,
        }
    for (league, stat), why in NOT_LISTED.items():
        families[f"{league}|{stat}"] = {"series": None, "not_listed": why}
    return {"kind": "kalshi_prop_table", "version": 1, "built_at": built_at,
            "entries": entries, "families": families, "ambiguous": ambiguous}


# -------------------------------------------------------------------- fetcher
class KalshiPublicFetcher:
    """Kalshi's public production data host: unsigned, read-only, the declared
    research User-Agent, polite spacing with 429 backoff (the unauthenticated
    rate limit is real; a refresh is a handful of calls once a day)."""

    def __init__(self, base: str = PUBLIC_BASE, spacing_s: float = 1.0):
        self.base = base
        self.spacing_s = spacing_s

    def _get(self, path: str) -> dict:
        for attempt in range(6):
            req = urllib.request.Request(self.base + path,
                                         headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("unreachable")

    def series_fee_multiplier(self, series: str):
        time.sleep(self.spacing_s)
        s = self._get(f"/series/{series}").get("series") or {}
        fee = s.get("fee_multiplier")
        return float(fee) if fee is not None else None

    def events(self, series: str, status: str = "open") -> list:
        time.sleep(self.spacing_s)
        out, cursor = [], None
        while True:
            p = f"/events?series_ticker={series}&status={status}&limit=200"
            if cursor:
                p += f"&cursor={cursor}"
            d = self._get(p)
            out += d.get("events", [])
            cursor = d.get("cursor")
            if not cursor:
                return out
            time.sleep(self.spacing_s)

    def markets(self, series: str, status: str = "open") -> list:
        time.sleep(self.spacing_s)
        out, cursor = [], None
        while True:
            p = f"/markets?series_ticker={series}&status={status}&limit=1000"
            if cursor:
                p += f"&cursor={cursor}"
            d = self._get(p)
            out += d.get("markets", [])
            cursor = d.get("cursor")
            if not cursor:
                return out
            time.sleep(self.spacing_s)


def build_live_table(fetcher: KalshiPublicFetcher, built_at: str) -> dict:
    markets, events, fees = {}, {}, {}
    for fam_key, fam in FAMILIES.items():
        events[fam_key] = fetcher.events(fam["series"])
        markets[fam_key] = fetcher.markets(fam["series"])
        fees[fam["series"]] = fetcher.series_fee_multiplier(fam["series"])
    return build_table(markets, events, fees, built_at)


# ------------------------------------------------------------- banked refresh
def _table_age_hours(table: dict, now: datetime) -> float | None:
    try:
        built = datetime.fromisoformat(str(table.get("built_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (now - built).total_seconds() / 3600.0


def table_is_fresh(table: dict | None, now: datetime,
                   max_age_hours: float = REFRESH_MAX_AGE_HOURS) -> bool:
    if not table:
        return False
    age = _table_age_hours(table, now)
    return age is not None and age <= max_age_hours


def load_or_refresh(store, now: datetime | None = None, fetcher=None) -> tuple[dict, dict]:
    """The paper firing's table source: read the banked table; rebuild and
    re-bank it inside this firing only when it is missing or stale.  Never
    raises -- a failed refresh falls back to the stale table, and no table at
    all degrades to an empty one (today's all-unmapped behavior)."""
    now = now or datetime.now(timezone.utc)
    cached = None
    try:
        raw = store.read(TABLE_KEY)
        cached = json.loads(raw.decode("utf-8"))
    except Exception:                                            # noqa: BLE001
        cached = None
    if table_is_fresh(cached, now):
        return cached, {"source": "cache", "refreshed": False,
                        "built_at": cached.get("built_at"),
                        "entries": len(cached.get("entries") or {})}
    try:
        fresh = build_live_table(fetcher or KalshiPublicFetcher(),
                                 now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        store.put(TABLE_KEY, json.dumps(fresh, indent=1, sort_keys=True).encode("utf-8"))
        return fresh, {"source": "refresh", "refreshed": True,
                       "built_at": fresh.get("built_at"),
                       "entries": len(fresh.get("entries") or {})}
    except Exception as e:                                       # noqa: BLE001
        if cached is not None:
            return cached, {"source": "stale_fallback", "refreshed": False,
                            "built_at": cached.get("built_at"),
                            "entries": len(cached.get("entries") or {}),
                            "refresh_error": f"{type(e).__name__}: {e}"}
        return {"kind": "kalshi_prop_table", "version": 1, "built_at": None,
                "entries": {}, "families": {}, "ambiguous": []}, \
               {"source": "empty", "refreshed": False, "built_at": None,
                "entries": 0, "refresh_error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------- resolver
def _matchup_teams(matchup: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in str(matchup or "").split(" @ ")]
    if len(parts) != 2 or not all(parts):
        return None
    return _fold_team(parts[0]), _fold_team(parts[1])


def _team_tokens(s: str) -> list:
    """Tokenize a folded team name.  One recorded alias: the odds feed writes
    the Los Angeles teams as "LA Clippers" / "L.A. Clippers" (and Lakers);
    Kalshi writes the city in full.  Expand that one abbreviation so both
    sides compare in the same vocabulary -- a closed alias, not a guess."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " "
                      for ch in _fold_team(s))
    toks = cleaned.split()
    if len(toks) >= 2 and toks[0] == "la" and toks[1] in ("clippers", "lakers"):
        toks = ["los", "angeles"] + toks[1:]
    return toks


def _team_prefix_agree(kalshi_toks: list, signal_toks: list) -> bool:
    """Kalshi's team tokens must match a leading run of the signal's full-name
    tokens: every Kalshi token but the last must equal the signal token in its
    position exactly; the last may be the full token or its single first
    letter.  The single-letter case is Kalshi's own disambiguation for shared
    cities, verified across the settled Nov 2026 tables (event titles carry
    "Los Angeles C" for the Clippers and "Los Angeles L" for the Lakers, e.g.
    events 26NOV01TORLAL vs 26NOV02TORLAC).  It cannot cross the two LA teams:
    the letter must be the nickname's first letter, so "Los Angeles C" never
    agrees with the Lakers."""
    if not kalshi_toks or len(kalshi_toks) > len(signal_toks):
        return False
    for i, kt in enumerate(kalshi_toks):
        st = signal_toks[i]
        if i < len(kalshi_toks) - 1:
            if kt != st:
                return False
        else:
            return kt == st or (len(kt) == 1 and st.startswith(kt))
    return True


def teams_agree(kalshi_teams: list, signal_teams: tuple[str, str]) -> bool:
    """Both Kalshi title teams must token-prefix-agree with distinct signal
    full team names, covering both.  No partial credit."""
    remaining = [_team_tokens(t) for t in signal_teams]
    for kt in kalshi_teams:
        ktoks = _team_tokens(kt)
        hit = next((t for t in remaining if _team_prefix_agree(ktoks, t)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return not remaining


# Kalshi stamps occurrence_datetime at the scheduled start + 3h (verified
# constant across every NBA/NHL prop market sampled Nov 1-16, 2026; see the
# AUT-11 memo).  Used ONLY to break candidate-date ties, never as a primary
# key: if the convention ever changes, ties fall back to unmapped.
_OCCURRENCE_OFFSET = timedelta(hours=3)
_OCCURRENCE_TOLERANCE = timedelta(minutes=45)


def _candidate_days(signal: Signal) -> set:
    """The dates that can name the signal's game (module docstring): the
    stamped game_day, the ET date of commence, and the day before it (the
    arena-local date of a post-midnight-ET start)."""
    days = {signal.game_day}
    try:
        comm = datetime.fromisoformat(str(signal.commence).replace("Z", "+00:00"))
        if comm.tzinfo is None:
            comm = comm.replace(tzinfo=timezone.utc)
        et_day = comm.astimezone(ET).date()
        days.add(et_day.isoformat())
        days.add((et_day - timedelta(days=1)).isoformat())
    except ValueError:
        pass
    return days


def _occurrence_matches(entry: dict, signal: Signal) -> bool:
    try:
        occ = datetime.fromisoformat(str(entry.get("occurrence")).replace("Z", "+00:00"))
        comm = datetime.fromisoformat(str(signal.commence).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if occ.tzinfo is None or comm.tzinfo is None:
        return False
    return abs((occ - comm) - _OCCURRENCE_OFFSET) <= _OCCURRENCE_TOLERANCE


class PropTableResolver:
    """Resolves AUT-01 prop signals against the verified Kalshi prop table.
    Anything short of an exact, team-checked, unambiguous hit is None (an
    audited REJ_UNMAPPED in the runner), never an approximation."""

    def __init__(self, table: dict):
        self.entries = (table or {}).get("entries") or {}

    def resolve(self, signal: Signal) -> ResolvedMarket | None:
        if (signal.league, signal.stat) not in FAMILIES:
            return None
        teams = _matchup_teams(signal.matchup)
        if teams is None:
            return None
        player_norm = norm_name(signal.player)
        hits: dict = {}
        for day in _candidate_days(signal):
            entry = self.entries.get(prop_key(signal.league, signal.stat,
                                              player_norm, signal.line, day))
            if entry is not None and teams_agree(entry.get("teams") or [], teams):
                hits[str(entry["ticker"])] = entry
        if len(hits) > 1:
            narrowed = {t: e for t, e in hits.items() if _occurrence_matches(e, signal)}
            if len(narrowed) == 1:
                hits = narrowed
            else:
                return None
        if not hits:
            return None
        entry = next(iter(hits.values()))
        ranges = tuple(tuple(r) for r in entry.get("ranges") or ())
        return ResolvedMarket(ticker=str(entry["ticker"]), ranges=ranges,
                              exchange_index=entry.get("exchange_index"),
                              fee_multiplier=entry.get("fee_multiplier"))
