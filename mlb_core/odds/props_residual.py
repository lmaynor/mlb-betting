"""Market-RESIDUAL model for player props (NBA / NFL), evaluated out of sample against the de-vigged market. UNVALIDATED.

The question this answers: given the de-vigged consensus P(over) of the books, can ANY cheap, hard-regularised model of
(engine projection, player form, line movement, book dispersion) learn a residual that lowers log loss vs the market on seasons
it never saw -- and is that residual worth money once line shopping is subtracted?

    p = sigmoid( logit(p_mkt) + f(x) )        # the market logit is an OFFSET: the model can only learn the residual

f is a ridge logistic (own IRLS with an offset; per-stat slopes shrunk toward the pooled slope) or a shallow xgboost with
`base_margin = logit(p_mkt)`. Nothing here is wired into the serving board or the Official tier.

PROTOCOL (the point of this module)
  * strictly chronological expanding window by season. For held-out season s the final fit uses seasons < s; its hyper-
    parameters are chosen by training on seasons < s-1 and scoring season s-1 (inner validation). Season s is touched once.
  * `fit_predict_fold` REFUSES to run when any training row is dated on or after the first test row (the leakage guard);
    `tests/test_props_residual.py` also garbles every row on / after a date and checks that the predictions for that date do
    not move.
  * paired log loss vs the market with DATE-CLUSTERED standard errors, Brier skill vs the market, ECE, residual-calibration
    slope (does the model's gap to the market predict the realised gap?), edge-bucket ROI at the MEDIAN and the BEST book
    price, and the NO-MODEL line-shopping control: ROI of the same buckets formed from the market probability alone, plus the
    ROI of each bet EXCESS over what the market itself says the bet was worth ("market-expected ROI"): that excess is the
    selection alpha, the rest is a best-quote artifact.

The history is ONE last-quote snapshot per day (no CLV). Nothing here can say more than UNDETERMINED / NO_EDGE unless the
out-of-sample numbers clear the bars in `BARS`.

ASCII only. All odds math is mlb_core.odds.utils / bettingpros_loader (single definitions).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mlb_core.odds import bettingpros_loader as L
from mlb_core.odds.utils import american_to_decimal

CLIP = (0.005, 0.995)
EDGE_BUCKETS = ((0.02, 0.04), (0.04, 0.06), (0.06, 0.08), (0.08, 9.0))
SHARP_BOOKS = {"novig": "p_novig", "prophetx": "p_prophetx", "pinnacle": "p_pinnacle"}
# Bars (set after the development folds were seen and BEFORE the held-out season was scored). A residual "beats the market" only
# when ALL hold on the held-out season: (1) paired log loss vs the market has date-clustered z <= z_logloss; (2) it also beats the
# CALIBRATION-ONLY model (per-stat intercept + slope on logit(p_mkt), i.e. just undoing the market's own level bias) at
# z <= z_vs_calib, so the gain is information and not recalibration; (3) the residual-calibration slope is positive with
# t >= t_slope; (4) at the BEST price, edge >= 2%, the model's bets earn more than the no-model line-shopping control of the SAME
# number of bets (top-K by market edge alone) with a date-clustered CI of the paired difference above 0.
BARS = {"z_logloss": -2.58, "z_vs_calib": -1.96, "t_slope": 2.0}
K_SHRINK = 10.0                  # games of prior-season mean the season average is shrunk toward
FEATURES = ["eng_gap", "z_eng", "z_season", "z_l10", "z_l5", "line_move_z", "open_shift", "has_open", "log_nb", "disp",
            "hold", "m", "min_ratio"]
KEY_FEATURES = ["eng_gap", "z_eng", "z_season", "z_l10"]      # get a per-stat slope (shrunk toward the pooled one)
REQUIRED = ["date", "game_date", "season", "stat", "line", "over", "p_mkt", "n_books", "best_over_odds", "best_under_odds",
            "med_over_odds", "med_under_odds", "eng_mu", "eng_p"]


# ------------------------------------------------------------------------------------------------------------ numerics
def logit(p) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), *CLIP)
    return np.log(p / (1.0 - p))


def sigmoid(z) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def logloss_vec(y, p) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), *CLIP)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def cluster_mean_se(x, clusters) -> tuple:
    """Mean of per-row values and its cluster-robust SE (CR1: G/(G-1)) with whole clusters (game dates) as units."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    codes, _ = pd.factorize(np.asarray(clusters))
    g = int(codes.max()) + 1
    if g < 2:
        return float(x.mean()), float("nan")
    m = float(x.mean())
    s = np.bincount(codes, weights=x - m, minlength=g)
    return m, math.sqrt(float(np.sum(s ** 2)) * g / (g - 1)) / n


def to_american(dec) -> np.ndarray:
    dec = np.asarray(dec, dtype=float)
    return np.where(dec >= 2.0, (dec - 1.0) * 100.0, -100.0 / (dec - 1.0))


def dec_odds(american) -> np.ndarray:
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, a / 100.0 + 1.0, 100.0 / np.abs(a) + 1.0)


def implied(american) -> np.ndarray:
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, 100.0 / (a + 100.0), np.abs(a) / (np.abs(a) + 100.0))


# ------------------------------------------------------------------------------------------------- market-side extras
def market_extras(long_df: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    """Per consensus row (event_id, player_id, line): MEDIAN American price per side across the two-sided reference books at
    that line, the opening line / opening fair P(over) (median over the books' own opening quotes) and the fair P(over) of the
    sharp books that quote (Novig / ProphetX / Pinnacle; Novig and ProphetX only exist in the feed from the 2025 season).

    `long_df` = ONE market's long BettingPros table; `cons` = its consensus rows (bettingpros_loader.consensus_from_long). The
    quote filter and the modal line are exactly the consensus' own, so the rows line up one-to-one."""
    keyc = ["event_id", "player_id", "line"]
    out_cols = keyc + ["med_over_odds", "med_under_odds", "open_line", "open_p"] + list(SHARP_BOOKS.values())
    if long_df is None or len(long_df) == 0 or cons is None or len(cons) == 0:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in out_cols})
    c = cons[keyc].drop_duplicates()
    c = c.assign(event_id=pd.to_numeric(c["event_id"], errors="coerce").astype("Int64"),
                 player_id=pd.to_numeric(c["player_id"], errors="coerce").astype("Int64")).dropna()
    d = L.filter_reference_quotes(long_df)
    d = d.assign(event_id=pd.to_numeric(d["event_id"], errors="coerce").astype("Int64"),
                 player_id=pd.to_numeric(d["player_id"], errors="coerce").astype("Int64")).dropna(subset=["event_id", "player_id"])
    d = d.sort_values("ts_updated", kind="mergesort", na_position="first")
    d = d.drop_duplicates(["event_id", "player_id", "line", "book_id", "side_n"], keep="last")
    key = ["event_id", "player_id", "line", "book_id"]
    o = d[d["side_n"] == "over"].set_index(key)[["odds", "book"]].rename(columns={"odds": "o"})
    u = d[d["side_n"] == "under"].set_index(key)[["odds"]].rename(columns={"odds": "u"})
    w = o.join(u, how="inner").reset_index().merge(c, on=keyc, how="inner")
    w["dec_o"] = w["o"].map(american_to_decimal)
    w["dec_u"] = w["u"].map(american_to_decimal)
    w["fair"] = L._fair_over(w["o"], w["u"])
    g = w.groupby(keyc)
    out = pd.DataFrame({"med_dec_o": g["dec_o"].median(), "med_dec_u": g["dec_u"].median()})
    out["med_over_odds"] = to_american(out["med_dec_o"].to_numpy())
    out["med_under_odds"] = to_american(out["med_dec_u"].to_numpy())
    out = out[["med_over_odds", "med_under_odds"]]
    name = w["book"].astype(str).str.strip().str.lower()
    for bk, col in SHARP_BOOKS.items():
        s = w[name == bk].groupby(keyc)["fair"].median().rename(col)
        out = out.join(s, how="left")
    for col in SHARP_BOOKS.values():
        out[col] = out[col].astype(float) if col in out.columns else np.nan
    out = out.reset_index()
    # --- opening quotes (is_open rows; DFS and Kalshi books excluded): median opening line and median opening fair P(over)
    op = _opening(long_df)
    out = out.merge(op, on=["event_id", "player_id"], how="left")
    return out[out_cols]


