"""N1: QB kneel-down feature for the NFL prop market-residual model (Argon game-state consult, Nov 11, 2026: KEEP as a build).

    E[kneel_yards] = P(win) x team_kneel_rate_given_win x (-1.12)

computed from PREGAME inputs only and added as a numeric feature to the residual regression against the de-vigged market
line (mlb_core.odds.props_residual, driven by nfl.props.residual). The companion count feature for the rush-attempts
market is E[kneel_attempts] = P(win) x team_kneel_rate_given_win (a kneel is scored as a rush attempt for the QB).

SPEC POINTS (Argon nitpicks are part of the spec, not options):
  * Q4 WIN-STATE KNEELS ONLY. Q2 kneels are end-of-half clock kills, independent of full-game win probability, and are
    EXCLUDED from the rate numerator and denominator alike (qtr == 4 only; overtime is qtr >= 5 and is excluded too --
    a tied game has no win-state kneel mechanism).
  * NO rush-yard / rush-attempt filter on the graded population. The packet's >= 10 rush-yard filter (and the >= 3
    attempt filter) are DROPPED: they excluded exactly the pocket passers whose 1.5-7.5 yard lines kneels flip. The
    graded population is ALL starting QBs with posted rush lines; attachment keys on the schedules' starting QB
    (home_qb_id / away_qb_id), and a row is eligible iff its player is that game's listed starter.
  * team_kneel_rate_given_win is WALK-FORWARD HONEST: for a game on date d it is computed only from team games dated
    STRICTLY BEFORE d. LOOKBACK: expanding window over every prior team game in the supplied history (no fixed season
    cap), shrunk toward the walk-forward league rate with PRIOR_WINS pseudo-wins (see below); the league rate is itself
    computed only from games before d. A team's rate in its first-ever game is the league prior.
  * P(win) is PREGAME MARKET only: de-vigged closing moneyline when both sides are quoted, else spread-derived from
    the closing spread (normal margin model). Never from realized game state (no in-game win probability, no final
    score). The final score is used ONLY to label which past games were wins when building the historical rate --
    that is the conditioning event of the rate, known before any later game it feeds.
  * -1.12 is the fixed league mean yards per kneel from the consult packet. It is a constant of the spec, not fitted.

EVALUATION (the Nov 24 holdout judges this; this module only wires the reporting):
    python -m nfl.props.kneel attach --table residual_table.parquet --pbp pbp.parquet --schedules schedules.parquet --out kneel_table.parquet
    python -m nfl.props.kneel evaluate --table kneel_table.parquet --holdout 2025 [--out-json kneel.json]
or, inside the existing harness, `nfl.props.residual run --kneel-ablation` on a table that already carries the
feature columns. `kneel_ablation` reports, for QB rush yards AND QB rush attempts: the out-of-sample paired log-loss
of the residual ridge WITH vs WITHOUT the kneel feature on the holdout season (fit on earlier seasons only), and the
calibration of both models in the <= 15.5-yard line bucket (rush yards). Argon's bar, encoded as the reported verdict:
KEEP requires pooled log-loss gain (without - with) >= 0.0015 AND monotonic calibration improvement in that bucket
(the with-feature bucket calibration error no worse in every line sub-bucket and better overall); anything else is
AUTO-KILL. This module never runs the Nov 24 holdout itself; `evaluate` scores whatever holdout season it is given.

ASCII only. Research only: nothing here is wired into any serving path.
"""
from __future__ import annotations

import argparse
import json
import logging
import math

import numpy as np
import pandas as pd

from mlb_core.odds import props_residual as R

log = logging.getLogger("nfl.props.kneel")

