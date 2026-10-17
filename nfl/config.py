"""NFL package configuration: nflverse release layout, dataset registry, GCS keys.

nflverse publishes its data as parquet files attached to GitHub releases of
https://github.com/nflverse/nflverse-data (one release TAG per dataset family,
one asset per season or one asset for the whole history). The URL shapes and
season floors below were taken from the nflreadpy loaders (nflreadpy/load_*.py),
which are the reference client, and the column contracts from nflreadr's data
dictionaries (nflreadr/data-raw/dictionary_*.csv).

We MIRROR the parquet files byte-for-byte into the shared bucket under the
NFL/ prefix (no re-encoding, no merge): nflverse files are full-season
snapshots that it rewrites in place, so "replace the object" is the correct
update. Readers go through nfl.data.read.

GCS keys are relative to the shared bucket (resolved by mlb_core.storage from
MLB_GCS_BUCKET / GCS_BUCKET). Everything sits under NFL/ so it never collides
with the MLB or NBA data lakes (live ParlayAPI odds for NFL are separate, under
OddsAccum/americanfootball_nfl and EVSweep/nfl_props).
"""
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

# -- nflverse release layout -----------------------------------------------------
NFLVERSE_BASE = os.environ.get(
    "NFL_NFLVERSE_BASE", "https://github.com/nflverse/nflverse-data/releases/download")
USER_AGENT = os.environ.get("NFL_USER_AGENT", "beezy.fyi-nfl-ingest/1.0 (+https://beezy.fyi)")
# ffverse's expected-opportunity model (ff_opportunity) is published on a DIFFERENT repo's releases.
FFOPP_BASE = os.environ.get("NFL_FFOPP_BASE", "https://github.com/ffverse/ffopportunity/releases/download")
TIMEZONE = "America/New_York"

# -- GCS prefix ------------------------------------------------------------------
NFL_PREFIX = "NFL"
NFLVERSE_PREFIX = f"{NFL_PREFIX}/nflverse"
MANIFEST_KEY = f"{NFL_PREFIX}/_manifest.json"
LAST_REFRESH = f"{NFL_PREFIX}/last_refresh.json"

# -- Season window ---------------------------------------------------------------
# Default backfill floor. nflverse goes back to 1999 for pbp/stats, but the props
# era this repo cares about (and the only era whose rules/usage patterns resemble
# today's) starts around 2018; widen with --seasons or NFL_BACKFILL_START.
BACKFILL_START = int(os.environ.get("NFL_BACKFILL_START", "2018"))

# -- Politeness / safety ---------------------------------------------------------
REQUEST_DELAY_SEC = float(os.environ.get("NFL_REQUEST_DELAY", "0.25"))
MAX_ASSET_BYTES = int(os.environ.get("NFL_MAX_ASSET_BYTES", str(750 * 1024 * 1024)))
# Refuse to overwrite a mirrored file whose row count fell below this fraction of
# the previously mirrored copy (a truncated/bad upstream publish). --force overrides.
SHRINK_GUARD = float(os.environ.get("NFL_SHRINK_GUARD", "0.8"))


@dataclass(frozen=True)
class Dataset:
    """One mirrored nflverse dataset.

    tag         GitHub release tag (first path segment in nflreadpy's loaders).
    stem        asset filename stem; contains ``{season}`` iff per_season.
    first       first season the upstream publishes (clips the window from below).
    group       core | detail | extra -- default CLI selection is core+detail.
    required    columns that must exist in the file or it is rejected as invalid. ONLY columns confirmed
                against nflreadr's dictionaries are listed; datasets with no dictionary (teams, officials,
                pfr_*, contracts, trades) or a format that changed (depth_charts, 2025) get structural
                validation only (valid parquet, non-empty).
    roster_year True for datasets nflreadpy keys on the ROSTER year (rolls over
                on Mar 15) rather than the season year (rolls over after Labor Day).
    base        release-download base URL when the dataset is not published on nflverse-data (None = NFLVERSE_BASE).
    """
    name: str
    tag: str
    stem: str
    per_season: bool
    first: int
    group: str
    required: tuple = ()
    roster_year: bool = False
    base: Optional[str] = None

    def url(self, season: Optional[int] = None) -> str:
        stem = self.stem.format(season=season) if self.per_season else self.stem
        return f"{self.base or NFLVERSE_BASE}/{self.tag}/{stem}.parquet"

    def key(self, season: Optional[int] = None) -> str:
        leaf = f"{self.name}_{season}" if self.per_season else self.name
        return f"{NFLVERSE_PREFIX}/{self.name}/{leaf}.parquet"


def _per_season(name, tag, stem, first, group, required=(), roster_year=False):
    return Dataset(name, tag, stem, True, first, group, tuple(required), roster_year)


def _single(name, tag, stem, group, required=()):
    return Dataset(name, tag, stem, False, 0, group, tuple(required))


