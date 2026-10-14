"""nba.props.identity -- bridge ESPN-id'd odds quotes to SportsBlaze box-score history.

The two id spaces do not join: SportsBlaze player ids are opaque UUIDs (the history the model needs), the odds layer
resolves quotes to ESPN ids (NBA/players_xwalk.csv via nba/data/espn_players.py). The only shared key is the player's
NAME plus (loosely) team, so this builds an explicit, audited crosswalk and FAILS CLOSED:

  * resolution reuses mlb_core.data.player_names.PlayerResolver over the SportsBlaze players (exact normalised name ->
    team narrowing -> compatible first name, e.g. "Nicolas Claxton" / "Nic Claxton"); anything ambiguous is DROPPED.
  * the mapping must be one-to-one: an SB id claimed by two ESPN players (or the reverse) drops all of them.
  * only SB players seen within `recent_days` of the end of history are candidates, so a retired namesake cannot
    capture a current player's quotes -- and a candidate whose last team differs from the ESPN roster team is bridged
    only when the name is UNIQUE (offseason trades are normal) and is flagged team_changed in the audit table.
Same hazard as docs/solutions/logic-errors/cross-book-player-identity-and-partial-crosswalk.md: a wrong merge prices a
bet off another player's history, so "no bridge" beats "best guess".
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from mlb_core.data.player_names import PlayerResolver, norm_name


@dataclass(frozen=True)
class Bridge:
    table: pd.DataFrame            # audit table: one row per ESPN player (ok and dropped)
    _map: dict                     # espn_id -> sb_player_id (ok rows only)

    def sb_id(self, espn_id):
        """-> (sb_player_id | None, reason). Unknown ESPN ids and dropped rows return None with the reason."""
        try:
            key = int(espn_id)
        except (TypeError, ValueError):
            return None, "bad_espn_id"
        if key in self._map:
            return self._map[key], "ok"
        row = self.table[self.table["espn_id"] == key]
        if len(row):
            return None, str(row.iloc[0]["reason"])
        return None, "espn_id_not_in_crosswalk"

    @property
    def n_ok(self) -> int:
        return len(self._map)


def sb_player_table(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per SportsBlaze player from prepared history: latest name/team, last game date, games played."""
    r = rows.sort_values(["tip_sort", "game_id"])
    g = r.groupby("player_id", sort=False)
    out = g.agg(name=("name", "last"), team=("team", "last"), last_date=("date", "max"), n_games=("game_id", "size"),
                last_season=("season_year", "max")).reset_index()
    return out


def build_bridge(rows: pd.DataFrame, xwalk: pd.DataFrame, recent_days: int = 550) -> Bridge:
    """rows: prepared history (data.prepare); xwalk: NBA/players_xwalk.csv (espn_id, full_name, team)."""
    sbp = sb_player_table(rows)
    end = sbp["last_date"].max()
    cand = sbp[sbp["last_date"] >= end - pd.Timedelta(days=recent_days)]
    res = PlayerResolver({"player_id": r.player_id, "full_name": r.name, "team": r.team}
                         for r in cand.itertuples(index=False))
    by_sb = cand.set_index("player_id")
    recs = []
    for e in xwalk.itertuples(index=False):
        sb_id, how = res.resolve(e.full_name, e.team)
        rec = {"espn_id": int(e.espn_id), "espn_name": e.full_name, "espn_team": e.team, "sb_player_id": None,
               "sb_name": None, "sb_last_team": None, "how": how, "team_changed": None, "status": "dropped",
               "reason": how}
        if sb_id is not None:
            s = by_sb.loc[sb_id]
            changed = s["team"] != e.team
            rec.update(sb_player_id=sb_id, sb_name=s["name"], sb_last_team=s["team"], team_changed=bool(changed))
            if how == "compat" and changed:
                rec["reason"] = "compat_name_and_team_differs"          # fuzzy name AND a different team: too weak
            else:
                rec["status"], rec["reason"] = "ok", how
        recs.append(rec)
    t = pd.DataFrame(recs)
    # one-to-one: an SB id claimed by >1 ESPN player poisons every claimant
    ok = t["status"] == "ok"
    dup = t[ok].duplicated("sb_player_id", keep=False)
    bad = t[ok][dup].index
    t.loc[bad, "status"] = "dropped"
    t.loc[bad, "reason"] = "sb_id_claimed_twice"
    # ... and two ESPN players sharing a name bridge only when each one's SB match is TEAM-confirmed (same team)
    namesake = t["espn_name"].map(norm_name).duplicated(keep=False)
    unconfirmed = namesake & (t["status"] == "ok") & (t["team_changed"] != False)       # noqa: E712
    t.loc[unconfirmed, "status"] = "dropped"
    t.loc[unconfirmed, "reason"] = "espn_namesake_unconfirmed"
    m = {int(r.espn_id): r.sb_player_id for r in t[t["status"] == "ok"].itertuples(index=False)}
    return Bridge(t, m)
