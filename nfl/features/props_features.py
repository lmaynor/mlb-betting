"""NFL player-prop feature builder (one row per player x regular-season game), STRICTLY PRE-GAME.

    from nfl.features.props_features import build_player_week_features, feature_columns
    df = build_player_week_features(range(2018, 2027))          # GCS-backed (nfl.data.read.load)
    X = df[feature_columns(df)]

The pure core is `build_from_frames(Frames(...), emit_seasons)` -- no I/O, used by the offline tests and by
`leakage_check`.

Targets (columns `y_*`, the ONLY post-game columns in the output)
    y_pass_yds, y_rush_yds, y_rec_yds, y_receptions, y_pass_tds   -- the five numeric markets
    y_td_count      = rushing_tds + receiving_tds  (the Poisson target behind anytime_td)
    y_anytime_td    = 1 if y_td_count >= 1  (passing TDs do NOT count -- the book "anytime TD scorer" market)
    y_anytime_td_all= same but also counting special-teams / defensive / fumble-recovery TDs (book rules vary;
                      the grader must pick the definition the book uses)

Experimental team_missing_*_share_l5 columns (teammate-absence usage redistribution): built into every frame, but
NOT in feature_columns() by default (pass include_experimental=True). See _attach_team_missing_shares.
Extra targets y_pass_attempts / y_completions / y_rush_attempts back the volume-count stats in nfl.models.props.

Leakage contract
    Every feature for a row at (season, week) is a function of data from STRICTLY EARLIER games, or of
    information that exists before kickoff (closing spread/total, roof/temp/wind, rest, injury report, the
    game's announced starting QB id). Trailing stats are `shift(1)` within the entity (player / team) --
    never rolling windows that include the current row. Outcome columns of the current game are dropped from
    the output. `leakage_check` perturbs one week's outcomes and asserts every feature at or before that week
    is bit-identical.

POPULATION (read this before trusting any number)
    nflverse `player_stats` has a row only for players who recorded at least one stat. A player who is on the
    roster but did not play (DNP / inactive / out) has NO row, and a player who played a snap or two and
    recorded nothing may or may not have one. We therefore define the label population as

        "player recorded >= 1 offensive snap (snap_counts) OR >= 1 non-zero offensive stat (player_stats)"

    and add ZERO-stat rows for snap-only players (snap_counts joined through players.pfr_id -> gsis_id, ~99.9%
    mapped). Players with 0 offensive snaps AND all-zero offensive stats (special-teams-only appearances) are
    dropped. Consequences / selection bias, stated plainly:
      * The model learns E[stat | player plays offense in this game]. Books void DNP props, so this matches a
        live bet's conditioning for the "player is active and plays" case -- but the live scorer only sees
        PRE-game status, so it must itself decide which players will play (injury report is a feature; the
        `own_status` column is only the FINAL report). A player who is listed healthy and then exits on the
        first series with a zero line IS in the population (via snaps) if he logged a snap; one who is
        inactive late is NOT. That residual late-scratch DNP mass is invisible to us.
      * Rows are conditioned on playing, so P(over) is a conditional-on-playing probability. Do not use it
        as an unconditional probability for books that grade DNP as a loss.
      * Eligibility flags (`elig_*`) restrict each stat's modeling population to players whose TRAILING
        usage looks like a book-quoted player. Thresholds are assumptions (ELIG), not the books' actual
        quoting rule (no historical prop lines exist in this repo). Rookies / first-game players have no
        trailing usage and are ineligible: the live scorer will not cover them either.
      * Bye weeks are handled by indexing on the player's own prior GAMES, not calendar weeks. Missed games
        are visible through `games_missed_gap`, `szn_availability`, and `team_changed`.

Data quirks handled (verified on the real mirror 2026-10-14)
    * depth_charts: format changed in 2025 (weekly `depth_team` -> dated `pos_abb/pos_rank` snapshots ending
      in March); NOT used (snap-share + trailing usage carry the same signal with a consistent schema).
    * injuries: `season_type` column absent before 2025; one duplicate (season, week, gsis_id) trio -> last.
    * schedules: 2026 future weeks have NaN spread/total/QBs; upcoming rows carry NaN lines until populated --
      the live scorer SHOULD overwrite `total_line`, `team_spread`, `implied_*` from its own consensus feed.
    * franchise relocations are folded (OAK->LV, SD->LAC, STL->LA) so trailing team histories do not reset.
    * `spread_line` is HOME-favoured positive (verified: corr(result, spread_line) = +0.44).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TEAM_ALIAS = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
SKILL_POS = {"QB", "RB", "FB", "WR", "TE"}
POS_CODE = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
EWM_HALFLIFE = 4.0          # games (carries across seasons)

STATS = ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td")

# Eligibility thresholds on the TRAILING ewm of usage (assumptions -- see module docstring).
ELIG = {
    "pass_yds": ("pass_att_ewm", 20.0),         # starting QBs (backups / partial-game QBs are not quoted)
    "pass_tds": ("pass_att_ewm", 20.0),
    "rush_yds": ("carries_ewm", 4.0),
    "rec_yds": ("tgt_ewm", 2.5),
    "receptions": ("tgt_ewm", 2.5),
}
ELIG_ATD_USAGE = 3.0        # carries_ewm + tgt_ewm  (QBs: carries_ewm >= 4)

PLAYER_RAW = {
    "attempts": "pass_att", "completions": "pass_cmp", "passing_yards": "pass_yds", "passing_tds": "pass_tds",
    "passing_interceptions": "pass_int", "sacks_suffered": "sacks", "passing_epa": "pass_epa",
    "carries": "carries", "rushing_yards": "rush_yds", "rushing_tds": "rush_tds",
    "targets": "tgt", "receptions": "rec", "receiving_yards": "rec_yds", "receiving_tds": "rec_tds",
    "receiving_air_yards": "rec_air", "target_share": "tgt_share", "air_yards_share": "ay_share", "wopr": "wopr",
    "special_teams_tds": "st_tds", "def_tds": "def_tds", "fumble_recovery_tds": "fr_tds",
}
STAT_COLS = ["pass_att", "pass_cmp", "pass_yds", "pass_tds", "pass_int", "sacks", "pass_epa", "carries",
             "rush_yds", "rush_tds", "tgt", "rec", "rec_yds", "rec_tds", "rec_air", "tgt_share", "ay_share",
             "wopr", "st_tds", "def_tds", "fr_tds"]
OFFENSE_ACT_COLS = ["pass_att", "carries", "tgt", "rec", "pass_yds", "rush_yds", "rec_yds", "pass_tds",
                    "rush_tds", "rec_tds", "sacks"]
BASES = ["pass_att", "pass_cmp", "pass_yds", "pass_tds", "sacks", "pass_epa", "carries", "rush_yds", "rush_tds",
         "tgt", "rec", "rec_yds", "rec_tds", "rec_air", "tgt_share", "ay_share", "wopr", "td_count",
         "snaps", "snap_pct", "rz_car", "gl_car", "rz_tgt"]
TRAIL_STATS = ("l3", "l5", "szn", "ewm")

OFF_COLS = ["off_plays", "off_pass_rate", "off_pass_yds", "off_rush_yds", "off_pass_tds", "off_rush_tds", "off_pts"]
ALW_COLS = [c.replace("off_", "alw_") for c in OFF_COLS]
DVP_METRICS = ["rec_yds", "rec", "tgt", "rush_yds", "carries", "tds", "pass_yds", "pass_att", "pass_tds"]
DVP_POS = ["QB", "RB", "WR", "TE"]

# ---- pbp context (OPT-IN: built into every frame when the derived tables are supplied, excluded from the default
# feature list; pass include_pbp_ctx=True). Trailing (shift(1)) EWM / L5 of nflfastR-style team and player usage rates
# from nfl.data.derived. All names start with PC_PREFIX.
PC_PREFIX = "pc_"
PC_OWN = ["neutral_pass_rate", "pass_oe", "sec_per_play_neutral", "epa_dropback", "epa_rush", "sack_rate",
          "rz_pass_rate", "adot", "expl_pass_rate", "success_rate", "no_huddle_rate"]
PC_DEF = ["epa_dropback", "epa_rush", "expl_pass_rate", "sack_rate", "adot", "neutral_pass_rate", "success_rate",
          "pass_oe"]
PC_OPP_OFF = ["sec_per_play_neutral", "neutral_pass_rate"]
PC_PLAYER = ["deep_tgt", "tgt_rz20", "tgt_in10", "car_rz10", "rush_expl10", "rec_epa", "rush_epa", "pass_epa",
             "air_yards", "tgt_pbp", "car_pbp"]
PC_PLAYER_COUNTS = [c for c in PC_PLAYER if not c.endswith("_epa")]

META_COLS = ["player_id", "player_name", "position", "pos_grp", "team", "opponent", "season", "week", "game_id",
             "gameday", "is_upcoming"]
Y_COLS = ["y_pass_yds", "y_rush_yds", "y_rec_yds", "y_receptions", "y_pass_tds", "y_td_count", "y_anytime_td",
          "y_anytime_td_all", "y_pass_attempts", "y_completions", "y_rush_attempts"]
ELIG_COLS = [f"elig_{s}" for s in STATS]

# ---- teammate-absence usage redistribution (EXPERIMENTAL: excluded from the default model feature list)
# Sum of the trailing-5-appearance usage share of same-team players the game-week injury report lists as absent.
TEAM_MISSING_COLS = ["team_missing_target_share_l5", "team_missing_rush_share_l5", "team_missing_pass_att_share_l5"]
EXPERIMENTAL_FEATURES = tuple(TEAM_MISSING_COLS)
# report_status values (lower-cased, stripped) that count as ABSENT. Real nflverse `injuries.report_status` values
# are Out / Doubtful / Questionable (or blank); Questionable is NOT absent. The IR-style aliases are defensive: the
# weekly injury report does not normally carry injured-reserve players (they sit on rosters_weekly status RES, which
# this builder does not read), so IR players are NOT captured unless an injuries frame happens to list them.
MISSING_STATUS = frozenset({"out", "doubtful", "ir", "injured reserve", "reserve/injured"})
MISSING_WINDOW = 5                 # appearances (same convention as the *_l5 columns: missed games are skipped)
MISSING_TOLERANCE_WIDX = 20.0      # an absent player's last appearance must be within ~20 week-slots (~1 season)


# ------------------------------------------------------------------------------------------------ frames
@dataclass
class Frames:
    """Raw nflverse frames. `snap_counts`, `injuries`, `players`, `pbp_rz` are optional (feature groups
    degrade to NaN when absent)."""
    player_stats: pd.DataFrame
    schedules: pd.DataFrame
    team_stats: pd.DataFrame
    snap_counts: Optional[pd.DataFrame] = None
    injuries: Optional[pd.DataFrame] = None
    players: Optional[pd.DataFrame] = None
    pbp_rz: Optional[pd.DataFrame] = None     # (game_id, player_id, rz_car, gl_car, rz_tgt) from aggregate_pbp_redzone
    pbp_team: Optional[pd.DataFrame] = None   # nfl.data.derived team_game_pbp (off_* / def_* rates per game, team)
    pbp_player: Optional[pd.DataFrame] = None  # nfl.data.derived player_game_pbp (targets / air yards / rz usage)


def _alias(s: pd.Series) -> pd.Series:
    return s.replace(TEAM_ALIAS)


# ------------------------------------------------------------------------------------------------ helpers
def _grp_ewm(x: pd.Series, keys: pd.Series, halflife: float) -> pd.Series:
    """Per-key EWM INCLUDING the current row (callers shift(1) it to make it pre-game)."""
    res = x.groupby(keys, sort=False).ewm(halflife=halflife, ignore_na=True, min_periods=1).mean()
    return res.reset_index(level=0, drop=True).reindex(x.index)


def add_trailing(df: pd.DataFrame, key: str, cols: Iterable[str], stats=TRAIL_STATS, halflife: float = EWM_HALFLIFE,
                 season_col: str = "season", post_cols: Iterable[str] = ()) -> dict:
    """Pre-game trailing features of `cols` within `key`. `df` MUST be sorted by (key, time) with a unique index.

    l3/l5: mean of the entity's previous 3/5 appearances (shift(1) first; cross-season; missed games skipped)
    szn  : mean of previous appearances in the same season
    ewm  : exponentially weighted mean of ALL previous appearances (halflife games; carries across seasons)
    `post_cols` additionally return `<c>_post` = ewm INCLUDING the current row (used only to value an absent
    player's usage from his LAST game -- never as a feature of that same row).
    """
    stats = tuple(stats)
    out: dict = {}
    g = df.groupby(key, sort=False)
    keys = df[key]
    for c in cols:
        x = df[c].astype(float)
        if "l3" in stats or "l5" in stats:
            kmax = 5 if "l5" in stats else 3
            arr = np.column_stack([g[c].shift(k).to_numpy(dtype=float) for k in range(1, kmax + 1)])
            for name, L in (("l3", 3), ("l5", 5)):
                if name in stats:
                    sub = arr[:, :L]
                    cnt = np.sum(~np.isnan(sub), axis=1)
                    tot = np.nansum(sub, axis=1)
                    out[f"{c}_{name}"] = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
        if "szn" in stats:
            xf = x.fillna(0.0)
            v = x.notna().astype(float)
            gk = [keys, df[season_col]]
            # exclusive prefix sums via shift (NOT cumsum - x: that re-introduces the current row through
            # floating-point rounding and is not bit-identical under outcome perturbation)
            cs = xf.groupby(gk, sort=False).cumsum().groupby(gk, sort=False).shift(1).fillna(0.0)
            cn = v.groupby(gk, sort=False).cumsum().groupby(gk, sort=False).shift(1).fillna(0.0)
            out[f"{c}_szn"] = np.where(cn > 0, cs / np.maximum(cn, 1), np.nan)
        if "ewm" in stats or c in post_cols:
            post = _grp_ewm(x, keys, halflife)
            if "ewm" in stats:
                out[f"{c}_ewm"] = post.groupby(keys, sort=False).shift(1).to_numpy(dtype=float)
            if c in post_cols:
                out[f"{c}_post"] = post.to_numpy(dtype=float)
    return {k: pd.Series(v, index=df.index) if not isinstance(v, pd.Series) else v for k, v in out.items()}


def aggregate_pbp_redzone(pbp: pd.DataFrame) -> pd.DataFrame:
    """play-by-play -> per (game_id, player_id) red-zone usage. Columns required: game_id, yardline_100,
    rush_attempt, pass_attempt, qb_kneel, rusher_player_id, receiver_player_id. Returns rz_car (carries from
    inside the 10), gl_car (inside the 3), rz_tgt (targets from inside the 20)."""
    p = pbp
    run = p[(p["rush_attempt"] == 1) & (p["qb_kneel"].fillna(0) == 0) & p["rusher_player_id"].notna()]
    car = pd.DataFrame({"game_id": run["game_id"], "player_id": run["rusher_player_id"],
                        "rz_car": (run["yardline_100"] <= 10).astype(float),
                        "gl_car": (run["yardline_100"] <= 3).astype(float)})
    air = p[(p["pass_attempt"] == 1) & p["receiver_player_id"].notna()]
    tgt = pd.DataFrame({"game_id": air["game_id"], "player_id": air["receiver_player_id"],
                        "rz_tgt": (air["yardline_100"] <= 20).astype(float)})
    a = car.groupby(["game_id", "player_id"], as_index=False)[["rz_car", "gl_car"]].sum()
    b = tgt.groupby(["game_id", "player_id"], as_index=False)[["rz_tgt"]].sum()
    out = a.merge(b, on=["game_id", "player_id"], how="outer")
    for c in ("rz_car", "gl_car", "rz_tgt"):
        out[c] = out[c].fillna(0.0)
    return out


# ------------------------------------------------------------------------------------------------ team-game table
def _team_game_table(fr: Frames, played_keys: set) -> pd.DataFrame:
    s = fr.schedules.copy()
    s["home_team"] = _alias(s["home_team"])
    s["away_team"] = _alias(s["away_team"])
    s["ord"] = s["season"] * 100 + s["week"]
    roof = s["roof"].astype("object")
    indoor = np.where(roof.isin(["dome", "closed"]), 1.0, np.where(roof.isna(), np.nan, 0.0))
    s["indoor"] = indoor
    s["temp_f"] = np.where(indoor == 1.0, np.nan, s["temp"].astype(float))
    s["wind_mph"] = np.where(indoor == 1.0, np.nan, s["wind"].astype(float))
    s["div_game"] = s["div_game"].astype(float) if "div_game" in s else np.nan

    def side(home: bool) -> pd.DataFrame:
        t, o = ("home", "away") if home else ("away", "home")
        sign = 1.0 if home else -1.0
        return pd.DataFrame({
            "game_id": s["game_id"], "season": s["season"], "week": s["week"], "ord": s["ord"],
            "gameday": s["gameday"], "game_type": s["game_type"],
            "team": s[f"{t}_team"], "opp": s[f"{o}_team"], "is_home": 1.0 if home else 0.0,
            "team_spread": sign * s["spread_line"].astype(float), "total_line": s["total_line"].astype(float),
            "team_pts": s[f"{t}_score"].astype(float), "opp_pts": s[f"{o}_score"].astype(float),
            "rest": s[f"{t}_rest"].astype(float), "opp_rest": s[f"{o}_rest"].astype(float),
            "qb_id": s[f"{t}_qb_id"], "indoor": s["indoor"], "temp_f": s["temp_f"], "wind_mph": s["wind_mph"],
            "div_game": s["div_game"],
        })

    tg = pd.concat([side(True), side(False)], ignore_index=True)
    tg = tg.sort_values(["team", "ord"], kind="mergesort").reset_index(drop=True)
    tg["implied_team_total"] = tg["total_line"] / 2.0 + tg["team_spread"] / 2.0
    tg["implied_opp_total"] = tg["total_line"] / 2.0 - tg["team_spread"] / 2.0
    tg["tg_idx"] = tg.groupby("team").cumcount()
    tg["tg_szn_n"] = tg.groupby(["team", "season"]).cumcount()
    tg["played"] = [(g, t) in played_keys for g, t in zip(tg["game_id"], tg["team"])]
    prev_qb = tg.groupby("team")["qb_id"].shift(1)
    tg["qb_id_f"] = tg["qb_id"].where(tg["qb_id"].notna(), prev_qb)       # upcoming game: last game's starter
    tg["qb_known"] = tg["qb_id"].notna().astype(float)
    tg["qb_changed"] = np.where(tg["qb_id_f"].notna() & prev_qb.notna(), (tg["qb_id_f"] != prev_qb).astype(float),
                                np.nan)
    return tg


def _attach_team_stats(tg: pd.DataFrame, ts: pd.DataFrame) -> pd.DataFrame:
    t = ts.copy()
    t["team"] = _alias(t["team"])
    pass_plays = t["attempts"].astype(float) + t["sacks_suffered"].astype(float)
    plays = pass_plays + t["carries"].astype(float)
    t = pd.DataFrame({
        "game_id": t["game_id"], "team": t["team"], "off_plays": plays,
        "off_pass_rate": pass_plays / plays.replace(0, np.nan),
        "off_pass_yds": t["passing_yards"].astype(float), "off_rush_yds": t["rushing_yards"].astype(float),
        "off_pass_tds": t["passing_tds"].astype(float), "off_rush_tds": t["rushing_tds"].astype(float),
    }).drop_duplicates(["game_id", "team"])
    tg = tg.merge(t, on=["game_id", "team"], how="left")
    tg["off_pts"] = tg["team_pts"]
    # what the OPPONENT's offense produced against this team's defense (alw_*)
    o = tg[["game_id", "team"] + OFF_COLS].rename(columns={"team": "opp", **{c: c.replace("off_", "alw_") for c in OFF_COLS}})
    return tg.merge(o, on=["game_id", "opp"], how="left")


def _attach_dvp(tg: pd.DataFrame, pg: pd.DataFrame) -> pd.DataFrame:
    """Defense-allowed-by-position per (defense team, game): what each position group produced AGAINST it."""
    d = pg[~pg["is_upcoming"]].copy()
    d["tds"] = d["rush_tds"].fillna(0) + d["rec_tds"].fillna(0)
    agg = d.groupby(["opp", "game_id", "pos_grp"])[DVP_METRICS].sum()
    wide = agg.unstack("pos_grp")
    wide.columns = [f"dvp_{pos}_{m}" for m, pos in wide.columns]
    wide = wide.reset_index().rename(columns={"opp": "team"})
    tg = tg.merge(wide, on=["game_id", "team"], how="left")
    dcols = [c for c in tg.columns if c.startswith("dvp_")]
    for pos in DVP_POS:                      # a played game with no row for a position => 0 allowed
        for m in DVP_METRICS:
            c = f"dvp_{pos}_{m}"
            if c not in tg.columns:
                tg[c] = np.nan
            tg[c] = np.where(tg["played"], tg[c].fillna(0.0), np.nan)
    return tg


# ------------------------------------------------------------------------------------------------ player-game table
def _player_game_raw(fr: Frames) -> pd.DataFrame:
    ps = fr.player_stats
    ps = ps[ps["position"].isin(SKILL_POS)].copy()
    ps = ps.rename(columns=PLAYER_RAW)
    for c in STAT_COLS:
        if c not in ps:
            ps[c] = np.nan if c == "pass_epa" else 0.0
        ps[c] = pd.to_numeric(ps[c], errors="coerce")
        if c != "pass_epa":
            ps[c] = ps[c].fillna(0.0)
    ps["team"] = _alias(ps["team"])
    ps["opp"] = _alias(ps["opponent_team"])
    ps["player_name"] = ps["player_display_name"] if "player_display_name" in ps else ps.get("player_name")
    ps["has_stat_row"] = 1.0
    base = ps[["player_id", "player_name", "position", "team", "opp", "season", "week", "game_id", "has_stat_row"]
              + STAT_COLS].copy()

    snaps_ok = fr.snap_counts is not None and fr.players is not None and len(fr.snap_counts) > 0
    base["snaps"] = np.nan
    base["snap_pct"] = np.nan
    if snaps_ok:
        xw = fr.players[["pfr_id", "gsis_id"]].dropna().drop_duplicates("pfr_id")
        sn = fr.snap_counts.merge(xw, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        sn = sn.dropna(subset=["gsis_id"]).drop_duplicates(["gsis_id", "game_id"])
        sn = sn.rename(columns={"gsis_id": "player_id"})
        sn["team"] = _alias(sn["team"])
        sn["opp"] = _alias(sn["opponent"])
        m = base.merge(sn[["player_id", "game_id", "offense_snaps", "offense_pct"]], on=["player_id", "game_id"],
                       how="left")
        base["snaps"] = m["offense_snaps"].to_numpy(dtype=float)
        base["snap_pct"] = m["offense_pct"].to_numpy(dtype=float)
        have = set(zip(base["player_id"], base["game_id"]))
        gh = sn[(sn["offense_snaps"] > 0) & sn["position"].isin(SKILL_POS)]
        gh = gh[[(p, g) not in have for p, g in zip(gh["player_id"], gh["game_id"])]]
        if len(gh):
            ghost = pd.DataFrame({
                "player_id": gh["player_id"], "player_name": gh["player"], "position": gh["position"],
                "team": gh["team"], "opp": gh["opp"], "season": gh["season"], "week": gh["week"],
                "game_id": gh["game_id"], "has_stat_row": 0.0, "snaps": gh["offense_snaps"].astype(float),
                "snap_pct": gh["offense_pct"].astype(float)})
            for c in STAT_COLS:
                ghost[c] = np.nan if c == "pass_epa" else 0.0
            base = pd.concat([base, ghost], ignore_index=True)

    # drop special-teams-only appearances: no offensive snap and no offensive stat
    off_act = (base[OFFENSE_ACT_COLS].abs().sum(axis=1) > 0) | (base["snaps"].fillna(0) > 0)
    base = base[off_act].copy()

    if fr.pbp_rz is not None and len(fr.pbp_rz):
        base = base.merge(fr.pbp_rz, on=["game_id", "player_id"], how="left")
        covered = base["game_id"].isin(set(fr.pbp_rz["game_id"]))
        for c in ("rz_car", "gl_car", "rz_tgt"):
            base[c] = np.where(covered, base[c].fillna(0.0), np.nan)
    else:
        for c in ("rz_car", "gl_car", "rz_tgt"):
            base[c] = np.nan
    base["td_count"] = base["rush_tds"] + base["rec_tds"]
    base["pos_grp"] = base["position"].replace({"FB": "RB"})
    base = base[base["pos_grp"].isin(DVP_POS)]
    base["is_upcoming"] = False
    return base.reset_index(drop=True)


def _add_upcoming_rows(pg: pd.DataFrame, tg: pd.DataFrame, emit_seasons: set, lookback_games: int = 3) -> pd.DataFrame:
    """Placeholder rows (outcomes NaN) for each team's NEXT unplayed game: players who appeared in that team's last
    `lookback_games` played games. The row's features are computed by the same shift(1) pipeline."""
    un = tg[(~tg["played"]) & tg["season"].isin(emit_seasons) & (tg["game_type"] == "REG")]
    if un.empty:
        return pg
    nxt = un.sort_values("ord").groupby("team", as_index=False).first()
    pg_idx = pg.merge(tg[["game_id", "team", "tg_idx"]], on=["game_id", "team"], how="left")
    rows = []
    for r in nxt.itertuples(index=False):
        recent = pg_idx[(pg_idx["team"] == r.team) & (pg_idx["tg_idx"] < r.tg_idx)
                        & (pg_idx["tg_idx"] >= r.tg_idx - lookback_games)]
        if recent.empty:
            continue
        last = recent.sort_values("tg_idx").groupby("player_id", as_index=False).last()
        rows.append(pd.DataFrame({
            "player_id": last["player_id"], "player_name": last["player_name"], "position": last["position"],
            "pos_grp": last["pos_grp"], "team": r.team, "opp": r.opp, "season": r.season, "week": r.week,
            "game_id": r.game_id, "has_stat_row": np.nan, "is_upcoming": True}))
    if not rows:
        return pg
    up = pd.concat(rows, ignore_index=True)
    for c in STAT_COLS + ["snaps", "snap_pct", "td_count", "rz_car", "gl_car", "rz_tgt"]:
        up[c] = np.nan
    return pd.concat([pg, up], ignore_index=True)


# ------------------------------------------------------------------------------------------------ main builder
def build_from_frames(fr: Frames, emit_seasons: Iterable[int], include_upcoming: bool = False,
                      include_postseason_history: bool = True) -> pd.DataFrame:
    emit = set(int(s) for s in emit_seasons)
    sched_type = fr.schedules.set_index("game_id")["game_type"]

    ps_keys = fr.player_stats[["game_id", "team"]].copy()
    ps_keys["team"] = _alias(ps_keys["team"])
    played_keys = set(zip(ps_keys["game_id"], ps_keys["team"]))
    tg = _team_game_table(fr, played_keys)

    pg = _player_game_raw(fr)
    if not include_postseason_history:
        pg = pg[pg["game_id"].map(sched_type) == "REG"]
    if include_upcoming:
        pg = _add_upcoming_rows(pg, tg, emit)

    tg = _attach_team_stats(tg, fr.team_stats)
    tg = _attach_dvp(tg, pg)

    # ---- team-level trailing (own offense; opponent defense allowed; defense vs position)
    tg = tg.sort_values(["team", "ord"], kind="mergesort").reset_index(drop=True)
    dvp_cols = [f"dvp_{p}_{m}" for p in DVP_POS for m in DVP_METRICS]
    tg = _attach_pbp_team(tg, fr.pbp_team)
    tr_pc = _pbp_team_trailing(tg)
    tr_off = add_trailing(tg, "team", OFF_COLS, stats=("l5", "ewm"))
    tr_alw = add_trailing(tg, "team", ALW_COLS, stats=("l5", "szn", "ewm"))
    tr_dvp = add_trailing(tg, "team", dvp_cols, stats=("l5", "ewm"))
    tgt_cols = pd.DataFrame({**tr_off, **tr_alw, **tr_dvp, **tr_pc})
    tg = pd.concat([tg, tgt_cols], axis=1)
    tg["exp_team_pass_plays"] = tg["off_plays_ewm"] * tg["off_pass_rate_ewm"]
    tg["exp_team_rush_plays"] = tg["off_plays_ewm"] * (1.0 - tg["off_pass_rate_ewm"])

    # ---- player-level trailing
    pg["ord"] = pg["season"] * 100 + pg["week"]
    pg["widx"] = (pg["season"] - 2000) * 30 + pg["week"]
    pg = pg.sort_values(["player_id", "ord"], kind="mergesort").reset_index(drop=True)
    pg = pg.merge(tg[["game_id", "team", "tg_idx", "tg_szn_n"]], on=["game_id", "team"], how="left")
    pg = pg.sort_values(["player_id", "ord"], kind="mergesort").reset_index(drop=True)
    tr = add_trailing(pg, "player_id", BASES, post_cols=("tgt", "carries", "rec"))
    pg = pd.concat([pg, pd.DataFrame(tr)], axis=1)
    pg = _attach_pbp_player(pg, fr.pbp_player)
    gp = pg.groupby("player_id", sort=False)
    prev_tg = gp["tg_idx"].shift(1)
    prev_team = gp["team"].shift(1)
    same_team = prev_team == pg["team"]
    pg["team_changed"] = np.where(prev_team.notna(), (~same_team).astype(float), np.nan)
    pg["games_missed_gap"] = np.where(same_team, pg["tg_idx"] - prev_tg - 1.0, np.nan)
    pg["n_prior_games"] = gp.cumcount().astype(float)
    pg["szn_games_prior"] = pg.groupby(["player_id", "season"], sort=False).cumcount().astype(float)
    pg["szn_availability"] = np.where(pg["tg_szn_n"] > 0, pg["szn_games_prior"] / pg["tg_szn_n"].clip(lower=1), np.nan)
    pg["ypt_ewm"] = pg["rec_yds_ewm"] / pg["tgt_ewm"].where(pg["tgt_ewm"] > 0)
    pg["catch_rate_ewm"] = pg["rec_ewm"] / pg["tgt_ewm"].where(pg["tgt_ewm"] > 0)
    pg["ypc_ewm"] = pg["rush_yds_ewm"] / pg["carries_ewm"].where(pg["carries_ewm"] > 0)
    pg["ypa_ewm"] = pg["pass_yds_ewm"] / pg["pass_att_ewm"].where(pg["pass_att_ewm"] > 0)
    pg["td_rate_per_touch_ewm"] = pg["td_count_ewm"] / (pg["carries_ewm"] + pg["tgt_ewm"]).where(
        (pg["carries_ewm"] + pg["tgt_ewm"]) > 0)

    # ---- static player attributes (age / draft capital / experience)
    pg["age"] = np.nan
    pg["draft_pick"] = np.nan
    pg["exp_years"] = np.nan
    if fr.players is not None and "birth_date" in fr.players:
        pl = fr.players.drop_duplicates("gsis_id").set_index("gsis_id")
        bd = pd.to_datetime(pl["birth_date"], errors="coerce")
        gd = pd.to_datetime(pg["game_id"].map(fr.schedules.set_index("game_id")["gameday"]), errors="coerce")
        pg["age"] = ((gd - pg["player_id"].map(bd)).dt.days / 365.25).to_numpy(dtype=float)
        pg["draft_pick"] = pg["player_id"].map(pd.to_numeric(pl.get("draft_pick"), errors="coerce"))
        pg["exp_years"] = pg["season"] - pg["player_id"].map(pd.to_numeric(pl.get("rookie_season"), errors="coerce"))

    # ---- game environment + own-team trailing offense
    tcols = ["game_id", "team", "is_home", "team_spread", "total_line", "implied_team_total", "implied_opp_total",
             "rest", "opp_rest", "indoor", "temp_f", "wind_mph", "div_game", "qb_id_f", "qb_known", "qb_changed",
             "gameday", "game_type", "exp_team_pass_plays", "exp_team_rush_plays"] + \
            [f"{c}_{s}" for c in OFF_COLS for s in ("l5", "ewm")] + \
            [c for c in tg.columns if c.startswith(PC_PREFIX + "tm_")]
    ctx = tg[tcols].rename(columns={c: f"team_{c}" for c in tcols if c.startswith("off_")})
    pg = pg.merge(ctx, on=["game_id", "team"], how="left")

    # ---- opponent defence (allowed) + opponent pace + defence vs the player's position group
    ocols = ["game_id", "team"] + [f"{c}_{s}" for c in ALW_COLS for s in ("l5", "szn", "ewm")] + \
            ["off_plays_ewm", "off_pass_rate_ewm"] + \
            [f"dvp_{p}_{m}_{s}" for p in DVP_POS for m in DVP_METRICS for s in ("l5", "ewm")] + \
            [c for c in tg.columns if c.startswith(PC_PREFIX + "opp_")]
    octx = tg[ocols].rename(columns={"team": "opp"})
    octx = octx.rename(columns={"off_plays_ewm": "opp_off_plays_ewm", "off_pass_rate_ewm": "opp_off_pass_rate_ewm",
                                **{c: f"opp_{c}" for c in octx.columns if c.startswith("alw_")}})
    pg = pg.merge(octx, on=["game_id", "opp"], how="left")
    posn = pg["pos_grp"].map({p: i for i, p in enumerate(DVP_POS)}).to_numpy()
    new = {}
    for m in DVP_METRICS:
        for s in ("l5", "ewm"):
            stack = np.column_stack([pg[f"dvp_{p}_{m}_{s}"].to_numpy(dtype=float) for p in DVP_POS])
            new[f"opp_dvp_{m}_{s}"] = stack[np.arange(len(pg)), posn]
    pg = pd.concat([pg, pd.DataFrame(new, index=pg.index)], axis=1)
    pg = pg.drop(columns=[f"dvp_{p}_{m}_{s}" for p in DVP_POS for m in DVP_METRICS for s in ("l5", "ewm")])

    # ---- the game's QB (starter id from schedule; forward-filled for the upcoming game)
    qbf = pg[["player_id", "game_id", "pass_att_ewm", "ypa_ewm", "pass_epa_ewm", "sacks_ewm"]].rename(columns={
        "player_id": "qb_id_f", "pass_att_ewm": "qb_att_ewm", "ypa_ewm": "qb_ypa_ewm", "pass_epa_ewm": "qb_epa_ewm",
        "sacks_ewm": "qb_sacks_ewm"}).drop_duplicates(["qb_id_f", "game_id"])
    pg = pg.merge(qbf, on=["qb_id_f", "game_id"], how="left")
    pg["is_own_qb"] = (pg["pos_grp"] == "QB").astype(float)

    # ---- injuries: own status + teammates ruled out/doubtful (their last-known usage)
    pg = _attach_injuries(pg, fr.injuries)
    pg = _attach_team_missing_shares(pg, fr.injuries)

    pg["pos_code"] = pg["pos_grp"].map(POS_CODE).astype(float)

    # ---- targets (post-game; ghosts/upcoming handled)
    up = pg["is_upcoming"].to_numpy()
    nan = np.nan
    pg["y_pass_yds"] = np.where(up, nan, pg["pass_yds"])
    pg["y_rush_yds"] = np.where(up, nan, pg["rush_yds"])
    pg["y_rec_yds"] = np.where(up, nan, pg["rec_yds"])
    pg["y_receptions"] = np.where(up, nan, pg["rec"])
    pg["y_pass_tds"] = np.where(up, nan, pg["pass_tds"])
    pg["y_td_count"] = np.where(up, nan, pg["rush_tds"] + pg["rec_tds"])
    pg["y_anytime_td"] = np.where(up, nan, (pg["y_td_count"] >= 1).astype(float))
    all_td = pg["y_td_count"] + pg["st_tds"] + pg["def_tds"] + pg["fr_tds"]
    pg["y_anytime_td_all"] = np.where(up, nan, (all_td >= 1).astype(float))
    pg["y_pass_attempts"] = np.where(up, nan, pg["pass_att"])
    pg["y_completions"] = np.where(up, nan, pg["pass_cmp"])
    pg["y_rush_attempts"] = np.where(up, nan, pg["carries"])

    # ---- eligibility (PRE-GAME trailing usage only)
    for s, (col, thr) in ELIG.items():
        pg[f"elig_{s}"] = (pg[col] >= thr).astype(int)
    isqb = pg["pos_grp"] == "QB"
    pg["elig_anytime_td"] = np.where(isqb, pg["carries_ewm"] >= 4.0,
                                     (pg["carries_ewm"].fillna(0) + pg["tgt_ewm"].fillna(0)) >= ELIG_ATD_USAGE).astype(int)
    pg["elig_pass_yds"] = ((pg["pass_att_ewm"] >= ELIG["pass_yds"][1]) & isqb).astype(int)
    pg["elig_pass_tds"] = pg["elig_pass_yds"]

    # ---- emit: regular season of the requested seasons
    gt = pg["game_type"] if "game_type" in pg else pg["game_id"].map(sched_type)
    out = pg[(gt == "REG") & pg["season"].isin(emit)].copy()
    out = out.rename(columns={"opp": "opponent"})
    out["gameday"] = out["gameday"].astype(str)
    feat = [c for c in out.columns if c not in META_COLS and c not in Y_COLS and c not in ELIG_COLS
            and c not in _DROP_FROM_OUTPUT and not c.startswith(_PCRAW_PREFIX)]
    out = out[META_COLS + feat + Y_COLS + ELIG_COLS]
    out = out.sort_values(["season", "week", "game_id", "player_id"], kind="mergesort").reset_index(drop=True)
    return out


# raw current-game columns / join keys that must NOT be exposed as features
_DROP_FROM_OUTPUT = set(STAT_COLS) | {
    "snaps", "snap_pct", "td_count", "rz_car", "gl_car", "rz_tgt", "has_stat_row", "ord", "widx", "tg_idx",
    "tg_szn_n", "game_type", "qb_id_f", "tgt_post", "carries_post", "rec_post", "team_game_type"}
_PCRAW_PREFIX = "pcraw_"


def feature_columns(df: pd.DataFrame, include_experimental: bool = False, include_pbp_ctx: bool = False) -> list:
    """Model-input columns of a built frame (everything that is not meta / target / eligibility).

    pbp-context columns (prefix `pc_`, see PC_*) are built when the derived tables are supplied but are NOT model
    inputs unless `include_pbp_ctx=True`: the default model feature list is unchanged by their addition.

    EXPERIMENTAL_FEATURES (teammate-absence usage shares) are built into every frame but are NOT model inputs
    unless `include_experimental=True`: the default model feature list is unchanged by their addition."""
    skip = set(META_COLS) | set(Y_COLS) | set(ELIG_COLS)
    if not include_experimental:
        skip |= set(EXPERIMENTAL_FEATURES)
    return [c for c in df.columns if c not in skip and (include_pbp_ctx or not c.startswith(PC_PREFIX))]


# ------------------------------------------------------------------------------------------------ pbp context
def _attach_pbp_team(tg: pd.DataFrame, pt: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Left-join the derived team-game rates onto the team-game table as pcx_off_* / pcx_def_* (RAW, same-game
    values: they are only ever used through shift(1) trailing, never emitted). Row order is preserved."""
    if pt is None or len(pt) == 0:
        return tg
    t = pt.copy()
    t["team"] = _alias(t["team"])
    keep = ["game_id", "team"]
    ren = {}
    for m in sorted(set(PC_OWN) | set(PC_OPP_OFF)):
        if f"off_{m}" in t.columns:
            ren[f"off_{m}"] = f"pcx_off_{m}"
    for m in PC_DEF:
        if f"def_{m}" in t.columns:
            ren[f"def_{m}"] = f"pcx_def_{m}"
    t = t[keep + list(ren)].rename(columns=ren).drop_duplicates(keep)
    return tg.merge(t, on=keep, how="left")


def _pbp_team_trailing(tg: pd.DataFrame) -> dict:
    """pc_tm_* (own offense), pc_opp_def_* (what a team's DEFENSE allowed) and pc_opp_off_* (a team's own offense
    pace/tendency, to be read for the OPPONENT) -- all pre-game trailing EWMs, keyed on the team-game table."""
    out = {}
    own = [f"pcx_off_{m}" for m in PC_OWN if f"pcx_off_{m}" in tg.columns]
    dfn = [f"pcx_def_{m}" for m in PC_DEF if f"pcx_def_{m}" in tg.columns]
    opp_off = [f"pcx_off_{m}" for m in PC_OPP_OFF if f"pcx_off_{m}" in tg.columns]
    cols = sorted(set(own + dfn + opp_off))
    if not cols:
        return out
    tr = add_trailing(tg, "team", cols, stats=("ewm",))
    for c in own:
        out[f"{PC_PREFIX}tm_{c[len('pcx_off_'):]}_ewm"] = tr[f"{c}_ewm"]
    for c in dfn:
        out[f"{PC_PREFIX}opp_def_{c[len('pcx_def_'):]}_ewm"] = tr[f"{c}_ewm"]
    for c in opp_off:
        out[f"{PC_PREFIX}opp_off_{c[len('pcx_off_'):]}_ewm"] = tr[f"{c}_ewm"]
    return out


def _attach_pbp_player(pg: pd.DataFrame, pp: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Merge the derived player-game usage (as raw pcraw_* columns, dropped from the output) and add the pre-game
    trailing pc_pl_* features. `pg` must be sorted by (player_id, ord) with a unique index; order is preserved.
    Counting columns are 0 (not NaN) for a player of a covered game who had no such event; uncovered games and
    upcoming rows stay NaN."""
    if pp is None or len(pp) == 0:
        return pg
    use = [c for c in PC_PLAYER if c in pp.columns]
    p = pp[["game_id", "player_id"] + use].drop_duplicates(["game_id", "player_id"])
    p = p.rename(columns={c: f"{_PCRAW_PREFIX}{c}" for c in use})
    pg = pg.merge(p, on=["game_id", "player_id"], how="left")
    covered = pg["game_id"].isin(set(pp["game_id"])) & ~pg["is_upcoming"].astype(bool)
    for c in PC_PLAYER_COUNTS:
        col = f"{_PCRAW_PREFIX}{c}"
        if col in pg.columns:
            pg[col] = np.where(covered, pg[col].fillna(0.0), np.nan)
    raw = [f"{_PCRAW_PREFIX}{c}" for c in use]
    tr = add_trailing(pg, "player_id", raw, stats=("ewm", "l5"))
    new = {f"{PC_PREFIX}pl_{k[len(_PCRAW_PREFIX):]}": v for k, v in tr.items()}
    ay, tg_ = new.get(f"{PC_PREFIX}pl_air_yards_ewm"), new.get(f"{PC_PREFIX}pl_tgt_pbp_ewm")
    if ay is not None and tg_ is not None:
        new[f"{PC_PREFIX}pl_adot_ewm"] = ay / tg_.where(tg_ > 0)
    return pd.concat([pg, pd.DataFrame(new, index=pg.index)], axis=1)


# ------------------------------------------------------------------------------------------------ injuries
_REPORT_CODE = {"Questionable": 1.0, "Doubtful": 2.0, "Out": 3.0}
_PRACTICE_CODE = {"Full Participation in Practice": 0.0, "Limited Participation in Practice": 1.0,
                  "Did Not Participate In Practice": 2.0}


def _attach_injuries(pg: pd.DataFrame, inj: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["own_report_code", "own_practice_code", "inj_report_available", "tm_out_tgt", "tm_out_car", "tm_out_rec",
            "tm_q_tgt", "tm_q_car", "tm_out_n"]
    if inj is None or len(inj) == 0:
        for c in cols:
            pg[c] = np.nan
        return pg
    i = inj.copy()
    i["team"] = _alias(i["team"])
    i = i[i["gsis_id"].notna()]
    if "game_type" in i:
        i = i[i["game_type"] == "REG"]
    i = i.drop_duplicates(["season", "week", "gsis_id"], keep="last")
    i["report_code"] = i["report_status"].map(_REPORT_CODE).fillna(0.0)
    i["practice_code"] = i["practice_status"].map(_PRACTICE_CODE)
    own = i[["gsis_id", "season", "week", "report_code", "practice_code"]].rename(columns={
        "gsis_id": "player_id", "report_code": "own_report_code", "practice_code": "own_practice_code"})
    pg = pg.merge(own, on=["player_id", "season", "week"], how="left")
    avail = i[["team", "season", "week"]].drop_duplicates().assign(inj_report_available=1.0)
    pg = pg.merge(avail, on=["team", "season", "week"], how="left")
    pg["inj_report_available"] = pg["inj_report_available"].fillna(0.0)
    pg["own_report_code"] = np.where(pg["inj_report_available"] == 1.0, pg["own_report_code"].fillna(0.0), np.nan)

    # last-known usage of players ruled Out/Doubtful/Questionable that week
    flagged = i[i["report_code"] >= 1.0][["gsis_id", "team", "season", "week", "report_code"]].copy()
    flagged["widx"] = ((flagged["season"] - 2000) * 30 + flagged["week"]).astype(float)
    flagged = flagged.rename(columns={"gsis_id": "player_id"}).sort_values("widx")
    last = pg[~pg["is_upcoming"]][["player_id", "widx", "tgt_post", "carries_post", "rec_post"]].copy()
    last["widx"] = last["widx"].astype(float)
    last = last.sort_values("widx")
    m = pd.merge_asof(flagged, last, on="widx", by="player_id", direction="backward",
                      allow_exact_matches=False, tolerance=45)
    m = m.dropna(subset=["tgt_post"])
    m["is_out"] = (m["report_code"] >= 2.0).astype(float)          # Doubtful or Out
    m["is_q"] = (m["report_code"] == 1.0).astype(float)
    for tag, flag in (("out", "is_out"), ("q", "is_q")):
        m[f"tm_{tag}_tgt"] = m["tgt_post"] * m[flag]
        m[f"tm_{tag}_car"] = m["carries_post"] * m[flag]
    m["tm_out_rec"] = m["rec_post"] * m["is_out"]
    m["tm_out_n"] = m["is_out"]
    agg = m.groupby(["team", "season", "week"], as_index=False)[
        ["tm_out_tgt", "tm_out_car", "tm_out_rec", "tm_q_tgt", "tm_q_car", "tm_out_n"]].sum()
    pg = pg.merge(agg, on=["team", "season", "week"], how="left")
    for c in ("tm_out_tgt", "tm_out_car", "tm_out_rec", "tm_q_tgt", "tm_q_car", "tm_out_n"):
        pg[c] = np.where(pg["inj_report_available"] == 1.0, pg[c].fillna(0.0), np.nan)
    # remove the player's own contribution (he is flagged but has a row => he is playing)
    own_flag = pg["own_report_code"]
    own_out = (own_flag >= 2.0).astype(float)
    own_q = (own_flag == 1.0).astype(float)
    pg["tm_out_tgt"] = (pg["tm_out_tgt"] - own_out * pg["tgt_ewm"].fillna(0)).clip(lower=0)
    pg["tm_out_car"] = (pg["tm_out_car"] - own_out * pg["carries_ewm"].fillna(0)).clip(lower=0)
    pg["tm_out_rec"] = (pg["tm_out_rec"] - own_out * pg["rec_ewm"].fillna(0)).clip(lower=0)
    pg["tm_q_tgt"] = (pg["tm_q_tgt"] - own_q * pg["tgt_ewm"].fillna(0)).clip(lower=0)
    pg["tm_q_car"] = (pg["tm_q_car"] - own_q * pg["carries_ewm"].fillna(0)).clip(lower=0)
    pg["tm_out_n"] = (pg["tm_out_n"] - own_out).clip(lower=0)
    return pg


def _post_window_mean(df: pd.DataFrame, key: str, col: str, window: int) -> np.ndarray:
    """Mean of the entity's last `window` appearances INCLUDING the current row (NaN-aware). `df` sorted by (key, time).
    Only ever used to value an ABSENT player from his last appearance strictly before the target week."""
    g = df.groupby(key, sort=False)[col]
    arr = np.column_stack([g.shift(k).to_numpy(dtype=float) for k in range(window)])
    cnt = np.sum(~np.isnan(arr), axis=1)
    return np.where(cnt > 0, np.nansum(arr, axis=1) / np.maximum(cnt, 1), np.nan)


def _attach_team_missing_shares(pg: pd.DataFrame, inj: Optional[pd.DataFrame]) -> pd.DataFrame:
    """team_missing_{target,rush,pass_att}_share_l5 = sum over TEAMMATES listed absent (MISSING_STATUS) on the
    game-week injury report of that teammate's usage share over his last MISSING_WINDOW appearances STRICTLY BEFORE
    the target week. Share = player stat / team total of that stat in that game (skill-position rows of the frame).

    Leakage reasoning
      * WHO is absent comes only from the injury report row keyed (season, week, team, gsis_id) for the target week:
        the final game-status report, published before kickoff. Nothing outcome-derived (snaps, stat rows, whether a
        player 'did play') decides absence; a player listed Out who nonetheless has a stat row stays 'absent'.
      * HOW MUCH usage the absent player carries comes from his appearances with widx < target widx
        (merge_asof backward, allow_exact_matches=False), so a stat outcome of the target week never enters.
      * The player's own listing is removed from his own row (teammates only).
      * No injury report for that team-week (e.g. seasons with gaps) -> NaN, never 0. A listed-absent player with no
        prior appearance within MISSING_TOLERANCE_WIDX on the SAME team contributes 0 (unknown usage).
    """
    for c in TEAM_MISSING_COLS:
        pg[c] = np.nan
    if inj is None or len(inj) == 0 or "report_status" not in inj.columns:
        return pg
    i = inj[inj["gsis_id"].notna()].copy()
    i["team"] = _alias(i["team"])
    i = i.drop_duplicates(["season", "week", "gsis_id"], keep="last")
    status = i["report_status"].astype("object").map(lambda v: str(v).strip().lower() if pd.notna(v) else "")
    flagged = i[status.isin(MISSING_STATUS)][["gsis_id", "team", "season", "week"]].rename(
        columns={"gsis_id": "player_id"}).copy()
    flagged["widx"] = ((flagged["season"] - 2000) * 30 + flagged["week"]).astype(float)

    # per player-game usage shares (played rows only; upcoming placeholder rows carry NaN outcomes)
    hist = pg[~pg["is_upcoming"]][["player_id", "team", "game_id", "widx", "tgt", "carries", "pass_att"]].copy()
    hist["widx"] = hist["widx"].astype(float)
    spec = {"tgt": "target_share", "carries": "rush_share", "pass_att": "pass_att_share"}
    for raw, name in spec.items():
        tot = hist.groupby(["game_id", "team"])[raw].transform("sum")
        hist[name] = np.where(tot > 0, hist[raw] / tot.where(tot > 0), np.nan)
    hist = hist.sort_values(["player_id", "widx"], kind="mergesort").reset_index(drop=True)
    for name in spec.values():
        hist[f"{name}_l5"] = _post_window_mean(hist, "player_id", name, MISSING_WINDOW)
    hist = hist.rename(columns={"team": "team_last"})
    last = hist[["player_id", "widx", "team_last"] + [f"{n}_l5" for n in spec.values()]].sort_values("widx", kind="mergesort")

    m = pd.merge_asof(flagged.sort_values("widx", kind="mergesort"), last, on="widx", by="player_id",
                      direction="backward", allow_exact_matches=False, tolerance=MISSING_TOLERANCE_WIDX)
    m = m[m["team_last"].notna() & (m["team_last"] == m["team"])]        # usage must have been earned on THIS team
    out_cols = dict(zip((f"{n}_l5" for n in spec.values()), TEAM_MISSING_COLS))
    for src in out_cols:
        m[src] = m[src].fillna(0.0)
    agg = m.groupby(["team", "season", "week"], as_index=False)[list(out_cols)].sum()
    own = m[["player_id", "season", "week"] + list(out_cols)].rename(columns={k: f"own_{k}" for k in out_cols})
    pg = pg.drop(columns=TEAM_MISSING_COLS)
    pg = pg.merge(agg, on=["team", "season", "week"], how="left").merge(own, on=["player_id", "season", "week"],
                                                                         how="left")
    avail = pg["inj_report_available"] == 1.0
    for src, dst in out_cols.items():
        tot = pg[src].fillna(0.0) - pg[f"own_{src}"].fillna(0.0)          # teammates only
        tot = np.where(np.abs(tot) < 1e-12, 0.0, tot)
        pg[dst] = np.where(avail, tot, np.nan)
    return pg.drop(columns=list(out_cols) + [f"own_{k}" for k in out_cols])


# ------------------------------------------------------------------------------------------------ GCS wrapper
def load_frames(seasons: Iterable[int], use_pbp: bool = True, history_seasons: int = 1,
                use_pbp_ctx: bool = True) -> Frames:
    """Load the raw nflverse frames (GCS via nfl.data.read) for `seasons` plus `history_seasons` earlier ones."""
    from nfl.data.read import load, mirrored_seasons

    seasons = sorted(set(int(s) for s in seasons))
    have = mirrored_seasons("player_stats")
    load_seasons = [s for s in range(min(seasons) - history_seasons, max(seasons) + 1) if s in have]
    ps = load("player_stats", seasons=load_seasons)
    ts = load("team_stats", seasons=load_seasons)
    sched = load("schedules")
    sched = sched[sched["season"].isin(load_seasons)]
    players = load("players")
    snaps = load("snap_counts", seasons=load_seasons, strict=False)
    inj = load("injuries", seasons=load_seasons, strict=False)
    rz = None
    if use_pbp:
        parts = []
        for s in load_seasons:
            try:
                p = load("pbp", seasons=[s], columns=["game_id", "yardline_100", "rush_attempt", "pass_attempt",
                                                      "qb_kneel", "rusher_player_id", "receiver_player_id"])
                parts.append(aggregate_pbp_redzone(p))
            except Exception as e:          # pbp is optional enrichment; never block the build on it
                logger.warning("pbp %s unavailable (%s) -- red-zone features NaN for that season", s, e)
        rz = pd.concat(parts, ignore_index=True) if parts else None
    pt = pp = None
    if use_pbp_ctx:
        try:
            from nfl.data.read import derived_seasons, load_derived
            from nfl.data.derived import PLAYER_TABLE, TEAM_TABLE
            have = set(derived_seasons(TEAM_TABLE)) & set(derived_seasons(PLAYER_TABLE))
            want = [s for s in load_seasons if s in have]
            if want:
                pt = load_derived(TEAM_TABLE, seasons=want)
                pp = load_derived(PLAYER_TABLE, seasons=want)
            else:
                logger.warning("no derived pbp tables (python -m nfl.data.derived) -- pc_* features not built")
        except Exception as e:          # derived tables are optional enrichment; never block the build on them
            logger.warning("derived pbp tables unavailable (%s) -- pc_* features not built", e)
    return Frames(ps, sched, ts, snaps, inj, players, rz, pt, pp)


def build_player_week_features(seasons: Iterable[int], include_upcoming: bool = False, use_pbp: bool = True,
                               history_seasons: int = 1, use_pbp_ctx: bool = True) -> pd.DataFrame:
    """GCS-backed builder. `seasons` = seasons to EMIT; `history_seasons` earlier seasons are loaded so trailing
    features at week 1 carry over. Set include_upcoming=True to also emit placeholder rows (y_* NaN,
    is_upcoming=True) for each team's next unplayed regular-season game."""
    seasons = sorted(set(int(s) for s in seasons))
    fr = load_frames(seasons, use_pbp=use_pbp, history_seasons=history_seasons, use_pbp_ctx=use_pbp_ctx)
    return build_from_frames(fr, seasons, include_upcoming=include_upcoming)


# ------------------------------------------------------------------------------------------------ leakage check
def perturb_outcomes(fr: Frames, season: int, week: int, seed: int = 0) -> Frames:
    """Copy of `fr` with every POST-GAME quantity of games at (season, week) randomised. Pre-game inputs (lines,
    roof/temp/wind, rest, injury report, game ids, starting QB ids, team/opp/position) are left alone."""
    rng = np.random.default_rng(seed)

    def scramble(df: pd.DataFrame, cols: list, mask: pd.Series) -> pd.DataFrame:
        df = df.copy()
        for c in cols:
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                n = int(mask.sum())
                df.loc[mask, c] = rng.integers(1, 150, size=n).astype(float) if n else df.loc[mask, c]
        return df

    ps = fr.player_stats
    pcols = [c for c in ps.columns if c not in ("season", "week")
             and pd.api.types.is_numeric_dtype(ps[c])]
    ps2 = scramble(ps, pcols, (ps["season"] == season) & (ps["week"] == week))
    ts = fr.team_stats
    tcols = [c for c in ts.columns if c not in ("season", "week") and pd.api.types.is_numeric_dtype(ts[c])]
    ts2 = scramble(ts, tcols, (ts["season"] == season) & (ts["week"] == week))
    sc = fr.schedules
    m = (sc["season"] == season) & (sc["week"] == week)
    sc2 = scramble(sc, ["home_score", "away_score", "result", "total", "overtime"], m)
    sn2 = fr.snap_counts
    if sn2 is not None:
        sn2 = scramble(sn2, ["offense_snaps", "offense_pct", "defense_snaps", "st_snaps"],
                       (sn2["season"] == season) & (sn2["week"] == week))
    rz2 = fr.pbp_rz
    if rz2 is not None:
        gids = set(sc.loc[m, "game_id"])
        rz2 = scramble(rz2, ["rz_car", "gl_car", "rz_tgt"], rz2["game_id"].isin(gids))
    gids = set(sc.loc[m, "game_id"])
    pt2, pp2 = fr.pbp_team, fr.pbp_player
    if pt2 is not None:
        pt2 = scramble(pt2, [c for c in pt2.columns if c.startswith(("off_", "def_"))], pt2["game_id"].isin(gids))
    if pp2 is not None:
        pp2 = scramble(pp2, [c for c in pp2.columns if c not in ("season", "week")], pp2["game_id"].isin(gids))
    return Frames(ps2, sc2, ts2, sn2, fr.injuries, fr.players, rz2, pt2, pp2)


def leakage_check(fr: Frames, emit_seasons: Iterable[int], season: int, week: int, seed: int = 0,
                  include_upcoming: bool = False) -> dict:
    """Rebuild after scrambling (season, week)'s outcomes. Returns
         {"n_checked": rows at/before (season, week) compared, "changed_cols": features that moved there (MUST be
          empty), "n_changed_after": feature cells that moved AFTER the week (sanity: > 0 => the test has power)}."""
    base = build_from_frames(fr, emit_seasons, include_upcoming=include_upcoming)
    pert = build_from_frames(perturb_outcomes(fr, season, week, seed), emit_seasons, include_upcoming=include_upcoming)
    feats = feature_columns(base, include_experimental=True, include_pbp_ctx=True)   # leakage-check EVERYTHING
    key = ["player_id", "game_id"]
    b = base.set_index(key)
    p = pert.set_index(key)
    common = b.index.intersection(p.index)
    b, p = b.loc[common], p.loc[common]
    # The label population (who "played offense") is defined from the CURRENT game's outcomes, so rows of the
    # scrambled week itself may appear/disappear; rows of earlier weeks must not.
    early_b = base[(base["season"] < season) | ((base["season"] == season) & (base["week"] < week))]
    early_p = pert[(pert["season"] < season) | ((pert["season"] == season) & (pert["week"] < week))]
    assert len(early_b) == len(early_p), "scrambling week outcomes changed the row set of EARLIER weeks"
    before = (b["season"] < season) | ((b["season"] == season) & (b["week"] <= week))
    changed = []
    for c in feats:
        x, y = b.loc[before, c], p.loc[before, c]
        if pd.api.types.is_numeric_dtype(x):
            same = (x.to_numpy(dtype=float) == y.to_numpy(dtype=float)) | (pd.isna(x.to_numpy()) & pd.isna(y.to_numpy()))
        else:
            same = (x.astype(str) == y.astype(str)).to_numpy()
        if not same.all():
            changed.append(c)
    after = ~before
    n_after = 0
    for c in feats:
        if pd.api.types.is_numeric_dtype(b[c]):
            x, y = b.loc[after, c].to_numpy(dtype=float), p.loc[after, c].to_numpy(dtype=float)
            n_after += int((~((x == y) | (np.isnan(x) & np.isnan(y)))).sum())
    return {"n_checked": int(before.sum()), "changed_cols": changed, "n_changed_after": n_after}