_DATASETS = [
    # ---- core: small, join-critical (ids, schedule/lines, outcomes, availability) ----
    # schedules/games carries the CLOSING betting lines (spread_line, total_line, home/away_moneyline),
    # result, roof/surface/temp/wind, rest days, coaches, QBs and referee for every game since 1999.
    _single("schedules", "schedules", "games", "core", ("game_id", "season", "week", "home_team", "away_team")),
    # players = the id crosswalk (gsis/pfr/espn/pff/...), the join table for name-matching book props to stats.
    _single("players", "players", "players", "core", ("gsis_id",)),
    _single("teams", "teams", "teams_colors_logos", "core"),
    _single("officials", "officials", "officials", "core"),
    # weekly player stat lines: the natural SECOND GRADER for prop settlement (ev design s4/s7.4).
    _per_season("player_stats", "stats_player", "stats_player_week_{season}", 1999, "core",
                ("player_id", "season", "week", "team")),
    _per_season("team_stats", "stats_team", "stats_team_week_{season}", 1999, "core",
                ("season", "week", "team")),
    _per_season("rosters", "rosters", "roster_{season}", 1920, "core", ("season", "team"), roster_year=True),
    _per_season("rosters_weekly", "weekly_rosters", "roster_weekly_{season}", 2002, "core", ("season", "week")),
    _per_season("injuries", "injuries", "injuries_{season}", 2009, "core", ("season", "week", "team")),
    _per_season("depth_charts", "depth_charts", "depth_charts_{season}", 2001, "core", roster_year=True),
    _per_season("snap_counts", "snap_counts", "snap_counts_{season}", 2012, "core",
                ("game_id", "season", "week", "team")),
    # ---- detail: large / specialised (play-by-play and tracking-derived) ----
    _per_season("pbp", "pbp", "play_by_play_{season}", 1999, "detail", ("game_id", "play_id", "season")),
    # participation: the in-progress season is NOT published until after the final week (FTN supplies it late,
    # per nflreadpy.load_participation) -- a 404 for the newest season is expected, not a failure.
    _per_season("participation", "pbp_participation", "pbp_participation_{season}", 2016, "detail",
                ("nflverse_game_id", "play_id")),
    _per_season("ftn_charting", "ftn_charting", "ftn_charting_{season}", 2022, "detail", ("season",)),
    # Next Gen Stats: ONE file per stat type covering all seasons.
    _single("ngs_passing", "nextgen_stats", "ngs_passing", "detail", ("season", "week")),
    _single("ngs_receiving", "nextgen_stats", "ngs_receiving", "detail", ("season", "week")),
    _single("ngs_rushing", "nextgen_stats", "ngs_rushing", "detail", ("season", "week")),
    # PFR advanced stats, weekly, per stat type per season (2018+).
    *[_per_season(f"pfr_{t}", "pfr_advstats", f"advstats_week_{t}_{{season}}", 2018, "detail")
      for t in ("pass", "rush", "rec", "def")],
    # ffverse expected-opportunity model (weekly): model-expected yards / TDs / fantasy points from each player's
    # actual opportunities (targets, air yards, carries by field position) -- the "what SHOULD he have scored"
    # series that regression-to-the-mean features need. Published by ffverse/ffopportunity (release tag
    # `latest-data`, asset ep_weekly_{season}), NOT nflverse-data; nflreadpy's load_ff_opportunity builds the same
    # path (floor 2006). URL existence verified by HEAD 2026-10-16 (2024: 1.1 MB, 2026: 0.4 MB); NO dictionary-verified
    # columns yet, so structural validation only until a first real download is inspected. Group `extra` (opt-in:
    # --groups extra / --datasets ff_opportunity) because no feature consumes it yet.
    Dataset("ff_opportunity", "latest-data", "ep_weekly_{season}", True, 2006, "extra", base=FFOPP_BASE),
    # ---- extra: not needed for betting work; opt-in via --groups extra ----
    _single("contracts", "contracts", "historical_contracts", "extra"),
    _single("draft_picks", "draft_picks", "draft_picks", "extra", ("season",)),
    _single("trades", "trades", "trades", "extra"),
    _single("combine", "combine", "combine", "extra", ("season",)),
]

DATASETS = {d.name: d for d in _DATASETS}
GROUPS = ("core", "detail", "extra")
DEFAULT_GROUPS = ("core", "detail")


# -- Current season (mirrors nflreadpy.utils_date.get_current_season) -----------
def current_season(today: Optional[date] = None, roster: bool = False) -> int:
    """Season year: rolls over on the Wednesday after Labor Day. roster=True rolls on Mar 15.

    nflverse stamps a season by the year it STARTS, so January/February games (and the Super
    Bowl) still belong to the previous calendar year's season.
    """
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    if roster:
        return today.year if today >= date(today.year, 3, 15) else today.year - 1
    labor_day = next(date(today.year, 9, d) for d in range(1, 8) if date(today.year, 9, d).weekday() == 0)
    season_start = date(today.year, 9, labor_day.day + 2)
    return today.year if today >= season_start else today.year - 1