KNEEL_YARDS_PER_PLAY = -1.12          # spec constant: league mean yards per QB kneel (Argon consult packet)
SPREAD_SIGMA = 13.45                  # sd of (realised margin - closing spread), the standard normal margin model
PRIOR_WINS = 8.0                      # shrinkage: team rate is pulled toward the league rate with this many pseudo-wins
LEAGUE_RATE_PRIOR = 1.9               # Q4 kneels per team win before any history exists (cold-start prior mean)
LL_BAR = 0.0015                       # Argon: KEEP requires out-of-sample log-loss gain >= 0.0015 on the Nov 24 holdout
CAL_BUCKET_MAX_LINE = 15.5            # Argon: calibration is judged in the <= 15.5-yard line bucket
CAL_SUB_BUCKETS = ((0.0, 5.5), (5.5, 10.5), (10.5, 15.5))   # line sub-buckets inside the calibration bucket
CAL_MIN_N = 30                        # sub-buckets smaller than this are reported but do not gate monotonicity
KNEEL_STATS = ("rush_yds", "rush_att")                       # residual-table stat names (engine stat names)
LAM_MAIN = 1e5                        # ridge penalty for the ablation fits (middle of props_residual.RIDGE_GRID's lam_main)
FEATURE_COLUMNS = ["team", "p_win_pregame", "team_kneel_rate_given_win", "e_kneel_yards", "e_kneel_att", "kneel_eligible"]


# ------------------------------------------------------------------------------------------------------- P(win), pregame
def _implied(american: float) -> float:
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def win_prob_from_moneyline(own_ml, opp_ml) -> float:
    """De-vigged P(win) from a two-sided moneyline pair (American odds). NaN when either side is missing/invalid."""
    if own_ml is None or opp_ml is None or not np.isfinite(own_ml) or not np.isfinite(opp_ml):
        return float("nan")
    if own_ml == 0 or opp_ml == 0:
        return float("nan")
    p_own, p_opp = _implied(own_ml), _implied(opp_ml)
    return p_own / (p_own + p_opp)


def win_prob_from_spread(team_spread: float, sigma: float = SPREAD_SIGMA) -> float:
    """P(win) from the closing spread under a normal margin model. Repo convention (nfl.features.props_features):
    team_spread > 0 means the team is FAVOURED, and realised margin ~ Normal(team_spread, sigma)."""
    if team_spread is None or not np.isfinite(team_spread):
        return float("nan")
    z = float(team_spread) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def game_win_probs(schedules: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team): the PREGAME market P(win) and its source. Moneyline (de-vigged) when both closing
    moneylines are present, else the closing spread. Columns: game_id, team, p_win_pregame, p_win_source."""
    s = schedules.drop_duplicates("game_id")
    rows = []
    has_ml = "home_moneyline" in s.columns and "away_moneyline" in s.columns
    for g in s.itertuples(index=False):
        gd = g._asdict() if hasattr(g, "_asdict") else vars(g)
        for side, oside in (("home", "away"), ("away", "home")):
            team = gd[f"{side}_team"]
            p = win_prob_from_moneyline(gd.get(f"{side}_moneyline"), gd.get(f"{oside}_moneyline")) if has_ml else float("nan")
            src = "moneyline"
            if not np.isfinite(p):
                spread_line = gd.get("spread_line")          # nflverse: spread_line > 0 = home favoured
                team_spread = float(spread_line) if side == "home" else -float(spread_line)
                p, src = win_prob_from_spread(team_spread), "spread"
            rows.append({"game_id": g.game_id, "team": team, "p_win_pregame": p, "p_win_source": src})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- historical kneel rates
def kneels_per_team_game(pbp: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """Q4 win-state kneels per team-game. One row per (game_id, team) that appears in the schedules, with `won` from the
    final score and `q4_kneels` = kneel plays (nflverse qb_kneel flag) by that team in quarter 4 of a game it won.
    Kneels in lost games are not win-state kneels and count 0; Q2 (and any non-Q4) kneels never enter either side.
    NO filter on any player's rush attempts or rush yards is applied anywhere in this module."""
    need = {"game_id", "posteam", "qtr", "qb_kneel"}
    miss = need - set(pbp.columns)
    if miss:
        raise ValueError(f"pbp is missing columns {sorted(miss)}")
    s = schedules.drop_duplicates("game_id").set_index("game_id")
    winner = {}
    for gid, g in s.iterrows():
        if pd.notna(g.get("home_score")) and pd.notna(g.get("away_score")) and g["home_score"] != g["away_score"]:
            winner[gid] = g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]
    gameday = s["gameday"].astype(str).str[:10].to_dict() if "gameday" in s.columns else {}
    season = s["season"].to_dict() if "season" in s.columns else {}
    teams = {}
    for gid, g in s.iterrows():
        for t in (g["home_team"], g["away_team"]):
            teams[(gid, t)] = {"game_id": gid, "team": t, "date": gameday.get(gid), "season": season.get(gid),
                               "won": winner.get(gid) == t, "q4_kneels": 0}
    k = pbp[(pbp["qtr"] == 4) & (pbp["qb_kneel"].fillna(0).astype(float) == 1.0)]
    for (gid, team), n in k.groupby([k["game_id"], k["posteam"]]).size().items():
        if (gid, team) in teams and teams[(gid, team)]["won"]:
            teams[(gid, team)]["q4_kneels"] = int(n)
    return pd.DataFrame(teams.values())


