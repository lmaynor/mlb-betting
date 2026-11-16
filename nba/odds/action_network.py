"""Action Network props + game-lines client and normalizers (BNK-11; spike BNK-10).

WHY: Action Network's web scoreboard endpoints are date-addressable with
real-book rows deep into past seasons, so historical props and game lines are
backfillable -- ParlayAPI (our live feed) keeps NO history, and BettingPros
(our historical panel) carries one line per book with no alternates at any
tier. BNK-10's trust diff (NBA rebounds, 2025-01-15, vs the banked
BettingPros panel) PASSED: 88.1% exact line agreement overall, DraftKings
97% exact / mean signed diff 0.000, the book-30 opener series 90.7% exact /
mean 0.000. AN is therefore a trusted second panel and the opener/consensus
source. The harvest is discovery-driven (owner, Nov 16): every league x type
is probed, and only types that return real rows are banked (the verified set
is recorded in a per-league manifest in the bucket).

ENDPOINTS (owner-supplied, verified live; BNK-10 probed exhaustively):
  Props:      GET /web/v2/scoreboard/{league}/markets?bookIds=15,30,68,69,75,123,79
                  &customPickTypes=<exactly ONE type per call -- multi-type 400s>
                  &date=YYYYMMDD (ET slate date)
  Game lines: GET /web/v2/scoreboard/{league}?date=YYYYMMDD
                  (one call per date returns the full slate with per-game,
                  per-book spread/total/moneyline/team-total markets and
                  bet_info percentages; banked as the "gamelines" market)
No auth. One date = one snapshot (the closing state for completed dates);
there are no per-row timestamps and no intraday history. Alt boards are NOT
two-sided ladders on any AN surface: they are one-sided milestone stacks
(side == "noside", a single Yes price per book per threshold), as are the
scorer markets (anytime/first/last). One-sided rows are normalized with
is_milestone=True so no consumer mistakes them for two-sided ladders; no
de-vig is possible from them.

RAW-FIRST (the PR #218 / first_scorer pattern): the runner banks the
untouched response body (gzipped) per (market, date) BEFORE any normalized
row exists. AN's own edge/projection/bet_quality fields ride along in raw
but are deliberately DROPPED by the normalizers -- they are AN's model
outputs, not prices, and do not belong in a price panel.

Pacing/retry policy lives here (AdaptivePacer + fetch_with_policy) so the
runner stays thin and the policy is unit-testable. Owner's rule (Nov 16):
on throttle signals, ADJUST (step pacing down, rebalance, prioritize) --
the hard stop is only for an outright block (403 wall).
"""
from __future__ import annotations

import gzip
import json
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MARKETS_URL = "https://api.actionnetwork.com/web/v2/scoreboard/{league}/markets"
SCOREBOARD_URL = "https://api.actionnetwork.com/web/v2/scoreboard/{league}"
GAMELINES_SLUG = "gamelines"

# Declared User-Agent for our own direct calls (house rule, AGENTS.md).
USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Origin": "https://www.actionnetwork.com",
    "Referer": "https://www.actionnetwork.com/",
    "Accept": "application/json",
}

# book id -> panel name. 15/30 are AN's own series (consensus / opening line),
# not books; they are banked as their own book rows, as in the BP panel.
BOOKS = {
    15: "consensus",
    30: "open",
    68: "draftkings",
    69: "fanduel",
    75: "betmgm",
    123: "caesars",
    79: "bet365",
}
DEFAULT_BOOK_IDS = (15, 30, 68, 69, 75, 123, 79)


def _ms(group: int, key_stat: str, slug_stat: str, ids: dict) -> dict:
    """Milestone type table: {slug: customPickType} from {threshold: type id}."""
    return {
        f"{slug_stat}_milestone_{n}plus":
            f"core_bet_type_{group}_{tid}_player_{key_stat}_milestones_{n}_or_more"
        for n, tid in ids.items()
    }


