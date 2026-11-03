"""soccer.game.backtest -- walk-forward Dixon-Coles vs the Pinnacle close (the Phase 1 promotion gate).

    python -m soccer.game.backtest --leagues epl --seasons 2021-2025 [--data-dir DIR] [--min-train 60]

Replays the engine strictly as-of each match date: ratings for a date are fit ONLY on matches before that date
(expanding window, refit per match date), then every match that day is priced from the scoreline matrix.
football-data.co.uk supplies both the history and the market reference: Pinnacle CLOSING 1X2 (PSCH/PSCD/PSCA)
and 2.5 totals (PC>2.5 / PC<2.5), de-vigged with devig_three_way(method="power") / devig_two_way.

Reported, per league and pooled:
  * 1X2 multiclass Brier + log-loss, model vs Pinnacle close (the model must not be embarrassed by the close;
    the promotion bar is set on CLV, not on beating Pinnacle outright)
  * Totals 2.5 Brier + log-loss, model vs close
  * the logit-blend coefficient c (t-stat): logit(y) = a + logit(p_market) + c * (logit(p_model) - logit(p_market))
    on home wins and on Over 2.5 -- c > 0 significantly means the model adds information the close lacks
  * flat-stake ROI of backing the model's biggest edge (model prob - closing fair prob > 3pp) at Pinnacle
    OPENING prices (PSH/PSD/PSA) and at market MAX prices (MaxCH/MaxCD/MaxCA), plus the mean price CLV of those
    bets (entry decimal / closing decimal - 1)

With --data-dir, season CSVs are read from local files named {league}_{season_code}.csv (the ingest cache
naming), so the whole run is offline; without it the seasons are pulled via soccer.data.ingest.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict

import numpy as np

from mlb_core.odds.utils import devig_three_way, devig_two_way
from soccer.data import ingest
from soccer.game import dixon_coles
from soccer.game.matrix_pricer import price_match

log = logging.getLogger("soccer.backtest")

EDGE_THRESHOLD = 0.03
_EPS = 1e-6


def _clip(p: float) -> float:
    return min(1.0 - _EPS, max(_EPS, p))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _blend_c(d: np.ndarray, logit_mkt: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    """Fit logit(y) = a + logit_mkt + c * d by Newton (a, c free); return (c, t-stat of c)."""
    X = np.column_stack([np.ones_like(d), d])
    beta = np.zeros(2)
    for _ in range(50):
        eta = logit_mkt + X @ beta
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1.0 - p), 1e-9, None)
        grad = X.T @ (y - p)
        hess = (X * w[:, None]).T @ X
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            return None, None
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    eta = logit_mkt + X @ beta
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1.0 - p), 1e-9, None)
    hess = (X * w[:, None]).T @ X
    try:
        cov = np.linalg.inv(hess)
        se = math.sqrt(max(cov[1, 1], 0.0))
    except np.linalg.LinAlgError:
        return float(beta[1]), None
    return float(beta[1]), (float(beta[1] / se) if se > 0 else None)


class _Acc:
    def __init__(self):
        self.n = 0
        self.brier_model = 0.0
        self.brier_mkt = 0.0
        self.ll_model = 0.0
        self.ll_mkt = 0.0
        self.tot_n = 0
        self.tot_brier_model = 0.0
        self.tot_brier_mkt = 0.0
        self.tot_ll_model = 0.0
        self.tot_ll_mkt = 0.0
        self.home_d: list[float] = []
        self.home_lm: list[float] = []
        self.home_y: list[float] = []
        self.tot_d: list[float] = []
        self.tot_lm: list[float] = []
        self.tot_y: list[float] = []
        self.bets = 0
        self.profit_open = 0.0
        self.profit_max = 0.0
        self.clv: list[float] = []


def _score_match(acc: _Acc, match: dict, probs: dict) -> None:
    odds = match.get("odds") or {}
    psch, pscd, psca = odds.get("psch"), odds.get("pscd"), odds.get("psca")
    if not psch or not pscd or not psca:
        return
    fair = devig_three_way(1.0 / psch, 1.0 / pscd, 1.0 / psca, method="power")
    model = (probs["p_home"], probs["p_draw"], probs["p_away"])
    hg, ag = match["home_goals"], match["away_goals"]
    y = (1.0, 0.0, 0.0) if hg > ag else ((0.0, 1.0, 0.0) if hg == ag else (0.0, 0.0, 1.0))
    acc.n += 1
    acc.brier_model += sum((p - t) ** 2 for p, t in zip(model, y))
    acc.brier_mkt += sum((p - t) ** 2 for p, t in zip(fair, y))
    acc.ll_model += -math.log(_clip(model[y.index(1.0)]))
    acc.ll_mkt += -math.log(_clip(fair[y.index(1.0)]))
    acc.home_d.append(_logit(model[0]) - _logit(fair[0]))
    acc.home_lm.append(_logit(fair[0]))
    acc.home_y.append(y[0])

    pc_o, pc_u = odds.get("pc_over25"), odds.get("pc_under25")
    tot = (probs.get("totals") or {}).get("2.5")
    if pc_o and pc_u and tot and tot.get("p_over") is not None:
        f_over, _ = devig_two_way(1.0 / pc_o, 1.0 / pc_u, "proportional")
        over = 1.0 if hg + ag > 2.5 else 0.0
        acc.tot_n += 1
        acc.tot_brier_model += (tot["p_over"] - over) ** 2
        acc.tot_brier_mkt += (f_over - over) ** 2
        acc.tot_ll_model += -(over * math.log(_clip(tot["p_over"])) + (1 - over) * math.log(_clip(1 - tot["p_over"])))
        acc.tot_ll_mkt += -(over * math.log(_clip(f_over)) + (1 - over) * math.log(_clip(1 - f_over)))
        acc.tot_d.append(_logit(tot["p_over"]) - _logit(f_over))
        acc.tot_lm.append(_logit(f_over))
        acc.tot_y.append(over)

    edges = [m - f for m, f in zip(model, fair)]
    best = int(np.argmax(edges))
    if edges[best] > EDGE_THRESHOLD:
        open_px = (odds.get("psh"), odds.get("psd"), odds.get("psa"))[best]
        max_px = (odds.get("maxch"), odds.get("maxcd"), odds.get("maxca"))[best]
        close_px = (psch, pscd, psca)[best]
        won = y[best] == 1.0
        acc.bets += 1
        if open_px:
            acc.profit_open += (open_px - 1.0) if won else -1.0
            acc.clv.append(open_px / close_px - 1.0)
        if max_px:
            acc.profit_max += (max_px - 1.0) if won else -1.0


def walk_forward(matches: list[dict], elo: dict[str, float] | None = None, min_train: int = 60,
                 fit_iterations: int = 1200) -> _Acc:
    """One league's matches (any order) -> accumulated scores. Ratings refit per match date on prior matches."""
    acc = _Acc()
    by_date: dict = defaultdict(list)
    for m in matches:
        by_date[m["date"]].append(m)
    history: list[dict] = []
    for day in sorted(by_date):
        todays = by_date[day]
        if len(history) >= min_train:
            try:
                ratings = dixon_coles.fit_ratings(history, day, elo=elo, iterations=fit_iterations)
            except ValueError:
                ratings = None
            if ratings is not None:
                for m in todays:
                    lam = ratings.expected_goals(m["home"], m["away"])
                    if lam is None:
                        continue
                    _, probs = price_match(lam[0], lam[1], ratings.rho)
                    _score_match(acc, m, probs)
        history.extend(todays)
    return acc


