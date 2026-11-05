"""Walk-forward score of the NFL game engine (Kalman ratings + PBP features + key-number matrix) against the CLOSING lines.

Replays every game in chronological order: price it from state built ONLY from earlier games, score it, then update the
state. Market = nflverse schedule closing spread / total / moneyline with their prices (one price per game, source book not
documented by nflverse), so there is no line shopping here (median == best). Metrics: mlb_core.eval.market_scoring.

    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025 --out docs/analysis/nfl_game_market_eval.md

UNVALIDATED research tool: nothing in the serving path reads this.
"""
from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from mlb_core.eval import market_scoring as ms
from nfl.data.read import load as nfl_load
from nfl.game.kalman_ratings import KalmanNFLTracker
from nfl.game.model import NFLGameEngine, W_KALMAN, W_ZFEAT
from nfl.game.pbp_features import PBPFeatureStore, aggregate_team_game_pbp, compute_matchup_pbp_features

log = logging.getLogger("nfl.game.market_eval")
WARM_FROM = 2018   # first season of nflverse pbp we mirror


def _alias(t: str) -> str:
    return {"JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR", "WSH": "WAS"}.get(str(t), str(t))


def _model_p(priced, system: str, side: str) -> Optional[float]:
    m = next((m for m in priced.markets if m.system == system and m.side == side), None)
    return None if m is None else float(m.p_model)


def replay(test_seasons: List[int], pbp: Optional[pd.DataFrame] = None, sched: Optional[pd.DataFrame] = None
           ) -> pd.DataFrame:
    """One row per regular-season game of `test_seasons`: model means and probabilities AT the closing lines, closing
    lines and prices, and the result. State for a game uses only games that finished before it."""
    seasons = list(range(WARM_FROM, max(test_seasons) + 1))
    if pbp is None:
        pbp = pd.concat([nfl_load("pbp", seasons=[s]) for s in seasons], ignore_index=True)
    if sched is None:
        sched = nfl_load("schedules", seasons=seasons)
    sched = sched[sched["home_score"].notna()].copy()
    sched["gd"] = sched["gameday"].astype(str).str[:10]
    sched = sched.sort_values(["gd", "game_id"])

    rows = aggregate_team_game_pbp(pbp)
    by_game: Dict[str, Dict[str, object]] = {}
    for r in rows:
        by_game.setdefault(r.game_id, {})["home" if r.is_home else "away"] = r
    plays = pbp.groupby("game_id").size().to_dict()

    tracker, store, nfl_engine = KalmanNFLTracker(), PBPFeatureStore(), NFLGameEngine()
    out: List[dict] = []
    for g in sched.itertuples(index=False):
        pair = by_game.get(g.game_id)
        if not pair or "home" not in pair or "away" not in pair:
            continue
        h, a = pair["home"], pair["away"]
        home, away, date = _alias(g.home_team), _alias(g.away_team), g.gd
        tracker.roll_season(date)
        if g.season in test_seasons and g.game_type == "REG" and pd.notna(g.spread_line) and pd.notna(g.total_line):
            k = tracker.predict_game(home, away, int(g.season), int(g.week), date)
            feats = compute_matchup_pbp_features(home, away, int(g.season), int(g.week), date, store)
            line_home = -float(g.spread_line)                     # nflverse: positive = home favoured
            priced = nfl_engine.price_game(g.game_id, k, feats, market_odds={
                "spreads": {"line": line_home}, "totals": {"line": float(g.total_line)}})
            out.append({
                "game_id": g.game_id, "date": date, "season": int(g.season), "week": int(g.week), "home": home,
                "away": away, "mu_margin": priced.mu_margin, "mu_total": priced.mu_total,
                # confidence inputs (pre-game state only; see mlb_core.eval.gating)
                "kal_margin": float(k.exp_margin), "kal_total": float(k.exp_total),
                # the PBP-feature side of the margin blend (bye / QB terms are 0 here: schedules are not passed)
                "feat_margin": float((priced.mu_margin - W_KALMAN * k.exp_margin) / W_ZFEAT),
                "kal_std_margin": float(k.std_margin), "kal_std_total": float(k.std_total),
                "n_prior_min": int(min(feats.home_profile.n_games, feats.away_profile.n_games)),
                "p_home_ml": _model_p(priced, "NFL_ML", "home"), "p_home_cover": _model_p(priced, "NFL_SPREAD", "home"),
                "p_away_cover": _model_p(priced, "NFL_SPREAD", "away"), "p_over": _model_p(priced, "NFL_TOTAL", "over"),
                "p_under": _model_p(priced, "NFL_TOTAL", "under"), "mkt_margin": float(g.spread_line),
                "mkt_total": float(g.total_line), "margin": float(g.home_score - g.away_score),
                "total": float(g.home_score + g.away_score), "home_ml": g.home_moneyline, "away_ml": g.away_moneyline,
                "home_sp_odds": g.home_spread_odds, "away_sp_odds": g.away_spread_odds, "over_odds": g.over_odds,
                "under_odds": g.under_odds})
        # post-game update, mirroring nfl.game.run_daily.fit_kalman_and_pbp
        store.add_game(h)
        store.add_game(a)
        n = float(plays.get(g.game_id, 0))
        tracker.update_game(home=h.team, away=a.team, home_score=h.team_score, away_score=a.team_score,
                            home_pass_epa=h.ed_pass_epa, away_pass_epa=a.ed_pass_epa, home_rush_epa=h.ed_rush_epa,
                            away_rush_epa=a.ed_rush_epa, home_plays=n, away_plays=n,
                            kalman_margin=h.kalman_obs_margin, game_date=h.game_date)
    return pd.DataFrame(out)


