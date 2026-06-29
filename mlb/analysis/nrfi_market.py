"""NRFI/YRFI historical-odds analysis toolkit.

Built around the user-supplied `yrfi_master.csv` (per-game NRFI/YRFI prices,
2024-04-18 onward: opening, consensus, best-of, and per-book). This is the
market-price layer that the model-improvement program
(handoffs/roadmap_2026-06-28_model_improvement.md) was missing.

Three deliverables, each a function below:

  1. backtest_vs_lines()       -- honest NRFI backtest against REAL market prices.
                                  Real ROI + CLV + edge-bucket realized win-rate.
                                  Needs: realized outcomes + model probabilities.
  2. market_baseline()         -- de-vigged consensus as a market-truth estimate;
                                  hold, line movement, best-vs-consensus value.
                                  Needs: ONLY yrfi_master.csv (runs anywhere).
  3. calibration_vs_market()   -- model vs market-implied probability, plus
                                  realized calibration by bucket.
                                  Needs: model probabilities (+ outcomes for the
                                  realized half).

Plumbing shared by all three:
  load_yrfi_master()  -- parse + normalize + attach market-implied / de-vigged probs.
  attach_outcomes()   -- join first-inning runs from scoring_master to get the
                         realized YRFI label (the odds file has only FINAL scores).

All odds math is reused from mlb_core.odds.utils -- do NOT reimplement here.
Run from repo root with PYTHONPATH=. so `mlb_core` imports.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from mlb_core.odds.utils import (
    american_to_decimal,
    american_to_implied_prob,
    clv_pct_from_prices,
    devig_two_way,
    kelly_pct,
)

# --------------------------------------------------------------------------
# Team-name normalization
#
# yrfi_master.csv mixes 3-letter abbrevs ("ARI") with full/medium names
# ("Atlanta", "Athletics", "Baltimore"). scoring_master uses game_pk + teams
# from the MLB Stats API. Normalize everything to a 3-letter canonical code so
# the outcome join is reliable.
# --------------------------------------------------------------------------
_TEAM_CANON = {
    # abbrevs map to themselves
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS", "CHC": "CHC",
    "CWS": "CWS", "CIN": "CIN", "CLE": "CLE", "COL": "COL", "DET": "DET",
    "HOU": "HOU", "KC": "KC", "LAA": "LAA", "LAD": "LAD", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "ATH": "OAK", "PHI": "PHI", "PIT": "PIT", "SD": "SD", "SF": "SF",
    "SEA": "SEA", "STL": "STL", "TB": "TB", "TEX": "TEX", "TOR": "TOR",
    "WSH": "WSH", "WAS": "WSH",
    # city / nickname / medium-name forms seen in the file
    "ARIZONA": "ARI", "DIAMONDBACKS": "ARI",
    "ATLANTA": "ATL", "BRAVES": "ATL",
    "BALTIMORE": "BAL", "ORIOLES": "BAL",
    "BOSTON": "BOS", "RED SOX": "BOS",
    "CHICAGO CUBS": "CHC", "CUBS": "CHC",
    "CHICAGO WHITE SOX": "CWS", "WHITE SOX": "CWS",
    "CINCINNATI": "CIN", "REDS": "CIN",
    "CLEVELAND": "CLE", "GUARDIANS": "CLE",
    "COLORADO": "COL", "ROCKIES": "COL",
    "DETROIT": "DET", "TIGERS": "DET",
    "HOUSTON": "HOU", "ASTROS": "HOU",
    "KANSAS CITY": "KC", "ROYALS": "KC",
    "LA ANGELS": "LAA", "ANGELS": "LAA",
    "LA DODGERS": "LAD", "DODGERS": "LAD",
    "MIAMI": "MIA", "MARLINS": "MIA",
    "MILWAUKEE": "MIL", "BREWERS": "MIL",
    "MINNESOTA": "MIN", "TWINS": "MIN",
    "NY METS": "NYM", "METS": "NYM",
    "NY YANKEES": "NYY", "YANKEES": "NYY",
    "OAKLAND": "OAK", "ATHLETICS": "OAK",
    "PHILADELPHIA": "PHI", "PHILLIES": "PHI",
    "PITTSBURGH": "PIT", "PIRATES": "PIT",
    "SAN DIEGO": "SD", "PADRES": "SD",
    "SAN FRANCISCO": "SF", "GIANTS": "SF",
    "SEATTLE": "SEA", "MARINERS": "SEA",
    "ST LOUIS": "STL", "ST. LOUIS": "STL", "CARDINALS": "STL",
    "TAMPA BAY": "TB", "RAYS": "TB",
    "TEXAS": "TEX", "RANGERS": "TEX",
    "TORONTO": "TOR", "BLUE JAYS": "TOR",
    "WASHINGTON": "WSH", "NATIONALS": "WSH",
}


def norm_team(name: str) -> str:
    """Map any team spelling in the odds file to a 3-letter canonical code."""
    if not isinstance(name, str):
        return ""
    key = name.strip().upper()
    return _TEAM_CANON.get(key, key)


def _parse_american(val) -> float:
    """Parse an American-odds cell. Handles '', 'EVEN', '+105', '-120'."""
    if val is None:
        return np.nan
    s = str(val).strip()
    if not s:
        return np.nan
    if s.upper() == "EVEN":
        return 100.0
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return np.nan


# Per-book column prefixes present in the file (YRFI/NRFI suffix each).
BOOK_COLS = [
    "bet365", "DraftKings", "BetMGM", "FanDuel", "theScore Bet", "BetRivers",
    "SugarHouse", "PartyCasino", "Fliff", "Caesars", "PointsBet",
    "Hard Rock Bet", "ESPNBet",
]


def load_yrfi_master(path: str = "yrfi_master.csv", devig_method: str = "shin") -> pd.DataFrame:
    """Load + normalize the historical NRFI/YRFI odds file.

    Returns a DataFrame with, per game:
      date, away, home (canonical 3-letter), game_key
      open/consensus/best American odds for YRFI and NRFI
      imp_yrfi / imp_nrfi          -- vig-inclusive implied probs (consensus)
      fair_yrfi / fair_nrfi        -- de-vigged fair probs (consensus, `devig_method`)
      hold                         -- consensus over-round (vig), as a fraction
      best_yrfi_odds / best_nrfi_odds
    """
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["away"] = df["Away"].map(norm_team)
    df["home"] = df["Home"].map(norm_team)
    df["game_key"] = (
        df["date"].dt.strftime("%Y-%m-%d") + "_" + df["away"] + "@" + df["home"]
    )

    for col in [
        "Open_YRFI", "Open_NRFI", "Best Odds_YRFI", "Best Odds_NRFI",
        "Consensus_YRFI", "Consensus_NRFI",
    ]:
        df[col] = df[col].map(_parse_american)
    for b in BOOK_COLS:
        for side in ("YRFI", "NRFI"):
            c = f"{b}_{side}"
            if c in df.columns:
                df[c] = df[c].map(_parse_american)

    # Vig-inclusive implied probabilities from the consensus price.
    df["imp_yrfi"] = df["Consensus_YRFI"].map(american_to_implied_prob)
    df["imp_nrfi"] = df["Consensus_NRFI"].map(american_to_implied_prob)
    df["hold"] = df["imp_yrfi"] + df["imp_nrfi"] - 1.0

    # De-vigged fair probabilities (favorite-longshot aware; default Shin).
    fair = df.apply(
        lambda r: devig_two_way(r["imp_yrfi"], r["imp_nrfi"], method=devig_method),
        axis=1, result_type="expand",
    )
    df["fair_yrfi"], df["fair_nrfi"] = fair[0], fair[1]

    df["best_yrfi_odds"] = df["Best Odds_YRFI"]
    df["best_nrfi_odds"] = df["Best Odds_NRFI"]
    df["year"] = df["date"].dt.year
    return df


def attach_outcomes(df: pd.DataFrame, features_path: str | None = None) -> pd.DataFrame:
    """Attach the realized game-level YRFI label from the NRFI model_features file.

    NOTE: Scoring/scoring_master.csv canNOT be used directly -- it has only
    (game_pk, inning, half, runs) with no date/team columns, so it cannot build
    the date_away@home game_key. The NRFI model_features.csv DOES carry game_pk,
    game_date, home_team, away_team and the `yrfi` target, so the realized
    outcome comes from there. The per-row `yrfi` is half-inning; the game is
    YRFI if EITHER half scored, so aggregate by max over game_pk.

    Usually you do NOT need this: gen_nrfi_preds.py already emits `yrfi` in the
    preds file, and the backtest/calibration read it from there. This helper is
    for the case where you have model_features locally but not preds.

    Adds `yrfi` and `outcome_matched`. No-op (yrfi=NaN) if the file is absent.
    """
    df = df.copy()
    df["yrfi"] = np.nan
    df["outcome_matched"] = False

    path = features_path or _find_features()
    if path is None or not os.path.exists(path):
        print(
            f"[attach_outcomes] model_features not found ({features_path or 'auto'}); "
            f"outcomes will come from the preds file instead (if it carries yrfi)."
        )
        return df

    feat = pd.read_csv(path, low_memory=False)
    need = {"game_pk", "game_date", "home_team", "away_team", "yrfi"}
    if not need.issubset(feat.columns):
        print(f"[attach_outcomes] {path} missing {need - set(feat.columns)}; skipping.")
        return df

    g = feat.groupby("game_pk").agg(
        game_date=("game_date", "first"),
        home_team=("home_team", "first"),
        away_team=("away_team", "first"),
        yrfi=("yrfi", "max"),  # game-level: YRFI if either half scored
    ).reset_index()
    g["game_key"] = (
        pd.to_datetime(g["game_date"]).dt.strftime("%Y-%m-%d")
        + "_" + g["away_team"].map(norm_team)
        + "@" + g["home_team"].map(norm_team)
    )
    key_to_yrfi = g.set_index("game_key")["yrfi"]
    df["yrfi"] = df["game_key"].map(key_to_yrfi)
    df["outcome_matched"] = df["yrfi"].notna()
    print(f"[attach_outcomes] matched {int(df['outcome_matched'].sum())}/{len(df)} games.")
    return df


def _find_features() -> str | None:
    for p in ("model_features.csv", "NRFI_Pro_System/data/model_features.csv",
              os.path.expanduser("~/mlb-betting/model_features.csv")):
        if os.path.exists(p):
            return p
    return os.environ.get("NRFI_MODEL_FEATURES_PATH")


# --------------------------------------------------------------------------
# Deliverable 2: market baseline (runs with ONLY the odds file)
# --------------------------------------------------------------------------
def market_baseline(df: pd.DataFrame) -> dict:
    """De-vigged consensus as a market-truth estimate + market structure stats.

    Returns a dict of frames/series:
      hold_by_year       -- mean consensus over-round (vig) per season
      fair_yrfi_dist     -- distribution of de-vigged P(YRFI)
      line_move          -- open -> consensus drift in P(NRFI) (who the market backs)
      best_vs_consensus  -- value available from line shopping (best vs consensus odds)
    """
    out = {}
    out["hold_by_year"] = df.groupby("year")["hold"].agg(["mean", "median", "count"])

    out["fair_yrfi_dist"] = df["fair_yrfi"].describe()

    open_imp_nrfi = df["Open_NRFI"].map(american_to_implied_prob)
    cons_imp_nrfi = df["imp_nrfi"]
    df_move = (cons_imp_nrfi - open_imp_nrfi).dropna()
    out["line_move"] = pd.Series({
        "mean_drift_nrfi_prob": df_move.mean(),
        "pct_games_market_to_nrfi": (df_move > 0).mean(),
        "n": len(df_move),
    })

    # Best-line value: extra decimal payout vs consensus on the same side.
    best_v_cons = []
    for side, best_c, cons_c in (("YRFI", "best_yrfi_odds", "Consensus_YRFI"),
                                 ("NRFI", "best_nrfi_odds", "Consensus_NRFI")):
        d = df[[best_c, cons_c]].dropna()
        gain = d[best_c].map(american_to_decimal) / d[cons_c].map(american_to_decimal) - 1.0
        best_v_cons.append(pd.Series({
            "side": side, "mean_pct_better": gain.mean() * 100,
            "pct_games_best>consensus": (gain > 0).mean(), "n": len(d),
        }))
    out["best_vs_consensus"] = pd.DataFrame(best_v_cons).set_index("side")
    return out


# --------------------------------------------------------------------------
# Deliverable 1: honest backtest vs real lines
# --------------------------------------------------------------------------
def backtest_vs_lines(
    df: pd.DataFrame,
    preds: pd.DataFrame,
    min_edge: float = 0.03,
    kelly_fraction: float = 0.25,
    side: str = "auto",
    price: str = "best",
) -> dict:
    """Backtest model NRFI/YRFI probabilities against REAL historical prices.

    Args:
      df:    output of attach_outcomes(load_yrfi_master()) -- needs `yrfi` label.
      preds: DataFrame with columns ['game_key', 'p_yrfi'] (model P(YRFI)).
             P(NRFI) is taken as 1 - p_yrfi.
      min_edge: minimum (p_model - p_fair) to place a bet.
      side:  'auto' bets whichever side has positive edge; or force 'YRFI'/'NRFI'.
      price: 'best' uses best-of-book odds; 'consensus' uses consensus.

    Returns dict with: n_bets, roi, win_rate, mean_edge, mean_clv, clv_tstat,
      and `by_edge_bucket` (the adverse-selection table: realized win-rate vs
      model prob vs market fair prob per edge bucket).
    """
    pred_cols = ["game_key", "p_yrfi"] + (["yrfi"] if "yrfi" in preds.columns else [])
    m = df.merge(preds[pred_cols], on="game_key", how="inner", suffixes=("", "_pred"))
    # Realized outcome can come from df (attach_outcomes) or the preds file.
    if "yrfi_pred" in m.columns:
        m["yrfi"] = m["yrfi"].fillna(m["yrfi_pred"]) if "yrfi" in m.columns else m["yrfi_pred"]
    if "yrfi" not in m.columns or m["yrfi"].notna().sum() == 0:
        raise ValueError("backtest needs realized outcomes; provide yrfi via preds or attach_outcomes.")
    m = m[m["yrfi"].notna()].copy()
    m["p_nrfi"] = 1.0 - m["p_yrfi"]

    rows = []
    for _, r in m.iterrows():
        cands = []
        for s, p_model, p_fair, odds_best, odds_cons in (
            ("YRFI", r["p_yrfi"], r["fair_yrfi"], r["best_yrfi_odds"], r["Consensus_YRFI"]),
            ("NRFI", r["p_nrfi"], r["fair_nrfi"], r["best_nrfi_odds"], r["Consensus_NRFI"]),
        ):
            odds = odds_best if price == "best" else odds_cons
            if pd.isna(p_model) or pd.isna(p_fair) or pd.isna(odds):
                continue
            edge = p_model - p_fair
            cands.append((s, p_model, p_fair, edge, odds, odds_cons))
        if not cands:
            continue
        if side == "auto":
            s, p_model, p_fair, edge, odds, odds_cons = max(cands, key=lambda c: c[3])
        else:
            picks = [c for c in cands if c[0] == side]
            if not picks:
                continue
            s, p_model, p_fair, edge, odds, odds_cons = picks[0]
        if edge < min_edge:
            continue

        won = (r["yrfi"] == 1) if s == "YRFI" else (r["yrfi"] == 0)
        dec = american_to_decimal(odds)
        profit = (dec - 1.0) if won else -1.0  # 1-unit flat
        rows.append({
            "game_key": r["game_key"], "side": s, "p_model": p_model,
            "p_fair": p_fair, "edge": edge, "odds": odds, "won": int(won),
            "profit_units": profit, "kelly_pct": kelly_pct(edge, odds, kelly_fraction),
            "clv_pct": clv_pct_from_prices(odds, odds_cons) if price == "best" else np.nan,
        })

    bt = pd.DataFrame(rows)
    if bt.empty:
        return {"n_bets": 0, "note": "no bets cleared min_edge"}

    res = {
        "n_bets": len(bt),
        "win_rate": bt["won"].mean(),
        "roi": bt["profit_units"].sum() / len(bt),
        "total_units": bt["profit_units"].sum(),
        "mean_edge": bt["edge"].mean(),
    }
    clv = bt["clv_pct"].dropna()
    if len(clv) > 1:
        res["mean_clv"] = clv.mean()
        res["clv_tstat"] = clv.mean() / (clv.std(ddof=1) / np.sqrt(len(clv)))

    # The adverse-selection table: do the big "edges" actually win?
    bt["edge_bucket"] = pd.cut(
        bt["edge"], [0, 0.05, 0.10, 0.15, 0.20, 1.0],
        labels=["0-5%", "5-10%", "10-15%", "15-20%", "20%+"],
    )
    res["by_edge_bucket"] = bt.groupby("edge_bucket", observed=True).agg(
        n=("won", "size"), win_rate=("won", "mean"),
        model_p=("p_model", "mean"), market_fair_p=("p_fair", "mean"),
        roi=("profit_units", "mean"),
    )
    res["_bets"] = bt
    return res


# --------------------------------------------------------------------------
# Deliverable 3: model vs market calibration
# --------------------------------------------------------------------------
def calibration_vs_market(df: pd.DataFrame, preds: pd.DataFrame, n_bins: int = 10) -> dict:
    """Compare model P(YRFI) to the market's de-vigged P(YRFI), and (if outcomes
    are present) to realized YRFI rate, bucketed by model probability.

    Returns a per-bin frame: model_p, market_fair_p, realized_p, n.
    A model well above the market AND above realized in a bin = overconfidence
    (the adverse-selection wound). Market vs realized gauges market sharpness.
    """
    pred_cols = ["game_key", "p_yrfi"] + (["yrfi"] if "yrfi" in preds.columns else [])
    m = df.merge(preds[pred_cols], on="game_key", how="inner", suffixes=("", "_pred"))
    if "yrfi_pred" in m.columns:
        m["yrfi"] = m["yrfi"].fillna(m["yrfi_pred"]) if "yrfi" in m.columns else m["yrfi_pred"]
    if "yrfi" not in m.columns:
        m["yrfi"] = np.nan
    m = m.dropna(subset=["p_yrfi", "fair_yrfi"]).copy()
    m["bin"] = pd.cut(m["p_yrfi"], np.linspace(0, 1, n_bins + 1))
    agg = {
        "model_p": ("p_yrfi", "mean"),
        "market_fair_p": ("fair_yrfi", "mean"),
        "n": ("p_yrfi", "size"),
    }
    if m["yrfi"].notna().sum() > 0:
        agg["realized_p"] = ("yrfi", "mean")
    table = m.groupby("bin", observed=True).agg(**agg)
    out = {"by_model_bin": table}
    # Aggregate divergence metrics.
    out["model_vs_market_mae"] = (m["p_yrfi"] - m["fair_yrfi"]).abs().mean()
    if m["yrfi"].notna().sum() > 0:
        out["model_brier"] = ((m["p_yrfi"] - m["yrfi"]) ** 2).mean()
        out["market_brier"] = ((m["fair_yrfi"] - m["yrfi"]) ** 2).mean()
    return out


# --------------------------------------------------------------------------
# CLI -- runs whatever the available inputs allow.
# --------------------------------------------------------------------------
def _print_section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main() -> None:
    ap = argparse.ArgumentParser(description="NRFI/YRFI historical-odds analysis")
    ap.add_argument("--odds", default="yrfi_master.csv")
    ap.add_argument("--features", default=None,
                    help="model_features.csv for independent outcomes (optional; "
                         "preds from gen_nrfi_preds already carries yrfi)")
    ap.add_argument("--preds", default=None,
                    help="CSV with game_key,p_yrfi[,yrfi] (from gen_nrfi_preds)")
    ap.add_argument("--devig", default="shin", choices=["proportional", "shin", "log"])
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--price", default="best", choices=["best", "consensus"])
    ap.add_argument("--year", type=int, default=None,
                    help="restrict to one season (use the post-training year for the "
                         "honest out-of-sample read)")
    args = ap.parse_args()

    df = load_yrfi_master(args.odds, devig_method=args.devig)
    if args.year:
        df = df[df["year"] == args.year].copy()
    print(f"Loaded {len(df)} games, {df['year'].min()}-{df['year'].max()}.")

    _print_section("Deliverable 2: market baseline (consensus de-vig)")
    for k, v in market_baseline(df).items():
        print(f"\n-- {k} --")
        print(v)

    if args.features:
        df = attach_outcomes(df, args.features)

    if args.preds and os.path.exists(args.preds):
        preds = pd.read_csv(args.preds)
        has_outcomes = ("yrfi" in preds.columns and preds["yrfi"].notna().any()) or \
                       (df["yrfi"].notna().any() if "yrfi" in df.columns else False)

        _print_section("Deliverable 3: model vs market calibration")
        for k, v in calibration_vs_market(df, preds).items():
            print(f"\n-- {k} --")
            print(v)

        if has_outcomes:
            _print_section("Deliverable 1: backtest vs real lines")
            res = backtest_vs_lines(df, preds, min_edge=args.min_edge, price=args.price)
            for k, v in res.items():
                if k.startswith("_"):
                    continue
                print(f"\n-- {k} --")
                print(v)
        else:
            print("\n[skip] backtest needs realized outcomes "
                  "(yrfi in --preds, or pass --features model_features.csv).")
    else:
        print("\n[skip] calibration + backtest need --preds "
              "(run gen_nrfi_preds.py in Cloud Shell first).")


if __name__ == "__main__":
    main()