def _opening(long_df: pd.DataFrame) -> pd.DataFrame:
    flag = long_df["is_open"].fillna(False).astype(bool) if "is_open" in long_df.columns else pd.Series(False, index=long_df.index)
    kind = long_df["book_kind"] if "book_kind" in long_df.columns else pd.Series("", index=long_df.index)
    d = long_df[flag | (kind == "open")]
    if len(d) == 0:
        return pd.DataFrame({"event_id": pd.Series(dtype="Int64"), "player_id": pd.Series(dtype="Int64"),
                             "open_line": pd.Series(dtype=float), "open_p": pd.Series(dtype=float)})
    dfs_ids = set(pd.to_numeric(long_df.loc[kind.isin(["dfs", "consensus"]), "book_id"], errors="coerce").dropna().astype(int))
    bid = pd.to_numeric(d["book_id"], errors="coerce")
    d = d[~bid.isin(dfs_ids) & (bid != L.KALSHI_BOOK_ID) & (bid != 0)]
    side = d["side"].astype(str).str.strip().str.lower()
    d = d.assign(side_n=np.where(side.isin(L.OVER_SIDES), "over", np.where(side.isin(L.UNDER_SIDES), "under", "")),
                 odds=pd.to_numeric(d["odds"], errors="coerce"), line=pd.to_numeric(d["line"], errors="coerce"),
                 event_id=pd.to_numeric(d["event_id"], errors="coerce").astype("Int64"),
                 player_id=pd.to_numeric(d["player_id"], errors="coerce").astype("Int64"))
    d = d[(d["side_n"] != "") & d["odds"].notna() & (d["odds"].abs() >= 100) & d["line"].notna()]
    d = d.dropna(subset=["event_id", "player_id"])
    key = ["event_id", "player_id", "line", "book_id"]
    d = d.sort_values("updated", kind="mergesort").drop_duplicates(key + ["side_n"], keep="last")
    o = d[d["side_n"] == "over"].set_index(key)[["odds"]].rename(columns={"odds": "o"})
    u = d[d["side_n"] == "under"].set_index(key)[["odds"]].rename(columns={"odds": "u"})
    w = o.join(u, how="inner").reset_index()
    if len(w) == 0:
        return pd.DataFrame({"event_id": pd.Series(dtype="Int64"), "player_id": pd.Series(dtype="Int64"),
                             "open_line": pd.Series(dtype=float), "open_p": pd.Series(dtype=float)})
    w["fair"] = L._fair_over(w["o"], w["u"])
    g = w.groupby(["event_id", "player_id"])
    return pd.DataFrame({"open_line": g["line"].median(), "open_p": g["fair"].median()}).reset_index()


# ------------------------------------------------------------------------------------------------- history features
HIST_COLS = ["n_season", "m_season", "n_prev", "m_prev", "m_l5", "m_l10", "m_l20", "sd_l20", "n_l20"]


