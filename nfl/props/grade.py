"""nfl.props.grade -- grade NFL player-prop quotes against nflverse ``player_stats`` (the second, independent grader).

The sweep (ev/sweep_nfl_props.py) logs book / Kalshi / Novig quotes; Kalshi's own settlement is the first grader.
This module re-derives the outcome from nflverse weekly stat lines so the two can be compared (design doc
docs/designs/multisport_ev_sweep.md s7.4, open item "second grader source for NFL"). Nothing here moves a stake.

STAT FORMULAS (nflverse ``player_stats`` columns; every stat is the player's FULL-GAME total)
    pass_yds    = passing_yards
    rush_yds    = rushing_yards
    rec_yds     = receiving_yards
    receptions  = receptions
    pass_tds    = passing_tds
    anytime_td  = rushing_tds + receiving_tds                       (td_scope="offense", the default)
                  [td_scope="broad" adds special_teams_tds + def_tds + fumble_recovery_tds]
  Passing TDs do NOT count toward anytime_td. 2-point conversions are not touchdowns (nflverse keeps them in
  *_2pt_conversions, which are not summed). UNVERIFIED: the repo contains NO sportsbook / Kalshi rule text for
  whether return, defensive or fumble-recovery TDs count toward "anytime touchdown scorer" (CONTEXT.md s10 covers MLB
  DraftKings only; the design doc does not read the NFL TD rule). The default follows the common US-book wording
  (rushing/receiving scores only) but that is an ASSUMPTION; grade_frame therefore also grades anytime_td under the
  "broad" scope and flags rows where the two disagree (``td_scope_sensitive``) so the effect is measurable.
  Also UNVERIFIED: nflverse passing_yards is the official gross figure (sack yards are separate), which is what books
  settle on; rushing_yards includes kneel-downs, as the NFL scores them.

SEMANTICS (must match ev/props.py)
    * side "over" wins iff actual > point, "under" iff actual < point. A half-point line can never push.
    * An integer line pushes on exact equality.
    * A one-sided "Yes" (bovada pass TDs, anytime TD, a Kalshi "N+" ladder strike) == "over" at that point, and the
      complementary "No" == "under". N+ == over N-0.5: ladder_point(3) == 2.5 ("3+ receptions" wins iff receptions >= 3).
    * anytime_td's natural point is 0.5 (Yes == at least one TD); point=None is accepted ONLY for anytime_td.

STATES (strings)
    win | loss | push | void | not_yet_graded            from grade()
    ungradable                                             extra, from grade_detail()/grade_frame(): the inputs cannot
                                                           be graded safely (stat value missing, player not resolved,
                                                           game not identified, ...). NEVER treated as a loss.
    - game_final=False                      -> not_yet_graded. The CALLER decides finality; grade_frame sets it True only
                                               when the game has rows in the mirrored player_stats, so "the mirror has not
                                               caught up yet" is not_yet_graded and never void.
    - game final, no stat row for the player -> void (DNP: books and Kalshi void/refund a player who never plays --
                                               docs/designs/multisport_ev_sweep.md s3 "never takes a snap -> fair price",
                                               s6). BUT nflverse only writes a row when the player recorded SOME stat, so a
                                               player who PLAYED and recorded nothing (a WR on the field with zero targets)
                                               has no row too, and for him an Over is a LOSS, not a void. Voiding that case
                                               silently drops losing overs and inflates ROI. So pass ``played=`` (from
                                               snap_counts): played=True & no row -> graded against zeros;
                                               played=False/None & no row -> void. grade_frame supplies it from snap_counts.
    Which snap type counts as "played" (offense+defense+special teams > 0) and whether a book requires a snap at all are
    UNVERIFIED book/Kalshi-rule assumptions (Kalshi's text, per the design doc: "active but never takes a snap").
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

import pandas as pd

from ev.props import norm_name, split_player  # noqa: F401  (re-exported for callers building frames)
from nfl.data.keys import add_game_keys, ev_code

WIN, LOSS, PUSH, VOID, NOT_YET_GRADED = "win", "loss", "push", "void", "not_yet_graded"
UNGRADABLE = "ungradable"
RESULTS = (WIN, LOSS, PUSH, VOID, NOT_YET_GRADED)

STATS = ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td")

# stat -> player_stats columns SUMMED to get the actual value
STAT_COLUMNS = {
    "pass_yds": ("passing_yards",),
    "rush_yds": ("rushing_yards",),
    "rec_yds": ("receiving_yards",),
    "receptions": ("receptions",),
    "pass_tds": ("passing_tds",),
    "anytime_td": ("rushing_tds", "receiving_tds"),
}
# UNVERIFIED broader scope for anytime_td (return / defensive / fumble-recovery scores)
ANYTIME_TD_BROAD_EXTRA = ("special_teams_tds", "def_tds", "fumble_recovery_tds")
TD_SCOPES = ("offense", "broad")
STAT_ROW_COLUMNS = tuple(sorted({c for cols in STAT_COLUMNS.values() for c in cols} | set(ANYTIME_TD_BROAD_EXTRA)))

_OVER = {"over", "o", "yes", "y"}
_UNDER = {"under", "u", "no", "n"}
_EPS = 1e-9


def canon_side(side) -> str:
    """'Over'/'OVER'/'yes' -> 'over'; 'Under'/'no' -> 'under'. Anything else raises (never guess a side)."""
    s = str(side).strip().lower()
    if s in _OVER:
        return "over"
    if s in _UNDER:
        return "under"
    raise ValueError(f"unknown side {side!r}")


def ladder_point(n_plus) -> float:
    """Kalshi-style 'N+' strike -> the book line it equals: 3+ == over 2.5."""
    n = float(n_plus)
    if not math.isfinite(n) or n < 1 or n != int(n):
        raise ValueError(f"a ladder strike is a positive integer 'N+', got {n_plus!r}")
    return n - 0.5


def _point(stat: str, point) -> float:
    if point is None or (isinstance(point, float) and math.isnan(point)):
        if stat == "anytime_td":
            return 0.5
        raise ValueError(f"{stat}: a point is required")
    p = float(point)
    if not math.isfinite(p) or p < 0:
        raise ValueError(f"bad point {point!r}")
    return p


def actual_value(stat: str, stat_row: Optional[Mapping], td_scope: str = "offense") -> Optional[float]:
    """The stat's value from one player_stats row; None if any needed column is absent or NaN."""
    if stat not in STAT_COLUMNS:
        raise ValueError(f"unknown stat {stat!r}; known: {STATS}")
    if td_scope not in TD_SCOPES:
        raise ValueError(f"unknown td_scope {td_scope!r}")
    cols = STAT_COLUMNS[stat] + (ANYTIME_TD_BROAD_EXTRA if (stat == "anytime_td" and td_scope == "broad") else ())
    total = 0.0
    for c in cols:
        try:
            v = stat_row[c]
        except (KeyError, IndexError, TypeError):
            return None
        if v is None or pd.isna(v):
            return None
        total += float(v)
    return total


