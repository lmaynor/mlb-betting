"""
mlb_core.data.auxiliary_features -- Additional free data sources for feature enrichment.

Four sources, all free, all cached to GCS:

  1. Baseball Reference Pitcher Quality (via pybaseball, per season)
     FIP, ERA, WHIP, SO9 (K/9), BB9, HR9
     GCS: AuxData/fangraphs_pitching_{year}.csv / fangraphs_pitching_master.csv
     Available: 2015+  (FanGraphs was blocked from Cloud IPs; B-Ref is not)

  2. Savant Swing-Take Leaderboard (pitcher, per season)
     chase_rate, heart_swing_rate, shadow_swing_rate, chase_rv, heart_rv
     GCS: AuxData/swing_take_{year}.csv / swing_take_master.csv
     Available: 2020+

  3. Team Schedule Features (per team, per game_pk)
     days_rest, is_home, travel_miles, home_away_streak, series_game_num
     GCS: AuxData/team_schedule_{year}.csv / team_schedule_master.csv
     Available: any season via MLB Stats API

  4. Manager Hook Tendencies (derived from statcast_master -- no external API)
     avg_starter_outs_L30, pct_quick_hooks_L30, pct_quality_starts_L30
     GCS: AuxData/manager_hooks_master.csv
     Rebuilt from statcast_master on request.

  5. Catcher Pop Time / Arm Strength (via pybaseball, per season) -- added
     for the SB (stolen base) model, 2026-08-20.
     maxeff_arm_2b_3b_sba, exchange_2b_3b_sba, pop_2b_sba, pop_2b_cs,
     pop_2b_sb, pop_3b_sba, pop_3b_cs, pop_3b_sb
     GCS: AuxData/catcher_poptime_{year}.csv / catcher_poptime_master.csv
     Available: 2015+ (same Statcast rollout window as sprint_speed).
     pybaseball.statcast_catcher_poptime() wraps
     baseballsavant.mlb.com/leaderboard/poptime -- same "call pybaseball
     directly" pattern as source 1 (B-Ref pitching), a real, working,
     already-installed function verified live before this was written.

Nightly entry point (called from /refresh-data Loop A):
  auxiliary_features_nightly_gcs()

Backfill entry points:
  fangraphs_backfill_gcs(start_year, end_year, force=False)
  swing_take_backfill_gcs(start_year, end_year, force=False)
  team_schedule_backfill_gcs(start_year, end_year, force=False)
  catcher_poptime_backfill_gcs(start_year, end_year, force=False)

Load helpers (for feature builders):
  load_fangraphs_pitching(years=None)  -> DataFrame keyed (name_norm, year)
  load_swing_take(years=None)          -> DataFrame keyed (player_id, year)
  load_team_schedule(years=None)       -> DataFrame keyed (team, game_pk)
  load_manager_hooks()                 -> DataFrame keyed (team, game_date)
  load_catcher_poptime(years=None)     -> DataFrame keyed (player_id, year)

Baseball Reference note:
  Uses pybaseball.pitching_stats_bref(season) -- B-Ref is not bot-protected
  like FanGraphs. Key columns: FIP, SO9, BB9, WHIP, ERA.
  If fetch fails, check pybaseball version or B-Ref schema changes.

Swing-Take URL note:
  Baseball Savant /leaderboard/swing-take was added in 2024. If the
  endpoint is unavailable for older seasons it returns an empty CSV or
  HTML -- both are handled gracefully. Verify new column names each
  off-season since Savant renames leaderboards occasionally.
"""
import io
import math
import logging
import random
import time
import unicodedata
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FANGRAPHS_START_YEAR = 2015
_SWING_TAKE_START_YEAR = 2024   # leaderboard added 2024; pre-2024 returns empty
_SCHEDULE_START_YEAR = 2021   # matches statcast_master window
_POPTIME_START_YEAR = 2015     # same Statcast rollout window as sprint_speed

_FANGRAPHS_MIN_IP = 20        # minimum IP filter for FanGraphs plate discipline
_SWING_TAKE_MIN   = 50        # minimum pitches for Swing-Take leaderboard
_HOOKS_LOOKBACK   = 30        # rolling window in games for manager hooks
_POPTIME_MIN_2B_ATT = 5        # minimum 2B steal attempts for a catcher to qualify

# MLB Stats API team ID <-> 3-letter abbreviation (stable across seasons).
# Consolidated 2026-09-04 into mlb_core.data.team_ids (was hardcoded here
# independently of the near-identical copies in id_resolver.py/lineups.py).
from mlb_core.data.team_ids import (
    TEAM_ID_TO_ABBREV as _TEAM_ID_TO_ABBREV,
    ABBREV_TO_TEAM_ID as _ABBREV_TO_TEAM_ID,
    MLB_TEAM_IDS as _MLB_TEAM_IDS,
)

_BACKFILL_SLEEP_MIN = 8.0    # seconds between backfill calls (min)
_BACKFILL_SLEEP_MAX = 14.0   # seconds between backfill calls (max)
_NIGHTLY_SLEEP      = 3.0    # seconds between nightly sources

# GCS key prefixes (without _year.csv or _master.csv suffix)
_FG_PREFIX    = "AuxData/fangraphs_pitching"
_ST_PREFIX    = "AuxData/swing_take"
_SCHED_PREFIX = "AuxData/team_schedule"
_HOOKS_KEY    = "AuxData/manager_hooks_master.csv"
_POP_PREFIX   = "AuxData/catcher_poptime"

