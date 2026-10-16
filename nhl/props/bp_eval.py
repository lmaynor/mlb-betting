"""Model-vs-market evaluation on HISTORICAL BettingPros NHL lines for goalie saves, goals, assists and points.

Reuses (not copies): mlb_core.odds.bettingpros_loader.consensus_from_long (reference books only, two-sided, de-vigged
median, modal line), nhl.props.history.resolve_players (FAIL-CLOSED name -> NHL id inside the event's two teams) and
mlb_core.odds.model_vs_market.score_model_vs_market (Brier / log-loss for model vs market + the blend regression).

SELECTION SAFETY (inherited from the loader): one snapshot per (market, day); BettingPros keeps the last quoted state of a
settled day, so a consensus row is CLOSE-ISH, not an opening line and not a time series. `pregame == False` rows are dropped.
There is NO true closing-line / CLV series in this feed, so nothing here can reach PROMOTE_CANDIDATE: the verdicts are
"model adds information beyond the market or not" (blend coefficient c) and "do model-disagreement bets pay".

Team codes: BettingPros writes LA / NJ / SJ / TB (+ 'FA' for no team) where the NHL API writes LAK / NJD / SJS / TBL; Arizona
(ARI) became Utah (UTA) in 2024 -- the two never coexist, so both sides are mapped to UTA. `canon()` is applied to the BP
side, the games table and the name crosswalk; a code left unmapped shows up as `no_game` in the coverage record.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from mlb_core.odds import model_vs_market as MVM
from mlb_core.odds.bettingpros_loader import consensus_from_long
from mlb_core.odds.utils import american_to_decimal
from nhl.props import history
from nhl.props import market as MK
from nhl.props.negbin import p_over_vec

BP_TO_NHL = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "ARI": "UTA", "Utah Mammoth": "UTA",
             "Utah Hockey Club": "UTA"}
SLUG = {"saves": "saves", "goals": "goals", "points": "points", "assists": "assists", "sog": "shots"}


def canon(code):
    return BP_TO_NHL.get(code, code)


def load_bp_long(bp_dir: str, slug: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Local BettingPros partitions `{bp_dir}/{slug}/{YYYY-MM-DD}.parquet` -> one long frame (empty days skipped)."""
    frames = []
    for f in sorted(glob.glob(os.path.join(bp_dir, slug, "*.parquet"))):
        day = os.path.basename(f)[:10]
        if (start and day < start) or (end and day > end):
            continue
        d = pd.read_parquet(f)
        if len(d):
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_consensus(long_df: pd.DataFrame, stat: str, min_books: int = 3, pregame_only: bool = False) -> pd.DataFrame:
    return consensus_from_long(long_df, min_books=min_books, stat_map={SLUG[stat]: stat}, pregame_only=pregame_only)


def name_table(masters: dict) -> pd.DataFrame:
    """player_id / full_name / team over EVERY player who ever dressed (skaters + goalies), from the masters' own pbp names --
    historical players included (NHL/players_master.csv only holds the current rosters)."""
    parts = []
    for k in ("skaters", "goalies"):
        d = masters[k]
        parts.append(pd.DataFrame({"player_id": d["player_id"], "full_name": d["name"], "team": d["team_abbr"].map(canon)}))
    t = pd.concat(parts, ignore_index=True).dropna()
    return t.drop_duplicates().reset_index(drop=True)


def games_table(games: pd.DataFrame) -> pd.DataFrame:
    g = games[games["game_type"].isin([2, 3])][["game_id", "date", "home_abbr", "away_abbr"]].copy()
    g["date"] = g["date"].astype(str)
    g["home_abbr"], g["away_abbr"] = g["home_abbr"].map(canon), g["away_abbr"].map(canon)
    return g


PREGAME_SLACK_MIN = 10.0