def _day(dates: pd.Series) -> np.ndarray:
    return (pd.to_datetime(dates.astype(str).str[:10]).astype("datetime64[ns]").astype("int64") // 86_400_000_000_000).to_numpy()


def history_features(games: pd.DataFrame, props: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """Form features for each prop from games STRICTLY BEFORE the prop's date.

    games: pid, date (ISO str), season, value (the stat in the unit the line is quoted in) -- one row per played game.
    props: pid, date, season (the prop's own). The result is aligned with props.index and holds, from the player's last game
    dated < the prop's date: n_season / m_season (this season to date), n_prev / m_prev (the season before; only when that
    whole season is dated before the prop), and the L5 / L10 / L20 means, the L20 SD (>= 5 games) and the L20 count across
    seasons. Pure and deterministic: rows of `games` dated on or after a prop's date cannot reach it (merge_asof with
    allow_exact_matches=False on the day number)."""
    cols = [prefix + c for c in HIST_COLS]
    if len(props) == 0:
        return pd.DataFrame(columns=cols, index=props.index, dtype=float)
    g = games[["pid", "date", "season", "value"]].dropna(subset=["pid", "date", "value"]).copy()
    g["value"] = g["value"].astype(float)
    g["day"] = _day(g["date"])
    g = g.sort_values(["pid", "day"], kind="mergesort").reset_index(drop=True)
    by = g.groupby("pid", sort=False)["value"]
    g["m_l5"] = by.transform(lambda s: s.rolling(5, min_periods=1).mean())
    g["m_l10"] = by.transform(lambda s: s.rolling(10, min_periods=1).mean())
    g["m_l20"] = by.transform(lambda s: s.rolling(20, min_periods=1).mean())
    g["sd_l20"] = by.transform(lambda s: s.rolling(20, min_periods=5).std())
    g["n_l20"] = by.transform(lambda s: s.rolling(20, min_periods=1).count())
    sg = g.groupby(["pid", "season"], sort=False)["value"]
    g["n_season"] = sg.cumcount() + 1
    g["m_season"] = sg.cumsum() / g["n_season"]
    tot = g.groupby(["pid", "season"], sort=False).agg(_s=("value", "sum"), n_prev=("value", "count"), _last=("day", "max")).reset_index()
    tot["m_prev"] = tot["_s"] / tot["n_prev"]
    tot["season"] = tot["season"] + 1                          # keyed by the season whose "previous season" this is
    tot = tot.rename(columns={"season": "p_season"})[["pid", "p_season", "n_prev", "m_prev", "_last"]]
    p = props[["pid", "date", "season"]].copy()
    p["day"] = _day(p["date"])
    p["_i"] = np.arange(len(p))
    p = p.sort_values(["day", "pid"], kind="mergesort")
    g = g.sort_values(["day", "pid"], kind="mergesort")
    keep = ["pid", "day", "season", "n_season", "m_season", "m_l5", "m_l10", "m_l20", "sd_l20", "n_l20"]
    m = pd.merge_asof(p, g[keep].rename(columns={"season": "g_season"}), on="day", by="pid", allow_exact_matches=False, direction="backward")
    stale = m["g_season"].ne(m["season"])                      # the last prior game is from an earlier season: this season is at 0
    m.loc[stale, "n_season"] = 0.0
    m.loc[stale, "m_season"] = np.nan
    m = m.merge(tot, left_on=["pid", "season"], right_on=["pid", "p_season"], how="left")
    ok = m["_last"].notna() & (m["_last"] < m["day"])           # the whole previous season must precede the prop
    m.loc[~ok, "m_prev"] = np.nan
    m["n_prev"] = np.where(ok, m["n_prev"], 0.0)
    m = m.sort_values("_i")
    res = m[HIST_COLS].copy()
    res.index = props.index
    res.columns = cols
    return res


# --------------------------------------------------------------------------------------------------- feature matrix
def make_features(t: pd.DataFrame) -> pd.DataFrame:
    """The model's inputs for each prop, all in market-relative / unit-free form. Missing history becomes 0 (no signal) with
    a flag where the absence itself carries information (has_open)."""
    line = t["line"].astype(float).to_numpy()
    m = logit(t["p_mkt"])
    sd0 = 0.5 * np.sqrt(np.maximum(line, 0.5))
    sd20 = t["sd_l20"].astype(float).where(t["n_l20"].astype(float) >= 8).to_numpy()
    sd = np.where(np.isfinite(sd20), np.maximum(sd20, sd0), sd0)

    def z(mean) -> np.ndarray:
        mean = np.asarray(mean, dtype=float)
        return np.clip(np.where(np.isfinite(mean), (mean - line) / sd, 0.0), -4.0, 4.0)
    n = t["n_season"].astype(float).fillna(0.0).to_numpy()
    w = n / (n + K_SHRINK)
    prior = t["m_prev"].astype(float).where(t["n_prev"].astype(float).fillna(0) >= 8).fillna(t["m_l20"].astype(float)).fillna(pd.Series(line, index=t.index)).to_numpy()
    season_blend = w * t["m_season"].astype(float).fillna(0.0).to_numpy() + (1.0 - w) * prior
    ep = t["eng_p"].astype(float)
    eng_gap = np.where(ep.notna(), np.clip(logit(ep.fillna(0.5)) - m, -2.0, 2.0), 0.0)
    has_open = t["open_line"].notna().to_numpy().astype(float) if "open_line" in t.columns else np.zeros(len(t))
    ol = t["open_line"].astype(float).to_numpy() if "open_line" in t.columns else np.full(len(t), np.nan)
    op = t["open_p"].astype(float).to_numpy() if "open_p" in t.columns else np.full(len(t), np.nan)
    line_move = np.where(np.isfinite(ol), np.clip((line - ol) / sd, -3.0, 3.0), 0.0)
    open_shift = np.where(np.isfinite(op), np.clip(m - logit(np.where(np.isfinite(op), op, 0.5)), -1.5, 1.5), 0.0)
    hold = np.clip(implied(t["med_over_odds"]) + implied(t["med_under_odds"]) - 1.0, -0.05, 0.25)
    mr = np.zeros(len(t))
    if "min_l5" in t.columns and "min_season" in t.columns:
        ratio = t["min_l5"].astype(float) / t["min_season"].astype(float).replace(0, np.nan)
        mr = np.clip(np.where(np.isfinite(ratio), np.log(np.clip(ratio, 0.3, 3.0)), 0.0), -1.0, 1.0)
    x = pd.DataFrame({
        "eng_gap": eng_gap,
        "z_eng": z(t["eng_mu"]),
        "z_season": z(season_blend),
        "z_l10": z(t["m_l10"]),
        "z_l5": z(t["m_l5"]),
        "line_move_z": line_move,
        "open_shift": open_shift,
        "has_open": has_open,
        "log_nb": np.log(np.clip(t["n_books"].astype(float), 1, None)),
        "disp": np.clip(t["disp"].astype(float).fillna(0.0), 0, 0.2) * 10.0 if "disp" in t.columns else 0.0,
        "hold": np.nan_to_num(hold, nan=0.0) * 10.0,
        "m": m,
        "min_ratio": mr,
    }, index=t.index)
    return x[FEATURES]


# ------------------------------------------------------------------------------------------------------------ models
class OffsetRidge:
    """Logistic regression with a fixed offset and a per-column ridge penalty, fitted by damped Newton (IRLS).
    The offset is the market logit: the model can only learn the residual."""

    def __init__(self, penalty: np.ndarray, max_iter: int = 60):
        self.pen = np.concatenate([[1e-3], np.asarray(penalty, dtype=float)])
        self.max_iter = max_iter
        self.w: np.ndarray | None = None
        self.mu: np.ndarray | None = None
        self.sd: np.ndarray | None = None

    def _std(self, X: np.ndarray, fit: bool) -> np.ndarray:
        if fit:
            self.mu, self.sd = X.mean(0), np.maximum(X.std(0), 1e-6)
        return np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])

    def fit(self, X: np.ndarray, y: np.ndarray, offset: np.ndarray) -> "OffsetRidge":
        Xb = self._std(np.asarray(X, dtype=float), True)
        y = np.asarray(y, dtype=float)
        w = np.zeros(Xb.shape[1])

        def obj(wv):
            eta = offset + Xb @ wv
            return float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * np.sum(self.pen * wv ** 2))
        cur = obj(w)
        for _ in range(self.max_iter):
            p = sigmoid(offset + Xb @ w)
            grad = Xb.T @ (y - p) - self.pen * w
            H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(self.pen)
            step = np.linalg.solve(H, grad)
            t = 1.0
            while t > 1e-4:
                new = obj(w + t * step)
                if new <= cur + 1e-12:
                    break
                t *= 0.5
            w = w + t * step
            if abs(cur - new) < 1e-9 * max(1.0, abs(cur)):
                cur = new
                break
            cur = new
        self.w = w
        return self

    def margin(self, X: np.ndarray) -> np.ndarray:
        return self._std(np.asarray(X, dtype=float), False) @ self.w


def ridge_design(x: pd.DataFrame, stats: pd.Series, stat_list: list, lam_main: float, lam_stat: float) -> tuple:
    """Columns: every feature (penalty lam_main), a per-stat intercept and a per-stat slope on KEY_FEATURES (both lam_stat)."""
    cols, pen = [x[FEATURES].to_numpy(float)], [np.full(len(FEATURES), lam_main)]
    dummies = np.column_stack([(stats.to_numpy() == s).astype(float) for s in stat_list])
    cols.append(dummies)
    pen.append(np.full(len(stat_list), lam_stat))
    for f in KEY_FEATURES:
        cols.append(dummies * x[f].to_numpy(float)[:, None])
        pen.append(np.full(len(stat_list), lam_stat))
    return np.column_stack(cols), np.concatenate(pen)


def fit_ridge(train: pd.DataFrame, xtr: pd.DataFrame, stat_list: list, lam_main: float, lam_stat: float, restrict: list | None = None):
    X, pen = ridge_design(xtr, train["stat"], stat_list, lam_main, lam_stat)
    mdl = OffsetRidge(pen).fit(X, train["over"].to_numpy(float), logit(train["p_mkt"]))
    mdl.stat_list, mdl.lam = stat_list, (lam_main, lam_stat)
    return mdl


def predict_ridge(mdl, test: pd.DataFrame, xte: pd.DataFrame) -> np.ndarray:
    X, _ = ridge_design(xte, test["stat"], mdl.stat_list, *mdl.lam)
    return sigmoid(logit(test["p_mkt"]) + mdl.margin(X))


def fit_blend_c(train: pd.DataFrame, xtr: pd.DataFrame, stat_list: list):
    """The earlier (c-only) blend as a comparison model: offset + per-stat slope on eng_gap, nothing else."""
    dummies = np.column_stack([(train["stat"].to_numpy() == s).astype(float) for s in stat_list])
    X = dummies * xtr["eng_gap"].to_numpy(float)[:, None]
    mdl = OffsetRidge(np.full(len(stat_list), 1.0)).fit(X, train["over"].to_numpy(float), logit(train["p_mkt"]))
    mdl.stat_list = stat_list
    return mdl


def predict_blend_c(mdl, test: pd.DataFrame, xte: pd.DataFrame) -> np.ndarray:
    dummies = np.column_stack([(test["stat"].to_numpy() == s).astype(float) for s in mdl.stat_list])
    X = dummies * xte["eng_gap"].to_numpy(float)[:, None]
    return sigmoid(logit(test["p_mkt"]) + mdl.margin(X))


def _ab_design(t: pd.DataFrame, x: pd.DataFrame, stat_list: list) -> np.ndarray:
    dummies = np.column_stack([(t["stat"].to_numpy() == s).astype(float) for s in stat_list])
    return np.column_stack([dummies, dummies * x["m"].to_numpy(float)[:, None]])