# Out-generating PA events in statcast -- used for outs_recorded proxy
_OUT_EVENTS = frozenset({
    "strikeout", "field_out", "force_out", "grounded_into_double_play",
    "double_play", "triple_play", "sac_fly", "sac_bunt",
    "fielders_choice_out", "strikeout_double_play",
})

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
})


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    """NFD + ASCII fold + lower + strip -- matches game_result.py normalization."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode()
    return n.lower().strip()


def norm_statcast_name(player_name: str) -> str:
    """Convert a Statcast 'Last, First' player_name to bref_pitching name_norm format.

    Statcast stores pitcher names as 'Cole, Gerrit'.
    bref_pitching.name_norm is 'gerrit cole' (from B-Ref 'Gerrit Cole').

    Feature builders must use this before joining to bref_pitching on name_norm:

        df["bref_key"] = df["player_name"].apply(norm_statcast_name)
        bref = load_fangraphs_pitching()
        df = df.merge(bref[["name_norm","year","FIP","SO9","BB9","WHIP"]],
                      left_on=["bref_key","year"], right_on=["name_norm","year"],
                      how="left")

    For swing_take and team_schedule / manager_hooks there is no name join --
    use player_id (MLBAM) or (team, game_pk) instead.
    """
    parts = str(player_name).split(", ")
    if len(parts) == 2:
        return _norm_name(f"{parts[1]} {parts[0]}")
    return _norm_name(player_name)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = 3959.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2.0 * R * math.asin(math.sqrt(max(0.0, a)))


def _fetch_csv_url(url: str, source: str, year: int,
                   max_retries: int = 4) -> pd.DataFrame | None:
    """Fetch a CSV URL with HTML detection, exponential backoff, and logging."""
    logger.info(f"auxiliary_features: fetching {source} {year} -- {url}")
    for attempt in range(max_retries):
        try:
            resp = _SESSION.get(url, timeout=30)
            resp.raise_for_status()
            content = resp.text.strip()
            if content.startswith("<!") or content.startswith("<html"):
                wait = 30 + attempt * 15
                logger.warning(
                    f"auxiliary_features: HTML response for {source} {year} "
                    f"(attempt {attempt + 1}/{max_retries}) -- likely rate-limited, "
                    f"sleeping {wait}s"
                )
                time.sleep(wait)
                continue
            if len(content) < 20:
                logger.info(
                    f"auxiliary_features: empty response for {source} {year} "
                    f"(len={len(content)})"
                )
                return None
            df = pd.read_csv(io.StringIO(content), low_memory=False)
            if df.empty:
                logger.info(f"auxiliary_features: 0-row CSV for {source} {year}")
                return None
            logger.info(
                f"auxiliary_features: {source} {year} -> "
                f"{len(df):,} rows, {len(df.columns)} cols | "
                f"first cols: {list(df.columns[:8])}"
            )
            return df
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            wait = 45 + attempt * 20 if status == 403 else (2 ** attempt) * 5
            logger.warning(
                f"auxiliary_features: HTTP {status} for {source} {year} "
                f"(attempt {attempt + 1}/{max_retries}), sleeping {wait}s"
            )
            time.sleep(wait)
        except Exception as e:
            wait = (2 ** attempt) * 3 + random.uniform(1, 3)
            logger.warning(
                f"auxiliary_features: fetch error {source} {year} "
                f"(attempt {attempt + 1}/{max_retries}): {e}, sleeping {wait:.1f}s"
            )
            time.sleep(wait)
    logger.error(
        f"auxiliary_features: all {max_retries} attempts failed "
        f"for {source} {year} -- returning None"
    )
    return None


def _gcs_year_key(prefix: str, year: int) -> str:
    return f"{prefix}_{year}.csv"


def _gcs_master_key(prefix: str) -> str:
    return f"{prefix}_master.csv"


def _rebuild_master(prefix: str, years: list[int],
                    id_cols: list[str]) -> pd.DataFrame:
    """Concat all per-year GCS files for prefix into a master, dedup, write."""
    from mlb_core.storage import read_csv, write_csv, exists

    frames = []
    for year in sorted(years):
        key = _gcs_year_key(prefix, year)
        if not exists(key):
            logger.debug(f"auxiliary_features: {key} not in GCS, skipping in master")
            continue
        try:
            df = read_csv(key, low_memory=False)
            if not df.empty:
                frames.append(df)
                logger.debug(f"auxiliary_features: loaded {key} ({len(df):,} rows)")
        except Exception as e:
            logger.warning(f"auxiliary_features: could not load {key}: {e}")

    if not frames:
        logger.warning(f"auxiliary_features: no year files found for master {prefix}")
        return pd.DataFrame()

    master = pd.concat(frames, ignore_index=True)
    valid_id = [c for c in id_cols if c in master.columns]
    if valid_id:
        before = len(master)
        master = master.drop_duplicates(subset=valid_id, keep="last")
        logger.debug(
            f"auxiliary_features: {prefix} dedup {before:,} -> {len(master):,} "
            f"on {valid_id}"
        )

    sort_cols = [c for c in ["year", "team", "game_date"] if c in master.columns]
    if sort_cols:
        master = master.sort_values(sort_cols).reset_index(drop=True)

    master_key = _gcs_master_key(prefix)
    write_csv(master, master_key)
    n_seasons = master["year"].nunique() if "year" in master.columns else "?"
    logger.info(
        f"auxiliary_features: master {prefix} rebuilt -- "
        f"{len(master):,} rows, {n_seasons} seasons -> {master_key}"
    )
    return master


def _backfill_generic(prefix: str, start_year: int, end_year: int,
                      fetch_fn, id_cols: list[str],
                      force: bool = False) -> dict:
    """Generic backfill: fetch each year, cache to GCS, rebuild master."""
    from mlb_core.storage import write_csv, exists

    years = list(range(start_year, end_year + 1))
    logger.info(
        f"auxiliary_features: backfill {prefix} "
        f"{start_year}-{end_year} ({len(years)} seasons)"
    )
    results: dict[int, int] = {}
    fetched_years: list[int] = []

    for i, year in enumerate(years):
        key = _gcs_year_key(prefix, year)
        if not force and exists(key):
            logger.info(
                f"auxiliary_features: {key} already cached -- skipping "
                f"(pass force=True to re-fetch)"
            )
            results[year] = -1   # sentinel: skipped/already cached
            fetched_years.append(year)
            continue

        df = fetch_fn(year)
        if df is None or df.empty:
            logger.warning(f"auxiliary_features: no data for {prefix} {year}")
            results[year] = 0
        else:
            write_csv(df, key)
            results[year] = len(df)
            fetched_years.append(year)
            logger.info(
                f"auxiliary_features: cached {prefix} {year} ({len(df):,} rows) -> {key}"
            )

        if i < len(years) - 1:
            sleep_secs = random.uniform(_BACKFILL_SLEEP_MIN, _BACKFILL_SLEEP_MAX)
            logger.debug(f"auxiliary_features: sleeping {sleep_secs:.1f}s before next year")
            time.sleep(sleep_secs)

    _rebuild_master(prefix, list(range(start_year, end_year + 1)), id_cols)

    total_new = sum(v for v in results.values() if v > 0)
    skipped   = sum(1 for v in results.values() if v == -1)
    logger.info(
        f"auxiliary_features: backfill {prefix} complete -- "
        f"{total_new:,} rows fetched, {skipped} years skipped (cached)"
    )
    return results


def _nightly_generic(prefix: str, start_year: int, fetch_fn,
                     id_cols: list[str], source_name: str) -> dict:
    """Generic nightly: re-fetch current season and rebuild master."""
    from mlb_core.storage import write_csv

    year  = date.today().year
    month = date.today().month

    if not (3 <= month <= 11):
        logger.info(
            f"auxiliary_features: skipping nightly {source_name} -- "
            f"off-season (month={month})"
        )
        return {"status": "skipped", "reason": "off_season"}

    logger.info(f"auxiliary_features: nightly {source_name} {year}")
    df = fetch_fn(year)

    if df is None or df.empty:
        logger.warning(
            f"auxiliary_features: nightly {source_name} {year} returned no data"
        )
        return {"status": "error", "rows": 0}

    key = _gcs_year_key(prefix, year)
    write_csv(df, key)
    logger.info(
        f"auxiliary_features: nightly {source_name} {year} -> "
        f"{len(df):,} rows -> {key}"
    )

    all_years = list(range(start_year, year + 1))
    _rebuild_master(prefix, all_years, id_cols)

    time.sleep(_NIGHTLY_SLEEP)
    return {"status": "ok", "year": year, "rows": len(df)}


# ===========================================================================
# 1. FanGraphs Plate Discipline (via pybaseball)
# ===========================================================================

# B-Ref columns that must be present to proceed (raw counting stats only).
_FG_REQUIRED_COLS = {"ERA", "SO", "IP"}

# FIP constant (league-average, stable enough for feature engineering).
_FIP_CONSTANT = 3.10

# pybaseball / B-Ref column name candidates for pitcher display name.
_FG_NAME_COLS = ["Name", "PlayerName", "playerName"]

# B-Ref columns to keep (anything extra is noise for our use case).
_BREF_KEEP_COLS = [
    "year", "name_norm", "Name", "Tm",
    "IP", "ERA", "FIP", "WHIP", "SO9", "BB9", "HR9", "ERA+",
    "G", "GS", "W", "L", "SV",
    # SB, CS: stolen bases / caught stealing ALLOWED while this pitcher was
    # on the mound -- added for the SB model, 2026-08-20. Real B-Ref
    # columns confirmed present in pybaseball.pitching_stats_bref() output
    # (checked live before adding this). Purely additive: no existing
    # consumer of this master (join_pitcher_aux's _BREF_COLS) references
    # them, so NRFI/K/F5/GAME are unaffected.
    "SB", "CS",
    # PO: successful pickoffs BY this pitcher -- added for the SB model,
    # 2026-08-21, prompted by an external reference project (an R paper
    # modeling optimal stolen-base leads) using a pitcher "threat" stat
    # built from raw pickoff-ATTEMPT rate. B-Ref/boxscores only expose
    # successful pickoffs (rarer, noisier than attempts would be -- true
    # attempt rate needs play-by-play parsing, not done here), but it's a
    # real, cheap, additive signal distinct from pitcher_sb_allowed/
    # pitcher_cs_allowed: this measures the pitcher's own pickoff-move
    # skill/usage (deters attempts outright) rather than outcomes of
    # attempts that were already made. Confirmed present in
    # pybaseball.pitching_stats_bref() output before adding this.
    "PO",
]


def _fetch_fangraphs_pitching(year: int) -> pd.DataFrame | None:
    """Fetch pitcher quality metrics from Baseball Reference via pybaseball.

    FanGraphs blocks all requests from Cloud IPs (HTTP 403). Baseball
    Reference does not. pybaseball.pitching_stats_bref(season) returns
    seasonal pitcher stats from B-Ref: ERA, FIP, WHIP, SO9, BB9, HR9.

    Filters to pitchers with >= _FANGRAPHS_MIN_IP innings pitched.
    Returns None if pybaseball unavailable or the fetch fails.
    """
    try:
        import pybaseball
    except ImportError:
        logger.error(
            "auxiliary_features: pybaseball not installed. "
            "Run: pip install pybaseball==2.2.7"
        )
        return None

    logger.info(f"auxiliary_features: pybaseball.pitching_stats_bref {year}")

    try:
        df = pybaseball.pitching_stats_bref(year)
    except Exception as e:
        logger.warning(
            f"auxiliary_features: pybaseball.pitching_stats_bref {year} failed: {e}"
        )
        return None

    if df is None or (hasattr(df, "empty") and df.empty):
        logger.info(
            f"auxiliary_features: pitching_stats_bref {year} returned empty"
        )
        return None

    logger.info(
        f"auxiliary_features: pitching_stats_bref {year} -> "
        f"{len(df):,} rows | cols: {list(df.columns[:16])}"
    )

    # Filter to minimum IP
    if "IP" in df.columns:
        df = df.copy()
        df["IP"] = pd.to_numeric(df["IP"], errors="coerce")
        before = len(df)
        df = df[df["IP"] >= _FANGRAPHS_MIN_IP]
        logger.debug(
            f"auxiliary_features: bref {year} IP>={_FANGRAPHS_MIN_IP} "
            f"filter: {before:,} -> {len(df):,}"
        )

    if df.empty:
        logger.info(f"auxiliary_features: bref {year} empty after IP filter")
        return None

    missing = _FG_REQUIRED_COLS - set(df.columns)
    if missing:
        logger.warning(
            f"auxiliary_features: bref_pitching {year} -- "
            f"required cols {missing} absent. Got: {list(df.columns[:20])}. "
            f"Check pybaseball version or B-Ref schema change."
        )
        return None

    if "year" not in df.columns:
        df.insert(0, "year", year)

    name_col = next((c for c in _FG_NAME_COLS if c in df.columns), None)
    if name_col:
        df["name_norm"] = df[name_col].apply(_norm_name)
        logger.debug(
            f"auxiliary_features: bref {year} name_norm "
            f"from '{name_col}': {df['name_norm'].iloc[:3].tolist()}"
        )
    else:
        logger.warning(
            f"auxiliary_features: bref {year} -- no name column found, "
            f"name_norm absent (join will fail)"
        )

    # Coerce numeric cols (B-Ref sometimes returns strings for IP/HR/BB)
    for col in ["IP", "HR", "BB", "HBP", "SO", "H", "ER", "ERA"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Compute derived metrics from counting stats (B-Ref doesn't return these)
    ip = df["IP"].replace(0, float("nan"))
    hr  = df.get("HR",  pd.Series(0, index=df.index))
    bb  = df.get("BB",  pd.Series(0, index=df.index))
    hbp = df.get("HBP", pd.Series(0, index=df.index))
    so  = df["SO"]
    h   = df.get("H",   pd.Series(0, index=df.index))
    df["FIP"]  = (13 * hr + 3 * (bb + hbp) - 2 * so) / ip + _FIP_CONSTANT
    df["WHIP"] = (h + bb) / ip
    df["SO9"]  = so * 9.0 / ip
    df["BB9"]  = bb * 9.0 / ip
    df["HR9"]  = hr * 9.0 / ip
    logger.debug(
        f"auxiliary_features: bref {year} computed FIP/WHIP/SO9/BB9/HR9 "
        f"from counting stats"
    )

    # Keep only the columns we care about (plus any extras present)
    keep = [c for c in _BREF_KEEP_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in _BREF_KEEP_COLS]
    df = df[keep + extra].copy()

    # Rename raw B-Ref SB/CS (allowed, while this pitcher was on the mound)
    # to explicit names -- "SB"/"CS" alone reads as the pitcher's own
    # baserunning, which is nonsensical; this is defense, not offense.
    # PO -> pitcher_pickoffs: this one IS the pitcher's own action (a pickoff
    # move he executed), so no offense/defense ambiguity, but still renamed
    # for consistency and to self-document the SB-model provenance.
    df = df.rename(columns={
        "SB": "pitcher_sb_allowed", "CS": "pitcher_cs_allowed",
        "PO": "pitcher_pickoffs",
    })

    fip_mean = df["FIP"].mean()
    so9_mean = df["SO9"].mean()
    nan_fip  = int(df["FIP"].isna().sum())
    logger.info(
        f"auxiliary_features: bref_pitching {year} -- "
        f"{len(df):,} pitchers | "
        f"FIP mean={fip_mean:.3f} NaN={nan_fip} | "
        f"SO9 mean={so9_mean:.2f}"
    )
    return df


def fangraphs_backfill_gcs(
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill Baseball Reference pitcher stats (FIP, K/9, BB/9, WHIP) to GCS.

    Args:
        start_year: First season to fetch (default: 2015).
        end_year:   Last season to fetch (default: current year).
        force:      Re-fetch even if year file already cached in GCS.

    Returns:
        Dict {year: rows_fetched} (-1 = skipped/already cached, 0 = no data).
    """
    start = start_year or _FANGRAPHS_START_YEAR
    end   = end_year   or date.today().year
    return _backfill_generic(
        _FG_PREFIX, start, end,
        _fetch_fangraphs_pitching,
        id_cols=["year", "name_norm"],
        force=force,
    )