def apply_slack(cons: pd.DataFrame, slack_min: float = PREGAME_SLACK_MIN) -> pd.DataFrame:
    """Re-label `pregame` with a slack window. The loader's strict `updated_max <= scheduled` throws away the books' LAST
    quotes: books re-post / suspend within seconds of the start, so ~46% of saves rows carry an update stamp 0-5 min AFTER
    the scheduled start (95th pct +5 min, 0.2% beyond +10 min), and in 2025-26 nearly every row does. Those ARE the closing
    lines (their over-rate matches their price); only updates far past the start are in-play lines. Both stamps are UTC
    (checked: the stamp gap is centred on -1 min)."""
    sched = pd.to_datetime(cons["scheduled"], utc=True, errors="coerce", format="mixed")
    upd = pd.to_datetime(cons["updated_max"], utc=True, errors="coerce", format="mixed")
    late = upd.notna() & sched.notna() & (((upd - sched).dt.total_seconds() / 60.0) > slack_min)
    return cons.assign(pregame=pd.array(np.where(late, False, True), dtype="boolean"))


def join_market(cons: pd.DataFrame, names: pd.DataFrame, games: pd.DataFrame, outcome: pd.DataFrame,
                actual_col: str, min_books: int = 3, slack_min: float | None = PREGAME_SLACK_MIN) -> tuple[pd.DataFrame, dict]:
    """consensus rows -> (props frame, coverage). `outcome` has (game_id, player_id, actual_col) for players who APPEARED.
    Every consensus row is either scored or counted under exactly one drop reason (coverage balances, MVM.coverage).
    `slack_min` re-labels `pregame` via apply_slack (None keeps the loader's strict flag)."""
    if slack_min is not None and len(cons):
        cons = apply_slack(cons, slack_min)
    c, n_in, dropped = MVM.screen_consensus(cons, {s: s for s in cons["stat"].unique()}, min_books)
    dropped = dict(dropped, int_line=0, no_game=0, unresolved=0, ambiguous=0, void=0, duplicate=0)
    if len(c) == 0:
        return pd.DataFrame(), MVM.coverage(n_in, 0, dropped)
    line = c["line"].astype(float)
    intl = (line % 1.0) == 0.0
    dropped["int_line"] = int(intl.sum())
    c = c[~intl].reset_index(drop=True)
    pairs = pd.DataFrame({
        "event_id": c["event_id"].to_numpy(), "game_date": c["game_date"].astype(str).to_numpy(),
        "home_abbrev": c["home"].map(canon).to_numpy(), "away_abbrev": c["away"].map(canon).to_numpy(),
        "player_name": c["player"].to_numpy(), "player_norm": c["player_norm"].to_numpy(),
        "line": c["line"].astype(float).to_numpy(), "cons_fair": c["p_over_cons"].astype(float).to_numpy(),
        "cons_n": c["n_books"].to_numpy(), "cons_disp": c["disp"].astype(float).to_numpy(),
        "best_over": c["best_over_odds"].to_numpy(), "best_under": c["best_under_odds"].to_numpy(),
        "novig_over": c["novig_over_odds"].to_numpy(), "novig_under": c["novig_under_odds"].to_numpy(),
        "stat": c["stat"].to_numpy()})
    pairs = history.resolve_players(pairs, names)
    how = pairs["resolve_how"].value_counts().to_dict()
    g = games.rename(columns={"date": "game_date", "home_abbr": "home_abbrev", "away_abbr": "away_abbrev"})
    # a same-day same-teams pair would double every prop on the merge; keep the first game (none exist in the NHL era used)
    g = g.drop_duplicates(["game_date", "home_abbrev", "away_abbrev"], keep="first")
    pairs = pairs.merge(g[["game_id", "game_date", "home_abbrev", "away_abbrev"]],
                        on=["game_date", "home_abbrev", "away_abbrev"], how="left")
    o = outcome[["game_id", "player_id", actual_col]].rename(columns={actual_col: "actual"})
    o = o.assign(player_id=o["player_id"].astype("Int64"))
    pairs = pairs.merge(o, on=["game_id", "player_id"], how="left")
    reason = np.full(len(pairs), "", dtype=object)
    no_game = pairs["game_id"].isna().to_numpy()
    unres = pairs["player_id"].isna().to_numpy() & ~no_game
    amb = unres & (pairs["resolve_how"] == "ambiguous").to_numpy()
    void = pairs["actual"].isna().to_numpy() & ~no_game & ~unres
    reason[no_game] = "no_game"
    reason[unres] = "unresolved"
    reason[amb] = "ambiguous"
    reason[void] = "void"
    live = reason == ""
    dup = live & pairs.duplicated(["game_id", "player_id", "stat"], keep=False).to_numpy()
    reason[dup] = "duplicate"
    for k in ("no_game", "unresolved", "ambiguous", "void", "duplicate"):
        dropped[k] = int((reason == k).sum())
    props = pairs[reason == ""].reset_index(drop=True)
    props["won_over"] = (props["actual"] > props["line"]).astype(float)
    props["pkey"] = "id" + props["player_id"].astype(str)
    return props, MVM.coverage(n_in, len(props), dropped, resolve_how=how)


