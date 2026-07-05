"""
Shared MLB lineup pull for all systems.
"""
import os
import time
import random
import requests
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from mlb_core.odds.dk_scraper import TEAM_NAME_TO_ABBREV

_session = requests.Session()

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

_LINEUP_COLS = [
    "game_pk", "team_side", "batting_order", "player_id", "player_name",
    "bats", "game_date", "away_team", "home_team", "game_time_utc",
]


def _get_games_for_date(date_str: str) -> list:
    try:
        r = _session.get(MLB_SCHEDULE_URL.format(date=date_str), timeout=30)
        r.raise_for_status()
    except Exception:
        return []
    games = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            games.append({
                "game_pk":        g["gamePk"],
                "game_date":      date_str,
                "game_time_utc":  g.get("gameDate", ""),
                "away_team_name": g["teams"]["away"]["team"]["name"],
                "home_team_name": g["teams"]["home"]["team"]["name"],
            })
    return games


def get_today_schedule(date_str: str) -> pd.DataFrame:
    """
    Public helper for the runners. Returns one row per game with home/away
    team abbrevs, game start time, and probable pitcher info (id+name+throws)
    if posted by MLB. Columns:
        game_pk, game_date, game_time_utc, home_team, away_team,
        home_pitcher_id, home_pitcher_name, home_pitcher_throws,
        away_pitcher_id, away_pitcher_name, away_pitcher_throws

    Probable-pitcher fields are NaN if the team hasn't announced one yet.
    """
    try:
        r = _session.get(MLB_SCHEDULE_URL.format(date=date_str), timeout=30)
        r.raise_for_status()
    except Exception:
        return pd.DataFrame()

    rows = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            row = {
                "game_pk":       g["gamePk"],
                "game_date":     date_str,
                "game_time_utc": g.get("gameDate", ""),
            }
            for side in ("home", "away"):
                tm = g["teams"][side]
                team_name = tm.get("team", {}).get("name", "")
                row[f"{side}_team"] = TEAM_NAME_TO_ABBREV.get(team_name, team_name)
                pp = tm.get("probablePitcher") or {}
                row[f"{side}_pitcher_id"]     = pp.get("id")
                row[f"{side}_pitcher_name"]   = pp.get("fullName")
                row[f"{side}_pitcher_throws"] = (pp.get("pitchHand") or {}).get("code")
            rows.append(row)
    return pd.DataFrame(rows)


def _get_lineup_for_game(game_pk: int) -> pd.DataFrame:
    try:
        r = _session.get(MLB_BOXSCORE_URL.format(game_pk=game_pk), timeout=30)
        r.raise_for_status()
        data_json = r.json()
    except Exception:
        return pd.DataFrame()
    rows = []
    for side in ["away", "home"]:
        team_data = data_json.get("teams", {}).get(side, {})
        batters = team_data.get("battingOrder", [])
        all_players = team_data.get("players", {})
        if not batters:
            continue
        for order_idx, player_id in enumerate(batters):
            pos = order_idx + 1
            if pos > 9:
                break
            info = all_players.get(f"ID{player_id}", {})
            rows.append({
                "game_pk":       game_pk,
                "team_side":     side,
                "batting_order": pos,
                "player_id":     player_id,
                "player_name":   info.get("person", {}).get("fullName", "Unknown"),
                "bats":          info.get("batSide", {}).get("code", ""),
                "position":      info.get("position", {}).get("abbreviation", ""),
            })
    return pd.DataFrame(rows)


def confirmed_lineup_ids(game_pk: int) -> set:
    """Set of MLBAM player ids in today's POSTED batting order for a game.
    Empty set if the lineup has not been posted yet (or on error)."""
    df = _get_lineup_for_game(game_pk)
    if df.empty or "player_id" not in df.columns:
        return set()
    return {int(x) for x in df["player_id"].dropna().tolist()}