def fangraphs_nightly_gcs() -> dict:
    """Nightly refresh of current season B-Ref pitcher stats."""
    return _nightly_generic(
        _FG_PREFIX, _FANGRAPHS_START_YEAR,
        _fetch_fangraphs_pitching,
        id_cols=["year", "name_norm"],
        source_name="bref_pitching",
    )


def load_fangraphs_pitching(years: list[int] | None = None) -> pd.DataFrame:
    """Load FanGraphs pitcher plate discipline master from GCS/local.

    Feature builders join on (name_norm, year). Because Statcast stores
    player_name as 'Last, First' and bref uses 'First Last', you must
    call norm_statcast_name(player_name) before joining:

        df["bref_key"] = df["player_name"].apply(norm_statcast_name)
        year = pd.to_datetime(df["game_date"]).dt.year
        bref = load_fangraphs_pitching()
        merged = df.merge(bref[["name_norm","year","FIP","SO9","BB9","WHIP"]],
                          left_on=["bref_key", year],
                          right_on=["name_norm","year"], how="left")

    Args:
        years: If provided, filter to these seasons only.

    Returns:
        DataFrame with name_norm, year, FIP, SO9, BB9, WHIP, ERA, ERA+,
        pitcher_sb_allowed, pitcher_cs_allowed (added 2026-08-20 for the SB
        model -- stolen bases / caught stealing allowed while this pitcher
        was on the mound, real Baseball-Reference counting stats),
        pitcher_pickoffs (added 2026-08-21, also for the SB model --
        successful pickoffs BY this pitcher, a pickoff-move-skill signal
        distinct from the two above).
        Empty DataFrame if master not found.
    """
    from mlb_core.storage import read_csv, exists

    key = _gcs_master_key(_FG_PREFIX)
    if not exists(key):
        logger.warning(f"auxiliary_features: fangraphs master not found: {key}")
        return pd.DataFrame()

    try:
        df = read_csv(key, low_memory=False)
    except Exception as e:
        logger.error(f"auxiliary_features: load fangraphs master failed: {e}")
        return pd.DataFrame()

    if years is not None and "year" in df.columns:
        df = df[df["year"].isin(years)].copy()

    logger.debug(
        f"auxiliary_features: loaded fangraphs_pitching {len(df):,} rows"
        + (f" (years={years})" if years else "")
    )
    return df