def walk_forward_kneel_rates(team_games: pd.DataFrame) -> pd.DataFrame:
    """Add `team_kneel_rate_given_win` to a kneels_per_team_game frame: for each team-game, the shrunk mean Q4 kneels
    per win over that team's games dated STRICTLY BEFORE this game's date,

        rate = (kneels_in_prior_wins + PRIOR_WINS * league_rate_before) / (prior_wins + PRIOR_WINS),

    where league_rate_before is the same ratio pooled over ALL teams' games before the date (cold start:
    LEAGUE_RATE_PRIOR). Same-date games never see each other. Pure function of the past: garbling any game on/after
    date d cannot move the rate of a game dated d or earlier."""
    tg = team_games.copy()
    tg["date"] = tg["date"].astype(str).str[:10]
    tg = tg.sort_values(["date", "game_id", "team"], kind="mergesort").reset_index(drop=True)
    won = tg["won"].fillna(False).astype(bool).to_numpy()
    kn = np.where(won, tg["q4_kneels"].astype(float).to_numpy(), 0.0)
    wn = won.astype(float)
    # league walk-forward totals, keyed by date (all games on a date share the totals of strictly earlier dates)
    by_date = pd.DataFrame({"date": tg["date"].to_numpy(), "k": kn, "w": wn}).groupby("date").sum()
    cum = by_date.cumsum() - by_date
    lg_k = tg["date"].map(cum["k"]).to_numpy()
    lg_w = tg["date"].map(cum["w"]).to_numpy()
    league_rate = np.where(lg_w > 0, lg_k / np.maximum(lg_w, 1.0), LEAGUE_RATE_PRIOR)
    # team walk-forward totals (expanding within team, shifted past every earlier date, not just earlier rows)
    tg["_k"], tg["_w"] = kn, wn
    tt = tg.groupby(["team", "date"], sort=False)[["_k", "_w"]].sum().groupby(level=0).cumsum()
    tt = tt.groupby(level=0).shift(1).fillna(0.0)
    team_k = tt["_k"].reindex(pd.MultiIndex.from_frame(tg[["team", "date"]])).to_numpy()
    team_w = tt["_w"].reindex(pd.MultiIndex.from_frame(tg[["team", "date"]])).to_numpy()
    tg["team_kneel_rate_given_win"] = (team_k + PRIOR_WINS * league_rate) / (team_w + PRIOR_WINS)
    tg["league_kneel_rate_before"] = league_rate
    return tg.drop(columns=["_k", "_w"])


# ------------------------------------------------------------------------------------------------------------- the feature
def expected_kneel_yards(p_win, rate) -> np.ndarray:
    """E[kneel_yards] = P(win) x team_kneel_rate_given_win x (-1.12). Pregame inputs only."""
    return np.asarray(p_win, dtype=float) * np.asarray(rate, dtype=float) * KNEEL_YARDS_PER_PLAY


