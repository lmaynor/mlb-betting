#!/usr/bin/env python3
"""RES-08 — P4 Stage 1: consensus-outlier harvesting, offline replay on the
Action Network backfill (BNK-11).

Spec: RUL-20261116-profitable-markets-paths, Q3 item 1 (offline replay stage),
as adapted to the Action Network panel's actual data shape. The AN panel is ONE
closing-state snapshot per (league, market, date) with no intraday timestamps,
so two ruled elements cannot be measured on it and are NOT approximated here:

  * quote age <= 15m / ">= 30m pre-game": no timestamps exist. The board is the
    closing state, which is the state the T-15m close would be compared
    against; entries are therefore evaluated AT the close.
  * T-15m price-ratio CLV: entry price == closing board by construction, so
    CLV against this panel's own close is identically zero. The CLV prong of
    the kill criterion is reported NOT MEASURABLE on this panel; the verdict
    rests on the ROI prong + outcome scoring. Stage 2 (live shadow) is where
    CLV becomes measurable.

What is implemented exactly as ruled:
  * leave-target-out consensus fair, sharp-weighted (sharp books 2x). The AN
    panel carries none of Pinnacle / Novig / ProphetX, so the sharpest
    available references are AN's own consensus (book 15) and opening series
    (book 30); config A weights those 2x and the real books 1x. Config B is
    the equal-weight real-books-only sensitivity. The CONTROL anchors on the
    plain market average (all quoted books incl. the target, equal weight) —
    the wave-2 comparison (+4.7% sharp-anchored vs -2.7% average-anchored)
    makes the control a first-class output.
  * de-vig per book pair: Power (method="log", the house log_two_way formula)
    when either side's raw implied prob is <= 0.35 or >= 0.65, proportional
    otherwise.
  * trigger: executable soft book in {DK 68, FD 69, BetMGM 75, Caesars 123,
    bet365 79} with pfair - p_book >= delta for delta in [0.03, 0.10] and the
    edge gap capped at <= 0.10; anchor dispersion (max-min of anchor books'
    de-vigged side probs) <= 0.06.
  * scoring: flat 1-unit stakes at the target book's price vs realized
    outcomes, game-clustered (CR1) standard errors.

Eligibility (primary): a (date, event, player, market, line) group needs >= 4
two-sided books in total and >= 3 real books. A >= 5-total-books sensitivity
is reported from the same bet frame (book counts are stored per bet).

Outcomes, all banked, every number names its panel:
  NBA  : NBA/player_boxscores_master.csv (points/rebounds/assists/threes made;
         pra/pr/pa are sums). DNP (played=False) voids the group.
  NHL  : NHL/skater_boxscores_master.csv (sog/points/assists) +
         NHL/goalie_boxscores_master.csv (saves).
  NFL  : NFL/nflverse/player_stats_*.parquet joined to schedules for game
         dates. A player with no stat row that week is treated as no-outcome
         (DNP/inactive) and excluded.
  MLB  : Statcast/statcast_master.parquet (PA-terminal rows) for batter
         hits/singles/doubles/triples/home_runs/total_bases and pitcher
         strikeouts/hits_allowed/pitching_outs/walks-allowed; the SB master
         (Scoring/sb_boxscore_master.csv)
         for stolen_bases (it also supplies the batter MLBAM->name crosswalk).
         rbis / runs_scored / earned_runs are NOT derivable from the banked
         pitch master without runner-responsibility tracking and are excluded
         by construction, stated in the memo.
  NCAAB / WNBA / NCAAF: no banked outcome source exists in the warehouse, so
  those leagues cannot be scored and are excluded, stated in the memo.

Usage:
  python scripts/p4_consensus_outlier_replay.py \
      --an-dir ~/workspace/p4-work/data/an \
      --data-dir ~/workspace/p4-work/data \
      --out-dir ~/workspace/p4-work/out
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import unicodedata

import numpy as np
import pandas as pd

REAL_BOOKS = {68, 69, 75, 79, 123}          # DK, FD, bet365, BetMGM, Caesars
PSEUDO_BOOKS = {15, 30}                      # AN consensus, AN open
ALL_BOOKS = REAL_BOOKS | PSEUDO_BOOKS
SHARP_AVAILABLE = {15, 30}                   # sharpest references present in-panel
DELTAS = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
EDGE_CAP = 0.10
DISPERSION_CAP = 0.06
MIN_BOOKS_TOTAL = 4
# >= 3 real books is this replay's operationalization (a target needs >= 2
# other real books in its config-B anchor). P4_MIN_BOOKS_REAL=2 runs the
# relaxed sensitivity reported in the memo.
MIN_BOOKS_REAL = int(os.environ.get("P4_MIN_BOOKS_REAL", "3"))

LEAGUE_MARKETS = {
    "nba": ["points", "rebounds", "assists", "threes", "pra", "pr", "pa"],
    "nhl": ["saves", "shots_on_goal", "points", "assists"],
    "nfl": ["passing_yards", "passing_tds", "pass_completions", "passing_attempts",
            "interceptions", "rushing_yards", "rushing_attempts", "receiving_yards",
            "receptions", "passing_rushing_yards", "rushing_receiving_yards",
            "field_goals_made", "extra_points_made", "kicking_points", "tackles_assists"],
    "mlb": ["hits", "singles", "doubles", "triples", "home_runs", "total_bases",
            "walks", "stolen_bases", "strikeouts", "pitching_outs", "hits_allowed"],
}


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def norm_name(name: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes for cross-panel identity."""
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", " ").replace("'", "").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def implied(odds: float) -> float:
    o = float(odds)
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def payout_profit(odds: float, won: bool, push: bool) -> float:
    if push:
        return 0.0
    if not won:
        return -1.0
    o = float(odds)
    return o / 100.0 if o > 0 else 100.0 / (-o)