# ===========================================================================
# 2. Savant Swing-Take Leaderboard
# ===========================================================================

# Columns expected from Savant Swing-Take endpoint.
# Savant occasionally renames columns -- log what we actually got.
_SWING_TAKE_REQUIRED_ANY = {"chase_rate", "swing_rate", "heart_swing_rate", "player_id"}


def _fetch_swing_take(year: int) -> pd.DataFrame | None:
    """Fetch Baseball Savant Swing-Take leaderboard for pitchers.

    Endpoint: /leaderboard/swing-take (added 2024). Returns per player
    chase_rate, heart_swing_rate, shadow_swing_rate, run values, etc.
    The leaderboard returns both pitchers and batters; we keep pitchers
    by filtering on pa_type or equivalent column when present.

    Note: type=pitcher causes 0-row responses -- this endpoint does not
    accept a player_type filter in the URL. Fetch all and filter in code.
    """
    url = (
        f"https://baseballsavant.mlb.com/leaderboard/swing-take"
        f"?year={year}&min={_SWING_TAKE_MIN}&csv=true"
    )
    df = _fetch_csv_url(url, "swing_take", year)
    if df is None or df.empty:
        return None

    got_cols = set(df.columns)
    logger.info(
        f"auxiliary_features: swing_take {year} raw -> "
        f"{len(df):,} rows | cols: {sorted(got_cols)}"
    )

    if not _SWING_TAKE_REQUIRED_ANY.intersection(got_cols):
        logger.warning(
            f"auxiliary_features: swing_take {year} -- "
            f"none of {_SWING_TAKE_REQUIRED_ANY} found. "
            f"Savant may have changed this endpoint -- check manually."
        )
        return None

    # Keep pitchers only. Savant returns a combined leaderboard; the
    # pitcher/batter split lives in a 'pa_type' or 'player_type' column
    # when present. Fallback: keep all rows (both views share the same schema).
    for type_col in ("pa_type", "player_type", "type"):
        if type_col in df.columns:
            before = len(df)
            df = df[df[type_col].astype(str).str.lower() == "pitcher"].copy()
            logger.info(
                f"auxiliary_features: swing_take {year} pitcher filter "
                f"on '{type_col}': {before} -> {len(df)} rows"
            )
            break
    else:
        logger.debug(
            f"auxiliary_features: swing_take {year} -- no type column found, "
            f"keeping all {len(df)} rows (may include batters)"
        )

    if df.empty:
        logger.warning(f"auxiliary_features: swing_take {year} empty after pitcher filter")
        return None

    if "year" not in df.columns:
        df.insert(0, "year", year)

    # Build name_norm from first/last name columns for fallback joins
    if "last_name" in df.columns and "first_name" in df.columns:
        df["name_norm"] = (
            (df["first_name"].fillna("") + " " + df["last_name"].fillna(""))
            .apply(_norm_name)
        )
    elif "last_name, first_name" in df.columns:
        # Savant sometimes returns "Last, First" in a single combined column
        def _split_savant_name(s: str) -> str:
            parts = str(s).split(", ")
            return _norm_name(f"{parts[1]} {parts[0]}") if len(parts) == 2 else _norm_name(s)
        df["name_norm"] = df["last_name, first_name"].apply(_split_savant_name)

    logger.info(
        f"auxiliary_features: swing_take {year} -- "
        f"{len(df):,} pitchers retained"
    )
    return df