def attach_model(props: pd.DataFrame, pred: pd.DataFrame) -> pd.DataFrame:
    """Add p_model (P(over) at the market's own line from the walk-forward mu/alpha). Props with no prediction are dropped."""
    p = props.merge(pred[["game_id", "player_id", "mu", "alpha"]].assign(player_id=lambda d: d["player_id"].astype("Int64")),
                    on=["game_id", "player_id"], how="inner")
    p["p_model"] = p_over_vec(p["line"].to_numpy(), p["mu"].to_numpy(), p["alpha"].to_numpy())
    return p


def disagreement_roi(p: pd.DataFrame, gap: float, price: str = "best") -> dict:
    """Flat 1u bets on the side where the model disagrees with the consensus by >= `gap` (probability points), at the best
    available book price (`best`, optimistic: includes any stale outlier) or Novig's price (`novig`, only where quoted).
    Reports ROI + game-clustered t, and the NO-SKILL reference on the same props (bet BOTH sides at the same prices: what
    paying the market's hold costs without any selection) so a negative ROI is read against the right zero."""
    po, pu = (p["best_over"], p["best_under"]) if price == "best" else (p["novig_over"], p["novig_under"])
    ok = po.notna() & pu.notna() & p["p_model"].notna()
    d = p[ok].copy()
    d["po"], d["pu"] = po[ok], pu[ok]
    diff = d["p_model"] - d["cons_fair"]
    side_over = diff >= gap
    side_under = diff <= -gap
    sel = d[side_over | side_under].copy()
    sel["is_over"] = (sel["p_model"] - sel["cons_fair"]) > 0
    dec = np.where(sel["is_over"], sel["po"].map(american_to_decimal), sel["pu"].map(american_to_decimal))
    win = np.where(sel["is_over"], sel["won_over"] == 1.0, sel["won_over"] == 0.0)
    ret = pd.Series(np.where(win, dec - 1.0, -1.0), index=sel.index)
    st = MK.roi_stats(ret, sel["game_id"])
    # no-skill: both sides of every prop in `sel`
    r_o = np.where(sel["won_over"] == 1.0, sel["po"].map(american_to_decimal) - 1.0, -1.0)
    r_u = np.where(sel["won_over"] == 0.0, sel["pu"].map(american_to_decimal) - 1.0, -1.0)
    ns = MK.roi_stats(pd.Series((r_o + r_u) / 2.0, index=sel.index), sel["game_id"])
    # paired: model-side return minus the both-sides average on the same props
    paired = MK.roi_stats(ret - pd.Series((r_o + r_u) / 2.0, index=sel.index), sel["game_id"])
    return {"gap": gap, "price": price, "n": st["n"], "roi": st["roi"], "t": st["t"], "win": st["win"],
            "no_skill_roi": ns["roi"], "vs_no_skill": paired["roi"], "vs_no_skill_t": paired["t"],
            "n_games": st["n_clusters"]}