def fit_calibration(train: pd.DataFrame, xtr: pd.DataFrame, stat_list: list):
    """CALIBRATION-ONLY comparison: a per-stat intercept and a per-stat slope on logit(p_mkt), no other input. How much of any gain
    is just undoing the market's own level / slope bias (the earlier (a, b) refit) rather than information."""
    mdl = OffsetRidge(np.full(2 * len(stat_list), 10.0)).fit(_ab_design(train, xtr, stat_list), train["over"].to_numpy(float), logit(train["p_mkt"]))
    mdl.stat_list = stat_list
    return mdl


def predict_calibration(mdl, test: pd.DataFrame, xte: pd.DataFrame) -> np.ndarray:
    return sigmoid(logit(test["p_mkt"]) + mdl.margin(_ab_design(test, xte, mdl.stat_list)))


def _gbdt_matrix(x: pd.DataFrame, stats: pd.Series, stat_list: list) -> np.ndarray:
    return np.column_stack([x[FEATURES].to_numpy(float)] + [(stats.to_numpy() == s).astype(float) for s in stat_list])


def fit_gbdt(train: pd.DataFrame, xtr: pd.DataFrame, stat_list: list, depth: int, rounds: int, nthread: int = 4, seed: int = 7):
    import xgboost as xgb
    X = _gbdt_matrix(xtr, train["stat"], stat_list)
    dm = xgb.DMatrix(X, label=train["over"].to_numpy(float), base_margin=logit(train["p_mkt"]))
    params = {"objective": "binary:logistic", "eta": 0.02, "max_depth": depth, "min_child_weight": 60.0, "subsample": 0.7,
              "colsample_bytree": 0.8, "reg_lambda": 100.0, "reg_alpha": 0.0, "tree_method": "hist", "nthread": nthread,
              "seed": seed, "verbosity": 0}
    bst = xgb.train(params, dm, num_boost_round=rounds)
    return {"bst": bst, "stat_list": stat_list}


def predict_gbdt(mdl, test: pd.DataFrame, xte: pd.DataFrame) -> np.ndarray:
    import xgboost as xgb
    X = _gbdt_matrix(xte, test["stat"], mdl["stat_list"])
    dm = xgb.DMatrix(X, base_margin=logit(test["p_mkt"]))
    return np.clip(mdl["bst"].predict(dm), *CLIP)


RIDGE_GRID = [(lm, ls) for lm in (1e4, 1e5, 1e6) for ls in (300.0, 3000.0, 30000.0, 300000.0)]   # 1e6 / 3e5 ~ "the market"
GBDT_GRID = [(d, r) for d in (1, 2) for r in (60, 200, 500)]


def assert_chronological(train: pd.DataFrame, test: pd.DataFrame) -> None:
    """Leakage guard: every training row is dated STRICTLY before the first test row. Fail closed."""
    if len(train) and len(test) and str(train["date"].max()) >= str(test["date"].min()):
        raise ValueError(f"leakage guard: training data reaches {train['date'].max()} but the test fold starts {test['date'].min()}")


@dataclass
class FoldResult:
    season: int
    n_train: int
    n_test: int
    chosen: dict
    valid: dict
    preds: pd.DataFrame = field(repr=False, default=None)


def _paired_d(y, p, pm) -> float:
    return float(np.mean(logloss_vec(y, p) - logloss_vec(y, pm)))


def fit_predict_fold(train: pd.DataFrame, test: pd.DataFrame, stat_list: list, hp: dict | None = None, nthread: int = 4) -> tuple:
    """Fit the candidate models on `train` (dated strictly before `test`) and predict `test`. `hp` holds the chosen
    hyper-parameters: {'ridge': (lam_main, lam_stat), 'gbdt': (depth, rounds)}. Returns (DataFrame of p_* columns, hp)."""
    assert_chronological(train, test)
    xtr, xte = make_features(train), make_features(test)
    out = pd.DataFrame(index=test.index)
    out["p_mkt"] = np.clip(test["p_mkt"].to_numpy(float), *CLIP)
    out["p_blend_c"] = predict_blend_c(fit_blend_c(train, xtr, stat_list), test, xte)
    out["p_calib"] = predict_calibration(fit_calibration(train, xtr, stat_list), test, xte)
    out["p_ridge"] = predict_ridge(fit_ridge(train, xtr, stat_list, *hp["ridge"]), test, xte)
    out["p_gbdt"] = predict_gbdt(fit_gbdt(train, xtr, stat_list, *hp["gbdt"], nthread=nthread), test, xte)
    return out, hp


def select_hyperparameters(train: pd.DataFrame, valid: pd.DataFrame, stat_list: list, nthread: int = 4) -> tuple:
    """Inner validation: fit every grid point on `train`, score `valid` (the season before the test season), pick the lowest
    log loss per family. Returns (hp, report). The 'auto' rule uses the validation season to decide whether to trust a model
    at all (a model that does not beat the market there is replaced by the market)."""
    assert_chronological(train, valid)
    xtr, xva = make_features(train), make_features(valid)
    y, pm = valid["over"].to_numpy(float), np.clip(valid["p_mkt"].to_numpy(float), *CLIP)
    rep = {"ridge": {}, "gbdt": {}}
    for hp in RIDGE_GRID:
        rep["ridge"][hp] = _paired_d(y, predict_ridge(fit_ridge(train, xtr, stat_list, *hp), valid, xva), pm)
    for hp in GBDT_GRID:
        rep["gbdt"][hp] = _paired_d(y, predict_gbdt(fit_gbdt(train, xtr, stat_list, *hp, nthread=nthread), valid, xva), pm)
    best = {k: min(v, key=v.get) for k, v in rep.items()}
    return {"ridge": best["ridge"], "gbdt": best["gbdt"]}, {k: {"best": best[k], "d_valid": rep[k][best[k]]} for k in rep}


def walk_forward(table: pd.DataFrame, test_seasons: list, nthread: int = 4, log=None) -> tuple:
    """Expanding-window folds. For test season s: hyper-parameters from (train < s-1, validate s-1), then a refit on < s and a
    single prediction of s. Returns (predictions frame aligned with `table` for the test rows, [FoldResult])."""
    seasons = sorted(table["season"].unique())
    stat_list = sorted(table["stat"].unique())
    parts, folds = [], []
    for s in test_seasons:
        prior = [x for x in seasons if x < s]
        if len(prior) < 2:
            continue
        tr_in, va = table[table["season"] < prior[-1]], table[table["season"] == prior[-1]]
        tr, te = table[table["season"] < s], table[table["season"] == s]
        if len(tr_in) < 1000 or len(va) < 500 or len(te) == 0:
            continue
        hp, rep = select_hyperparameters(tr_in, va, stat_list, nthread)
        pr, _ = fit_predict_fold(tr, te, stat_list, hp, nthread)
        xva_d = {k: v["d_valid"] for k, v in rep.items()}
        pr["p_auto"] = pr["p_mkt"]                                      # market unless a family beat it on the validation season
        fam = min(xva_d, key=xva_d.get)
        if xva_d[fam] < 0:
            pr["p_auto"] = pr["p_ridge" if fam == "ridge" else "p_gbdt"]
        folds.append(FoldResult(int(s), len(tr), len(te), {k: v["best"] for k, v in rep.items()}, xva_d, None))
        parts.append(pr)
        if log:
            log(f"fold {s}: train {len(tr)} test {len(te)} hp {hp} valid d {xva_d}")
    return (pd.concat(parts) if parts else pd.DataFrame()), folds