def swing_take_backfill_gcs(
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill Savant Swing-Take leaderboard to GCS.

    Note: endpoint only exists from 2020. Years before that return empty/HTML.

    Args:
        start_year: First season (default: 2020).
        end_year:   Last season (default: current year).
        force:      Re-fetch even if cached.
    """
    start = start_year or _SWING_TAKE_START_YEAR
    end   = end_year   or date.today().year
    return _backfill_generic(
        _ST_PREFIX, start, end,
        _fetch_swing_take,
        id_cols=["year", "player_id"],
        force=force,
    )


def swing_take_nightly_gcs() -> dict:
    """Nightly refresh of current season Swing-Take leaderboard."""
    return _nightly_generic(
        _ST_PREFIX, _SWING_TAKE_START_YEAR,
        _fetch_swing_take,
        id_cols=["year", "player_id"],
        source_name="swing_take",
    )


def load_swing_take(years: list[int] | None = None) -> pd.DataFrame:
    """Load Savant Swing-Take master from GCS/local.

    Feature builders join on (player_id, year) for pitchers, or
    (name_norm, year) as fallback.
    """
    from mlb_core.storage import read_csv, exists

    key = _gcs_master_key(_ST_PREFIX)
    if not exists(key):
        logger.warning(f"auxiliary_features: swing_take master not found: {key}")
        return pd.DataFrame()

    try:
        df = read_csv(key, low_memory=False)
    except Exception as e:
        logger.error(f"auxiliary_features: load swing_take failed: {e}")
        return pd.DataFrame()

    if years is not None and "year" in df.columns:
        df = df[df["year"].isin(years)].copy()

    logger.debug(f"auxiliary_features: loaded swing_take {len(df):,} rows")
    return df


# ===========================================================================
# 5. Catcher Pop Time / Arm Strength (for the SB model)
# ===========================================================================


def _fetch_catcher_poptime(year: int) -> pd.DataFrame | None:
    """Fetch catcher pop time / arm strength from Baseball Savant via pybaseball.

    pybaseball.statcast_catcher_poptime(year) wraps
    baseballsavant.mlb.com/leaderboard/poptime -- verified live 2026-08-20
    (handoffs/scope_stolen_base_model_2026-08-20.md s1) to return real
    columns: entity_name, entity_id (catcher MLBAM id), team_id, age,
    maxeff_arm_2b_3b_sba (arm strength), exchange_2b_3b_sba (exchange time),
    pop_2b_sba/_cs/_sb (pop time overall / on caught-stealing / on stolen
    bases specifically), and the 3B-throw equivalents. Same
    "call pybaseball directly" pattern as _fetch_fangraphs_pitching (source 1).

    Renamed to match this module's other loaders: entity_id -> player_id,
    entity_name -> name.
    """
    try:
        import pybaseball
    except ImportError:
        logger.error(
            "auxiliary_features: pybaseball not installed. "
            "Run: pip install pybaseball==2.2.7"
        )
        return None

    logger.info(f"auxiliary_features: pybaseball.statcast_catcher_poptime {year}")

    try:
        df = pybaseball.statcast_catcher_poptime(year, min_2b_att=_POPTIME_MIN_2B_ATT)
    except Exception as e:
        logger.warning(
            f"auxiliary_features: statcast_catcher_poptime {year} failed: {e}"
        )
        return None

    if df is None or (hasattr(df, "empty") and df.empty):
        logger.info(f"auxiliary_features: catcher_poptime {year} returned empty "
                    f"(pop time tracking may not extend this far back)")
        return None

    df = df.rename(columns={"entity_id": "player_id", "entity_name": "name"})

    if "player_id" not in df.columns:
        logger.warning(
            f"auxiliary_features: catcher_poptime {year} -- no entity_id/player_id "
            f"column found. Got: {list(df.columns)}. Check pybaseball version."
        )
        return None

    if "year" not in df.columns:
        df.insert(0, "year", year)

    logger.info(
        f"auxiliary_features: catcher_poptime {year} -- "
        f"{len(df):,} catchers | "
        f"pop_2b_sba mean={df['pop_2b_sba'].mean():.3f}" if "pop_2b_sba" in df.columns
        else f"auxiliary_features: catcher_poptime {year} -- {len(df):,} catchers"
    )
    return df


def catcher_poptime_backfill_gcs(
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill catcher pop time / arm strength to GCS.

    Args:
        start_year: First season to fetch (default: 2015).
        end_year:   Last season to fetch (default: current year).
        force:      Re-fetch even if year file already cached in GCS.
    """
    start = start_year or _POPTIME_START_YEAR
    end   = end_year   or date.today().year
    return _backfill_generic(
        _POP_PREFIX, start, end,
        _fetch_catcher_poptime,
        id_cols=["year", "player_id"],
        force=force,
    )


def catcher_poptime_nightly_gcs() -> dict:
    """Nightly refresh of current season catcher pop time."""
    return _nightly_generic(
        _POP_PREFIX, _POPTIME_START_YEAR,
        _fetch_catcher_poptime,
        id_cols=["year", "player_id"],
        source_name="catcher_poptime",
    )


def load_catcher_poptime(years: list[int] | None = None) -> pd.DataFrame:
    """Load catcher pop time / arm strength master from GCS/local.

    Feature builders join on (player_id, year) where player_id is the
    CATCHER's MLBAM id (resolve today's starting catcher first via
    mlb_core.data.lineups.get_starting_catchers()).

    Returns:
        DataFrame with player_id, year, name, maxeff_arm_2b_3b_sba,
        exchange_2b_3b_sba, pop_2b_sba, pop_2b_cs, pop_2b_sb,
        pop_3b_sba, pop_3b_cs, pop_3b_sb. Empty DataFrame if master not found.
    """
    from mlb_core.storage import read_csv, exists

    key = _gcs_master_key(_POP_PREFIX)
    if not exists(key):
        logger.warning(f"auxiliary_features: catcher_poptime master not found: {key}")
        return pd.DataFrame()

    try:
        df = read_csv(key, low_memory=False)
    except Exception as e:
        logger.error(f"auxiliary_features: load catcher_poptime failed: {e}")
        return pd.DataFrame()

    if years is not None and "year" in df.columns:
        df = df[df["year"].isin(years)].copy()

    logger.debug(f"auxiliary_features: loaded catcher_poptime {len(df):,} rows")
    return df


# ===========================================================================
# 3. Team Schedule Features (rest, travel, home/away streak, series position)
# ===========================================================================

_MLB_SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId=1&teamId={tid}&season={year}&gameType=R"
)