# ---------------------------------------------------------------------------
# NBA (verified live end-to-end, BNK-10 + the BNK-11 proving window)
# ---------------------------------------------------------------------------
NBA_MARKET_TYPES = {
    "points": "core_bet_type_27_points",
    "rebounds": "core_bet_type_23_rebounds",
    "assists": "core_bet_type_26_assists",
    "threes": "core_bet_type_21_3fgm",
    "pra": "core_bet_type_85_points_rebounds_assists",
    "pr": "core_bet_type_86_points_rebounds",
    "pa": "core_bet_type_87_points_assists",
}
NBA_MILESTONE_GROUPS = {
    "points": (201, "points", {
        5: 694, 10: 695, 15: 696, 20: 697, 25: 698,
        30: 699, 35: 700, 40: 701, 45: 702, 50: 1143,
    }),
    "rebounds": (202, "rebounds", {
        2: 1243, 3: 735, 4: 736, 5: 737, 6: 738, 7: 739, 8: 740, 10: 741,
        12: 742, 13: 743, 14: 744, 15: 745, 16: 746, 17: 1138, 18: 1273, 20: 1242,
    }),
    "assists": (203, "assists", {
        2: 747, 3: 748, 4: 749, 5: 750, 6: 751, 7: 752, 8: 753, 10: 754,
        12: 755, 13: 756, 14: 757, 15: 758, 16: 759,
    }),
    "threes": (204, "threes_made", {
        1: 760, 2: 761, 3: 762, 4: 763, 5: 764,
        6: 1052, 7: 1053, 8: 1054, 9: 1055, 10: 1058,
    }),
}
for _stat, (_g, _ks, _ids) in NBA_MILESTONE_GROUPS.items():
    NBA_MARKET_TYPES.update(_ms(_g, _ks, _stat, _ids))

def milestone_type(stat: str, threshold: int) -> str:
    group, key_stat, ids = NBA_MILESTONE_GROUPS[stat]
    return f"core_bet_type_{group}_{ids[threshold]}_player_{key_stat}_milestones_{threshold}_or_more"


def milestone_slug(stat: str, threshold: int) -> str:
    return f"{stat}_milestone_{threshold}plus"


# Back-compat aliases (the NBA-only names this module shipped with).
MARKET_TYPES = {k: v for k, v in NBA_MARKET_TYPES.items() if "_milestone_" not in k}
MILESTONE_GROUPS = NBA_MILESTONE_GROUPS
ALL_MARKET_TYPES = NBA_MARKET_TYPES
MILESTONE_SLUGS = frozenset(s for s in NBA_MARKET_TYPES if "_milestone_" in s)
PROBE_MARKET = GAMELINES_SLUG

# ---------------------------------------------------------------------------
# NHL (candidates extracted from the NHL props pages' site config, 2026-11-16;
# the league probe verifies which return rows). Priority order: goaltender
# saves and shots on goal are our proven/near-proven surfaces and come first.
# No plain goals O/U key exists in the NHL config; scorer markets carry goals.
# Duplicate anytime/first/last keys (313/316, 48/314, 310/315) are distinct
# AN types (period variants); both are candidates, suffixed _b.
# ---------------------------------------------------------------------------
NHL_MARKET_TYPES = {
    "saves": "core_bet_type_38_goaltender_saves",
    "shots_on_goal": "core_bet_type_31_shots_on_goal",
    "points": "core_bet_type_280_points",
    "assists": "core_bet_type_279_assists",
    "anytime_goal_scorer": "core_bet_type_313_anytime_goal_scorer",
    "anytime_goal_scorer_b": "core_bet_type_316_anytime_goal_scorer",
    "first_goal_scorer": "core_bet_type_48_first_goal_scorer",
    "first_goal_scorer_b": "core_bet_type_314_first_goal_scorer",
    "last_goal_scorer": "core_bet_type_310_last_goal_scorer",
    "last_goal_scorer_b": "core_bet_type_315_last_goal_scorer",
    "to_score_2_or_more_goals": "core_bet_type_311_to_score_2_or_more_goals",
    "to_score_3_or_more_goals": "core_bet_type_312_to_score_3_or_more_goals",
}
NHL_MARKET_TYPES.update(_ms(205, "shots_on_goal", "shots_on_goal",
                            {1: 765, 2: 766, 3: 767, 4: 768, 5: 769, 6: 770, 7: 771, 8: 772}))
NHL_MARKET_TYPES.update(_ms(545, "assists", "assists", {2: 1181, 3: 1182}))
NHL_ONE_SIDED = frozenset(
    s for s in NHL_MARKET_TYPES if "_milestone_" in s or "scorer" in s or s.startswith("to_score_"))