def expected_kneel_attempts(p_win, rate) -> np.ndarray:
    """E[kneel_attempts] = P(win) x team_kneel_rate_given_win (each kneel is a scored QB rush attempt)."""
    return np.asarray(p_win, dtype=float) * np.asarray(rate, dtype=float)


def attach_kneel_features(table: pd.DataFrame, schedules: pd.DataFrame, pbp: pd.DataFrame) -> pd.DataFrame:
    """Attach FEATURE_COLUMNS to a residual table (nfl.props.residual.build_table output). A row's team is its game's
    listed starting QB side: the row is kneel-eligible iff `pid` equals the schedules home_qb_id / away_qb_id for its
    game (all starting QBs with posted rush lines; no rush-yard or rush-attempt screen). Non-QB stats and non-starters
    get 0.0 features and kneel_eligible=False. Rows whose game has no market P(win) keep NaN p_win and 0.0 features."""
    t = table.copy()
    rates = walk_forward_kneel_rates(kneels_per_team_game(pbp, schedules))
    rate_map = {(r.game_id, r.team): r.team_kneel_rate_given_win for r in rates.itertuples(index=False)}
    probs = game_win_probs(schedules)
    prob_map = {(r.game_id, r.team): r.p_win_pregame for r in probs.itertuples(index=False)}
    s = schedules.drop_duplicates("game_id").set_index("game_id")
    team_of = {}
    for gid, g in s.iterrows():
        for side in ("home", "away"):
            qid = g.get(f"{side}_qb_id")
            if isinstance(qid, str) and qid:
                team_of[(gid, qid)] = g[f"{side}_team"]
    keys = list(zip(t["game_id"].astype(str), t["pid"].astype(str)))
    team = pd.Series([team_of.get(k) for k in keys], index=t.index, dtype=object)
    p_win = pd.Series([prob_map.get((gid, tm)) if tm is not None else np.nan
                       for (gid, _), tm in zip(keys, team)], index=t.index, dtype=float)
    rate = pd.Series([rate_map.get((gid, tm), np.nan) if tm is not None else np.nan
                      for (gid, _), tm in zip(keys, team)], index=t.index, dtype=float)
    eligible = team.notna() & t["stat"].isin(KNEEL_STATS)
    ey = expected_kneel_yards(p_win.fillna(0.0), rate.fillna(0.0))
    ea = expected_kneel_attempts(p_win.fillna(0.0), rate.fillna(0.0))
    t["team"] = team
    t["p_win_pregame"] = p_win
    t["team_kneel_rate_given_win"] = rate
    t["e_kneel_yards"] = np.where(eligible, ey, 0.0)
    t["e_kneel_att"] = np.where(eligible, ea, 0.0)
    t["kneel_eligible"] = eligible.to_numpy()
    return t


# --------------------------------------------------------------------------------------------------- holdout ablation
def _fit_predict(train: pd.DataFrame, test: pd.DataFrame, kneel_col: str | None) -> np.ndarray:
    """One-stat offset-ridge on the shared engine's features, with (kneel_col given) or without the kneel feature.
    Identical design both ways; the kneel column is appended raw (OffsetRidge standardises internally)."""
    xtr, xte = R.make_features(train), R.make_features(test)
    if kneel_col is not None:
        xtr = xtr.assign(kneel=train[kneel_col].astype(float).fillna(0.0).to_numpy())
        xte = xte.assign(kneel=test[kneel_col].astype(float).fillna(0.0).to_numpy())
    cols = list(xtr.columns)
    pen = np.full(len(cols), LAM_MAIN)
    mdl = R.OffsetRidge(pen).fit(xtr[cols].to_numpy(float), train["over"].to_numpy(float), R.logit(train["p_mkt"]))
    return R.sigmoid(R.logit(test["p_mkt"]) + mdl.margin(xte[cols].to_numpy(float)))


