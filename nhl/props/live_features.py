"""Pre-game feature rows for UNPLAYED NHL games, computed by exactly the code that computed the training features.

The trained prop models (nhl.props.xgfeat feature sets, nhl.props.countmodel boosters) were fit on frames with one row per
played appearance. A served model needs the same columns for games that have not happened. Rather than re-implement the rolling
features (and let train and serve drift apart), this module appends one PLACEHOLDER row per entity per upcoming game to the
builders' inputs -- game id >= xgfeat.PLACEHOLDER_BASE, no outcome, no xG, no TOI -- and runs the unchanged builders. Every
rolling feature is shift(1) first, so a placeholder's features are a function of the games before it and never of itself.
Parity with the historical frame is pinned by tests/test_nhl_live_features.py and verified on the real masters by
scripts/nhl_serving_parity.py.

What a placeholder cannot know (and the trained models were therefore evaluated without; see docs/analysis/
nhl_trained_serving_2026-11-01.md): the opposing STARTER (the expected one is used: most starts in the last 10, backup on a
back-to-back), lineups, scratches and injuries.

Known approximation: when a team has two upcoming games in the window, the second placeholder's rolling window contains the
first (outcome-less) placeholder, so its windows hold one fewer real game.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nhl.props import xgfeat as X

LAST_N_TEAM_GAMES = 10          # a skater / goalie is "current" if he appeared in the team's last 10 games


def team_id_map(games: pd.DataFrame) -> dict:
    out = {}
    for ab, tid in zip(games["home_abbr"], games["home_id"]):
        out[ab] = int(tid)
    for ab, tid in zip(games["away_abbr"], games["away_id"]):
        out[ab] = int(tid)
    return out


def current_entities(masters: dict, teams: set, as_of: str | None = None, exclude: set | frozenset = frozenset()) -> dict:
    """{team: {"skaters": [(player_id, name, position)], "goalies": [(player_id, name)]}} for each team in `teams`: skaters who
    dressed for the team in >= 3 of its last 10 games (and whose latest team it is), the two goalies with the most starts in
    those games. Only games strictly before `as_of` are read. `exclude` = player ids to drop (e.g. off the active roster)."""
    sk, gl = masters["skaters"], masters["goalies"]
    sk = sk[sk["game_type"].isin(X.GAME_TYPES) & (sk["toi_sec"] > 0)]
    gl = gl[gl["game_type"].isin(X.GAME_TYPES) & (gl["toi_sec"] > 0)]
    if as_of:
        sk, gl = sk[sk["date"].astype(str) < as_of], gl[gl["date"].astype(str) < as_of]
    out = {}
    for team in teams:
        tg = sk[sk["team_abbr"] == team].drop_duplicates("game_id")[["game_id", "date"]].sort_values(["date", "game_id"])
        last = list(tg["game_id"].tail(LAST_N_TEAM_GAMES))
        if not last:
            continue
        rows = sk[(sk["team_abbr"] == team) & sk["game_id"].isin(last)]
        cnt = rows.groupby("player_id").size()
        keep = [int(p) for p in cnt[cnt >= 3].index if int(p) not in exclude]
        latest = sk[sk["player_id"].isin(keep)].sort_values(["date", "game_id"]).drop_duplicates("player_id", keep="last")
        latest = latest[latest["team_abbr"] == team]
        skaters = [(int(r.player_id), str(r.name), r.position) for r in latest.itertuples(index=False)]
        gstarts = gl[(gl["team_abbr"] == team) & gl["game_id"].isin(last) &
                     (gl["starter"].astype(str).str.lower() == "true")]
        top = gstarts.groupby("player_id").size().sort_values(ascending=False).index[:2]
        names = gl.drop_duplicates("player_id", keep="last").set_index("player_id")["name"]
        goalies = [(int(p), str(names.get(p, p))) for p in top if int(p) not in exclude]
        out[team] = {"skaters": skaters, "goalies": goalies}
    return out


def build_live(masters: dict, xg: dict, upcoming: list, entities: dict | None = None,
               exclude: set | frozenset = frozenset()) -> dict:
    """masters = nhl.xg.data.load_masters; xg = {"skater": xg_skater_game, "goalie": xg_goalie_game, "team": xg_team_game};
    upcoming = [{"date": "YYYY-MM-DD", "home": abbr, "away": abbr}, ...] (unplayed games, any order);
    entities = current_entities(...) override (default: derived from the masters as of each game's date).
    -> {"saves": features of the expected starter and backup of each team, "skater": features of every current skater,
        "games": the placeholder game table}, each row tagged with game_id (>= PLACEHOLDER_BASE), date, team, opp."""
    if not upcoming:
        return {"saves": pd.DataFrame(), "skater": pd.DataFrame(), "games": pd.DataFrame()}
    games = masters["games"]
    ids = team_id_map(games)
    teams = {t for u in upcoming for t in (u["home"], u["away"])}
    first = min(u["date"] for u in upcoming)
    ents = entities if entities is not None else current_entities(masters, teams, first, exclude)
    ph_games, sk_rows, gl_rows = [], [], []
    for i, u in enumerate(sorted(upcoming, key=lambda u: (u["date"], u["home"]))):
        gid = X.PLACEHOLDER_BASE + i
        ph_games.append({"game_id": gid, "season_year": int(pd.Timestamp(u["date"]).year - (pd.Timestamp(u["date"]).month < 8)),
                         "game_type": 2, "date": u["date"], "home_id": ids[u["home"]], "away_id": ids[u["away"]],
                         "home_abbr": u["home"], "away_abbr": u["away"]})
        for team, is_home in ((u["home"], 1), (u["away"], 0)):
            e = ents.get(team, {"skaters": [], "goalies": []})
            for pid, name, pos in e["skaters"]:
                sk_rows.append({"game_id": gid, "game_type": 2, "date": u["date"], "team_abbr": team, "team_id": ids[team],
                                "is_home": bool(is_home), "player_id": pid, "name": name, "position": pos, "toi_sec": np.nan, "played": True})
            for pid, name in e["goalies"]:
                gl_rows.append({"game_id": gid, "game_type": 2, "date": u["date"], "team_abbr": team,
                                "team_id": ids[team], "is_home": bool(is_home), "player_id": pid, "name": name,
                                "toi_sec": np.nan,
                                "starter": True, "played": True})
    if not ph_games:
        return {"saves": pd.DataFrame(), "skater": pd.DataFrame(), "games": pd.DataFrame()}
    pg = pd.DataFrame(ph_games)
    games_all = pd.concat([games, pg], ignore_index=True)
    sk_all = pd.concat([masters["skaters"], pd.DataFrame(sk_rows)], ignore_index=True)
    gl_all = pd.concat([masters["goalies"], pd.DataFrame(gl_rows)], ignore_index=True)
    tt = X.team_table(games_all, xg["team"])
    gt = X.goalie_table(games_all, gl_all, xg["goalie"])
    st = X.skater_table(games_all, sk_all, xg["skater"])
    saves = X.saves_features(gt, tt)
    skater = X.skater_features(st, tt, gt, "expected")
    ph = lambda f: f[f["game_id"] >= X.PLACEHOLDER_BASE].reset_index(drop=True)          # noqa: E731
    return {"saves": ph(saves), "skater": ph(skater), "games": pg}