def _fetch_team_schedule_raw(team_id: int, year: int) -> list[dict]:
    """Fetch raw game list for one team/season from MLB Stats API."""
    url = _MLB_SCHEDULE_URL.format(tid=team_id, year=year)
    try:
        resp = _SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(
            f"auxiliary_features: schedule fetch failed "
            f"team_id={team_id} {year}: {e}"
        )
        return []

    rows = []
    for d in resp.json().get("dates", []):
        for g in d.get("games", []):
            away_id = g["teams"]["away"]["team"]["id"]
            home_id = g["teams"]["home"]["team"]["id"]
            opp_id  = away_id if team_id == home_id else home_id
            rows.append({
                "game_pk":    g["gamePk"],
                "game_date":  g["gameDate"][:10],   # YYYY-MM-DD
                "team_id":    team_id,
                "team":       _TEAM_ID_TO_ABBREV.get(team_id, str(team_id)),
                "is_home":    int(team_id == home_id),
                "opp_team_id": opp_id,
                "opp_team":   _TEAM_ID_TO_ABBREV.get(opp_id, str(opp_id)),
            })

    logger.debug(
        f"auxiliary_features: schedule team_id={team_id} {year} -> {len(rows)} games"
    )
    return rows


def _compute_schedule_features(games: list[dict], stadiums: dict) -> pd.DataFrame:
    """Compute rest, travel, streak, and series features from raw game list.

    stadiums: STADIUMS dict from mlb_core.data.weather -- tuple (lat, lon, roof, offset).
    Returns DataFrame with one row per team-game.
    """
    if not games:
        return pd.DataFrame()

    df = pd.DataFrame(games).sort_values("game_date").reset_index(drop=True)
    team_abbrev = df["team"].iloc[0]

    # --- days_rest ---
    df["game_date_dt"] = pd.to_datetime(df["game_date"])
    df["prev_date"]    = df["game_date_dt"].shift(1)
    df["days_rest"] = (
        (df["game_date_dt"] - df["prev_date"]).dt.days
        .fillna(0).clip(0, 14).astype(int)
    )

    # --- home_away_streak: consecutive home (positive) or away (negative) ---
    streaks: list[int] = []
    streak = 0
    for is_home in df["is_home"]:
        if is_home:
            streak = max(streak, 0) + 1
        else:
            streak = min(streak, 0) - 1
        streaks.append(streak)
    df["home_away_streak"] = streaks

    # --- series_game_num: game number in current homestand/road trip series ---
    series_nums: list[int] = []
    prev_opp   = None
    series_cnt = 0
    for opp in df["opp_team"]:
        if opp != prev_opp:
            series_cnt = 1
            prev_opp   = opp
        else:
            series_cnt += 1
        series_nums.append(series_cnt)
    df["series_game_num"] = series_nums

    # --- travel_miles: haversine between previous game's park and current park ---
    # Home park coordinates: stadiums[team][0]=lat, stadiums[team][1]=lon
    def _coords(abbrev: str) -> tuple[float, float] | None:
        entry = stadiums.get(abbrev)
        if entry and len(entry) >= 2:
            try:
                return (float(entry[0]), float(entry[1]))
            except (TypeError, ValueError):
                pass
        return None

    travel: list[float] = []
    prev_coords = _coords(team_abbrev)   # start at home city
    for _, row in df.iterrows():
        cur_coords = _coords(row["team"]) if row["is_home"] else _coords(row["opp_team"])
        if prev_coords is not None and cur_coords is not None:
            miles = _haversine_miles(prev_coords[0], prev_coords[1],
                                     cur_coords[0],  cur_coords[1])
        else:
            miles = float("nan")
        travel.append(miles)
        prev_coords = cur_coords

    df["travel_miles"] = travel

    n_nan_travel = sum(math.isnan(m) for m in travel)
    logger.debug(
        f"auxiliary_features: schedule features {team_abbrev} -- "
        f"{len(df)} games | avg_rest={df['days_rest'].mean():.1f} | "
        f"avg_travel={df['travel_miles'].mean():.0f}mi | "
        f"travel NaN={n_nan_travel}"
    )

    return df.drop(columns=["game_date_dt", "prev_date"], errors="ignore")


def _fetch_team_schedule_year(year: int) -> pd.DataFrame | None:
    """Fetch and compute schedule features for all 30 teams for one season."""
    try:
        from mlb_core.data.weather import STADIUMS
    except Exception as e:
        logger.warning(
            f"auxiliary_features: could not import STADIUMS from weather.py: {e} -- "
            f"travel_miles will be NaN"
        )
        STADIUMS = {}

    all_frames = []
    for i, tid in enumerate(_MLB_TEAM_IDS):
        games = _fetch_team_schedule_raw(tid, year)
        if games:
            features = _compute_schedule_features(games, STADIUMS)
            if not features.empty:
                features["year"] = year
                all_frames.append(features)
        # Brief pause between team API calls -- MLB Stats API is permissive
        # but 30 rapid calls per season backfill is still courteous
        if i < len(_MLB_TEAM_IDS) - 1:
            time.sleep(random.uniform(0.15, 0.40))

    if not all_frames:
        logger.warning(f"auxiliary_features: no schedule data produced for {year}")
        return None

    df = pd.concat(all_frames, ignore_index=True)
    # Doubleheader games appear once per team -- dedup is correct
    df = df.drop_duplicates(subset=["team", "game_pk"], keep="last")

    logger.info(
        f"auxiliary_features: team_schedule {year} -- "
        f"{len(df):,} team-game rows | "
        f"{df['game_pk'].nunique()} unique games | "
        f"{df['team'].nunique()} teams"
    )
    return df