@dataclass(frozen=True)
class GradeResult:
    result: str
    actual: Optional[float] = None
    reason: str = ""


def grade_detail(stat: str, point, side, stat_row: Optional[Mapping], *, game_final: bool,
                 played: Optional[bool] = None, td_scope: str = "offense") -> GradeResult:
    """Full-information grader: (result, actual, reason). See the module docstring for every rule.

    stat_row   : the player's player_stats row for THIS game (dict / Series), or None when there is none.
    game_final : True only when the final stat lines for this game are in hand (see module docstring).
    played     : snap evidence when stat_row is None: True -> the player was on the field and recorded nothing (grade
                 against zeros); False / None -> DNP (void).
    Bad inputs (unknown stat / side, negative or missing point) raise ValueError: they are caller bugs, not outcomes.
    """
    if stat not in STAT_COLUMNS:
        raise ValueError(f"unknown stat {stat!r}; known: {STATS}")
    pt = _point(stat, point)
    sd = canon_side(side)
    if not game_final:
        return GradeResult(NOT_YET_GRADED, None, "game_not_final_or_not_in_mirror")
    if stat_row is None:
        if played is True:
            actual, why = 0.0, "played_no_stat_row->0"
        else:
            return GradeResult(VOID, None, "dnp_no_stat_row" if played is False else "no_stat_row_assumed_dnp")
    else:
        actual = actual_value(stat, stat_row, td_scope)
        if actual is None:
            return GradeResult(UNGRADABLE, None, "stat_value_missing")
        why = ""
    if abs(actual - pt) < _EPS:
        return GradeResult(PUSH, actual, why)
    over_wins = actual > pt
    return GradeResult(WIN if over_wins == (sd == "over") else LOSS, actual, why)