# ------------------------------------------------------------------------------------------------------------ metrics
def paired_logloss(y, p, pm, clusters) -> dict:
    d = logloss_vec(y, p) - logloss_vec(y, pm)
    m, se = cluster_mean_se(d, clusters)
    return {"d": m, "se": se, "z": m / se if se and se > 0 else float("nan"), "ll_model": float(logloss_vec(y, p).mean()),
            "ll_market": float(logloss_vec(y, pm).mean())}


def brier_skill(y, p, pm, clusters) -> dict:
    y, p, pm = (np.asarray(a, dtype=float) for a in (y, p, pm))
    bm, bk = (p - y) ** 2, (pm - y) ** 2
    d, se = cluster_mean_se(bm - bk, clusters)
    return {"bss": 1.0 - float(bm.mean() / bk.mean()), "se": se / float(bk.mean()) if se == se else float("nan"),
            "brier_model": float(bm.mean()), "brier_market": float(bk.mean())}


def ece(y, p, bins: int = 10) -> float:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y) < bins * 5:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    return float(sum(len(i) / len(y) * abs(y[i].mean() - p[i].mean()) for i in np.array_split(order, bins)))


def residual_slope(y, p, pm, clusters) -> dict:
    """Does the model's gap to the market predict the realised gap?  (y - p_mkt) = b * (p - p_mkt): b = 1 is a model that
    is right about its own residual, b = 0 is noise. Clustered by date."""
    y, p, pm = (np.asarray(a, dtype=float) for a in (y, p, pm))
    x, r = p - pm, y - pm
    sxx = float(np.sum(x * x))
    if sxx <= 0:
        return {"b": float("nan"), "se": float("nan"), "t": float("nan"), "sd_gap": 0.0}
    b = float(np.sum(x * r) / sxx)
    e = r - b * x
    codes, _ = pd.factorize(np.asarray(clusters))
    g = int(codes.max()) + 1
    s = np.bincount(codes, weights=x * e, minlength=g)
    se = math.sqrt(float(np.sum(s ** 2)) * g / max(g - 1, 1)) / sxx
    return {"b": b, "se": se, "t": b / se if se > 0 else float("nan"), "sd_gap": float(np.std(x))}


def residual_deciles(y, p, pm, k: int = 5) -> list:
    """Mean model gap and mean realised gap by quantile of the model gap (monotone realised gap = a real residual)."""
    y, p, pm = (np.asarray(a, dtype=float) for a in (y, p, pm))
    gap = p - pm
    order = np.argsort(gap, kind="mergesort")
    out = []
    for i in np.array_split(order, k):
        out.append({"n": int(len(i)), "mean_gap": float(gap[i].mean()), "real_gap": float((y[i] - pm[i]).mean())})
    return out


# ----------------------------------------------------------------------------------------------------------------- ROI
def _bets(t: pd.DataFrame, p: np.ndarray, price: str) -> pd.DataFrame:
    """One bet per prop on the side with the larger edge vs that side's price (best or median book). Columns: edge, profit,
    mkt_ev (what the market's own probability says the bet is worth), won, imp, date."""
    po, pu = np.asarray(p, dtype=float), 1.0 - np.asarray(p, dtype=float)
    oo, ou = t[f"{price}_over_odds"].to_numpy(float), t[f"{price}_under_odds"].to_numpy(float)
    e_o, e_u = po - implied(oo), pu - implied(ou)
    over = e_o >= e_u
    odds = np.where(over, oo, ou)
    y = t["over"].to_numpy(float)
    won = np.where(over, y == 1.0, y == 0.0)
    dec = dec_odds(odds)
    pm = t["p_mkt"].to_numpy(float)
    pm_side = np.where(over, pm, 1.0 - pm)
    return pd.DataFrame({"edge": np.where(over, e_o, e_u), "profit": np.where(won, dec - 1.0, -1.0), "mkt_ev": pm_side * dec - 1.0,
                         "won": won.astype(float), "imp": implied(odds), "date": t["game_date"].to_numpy(),
                         "stat": t["stat"].to_numpy()}, index=t.index).replace([np.inf, -np.inf], np.nan).dropna(subset=["edge", "profit"])


def _boot(sel_a: pd.DataFrame, sel_b: pd.DataFrame | None, n_boot: int = 600, seed: int = 11) -> dict:
    """Date-clustered bootstrap of mean ROI (sel_a), of its excess over the market-expected ROI, and (when sel_b is given) of
    the paired difference of mean ROI against sel_b, resampling the SAME dates for both rules."""
    dates = pd.Index(sorted(set(sel_a["date"]) | (set(sel_b["date"]) if sel_b is not None else set())))
    codes = {d: i for i, d in enumerate(dates)}
    k = len(dates)

    def agg(sel):
        if sel is None or len(sel) == 0:
            return np.zeros((k, 3))
        a = np.zeros((k, 3))
        idx = sel["date"].map(codes).to_numpy()
        np.add.at(a[:, 0], idx, 1.0)
        np.add.at(a[:, 1], idx, sel["profit"].to_numpy())
        np.add.at(a[:, 2], idx, sel["mkt_ev"].to_numpy())
        return a
    A, B = agg(sel_a), agg(sel_b)
    rng = np.random.default_rng(seed)
    ix = rng.integers(0, k, size=(n_boot, k))
    na, pa, ea = (A[ix, j].sum(1) for j in range(3))
    roi = pa / np.maximum(na, 1)
    exc = (pa - ea) / np.maximum(na, 1)
    out = {"roi_ci": (float(np.percentile(roi, 2.5)), float(np.percentile(roi, 97.5))),
           "excess_ci": (float(np.percentile(exc, 2.5)), float(np.percentile(exc, 97.5)))}
    if sel_b is not None:
        nb, pb = B[ix, 0].sum(1), B[ix, 1].sum(1)
        diff = roi - pb / np.maximum(nb, 1)
        out["diff_ci"] = (float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5)))
    return out


def roi_buckets(t: pd.DataFrame, p, price: str, control: np.ndarray | None = None, n_boot: int = 600) -> list:
    """Per edge bucket: n, ROI, hit rate, mean market-expected ROI, EXCESS (ROI - market-expected) and date-clustered CIs; with
    `control` (the market probability, i.e. no model at all) the control's ROI in the SAME bucket and the paired difference."""
    b = _bets(t, p, price)
    bc = _bets(t, control, price) if control is not None else None
    rows = []
    for lo, hi in EDGE_BUCKETS:
        s = b[(b["edge"] >= lo) & (b["edge"] < hi)]
        sc = bc[(bc["edge"] >= lo) & (bc["edge"] < hi)] if bc is not None else None
        row = {"lo": lo, "hi": hi, "n": int(len(s))}
        if len(s):
            bt = _boot(s, sc, n_boot)
            row.update({"roi": float(s["profit"].mean()), "hit": float(s["won"].mean()), "imp": float(s["imp"].mean()),
                        "mkt_ev": float(s["mkt_ev"].mean()), "excess": float((s["profit"] - s["mkt_ev"]).mean()), **bt})
        if sc is not None:
            row["ctrl_n"] = int(len(sc))
            row["ctrl_roi"] = float(sc["profit"].mean()) if len(sc) else float("nan")
        rows.append(row)
    return rows