def _summarize(acc: _Acc) -> dict:
    def _blend(d, lm, y):
        if len(d) < 30:
            return {"c": None, "t": None}
        c, t = _blend_c(np.array(d), np.array(lm), np.array(y))
        return {"c": None if c is None else round(c, 3), "t": None if t is None else round(t, 2)}
    n = max(acc.n, 1)
    tn = max(acc.tot_n, 1)
    return {
        "matches_scored": acc.n,
        "brier_1x2_model": round(acc.brier_model / n, 4) if acc.n else None,
        "brier_1x2_pinnacle_close": round(acc.brier_mkt / n, 4) if acc.n else None,
        "logloss_1x2_model": round(acc.ll_model / n, 4) if acc.n else None,
        "logloss_1x2_pinnacle_close": round(acc.ll_mkt / n, 4) if acc.n else None,
        "totals_matches": acc.tot_n,
        "brier_totals25_model": round(acc.tot_brier_model / tn, 4) if acc.tot_n else None,
        "brier_totals25_pinnacle_close": round(acc.tot_brier_mkt / tn, 4) if acc.tot_n else None,
        "logloss_totals25_model": round(acc.tot_ll_model / tn, 4) if acc.tot_n else None,
        "logloss_totals25_pinnacle_close": round(acc.tot_ll_mkt / tn, 4) if acc.tot_n else None,
        "blend_home_win": _blend(acc.home_d, acc.home_lm, acc.home_y),
        "blend_over25": _blend(acc.tot_d, acc.tot_lm, acc.tot_y),
        "edge_bets": acc.bets,
        "roi_at_pinnacle_open": round(acc.profit_open / acc.bets, 4) if acc.bets else None,
        "roi_at_market_max": round(acc.profit_max / acc.bets, 4) if acc.bets else None,
        "mean_price_clv_open_vs_close": round(float(np.mean(acc.clv)), 4) if acc.clv else None,
    }


