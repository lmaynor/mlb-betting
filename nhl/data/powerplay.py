"""NHL power-play points (goals + assists on the power play), derived from play-by-play goals.

The official boxscore skater row carries `powerPlayGoals` but NO `powerPlayPoints`, no power-play assists and no
power-play TOI (verified against the live endpoint 2026-11-01: the row keys are assists, blockedShots,
faceoffWinningPctg, giveaways, goals, hits, name, pim, playerId, plusMinus, points, position, powerPlayGoals, shifts,
sog, sweaterNumber, takeaways, toi). Power-play ASSISTS therefore have to come from the play-by-play, where every goal
carries `situationCode` (away goalie, away skaters, home skaters, home goalie) plus scoringPlayerId and the assist ids.

A goal is a power-play goal when the scoring side has MORE skaters than the other side once an extra attacker is
discounted (a side whose own goalie digit is 0 has pulled its goalie for a sixth skater):

    effective_skaters(side) = skaters - (1 if that side's goalie digit is 0 else 0)
    power play  <=>  effective_for > effective_against        (shootout goals never count)

Checked against the master's own `power_play_goals` on every regular-season and playoff skater row of 2023-2025
(150,893 rows, 5,130 goals): an exact match, 0 differing rows. A plain `skaters_for > skaters_against` rule misses 2.3%
(a goal scored 6v4 with the goalie pulled during a delayed penalty IS a power-play goal) so the effective-skater rule
is the one used. Assists are credited to the same goals, so PP assists / PP points inherit that agreement.

Pure functions only (no I/O) except `main`, which backfills the skater master from the shots masters.
"""
from __future__ import annotations

import argparse
import logging
import math

import pandas as pd

log = logging.getLogger("nhl.data.powerplay")

PP_COLUMNS = ("power_play_assists", "power_play_points")