def team_schedule_backfill_gcs(
    start_year: int | None = None,
    end_year: int | None = None,
    force: bool = False,
) -> dict:
    """Backfill team schedule features (rest, travel, streaks) to GCS.

    Args:
        start_year: First season (default: 2021 to match statcast_master).
        end_year:   Last season (default: current year).
        force:      Re-fetch even if year file already cached.
    """
    start = start_year or _SCHEDULE_START_YEAR
    end   = end_year   or date.today().year
    return _backfill_generic(
        _SCHED_PREFIX, start, end,
        _fetch_team_schedule_year,
        id_cols=["year", "team", "game_pk"],
        force=force,
    )


def team_schedule_nightly_gcs() -> dict:
    """Nightly refresh of current season team schedule features."""
    return _nightly_generic(
        _SCHED_PREFIX, _SCHEDULE_START_YEAR,
        _fetch_team_schedule_year,
        id_cols=["year", "team", "game_pk"],
        source_name="team_schedule",
    )


def load_team_schedule(years: list[int] | None = None) -> pd.DataFrame:
    """Load team schedule features master from GCS/local.

    Feature builders join on (team, game_pk) to get:
      days_rest, is_home, travel_miles, home_away_streak, series_game_num.
    """
    from mlb_core.storage import read_csv, exists

    key = _gcs_master_key(_SCHED_PREFIX)
    if not exists(key):
        logger.warning(f"auxiliary_features: team_schedule master not found: {key}")
        return pd.DataFrame()

    try:
        df = read_csv(key, low_memory=False)
    except Exception as e:
        logger.error(f"auxiliary_features: load team_schedule failed: {e}")
        return pd.DataFrame()

    if years is not None and "year" in df.columns:
        df = df[df["year"].isin(years)].copy()

    logger.debug(f"auxiliary_features: loaded team_schedule {len(df):,} rows")
    return df


# ===========================================================================
# 4. Manager Hook Tendencies (derived from statcast_master)
# ===========================================================================

def compute_manager_hooks(force: bool = False) -> pd.DataFrame:
    """Compute rolling manager hook tendency features from statcast_master.

    No external API -- derives everything from the existing statcast_master.
    Identifies the starter per game as the pitcher with the most BF faced,
    then counts outs via out-generating events.

    Per (team, game_date, game_pk) output columns:
      avg_starter_outs_L30  -- rolling 30-game mean of starter outs recorded
      pct_quick_hooks_L30   -- fraction of last 30 where starter < 15 outs (5 IP)
      pct_quality_starts_L30-- fraction of last 30 where starter >= 18 outs (6 IP)

    Args:
        force: Recompute and overwrite even if GCS file already exists.

    Returns:
        Computed DataFrame (also written to GCS at _HOOKS_KEY).
        Returns empty DataFrame on statcast load failure.
    """
    from mlb_core.storage import read_csv, write_csv, exists

    if not force and exists(_HOOKS_KEY):
        logger.info(
            f"auxiliary_features: manager_hooks already at {_HOOKS_KEY} -- "
            f"skipping recompute (pass force=True to rebuild)"
        )
        return load_manager_hooks()

    logger.info(
        "auxiliary_features: computing manager hooks from statcast_master -- "
        "loading statcast with usecols filter"
    )

    _needed = {"game_pk", "pitcher", "inning_topbot",
               "home_team", "away_team", "game_date", "events"}
    try:
        statcast = read_csv(
            "Statcast/statcast_master.csv",
            usecols=lambda c: c in _needed,
            low_memory=False,
        )
    except Exception as e:
        logger.error(
            f"auxiliary_features: failed to load statcast_master: {e} -- "
            f"cannot compute manager hooks"
        )
        return pd.DataFrame()

    logger.info(
        f"auxiliary_features: statcast loaded {len(statcast):,} rows | "
        f"cols: {list(statcast.columns)}"
    )

    # --- Identify starter per game: pitcher with most plate appearances ---
    agg = (
        statcast
        .groupby(["game_pk", "pitcher"])
        .agg(
            bf=("events", "count"),
            dominant_half=("inning_topbot", lambda x: x.mode().iloc[0] if len(x) else ""),
            home_team=("home_team", "first"),
            away_team=("away_team", "first"),
            game_date=("game_date", "first"),
        )
        .reset_index()
    )
    logger.debug(
        f"auxiliary_features: manager_hooks -- {len(agg):,} pitcher-game rows "
        f"before starter filter"
    )

    # Keep only max-BF pitcher per game per team (one starter per game side)
    idx_max   = agg.groupby(["game_pk", "dominant_half"])["bf"].idxmax()
    starters  = agg.loc[idx_max].copy()
    logger.info(
        f"auxiliary_features: manager_hooks -- {len(starters):,} starter-game rows "
        f"| avg BF={starters['bf'].mean():.1f}"
    )

    # Pitcher's team: Top inning = pitcher is on the home team (away bats in top)
    starters["pitcher_team"] = starters.apply(
        lambda r: r["home_team"] if r["dominant_half"] == "Top" else r["away_team"],
        axis=1,
    )

    # --- Count outs recorded by the starter ---
    # Statcast is PA-level: each row's 'events' is the PA outcome.
    # Filtering to out-generating events gives approximate outs recorded.
    outs_agg = (
        statcast[statcast["events"].isin(_OUT_EVENTS)]
        .groupby(["game_pk", "pitcher"])["events"]
        .count()
        .reset_index()
        .rename(columns={"events": "outs_recorded"})
    )
    starters = starters.merge(outs_agg, on=["game_pk", "pitcher"], how="left")
    starters["outs_recorded"] = starters["outs_recorded"].fillna(0).astype(int)

    logger.info(
        f"auxiliary_features: manager_hooks -- outs_recorded: "
        f"mean={starters['outs_recorded'].mean():.1f} "
        f"p25={starters['outs_recorded'].quantile(.25):.0f} "
        f"p75={starters['outs_recorded'].quantile(.75):.0f} "
        f"NaN={starters['outs_recorded'].isna().sum()}"
    )

    # --- Rolling tendencies per team ---
    N = _HOOKS_LOOKBACK
    starters["game_date"] = pd.to_datetime(starters["game_date"])
    starters = starters.sort_values(["pitcher_team", "game_date"])

    frames = []
    for team, grp in starters.groupby("pitcher_team"):
        grp = grp.sort_values("game_date").reset_index(drop=True)
        grp[f"avg_starter_outs_L{N}"] = (
            grp["outs_recorded"].rolling(N, min_periods=5).mean()
        )
        grp[f"pct_quick_hooks_L{N}"] = (
            (grp["outs_recorded"] < 15).astype(float)
            .rolling(N, min_periods=5).mean()
        )
        grp[f"pct_quality_starts_L{N}"] = (
            (grp["outs_recorded"] >= 18).astype(float)
            .rolling(N, min_periods=5).mean()
        )
        frames.append(
            grp[[
                "pitcher_team", "game_date", "game_pk",
                f"avg_starter_outs_L{N}",
                f"pct_quick_hooks_L{N}",
                f"pct_quality_starts_L{N}",
            ]]
        )

    if not frames:
        logger.warning("auxiliary_features: no manager hook data computed -- empty output")
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result = result.rename(columns={"pitcher_team": "team"})
    result["game_date"] = result["game_date"].dt.strftime("%Y-%m-%d")

    nan_avg = result[f"avg_starter_outs_L{N}"].isna().sum()
    logger.info(
        f"auxiliary_features: manager_hooks -- "
        f"{len(result):,} rows | {result['team'].nunique()} teams | "
        f"NaN in avg_starter_outs_L{N}: {nan_avg} "
        f"(expected for first {N} games per team)"
    )

    write_csv(result, _HOOKS_KEY)
    logger.info(f"auxiliary_features: manager_hooks written to {_HOOKS_KEY}")
    return result