def confirmed_lineup_sides(game_pk: int) -> dict:
    """Posted batting-order ids PER SIDE: {"away": set, "home": set}.

    Lineups post one team at a time; a game-level set cannot distinguish
    "his team's lineup is up and he's not in it" (really out) from "his
    team's lineup just isn't posted yet" (unknown). Callers should only mark
    a batter out when the batter is absent from a game where BOTH sides have
    posted (or, if the caller knows the team, that side has posted)."""
    df = _get_lineup_for_game(game_pk)
    out = {"away": set(), "home": set()}
    if df.empty or "player_id" not in df.columns:
        return out
    for side in ("away", "home"):
        sub = df[df["team_side"] == side]
        out[side] = {int(x) for x in sub["player_id"].dropna().tolist()}
    return out


def _pull_lineup_date(date_str: str, verbose: bool = False) -> pd.DataFrame:
    games = _get_games_for_date(date_str)
    if not games:
        return pd.DataFrame()
    frames = []
    for game in games:
        ldf = _get_lineup_for_game(game["game_pk"])
        if ldf.empty:
            continue
        ldf["game_date"] = date_str
        ldf["game_time_utc"] = game["game_time_utc"]
        ldf["away_team"] = TEAM_NAME_TO_ABBREV.get(game["away_team_name"], game["away_team_name"])
        ldf["home_team"] = TEAM_NAME_TO_ABBREV.get(game["home_team_name"], game["home_team_name"])
        frames.append(ldf)
        time.sleep(random.uniform(0.1, 0.3))
    if not frames:
        return pd.DataFrame()
    day_df = pd.concat(frames, ignore_index=True)
    if verbose:
        print(f"  {date_str}: {len(games)} games | {day_df['game_pk'].nunique()} with lineups")
    return day_df


def lineup_historical(cache_dir: Path, master_path: Path,
                      season_start: int = 2019,
                      season_start_month: int = 3,
                      season_end_month: int = 11):
    from mlb_core.data.statcast import _build_date_list
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        end_date=date.today() - timedelta(days=1),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    cached = {f.replace(".csv", "") for f in os.listdir(cache_dir) if f.endswith(".csv")}
    to_pull = [d for d in all_dates if d.strftime("%Y-%m-%d") not in cached]
    print(f"Lineup historical: {len(to_pull)} days to fetch")
    api_errors = 0
    it = tqdm(to_pull, desc="Lineups", unit="day") if HAS_TQDM else to_pull
    for d in it:
        date_str = d.strftime("%Y-%m-%d")
        cache_file = cache_dir / f"{date_str}.csv"
        try:
            day_df = _pull_lineup_date(date_str)
            api_errors = 0
        except Exception as e:
            pd.DataFrame(columns=_LINEUP_COLS).to_csv(cache_file, index=False)
            api_errors += 1
            if api_errors >= 10:
                print(f"10 consecutive API errors - stopping. Last: {e}")
                break
            time.sleep(2)
            continue
        if day_df.empty:
            pd.DataFrame(columns=_LINEUP_COLS).to_csv(cache_file, index=False)
            continue
        day_df.to_csv(cache_file, index=False)
        time.sleep(random.uniform(0.3, 0.7))
    lineup_rebuild_master(cache_dir, master_path,
                          season_start=season_start,
                          season_start_month=season_start_month,
                          season_end_month=season_end_month)


def lineup_nightly(cache_dir: Path, master_path: Path, **kwargs):
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_file = Path(cache_dir) / f"{yesterday}.csv"
    print(f"Lineup nightly: {yesterday}")
    day_df = _pull_lineup_date(yesterday, verbose=True)
    if day_df.empty:
        print("  No lineup data for yesterday")
        return
    day_df.to_csv(cache_file, index=False)
    lineup_rebuild_master(cache_dir, master_path, **kwargs)