def _two_way(df: pd.DataFrame, a_odds: str, b_odds: str):
    pa = np.full(len(df), np.nan)
    for i, (x, y) in enumerate(zip(df[a_odds], df[b_odds])):
        if pd.notna(x) and pd.notna(y):
            pa[i] = ms.devig_pair(x, y)[0]
    da = np.array([ms.american_to_decimal_safe(x) if pd.notna(x) else np.nan for x in df[a_odds]])
    db = np.array([ms.american_to_decimal_safe(x) if pd.notna(x) else np.nan for x in df[b_odds]])
    return pa, da, db


def _conf_cols(d: pd.DataFrame, kind: str) -> Dict[str, object]:
    """Confidence inputs for mlb_core.eval.gating, on the scale of the market (all pre-game state)."""
    nan = pd.Series(np.nan, index=d.index)
    col = lambda name: d[name] if name in d.columns else nan          # noqa: E731  (old record files lack them)
    if kind == "margin":
        return {"c_kal": col("kal_margin"), "c_feat": col("feat_margin"), "c_mu": col("mu_margin"),
                "c_line": d["mkt_margin"], "c_sd": col("kal_std_margin"), "c_phase": col("week"), "c_dq": nan}
    return {"c_kal": col("kal_total"), "c_feat": nan, "c_mu": col("mu_total"), "c_line": d["mkt_total"],
            "c_sd": col("kal_std_total"), "c_phase": col("week"), "c_dq": nan}


def to_events(rec: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Records -> per-market event frames for market_scoring.score_market (pushes / ties dropped)."""
    out: Dict[str, pd.DataFrame] = {}
    # moneyline: event = home wins
    d = rec[rec["margin"] != 0].copy()
    pk, da, db = _two_way(d, "home_ml", "away_ml")
    out["moneyline (home wins)"] = pd.DataFrame({
        "date": d["date"], "p_model": d["p_home_ml"], "p_mkt": pk, "y": (d["margin"] > 0).astype(int),
        "dec_a_med": da, "dec_b_med": db, "dec_a_best": da, "dec_b_best": db,
        "season": d["season"] if "season" in d.columns else np.nan, **_conf_cols(d, "margin")}).dropna(subset=["p_mkt"])
    # spread: event = home covers the closing line
    d = rec[(rec["margin"] + (-rec["mkt_margin"])) != 0].copy()
    pk, da, db = _two_way(d, "home_sp_odds", "away_sp_odds")
    pm = d["p_home_cover"] / (d["p_home_cover"] + d["p_away_cover"])
    out["spread (home covers)"] = pd.DataFrame({
        "date": d["date"], "p_model": pm, "p_mkt": pk, "y": ((d["margin"] - d["mkt_margin"]) > 0).astype(int),
        "dec_a_med": da, "dec_b_med": db, "dec_a_best": da, "dec_b_best": db,
        "season": d["season"] if "season" in d.columns else np.nan, **_conf_cols(d, "margin")}).dropna(subset=["p_mkt"])
    # total: event = over
    d = rec[rec["total"] != rec["mkt_total"]].copy()
    pk, da, db = _two_way(d, "over_odds", "under_odds")
    pm = d["p_over"] / (d["p_over"] + d["p_under"])
    out["total (over)"] = pd.DataFrame({
        "date": d["date"], "p_model": pm, "p_mkt": pk, "y": (d["total"] > d["mkt_total"]).astype(int),
        "dec_a_med": da, "dec_b_med": db, "dec_a_best": da, "dec_b_best": db,
        "season": d["season"] if "season" in d.columns else np.nan, **_conf_cols(d, "total")}).dropna(subset=["p_mkt"])
    return out


def accuracy_lines(rec: pd.DataFrame) -> List[str]:
    """Point-forecast check: engine vs the closing line on the realised margin / total."""
    def rmse(x):
        return float(np.sqrt(np.mean(np.square(x))))
    mk = rec["mkt_margin"]                       # nflverse: points the home team is favoured by
    return [f"- margin RMSE: engine {rmse(rec['margin'] - rec['mu_margin']):.2f} vs closing line "
            f"{rmse(rec['margin'] - mk):.2f}  (n={len(rec)})",
            f"- total RMSE: engine {rmse(rec['total'] - rec['mu_total']):.2f} vs closing line "
            f"{rmse(rec['total'] - rec['mkt_total']):.2f}",
            f"- mean (engine - line): margin {float((rec['mu_margin'] - mk).mean()):+.2f}, "
            f"total {float((rec['mu_total'] - rec['mkt_total']).mean()):+.2f}"]


def report(rec: pd.DataFrame) -> str:
    lines = ["## NFL game engine vs closing lines (nflverse schedules)", "", *accuracy_lines(rec), ""]
    for name, ev in to_events(rec).items():
        lines += [ms.render_markdown(ms.score_market(ev, name)), ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seasons", default="2021-2025")
    ap.add_argument("--out", default=None)
    ap.add_argument("--records-csv", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    lo, _, hi = args.seasons.partition("-")
    seasons = list(range(int(lo), int(hi or lo) + 1))
    rec = replay(seasons)
    if args.records_csv:
        rec.to_csv(args.records_csv, index=False)
    text = report(rec)
    print(text)
    if args.out:
        open(args.out, "w").write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