# ---------------------------------------------------------------------------
# NFL (candidates from the NFL props index config, 2026-11-16)
# ---------------------------------------------------------------------------
NFL_MARKET_TYPES = {
    "passing_yards": "core_bet_type_9_passing_yards",
    "pass_completions": "core_bet_type_10_pass_completions",
    "passing_tds": "core_bet_type_11_passing_tds",
    "rushing_yards": "core_bet_type_12_rushing_yards",
    "rushing_tds": "core_bet_type_13_rushing_tds",
    "receptions": "core_bet_type_15_receptions",
    "receiving_yards": "core_bet_type_16_receiving_yards",
    "receiving_tds": "core_bet_type_17_receiving_tds",
    "rushing_attempts": "core_bet_type_18_rushing_attempts",
    "passing_attempts": "core_bet_type_30_passing_attempts",
    "kicking_points": "core_bet_type_43_kicking_points",
    "first_touchdown_scorer": "core_bet_type_56_first_touchdown_scorer",
    "longest_rush": "core_bet_type_58_longest_rush",
    "longest_reception": "core_bet_type_59_longest_reception",
    "longest_completion": "core_bet_type_60_longest_completion",
    "anytime_touchdown_scorer": "core_bet_type_62_anytime_touchdown_scorer",
    "last_touchdown_scorer": "core_bet_type_63_last_touchdown_scorer",
    "interceptions": "core_bet_type_65_interceptions",
    "rushing_receiving_yards": "core_bet_type_66_rushing_receiving_yards",
    "to_score_2_or_more_touchdowns": "core_bet_type_67_to_score_2_or_more_touchdowns",
    "to_score_3_or_more_touchdowns": "core_bet_type_68_to_score_3_or_more_touchdowns",
    "tackles_assists": "core_bet_type_70_tackles_assists",
    "passing_rushing_yards": "core_bet_type_71_passing_rushing_yards",
    "extra_points_made": "core_bet_type_212_extra_points_made",
    "field_goals_made": "core_bet_type_213_field_goals_made",
    "sacks": "core_bet_type_578_sacks",
}
NFL_MARKET_TYPES.update(_ms(524, "passing_touchdowns", "passing_touchdowns",
                            {1: 1034, 2: 1035, 3: 1036, 4: 1037}))
NFL_MARKET_TYPES.update(_ms(525, "passing_yards", "passing_yards",
                            {150: 1046, 175: 1045, 200: 1006, 225: 1007, 250: 1008,
                             275: 1009, 300: 1010, 325: 1011, 350: 1012, 400: 1013}))
NFL_MARKET_TYPES.update(_ms(527, "rushing_touchdowns", "rushing_touchdowns",
                            {1: 1047, 2: 1048, 3: 1049, 4: 1050, 5: 1088}))
NFL_MARKET_TYPES.update(_ms(528, "rushing_yards", "rushing_yards",
                            {25: 1014, 40: 1015, 50: 1016, 60: 1017, 70: 1018,
                             80: 1019, 90: 1020, 100: 1021, 110: 1022}))
NFL_MARKET_TYPES.update(_ms(530, "receiving_yards", "receiving_yards",
                            {25: 1023, 40: 1024, 50: 1025, 60: 1026, 70: 1027,
                             80: 1028, 90: 1029, 100: 1030, 110: 1031, 125: 1032}))
NFL_MARKET_TYPES.update(_ms(531, "tackles", "tackles",
                            {1: 1272, 2: 1066, 3: 1067, 4: 1068, 5: 1069,
                             6: 1070, 7: 1071, 8: 1072, 9: 1073, 10: 1074}))
NFL_MARKET_TYPES.update(_ms(532, "receptions", "receptions",
                            {2: 1038, 3: 1039, 4: 1040, 5: 1041, 6: 1042,
                             7: 1043, 8: 1044, 9: 1056, 10: 1057}))
NFL_MARKET_TYPES.update(_ms(533, "rushing_attempts", "rushing_attempts",
                            {8: 1081, 16: 1116, 24: 1151, 28: 1152, 30: 1108}))
NFL_MARKET_TYPES.update(_ms(534, "passing_attempts", "passing_attempts",
                            {30: 1061, 34: 1119, 38: 1120, 42: 1141, 46: 1142, 50: 1065}))
NFL_ONE_SIDED = frozenset(
    s for s in NFL_MARKET_TYPES
    if "_milestone_" in s or "scorer" in s or s.startswith("to_score_"))