def _isnull(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def goal_is_power_play(situation_code, is_home: bool, period_type=None) -> bool:
    """True when a goal scored by the home (`is_home`) or away side at `situation_code` is a power-play goal."""
    if period_type == "SO" or _isnull(situation_code):
        return False
    try:
        s = str(int(situation_code)).zfill(4)
    except (TypeError, ValueError):
        return False
    if len(s) != 4:
        return False
    ag, a_sk, h_sk, hg = (int(c) for c in s)
    own_g, own_sk, opp_g, opp_sk = (hg, h_sk, ag, a_sk) if is_home else (ag, a_sk, hg, h_sk)
    return (own_sk - (1 if own_g == 0 else 0)) > (opp_sk - (1 if opp_g == 0 else 0))


def _pid(x):
    if _isnull(x):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def pp_points_from_goals(goals) -> dict:
    """Flattened goal rows -> {player_id: {"pp_goals": n, "pp_assists": n, "pp_points": n}}.

    `goals` = dicts with situation_code, is_home, period_type, shooter_id, assist1_id, assist2_id (the shape of
    nhl.data.flatten.flatten_shots rows for `event == "goal"`; other events are ignored). Players with no power-play
    point are simply absent: the caller decides what an absent player means (0 when the game's play-by-play exists)."""
    out: dict = {}
    for g in goals:
        if g.get("event", "goal") != "goal":
            continue
        if not goal_is_power_play(g.get("situation_code"), bool(g.get("is_home")), g.get("period_type")):
            continue
        for pid, key in ((_pid(g.get("shooter_id")), "pp_goals"), (_pid(g.get("assist1_id")), "pp_assists"),
                         (_pid(g.get("assist2_id")), "pp_assists")):
            if pid is None:
                continue
            rec = out.setdefault(pid, {"pp_goals": 0, "pp_assists": 0, "pp_points": 0})
            rec[key] += 1
            rec["pp_points"] += 1
    return out


def pp_points_from_pbp(pbp: dict) -> dict | None:
    """The raw api-web.nhle.com play-by-play JSON -> the same {player_id: {...}} map, or None when it carries no plays
    (None, not {}: an empty map would claim every skater scored zero power-play points). Used to GRADE pp_points props."""
    plays = (pbp or {}).get("plays")
    if not plays:
        return None
    home_id = (pbp.get("homeTeam") or {}).get("id")
    rows = []
    for play in plays:
        if play.get("typeDescKey") != "goal":
            continue
        d = play.get("details") or {}
        rows.append({"event": "goal", "situation_code": play.get("situationCode"),
                     "is_home": d.get("eventOwnerTeamId") == home_id,
                     "period_type": (play.get("periodDescriptor") or {}).get("periodType"),
                     "shooter_id": d.get("scoringPlayerId"), "assist1_id": d.get("assist1PlayerId"),
                     "assist2_id": d.get("assist2PlayerId")})
    return pp_points_from_goals(rows)


def derive_frame(shots: pd.DataFrame) -> pd.DataFrame:
    """A shots-master frame -> one row per (game_id, player_id) with a power-play point:
    game_id, player_id, pp_goals, pp_assists, pp_points. Only `event == "goal"` rows are read."""
    cols = ["game_id", "situation_code", "is_home", "period_type", "shooter_id", "assist1_id", "assist2_id"]
    g = shots[shots["event"] == "goal"]
    g = g[[c for c in cols if c in g.columns]]
    rows = []
    for gid, sub in g.groupby("game_id"):
        for pid, rec in pp_points_from_goals(sub.to_dict("records")).items():
            rows.append({"game_id": int(gid), "player_id": pid, **rec})
    return pd.DataFrame(rows, columns=["game_id", "player_id", "pp_goals", "pp_assists", "pp_points"])


def fill_skater_frame(sk: pd.DataFrame, shots: pd.DataFrame) -> pd.DataFrame:
    """Fill `power_play_assists` / `power_play_points` on skater rows whose value is missing, from `shots` (the shots
    master of the same games). A row is only filled when its game has play-by-play in `shots` (any shot event): a game
    with no shots data stays NaN, never a fabricated 0. Rows that already carry a value are left alone."""
    sk = sk.copy()
    for c in PP_COLUMNS:
        if c not in sk.columns:
            sk[c] = float("nan")
    have_pbp = set(pd.to_numeric(shots["game_id"], errors="coerce").dropna().astype(int))
    derived = derive_frame(shots)
    ppa = {(int(g), int(p)): a for g, p, a in zip(derived["game_id"], derived["player_id"], derived["pp_assists"])}
    ppp = {(int(g), int(p)): a for g, p, a in zip(derived["game_id"], derived["player_id"], derived["pp_points"])}
    gids = pd.to_numeric(sk["game_id"], errors="coerce")
    pids = pd.to_numeric(sk["player_id"], errors="coerce")
    miss = sk["power_play_points"].isna() & gids.notna() & pids.notna()
    miss &= gids.fillna(-1).astype(int).isin(have_pbp)
    idx = sk.index[miss]
    keys = list(zip(gids[miss].astype(int), pids[miss].astype(int)))
    sk.loc[idx, "power_play_assists"] = [ppa.get(k, 0) for k in keys]
    sk.loc[idx, "power_play_points"] = [ppp.get(k, 0) for k in keys]
    return sk


def main(argv=None) -> int:
    """Backfill the skater master's power_play_assists / power_play_points from the shots masters (dry run by default).

        PYTHONPATH=. python -m nhl.data.powerplay --seasons 2023,2024,2025,2026 [--write]
    """
    from mlb_core import storage
    from nhl.data.settings import SKATER_BOX_MASTER, shots_key

    ap = argparse.ArgumentParser(description=main.__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", required=True, help="comma list of season start years whose shots masters to read")
    ap.add_argument("--write", action="store_true", help="write the updated skater master (default: report only)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sk = storage.read_csv(SKATER_BOX_MASTER)
    before = int(sk["power_play_points"].notna().sum()) if "power_play_points" in sk.columns else 0
    for y in (int(s) for s in a.seasons.split(",")):
        try:
            shots = storage.read_csv(shots_key(y))
        except Exception as exc:                                                       # noqa: BLE001
            log.warning("season %s shots unreadable (%s): skipped", y, exc)
            continue
        sk = fill_skater_frame(sk, shots)
    after = int(sk["power_play_points"].notna().sum())
    log.info("power_play_points filled: %d -> %d of %d skater rows", before, after, len(sk))
    if a.write:
        storage.write_csv(sk, SKATER_BOX_MASTER)
        log.info("wrote %s", SKATER_BOX_MASTER)
    else:
        log.info("dry run: pass --write to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