def grade(stat: str, point, side, stat_row: Optional[Mapping], *, game_final: bool,
          played: Optional[bool] = None, td_scope: str = "offense") -> str:
    """win | loss | push | void | not_yet_graded (| ungradable when a needed stat column is missing/NaN)."""
    return grade_detail(stat, point, side, stat_row, game_final=game_final, played=played, td_scope=td_scope).result


# ================================================================================================ batch grading
def attach_gsis(snaps: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """snap_counts -> add `gsis_id` via players.pfr_id (snap_counts carries pfr_player_id only)."""
    m = players[["gsis_id", "pfr_id"]].dropna().drop_duplicates("pfr_id")
    return snaps.merge(m, left_on="pfr_player_id", right_on="pfr_id", how="left").drop(columns=["pfr_id"])


class Actuals:
    """Indexed nflverse actuals for batch grading. Build from frames (tests) or `Actuals.from_mirror(seasons)`."""

    def __init__(self, stats: pd.DataFrame, schedules: pd.DataFrame, snaps: Optional[pd.DataFrame] = None):
        need = {"player_id", "game_id"} | set(STAT_ROW_COLUMNS)
        miss = need - set(stats.columns)
        if miss:
            raise ValueError(f"player_stats frame is missing columns {sorted(miss)}")
        self.stats = {(r["player_id"], r["game_id"]): r for r in stats.to_dict("records")}
        self.final_games = set(stats["game_id"].unique())
        sch = add_game_keys(schedules) if "game_key" not in schedules.columns else schedules.copy()
        sch["away_ev"] = [ev_code(x) for x in sch["away_team"]]
        sch["home_ev"] = [ev_code(x) for x in sch["home_team"]]
        self.games = {r["game_id"]: r for r in sch.to_dict("records")}
        self._by_key: dict = {}
        self._by_team_week: dict = {}
        self._by_team_date: dict = {}
        for gid, g in self.games.items():
            if g.get("game_key"):
                self._by_key.setdefault(g["game_key"], []).append(gid)
            for t in (g["away_ev"], g["home_ev"]):
                if t:
                    self._by_team_week.setdefault((int(g["season"]), int(g["week"]), t), []).append(gid)
                    self._by_team_date.setdefault((str(g["gameday"])[:10], t), []).append(gid)
        self.snaps = None
        self.snap_games: set = set()
        if snaps is not None:
            if "gsis_id" not in snaps.columns:
                raise ValueError("snaps frame needs a gsis_id column (use attach_gsis(snaps, players))")
            s = snaps.copy()
            for c in ("offense_snaps", "defense_snaps", "st_snaps"):
                s[c] = pd.to_numeric(s[c], errors="coerce").fillna(0)
            s["total_snaps"] = s["offense_snaps"] + s["defense_snaps"] + s["st_snaps"]
            self.snaps = {(r["gsis_id"], r["game_id"]): float(r["total_snaps"])
                          for r in s.dropna(subset=["gsis_id"]).to_dict("records")}
            self.snap_games = set(s["game_id"].unique())

    @classmethod
    def from_mirror(cls, seasons) -> "Actuals":
        from nfl.data.read import load
        seasons = sorted(set(seasons))
        stats = load("player_stats", seasons=seasons)
        snaps = attach_gsis(load("snap_counts", seasons=seasons), load("players", columns=["gsis_id", "pfr_id"]))
        return cls(stats, load("schedules"), snaps)

    def find_game(self, row: Mapping):
        """Row -> (game_id | None, why). Tries game_id, game_key, (season, week, team), (game_date, team)."""
        def one(ids, label):
            ids = list(dict.fromkeys(ids))
            if len(ids) == 1:
                return ids[0], ""
            return None, f"{label}_matches_{len(ids)}_games"

        gid = row.get("game_id")
        if isinstance(gid, str) and gid:
            return (gid, "") if gid in self.games else (None, "game_id_not_in_schedules")
        gk = row.get("game_key")
        if isinstance(gk, str) and gk:
            return one(self._by_key.get(gk, []), "game_key")
        team = ev_code(row.get("team")) if isinstance(row.get("team"), str) else None
        if _has(row, "season") and _has(row, "week") and team:
            return one(self._by_team_week.get((int(row["season"]), int(row["week"]), team), []), "season_week_team")
        gd = row.get("game_date")
        if isinstance(gd, str) and gd and team:
            return one(self._by_team_date.get((gd[:10], team), []), "date_team")
        return None, "no_game_identifier(game_id|game_key|season+week+team|game_date+team)"

    def game_is_final(self, game_id: str):
        """-> (final: bool, why). Final == the mirror has stat rows for the game. A scored game that is not in
        player_stats yet is NOT final here (the mirror lags), and an unscored game is not final at all."""
        if game_id in self.final_games:
            return True, ""
        g = self.games.get(game_id, {})
        if pd.notna(g.get("home_score")) and pd.notna(g.get("away_score")):
            return False, "scored_but_player_stats_not_mirrored_yet"
        return False, "game_not_played_yet_or_unscored"


def _has(row, k) -> bool:
    v = row.get(k)
    try:
        if v is None or pd.isna(v):
            return False
    except (TypeError, ValueError):
        return True
    return not (isinstance(v, str) and not v.strip())


def _first(row: Mapping, *names):
    for n in names:
        if _has(row, n):
            return row[n]
    return None


def grade_frame(df: pd.DataFrame, *, resolver, actuals: Actuals, td_scope: str = "offense") -> pd.DataFrame:
    """Grade a frame of quotes. Returns a COPY of `df` plus:
         gsis_id, resolve_status, resolve_how, game_id, result, actual, reason, [result_broad_td, td_scope_sensitive]

    Input columns (case-sensitive; extra columns pass through untouched):
      player            required. Name as a book/Kalshi writes it; "(IND)" team tails are honoured.
      stat              required, one of STATS.
      point             the line (half-point or integer). Aliases: strike, line. `n_plus` (a Kalshi 'N+' integer) is
                        converted with ladder_point. anytime_td may leave it blank (== 0.5).
      side              over|under|yes|no. If the column is ABSENT every row is graded for both sides (two output rows,
                        side = over/under -- handy for evidence CSVs that carry no side). A blank side -> ungradable.
      game identity     one of: game_id | game_key ("nfl:2026-10-05:ATL@NO") | season+week+team | game_date+team.
      team, position    optional hints passed to the resolver (team must be one of the game's two teams).
    `resolver` is a PlayerResolver; `actuals` an Actuals. Failures to resolve / identify / grade become
    result="ungradable" with `reason` -- never a guess and never a loss.
    """
    if "player" not in df.columns or "stat" not in df.columns:
        raise ValueError("frame needs 'player' and 'stat' columns")
    base = df.copy()
    if "side" not in base.columns:
        a, b = base.copy(), base.copy()
        a["side"], b["side"] = "over", "under"
        base = pd.concat([a, b], ignore_index=True)
    out = []
    for rec in base.to_dict("records"):
        out.append({**rec, **_grade_row(rec, resolver, actuals, td_scope)})
    return pd.DataFrame(out, columns=list(base.columns) + [c for c in _OUT_COLS if c not in base.columns])


_OUT_COLS = ("gsis_id", "resolve_status", "resolve_how", "game_id", "result", "actual", "reason",
             "result_broad_td", "td_scope_sensitive")


def _blank(**kw):
    d = {c: None for c in _OUT_COLS}
    d.update(kw)
    return d


def _grade_row(rec: dict, resolver, actuals: Actuals, td_scope: str) -> dict:
    stat = rec.get("stat")
    if stat not in STAT_COLUMNS:
        return _blank(result=UNGRADABLE, reason=f"unknown_stat:{stat}")
    if not _has(rec, "side"):
        return _blank(result=UNGRADABLE, reason="blank_side")
    try:
        side = canon_side(rec["side"])
        if _has(rec, "n_plus") and _first(rec, "point", "strike", "line") is None:
            point = ladder_point(rec["n_plus"])
        else:
            point = _point(stat, _first(rec, "point", "strike", "line"))
    except ValueError as e:
        return _blank(result=UNGRADABLE, reason=f"bad_input:{e}")

    gid, why = actuals.find_game(rec)
    if gid is None:
        return _blank(result=UNGRADABLE, reason=why)
    g = actuals.games[gid]
    team = ev_code(rec["team"]) if _has(rec, "team") and isinstance(rec.get("team"), str) else None
    if _has(rec, "team") and team is None:
        return _blank(game_id=gid, result=UNGRADABLE, reason=f"unrecognised_team:{rec.get('team')}")
    if team is not None and team not in (g["away_ev"], g["home_ev"]):
        return _blank(game_id=gid, result=UNGRADABLE, reason=f"team_{team}_not_in_game")

    res = resolver.resolve(str(rec["player"]), season=int(g["season"]), week=int(g["week"]), team=team,
                           position=rec.get("position") if _has(rec, "position") else None)
    if not res.ok:
        return _blank(game_id=gid, resolve_status=res.status, resolve_how=res.how,
                      result=UNGRADABLE, reason=f"player_{res.status}:{res.how}:{res.note}"[:200])
    pid = res.gsis_id
    base = dict(gsis_id=pid, resolve_status=res.status, resolve_how=res.how, game_id=gid)

    # the resolved player must belong to one of the game's two teams that week (guards a wrong-namesake id)
    teams = resolver.teams_of(pid, int(g["season"]), int(g["week"]))
    if teams and not (teams & {g["away_ev"], g["home_ev"]}):
        return _blank(**base, result=UNGRADABLE, reason=f"player_on_{sorted(teams)}_not_in_game")

    final, fwhy = actuals.game_is_final(gid)
    if not final:
        return _blank(**base, result=NOT_YET_GRADED, reason=fwhy)

    row = actuals.stats.get((pid, gid))
    played = None
    snap_note = ""
    if row is None:
        if actuals.snaps is not None and gid in actuals.snap_games:
            played = actuals.snaps.get((pid, gid), 0.0) > 0
            snap_note = f"snaps={actuals.snaps.get((pid, gid), 0.0):g}"
        else:
            snap_note = "no_snap_data_for_game"
    elif team is not None and ev_code(row.get("team")) not in (None, team):
        return _blank(**base, result=UNGRADABLE, reason=f"stat_row_team_{row.get('team')}_vs_hint_{team}")

    d = grade_detail(stat, point, side, row, game_final=True, played=played, td_scope=td_scope)
    out = _blank(**base, result=d.result, actual=d.actual, reason=";".join(x for x in (d.reason, snap_note) if x))
    if stat == "anytime_td" and td_scope == "offense":
        b = grade_detail(stat, point, side, row, game_final=True, played=played, td_scope="broad")
        out["result_broad_td"] = b.result
        out["td_scope_sensitive"] = b.result != d.result
    return out


def summarize(graded: pd.DataFrame) -> dict:
    """Counts by result, plus the ungradable/not-yet reasons -- the one-glance health check of a grading run."""
    r = graded["result"].value_counts().to_dict()
    decided = r.get(WIN, 0) + r.get(LOSS, 0)
    return {"counts": r, "win_rate_ex_push_void": (r.get(WIN, 0) / decided) if decided else float("nan"),
            "reasons": graded.loc[graded["result"].isin([UNGRADABLE, NOT_YET_GRADED, VOID]), "reason"]
            .str.split(":").str[0].value_counts().to_dict()}