def roi_threshold(t: pd.DataFrame, p, price: str, thr: float = 0.02, control: np.ndarray | None = None, n_boot: int = 600,
                  controls: dict | None = None) -> dict:
    """All bets with edge >= thr: the headline ROI and its excess over market-expected ROI. `control` = the market probability
    (the no-model line-shopping rule at the same threshold); `controls` = {name: probability array}: each is evaluated on the SAME
    NUMBER of bets (its top-K by its own edge, K = the model's count) so a rule that simply finds more / bigger quotes is not
    credited, with the date-clustered CI of the paired ROI difference."""
    b = _bets(t, p, price)
    s = b[b["edge"] >= thr]
    bc = _bets(t, control, price) if control is not None else None
    sc = bc[bc["edge"] >= thr] if bc is not None else None
    out = {"thr": thr, "n": int(len(s))}
    if len(s):
        out.update({"roi": float(s["profit"].mean()), "mkt_ev": float(s["mkt_ev"].mean()),
                    "excess": float((s["profit"] - s["mkt_ev"]).mean()), **_boot(s, sc, n_boot)})
    if sc is not None:
        out.update({"ctrl_n": int(len(sc)), "ctrl_roi": float(sc["profit"].mean()) if len(sc) else float("nan")})
    out["matched"] = {}
    for name, pc in (controls or {}).items():
        if not len(s):
            break
        cb = _bets(t, pc, price).sort_values("edge", ascending=False, kind="mergesort").head(len(s))
        bt = _boot(s, cb, n_boot)
        out["matched"][name] = {"n": int(len(cb)), "roi": float(cb["profit"].mean()), "diff": float(s["profit"].mean() - cb["profit"].mean()),
                                "diff_ci": bt["diff_ci"]}
    return out


def monotonic(rows: list, key: str = "roi") -> dict:
    """Is ROI non-decreasing across the (non-empty) buckets? Reports the count of up-steps and the rank correlation."""
    v = [(r["lo"], r[key]) for r in rows if r.get("n", 0) >= 30 and key in r]
    if len(v) < 3:
        return {"steps_up": None, "steps": None, "spearman": None}
    ys = [a for _, a in v]
    up = sum(1 for a, b in zip(ys, ys[1:]) if b >= a)
    rk = pd.Series(ys).rank().to_numpy()
    xs = np.arange(len(ys), dtype=float)
    sp = float(np.corrcoef(xs, rk)[0, 1])
    return {"steps_up": int(up), "steps": len(ys) - 1, "spearman": sp}


# ------------------------------------------------------------------------------------------------------ the evaluation
def evaluate(test: pd.DataFrame, preds: pd.DataFrame, models=("p_ridge", "p_gbdt", "p_auto", "p_blend_c", "p_calib"), n_boot: int = 600) -> dict:
    """Everything the report prints for one set of held-out rows (pushes already removed). `preds` is aligned with `test`."""
    y = test["over"].to_numpy(float)
    pm = np.clip(test["p_mkt"].to_numpy(float), *CLIP)
    cl = test["game_date"].to_numpy()
    out = {"n": int(len(test)), "n_dates": int(test["game_date"].nunique()), "over_rate": float(y.mean()),
           "ece_market": ece(y, pm), "models": {}}
    for name in models:
        if name not in preds.columns:
            continue
        p = np.clip(preds.loc[test.index, name].to_numpy(float), *CLIP)
        m = {"logloss": paired_logloss(y, p, pm, cl), "brier": brier_skill(y, p, pm, cl), "ece": ece(y, p),
             "slope": residual_slope(y, p, pm, cl), "quintiles": residual_deciles(y, p, pm),
             "mean_abs_gap": float(np.mean(np.abs(p - pm)))}
        ctrls = {"market": pm}
        if "p_calib" in preds.columns:
            pc = np.clip(preds.loc[test.index, "p_calib"].to_numpy(float), *CLIP)
            m["vs_calib"] = paired_logloss(y, p, pc, cl)
            if name != "p_calib":
                ctrls["calibration"] = pc
        m["roi"] = {price: {"buckets": roi_buckets(test, p, price, control=pm, n_boot=n_boot),
                            "ge2": roi_threshold(test, p, price, 0.02, control=pm, n_boot=n_boot, controls=ctrls)} for price in ("med", "best")}
        m["monotone_best"] = monotonic(m["roi"]["best"]["buckets"])
        m["per_stat"] = per_stat(test, p, pm)
        out["models"][name] = m
    out["control"] = {price: {"buckets": roi_buckets(test, pm, price, n_boot=n_boot), "ge2": roi_threshold(test, pm, price, 0.02, n_boot=n_boot)}
                      for price in ("med", "best")}
    z = np.zeros(len(test))
    out["blind"] = {}
    for label, pv in (("always_under", z), ("always_over", z + 1.0)):     # no-model, no-information rules
        out["blind"][label] = {}
        for price in ("med", "best"):
            r = roi_threshold(test, pv, price, -9.0, n_boot=n_boot)
            out["blind"][label][price] = {"n": r["n"], "roi": r.get("roi"), "roi_ci": r.get("roi_ci"), "excess": r.get("excess")}
    return out


def per_stat(test: pd.DataFrame, p, pm) -> dict:
    out = {}
    y = test["over"].to_numpy(float)
    for stat, idx in test.groupby("stat").indices.items():
        if len(idx) < 200:
            continue
        sub = test.iloc[idx]
        pl = paired_logloss(y[idx], np.asarray(p)[idx], pm[idx], sub["game_date"].to_numpy())
        bs = brier_skill(y[idx], np.asarray(p)[idx], pm[idx], sub["game_date"].to_numpy())
        sl = residual_slope(y[idx], np.asarray(p)[idx], pm[idx], sub["game_date"].to_numpy())
        bt = roi_threshold(sub, np.asarray(p)[idx], "best", 0.02, control=pm[idx], n_boot=200)
        out[str(stat)] = {"n": int(len(idx)), "d": pl["d"], "se": pl["se"], "z": pl["z"], "bss": bs["bss"], "slope": sl["b"],
                          "t_slope": sl["t"], "n_bets": bt["n"], "roi_best": bt.get("roi"), "excess_best": bt.get("excess"),
                          "ctrl_roi_best": bt.get("ctrl_roi")}
    return out


def verdict(res: dict, model: str) -> dict:
    """The pre-registered decision for one model on one held-out evaluation (see BARS)."""
    m = res["models"].get(model)
    if not m:
        return {"clears": False, "reason": "no predictions"}
    z, ts = m["logloss"]["z"], m["slope"]["t"]
    zc = m.get("vs_calib", {}).get("z", float("nan"))
    mc = m["roi"]["best"]["ge2"].get("matched", {}).get("market", {})
    ci = mc.get("diff_ci", (float("nan"), float("nan")))
    checks = {"logloss_z_vs_market": bool(z == z and z <= BARS["z_logloss"]),
              "logloss_z_vs_calibration": bool(zc == zc and zc <= BARS["z_vs_calib"]),
              "slope_t": bool(ts == ts and ts >= BARS["t_slope"] and m["slope"]["b"] > 0),
              "roi_beats_matched_line_shopping_control": bool(ci[0] == ci[0] and ci[0] > 0)}
    return {"clears": all(checks.values()), "checks": checks, "z": z, "z_vs_calib": zc, "t_slope": ts, "matched_diff_ci": ci}