def _calibration_buckets(test: pd.DataFrame, p_with: np.ndarray, p_without: np.ndarray) -> dict:
    """Calibration in the <= 15.5-yard line bucket: per line sub-bucket, |mean predicted P(over) - realised over rate|
    for both models. Monotonic improvement = the with-feature error is no worse in EVERY sub-bucket with n >= CAL_MIN_N
    and the n-weighted bucket error improves."""
    sub = test[test["line"].astype(float) <= CAL_BUCKET_MAX_LINE]
    pw = p_with[test["line"].astype(float).to_numpy() <= CAL_BUCKET_MAX_LINE]
    po = p_without[test["line"].astype(float).to_numpy() <= CAL_BUCKET_MAX_LINE]
    rows, mono, wsum, wtot = [], True, 0.0, 0
    for lo, hi in CAL_SUB_BUCKETS:
        m = (sub["line"].astype(float).to_numpy() > lo) & (sub["line"].astype(float).to_numpy() <= hi) if lo > 0 else \
            (sub["line"].astype(float).to_numpy() <= hi)
        if not m.any():
            continue
        y = sub["over"].to_numpy(float)[m]
        ew, eo = abs(float(pw[m].mean()) - float(y.mean())), abs(float(po[m].mean()) - float(y.mean()))
        rows.append({"lo": lo, "hi": hi, "n": int(m.sum()), "err_with": ew, "err_without": eo, "improvement": eo - ew})
        if m.sum() >= CAL_MIN_N:
            mono = mono and (ew <= eo)
            wsum += (eo - ew) * m.sum()
            wtot += int(m.sum())
    return {"n": int(len(sub)), "sub_buckets": rows, "weighted_improvement": (wsum / wtot if wtot else float("nan")),
            "monotonic_improvement": bool(mono and wtot > 0 and wsum > 0)}


def kneel_ablation(table: pd.DataFrame, holdout: int) -> dict:
    """The Nov 24 reporting hook. Fits the residual ridge on seasons < holdout and scores season == holdout, for QB
    rush yards and QB rush attempts (kneel-eligible rows only), WITH vs WITHOUT the kneel feature. Returns the paired
    log-loss gains (without - with; positive = the feature helps), the <= 15.5-yard calibration comparison (rush
    yards), and Argon's verdict against LL_BAR. Raises if the table lacks the feature columns or the split is empty."""
    miss = [c for c in ("e_kneel_yards", "e_kneel_att", "kneel_eligible") if c not in table.columns]
    if miss:
        raise ValueError(f"table is missing kneel feature columns {miss}; run `nfl.props.kneel attach` (or build the "
                         "residual table with kneel attachment) first")
    t = R.prepare(table)
    t = t[t["kneel_eligible"].astype(bool) & t["stat"].isin(KNEEL_STATS)].copy()
    out = {"holdout_season": int(holdout), "ll_bar": LL_BAR, "by_stat": {}}
    gains, cal = {}, None
    for stat, col in (("rush_yds", "e_kneel_yards"), ("rush_att", "e_kneel_att")):
        tr = t[(t["stat"] == stat) & (t["season"] < holdout)]
        te = t[(t["stat"] == stat) & (t["season"] == holdout)]
        if len(tr) < 100 or len(te) < 30:
            out["by_stat"][stat] = {"n_train": int(len(tr)), "n_test": int(len(te)), "note": "too few rows to score"}
            continue
        R.assert_chronological(tr, te)
        p_with = _fit_predict(tr, te, col)
        p_without = _fit_predict(tr, te, None)
        y, pm, cl = te["over"].to_numpy(float), np.clip(te["p_mkt"].to_numpy(float), *R.CLIP), te["game_date"].to_numpy()
        ll_w = R.paired_logloss(y, p_with, pm, cl)
        ll_wo = R.paired_logloss(y, p_without, pm, cl)
        gains[stat] = ll_wo["ll_model"] - ll_w["ll_model"]
        out["by_stat"][stat] = {"n_train": int(len(tr)), "n_test": int(len(te)), "ll_without": ll_wo["ll_model"],
                                "ll_with": ll_w["ll_model"], "ll_gain": gains[stat],
                                "d_vs_market_with": ll_w["d"], "d_vs_market_without": ll_wo["d"]}
        if stat == "rush_yds":
            cal = _calibration_buckets(te.reset_index(drop=True), p_with, p_without)
    if gains:
        n_pool = sum(out["by_stat"][s]["n_test"] for s in gains)
        pooled = sum(gains[s] * out["by_stat"][s]["n_test"] for s in gains) / n_pool
    else:
        pooled = float("nan")
    out["ll_gain_pooled"] = pooled
    out["calibration_le_15_5"] = cal
    keep = bool(gains) and pooled == pooled and pooled >= LL_BAR and bool(cal and cal["monotonic_improvement"])
    out["verdict"] = "KEEP" if keep else "AUTO-KILL"
    out["bar"] = (f"KEEP requires pooled out-of-sample log-loss gain >= {LL_BAR} on QB rush yards + rush attempts AND "
                  "monotonic calibration improvement in the <= 15.5-yard line bucket (Argon, Nov 11, 2026)")
    return out