def lineup_live() -> pd.DataFrame:
    """Pull today's lineups. Not cached."""
    today_str = date.today().strftime("%Y-%m-%d")
    print(f"Live lineups: {today_str}")
    return _pull_lineup_date(today_str, verbose=True)


def lineup_rebuild_master(cache_dir: Path, master_path: Path,
                           season_start: int = 2019,
                           season_start_month: int = 3,
                           season_end_month: int = 11):
    from mlb_core.data.statcast import _build_date_list
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    frames, skipped = [], 0
    for d in all_dates:
        f = Path(cache_dir) / f"{d.strftime('%Y-%m-%d')}.csv"
        if not f.exists():
            continue
        if f.stat().st_size < 10:
            skipped += 1
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            skipped += 1
            continue
        if df.empty or "game_pk" not in df.columns:
            skipped += 1
            continue
        frames.append(df)
    if not frames:
        print("  No lineup data found in cache")
        return
    master = pd.concat(frames, ignore_index=True)
    master = master.drop_duplicates(subset=["game_pk", "team_side", "batting_order"])
    master.to_csv(master_path, index=False)
    n_games = master["game_pk"].nunique()
    print(f"  {len(master):,} lineup rows | {n_games:,} games | {skipped} empty days skipped")

# MLB Stats API team IDs for all 30 franchises
_MLB_TEAM_IDS = [
    108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
    118, 119, 120, 121, 133, 134, 135, 136, 137, 138,
    139, 140, 141, 142, 143, 144, 145, 146, 147, 158,
]

# rosterType=injured is unreliable -- MLB's API ignores it and returns the
# active roster (every entry status.code="A"). The 40-man roster is the only
# variant that carries real IL status codes (D7/D10/D15/D60 -> "Injured N-Day").
_ROSTER_40MAN_URL = "https://statsapi.mlb.com/api/v1/teams/{tid}/roster?rosterType=40Man"


def _is_injured(entry: dict) -> bool:
    """True if a 40-man roster entry is on the IL. Matches on the status
    description ('Injured 10-Day' etc.) or a D-prefixed code (D7/D10/D15/D60).
    Excludes RM/PL/DES/RA (reassigned, paternity, designated, restricted).
    Missing/unparseable status -> False (never a false 'out')."""
    st = entry.get("status", {}) or {}
    desc = (st.get("description") or "").lower()
    code = (st.get("code") or "")
    return ("injured" in desc) or code[:1] == "D"


def _fetch_40man_il(want_pitchers_only: bool, log_label: str) -> set:
    import logging
    logger = logging.getLogger(__name__)
    il_ids = set()
    for tid in _MLB_TEAM_IDS:
        try:
            r = _session.get(_ROSTER_40MAN_URL.format(tid=tid), timeout=10)
            r.raise_for_status()
            for entry in r.json().get("roster", []):
                pid = entry.get("person", {}).get("id")
                if not pid or not _is_injured(entry):
                    continue
                if want_pitchers_only and entry.get("position", {}).get("type", "") != "Pitcher":
                    continue
                il_ids.add(int(pid))
        except Exception as exc:
            logger.warning("%s: team %d failed: %s", log_label, tid, exc)
    logger.info("%s: %d players currently on IL", log_label, len(il_ids))
    return il_ids


def fetch_il_pitcher_ids() -> set:
    """
    Return the set of pitcher MLBAM IDs currently on the IL (any variant:
    7/10/15/60-day) across all 30 MLB teams, from the 40-man roster.
    Returns empty set on any network error so callers degrade gracefully.
    """
    return _fetch_40man_il(want_pitchers_only=True, log_label="fetch_il_pitcher_ids")


def fetch_il_ids() -> set:
    """Return the set of ALL player MLBAM IDs currently on the IL (any position),
    across all 30 teams. For surfacing 'Out / IL' status on The Edge cockpit.
    Returns empty set on any network error so callers degrade gracefully."""
    return _fetch_40man_il(want_pitchers_only=False, log_label="fetch_il_ids")