# ---------------------------------------------------------------------------
# NCAAB (candidates: the NBA type keys under league=ncaab, plus AN's
# double/triple-double types; the probe decides what actually returns rows)
# ---------------------------------------------------------------------------
NCAAB_MARKET_TYPES = dict(NBA_MARKET_TYPES)
NCAAB_MARKET_TYPES.update({
    "double_double": "core_bet_type_113_double",
    "triple_double": "core_bet_type_114_triple",
})
NCAAB_ONE_SIDED = frozenset(
    s for s in NCAAB_MARKET_TYPES if "_milestone_" in s) | {"double_double", "triple_double"}

# ---------------------------------------------------------------------------
# Tail leagues (expansion 3, 2026-11-16). NCAAF shares the NFL keyspace (the
# same ids appear on its props pages; the probe verifies). MLB has its own
# keyspace, harvested from the MLB props page config. WNBA = NBA keyspace
# plus its first-basket / double-double types. Soccer competitions and
# NCAAW exposed no prop keyspace on their public props pages: gamelines
# only (empty candidate tables).
# ---------------------------------------------------------------------------
MLB_MARKET_TYPES = {
    "singles": "core_bet_type_32_singles",
    "home_runs": "core_bet_type_33_hr",
    "rbis": "core_bet_type_34_rbi",
    "doubles": "core_bet_type_35_doubles",
    "hits": "core_bet_type_36_hits",
    "strikeouts": "core_bet_type_37_strikeouts",
    "pitching_outs": "core_bet_type_42_pitching_outs",
    "hits_allowed": "core_bet_type_72_hits_allowed",
    "stolen_bases": "core_bet_type_73_stolen_bases",
    "earned_runs": "core_bet_type_74_earned_runs",
    "triples": "core_bet_type_75_triples",
    "walks": "core_bet_type_76_walks",
    "total_bases": "core_bet_type_77_total_bases",
    "runs_scored": "core_bet_type_78_runs_scored",
}
MLB_MARKET_TYPES.update(_ms(433, "home_runs", "home_runs", {1: 943, 2: 944}))
MLB_MARKET_TYPES.update(_ms(434, "runs", "runs", {1: 945, 2: 946, 3: 947, 4: 1240}))
MLB_MARKET_TYPES.update(_ms(436, "hits", "hits",
                            {1: 952, 2: 953, 3: 954, 4: 955, 5: 1173, 6: 1174}))
MLB_MARKET_TYPES.update(_ms(437, "total_bases", "total_bases",
                            {2: 956, 3: 957, 4: 958, 5: 959, 6: 1156, 7: 1157, 8: 1158}))
MLB_MARKET_TYPES.update(_ms(438, "stolen_bases", "stolen_bases", {1: 960, 2: 961}))
MLB_MARKET_TYPES.update(_ms(439, "strikeouts", "strikeouts",
                            {3: 962, 4: 963, 5: 964, 6: 965, 7: 966, 8: 967, 9: 970,
                             10: 972, 11: 973, 12: 1094, 13: 1098, 14: 1099, 15: 1100}))
MLB_ONE_SIDED = frozenset(s for s in MLB_MARKET_TYPES if "_milestone_" in s)

WNBA_MARKET_TYPES = dict(NBA_MARKET_TYPES)
WNBA_MARKET_TYPES.update({
    "first_basket_scorer": "core_bet_type_112_first_basket_scorer",
    "double_double": "core_bet_type_113_double",
    "triple_double": "core_bet_type_114_triple",
    "first_fg_scorer": "core_bet_type_145_first_fg_scorer",
})
WNBA_ONE_SIDED = frozenset(
    s for s in WNBA_MARKET_TYPES if "_milestone_" in s) | {
        "double_double", "triple_double", "first_basket_scorer", "first_fg_scorer"}