def render_kneel_ablation(res: dict) -> str:
    L_ = [f"##### N1 QB kneel feature, holdout {res['holdout_season']} (WITH vs WITHOUT, residual ridge, fit on earlier seasons only)", ""]
    for stat, r in res["by_stat"].items():
        if "ll_gain" in r:
            L_.append(f"- {stat}: n_test {r['n_test']}, LL without {r['ll_without']:.4f}, LL with {r['ll_with']:.4f}, "
                      f"gain {r['ll_gain']:+.5f} (bar {res['ll_bar']})")
        else:
            L_.append(f"- {stat}: not scored ({r.get('note')}; train {r['n_train']}, test {r['n_test']})")
    cal = res.get("calibration_le_15_5")
    if cal:
        L_.append(f"- calibration, rush yards line <= {CAL_BUCKET_MAX_LINE}: n {cal['n']}, weighted improvement "
                  f"{cal['weighted_improvement']:+.4f}, monotonic improvement {cal['monotonic_improvement']}")
        for b in cal["sub_buckets"]:
            L_.append(f"  - line {b['lo']}-{b['hi']}: n {b['n']}, err without {b['err_without']:.4f}, err with {b['err_with']:.4f}")
    L_.append(f"- pooled LL gain: {res['ll_gain_pooled']:+.5f}" if res["ll_gain_pooled"] == res["ll_gain_pooled"] else "- pooled LL gain: n/a")
    L_.append(f"- VERDICT vs Argon's bar: {res['verdict']}")
    return "\n".join(L_) + "\n"


# ------------------------------------------------------------------------------------------------------------- CLI
def _attach(args) -> int:
    table = pd.read_parquet(args.table)
    pbp = pd.read_parquet(args.pbp)
    sched = pd.read_parquet(args.schedules)
    out = attach_kneel_features(table, sched, pbp)
    out.to_parquet(args.out, index=False)
    n = int(out["kneel_eligible"].sum())
    log.info("attached kneel features: %d rows, %d kneel-eligible QB rush rows", len(out), n)
    return 0


def _evaluate(args) -> int:
    table = pd.read_parquet(args.table)
    res = kneel_ablation(table, args.holdout)
    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(res, fh, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(render_kneel_ablation(res))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("attach", help="attach the kneel feature columns to a residual table")
    a.add_argument("--table", required=True)
    a.add_argument("--pbp", required=True, help="nflverse play-by-play parquet (game_id, posteam, qtr, qb_kneel, ...)")
    a.add_argument("--schedules", required=True, help="nflverse schedules parquet (scores, lines, moneylines, qb ids)")
    a.add_argument("--out", required=True)
    e = sub.add_parser("evaluate", help="WITH vs WITHOUT holdout ablation + Argon verdict (scores the given season)")
    e.add_argument("--table", required=True)
    e.add_argument("--holdout", type=int, required=True)
    e.add_argument("--out-json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return _attach(args) if args.cmd == "attach" else _evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