def load_manager_hooks() -> pd.DataFrame:
    """Load manager hook tendencies master from GCS/local.

    Feature builders join on (team, game_pk) -- use the home/away team
    column matching the side whose pitcher is being scored.
    """
    from mlb_core.storage import read_csv, exists

    if not exists(_HOOKS_KEY):
        logger.warning(
            f"auxiliary_features: manager_hooks not found at {_HOOKS_KEY} -- "
            f"run compute_manager_hooks() first"
        )
        return pd.DataFrame()

    try:
        df = read_csv(_HOOKS_KEY, low_memory=False)
        logger.debug(f"auxiliary_features: loaded manager_hooks {len(df):,} rows")
        return df
    except Exception as e:
        logger.error(f"auxiliary_features: load manager_hooks failed: {e}")
        return pd.DataFrame()


# ===========================================================================
# Nightly all (called from /refresh-data Loop A)
# ===========================================================================

def auxiliary_features_nightly_gcs() -> dict:
    """Run all nightly refreshes for auxiliary feature sources.

    Called by /refresh-data (Loop A, 08:00 UTC). In-season only (Mar-Nov).
    Sources 1-3 and 5 re-fetch current season data from external APIs.
    Source 4 (manager hooks) is recomputed from statcast_master, which
    /refresh-data has already updated before calling this function.

    Returns:
        Dict {source: result} for monitoring. result.status = 'ok'|'error'|'skipped'.
    """
    month = date.today().month
    if not (3 <= month <= 11):
        logger.info(
            "auxiliary_features: nightly all -- off-season (month=%d), skipping",
            month,
        )
        return {"status": "skipped", "reason": "off_season"}

    results: dict = {}

    for source_name, fn in [
        ("fangraphs_pitching", fangraphs_nightly_gcs),
        ("swing_take",         swing_take_nightly_gcs),
        ("team_schedule",      team_schedule_nightly_gcs),
        ("catcher_poptime",    catcher_poptime_nightly_gcs),
    ]:
        try:
            results[source_name] = fn()
        except Exception as e:
            logger.error(
                f"auxiliary_features: nightly {source_name} raised: {e}"
            )
            results[source_name] = {"status": "error", "error": str(e)}
        time.sleep(random.uniform(4.0, 8.0))

    # Manager hooks: derived from statcast (already refreshed earlier in Loop A)
    try:
        mh = compute_manager_hooks(force=True)
        results["manager_hooks"] = {
            "status": "ok" if not mh.empty else "error",
            "rows": len(mh),
        }
    except Exception as e:
        logger.error(f"auxiliary_features: nightly manager_hooks raised: {e}")
        results["manager_hooks"] = {"status": "error", "error": str(e)}

    n_ok = sum(
        1 for v in results.values()
        if isinstance(v, dict) and v.get("status") == "ok"
    )
    logger.info(
        f"auxiliary_features: nightly all complete -- "
        f"{n_ok}/{len(results)} sources ok"
    )
    return results


# ===========================================================================
# __main__ CLI
# ===========================================================================

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s -- %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Backfill or refresh auxiliary feature data to GCS."
    )
    parser.add_argument(
        "source",
        choices=["fangraphs", "swing_take", "team_schedule", "manager_hooks", "all"],
        help="Data source to operate on.",
    )
    parser.add_argument("--start-year", type=int, default=None,
                        help="First season to backfill (backfill mode only).")
    parser.add_argument("--end-year",   type=int, default=None,
                        help="Last season to backfill (backfill mode only).")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch/recompute even if already cached in GCS.",
    )
    parser.add_argument(
        "--nightly", action="store_true",
        help="Run nightly mode (current season only) instead of backfill.",
    )
    args = parser.parse_args()

    if args.nightly:
        dispatch = {
            "fangraphs":      fangraphs_nightly_gcs,
            "swing_take":     swing_take_nightly_gcs,
            "team_schedule":  team_schedule_nightly_gcs,
            "manager_hooks":  lambda: {
                "status": "ok",
                "rows": len(compute_manager_hooks(force=args.force)),
            },
            "all":            auxiliary_features_nightly_gcs,
        }
        result = dispatch[args.source]()
    else:
        if args.source == "fangraphs":
            result = fangraphs_backfill_gcs(args.start_year, args.end_year, args.force)
        elif args.source == "swing_take":
            result = swing_take_backfill_gcs(args.start_year, args.end_year, args.force)
        elif args.source == "team_schedule":
            result = team_schedule_backfill_gcs(args.start_year, args.end_year, args.force)
        elif args.source == "manager_hooks":
            mh = compute_manager_hooks(force=args.force)
            result = {"status": "ok", "rows": len(mh)}
        else:
            # all: run each backfill sequentially
            result = {}
            for src, fn in [
                ("fangraphs",     fangraphs_backfill_gcs),
                ("swing_take",    swing_take_backfill_gcs),
                ("team_schedule", team_schedule_backfill_gcs),
            ]:
                result[src] = fn(args.start_year, args.end_year, args.force)
            mh = compute_manager_hooks(force=args.force)
            result["manager_hooks"] = {"status": "ok", "rows": len(mh)}

    print(json.dumps({str(k): v for k, v in result.items()}, indent=2))