LEAGUE_MARKET_TYPES = {
    "nba": NBA_MARKET_TYPES,
    "nhl": NHL_MARKET_TYPES,
    "nfl": NFL_MARKET_TYPES,
    "ncaab": NCAAB_MARKET_TYPES,
    "ncaaf": NFL_MARKET_TYPES,
    "mlb": MLB_MARKET_TYPES,
    "wnba": WNBA_MARKET_TYPES,
    "epl": {},
    "mls": {},
    "laliga": {},
    "bundesliga": {},
    "seriea": {},
    "ligue1": {},
    "ncaaw": {},
}
LEAGUE_ONE_SIDED = {
    "nba": MILESTONE_SLUGS,
    "nhl": NHL_ONE_SIDED,
    "nfl": NFL_ONE_SIDED,
    "ncaab": NCAAB_ONE_SIDED,
    "ncaaf": NFL_ONE_SIDED,
    "mlb": MLB_ONE_SIDED,
    "wnba": WNBA_ONE_SIDED,
}
# Marquee type per league for date-depth ladders (the probe's yardstick).
LEAGUE_LADDER_TYPE = {
    "nba": "points",
    "nhl": "saves",
    "nfl": "passing_yards",
    "ncaab": "points",
    "ncaaf": "passing_yards",
    "mlb": "strikeouts",
    "wnba": "points",
}


def is_one_sided(league: str, slug: str) -> bool:
    return slug in LEAGUE_ONE_SIDED.get(league, frozenset()) or "_milestone_" in slug


def milestone_threshold(slug: str):
    """The N in an 'N+' milestone slug, or None for main boards/scorer markets."""
    if "_milestone_" not in slug:
        return None
    try:
        return float(slug.rsplit("_", 1)[1].removesuffix("plus"))
    except ValueError:
        return None


def build_url(league: str, date_yyyymmdd: str, pick_type: str, book_ids=DEFAULT_BOOK_IDS) -> str:
    books = ",".join(str(b) for b in book_ids)
    return (f"{MARKETS_URL.format(league=league)}?bookIds={books}"
            f"&customPickTypes={pick_type}&date={date_yyyymmdd}")


def build_scoreboard_url(league: str, date_yyyymmdd: str) -> str:
    return f"{SCOREBOARD_URL.format(league=league)}?date={date_yyyymmdd}"


@dataclass
class FetchResult:
    status: int | None          # HTTP status, None on transport error
    body: bytes = b""
    latency_s: float = 0.0
    error: str = ""


class ActionNetworkClient:
    """Single-attempt HTTP client. Retry/pacing policy is the caller's (see
    fetch_with_policy); this class only knows how to make one honest call."""

    def __init__(self, session=None, timeout=30):
        if session is None:
            import requests
            session = requests.Session()
            session.headers.update(HEADERS)
        self.session = session
        self.timeout = timeout

    def _get(self, url: str) -> FetchResult:
        t0 = time.monotonic()
        try:
            resp = self.session.get(url, timeout=self.timeout)
            return FetchResult(status=resp.status_code, body=resp.content,
                               latency_s=time.monotonic() - t0)
        except Exception as exc:  # transport error: status None, runner retries
            return FetchResult(status=None, latency_s=time.monotonic() - t0,
                               error=f"{type(exc).__name__}: {exc}")

    def fetch(self, league: str, date_yyyymmdd: str, pick_type: str,
              book_ids=DEFAULT_BOOK_IDS) -> FetchResult:
        return self._get(build_url(league, date_yyyymmdd, pick_type, book_ids))

    def fetch_scoreboard(self, league: str, date_yyyymmdd: str) -> FetchResult:
        return self._get(build_scoreboard_url(league, date_yyyymmdd))


class AdaptivePacer:
    """~1 req/s polite pacing that steps DOWN on throttle signals and eases
    back up on sustained health (owner's adjust-on-throttle rule, Nov 16).

    Every adjustment is recorded in .adjustments as
    {ts, shard, old_interval, new_interval, trigger} for the run log/memo."""

    BASE_INTERVAL = 1.0
    MAX_INTERVAL = 3.5
    STEP_UP_AFTER = 60          # consecutive successes before easing back up

    def __init__(self, shard: str = "", base_interval: float = BASE_INTERVAL,
                 clock=time.monotonic, wall=lambda: ""):
        self.shard = shard
        self.interval = base_interval
        self._clock = clock
        self._wall = wall
        self._last_call = None
        self._ok_streak = 0
        self.adjustments = []

    def wait(self, sleep=time.sleep, rand=random.random):
        """Block until the next call may start (interval + jitter)."""
        now = self._clock()
        if self._last_call is not None:
            delay = self.interval - (now - self._last_call) + rand() * 0.4 * self.interval
            if delay > 0:
                sleep(delay)
                now = self._clock()
        self._last_call = now

    def _set_interval(self, new: float, trigger: str):
        new = round(min(self.MAX_INTERVAL, max(self.BASE_INTERVAL, new)), 3)
        if new != self.interval:
            self.adjustments.append({
                "ts": self._wall(), "shard": self.shard,
                "old_interval": self.interval, "new_interval": new, "trigger": trigger,
            })
            logger.warning("pacer %s: %.2fs -> %.2fs (%s)", self.shard, self.interval, new, trigger)
            self.interval = new

    def on_throttle(self, trigger: str):
        self._ok_streak = 0
        self._set_interval(self.interval * 1.6 + 0.2, trigger)

    def on_success(self, latency_s: float):
        if latency_s > 4.0:
            self.on_throttle(f"latency {latency_s:.1f}s")
            return
        self._ok_streak += 1
        if self._ok_streak >= self.STEP_UP_AFTER and self.interval > self.BASE_INTERVAL:
            self._ok_streak = 0
            self._set_interval(self.interval * 0.9, "sustained health")


