"""
nba.games.soft_lines -- audit of SOFT (off-consensus) book quotes on NBA spreads and totals, no game model involved.

Idea: books quote different main lines for the same game and some sit on a stale number (a quote last updated 12 h before tip
while the market moved). Using the other books' consensus as the yardstick, a quote at line `l` with price `dec` is +EV if
fair_p(l) * dec > 1, where fair_p(l) comes from a normal margin/total distribution centred so that the consensus line and its
de-vigged probability are reproduced (sigma = the empirical sd of outcome around the market, 13.6 spread / 18.0 total), with an
integer-margin continuity correction so whole-number lines carry a push probability.

The validator is REALIZED ROI on settled games, not the estimated EV: the estimate only chooses which quotes to bet.
Data = BettingPros last quote per book per day (a closing proxy, stamped a median +1 min after tip). A stale quote that was still
posted at the close was bettable earlier; this is NOT a claim that it was bettable at the last moment, nor that limits allow it.

    PYTHONPATH=. python -m nba.games.soft_lines --base-data <dir> --games <eval parquet from evaluate --out>
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd
from scipy.stats import norm

from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob

SIGMA = {"spread": 13.6, "total": 18.0}
MIN_BOOKS = 6
THRESHOLDS = (0.0, 0.02, 0.04, 0.06, 0.08)


def quotes(bp: pd.DataFrame, market: str) -> pd.DataFrame:
    """One row per (event, book): the book's quoted line and both sides' decimal price. spread: line = HOME handicap, a = home,
    b = away; total: line = points total, a = over, b = under. Only real books, pre-game stamps (<= 10 min after tip)."""
    b = bp[(bp["market"] == market) & (bp["book_kind"] == "book") & (~bp["is_open"].astype(bool))].copy()
    gap = (pd.to_datetime(b["updated"], utc=True, errors="coerce")
           - pd.to_datetime(b["scheduled"], utc=True, errors="coerce")).dt.total_seconds() / 60.0
    b["age_min"] = -gap
    b = b[~(gap > 10)]
    b["odds"] = b["odds"].astype(float)
    sa, sb = ("home", "away") if market == "spread" else ("over", "under")
    a = b[b["side"] == sa].drop_duplicates(["event_id", "book_id", "line"], keep="last")
    o = b[b["side"] == sb].copy()
    if market == "spread":
        o["line"] = -o["line"]                      # the away side is quoted at -home_line
    o = o.drop_duplicates(["event_id", "book_id", "line"], keep="last")
    j = a.merge(o[["event_id", "book_id", "line", "odds"]], on=["event_id", "book_id", "line"], suffixes=("_a", "_b"))
    j = j[(j["odds_a"].abs() >= 100) & (j["odds_b"].abs() >= 100)]
    j["dec_a"] = j["odds_a"].map(american_to_decimal)
    j["dec_b"] = j["odds_b"].map(american_to_decimal)
    pa, pb = j["odds_a"].map(american_to_implied_prob), j["odds_b"].map(american_to_implied_prob)
    j["p_a_devig"] = pa / (pa + pb)
    return j[["event_id", "book", "book_id", "line", "dec_a", "dec_b", "p_a_devig", "age_min", "home", "away", "scheduled"]]


def _win_push(mu: np.ndarray, sigma: float, thr: np.ndarray):
    """For X ~ N(mu, sigma) on integers, side A wins iff X > thr: returns (P(win_A), P(push), P(win_B)); push needs integer thr."""
    k = np.floor(thr) + 1.0
    p_a = 1.0 - norm.cdf((k - 0.5 - mu) / sigma)
    is_int = np.isclose(thr, np.round(thr))
    p_push = np.where(is_int, norm.cdf((thr + 0.5 - mu) / sigma) - norm.cdf((thr - 0.5 - mu) / sigma), 0.0)
    return p_a, p_push, 1.0 - p_a - p_push


def audit(q: pd.DataFrame, games: pd.DataFrame, market: str) -> pd.DataFrame:
    """q = quotes(); games = frame with event_id, margin, total. -> one row per (quote, side) with est EV and realized return."""
    sigma = SIGMA[market]
    q = q.copy()
    n = q.groupby("event_id")["book_id"].transform("nunique")
    q = q[n >= MIN_BOOKS].copy()
    # consensus: modal line, median de-vigged P(side a) among books on that line
    mode = q.groupby("event_id")["line"].agg(lambda s: s.value_counts().index[0])
    q["mode_line"] = q["event_id"].map(mode)
    cons = q[q["line"] == q["mode_line"]].groupby("event_id")["p_a_devig"].median().rename("p_cons")
    q = q.join(cons, on="event_id").dropna(subset=["p_cons"])
    q["p_cons"] = q["p_cons"].clip(0.02, 0.98)
    # centre the distribution so P(a wins at mode_line) = p_cons   (spread: a=home covers iff margin > -line; total: over iff total > line)
    thr_mode = (-q["mode_line"] if market == "spread" else q["mode_line"]).values
    z = norm.ppf(q["p_cons"].values)
    mu = thr_mode + sigma * z
    thr = (-q["line"] if market == "spread" else q["line"]).values
    pa, pp, pb = _win_push(mu, sigma, thr)
    q["ev_a"], q["ev_b"] = pa * (q["dec_a"] - 1) - pb - 0.0, pb * (q["dec_b"] - 1) - pa
    q["gap"] = q["line"] - q["mode_line"]
    g = games.set_index("event_id")
    q = q[q["event_id"].isin(g.index)]
    x = (g.loc[q["event_id"], "margin"] if market == "spread" else g.loc[q["event_id"], "total"]).values
    thr = (-q["line"] if market == "spread" else q["line"]).values
    a_win, push = x > thr, np.isclose(x, thr)
    q["ret_a"] = np.where(push, 0.0, np.where(a_win, q["dec_a"] - 1, -1.0))
    q["ret_b"] = np.where(push, 0.0, np.where(~a_win, q["dec_b"] - 1, -1.0))
    long = pd.concat([
        q.assign(side="a", ev=q["ev_a"], ret=q["ret_a"])[["event_id", "book", "line", "mode_line", "gap", "age_min", "side", "ev", "ret"]],
        q.assign(side="b", ev=q["ev_b"], ret=q["ret_b"])[["event_id", "book", "line", "mode_line", "gap", "age_min", "side", "ev", "ret"]]])
    return long


def summarize(long: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for t in THRESHOLDS:
        s = long[long["ev"] >= t]
        n = len(s)
        rows.append({"est_ev>=": t, "n_bets": n, "games": s["event_id"].nunique(),
                     "mean_est_ev": s["ev"].mean(), "realized_roi": s["ret"].mean(),
                     "se": s["ret"].std(ddof=1) / np.sqrt(n) if n > 1 else np.nan})
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame) -> str:
    d = df.round(4).astype(object).where(df.notna(), "")
    rows = ["| " + " | ".join(map(str, d.columns)) + " |", "|" + "---|" * len(d.columns)]
    return "\n".join(rows + ["| " + " | ".join(str(v) for v in r) + " |" for r in d.values.tolist()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-data", required=True)
    ap.add_argument("--games", required=True, help="parquet written by nba.games.evaluate --out (needs event_id, margin, total)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    os.environ["MLB_BASE_DATA"] = a.base_data
    from nba.games import bp_games
    bp = bp_games.read_games()
    games = pd.read_parquet(a.games).dropna(subset=["event_id"])
    out = ["# NBA soft-line audit (off-consensus book quotes), BettingPros 2021-26", ""]
    for market in ("spread", "total"):
        long = audit(quotes(bp, market), games, market)
        out += [f"## {market}", f"- quote-sides evaluated: {len(long)}; games: {long['event_id'].nunique()}", "",
                "### Realized ROI by estimated-EV threshold", _md(summarize(long)), ""]
        s = long[long["ev"] >= 0.04]
        if len(s):
            by_book = s.groupby("book")["ret"].agg(n="size", roi="mean").sort_values("n", ascending=False).head(12)
            out += ["### Estimated EV >= 4% by book", _md(by_book.reset_index()), ""]
            s = s.assign(abs_gap=s["gap"].abs().clip(upper=3), stale=pd.cut(s["age_min"], [-1, 30, 180, 720, 1e9],
                                                                          labels=["<30m", "30m-3h", "3-12h", ">12h"]))
            out += ["### by |line gap to consensus| (pts)", _md(s.groupby("abs_gap")["ret"].agg(n="size", roi="mean").reset_index()), "",
                    "### by quote age before tip", _md(s.groupby("stale", observed=True)["ret"].agg(n="size", roi="mean").reset_index()), ""]
    txt = "\n".join(out)
    print(txt)
    if a.out:
        open(a.out, "w").write(txt)


if __name__ == "__main__":
    main()