def load_matches(leagues: list[str], seasons: list[int], data_dir: str | None = None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for league in leagues:
        matches: list[dict] = []
        for year in seasons:
            if data_dir:
                path = os.path.join(data_dir, f"{league}_{ingest.season_code(year)}.csv")
                with open(path, encoding="utf-8-sig") as fh:
                    matches.extend(ingest.parse_football_data_csv(fh.read(), league))
            else:
                matches.extend(ingest.fetch_football_data_csv(league, year))
        out[league] = sorted(matches, key=lambda m: m["date"])
    return out


def run_backtest(leagues: list[str], seasons: list[int], data_dir: str | None = None,
                 elo: dict[str, float] | None = None, min_train: int = 60) -> dict:
    by_league = load_matches(leagues, seasons, data_dir)
    report: dict = {"leagues": {}, "validated": False}
    pooled = _Acc()
    for league, matches in by_league.items():
        acc = walk_forward(matches, elo=elo, min_train=min_train)
        report["leagues"][league] = _summarize(acc)
        for attr in ("n", "brier_model", "brier_mkt", "ll_model", "ll_mkt", "tot_n", "tot_brier_model",
                     "tot_brier_mkt", "tot_ll_model", "tot_ll_mkt", "bets", "profit_open", "profit_max"):
            setattr(pooled, attr, getattr(pooled, attr) + getattr(acc, attr))
        pooled.home_d += acc.home_d; pooled.home_lm += acc.home_lm; pooled.home_y += acc.home_y
        pooled.tot_d += acc.tot_d; pooled.tot_lm += acc.tot_lm; pooled.tot_y += acc.tot_y
        pooled.clv += acc.clv
    report["pooled"] = _summarize(pooled)
    return report


def _parse_seasons(raw: str) -> list[int]:
    if "-" in raw:
        lo, hi = raw.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in raw.split(",") if s.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Soccer Dixon-Coles walk-forward backtest vs Pinnacle close")
    ap.add_argument("--leagues", default="epl", help="comma-separated league keys (epl, ucl, ...)")
    ap.add_argument("--seasons", default="2021-2025", help="start-year range '2021-2025' or list '2023,2024'")
    ap.add_argument("--data-dir", default=None, help="local CSV dir ({league}_{season_code}.csv); default: fetch")
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--out", default=None, help="optional JSON report path")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    report = run_backtest([s.strip() for s in args.leagues.split(",") if s.strip()],
                          _parse_seasons(args.seasons), data_dir=args.data_dir, min_train=args.min_train)
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