@dataclass
class PolicyOutcome:
    result: FetchResult | None = None
    hard_stop: bool = False     # outright block (403 wall): stop the shard
    gave_up: bool = False       # retries exhausted for this partition
    attempts: int = 0
    notes: list = field(default_factory=list)


def fetch_with_policy(call, pacer: AdaptivePacer, state: dict,
                      sleep=time.sleep, rand=random.random) -> PolicyOutcome:
    """One partition fetch under the run's retry/backoff policy. `call` is a
    zero-arg callable returning a FetchResult (either endpoint).

    state carries the shard's consecutive-403 counter across calls.
    429 -> pacer step-down + backoff; 5xx/transport -> backoff; 403 -> count,
    hard stop at 3 consecutive; 400 -> give up on this partition only (a data
    error, e.g. a dead type key -- retrying cannot fix it)."""
    backoffs = {429: (10, 30, 90), 500: (5, 15, 45)}
    out = PolicyOutcome()
    for attempt in range(4):
        out.attempts = attempt + 1
        pacer.wait(sleep=sleep, rand=rand)
        res = call()
        out.result = res
        if res.status == 200:
            state["consecutive_403"] = 0
            pacer.on_success(res.latency_s)
            return out
        if res.status == 403:
            state["consecutive_403"] = state.get("consecutive_403", 0) + 1
            out.notes.append(f"403 (consecutive {state['consecutive_403']})")
            if state["consecutive_403"] >= 3:
                out.hard_stop = True
                return out
            sleep(20 * attempt + 5)
            continue
        if res.status == 400:
            out.gave_up = True
            out.notes.append("400 INVALID_DATA (no retry)")
            return out
        # retryable: 429, 5xx, transport
        if res.status == 429:
            pacer.on_throttle("http_429")
        delays = backoffs[429] if res.status == 429 else backoffs[500]
        if attempt < 3:
            delay = delays[attempt] * (0.8 + 0.4 * rand())
            out.notes.append(f"status {res.status} attempt {attempt + 1}: backoff {delay:.0f}s")
            sleep(delay)
    out.gave_up = True
    out.notes.append("retries exhausted")
    return out


# ---------------------------------------------------------------------------
# Normalizers (pure: payload dict -> row dicts; unit-tested on live samples)
# ---------------------------------------------------------------------------

def _team_maps(payload: dict):
    """team_id -> abbr, and event_id -> game context, from games[]."""
    teams, games = {}, {}
    for g in payload.get("games") or []:
        home, away = g.get("home_team") or {}, g.get("away_team") or {}
        for t in (home, away):
            if t.get("id") is not None:
                teams[t["id"]] = t.get("abbr") or t.get("full_name") or ""
        games[g.get("id")] = {
            "home_abbr": home.get("abbr") or "", "away_abbr": away.get("abbr") or "",
            "start_time": g.get("start_time") or "", "game_status": g.get("status") or "",
        }
    return teams, games


