"""RES-08 standing discovery screen (P8 of RUL-20261116-profitable-markets-paths).

One deterministic batch screen over the Action Network backfill (BNK-11): for every
(league, stat) cell with >= MIN_BOOKS two-sided real-book coverage and a banked outcome
source, fit a walk-forward ridge-logistic residual on the de-vigged median market logit
and score the out-of-sample blend against the pre-registered candidate standard:

  PASS  iff out-of-sample blend c >= 0.25 (t >= 2.0, game-clustered), paired delta-LL of
        the blend vs the de-vigged consensus < 0, blend ECE <= 0.015, and
        control-subtracted best-price ROI >= +2.0%.
  INSUFFICIENT DATA if out-of-sample n < 1,000, out-of-sample dates < 40, or
        median-book gated bets < 250 (reported, not passed or killed).
  KILL  otherwise (permanent for that cell on this data).

Four game-clustered metrics per cell: (1) paired OOS delta-LL (blend vs consensus),
(2) blend ECE, (3) median-book ROI at the 3-12pp gate, (4) control-subtracted
best-price ROI (model selections minus no-model line-shopping selections, same panel).

Conventions are the house ones, imported not re-implemented where they exist:
blend form logit(p) = logit(mkt) + c*(logit(model) - logit(mkt)) (ev.daily_projections
c-only form), blend c / clustered t from mlb_core.odds.model_vs_market, ECE as the
10-bin equal-frequency form used by the RES-07 audit frame.

Data discipline: outcomes come from banked actuals only; identity joins are
fail-closed (a panel row settles only on exactly one master row for the normalized
player name on the game date, with team agreement when both sides carry a team);
AN boards are closing-state snapshots with no timestamps, so every price is read
as the closing board (see the run memo for what that limits).

Usage:
  python scripts/discovery_screen.py inventory   # cell universe + coverage counts
  python scripts/discovery_screen.py run [--leagues nba,nhl] [--out results.json]
Downloads land in --cache-dir (default ~/workspace/beezy/p8-screen-cache); nothing
is written to the bucket and no production state is touched.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import unicodedata

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mlb_core.odds import model_vs_market as MVM  # noqa: E402

BUCKET = "gs://concrete-crow-445205-m4-mlb-data"
GCLOUD = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
GCP_ENV = dict(
    os.environ,
    GOOGLE_CLOUD_PROJECT="concrete-crow-445205-m4",
    GOOGLE_APPLICATION_CREDENTIALS=os.path.expanduser(
        "~/.config/gcloud/legacy_credentials/lmaynor21@gmail.com/adc.json"
    ),
)

# Action Network book ids that are bettable books. 15 = AN consensus (an aggregate,
# not a quote) and 30 = the opening series are excluded from coverage, the median,
# and best-price construction everywhere below.
REAL_BOOKS = {68, 69, 75, 79, 123, 71, 49}

MIN_BOOKS = 4            # ruling: >= 4-book two-sided coverage
FLOOR_N_OOS = 1000       # ruling: per-cell out-of-sample sample floor
FLOOR_DATES = 40
FLOOR_GATED = 250
PASS_C = 0.25
PASS_T = 2.0
PASS_ECE = 0.015
PASS_CTRL_SUB = 0.02
GATE_LO, GATE_HI = 0.03, 0.12   # median-book ROI gate band (blend edge vs median book)
BEST_GAP = 0.05                 # best-price selection gap (house disagreement form)
BURN_IN_FRAC = 0.40
N_BLOCKS = 4
RIDGE_LAMBDA = 1.0
EPS = 1e-9

LEAGUES = ["nba", "nhl", "nfl", "mlb", "ncaab", "ncaaf", "wnba",
           "epl", "mls", "laliga", "bundesliga", "seriea", "ligue1", "ncaaw"]


# --------------------------------------------------------------------------- io

def gcloud_ls(prefix: str) -> list[str]:
    r = subprocess.run([GCLOUD, "storage", "ls", prefix],
                       capture_output=True, text=True, env=GCP_ENV)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def gcloud_cp(src: str, dst: str) -> bool:
    r = subprocess.run([GCLOUD, "storage", "cp", src, dst],
                       capture_output=True, text=True, env=GCP_ENV)
    return r.returncode == 0


def list_an_markets(league: str) -> list[str]:
    """Market prefixes banked for a league (from the bucket listing)."""
    tops = gcloud_ls(f"{BUCKET}/Odds/actionnetwork_{league}/")
    out = []
    for t in tops:
        name = t.rstrip("/").rsplit("/", 1)[-1]
        if t.endswith("/") and name not in ("checkpoints",):
            out.append(name)
    return sorted(out)


def is_main_market(market: str) -> bool:
    """Main-board (potentially two-sided) markets only: milestones and game lines
    are excluded by construction (one-sided / team markets)."""
    return "milestone" not in market and market != "gamelines"


def download_market(cache_dir: str, league: str, market: str) -> str:
    """Download every parquet partition of one AN market into the cache with a
    single wildcard transfer (gcloud parallelizes internally; a per-file loop
    costs one process spawn per partition and is minutes slower per market)."""
    dst_dir = os.path.join(cache_dir, "an", league, market)
    os.makedirs(dst_dir, exist_ok=True)
    src = f"{BUCKET}/Odds/actionnetwork_{league}/{market}/*.parquet"
    subprocess.run([GCLOUD, "storage", "cp", src, dst_dir + "/"],
                   capture_output=True, text=True, env=GCP_ENV)
    return dst_dir


def download_master(cache_dir: str, key: str) -> str:
    dst = os.path.join(cache_dir, "masters", os.path.basename(key))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        gcloud_cp(f"{BUCKET}/{key}", dst)
    return dst


# ------------------------------------------------------------------ panel build

def norm_name(s) -> str:
    """House identity normalization (same form as the RES-07/LAD-05 panels)."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def american_to_prob(odds: float) -> float:
    o = float(odds)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_to_decimal(odds: float) -> float:
    o = float(odds)
    return 1.0 + (100.0 / (-o) if o < 0 else o / 100.0)