def devig_pair(p_over: np.ndarray, p_under: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized house de-vig: Power (log) on skewed pairs, proportional else.

    Mirrors mlb_core.odds.utils.devig_two_way with method selection per the
    ruling: 'log' when either side's raw implied prob is <= 0.35 or >= 0.65.
    log method: fair_i proportional to p_i**k with k solved so the powered
    probs sum to 1 (bisection on k in [1, 8], as in log_two_way).
    """
    p_over = np.asarray(p_over, dtype=float)
    p_under = np.asarray(p_under, dtype=float)
    s = p_over + p_under
    fair_over = p_over / s
    fair_under = p_under / s
    skewed = (p_over <= 0.35) | (p_over >= 0.65) | (p_under <= 0.35) | (p_under >= 0.65)
    idx = np.where(skewed & (s > 1.0))[0]
    if len(idx):
        a, b = p_over[idx], p_under[idx]
        lo, hi = np.ones_like(a), np.full_like(a, 8.0)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            tot = a ** mid + b ** mid
            too_big = tot > 1.0
            lo = np.where(too_big, mid, lo)
            hi = np.where(too_big, hi, mid)
        k = (lo + hi) / 2.0
        ao, bo = a ** k, b ** k
        tot = ao + bo
        fair_over[idx] = ao / tot
        fair_under[idx] = bo / tot
    return fair_over, fair_under


def cluster_mean_stats(values: np.ndarray, clusters: np.ndarray) -> dict:
    """Mean with CR1 cluster-robust SE (clusters = games)."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "se": float("nan"),
                "lo95": float("nan"), "hi95": float("nan"), "n_clusters": 0}
    mean = float(values.mean())
    df = pd.DataFrame({"v": values, "g": clusters})
    resid = df["v"] - mean
    u = resid.groupby(df["g"]).sum().to_numpy()
    g = len(u)
    if g < 2:
        se = float("nan")
    else:
        var = (g / (g - 1.0)) * float(np.sum(u ** 2)) / (n ** 2)
        se = math.sqrt(max(var, 0.0))
    return {"n": int(n), "mean": mean, "se": se,
            "lo95": mean - 1.96 * se, "hi95": mean + 1.96 * se,
            "n_clusters": int(g)}


# --------------------------------------------------------------------------- #
# Action Network panel loading
# --------------------------------------------------------------------------- #

AN_COLS = ["date", "event_id", "player_name", "team_abbr", "home_abbr", "away_abbr",
           "start_time", "market", "is_milestone", "book_id", "side", "line",
           "odds", "line_status", "game_status"]