def normalize_payload(payload: dict, *, league: str = "nba", date_iso: str,
                      market_slug: str, pick_type: str, scraped_at: str) -> list:
    """Props: one row per outcome in the payload, across all books.

    Row: date, event_id, player_id, player_name, team_id, team_abbr,
    home_abbr, away_abbr, start_time, game_status, market, source_type,
    is_milestone (one-sided: milestone stack or scorer market), threshold,
    book_id, book_name, side, line, odds, is_alt_market, line_status, period,
    market_id, outcome_id, scraped_at. AN's edge/projection/bet_quality
    fields are dropped on purpose (see the module docstring)."""
    teams, games = _team_maps(payload)
    players = {p.get("id"): p for p in payload.get("players") or []}
    one_sided = is_one_sided(league, market_slug)
    threshold = milestone_threshold(market_slug)
    rows = []
    markets = payload.get("markets") or {}
    for book_key, by_period in markets.items():
        try:
            book_id = int(book_key)
        except (TypeError, ValueError):
            continue
        outcomes = ((by_period or {}).get("event") or {}).get(pick_type) or []
        for o in outcomes:
            player = players.get(o.get("player_id")) or {}
            team_id = player.get("team_id")
            game = games.get(o.get("event_id")) or {}
            rows.append({
                "date": date_iso,
                "event_id": o.get("event_id"),
                "player_id": o.get("player_id"),
                "player_name": player.get("full_name") or "",
                "team_id": team_id,
                "team_abbr": teams.get(team_id, ""),
                "home_abbr": game.get("home_abbr", ""),
                "away_abbr": game.get("away_abbr", ""),
                "start_time": game.get("start_time", ""),
                "game_status": game.get("game_status", ""),
                "market": market_slug,
                "source_type": pick_type,
                "is_milestone": one_sided,
                "threshold": threshold,
                "book_id": book_id,
                "book_name": BOOKS.get(book_id, f"book_{book_id}"),
                "side": o.get("side") or "",
                # Milestone outcomes carry value=0: the threshold lives in
                # the type key, so a milestone row's line IS its threshold.
                "line": threshold if threshold is not None else o.get("value"),
                "odds": o.get("odds"),
                "is_alt_market": bool(o.get("is_alt_market")),
                "line_status": o.get("line_status") or "",
                "period": o.get("period") or "",
                "market_id": o.get("market_id"),
                "outcome_id": o.get("outcome_id"),
                "scraped_at": scraped_at,
            })
    return rows


def normalize_gamelines(payload: dict, *, date_iso: str, scraped_at: str) -> list:
    """Game lines: one row per outcome in each game's embedded markets.

    Row: date, event_id, home_abbr, away_abbr, start_time, game_status,
    book_id, book_name, market_type (AN's raw type string: total, spread,
    moneyline, core_bet_type_6_team_score, ...), team_id, team_abbr, side,
    line, odds, tickets_pct, money_pct (bet_info percentages, None when the
    payload zeroes them), is_live, line_status, period, market_id,
    outcome_id, scraped_at."""
    rows = []
    for g in payload.get("games") or []:
        teams = {t.get("id"): (t.get("abbr") or t.get("full_name") or "")
                 for t in g.get("teams") or []}
        home_id, away_id = g.get("home_team_id"), g.get("away_team_id")
        base = {
            "date": date_iso, "event_id": g.get("id"),
            "home_abbr": teams.get(home_id, ""), "away_abbr": teams.get(away_id, ""),
            "start_time": g.get("start_time") or "", "game_status": g.get("status") or "",
            "scraped_at": scraped_at,
        }
        for book_key, by_period in (g.get("markets") or {}).items():
            try:
                book_id = int(book_key)
            except (TypeError, ValueError):
                continue
            for mtype, outcomes in ((by_period or {}).get("event") or {}).items():
                for o in outcomes or []:
                    bet_info = o.get("bet_info") or {}
                    rows.append({
                        **base,
                        "book_id": book_id,
                        "book_name": BOOKS.get(book_id, f"book_{book_id}"),
                        "market_type": mtype,
                        "team_id": o.get("team_id"),
                        "team_abbr": teams.get(o.get("team_id"), ""),
                        "side": o.get("side") or "",
                        "line": o.get("value"),
                        "odds": o.get("odds"),
                        "tickets_pct": (bet_info.get("tickets") or {}).get("percent"),
                        "money_pct": (bet_info.get("money") or {}).get("percent"),
                        "is_live": bool(o.get("is_live")),
                        "line_status": o.get("line_status") or "",
                        "period": o.get("period") or "",
                        "market_id": o.get("market_id"),
                        "outcome_id": o.get("outcome_id"),
                    })
    return rows


def payload_games(payload: dict) -> list:
    return payload.get("games") or []


def gzip_body(body: bytes) -> bytes:
    return gzip.compress(body)


def parse_body(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))