# ------------------------------------------------------------------------------------------------------ sharp skew
def sharp_diagnostic(table: pd.DataFrame, n_blocks: int = 5) -> dict:
    """Novig / ProphetX only exist in the feed from the 2025 season, so a sharp-vs-soft skew can never be TRAINED on earlier
    seasons. This is the exploratory check on the seasons that have it: does skew = logit(p_sharp) - logit(p_mkt) carry
    information about the outcome beyond the market? Offset-logistic single-slope fit, date-block cross-validated (NOT
    chronological: later blocks help fit earlier ones), paired log loss vs the market with date-clustered SE."""
    sk = table[["p_novig", "p_prophetx"]].astype(float).mean(axis=1, skipna=True)
    t = table.assign(skew=np.where(sk.notna(), logit(sk.fillna(0.5)) - logit(table["p_mkt"]), np.nan))
    t = t[t["skew"].notna()].copy()
    if len(t) < 500:
        return {"n": int(len(t)), "note": "too few rows with a sharp-book quote"}
    t["skew"] = np.clip(t["skew"], -1.5, 1.5)
    dates = sorted(t["game_date"].unique())
    block = {d: i * n_blocks // len(dates) for i, d in enumerate(dates)}
    b = t["game_date"].map(block).to_numpy()
    p_cv = np.zeros(len(t))
    for k in range(n_blocks):
        tr, te = b != k, b == k
        mdl = OffsetRidge(np.array([1.0])).fit(t.loc[tr, ["skew"]].to_numpy(float), t.loc[tr, "over"].to_numpy(float), logit(t.loc[tr, "p_mkt"]))
        p_cv[te] = sigmoid(logit(t.loc[te, "p_mkt"]) + mdl.margin(t.loc[te, ["skew"]].to_numpy(float)))
    y, pm = t["over"].to_numpy(float), np.clip(t["p_mkt"].to_numpy(float), *CLIP)
    pl = paired_logloss(y, p_cv, pm, t["game_date"].to_numpy())
    full = OffsetRidge(np.array([1.0])).fit(t[["skew"]].to_numpy(float), y, logit(t["p_mkt"]))
    gap = np.clip(sigmoid(logit(t["p_mkt"]) + full.margin(t[["skew"]].to_numpy(float))) - pm, -1, 1)
    sl = residual_slope(y, np.clip(pm + (p_cv - pm), *CLIP), pm, t["game_date"].to_numpy())
    corr = float(np.corrcoef(t["skew"], y - pm)[0, 1])
    return {"n": int(len(t)), "seasons": sorted(int(s) for s in t["season"].unique()), "coverage": float(len(t) / len(table[table['season'].isin(t['season'].unique())])),
            "mean_skew": float(t["skew"].mean()), "sd_skew": float(t["skew"].std()), "corr_skew_vs_realised_gap": corr,
            "cv_logloss": pl, "cv_slope": sl}


# --------------------------------------------------------------------------------------------------------------- render
def _pct(x, d=2):
    return "n/a" if x is None or x != x else f"{100 * x:+.{d}f}%"


def _ci(c):
    return "[n/a]" if not c or c[0] != c[0] else f"[{100 * c[0]:+.1f}, {100 * c[1]:+.1f}]"


def render_block(label: str, res: dict, models=("p_ridge", "p_gbdt", "p_auto", "p_blend_c", "p_calib")) -> str:
    L_ = [f"#### {label}", "", f"n = {res['n']:,} props, {res['n_dates']} game dates, over rate {res['over_rate']:.3f}, market ECE {res['ece_market']:.4f}", ""]
    L_ += ["| model | LL market | LL model | d LL vs market (SE) | z | d LL vs calibration-only (z) | Brier skill vs mkt (SE) | ECE model | resid slope b (t) | mean abs gap |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name in models:
        m = res["models"].get(name)
        if not m:
            continue
        a, b, s = m["logloss"], m["brier"], m["slope"]
        vc = m.get("vs_calib")
        vcs = "n/a" if not vc or name == "p_calib" else f"{vc['d']:+.5f} ({vc['z']:+.2f})"
        L_.append(f"| {name[2:]} | {a['ll_market']:.4f} | {a['ll_model']:.4f} | {a['d']:+.5f} ({a['se']:.5f}) | {a['z']:+.2f} | {vcs} | "
                  f"{b['bss'] * 100:+.3f}% ({b['se'] * 100:.3f}) | {m['ece']:.4f} | {s['b']:+.2f} ({s['t']:+.1f}) | {m['mean_abs_gap']:.4f} |")
    bl = res.get("blind")
    if bl:
        L_.append("")
        L_.append("No-information controls (every prop, flat 1u): " + "; ".join(
            f"{k.replace('_', ' ')} at {price} price ROI {_pct(v[price]['roi'])} {_ci(v[price]['roi_ci'])}" for k, v in bl.items() for price in ("med", "best")) + ".")
    L_.append("")
    return "\n".join(L_)


def render_roi(label: str, res: dict, model: str) -> str:
    m = res["models"].get(model)
    if not m:
        return ""
    L_ = [f"##### ROI, {label}, model `{model[2:]}` (flat 1u, one bet per prop, edge = model P(side) - implied price)", "",
          "| price | edge bucket | n bets | ROI [95% CI] | hit | market-expected ROI | EXCESS over market-expected [CI] | no-model control ROI (n) | model - control [CI] |",
          "|---|---|---|---|---|---|---|---|---|"]
    for price in ("med", "best"):
        for r in m["roi"][price]["buckets"]:
            hi = "+" if r["hi"] > 1 else f"-{100 * r['hi']:.0f}"
            nm = f"{100 * r['lo']:.0f}{hi}%"
            if not r["n"]:
                L_.append(f"| {price} | {nm} | 0 | | | | | {_pct(r.get('ctrl_roi'))} ({r.get('ctrl_n', 0)}) | |")
                continue
            L_.append(f"| {price} | {nm} | {r['n']} | {_pct(r['roi'])} {_ci(r['roi_ci'])} | {r['hit']:.3f} | {_pct(r['mkt_ev'])} | "
                      f"{_pct(r['excess'])} {_ci(r['excess_ci'])} | {_pct(r['ctrl_roi'])} ({r['ctrl_n']}) | {_ci(r.get('diff_ci'))} |")
    for price in ("med", "best"):
        g = m["roi"][price]["ge2"]
        if g["n"]:
            L_.append(f"| {price} | all >= 2% | {g['n']} | {_pct(g['roi'])} {_ci(g['roi_ci'])} | | {_pct(g['mkt_ev'])} | "
                      f"{_pct(g['excess'])} {_ci(g['excess_ci'])} | {_pct(g.get('ctrl_roi'))} ({g.get('ctrl_n', 0)}) | {_ci(g.get('diff_ci'))} |")
    g = m["roi"]["best"]["ge2"]
    for cname, c in g.get("matched", {}).items():
        L_.append(f"| best | >= 2%, vs `{cname}` control with the SAME number of bets (top-K by its own edge) | {c['n']} | {_pct(c['roi'])} | | | | "
                  f"model - control {_pct(c['diff'])} | {_ci(c['diff_ci'])} |")
    mono = m["monotone_best"]
    L_.append("")
    L_.append(f"Edge-bucket monotonicity at the best price: " + ("not computable (fewer than 3 buckets with >= 30 bets)" if mono["steps"] is None else
              f"{mono['steps_up']} of {mono['steps']} steps up, rank correlation {mono['spearman']:+.2f}") + ".")
    c = res["control"]
    L_.append("No-model control, all buckets pooled (best-quote vs consensus alone): " +
              (f"{c['best']['ge2']['n']} bets at >= 2%, ROI {_pct(c['best']['ge2'].get('roi'))}, market-expected {_pct(c['best']['ge2'].get('mkt_ev'))}, excess {_pct(c['best']['ge2'].get('excess'))}" if c["best"]["ge2"]["n"] else "no bets") + ".")
    L_.append("")
    return "\n".join(L_)


def render_quintiles(res: dict, model: str) -> str:
    m = res["models"].get(model)
    if not m:
        return ""
    L_ = [f"Residual quintiles, `{model[2:]}` (props sorted by the model's gap to the market): mean model gap -> realised gap (y - p_mkt)", ""]
    L_ += ["| quintile | n | mean model gap | realised gap |", "|---|---|---|---|"]
    for i, q in enumerate(m["quintiles"], 1):
        L_.append(f"| {i} | {q['n']} | {q['mean_gap'] * 100:+.2f} pts | {q['real_gap'] * 100:+.2f} pts |")
    return "\n".join(L_) + "\n"


def render_per_stat(label: str, res: dict, model: str) -> str:
    m = res["models"].get(model)
    if not m or not m["per_stat"]:
        return ""
    L_ = [f"##### per stat, {label}, model `{model[2:]}`", "",
          "| stat | n | d LL (SE) | z | Brier skill | slope b (t) | bets >= 2% (best) | ROI | excess | no-model ROI |", "|---|---|---|---|---|---|---|---|---|---|"]
    for stat, r in sorted(m["per_stat"].items()):
        L_.append(f"| {stat} | {r['n']} | {r['d']:+.5f} ({r['se']:.5f}) | {r['z']:+.2f} | {r['bss'] * 100:+.3f}% | {r['slope']:+.2f} ({r['t_slope']:+.1f}) | "
                  f"{r['n_bets']} | {_pct(r['roi_best'])} | {_pct(r['excess_best'])} | {_pct(r['ctrl_roi_best'])} |")
    return "\n".join(L_) + "\n"


def prepare(table: pd.DataFrame) -> pd.DataFrame:
    """Validate the schema and drop what cannot be scored (pushes, no price, p_mkt outside (0, 1)). Returns a clean frame."""
    miss = [c for c in REQUIRED if c not in table.columns]
    if miss:
        raise ValueError(f"prop table is missing columns {miss}")
    t = table.copy()
    for c in ("p_mkt", "over", "line", "best_over_odds", "best_under_odds", "med_over_odds", "med_under_odds"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    if "push" in t.columns:
        t = t[~t["push"].fillna(False).astype(bool)]
    t = t.dropna(subset=["p_mkt", "over", "best_over_odds", "best_under_odds", "med_over_odds", "med_under_odds"])
    t = t[(t["p_mkt"] > 0.0) & (t["p_mkt"] < 1.0)]
    t["date"] = t["date"].astype(str).str[:10]
    t["game_date"] = t["game_date"].astype(str).str[:10]
    t["season"] = t["season"].astype(int)
    return t.sort_values(["date", "stat"], kind="mergesort").reset_index(drop=True)


def render_study(sport: str, res: dict) -> str:
    """Markdown tables for a `study` result (unedited numbers; the analysis doc wraps them in prose)."""
    L_ = [f"### {sport}: market-residual model, walk-forward (UNVALIDATED)", "",
          f"Seasons in the table: {res['seasons']}; rows by season {res['rows_by_season']}; rows by stat {res['rows_by_stat']}.",
          f"Held-out season: {res['holdout_season']} ({'SCORED in this run' if res['report_holdout'] else 'NOT touched in this run'}).", "",
          "Folds (hyper-parameters chosen on the season before the test season; `valid d` = paired log-loss gain of the best "
          "grid point vs the market there, negative = better):", "",
          "| test season | n train | n test | ridge (lam_main, lam_stat) | gbdt (depth, rounds) | valid d ridge | valid d gbdt |", "|---|---|---|---|---|---|---|"]
    for f in res["folds"]:
        c, v = f["chosen"], f["valid_d"]
        L_.append(f"| {f['season']} | {f['n_train']} | {f['n_test']} | {c['ridge']} | {c['gbdt']} | {v['ridge']:+.5f} | {v['gbdt']:+.5f} |")
    L_.append("")
    blocks = [("development folds pooled (seasons the inner validation could still see)", res.get("dev_pooled"))]
    blocks += [(f"season {s}", r) for s, r in sorted(res["per_season"].items())]
    for label, r in blocks:
        if r is None:
            continue
        tag = " (HELD OUT, scored once)" if label == f"season {res['holdout_season']}" and res["report_holdout"] else ""
        L_.append(render_block(label + tag, r))
    d = res.get("dev_pooled")
    if d:
        for model in ("p_ridge", "p_gbdt"):
            L_.append(render_roi("development folds pooled", d, model))
            L_.append(render_per_stat("development folds pooled", d, model))
            L_.append(render_quintiles(d, model))
    h = res.get("holdout")
    if h:
        for model in ("p_ridge", "p_gbdt"):
            L_.append(render_roi(f"held-out {res['holdout_season']}", h, model))
            L_.append(render_per_stat(f"held-out {res['holdout_season']}", h, model))
            L_.append(render_quintiles(h, model))
        L_.append("Verdicts on the held-out season (all four checks must hold; bars set before this season was scored: log-loss z vs market <= "
                  f"{BARS['z_logloss']}, z vs the calibration-only model <= {BARS['z_vs_calib']}, residual-slope t >= {BARS['t_slope']} with b > 0, "
                  "best-price >= 2% ROI above the same-number-of-bets line-shopping control with the paired CI above 0):")
        L_.append("")
        for m, v in res["verdict"].items():
            L_.append(f"- {m[2:]}: {'CLEARS' if v['clears'] else 'does not clear'} | " +
                      ", ".join(f"{k}={'yes' if ok else 'no'}" for k, ok in v.get("checks", {}).items()))
        L_.append("")
    if res.get("sharp"):
        L_.append("Sharp-vs-soft skew (Novig / ProphetX only quote from the 2025 season, so this CANNOT be trained on history; exploratory):")
        L_.append("")
        L_.append("```\n" + json.dumps(res["sharp"], indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n```")
    return "\n".join(L_) + "\n"


def study(table: pd.DataFrame, holdout: int, report_holdout: bool = False, post: tuple = (), nthread: int = 4, n_boot: int = 600,
          log=None) -> tuple:
    """The whole protocol on a prop table. Development folds are every season before `holdout` that has two earlier seasons;
    the `holdout` season (the latest full one) is scored ONLY when report_holdout is True, and then exactly once
    (hyper-parameters from the season before it). `post` = later, partial seasons scored the same way after the holdout.
    Returns (results dict, predictions frame aligned with the prepared table)."""
    t = prepare(table)
    seasons = sorted(t["season"].unique())
    dev_seasons = [s for s in seasons if s < holdout]
    dev_tests = [s for s in dev_seasons if len([x for x in dev_seasons if x < s]) >= 2]
    use = t if report_holdout else t[t["season"] < holdout]
    test_seasons = dev_tests + ([holdout] if report_holdout else []) + [s for s in post if report_holdout and s in seasons]
    preds, folds = walk_forward(use, test_seasons, nthread=nthread, log=log)
    out = {"holdout_season": int(holdout), "report_holdout": bool(report_holdout), "seasons": [int(s) for s in seasons],
           "rows_by_season": {int(k): int(v) for k, v in t.groupby("season").size().items()},
           "rows_by_stat": {str(k): int(v) for k, v in t.groupby("stat").size().items()},
           "folds": [{"season": f.season, "n_train": f.n_train, "n_test": f.n_test,
                      "chosen": {k: [float(x) for x in v] for k, v in f.chosen.items()}, "valid_d": f.valid} for f in folds],
           "per_season": {}}
    if not len(preds):
        return out, preds
    te = use.loc[preds.index]
    for s in sorted(te["season"].unique()):
        out["per_season"][int(s)] = evaluate(te[te["season"] == s], preds, n_boot=n_boot)
    dev = te[te["season"].isin(dev_tests)]
    if len(dev):
        out["dev_pooled"] = evaluate(dev, preds, n_boot=n_boot)
    if report_holdout and holdout in out["per_season"]:
        out["holdout"] = out["per_season"][holdout]
        out["verdict"] = {m: verdict(out["holdout"], m) for m in ("p_ridge", "p_gbdt", "p_auto")}
    if report_holdout and {"p_novig", "p_prophetx"} <= set(t.columns):
        out["sharp"] = sharp_diagnostic(t[t["season"] >= holdout])
    return out, preds
