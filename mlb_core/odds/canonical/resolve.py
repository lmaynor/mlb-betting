"""mlb_core.odds.canonical.resolve -- Master Identity Resolution (BNK-12).

Spec 2.2:
MasterIndex built from a league's outcome-master frames:
  - players: master_player_id, name, team, game_date_et (or roster)
  - games: event_id, game_date_et, away_team, home_team, start_time_utc

Resolution algorithm (in order; first success wins):
  1. Verified alias: (source, source_player_id) or (source, norm_name) -> master ID
  2. Name + event team pair + date:
     Candidates = master players matching normalized name AND belonging to
     one of event's two teams on that date.
     Exactly one -> resolved.
     >=2 -> ambiguous.
     0 -> if name exists on other teams -> team_mismatch; else proceed to step 3.
  3. Boxscore / roster fallback:
     Check the event's own boxscore for matching normalized name.
     Exactly one -> resolved.
     >=2 -> ambiguous.
     0 -> unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd

from mlb_core import storage
from mlb_core.odds.canonical.normalize import NameCollisionTracker, normalize_name
from mlb_core.odds.canonical.registry import load_player_aliases, normalize_team


@dataclass(frozen=True)
class GameInfo:
    event_id: str
    game_date_et: str
    away_team: str
    home_team: str
    start_time_utc: str


@dataclass(frozen=True)
class PlayerInfo:
    master_player_id: str
    name: str
    norm_name: str
    team: str
    game_date_et: Optional[str] = None
    event_id: Optional[str] = None


class MasterIndex:
    """In-memory index over an outcome master for player and event identity resolution."""

    def __init__(self, league: str):
        self.league = league.lower()
        self.collision_tracker = NameCollisionTracker()

        # Games indexing
        self.games_by_id: Dict[str, GameInfo] = {}
        # (game_date_et, frozenset({away_team, home_team})) -> list of GameInfo
        self.games_by_date_and_teams: Dict[Tuple[str, frozenset], List[GameInfo]] = {}

        # Players indexing
        # (game_date_et, team, norm_name) -> list of PlayerInfo
        self.players_by_date_team_name: Dict[Tuple[str, str, str], List[PlayerInfo]] = {}
        # (event_id, norm_name) -> list of PlayerInfo (boxscore)
        self.players_by_event_boxscore: Dict[Tuple[str, str], List[PlayerInfo]] = {}
        # norm_name -> list of PlayerInfo (all seen in league)
        self.all_players_by_name: Dict[str, List[PlayerInfo]] = {}
        # master_player_id -> list of PlayerInfo
        self.players_by_master_id: Dict[str, List[PlayerInfo]] = {}

        # Verified aliases: (source, source_player_id) -> master_player_id
        self.aliases_by_source_id: Dict[Tuple[str, str], str] = {}
        # (source, norm_name) -> master_player_id
        self.aliases_by_name: Dict[Tuple[str, str], str] = {}

    def add_game(self, event_id: str, game_date_et: str, away_team: str, home_team: str, start_time_utc: str = "") -> GameInfo:
        away = normalize_team(self.league, away_team)
        home = normalize_team(self.league, home_team)
        g = GameInfo(
            event_id=str(event_id).strip(),
            game_date_et=str(game_date_et).strip(),
            away_team=away,
            home_team=home,
            start_time_utc=str(start_time_utc).strip(),
        )
        self.games_by_id[g.event_id] = g
        pair_key = (g.game_date_et, frozenset({away, home}))
        self.games_by_date_and_teams.setdefault(pair_key, []).append(g)
        return g

    def add_player(
        self,
        master_player_id: str,
        name: str,
        team: str,
        game_date_et: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> PlayerInfo:
        mid = str(master_player_id).strip()
        norm = self.collision_tracker.register(name, mid)
        c_team = normalize_team(self.league, team)
        p = PlayerInfo(
            master_player_id=mid,
            name=name.strip(),
            norm_name=norm,
            team=c_team,
            game_date_et=str(game_date_et).strip() if game_date_et else None,
            event_id=str(event_id).strip() if event_id else None,
        )
        if p.game_date_et:
            dt_key = (p.game_date_et, c_team, norm)
            self.players_by_date_team_name.setdefault(dt_key, []).append(p)
        if p.event_id:
            ev_key = (p.event_id, norm)
            self.players_by_event_boxscore.setdefault(ev_key, []).append(p)

        self.all_players_by_name.setdefault(norm, []).append(p)
        self.players_by_master_id.setdefault(mid, []).append(p)
        return p

    def add_alias(self, source: str, source_player_id: Optional[str], source_name_norm: Optional[str], master_player_id: str) -> None:
        src = source.strip().lower()
        mid = str(master_player_id).strip()
        if source_player_id:
            spid = str(source_player_id).strip()
            self.aliases_by_source_id[(src, spid)] = mid
        if source_name_norm:
            norm = normalize_name(source_name_norm)
            self.aliases_by_name[(src, norm)] = mid

    def resolve_event(self, game_date_et: str, away_team: str, home_team: str) -> Tuple[Optional[str], str]:
        """Resolve event identity from date and teams.

        Returns (event_id, status):
          - (event_id, 'ok')
          - (None, 'not_found')
          - (None, 'ambiguous_event') on doubleheaders without game distinction
        """
        away = normalize_team(self.league, away_team)
        home = normalize_team(self.league, home_team)
        pair_key = (str(game_date_et).strip(), frozenset({away, home}))
        games = self.games_by_date_and_teams.get(pair_key, [])
        if len(games) == 1:
            return games[0].event_id, "ok"
        if len(games) > 1:
            return None, "ambiguous_event"
        return None, "not_found"

    def resolve_player(
        self,
        source: str,
        source_player_id: Optional[str],
        player_name: str,
        event_id: str,
        game_date_et: str,
        away_team: str,
        home_team: str,
        source_player_team: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], str]:
        """Execute the 3-step resolution algorithm per spec 2.2.

        Returns:
          (master_player_id, player_team, resolution_step_or_quarantine_reason)
        """
        src = (source or "").strip().lower()
        norm_name = normalize_name(player_name)
        away = normalize_team(self.league, away_team)
        home = normalize_team(self.league, home_team)
        event_teams = {away, home}

        # -------------------------------------------------------------------
        # Step 1: Verified Alias
        # -------------------------------------------------------------------
        master_id: Optional[str] = None
        if source_player_id:
            spid = str(source_player_id).strip()
            master_id = self.aliases_by_source_id.get((src, spid))

        if not master_id and norm_name:
            master_id = self.aliases_by_name.get((src, norm_name))

        if master_id:
            # Determine player's team for this event
            player_team = None
            if source_player_team:
                c_st = normalize_team(self.league, source_player_team)
                if c_st in event_teams:
                    player_team = c_st
            if not player_team:
                # Check master player's team records
                for p in self.players_by_master_id.get(master_id, []):
                    if p.team in event_teams:
                        player_team = p.team
                        break
            return master_id, player_team, "step1_alias"

        if not norm_name:
            return None, None, "unresolved"

        # Check collision tracker
        if self.collision_tracker.is_collision(norm_name):
            return None, None, "ambiguous"

        # -------------------------------------------------------------------
        # Step 2: Name + event team pair + date
        # -------------------------------------------------------------------
        # Candidates on event teams on game_date_et
        cands: List[PlayerInfo] = []
        for team in event_teams:
            dt_key = (str(game_date_et).strip(), team, norm_name)
            cands.extend(self.players_by_date_team_name.get(dt_key, []))

        # Deduplicate candidates by master_player_id
        unique_mids = {c.master_player_id for c in cands}
        if len(unique_mids) == 1:
            cand = next(c for c in cands if c.master_player_id == next(iter(unique_mids)))
            return cand.master_player_id, cand.team, "step2_name_event_teams"
        elif len(unique_mids) > 1:
            return None, None, "ambiguous"

        # 0 candidates on event teams on game_date_et:
        # Check if the player exists in the league on OTHER teams (RES-09 team_mismatch)
        league_cands = self.all_players_by_name.get(norm_name, [])
        other_team_cands = [p for p in league_cands if p.team not in event_teams]
        if other_team_cands and not cands:
            # Player exists in league, but on neither event team
            return None, None, "team_mismatch"

        # -------------------------------------------------------------------
        # Step 3: Boxscore / roster fallback
        # -------------------------------------------------------------------
        box_cands = self.players_by_event_boxscore.get((str(event_id).strip(), norm_name), [])
        unique_box_mids = {c.master_player_id for c in box_cands}
        if len(unique_box_mids) == 1:
            cand = next(c for c in box_cands if c.master_player_id == next(iter(unique_box_mids)))
            return cand.master_player_id, cand.team, "step3_boxscore"
        elif len(unique_box_mids) > 1:
            return None, None, "ambiguous"

        return None, None, "unresolved"


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def build_nba_master_index(
    games_master_path: Optional[str] = None,
    player_box_path: Optional[str] = None,
    registry_dir: Optional[str] = None,
) -> MasterIndex:
    """Build MasterIndex for NBA from bucket masters via nba.config paths.

    Date derivation convention:
    SportsBlaze date values in games_master / player_boxscores_master are
    UTC-tip-dated timestamps (e.g. '2025-12-17T00:30:00Z').
    We convert the UTC timestamp to America/New_York date string (game_date_et).
    If only 'YYYY-MM-DD' is present without timestamp, it is used directly.
    """
    from nba.config import GAMES_MASTER, PLAYER_BOX_MASTER

    idx = MasterIndex("nba")

    # Load aliases from registry
    alias_df = load_player_aliases("nba", registry_dir=registry_dir)
    for _, row in alias_df.iterrows():
        if pd.notna(row.get("master_player_id")):
            idx.add_alias(
                source=str(row.get("source", "")),
                source_player_id=str(row["source_player_id"]) if pd.notna(row.get("source_player_id")) else None,
                source_name_norm=str(row["source_name_norm"]) if pd.notna(row.get("source_name_norm")) else None,
                master_player_id=str(row["master_player_id"]),
            )

    # Load games master
    g_key = games_master_path or GAMES_MASTER
    if storage.exists(g_key):
        gdf = storage.read_csv(g_key)
        for _, r in gdf.iterrows():
            gid = str(r.get("game_id", ""))
            raw_date = str(r.get("date", ""))
            # UTC to ET date derivation
            if "T" in raw_date or ":" in raw_date:
                try:
                    dt_utc = pd.to_datetime(raw_date, utc=True)
                    date_et = dt_utc.tz_convert("America/New_York").strftime("%Y-%m-%d")
                    start_utc = dt_utc.isoformat()
                except Exception:
                    date_et = raw_date[:10]
                    start_utc = raw_date
            else:
                date_et = raw_date[:10]
                start_utc = raw_date

            idx.add_game(
                event_id=gid,
                game_date_et=date_et,
                away_team=str(r.get("away_abbr") or r.get("away_name", "")),
                home_team=str(r.get("home_abbr") or r.get("home_name", "")),
                start_time_utc=start_utc,
            )

    # Load player boxscores master
    p_key = player_box_path or PLAYER_BOX_MASTER
    if storage.exists(p_key):
        pdf = storage.read_csv(p_key)
        for _, r in pdf.iterrows():
            pid = str(r.get("player_id", ""))
            name = str(r.get("name", ""))
            team = str(r.get("team_abbr", ""))
            gid = str(r.get("game_id", ""))
            raw_date = str(r.get("date", ""))
            if "T" in raw_date or ":" in raw_date:
                try:
                    date_et = pd.to_datetime(raw_date, utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d")
                except Exception:
                    date_et = raw_date[:10]
            else:
                date_et = raw_date[:10]

            idx.add_player(
                master_player_id=pid,
                name=name,
                team=team,
                game_date_et=date_et,
                event_id=gid,
            )

    return idx


def build_generic_master_index(
    league: str,
    games_df: Optional[pd.DataFrame] = None,
    players_df: Optional[pd.DataFrame] = None,
    aliases_df: Optional[pd.DataFrame] = None,
    registry_dir: Optional[str] = None,
) -> MasterIndex:
    """Build MasterIndex for any league from injected DataFrames."""
    idx = MasterIndex(league)

    # Aliases
    adf = aliases_df if aliases_df is not None else load_player_aliases(league, registry_dir=registry_dir)
    if adf is not None and not adf.empty:
        for _, row in adf.iterrows():
            if pd.notna(row.get("master_player_id")):
                idx.add_alias(
                    source=str(row.get("source", "")),
                    source_player_id=str(row["source_player_id"]) if pd.notna(row.get("source_player_id")) else None,
                    source_name_norm=str(row["source_name_norm"]) if pd.notna(row.get("source_name_norm")) else None,
                    master_player_id=str(row["master_player_id"]),
                )

    # Games
    if games_df is not None and not games_df.empty:
        for _, r in games_df.iterrows():
            idx.add_game(
                event_id=str(r.get("event_id") or r.get("game_id", "")),
                game_date_et=str(r.get("game_date_et") or r.get("date", ""))[:10],
                away_team=str(r.get("away_team") or r.get("away_abbr") or r.get("away", "")),
                home_team=str(r.get("home_team") or r.get("home_abbr") or r.get("home", "")),
                start_time_utc=str(r.get("start_time_utc") or r.get("start_time") or r.get("scheduled", "")),
            )

    # Players
    if players_df is not None and not players_df.empty:
        for _, r in players_df.iterrows():
            idx.add_player(
                master_player_id=str(r.get("master_player_id") or r.get("player_id", "")),
                name=str(r.get("name") or r.get("player_name") or r.get("full_name", "")),
                team=str(r.get("team") or r.get("team_abbr", "")),
                game_date_et=str(r.get("game_date_et") or r.get("date", ""))[:10] if pd.notna(r.get("game_date_et") or r.get("date")) else None,
                event_id=str(r.get("event_id") or r.get("game_id", "")) if pd.notna(r.get("event_id") or r.get("game_id")) else None,
            )

    return idx
