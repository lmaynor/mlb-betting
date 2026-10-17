"""Validation study runner for NHL SOG props on the 63-day historical quote DB. Prints markdown tables.

    PYTHONPATH=. python -m nhl.props.study --db /path/to/nhl_data.db [--json out.json]

Strategy set, thresholds, haircut and verdict rule are PRE-REGISTERED in docs/analysis/nhl_sog_validation_2026-10-14.md
(section 1, committed before outcomes were joined). Anything not in that list that this script prints is labelled
EXPLORATORY and must not be used to upgrade a verdict.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from mlb_core.odds.utils import american_to_decimal
from nhl.props import gamelog, history, market, train
from nhl.props.features import build_features
from nhl.props.negbin import p_over_vec

CUTOFFS = ["2026-01-01", "2026-02-01", "2026-03-01"]     # expanding monthly retrain: month m is scored by a model fit on date < m
SHARP = ("betonlineag", "draftkings", "fanduel")
SOFT = ("betmgm", "williamhill_us")
K_TESTS = 14
T_BAR = 2.7              # Bonferroni alpha .05 two-sided for K=14 (normal approx)
MIN_PRIOR_MODEL = 5


# ------------------------------------------------------------------------------------------------- dataset
def build_dataset(db: str, extra_names: pd.DataFrame | None = None):
    q = history.load_quotes(db)
    acct = {"quote_rows": len(q), "quotes_after_start_dropped": int((q["mins_to_start"] < 0).sum())}
    pairs = history.pair_quotes(q)
    acct["pairs"] = len(pairs)
    names = history.name_table(db, extra_names)
    pairs = history.resolve_players(pairs, names)
    acct["resolve_how"] = pairs["resolve_how"].value_counts().to_dict()
    gl = gamelog.from_history_db(db)
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    games = pd.read_sql("select game_id, date, home_team, away_team from games", con)
    con.close()
    pairs = history.attach_outcomes(pairs, games, gl)
    acct["status"] = pairs["status"].value_counts().to_dict()
    pairs["pkey"] = np.where(pairs["player_id"].notna(), "id" + pairs["player_id"].astype(str), "n:" + pairs["player_norm"])
    return pairs, gl, acct


def add_model(pairs: pd.DataFrame, gl: pd.DataFrame, cutoffs=None):
    """Expanding monthly retrain; each pair is scored by the model fit strictly before its month. Adds mu, nb_alpha, p_model_over.
    `cutoffs` (default None = the pre-registered CUTOFFS, behaviour unchanged) lets the multi-season BettingPros study
    pass its own month starts; a cutoff with too little history to fit is then skipped (its month stays unscored)."""
    feat = build_features(gl)
    if cutoffs is None:
        arts = {c: train.fit(feat, c) for c in CUTOFFS}
    else:
        arts = {}
        for c in cutoffs:
            try:
                arts[c] = train.fit(feat, c)
            except RuntimeError:                       # fewer than 500 pre-cutoff training rows: leave the month unscored
                continue
    fkey = feat.set_index(["game_id", "player_id"])
    p = pairs.copy()
    p["month_cut"] = p["game_date"].str[:7] + "-01"
    p["mu"] = np.nan
    p["nb_alpha"] = np.nan
    p["n_prior"] = np.nan
    ok = p["status"] == "ok"
    for cut, art in arts.items():
        m = ok & (p["month_cut"] == cut)
        if not m.any():
            continue
        sub = p.loc[m, ["game_id", "player_id"]].drop_duplicates()
        f = fkey.reindex(pd.MultiIndex.from_frame(sub.astype({"game_id": "int64", "player_id": "int64"}))).reset_index()
        f["mu"] = train.predict_mu(art, f)
        mm = p.loc[m, ["game_id", "player_id"]].astype({"game_id": "int64", "player_id": "int64"}).merge(
            f[["game_id", "player_id", "mu", "n_prior"]], on=["game_id", "player_id"], how="left")
        p.loc[m, "mu"] = mm["mu"].to_numpy()
        p.loc[m, "n_prior"] = mm["n_prior"].to_numpy()
        p.loc[m, "nb_alpha"] = art["meta"]["nb_alpha"]
    p["p_model_over"] = np.where((p["n_prior"] >= MIN_PRIOR_MODEL) & p["mu"].notna(),
                                 p_over_vec(p["line"].to_numpy(), p["mu"].fillna(1.0).to_numpy(),
                                            p["nb_alpha"].fillna(0.1).to_numpy()), np.nan)
    return p, arts, feat


def prop_table(p: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, player, line): consensus over ALL books, best / median price per side, model p, outcome."""
    g = p.groupby(market.PROP, sort=False)
    rows = []
    for k, d in g:
        dec_o, dec_u = d["over"].map(american_to_decimal), d["under"].map(american_to_decimal)
        rows.append({
            "event_id": k[0], "pkey": k[1], "line": k[2], "game_id": d["game_id"].iloc[0], "game_date": d["game_date"].iloc[0],
            "status": d["status"].iloc[0], "won_over": d["won_over"].iloc[0], "push": bool(d["push"].iloc[0]),
            "cons_fair": float(np.median(d["fair_over"])), "cons_n": len(d),
            "p_model": d["p_model_over"].iloc[0] if "p_model_over" in d else np.nan,
            "dec_over_best": dec_o.max(), "dec_under_best": dec_u.max(),
            "dec_over_med": dec_o.median(), "dec_under_med": dec_u.median(),
            "sharp_fair": (float(np.median(d.loc[d["bookmaker"].isin(SHARP), "fair_over"]))
                           if d["bookmaker"].isin(SHARP).sum() >= 2 else np.nan),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- analyses
def fmt(r: dict) -> str:
    return (f"n={r['n']} roi={r['roi'] * 100:+.2f}% se={r['se'] * 100:.2f} t={r['t']:+.2f} win={r['win'] * 100:.1f}% "
            f"clusters={r['n_clusters']}") if r["n"] > 1 else f"n={r['n']}"


def settled(df):
    return df[(df["status"] == "ok") & (~df["push"]) & df["won_over"].notna()]


def m1_calibration(p: pd.DataFrame, props: pd.DataFrame) -> dict:
    s = settled(p)
    out = {"per_book": {}, "line_base_rate": {}}
    for b, d in s.groupby("bookmaker"):
        out["per_book"][b] = {"n": len(d), "logloss": market.logloss(d["won_over"], d["fair_over"]),
                              "brier": market.brier(d["won_over"], d["fair_over"]),
                              "mean_overround": float(d["overround"].mean()),
                              "p_over_mean": float(d["fair_over"].mean()), "over_rate": float(d["won_over"].mean())}
    ps = settled(props)
    ps = ps[ps["cons_n"] >= 3]
    out["consensus3"] = {"n": len(ps), "logloss": market.logloss(ps["won_over"], ps["cons_fair"]),
                         "brier": market.brier(ps["won_over"], ps["cons_fair"])}
    rate = ps.groupby("line")["won_over"].transform("mean")
    out["consensus3"]["logloss_line_base_rate_insample"] = market.logloss(ps["won_over"], rate)
    out["reliability_consensus3"] = market.reliability(ps["won_over"], ps["cons_fair"]).to_dict("records")
    pin = s[s["bookmaker"] == "pinnacle"]
    oth = props.merge(pin[["event_id", "pkey", "line", "fair_over"]].rename(columns={"fair_over": "pin_fair"}),
                      on=market.PROP, how="inner")
    oth = settled(oth)
    out["pinnacle_vs_cons"] = {"n": len(oth), "pin_logloss": market.logloss(oth["won_over"], oth["pin_fair"]),
                               "cons_logloss": market.logloss(oth["won_over"], oth["cons_fair"])} if len(oth) else {"n": 0}
    for ln, d in ps.groupby("line"):
        out["line_base_rate"][float(ln)] = {"n": len(d), "over_rate": float(d["won_over"].mean()),
                                            "mean_cons": float(d["cons_fair"].mean())}
    return out


def sink_a_section(p: pd.DataFrame) -> dict:
    out = {}
    pc = market.add_consensus(p)
    pg = pc[pc["status"].isin(["ok", "void"])]
    base_all = pd.concat([market.side_returns(pc, "over"), market.side_returns(pc, "under")])
    out["baseline_all_quotes"] = {"over": market.roi_stats(market.side_returns(pc, "over"), pc["game_id"]),
                                  "under": market.roi_stats(market.side_returns(pc, "under"), pc["game_id"])}
    variants = {"A1": dict(min_ev=0.03, max_ev=0.15), "A2": dict(min_ev=0.05, max_ev=0.15), "A3": dict(min_ev=0.03, max_ev=None),
                # EXPLORATORY (not pre-registered; counted in the variant tally, cannot upgrade the verdict)
                "X1_others2": dict(min_others=2, min_ev=0.03, max_ev=0.15), "X2_ev2": dict(min_ev=0.02, max_ev=0.15)}
    out["bets"] = {}
    for name, kw in variants.items():
        b = market.sink_a_bets(pg, **kw)
        b = b[b["status"] == "ok"]
        out[name] = market.roi_stats(b["ret"], b["game_id"])
        out[name]["ev_mean_claimed"] = float(b["ev"].mean()) if len(b) else np.nan
        # per-prop dedup: keep only the best-priced flagged quote per (event, player, line, side)
        if len(b):
            d = b.sort_values("ev", ascending=False).drop_duplicates(["event_id", "pkey", "line", "side"])
            out[name]["per_prop"] = market.roi_stats(d["ret"], d["game_id"])
        out["bets"][name] = b
    b1 = out["bets"]["A1"]
    out["A1_by_book"] = {bk: market.roi_stats(d["ret"], d["game_id"]) for bk, d in b1.groupby("bookmaker")}
    out["A1_by_side"] = {sd: market.roi_stats(d["ret"], d["game_id"]) for sd, d in b1.groupby("side")}
    if len(b1):
        b1 = b1.assign(bucket=pd.cut(b1["ev"], [0.03, 0.05, 0.08, 0.11, 0.15], include_lowest=True))
        out["A1_ladder"] = {str(k): market.roi_stats(d["ret"], d["game_id"]) for k, d in b1.groupby("bucket", observed=True)}
    # A4: Pinnacle's own de-vigged fair as the reference (non-Pinnacle quotes), 23 days only
    pin = p[p["bookmaker"] == "pinnacle"][["event_id", "pkey", "line", "fair_over"]].rename(columns={"fair_over": "pin_fair"})
    d = p[p["bookmaker"] != "pinnacle"].merge(pin, on=market.PROP, how="inner")
    rows = []
    for side in ("over", "under"):
        x = d.copy()
        x["side"] = side
        x["p_ref"] = x["pin_fair"] if side == "over" else 1 - x["pin_fair"]
        x["ev"] = x["p_ref"] * x[side].map(american_to_decimal) - 1
        x = x[(x["ev"] >= 0.03) & (x["ev"] <= 0.15) & (x["status"] == "ok")]
        x["ret"] = market.side_returns(x, side)
        rows.append(x)
    a4 = pd.concat(rows)
    out["A4"] = market.roi_stats(a4["ret"], a4["game_id"])
    out["A4_all_quotes_vs_pinnacle_n"] = int(len(d))
    return out


def model_sections(props: pd.DataFrame, p: pd.DataFrame) -> dict:
    out = {}
    s = settled(props)
    s = s[(s["cons_n"] >= 3) & s["p_model"].notna()]
    out["c1"] = {"n": len(s), "model_logloss": market.logloss(s["won_over"], s["p_model"]),
                 "cons_logloss": market.logloss(s["won_over"], s["cons_fair"]),
                 "model_brier": market.brier(s["won_over"], s["p_model"]), "cons_brier": market.brier(s["won_over"], s["cons_fair"]),
                 "mean_p_model": float(s["p_model"].mean()), "mean_p_cons": float(s["cons_fair"].mean()),
                 "over_rate": float(s["won_over"].mean())}
    out["c1"]["reliability_model"] = market.reliability(s["won_over"], s["p_model"]).to_dict("records")
    out["c2_blend_ll"] = {f"w={w}": market.logloss(s["won_over"], w * s["p_model"] + (1 - w) * s["cons_fair"])
                          for w in (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)}
    out["c2_reg"] = market.blend_regression(s["won_over"], s["cons_fair"], s["p_model"], s["game_id"])
    # B strategies
    def b_strategy(thr, price):
        d = s.copy()
        diff = d["p_model"] - d["cons_fair"]
        over = diff >= thr
        under = diff <= -thr
        r = pd.Series(np.nan, index=d.index)
        dec_o = d["dec_over_best"] if price == "best" else d["dec_over_med"]
        dec_u = d["dec_under_best"] if price == "best" else d["dec_under_med"]
        r[over] = np.where(d.loc[over, "won_over"] == 1, dec_o[over] - 1, -1)
        r[under] = np.where(d.loc[under, "won_over"] == 0, dec_u[under] - 1, -1)
        ro = pd.Series(np.where(d["won_over"] == 1, dec_o - 1, -1.0), index=d.index)
        ru = pd.Series(np.where(d["won_over"] == 0, dec_u - 1, -1.0), index=d.index)
        d = d.assign(ret=r, edge=diff.abs(), side=np.where(over, "over", "under"), ret_avg_sides=(ro + ru) / 2)
        return d[over | under]
    out["B"] = {}
    for name, thr, price in (("B1", .05, "best"), ("B2", .03, "best"), ("B3", .08, "best"),
                             ("B4", .05, "med"), ("B5", .03, "med"), ("B6", .08, "med")):
        b = b_strategy(thr, price)
        out["B"][name] = market.roi_stats(b["ret"], b["game_id"])
        out["B"][name]["over_share"] = float((b["side"] == "over").mean()) if len(b) else np.nan
        # paired side-selection skill: chosen side's return minus the same prop's average-of-both-sides return
        out["B"][name]["paired_vs_noskill"] = market.roi_stats(b["ret"] - b["ret_avg_sides"], b["game_id"])
    bl = b_strategy(0.02, "best")
    bl = bl.assign(bucket=pd.cut(bl["edge"], [0.02, 0.04, 0.06, 0.10, 1.0]))
    # no-skill reference on the SAME props: bet over / under / both at the same prices, no model involved
    ref = {}
    for price in ("best", "med"):
        do, du = (s["dec_over_" + price], s["dec_under_" + price])
        ro = pd.Series(np.where(s["won_over"] == 1, do - 1, -1.0), index=s.index)
        ru = pd.Series(np.where(s["won_over"] == 0, du - 1, -1.0), index=s.index)
        ref[price] = {"over": market.roi_stats(ro, s["game_id"]), "under": market.roi_stats(ru, s["game_id"]),
                      "both": market.roi_stats(pd.concat([ro, ru]), pd.concat([s["game_id"], s["game_id"]]))}
    out["B_reference_no_skill"] = ref
    out["B_ladder_best"] = {str(k): market.roi_stats(d["ret"], d["game_id"]) for k, d in bl.groupby("bucket", observed=True)}
    bl2 = b_strategy(0.02, "med")
    bl2 = bl2.assign(bucket=pd.cut(bl2["edge"], [0.02, 0.04, 0.06, 0.10, 1.0]))
    out["B_ladder_med"] = {str(k): market.roi_stats(d["ret"], d["game_id"]) for k, d in bl2.groupby("bucket", observed=True)}
    # H1: BetMGM / William Hill UNDERS vs blend of model + sharp consensus
    sh = settled(props)
    sh = sh[sh["p_model"].notna() & sh["sharp_fair"].notna()][market.PROP + ["p_model", "sharp_fair"]]
    q = settled(p)
    q = q[q["bookmaker"].isin(SOFT)].merge(sh, on=market.PROP, how="inner")
    q["dec_u"] = q["under"].map(american_to_decimal)
    q["ret_u"] = np.where(q["won_over"] == 0, q["dec_u"] - 1, -1.0)
    out["H1"] = {}
    for name, use_model, thr in (("H1a", True, .05), ("H1b", True, .03), ("H1c", False, .05), ("H1d", False, .03)):
        pb = 0.5 * q["p_model"] + 0.5 * q["sharp_fair"] if use_model else q["sharp_fair"]
        ev = (1 - pb) * q["dec_u"] - 1
        d = q[ev >= thr]
        out["H1"][name] = market.roi_stats(d["ret_u"], d["game_id"])
    out["H1_all_soft_unders"] = market.roi_stats(q["ret_u"], q["game_id"])
    return out


# ------------------------------------------------------------------------------------------------- BettingPros history
def month_cutoffs(game_dates) -> list:
    """Sorted distinct month starts ('YYYY-MM-01') covering the given game dates: month m is scored by a model fit on date < m."""
    return sorted({str(d)[:7] + "-01" for d in game_dates if isinstance(d, str) and len(d) >= 7})


def games_from_gamelog(gl: pd.DataFrame) -> pd.DataFrame:
    """game_id, date, home_team, away_team from the canonical game log (each game has a home skater row with its opp)."""
    h = gl[gl["is_home"] == 1][["game_id", "date", "team", "opp"]].drop_duplicates("game_id")
    return h.rename(columns={"team": "home_team", "opp": "away_team"}).reset_index(drop=True)


def bettingpros_model_vs_market(cons: pd.DataFrame, gl: pd.DataFrame, names: pd.DataFrame, min_books: int = 3,
                                cutoffs=None, allow_compat: bool = True, slack_min: float | None = None) -> dict:
    """OPTIONAL (nothing calls it by default): score the SOG model against HISTORICAL BettingPros consensus lines.

    cons   mlb_core.odds.bettingpros_loader consensus frame (stat label 'sog'); gl  canonical game log (nhl.props.gamelog);
    names  player_id / full_name / team crosswalk (e.g. NHL/players_master.csv columns, see history.name_table).
    Pipeline (reuses this module's own pieces): resolve_players (mlb_core.data.player_names, FAIL-CLOSED, within the event's
    two teams) -> attach_outcomes -> add_model (expanding monthly retrain, month m scored by a model fit on date < m;
    `cutoffs` default = every month present) -> market.logloss / brier / blend_regression via
    mlb_core.odds.model_vs_market.score_model_vs_market (model P(over) at the market's own line vs the consensus P(over)).

    Selection safety: the consensus is the LAST quote BettingPros held for the day (close-ish, one snapshot per day;
    quotes updated after the scheduled start are dropped as `postgame_quote`); the model's features for a game use only
    earlier games and its booster only earlier dates; void props (player did not appear) are dropped, never graded.
    `allow_compat=False` additionally drops names the resolver matched only by compatible first name.
    `slack_min` (default None = the loader's STRICT `pregame` flag, behaviour unchanged): re-label `pregame` with
    nhl.props.bp_eval.apply_slack -- a quote last updated up to `slack_min` minutes AFTER the scheduled start is kept. The strict
    flag discards the books' last quotes (books re-post within seconds of the puck drop): 46% of saves rows, 36% of SOG rows, and in
    2025-26 nearly every row (docs/solutions/integration-issues/bettingpros-nhl-pregame-flag-and-team-codes.md). Use 10.
    The frame also carries best_over / best_under / novig_over / novig_under (American prices) for price-based scoring.
    Returns {"coverage": {consensus_rows, matched, dropped{low_books, postgame_quote, no_game, unresolved, ambiguous,
    void, duplicate, no_model, push}}, "resolve_how": {...}, "scores": {...}, "frame": props, "cutoffs": [...]}.
    Team codes: BettingPros uses LA/NJ/SJ/TB (+ ARI/UTA by era); history.canon_team maps them to the game log's tricodes
    and attach_outcomes retries a unique +-1 day match. Anything still unmatched shows up as `no_game`."""
    from mlb_core.odds import model_vs_market as MVM
    if slack_min is not None and len(cons):
        from nhl.props import bp_eval
        cons = bp_eval.apply_slack(cons, slack_min)
    c, n_in, dropped = MVM.screen_consensus(cons, {"sog": "sog"}, min_books)
    dropped = dict(dropped, no_game=0, unresolved=0, ambiguous=0, void=0, duplicate=0, no_model=0, push=0)
    cols = ["event_id", "pkey", "line", "game_id", "game_date", "status", "won_over", "push", "cons_fair", "cons_n",
            "p_model", "best_over", "best_under", "novig_over", "novig_under"]
    if len(c) == 0:
        return {"coverage": MVM.coverage(n_in, 0, dropped), "resolve_how": {}, "scores": MVM.score_model_vs_market([], [], [], []),
                "frame": pd.DataFrame(columns=cols), "cutoffs": []}
    pairs = pd.DataFrame({"event_id": c["event_id"].to_numpy(), "game_date": c["game_date"].astype(str).to_numpy(),
                          "home_abbrev": [history.canon_team(t, d) for t, d in zip(c["home"], c["game_date"].astype(str))],
                          "away_abbrev": [history.canon_team(t, d) for t, d in zip(c["away"], c["game_date"].astype(str))],
                          "player_name": c["player"].to_numpy(), "player_norm": c["player_norm"].to_numpy(),
                          "line": c["line"].astype(float).to_numpy(), "cons_fair": c["p_over_cons"].astype(float).to_numpy(),
                          "cons_n": c["n_books"].to_numpy(),
                          "best_over": pd.to_numeric(c["best_over_odds"], errors="coerce").to_numpy(),
                          "best_under": pd.to_numeric(c["best_under_odds"], errors="coerce").to_numpy(),
                          "novig_over": pd.to_numeric(c["novig_over_odds"], errors="coerce").to_numpy(),
                          "novig_under": pd.to_numeric(c["novig_under_odds"], errors="coerce").to_numpy()})
    pairs = history.resolve_players(pairs, names)
    how = pairs["resolve_how"].value_counts().to_dict()
    if not allow_compat:
        pairs.loc[pairs["resolve_how"] == "compat", "player_id"] = pd.NA
    games = games_from_gamelog(gl)
    # team codes are canonicalised on BOTH sides, dated, inside history.attach_outcomes (ARI through 2024-04, UTA after)
    pairs = history.attach_outcomes(pairs, games, gl)
    amb = (pairs["resolve_how"] == "ambiguous") | (pairs["resolve_how"] == "compat") & pairs["player_id"].isna()
    st = pairs["status"].to_numpy(dtype=object).copy()
    reason = np.full(len(pairs), "", dtype=object)
    reason[st == "no_game"] = "no_game"
    reason[(st == "unresolved") & amb.to_numpy()] = "ambiguous"
    reason[(st == "unresolved") & ~amb.to_numpy()] = "unresolved"
    reason[st == "void"] = "void"
    live = reason == ""
    dup = live & pairs.duplicated(["game_id", "player_id"], keep=False).to_numpy()
    reason[dup] = "duplicate"
    for k in ("no_game", "ambiguous", "unresolved", "void", "duplicate"):
        dropped[k] = int((reason == k).sum())
    p = pairs[reason == ""].reset_index(drop=True)
    cuts = list(cutoffs) if cutoffs is not None else month_cutoffs(p["game_date"].unique())
    if len(p):
        p, _arts, _feat = add_model(p, gl, cutoffs=cuts)
    else:
        p["p_model_over"] = np.nan
    nomodel = p["p_model_over"].isna().to_numpy() if len(p) else np.array([], dtype=bool)
    dropped["no_model"] = int(nomodel.sum())
    p = p[~nomodel].reset_index(drop=True)
    pushed = p["push"].to_numpy(dtype=bool) if len(p) else np.array([], dtype=bool)
    dropped["push"] = int(pushed.sum())
    p = p[~pushed].reset_index(drop=True)
    p["pkey"] = "id" + p["player_id"].astype(str)
    p["p_model"] = p["p_model_over"]
    props = p[cols].reset_index(drop=True) if len(p) else pd.DataFrame(columns=cols)
    cov = MVM.coverage(n_in, len(props), dropped)
    scores = MVM.score_model_vs_market(props["won_over"], props["p_model"], props["cons_fair"], props["game_id"]) \
        if len(props) else MVM.score_model_vs_market([], [], [], [])
    return {"coverage": cov, "resolve_how": how, "scores": scores, "frame": props, "cutoffs": cuts}


def load_history_dir(d: str) -> pd.DataFrame:
    """Canonical game log from LOCAL copies of our masters (games_master.csv, skater_boxscores_master.csv, shots_*.csv)."""
    import glob
    import os
    games = pd.read_csv(os.path.join(d, "games_master.csv"), low_memory=False)
    sk = pd.read_csv(os.path.join(d, "skater_boxscores_master.csv"), low_memory=False)
    files = sorted(glob.glob(os.path.join(d, "shots_*.csv"))) or sorted(glob.glob(os.path.join(d, "shots", "shots_*.csv")))
    frames = [pd.read_csv(f, low_memory=False) for f in files]
    return gamelog.from_masters(games, sk, pd.concat(frames, ignore_index=True) if frames else None)


def run(db: str, players_master: str | None = None, history_dir: str | None = None) -> dict:
    extra = None
    if players_master:
        pm = pd.read_csv(players_master)
        extra = pm[["player_id", "full_name", "team"]]
    pairs, gl, acct = build_dataset(db, extra)
    p, arts, feat = add_model(pairs, load_history_dir(history_dir) if history_dir else gl)
    pg = p[p["status"].isin(["ok", "void"])]
    props = prop_table(pg)
    res = {"accounting": acct, "n_ok_pairs": int((p["status"] == "ok").sum()), "n_props_ok": int((props["status"] == "ok").sum()),
           "models": {c: {k: v for k, v in a["meta"].items() if k not in ("feature_means", "features", "params")}
                      for c, a in arts.items()}}
    res["m1"] = m1_calibration(p, props)
    res["sink_a"] = sink_a_section(p)
    res["model"] = model_sections(props, p)
    return res


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, (pd.Interval, pd.Timestamp)):
        return str(o)
    if isinstance(o, pd.DataFrame):
        return o.to_dict("records")
    return str(o)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json")
    ap.add_argument("--history-dir", help="train/score features from our long NHL masters (local CSV copies) instead of the "
                    "DB's 2025-26-only game log; quotes + outcomes still come from --db")
    ap.add_argument("--players-master", help="NHL/players_master.csv (extra name crosswalk for names the DB lacks)")
    a = ap.parse_args(argv)
    res = run(a.db, a.players_master, a.history_dir)
    res["sink_a"].pop("bets", None)
    txt = json.dumps(res, indent=1, default=_json_default)
    print(txt)
    if a.json:
        open(a.json, "w").write(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