def blind_side_roi(p: pd.DataFrame, side: str) -> dict:
    """Flat 1u on EVERY prop's `side` at the best book price (the market-wide sanity check: a market whose realized over-rate
    sits below its de-vigged price shows up here before any model is involved)."""
    price = p["best_under"] if side == "under" else p["best_over"]
    ok = price.notna()
    d = p[ok]
    dec = price[ok].map(american_to_decimal)
    win = (d["won_over"] == 0.0) if side == "under" else (d["won_over"] == 1.0)
    ret = pd.Series(np.where(win, dec - 1.0, -1.0), index=d.index)
    st = MK.roi_stats(ret, d["game_id"])
    return {"n": st["n"], "roi": st["roi"], "t": st["t"]}


def score(p: pd.DataFrame, label: str = "") -> dict:
    """Brier / log-loss of model vs market + blend regression, plus disagreement-ROI ladders and market calibration."""
    if len(p) == 0:
        return {"label": label, "n": 0}
    s = MVM.score_model_vs_market(p["won_over"], p["p_model"], p["cons_fair"], p["game_id"])
    out = {"label": label, "scores": s, "roi": []}
    for gap in (0.03, 0.05, 0.08):
        for price in ("best", "novig"):
            r = disagreement_roi(p, gap, price)
            if r["n"] >= 30:
                out["roi"].append(r)
    rel_m = MK.reliability(p["won_over"], p["cons_fair"])
    rel_p = MK.reliability(p["won_over"], p["p_model"])
    out["reliability_market"] = rel_m.assign(bin=rel_m["bin"].astype(str)).to_dict("records")
    out["reliability_model"] = rel_p.assign(bin=rel_p["bin"].astype(str)).to_dict("records")
    return out


def score_by_season(p: pd.DataFrame) -> list:
    """The numbers that matter, per NHL season (a regime change -- e.g. league-wide shot volume falling ~12% in three seasons
    -- can make a model look great in one season and worse than the market in the next; a pooled figure hides that).
    Per slice: n, realized over-rate, mean market / model P(over), log-loss of each, blend c (+t), and the 5pp best-price ROI
    of betting model disagreement vs the both-sides no-skill reference."""
    if len(p) == 0:
        return []
    d = p.copy()
    dt = pd.to_datetime(d["game_date"])
    d["season"] = np.where(dt.dt.month >= 9, dt.dt.year, dt.dt.year - 1)
    out = []
    for lab, g in [("ALL", d)] + [(int(k), x) for k, x in d.groupby("season")]:
        if len(g) < 60:
            continue
        sc = MVM.score_model_vs_market(g["won_over"], g["p_model"], g["cons_fair"], g["game_id"])
        b = sc.get("blend") or {}
        r = disagreement_roi(g, 0.05, "best")
        bu = blind_side_roi(g, "under")
        bo = blind_side_roi(g, "over")
        out.append({"blind_under_roi": bu["roi"], "blind_under_t": bu["t"], "blind_over_roi": bo["roi"], "season": lab, "n": sc["n"], "over_rate": sc["over_rate"], "mean_p_market": sc["mean_p_market"],
                    "mean_p_model": sc["mean_p_model"], "logloss_model": sc["logloss_model"],
                    "logloss_market": sc["logloss_market"], "d_logloss": sc["d_logloss"], "blend_c": b.get("c"),
                    "blend_t": b.get("t_c"), "roi5": r["roi"], "roi5_n": r["n"], "roi5_t": r["t"],
                    "roi5_vs_no_skill": r["vs_no_skill"], "roi5_vs_no_skill_t": r["vs_no_skill_t"]})
    return out