def build_panel(frames: list[pd.DataFrame], min_books: int = MIN_BOOKS,
                date_mode: str = "utc") -> tuple[pd.DataFrame, dict]:
    """Raw AN rows -> one row per (date, event, player, market) at the modal line.

    Keeps real books only; a (player, book) contributes only when it quotes both
    sides at a single line; the row's line is the modal book line; coverage,
    median and best prices are computed over the real books at that line.
    Returns (panel, counts) where counts tracks every drop reason.

    The game key (`date`) follows each league master's own convention, measured
    against the banked masters: the NBA master keys games by UTC tip date
    (date_mode="utc": the UTC date of AN's start_time), while the NHL master
    keys by the local game date (date_mode="slate": AN's own `date` field).
    """
    counts = {"rows_in": 0, "not_main_or_side": 0, "not_real_book": 0,
              "book_not_two_sided": 0, "rows_pre_gate": 0, "below_min_books": 0}
    if not frames:
        return pd.DataFrame(), counts
    d = pd.concat(frames, ignore_index=True)
    counts["rows_in"] = int(len(d))
    d = d.rename(columns={"date": "slate_date"})
    if date_mode == "utc":
        d["date"] = pd.to_datetime(d["start_time"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    elif date_mode == "et":
        # Game key = US Eastern date of the start time. Required for the NFL:
        # AN's `date` field on NFL rows is not the game date (the BNK-11 NFL
        # harvest banked the current week's board under ~149 fictitious slate
        # dates; start_time is the only truthful key).
        d["date"] = (pd.to_datetime(d["start_time"], utc=True, errors="coerce")
                       .dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d"))
    else:
        d["date"] = d["slate_date"]
    d = d[d["date"].notna()]
    d = d[(d["is_milestone"] == False) & (d["side"].isin(["over", "under"]))]  # noqa: E712
    counts["not_main_or_side"] = counts["rows_in"] - int(len(d))
    d = d[d["book_id"].isin(REAL_BOOKS) & d["line"].notna() & d["odds"].notna()]
    counts["not_real_book"] = counts["rows_in"] - counts["not_main_or_side"] - int(len(d))
    if d.empty:
        return pd.DataFrame(), counts
    d = d.assign(player_key=d["player_name"].map(norm_name))
    # one line, both sides, per (date, event, player, market, book)
    pv = (d.pivot_table(index=["date", "event_id", "player_key", "market", "book_id", "line"],
                        columns="side", values="odds", aggfunc="first")
            .dropna(subset=["over", "under"]).reset_index())
    n_books_raw = d.groupby(["date", "event_id", "player_key", "market"])["book_id"].nunique()
    n_books_ok = pv.groupby(["date", "event_id", "player_key", "market"])["book_id"].nunique()
    counts["book_not_two_sided"] = int((n_books_raw - n_books_ok.reindex(n_books_raw.index).fillna(0)).sum())
    if pv.empty:
        return pd.DataFrame(), counts
    # modal line per (date, event, player, market): most books, then lowest line
    cnt = (pv.groupby(["date", "event_id", "player_key", "market", "line"])["book_id"]
             .nunique().rename("n_at_line").reset_index())
    cnt = cnt.sort_values(["date", "event_id", "player_key", "market",
                           "n_at_line", "line"],
                          ascending=[True, True, True, True, False, True])
    modal = cnt.drop_duplicates(["date", "event_id", "player_key", "market"])[
        ["date", "event_id", "player_key", "market", "line"]]
    pv = pv.merge(modal, on=["date", "event_id", "player_key", "market", "line"], how="inner")
    pv["imp_o"] = pv["over"].map(american_to_prob)
    pv["imp_u"] = pv["under"].map(american_to_prob)
    pv["fair_o"] = pv["imp_o"] / (pv["imp_o"] + pv["imp_u"])
    pv["dec_o"] = pv["over"].map(american_to_decimal)
    pv["dec_u"] = pv["under"].map(american_to_decimal)
    meta = (d.drop_duplicates(["date", "event_id", "player_key", "market"])
              [["date", "event_id", "player_key", "market", "player_name", "team_abbr",
                "home_abbr", "away_abbr"]])
    g = pv.groupby(["date", "event_id", "player_key", "market"])
    panel = g.agg(line=("line", "first"), n_books=("book_id", "nunique"),
                  p_mkt=("fair_o", "median"), dispersion=("fair_o", "std"),
                  med_over_dec=("dec_o", "median"), med_under_dec=("dec_u", "median"),
                  best_over_dec=("dec_o", "max"), best_under_dec=("dec_u", "max"),
                  hold_med=("imp_o", "median")).reset_index()
    # hold at the median book: rebuild from median implieds
    med_imp = g.agg(med_imp_o=("imp_o", "median"), med_imp_u=("imp_u", "median")).reset_index()
    panel = panel.merge(med_imp, on=["date", "event_id", "player_key", "market"])
    panel["hold_med"] = panel["med_imp_o"] + panel["med_imp_u"] - 1.0
    panel = panel.drop(columns=["med_imp_o", "med_imp_u"])
    panel = panel.merge(meta, on=["date", "event_id", "player_key", "market"], how="left")
    panel["dispersion"] = panel["dispersion"].fillna(0.0)
    counts["rows_pre_gate"] = int(len(panel))
    panel = panel[panel["n_books"] >= min_books].copy()
    counts["below_min_books"] = counts["rows_pre_gate"] - int(len(panel))
    return panel, counts


# -------------------------------------------------------------------- outcomes

def settle(panel: pd.DataFrame, actuals: pd.DataFrame, stat: str,
           game_map: dict | None = None, alias: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Fail-closed join of panel rows to banked actuals.

    `actuals` is the normalized master frame: columns date, name_key, team_abbr,
    game_id, played, and one column per AN market name with the player's actual
    stat total. A panel row settles only when the master yields exactly one
    candidate for (name_key, date) after, in order: dropping did-not-play rows
    (all-DNP -> void), and disambiguating multiple played rows by the event's
    team pair via `game_map` (game_id -> {home_abbr, away_abbr} in master
    dialect; `alias` maps AN abbreviations to the master dialect). A surviving
    candidate whose team is not one of the event's two teams is a mismatch and
    is dropped. (The check uses the event pair, not the panel row's team_abbr:
    AN stamps the player's CURRENT team onto historical rows — verified on the
    NBA panel, e.g. a 2024-10-27 row carrying a player's 2025-26 team — while
    home/away are event-true.) Pushes (actual == line) are excluded and
    counted. Every drop is counted; nothing is ever force-matched.
    """
    counts = {"unresolved": 0, "ambiguous": 0, "team_mismatch": 0, "push": 0,
              "settled": 0, "void_dnp": 0}
    out = panel.copy()
    out["actual"] = np.nan
    out["y"] = np.nan
    if panel.empty or actuals.empty or stat not in actuals.columns:
        counts["unresolved"] = int(len(panel))
        return out, counts
    alias = alias or {}
    a = actuals.copy()
    if "played" not in a.columns:
        a["played"] = True
    by_key: dict[tuple, list] = {}
    for r in a.itertuples(index=False):
        by_key.setdefault((r.name_key, r.date), []).append(r)
    gmap = game_map or {}
    for i, r in enumerate(out.itertuples(index=False)):
        cands = by_key.get((r.player_key, r.date))
        if not cands:
            counts["unresolved"] += 1
            continue
        played = [c for c in cands if c.played]
        if not played:
            counts["void_dnp"] += 1
            continue
        if len(played) > 1:
            pair = frozenset({alias.get(r.home_abbr, r.home_abbr),
                              alias.get(r.away_abbr, r.away_abbr)})
            surv = [c for c in played if gmap.get(getattr(c, "game_id", None)) == pair]
            if len(surv) == 1:
                played = surv
        if len(played) != 1:
            counts["ambiguous"] += 1
            continue
        c = played[0]
        cteam = getattr(c, "team_abbr", None)
        if isinstance(cteam, str) and cteam:
            event_teams = {alias.get(r.home_abbr, r.home_abbr),
                           alias.get(r.away_abbr, r.away_abbr)} - {None, ""}
            if event_teams:
                if cteam not in event_teams:
                    counts["team_mismatch"] += 1
                    continue
            elif alias.get(r.team_abbr, r.team_abbr) not in (None, "", cteam):
                counts["team_mismatch"] += 1
                continue
        actual = getattr(c, stat)
        if actual is None or (isinstance(actual, float) and not np.isfinite(actual)):
            counts["unresolved"] += 1
            continue
        out.iat[i, out.columns.get_loc("actual")] = float(actual)
        if float(actual) == float(r.line):
            counts["push"] += 1
            continue
        out.iat[i, out.columns.get_loc("y")] = 1.0 if float(actual) > float(r.line) else 0.0
        counts["settled"] += 1
    return out, counts


def trailing_features(actuals: pd.DataFrame, stat: str, panel: pd.DataFrame,
                      ambiguous: set | None = None) -> pd.DataFrame:
    """Per (name_key, date) trailing features from the master, strictly prior games:
    z-scores of the last-10 and last-20 played games vs the row's line, and the
    last-10 empirical exceedance rate at that line. Players keyed by name_key;
    names shared by more than one master identity get no history (fail-closed)."""
    if actuals.empty or stat not in actuals.columns:
        return panel.assign(z10=np.nan, z20=np.nan, exc10=np.nan)
    a = actuals
    if "played" in a.columns:
        a = a[a["played"] == True]  # noqa: E712  (trailing form counts games he played)
    a = a[["date", "name_key", stat]].dropna().sort_values(["name_key", "date"])
    hist: dict[str, list[float]] = {}
    feat: dict[tuple, tuple] = {}
    # build per-player history once, then answer each (player, date) by bisect
    from bisect import bisect_left
    dates_by_player: dict[str, list[str]] = {}
    vals_by_player: dict[str, list[float]] = {}
    for k, grp in a.groupby("name_key"):
        dates_by_player[k] = list(grp["date"])
        vals_by_player[k] = [float(v) for v in grp[stat]]
    out = []
    for r in panel.itertuples(index=False):
        k, d, line = r.player_key, r.date, float(r.line)
        if ambiguous and k in ambiguous:
            out.append((np.nan, np.nan, np.nan))
            continue
        ds = dates_by_player.get(k)
        if not ds:
            out.append((np.nan, np.nan, np.nan))
            continue
        i = bisect_left(ds, d)          # strictly prior games only
        prior = vals_by_player[k][:i]
        if len(prior) < 5:
            out.append((np.nan, np.nan, np.nan))
            continue
        def z(vals):
            mu = float(np.mean(vals))
            sd = float(np.std(vals))
            sd = max(sd, 0.1 * abs(mu) + 0.05)
            return (mu - line) / sd
        last10 = prior[-10:]
        last20 = prior[-20:]
        exc = float(np.mean([1.0 if v > line else 0.0 for v in last10]))
        out.append((z(last10), z(last20), exc))
    panel = panel.copy()
    panel[["z10", "z20", "exc10"]] = pd.DataFrame(out, index=panel.index)
    return panel


# ---------------------------------------------------------------- screen engine

def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_residual_logistic(X: np.ndarray, y: np.ndarray, offset: np.ndarray,
                          lam: float = RIDGE_LAMBDA, iters: int = 100) -> np.ndarray:
    """Ridge-penalized logistic regression with a fixed offset (Newton, deterministic).
    X must already be standardized; the intercept is carried by the offset (the
    market logit), so no free intercept is fitted."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        z = offset + X @ beta
        p = _sigmoid(z)
        w = np.clip(p * (1 - p), 1e-9, None)
        H = X.T @ (X * w[:, None]) + lam * np.eye(X.shape[1])
        grad = X.T @ (y - p) - lam * beta
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return beta


FEATURES = ["z10", "z20", "exc_gap", "dispersion", "hold_med"]


def fit_blend_c(y: np.ndarray, p_mkt: np.ndarray, p_sig: np.ndarray,
                iters: int = 60) -> float:
    """The screen's blend weight on TRAIN: 1-D Newton for c in the c-only house
    form logit(p) = logit(p_mkt) + c*(logit(p_sig) - logit(p_mkt)). Identified
    whenever the signal varies, including where the full (a, b, c) regression
    degenerates (e.g. a near-constant market logit)."""
    y = np.asarray(y, float)
    lm = _logit(p_mkt)
    d = _logit(p_sig) - lm
    if np.std(d) < 1e-9:
        return 0.0
    c = 0.0
    for _ in range(iters):
        p = _sigmoid(lm + c * d)
        w = np.clip(p * (1 - p), 1e-9, None)
        H = float(np.sum(w * d * d)) + 1e-9
        grad = float(np.sum((y - p) * d))
        step = grad / H
        c += step
        if abs(step) < 1e-10:
            break
    return float(c)


def walk_forward(df: pd.DataFrame) -> pd.DataFrame:
    """Expanding-window walk-forward: burn-in on the first BURN_IN_FRAC of dates,
    then N_BLOCKS sequential test blocks. Fits the residual model and the blend c
    on the training past only; returns the OOS frame with p_sig / p_blend."""
    df = df.copy()
    df["exc_gap"] = df["exc10"] - df["p_mkt"]
    df = df.dropna(subset=["y"] + FEATURES).sort_values("date")
    dates = sorted(df["date"].unique())
    if len(dates) < 10:
        return df.assign(p_sig=np.nan, p_blend=np.nan).iloc[0:0]
    n_burn = max(1, int(len(dates) * BURN_IN_FRAC))
    test_dates = dates[n_burn:]
    blocks = np.array_split(np.array(test_dates), N_BLOCKS)
    oos = []
    for block in blocks:
        if len(block) == 0:
            continue
        train = df[df["date"] < block[0]]
        test = df[df["date"].isin(set(block.tolist()))]
        if len(train) < 200 or test.empty:
            continue
        mu = train[FEATURES].mean()
        sd = train[FEATURES].std().replace(0, 1.0).fillna(1.0)
        Xtr = ((train[FEATURES] - mu) / sd).to_numpy()
        Xte = ((test[FEATURES] - mu) / sd).to_numpy()
        off_tr = _logit(train["p_mkt"].to_numpy())
        beta = fit_residual_logistic(Xtr, train["y"].to_numpy(), off_tr)
        p_sig_tr = _sigmoid(off_tr + Xtr @ beta)
        c_train = fit_blend_c(train["y"].to_numpy(), train["p_mkt"].to_numpy(), p_sig_tr)
        off_te = _logit(test["p_mkt"].to_numpy())
        p_sig = _sigmoid(off_te + Xte @ beta)
        p_blend = _sigmoid(off_te + c_train * (_logit(p_sig) - off_te))
        test = test.assign(p_sig=p_sig, p_blend=p_blend)
        oos.append(test)
    if not oos:
        return df.assign(p_sig=np.nan, p_blend=np.nan).iloc[0:0]
    return pd.concat(oos, ignore_index=True)


def logloss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """House ECE: 10 equal-frequency bins on P(over) vs the over outcome.

    Argument order is (probabilities, outcomes) — note the RES-07 frame's
    helper takes (y, p); every call site here passes (p, y)."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    if len(y) == 0:
        return float("nan")
    qs = np.quantile(p, np.linspace(0, 1, bins + 1))
    qs[0], qs[-1] = -np.inf, np.inf
    idx = np.clip(np.digitize(p, qs[1:-1]), 0, bins - 1)
    out = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            out += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(out)


def _returns(win: np.ndarray, dec: np.ndarray) -> np.ndarray:
    return np.where(win, dec - 1.0, -1.0)


def median_book_roi(oos: pd.DataFrame) -> tuple[float, int]:
    """Flat-stake ROI at the median book where the blend's edge over the median
    book's implied probability sits in the [GATE_LO, GATE_HI] band."""
    p = oos["p_blend"].to_numpy()
    y = oos["y"].to_numpy()
    imp_o = 1.0 / oos["med_over_dec"].to_numpy()
    imp_u = 1.0 / oos["med_under_dec"].to_numpy()
    edge_o = p - imp_o
    edge_u = (1 - p) - imp_u
    take_o = (edge_o >= GATE_LO) & (edge_o <= GATE_HI)
    take_u = (~take_o) & (edge_u >= GATE_LO) & (edge_u <= GATE_HI)
    rets, wins, decs = [], [], []
    if take_o.any():
        wins.append(y[take_o] == 1)
        decs.append(oos["med_over_dec"].to_numpy()[take_o])
    if take_u.any():
        wins.append(y[take_u] == 0)
        decs.append(oos["med_under_dec"].to_numpy()[take_u])
    if not wins:
        return float("nan"), 0
    win = np.concatenate(wins)
    dec = np.concatenate(decs)
    r = _returns(win, dec)
    return float(r.mean()), int(len(r))


def best_price_rois(oos: pd.DataFrame) -> dict:
    """Model best-price ROI (blend disagreement >= BEST_GAP vs consensus, priced at
    the best quote) and the no-model line-shopping control (best quote deviating
    from consensus by >= BEST_GAP, priced at that quote), on the same OOS panel."""
    p_b = oos["p_blend"].to_numpy()
    p_m = oos["p_mkt"].to_numpy()
    y = oos["y"].to_numpy()
    bo = oos["best_over_dec"].to_numpy()
    bu = oos["best_under_dec"].to_numpy()

    def roi(sel_o, sel_u):
        wins, decs = [], []
        if sel_o.any():
            wins.append(y[sel_o] == 1)
            decs.append(bo[sel_o])
        if sel_u.any():
            wins.append(y[sel_u] == 0)
            decs.append(bu[sel_u])
        if not wins:
            return float("nan"), 0
        r = _returns(np.concatenate(wins), np.concatenate(decs))
        return float(r.mean()), int(len(r))

    diff = p_b - p_m
    m_roi, m_n = roi(diff >= BEST_GAP, diff <= -BEST_GAP)
    gap_o = p_m - 1.0 / bo        # best over price cheaper than consensus fair
    gap_u = (1 - p_m) - 1.0 / bu
    c_roi, c_n = roi(gap_o >= BEST_GAP, gap_u >= BEST_GAP)
    return {"model_best_roi": m_roi, "model_best_n": m_n,
            "control_best_roi": c_roi, "control_best_n": c_n,
            "ctrl_sub_roi": (m_roi - c_roi) if np.isfinite(m_roi) and np.isfinite(c_roi)
            else float("nan")}


def score_cell(oos: pd.DataFrame) -> dict:
    """The four ruling metrics + verdict inputs on one cell's OOS frame."""
    out = {"n_oos": int(len(oos)), "n_dates": int(oos["date"].nunique()) if len(oos) else 0}
    if oos.empty:
        out.update(c=np.nan, t_c=np.nan, d_ll_blend=np.nan, d_ll_model=np.nan,
                   ece_blend=np.nan, med_roi=np.nan, n_gated=0,
                   model_best_roi=np.nan, model_best_n=0, control_best_roi=np.nan,
                   control_best_n=0, ctrl_sub_roi=np.nan)
        return out
    y = oos["y"].to_numpy()
    s = MVM.score_model_vs_market(y, oos["p_sig"].to_numpy(), oos["p_mkt"].to_numpy(),
                                  oos["event_id"].to_numpy())
    blend = s.get("blend") or {}
    out["c"] = blend.get("c", np.nan)
    out["t_c"] = blend.get("t_c", np.nan)
    out["d_ll_blend"] = logloss(y, oos["p_blend"].to_numpy()) - logloss(y, oos["p_mkt"].to_numpy())
    out["d_ll_model"] = s["d_logloss"]
    out["ece_blend"] = ece(oos["p_blend"].to_numpy(), y)
    med_roi, n_gated = median_book_roi(oos)
    out["med_roi"], out["n_gated"] = med_roi, n_gated
    out.update(best_price_rois(oos))
    return out


def verdict(m: dict) -> tuple[str, list[str]]:
    """(verdict, reasons) under the pre-registered P8 candidate standard."""
    if m["n_oos"] < FLOOR_N_OOS or m["n_dates"] < FLOOR_DATES or m["n_gated"] < FLOOR_GATED:
        return "insufficient_data", [
            f"n_oos={m['n_oos']} (floor {FLOOR_N_OOS}), dates={m['n_dates']} "
            f"(floor {FLOOR_DATES}), gated={m['n_gated']} (floor {FLOOR_GATED})"]
    fails = []
    if not (np.isfinite(m["c"]) and m["c"] >= PASS_C):
        fails.append(f"c={m['c']:+.3f} < {PASS_C}")
    if not (np.isfinite(m["t_c"]) and m["t_c"] >= PASS_T):
        fails.append(f"t_c={m['t_c']:+.2f} < {PASS_T}")
    if not (np.isfinite(m["d_ll_blend"]) and m["d_ll_blend"] < 0):
        fails.append(f"dLL_blend={m['d_ll_blend']:+.5f} >= 0")
    if not (np.isfinite(m["ece_blend"]) and m["ece_blend"] <= PASS_ECE):
        fails.append(f"ECE={m['ece_blend']:.4f} > {PASS_ECE}")
    if not (np.isfinite(m["ctrl_sub_roi"]) and m["ctrl_sub_roi"] >= PASS_CTRL_SUB):
        fails.append(f"ctrl_sub_roi={m['ctrl_sub_roi']:+.3f} < {PASS_CTRL_SUB:+.2f}")
    return ("pass", ["all five candidate elements met"]) if not fails else ("kill", fails)


# --------------------------------------------------------------------- adapters
"""Each adapter returns (actuals, ambiguous_names): the master normalized to
columns [date, name_key, team_abbr, played, <one column per AN market name>],
with `date` aligned to the panel game key (UTC start date), plus the set of
name_keys shared by more than one master identity (no trailing history)."""

def _namesakes(m: pd.DataFrame) -> set:
    if "player_id" not in m.columns:
        return set()
    g = m.groupby("name_key")["player_id"].nunique()
    return set(g[g > 1].index)


def _game_map(cache_dir: str, key: str) -> dict:
    g = pd.read_csv(download_master(cache_dir, key), low_memory=False)
    return {r.game_id: frozenset({r.home_abbr, r.away_abbr})
            for r in g.itertuples(index=False)}


# AN team abbreviations -> master dialect, per league (only where they differ).
TEAM_ALIAS = {
    "nba": {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "PHX": "PHO",
            "UTAH": "UTA", "WSH": "WAS"},
    "nhl": {},
    "nfl": {},  # verified identical to nflverse codes (LA, WAS, JAX, ...)
    "mlb": {"ARI": "AZ", "OAK": "ATH"},  # master dialect = Statcast codes
}


def actuals_nba(cache_dir: str) -> dict:
    p = download_master(cache_dir, "NBA/player_boxscores_master.csv")
    m = pd.read_csv(p, low_memory=False)
    m["name_key"] = m["name"].map(norm_name)
    m["pra"] = m["points"] + m["rebounds"] + m["assists"]
    m["pr"] = m["points"] + m["rebounds"]
    m["pa"] = m["points"] + m["assists"]
    m = m.rename(columns={"three_points_made": "threes"})
    keep = ["date", "name_key", "team_abbr", "game_id", "played", "points",
            "rebounds", "assists", "threes", "pra", "pr", "pa", "blocks", "steals"]
    return {"actuals": m[[c for c in keep if c in m.columns]],
            "ambiguous": _namesakes(m),
            "game_map": _game_map(cache_dir, "NBA/games_master.csv"),
            "alias": TEAM_ALIAS["nba"]}


def actuals_nhl(cache_dir: str) -> dict:
    sk = pd.read_csv(download_master(cache_dir, "NHL/skater_boxscores_master.csv"),
                     low_memory=False)
    go = pd.read_csv(download_master(cache_dir, "NHL/goalie_boxscores_master.csv"),
                     low_memory=False)
    for m in (sk, go):
        m["name_key"] = m["name"].map(norm_name)
    sk = sk.rename(columns={"sog": "shots_on_goal"})
    cols_sk = ["date", "name_key", "team_abbr", "game_id", "played", "points",
               "assists", "goals", "shots_on_goal", "blocked_shots"]
    cols_go = ["date", "name_key", "team_abbr", "game_id", "played", "saves"]
    amb = _namesakes(sk) | _namesakes(go)
    out = pd.concat([sk[[c for c in cols_sk if c in sk.columns]],
                     go[[c for c in cols_go if c in go.columns]]], ignore_index=True)
    return {"actuals": out, "ambiguous": amb,
            "game_map": _game_map(cache_dir, "NHL/games_master.csv"),
            "alias": TEAM_ALIAS["nhl"]}


def actuals_nfl(cache_dir: str) -> dict:
    """nflverse player_stats (banked parquet, 2018-2026) joined to schedules
    for the local game date. No `played` flag exists in nflverse: a quoted
    player with no row stays unresolved (fail-closed). Counting stats are
    0-filled per nflverse convention (a recorded player-game row with a null
    counting stat recorded none of it). tackles_assists is deliberately NOT
    mapped: nflverse tackle-credit columns (solo / with-assist / assists) do
    not correspond unambiguously to the books' tackles+assists definition.
    longest_* markets need play-by-play and are not mapped either."""
    frames = []
    for yr in range(2018, 2027):
        p = download_master(cache_dir, f"NFL/nflverse/player_stats/player_stats_{yr}.parquet")
        frames.append(pd.read_parquet(p))
    ps = pd.concat(frames, ignore_index=True)
    sched = pd.read_parquet(download_master(
        cache_dir, "NFL/nflverse/schedules/schedules.parquet"))
    gmap = {r.game_id: frozenset({r.home_team, r.away_team})
            for r in sched.itertuples(index=False)}
    gameday = sched.set_index("game_id")["gameday"].astype(str).str.slice(0, 10)
    ps["date"] = ps["game_id"].map(gameday)
    ps = ps[ps["date"].notna()].copy()
    ps["name_key"] = ps["player_display_name"].map(norm_name)
    for c in ["passing_yards", "completions", "passing_tds", "attempts",
              "passing_interceptions", "rushing_yards", "rushing_tds",
              "receiving_yards", "receptions", "carries", "fg_made", "pat_made"]:
        if c in ps.columns:
            ps[c] = ps[c].fillna(0)
    ps["rushing_receiving_yards"] = ps["rushing_yards"] + ps["receiving_yards"]
    ps["passing_rushing_yards"] = ps["passing_yards"] + ps["rushing_yards"]
    ps["kicking_points"] = 3 * ps["fg_made"] + ps["pat_made"]
    ps = ps.rename(columns={"completions": "pass_completions",
                            "attempts": "passing_attempts",
                            "passing_interceptions": "interceptions",
                            "carries": "rushing_attempts",
                            "fg_made": "field_goals_made",
                            "pat_made": "extra_points_made",
                            "team": "team_abbr"})
    ps["played"] = True
    keep = ["date", "name_key", "team_abbr", "game_id", "played", "passing_yards",
            "pass_completions", "passing_tds", "passing_attempts", "interceptions",
            "rushing_yards", "rushing_tds", "receiving_yards", "receptions",
            "rushing_attempts", "rushing_receiving_yards", "passing_rushing_yards",
            "field_goals_made", "extra_points_made", "kicking_points"]
    return {"actuals": ps[[c for c in keep if c in ps.columns]],
            "ambiguous": _namesakes(ps), "game_map": gmap,
            "alias": TEAM_ALIAS["nfl"]}


_HIT_EVENTS = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
_EVENT_OUTS = {"field_out": 1, "force_out": 1, "fielders_choice_out": 1,
               "sac_fly": 1, "sac_bunt": 1, "strikeout": 1,
               "strikeout_double_play": 2, "double_play": 2, "triple_play": 3}


def _mlb_bridge() -> dict:
    """MLBAM id -> name_key, from the repo-banked headshot player map
    (slug -> MLBAM id, merged from active rosters; coverage is partial by
    construction, so unbridged players stay unresolved, never guessed)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "beezy-vip", "public", "headshots", "player_map.json")
    with open(path) as fh:
        slug2id = json.load(fh)
    return {int(v): k.replace("_", " ") for k, v in slug2id.items()}


def actuals_mlb(cache_dir: str) -> dict:
    """Banked actuals from two sources: the SB boxscore master (stolen_bases,
    names banked directly) and the Statcast PA master (batter hits/singles/
    doubles/triples/home_runs/total_bases/rbis and pitcher strikeouts/
    hits_allowed/walks/pitching_outs derived from PA-terminal events), with
    MLBAM identity bridged through the repo player map. earned_runs is NOT
    derivable from Statcast (no earned/unearned attribution) and stays
    unmapped. AN's `walks` market quotes pitchers (verified on the panel:
    Keller/Irvin/Verlander at 1.5-2.5), so walks = pitcher walks allowed."""
    bridge = _mlb_bridge()
    sc = pd.read_parquet(
        download_master(cache_dir, "Statcast/statcast_master.parquet"),
        columns=["pitcher", "batter", "game_pk", "game_date", "home_team",
                 "away_team", "inning_topbot", "events", "bat_score",
                 "post_bat_score"])
    sc = sc[sc["events"].notna() & (sc["events"] != "")].copy()
    gmap = {r.game_pk: frozenset({r.home_team, r.away_team}) for r in
            sc.drop_duplicates("game_pk").itertuples(index=False)}
    sc["bat_team"] = np.where(sc["inning_topbot"] == "Bot",
                              sc["home_team"], sc["away_team"])
    sc["pit_team"] = np.where(sc["inning_topbot"] == "Bot",
                              sc["away_team"], sc["home_team"])
    sc["hit_val"] = sc["events"].map(_HIT_EVENTS).fillna(0).astype(int)
    sc["is_hit"] = (sc["hit_val"] > 0).astype(int)
    sc["is_single"] = (sc["events"] == "single").astype(int)
    sc["is_double"] = (sc["events"] == "double").astype(int)
    sc["is_triple"] = (sc["events"] == "triple").astype(int)
    sc["is_hr"] = (sc["events"] == "home_run").astype(int)
    sc["is_k"] = sc["events"].isin(["strikeout", "strikeout_double_play"]).astype(int)
    sc["is_bb"] = (sc["events"] == "walk").astype(int)
    sc["rbi"] = (sc["post_bat_score"] - sc["bat_score"]).clip(lower=0)
    sc["outs"] = sc["events"].map(_EVENT_OUTS).fillna(0).astype(int)
    bat = sc.groupby(["game_date", "game_pk", "batter", "bat_team"], as_index=False).agg(
        hits=("is_hit", "sum"), singles=("is_single", "sum"),
        doubles=("is_double", "sum"), triples=("is_triple", "sum"),
        home_runs=("is_hr", "sum"), total_bases=("hit_val", "sum"),
        rbis=("rbi", "sum"))
    bat = bat.rename(columns={"batter": "player_id", "bat_team": "team_abbr",
                              "game_date": "date", "game_pk": "game_id"})
    pit = sc.groupby(["game_date", "game_pk", "pitcher", "pit_team"], as_index=False).agg(
        strikeouts=("is_k", "sum"), hits_allowed=("is_hit", "sum"),
        walks=("is_bb", "sum"), pitching_outs=("outs", "sum"))
    pit = pit.rename(columns={"pitcher": "player_id", "pit_team": "team_abbr",
                              "game_date": "date", "game_pk": "game_id"})
    both = pd.merge(bat, pit, on=["date", "game_id", "player_id", "team_abbr"],
                    how="outer")
    both["name_key"] = both["player_id"].map(bridge)
    n_unbridged = int(both["name_key"].isna().sum())
    both = both[both["name_key"].notna()].copy()
    both["played"] = True
    amb = _namesakes(both)
    sb = pd.read_csv(download_master(cache_dir, "Scoring/sb_boxscore_master.csv"),
                     low_memory=False)
    sb["name_key"] = sb["batter_name"].map(norm_name)
    sb["team_abbr"] = sb["team"].replace({"ARI": "AZ", "WAS": "WSH"})
    sb = sb.rename(columns={"game_date": "date", "game_pk": "game_id"})
    sb["played"] = True
    out = pd.merge(both, sb[["date", "name_key", "game_id", "team_abbr",
                             "stolen_bases"]],
                   on=["date", "name_key", "game_id"], how="outer",
                   suffixes=("", "_sb"))
    out["team_abbr"] = out["team_abbr"].fillna(out["team_abbr_sb"])
    out = out.drop(columns=["team_abbr_sb"])
    out["played"] = True
    print(f"mlb adapter: {len(both)} statcast player-games bridged, "
          f"{n_unbridged} unbridged (dropped, fail-closed)", flush=True)
    keep = ["date", "name_key", "team_abbr", "game_id", "played", "hits",
            "singles", "doubles", "triples", "home_runs", "total_bases",
            "rbis", "strikeouts", "hits_allowed", "walks", "pitching_outs",
            "stolen_bases"]
    return {"actuals": out[[c for c in keep if c in out.columns]],
            "ambiguous": amb, "game_map": gmap, "alias": TEAM_ALIAS["mlb"]}


ADAPTERS = {"nba": actuals_nba, "nhl": actuals_nhl, "nfl": actuals_nfl,
            "mlb": actuals_mlb}
DATE_MODE = {"nba": "utc", "nhl": "slate", "nfl": "et", "mlb": "slate"}
# Leagues with no adapter have no banked outcome source in this build; their
# cells are inventoried, never silently dropped (see run_screen).


# ----------------------------------------------------------------------- runner

PANEL_COLS = ["date", "event_id", "player_name", "team_abbr", "home_abbr",
              "away_abbr", "start_time", "market", "is_milestone", "book_id",
              "side", "line", "odds"]


def load_market_frames(cache_dir: str, league: str, market: str) -> list[pd.DataFrame]:
    dst = download_market(cache_dir, league, market)
    frames = []
    for f in sorted(os.listdir(dst)):
        if f.endswith(".parquet"):
            frames.append(pd.read_parquet(os.path.join(dst, f), columns=PANEL_COLS))
    return frames


def run_cell(cache_dir: str, league: str, market: str, adapter: dict | None,
             min_books: int = MIN_BOOKS) -> dict:
    rec: dict = {"league": league, "market": market}
    # Outcome-source checks come first: a cell that can never settle is
    # recorded without a panel build (its coverage facts live in the
    # inventory); panel builds are spent only on settleable cells.
    if adapter is None:
        rec["verdict"] = "no_outcome_source"
        return rec
    actuals = adapter["actuals"]
    if market not in actuals.columns:
        rec["verdict"] = "no_outcome_mapping"
        return rec
    frames = load_market_frames(cache_dir, league, market)
    panel, counts = build_panel(frames, min_books=min_books,
                                date_mode=DATE_MODE.get(league, "slate"))
    rec["panel_counts"] = counts
    settled, scounts = settle(panel, actuals, market, game_map=adapter["game_map"],
                              alias=adapter["alias"])
    rec["settle_counts"] = scounts
    settled = settled[settled["y"].notna()].copy()
    rec["settled_rows"] = int(len(settled))
    if settled.empty:
        rec["verdict"] = "insufficient_data"
        rec["reasons"] = ["zero settled rows after fail-closed join"]
        return rec
    settled = trailing_features(actuals, market, settled, adapter["ambiguous"])
    oos = walk_forward(settled)
    rec["n_oos_rows_pre_floor"] = int(len(oos))
    m = score_cell(oos)
    rec["metrics"] = {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                      for k, v in m.items()}
    v, reasons = verdict(m)
    rec["verdict"] = v
    rec["reasons"] = reasons
    return rec


def run_screen(cache_dir: str, leagues: list[str], min_books: int = MIN_BOOKS,
               markets_only: list[str] | None = None) -> list[dict]:
    board = []
    for league in leagues:
        markets = [m for m in list_an_markets(league) if is_main_market(m)]
        if markets_only:
            markets = [m for m in markets if m in markets_only]
        adapter = ADAPTERS[league](cache_dir) if league in ADAPTERS else None
        for market in markets:
            rec = run_cell(cache_dir, league, market, adapter, min_books)
            board.append(rec)
            print(f"{league}/{market}: {rec['verdict']}", flush=True)
    return board


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["run", "inventory"])
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/workspace/beezy/p8-screen-cache"))
    ap.add_argument("--leagues", default=",".join(LEAGUES))
    ap.add_argument("--markets", default=None, help="comma list to restrict markets")
    ap.add_argument("--min-books", type=int, default=MIN_BOOKS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]
    if args.command == "inventory":
        inv = {}
        for league in leagues:
            mk = list_an_markets(league)
            inv[league] = {"markets": mk,
                           "main_markets": [m for m in mk if is_main_market(m)],
                           "has_outcome_adapter": league in ADAPTERS}
            print(league, inv[league])
        if args.out:
            with open(args.out, "w") as f:
                json.dump(inv, f, indent=1)
        return 0
    board = run_screen(args.cache_dir, leagues, args.min_books,
                       markets_only=args.markets.split(",") if args.markets else None)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(board, f, indent=1, default=str)
    n_pass = sum(1 for r in board if r["verdict"] == "pass")
    print(f"BOARD: {len(board)} cells, {n_pass} passing candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