def load_an(an_dir: str, league: str, markets: list[str]) -> pd.DataFrame:
    frames = []
    for market in markets:
        path = os.path.join(an_dir, league, market)
        if not os.path.isdir(path):
            print(f"  [warn] missing AN dir {path}")
            continue
        files = sorted(f for f in os.listdir(path) if f.endswith(".parquet"))
        for f in files:
            d = pd.read_parquet(os.path.join(path, f), columns=AN_COLS)
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=AN_COLS + ["league", "player_norm", "utc_date"])
    an = pd.concat(frames, ignore_index=True)
    an["league"] = league
    an = an[(~an["is_milestone"]) & an["line"].notna() & an["odds"].notna() & (an["odds"] != 0)]
    an = an[an["line_status"].isin(["normal", "", None]) | an["line_status"].isna()]
    an["player_norm"] = an["player_name"].map(norm_name)
    an["utc_date"] = pd.to_datetime(an["start_time"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    an["date"] = an["date"].astype(str)
    return an


# --------------------------------------------------------------------------- #
# outcomes: NBA / NHL masters
# --------------------------------------------------------------------------- #

CANON_TEAM = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX"}


def canon_team(t) -> str:
    if not isinstance(t, str):
        return ""
    return CANON_TEAM.get(t.strip().upper(), t.strip().upper())


def _master_lookup(df: pd.DataFrame, stat_cols: dict[str, str]) -> dict:
    """(date, player_norm) -> {stat: value, team: canon} for played rows only."""
    out = {}
    df = df[df["played"] == True].copy()  # noqa: E712
    df["player_norm"] = df["name"].map(norm_name)
    for rec in df.itertuples(index=False):
        d = getattr(rec, "date")
        key = (str(d), rec.player_norm)
        row = {stat: float(getattr(rec, col)) for stat, col in stat_cols.items()}
        row["team"] = canon_team(getattr(rec, "team_abbr"))
        out[key] = row
    return out


def nba_outcomes(path: str) -> tuple[dict, set]:
    df = pd.read_csv(path, low_memory=False,
                     usecols=["date", "team_abbr", "name", "played", "points",
                              "rebounds", "assists", "three_points_made"])
    df["pra"] = df["points"] + df["rebounds"] + df["assists"]
    df["pr"] = df["points"] + df["rebounds"]
    df["pa"] = df["points"] + df["assists"]
    played = _master_lookup(df, {"points": "points", "rebounds": "rebounds",
                                 "assists": "assists", "threes": "three_points_made",
                                 "pra": "pra", "pr": "pr", "pa": "pa"})
    dnp = set()
    d = df[df["played"] != True].copy()  # noqa: E712
    d["player_norm"] = d["name"].map(norm_name)
    for rec in d.itertuples(index=False):
        dnp.add((str(rec.date), rec.player_norm))
    return played, dnp


def nhl_outcomes(skater_path: str, goalie_path: str) -> tuple[dict, set]:
    sk = pd.read_csv(skater_path, low_memory=False,
                     usecols=["date", "team_abbr", "name", "played", "sog",
                              "points", "assists"])
    go = pd.read_csv(goalie_path, low_memory=False,
                     usecols=["date", "team_abbr", "name", "played", "saves"])
    played = _master_lookup(sk, {"shots_on_goal": "sog", "points": "points",
                                 "assists": "assists"})
    played.update(_master_lookup(go, {"saves": "saves"}))
    dnp = set()
    for df in (sk, go):
        d = df[df["played"] != True].copy()  # noqa: E712
        d["player_norm"] = d["name"].map(norm_name)
        for rec in d.itertuples(index=False):
            dnp.add((str(rec.date), rec.player_norm))
    return played, dnp


def resolve_outcome(an_row_dates: list[str], player_norm: str, event_teams: set,
                    played: dict, dnp: set) -> tuple[float | None, str]:
    """Try candidate dates in order; a hit must team-match the event when the
    master's team is known and the event teams are known. Returns (row, why)
    where row is the outcome dict or None and why is a counter key."""
    hits = []
    for d in an_row_dates:
        row = played.get((d, player_norm))
        if row is not None:
            if event_teams and row.get("team") and row["team"] not in event_teams:
                continue
            hits.append((d, row))
    uniq = {id(r) for _, r in hits}
    if len(hits) >= 1 and len(uniq) == 1:
        return hits[0][1], "ok"
    if len(hits) > 1:
        return None, "ambiguous"
    for d in an_row_dates:
        if (d, player_norm) in dnp:
            return None, "dnp_void"
    return None, "unresolved"


# --------------------------------------------------------------------------- #
# outcomes: NFL (nflverse)
# --------------------------------------------------------------------------- #

NFL_STAT_MAP = {
    "passing_yards": lambda r: r.passing_yards,
    "passing_tds": lambda r: r.passing_tds,
    "pass_completions": lambda r: r.completions,
    "passing_attempts": lambda r: r.attempts,
    "interceptions": lambda r: r.passing_interceptions,
    "rushing_yards": lambda r: r.rushing_yards,
    "rushing_attempts": lambda r: r.carries,
    "receiving_yards": lambda r: r.receiving_yards,
    "receptions": lambda r: r.receptions,
    "passing_rushing_yards": lambda r: r.passing_yards + r.rushing_yards,
    "rushing_receiving_yards": lambda r: r.rushing_yards + r.receiving_yards,
    "field_goals_made": lambda r: r.fg_made,
    "extra_points_made": lambda r: r.pat_made,
    "kicking_points": lambda r: 3.0 * r.fg_made + r.pat_made,
    "tackles_assists": lambda r: r.def_tackles_solo + r.def_tackles_with_assist + r.def_tackle_assists,
}


def nfl_outcomes(stats_dir: str, sched_dir: str) -> dict:
    """(game_date, player_norm) -> {market: value}. Dates come from the
    nflverse schedules files; player rows are per player-week (one game)."""
    game_date = {}
    for f in sorted(os.listdir(sched_dir)):
        if f.endswith(".parquet"):
            s = pd.read_parquet(os.path.join(sched_dir, f), columns=["game_id", "gameday"])
            for rec in s.itertuples(index=False):
                game_date[rec.game_id] = str(rec.gameday)
    out = {}
    for f in sorted(os.listdir(stats_dir)):
        if not f.endswith(".parquet"):
            continue
        ps = pd.read_parquet(os.path.join(stats_dir, f))
        ps["player_norm"] = ps["player_display_name"].map(norm_name)
        for rec in ps.itertuples(index=False):
            d = game_date.get(rec.game_id)
            if d is None:
                continue
            row = {}
            for market, fn in NFL_STAT_MAP.items():
                try:
                    v = fn(rec)
                except AttributeError:
                    v = np.nan
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    row[market] = float(v)
            row["team"] = str(rec.team).upper()
            out[(d, rec.player_norm)] = row
    return out


# --------------------------------------------------------------------------- #
# outcomes: MLB (Statcast PA-terminal master + SB boxscore master)
# --------------------------------------------------------------------------- #

HIT_EVENTS = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
OUT_EVENTS = {"field_out": 1, "force_out": 1, "sac_fly": 1, "sac_bunt": 1,
              "fielders_choice_out": 1, "strikeout": 1, "strikeout_double_play": 2,
              "double_play": 2, "grounded_into_double_play": 2, "sac_fly_double_play": 2,
              "triple_play": 3, "double_play_2": 2}


def mlb_outcomes(statcast_path: str, sb_path: str) -> dict:
    """(game_date, player_norm) -> {market: value, team: abbr}.

    Batter lines come from PA-terminal events; pitcher outs use the
    outs_when_up delta within a half-inning with an events-map fallback for a
    game's final PA. stolen_bases comes from the SB boxscore master, which
    also supplies the batter MLBAM-id -> name crosswalk."""
    sb = pd.read_csv(sb_path, low_memory=False,
                     usecols=["game_date", "batter_mlbam_id", "batter_name",
                              "team", "stolen_bases"])
    xwalk = {}
    for rec in sb.itertuples(index=False):
        xwalk[int(rec.batter_mlbam_id)] = norm_name(str(rec.batter_name))
    out: dict = {}
    sb["player_norm"] = sb["batter_name"].map(norm_name)
    for rec in sb.itertuples(index=False):
        key = (str(rec.game_date), rec.player_norm)
        row = out.setdefault(key, {})
        row["stolen_bases"] = float(rec.stolen_bases)
        row["team"] = str(rec.team).upper()

    sc = pd.read_parquet(statcast_path)
    # AN MLB partitions banked from 2024-03-20 (season starts); do not filter
    # the pitch master by date or the March-April 2024 chunk cannot resolve.
    sc["game_date"] = sc["game_date"].astype(str)
    sc = sc.sort_values(["game_pk", "inning", "inning_topbot", "at_bat_number"],
                        kind="mergesort")
    # outs added per PA: delta of outs_when_up to the next PA in the same
    # half-inning; if the half-inning ends, 3 - outs_when_up; the game's final
    # PA falls back to the events map.
    sc["half"] = (sc["game_pk"].astype(str) + "_" + sc["inning"].astype(str)
                  + "_" + sc["inning_topbot"].astype(str))
    nxt_outs = sc.groupby("half")["outs_when_up"].shift(-1)
    nxt_half = sc["half"].shift(-1)
    same_half_next = nxt_half == sc["half"]
    outs_added = np.where(same_half_next, nxt_outs - sc["outs_when_up"], np.nan)
    ended = (~same_half_next) & (sc["half"].shift(-1).notna() | (sc["game_pk"].shift(-1) == sc["game_pk"]))
    outs_added = np.where(np.isnan(outs_added) & ended, 3.0 - sc["outs_when_up"], outs_added)
    ev_outs = sc["events"].map(OUT_EVENTS)
    outs_added = np.where(np.isnan(outs_added), ev_outs.fillna(0.0), outs_added)
    sc["outs_added"] = np.clip(outs_added, 0, 3)

    bat = sc.groupby(["game_date", "game_pk", "batter"]).agg(
        hits=("events", lambda e: e.isin(HIT_EVENTS).sum()),
        singles=("events", lambda e: (e == "single").sum()),
        doubles=("events", lambda e: (e == "double").sum()),
        triples=("events", lambda e: (e == "triple").sum()),
        home_runs=("events", lambda e: (e == "home_run").sum()),
    ).reset_index()
    tb_map = sc.assign(tb=sc["events"].map(HIT_EVENTS).fillna(0)).groupby(
        ["game_date", "game_pk", "batter"])["tb"].sum().rename("total_bases").reset_index()
    bat = bat.merge(tb_map, on=["game_date", "game_pk", "batter"], how="left")
    bat_team = sc.assign(
        bat_team=np.where(sc["inning_topbot"] == "Bot", sc["home_team"], sc["away_team"])
    ).groupby(["game_pk", "batter"])["bat_team"].first().reset_index()
    bat = bat.merge(bat_team, on=["game_pk", "batter"], how="left")

    pit = sc.groupby(["game_date", "game_pk", "pitcher", "player_name"]).agg(
        strikeouts=("events", lambda e: e.isin(["strikeout", "strikeout_double_play"]).sum()),
        hits_allowed=("events", lambda e: e.isin(HIT_EVENTS).sum()),
        pitching_outs=("outs_added", "sum"),
        # AN's "walks" market is pitcher walks allowed (its player-days are
        # pitchers, ~24/day); batter walks are not the AN market.
        walks=("events", lambda e: e.isin(["walk", "intentional_walk"]).sum()),
    ).reset_index()
    pit_team = sc.assign(
        pit_team=np.where(sc["inning_topbot"] == "Bot", sc["away_team"], sc["home_team"])
    ).groupby(["game_pk", "pitcher"])["pit_team"].first().reset_index()
    pit = pit.merge(pit_team, on=["game_pk", "pitcher"], how="left")

    games_per_day_name: dict = {}
    for rec in bat.itertuples(index=False):
        nm = xwalk.get(int(rec.batter))
        if nm is None:
            continue
        key = (rec.game_date, nm)
        games_per_day_name.setdefault(key, set()).add(rec.game_pk)
        row = out.setdefault(key, {})
        for stat in ("hits", "singles", "doubles", "triples", "home_runs", "total_bases"):
            row[stat] = float(getattr(rec, stat))
        row["team"] = str(rec.bat_team).upper()
    for rec in pit.itertuples(index=False):
        nm = norm_name(str(rec.player_name).split(", ")[-1] + " " + str(rec.player_name).split(", ")[0]
                       ) if ", " in str(rec.player_name) else norm_name(str(rec.player_name))
        key = (rec.game_date, nm)
        games_per_day_name.setdefault(key, set()).add(rec.game_pk)
        row = out.setdefault(key, {})
        for stat in ("strikeouts", "hits_allowed", "pitching_outs", "walks"):
            row[stat] = float(getattr(rec, stat))
        row["team"] = str(rec.pit_team).upper()
    # doubleheader player-days (two game_pks, indistinguishable by date+name)
    # are marked ambiguous by the caller via this set.
    ambiguous = {k for k, v in games_per_day_name.items() if len(v) > 1}
    return out, ambiguous


# --------------------------------------------------------------------------- #
# replay engine
# --------------------------------------------------------------------------- #

def build_book_frame(an: pd.DataFrame) -> pd.DataFrame:
    """One row per (group, book): both sides' odds, raw implied, de-vigged fairs.
    Group = (date, event_id, player_norm, market, line)."""
    an = an[an["book_id"].isin(ALL_BOOKS)].copy()
    an["p_raw"] = an["odds"].map(implied)
    piv = an.pivot_table(index=["date", "event_id", "player_norm", "market", "line", "book_id"],
                         columns="side", values=["odds", "p_raw"], aggfunc="first")
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv = piv.reset_index()
    need = ["odds_over", "odds_under", "p_raw_over", "p_raw_under"]
    for c in need:
        if c not in piv.columns:
            piv[c] = np.nan
    piv = piv.dropna(subset=need)
    fo, fu = devig_pair(piv["p_raw_over"].to_numpy(), piv["p_raw_under"].to_numpy())
    piv["fair_over"], piv["fair_under"] = fo, fu
    meta = an.groupby(["date", "event_id", "player_norm"]).agg(
        utc_date=("utc_date", "first"), an_date=("date", "first"),
        home=("home_abbr", "first"), away=("away_abbr", "first"),
        team_abbr=("team_abbr", "first")).reset_index()
    piv = piv.merge(meta, on=["date", "event_id", "player_norm"], how="left")
    return piv


def _anchor_stats(fair: np.ndarray, weights: np.ndarray, targets: np.ndarray) -> tuple:
    """For each target index: (weighted mean of the others, range of the others)."""
    means, ranges = [], []
    total_w = weights.sum()
    total_s = float((weights * fair).sum())
    for t in targets:
        mask = np.ones(len(fair), dtype=bool)
        mask[t] = False
        others = fair[mask]
        means.append((total_s - weights[t] * fair[t]) / (total_w - weights[t]))
        ranges.append(float(others.max() - others.min()) if len(others) else np.nan)
    return np.array(means), np.array(ranges)


def run_league(league: str, an: pd.DataFrame, outcome_fn) -> tuple[pd.DataFrame, dict]:
    """outcome_fn(date_candidates, player_norm, event_teams, market) ->
    (value or None, why). Returns (bets, coverage counters). Only candidates
    with edge >= min(DELTAS) are recorded; every delta slice is a filter of
    that set (edge <= EDGE_CAP and dispersion <= cap already applied)."""
    cov = {"groups_total": 0, "groups_eligible": 0, "groups_ineligible": 0,
           "outcome_ok": 0, "outcome_dnp_void": 0, "outcome_ambiguous": 0,
           "outcome_unresolved": 0, "outcome_no_stat": 0}
    books = build_book_frame(an)
    min_delta = min(DELTAS)
    books["gkey"] = books.groupby(["date", "event_id", "player_norm", "market", "line"],
                                  sort=False).ngroup()
    books = books.sort_values("gkey", kind="mergesort").reset_index(drop=True)
    gk = books["gkey"].to_numpy()
    bounds = np.flatnonzero(np.r_[True, np.diff(gk) > 0, True])
    bid_a = books["book_id"].to_numpy()
    fo_a = books["fair_over"].to_numpy(); fu_a = books["fair_under"].to_numpy()
    po_a = books["p_raw_over"].to_numpy(); pu_a = books["p_raw_under"].to_numpy()
    oo_a = books["odds_over"].to_numpy(); ou_a = books["odds_under"].to_numpy()
    meta = books.iloc[bounds[:-1]]
    m_date = meta["date"].to_numpy(); m_event = meta["event_id"].to_numpy()
    m_player = meta["player_norm"].to_numpy(); m_market = meta["market"].to_numpy()
    m_line = meta["line"].to_numpy(); m_utc = meta["utc_date"].to_numpy()
    m_and = meta["an_date"].to_numpy(); m_home = meta["home"].to_numpy()
    m_away = meta["away"].to_numpy()
    real_list = list(REAL_BOOKS); sharp_list = list(SHARP_AVAILABLE)
    bets = []
    outcome_cache: dict = {}
    for gi in range(len(bounds) - 1):
        s, e = bounds[gi], bounds[gi + 1]
        cov["groups_total"] += 1
        bids = bid_a[s:e]
        n_total = e - s
        is_real = np.isin(bids, real_list)
        n_real = int(is_real.sum())
        if n_total < MIN_BOOKS_TOTAL or n_real < MIN_BOOKS_REAL:
            cov["groups_ineligible"] += 1
            continue
        cov["groups_eligible"] += 1
        date, event_id = m_date[gi], m_event[gi]
        player_norm, market, line = m_player[gi], m_market[gi], float(m_line[gi])
        ck = (date, event_id, player_norm, market)
        if ck not in outcome_cache:
            teams = {canon_team(m_home[gi]), canon_team(m_away[gi])} - {""}
            outcome_cache[ck] = outcome_fn([m_utc[gi], m_and[gi]], player_norm,
                                           teams, market)
        y, why = outcome_cache[ck]
        if y is None:
            cov[f"outcome_{why}"] = cov.get(f"outcome_{why}", 0) + 1
            continue
        cov["outcome_ok"] += 1
        fo = fo_a[s:e]; fu = fu_a[s:e]; po = po_a[s:e]; pu = pu_a[s:e]
        oo = oo_a[s:e]; ou = ou_a[s:e]
        w_a = np.where(np.isin(bids, sharp_list), 2.0, 1.0)
        real_idx = np.where(is_real)[0]
        for side, fair_all, praw_all, odds_all in (
                ("over", fo, po, oo), ("under", fu, pu, ou)):
            won = (y > line) if side == "over" else (y < line)
            push = (y == line)
            # config A: LTO over all books, sharp-available weighted 2x
            m_a, r_a = _anchor_stats(fair_all, w_a, real_idx)
            # config B: LTO over real books only, equal weights
            m_b, r_b = _anchor_stats(fair_all[real_idx], np.ones(len(real_idx)),
                                     np.arange(len(real_idx)))
            # control: plain average of all books incl target
            m_c = float(fair_all.mean())
            r_c = float(fair_all.max() - fair_all.min())
            for j, t in enumerate(real_idx):
                pbook = praw_all[t]
                profit = payout_profit(odds_all[t], won, push)
                for config, pfair, disp in (("A_sharp_anchor", m_a[j], r_a[j]),
                                            ("B_real_only", m_b[j], r_b[j]),
                                            ("CTRL_market_avg", m_c, r_c)):
                    edge = pfair - pbook
                    if edge < min_delta or edge > EDGE_CAP or disp > DISPERSION_CAP:
                        continue
                    bets.append({
                        "league": league, "date": date, "event_id": event_id,
                        "player": player_norm, "market": market, "line": line,
                        "side": side, "book_id": int(bids[t]), "odds": float(odds_all[t]),
                        "config": config, "edge": float(edge), "pfair": float(pfair),
                        "pbook": float(pbook), "dispersion": float(disp),
                        "n_books_total": int(n_total), "n_books_real": int(n_real),
                        "y": float(y), "profit": float(profit),
                        "cluster": f"{league}:{event_id}",
                    })
    return pd.DataFrame(bets), cov


def summarize(bets: pd.DataFrame) -> dict:
    out = {}
    if bets.empty:
        return out
    for config, sub in bets.groupby("config"):
        out[config] = {}
        for delta in DELTAS:
            cut = sub[(sub["edge"] >= delta)]
            row = {"pooled": cluster_mean_stats(cut["profit"].to_numpy(),
                                                cut["cluster"].to_numpy())}
            row["pooled"]["dates"] = int(cut["date"].nunique())
            row["pooled"]["leagues"] = int(cut["league"].nunique())
            row["pooled"]["hit_rate"] = float((cut["profit"] > 0).mean()) if len(cut) else float("nan")
            per = {}
            for lg, lsub in cut.groupby("league"):
                st = cluster_mean_stats(lsub["profit"].to_numpy(), lsub["cluster"].to_numpy())
                st["dates"] = int(lsub["date"].nunique())
                st["hit_rate"] = float((lsub["profit"] > 0).mean()) if len(lsub) else float("nan")
                per[lg] = st
            row["per_league"] = per
            out[config][f"delta_{delta:.2f}"] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--an-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--leagues", default="nba,nhl,nfl,mlb")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    d = args.data_dir
    leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]

    outcome_fns = {}
    coverage = {}

    if "nba" in leagues:
        played, dnp = nba_outcomes(os.path.join(d, "player_boxscores_master.csv"))

        def nba_fn(dates, player, teams, market, _p=played, _d=dnp):
            row, why = resolve_outcome(dates, player, teams, _p, _d)
            if row is None:
                return None, why
            return (row.get(market), "ok") if market in row else (None, "no_stat")
        outcome_fns["nba"] = nba_fn

    if "nhl" in leagues:
        played, dnp = nhl_outcomes(os.path.join(d, "skater_boxscores_master.csv"),
                                   os.path.join(d, "goalie_boxscores_master.csv"))

        def nhl_fn(dates, player, teams, market, _p=played, _d=dnp):
            row, why = resolve_outcome(dates, player, teams, _p, _d)
            if row is None:
                return None, why
            return (row.get(market), "ok") if market in row else (None, "no_stat")
        outcome_fns["nhl"] = nhl_fn

    if "nfl" in leagues:
        nfl = nfl_outcomes(os.path.join(d, "nfl_player_stats"),
                           os.path.join(d, "nfl_schedules"))

        def nfl_fn(dates, player, teams, market, _n=nfl):
            row = _n.get((dates[1], player))  # AN date == game date for NFL
            if row is None:
                return None, "unresolved"
            return (row.get(market), "ok") if market in row else (None, "no_stat")
        outcome_fns["nfl"] = nfl_fn

    if "mlb" in leagues:
        mlb, mlb_amb = mlb_outcomes(os.path.join(d, "statcast_master.parquet"),
                                    os.path.join(d, "sb_boxscore_master.csv"))

        def mlb_fn(dates, player, teams, market, _m=mlb, _a=mlb_amb):
            key = (dates[1], player)  # AN date == game_date for MLB
            if key in _a:
                return None, "ambiguous"
            row = _m.get(key)
            if row is None:
                return None, "unresolved"
            if market not in row:
                return None, "no_stat"
            return row[market], "ok"
        outcome_fns["mlb"] = mlb_fn

    all_bets = []
    for league in leagues:
        print(f"== {league}: loading AN panel", flush=True)
        an = load_an(args.an_dir, league, LEAGUE_MARKETS[league])
        print(f"   rows after filters: {len(an):,}", flush=True)
        bets, cov = run_league(league, an, outcome_fns[league])
        coverage[league] = cov
        print(f"   coverage: {cov}", flush=True)
        print(f"   settled trigger rows (edge in [0.03, cap], all configs): {len(bets):,}",
              flush=True)
        if len(bets):
            all_bets.append(bets)
            bets.to_parquet(os.path.join(args.out_dir, f"bets_{league}.parquet"),
                            index=False)
    bets = pd.concat(all_bets, ignore_index=True) if all_bets else pd.DataFrame()
    bets.to_parquet(os.path.join(args.out_dir, "bets.parquet"), index=False)
    results = {"coverage": coverage, "summary": summarize(bets)}
    with open(os.path.join(args.out_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(json.dumps(results["summary"], indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
