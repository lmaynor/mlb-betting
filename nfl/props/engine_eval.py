"""Walk-forward replay of the SERVING projection engine (ev.daily_projections, `empirical_ewm_v1`) against outcomes.

    python -m nfl.props.engine_eval --seasons 2021-2025 --tune-through 2023 [--out report.md] [--cache rows.parquet]

Why. The NFL projection board that beezy.fyi serves is computed by `ev.daily_projections` (EWM + usage + opponent
factor), NOT by the trained XGBoost research models (their artifacts are local-only). Until now nothing measured THAT
code: every accuracy number in this repo belonged to the research models. This module replays the exact serving
functions (`project_mu`, `GameLogs.context`, `p_over`) as of every regular-season week, using only games strictly before
that week and the same two-season window the production loader keeps, and scores them against what happened.

What it measures (ACCURACY, not edge -- there are no historical NFL prop lines in this repo; see nfl.models.walkforward):
  * MAE / bias of the projection vs trailing baselines (L5 mean, EWM only, usage-adjusted without the opponent factor)
  * mean log score of the stat's distribution at the projection (paired against the baselines on the SAME rows)
  * Brier of P(over proxy line), proxy line = the L5 mean rounded to x.5 -- NOT a real book line

Variants. The parameters of the engine (EWM half-life, EWM-vs-usage weight, opponent shrinkage) were set by hand. The
replay scores a small grid of them. To keep that honest, variants are SELECTED per stat on the early seasons
(`--tune-through`) and only then scored on the later seasons that no selection step ever saw; the report shows the
held-out result of the pre-selected variant against the shipped default, paired, with a standard error. A variant is
adopted only if it beats the default on the held-out seasons by ADOPT_Z standard errors (multiplicity-corrected over
the 6 stats) AND the secondary guard agrees (MAE; Brier for the 0/1 anytime_td, where MAE is not a proper score), whatever its in-sample numbers say.

Eligibility mirrors the board: a player-stat is replayed only when the player's average usage over his last 10 games
clears the board's rotation threshold (ev.daily_projections.ROTATION_MIN_USAGE) -- the population a book would quote.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd
from scipy.stats import nbinom, norm

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf
from nfl.props import env_adjust as EA

logger = logging.getLogger(__name__)

NFL_COLS = ["player_id", "player_display_name", "player_name", "team", "season", "week", "season_type", "game_id",
            "opponent_team", "passing_yards", "passing_tds", "attempts", "rushing_yards", "carries",
            "receiving_yards", "receptions", "targets", "rushing_tds", "receiving_tds", "position"]
STATS = ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td")
# usage the player must average (last 10) to count as "book-quoted"; pass_tds follows pass_yds, anytime_td follows
# either rush or receiving usage (the board's rotation thresholds)
ELIG_USAGE = {"pass_yds": ("attempts", 15.0), "pass_tds": ("attempts", 15.0), "rush_yds": ("carries", 8.0),
              "rec_yds": ("targets", 4.0), "receptions": ("targets", 4.0)}
MIN_ROWS = 200
KEEP_SEASONS = cf.KEEP_SEASONS
# A held-out result is "adopt-worthy" only past a Bonferroni-style bar: 6 stats are scored, so a one-sided 5% test per
# stat needs z >= 2.64 (norm.ppf(1 - 0.05 / 12)), not 1.96. Plus the MAE must move the same way.
ADOPT_Z = float(norm.ppf(1 - 0.05 / (2 * len(STATS))))


@dataclass(frozen=True)
class Variant:
    """An engine parameterisation. `shipped=True` resolves per stat to what ev.daily_projections ships today
    (DP.stat_params); otherwise the three numbers below are used for every stat."""
    name: str
    halflife: float = 3.0
    ewm_weight: float = 0.5
    opp_k: float = 60.0
    shipped: bool = False
    opp_off: bool = False           # drop the opponent factor entirely

    def resolve(self, stat: str) -> tuple:
        return DP.stat_params("nfl", stat) if self.shipped else (self.halflife, self.ewm_weight, self.opp_k)


SHIPPED = Variant("default", shipped=True)                       # the engine as it is NOW (incl. validated overrides)
SHIPPED_NO_OPP = Variant("shipped_no_opp", shipped=True, opp_off=True)
LEGACY = Variant("legacy_v1")                                    # the original hand-set values (hl 3, w 0.5, k 60)
DEFAULT = SHIPPED
GRID = [SHIPPED, SHIPPED_NO_OPP, LEGACY] + [
    Variant(f"hl{hl:g}_w{w:g}_k{int(k)}", hl, w, k)
    for hl in (2.0, 3.0, 4.5, 7.0) for w in (0.3, 0.5, 0.8, 1.0) for k in (60.0, 200.0)
    if (hl, w, k) != (3.0, 0.5, 60.0)] + [
    Variant(f"hl{hl:g}_w{w:g}_koff", hl, w, 60.0, opp_off=True)
    for hl in (2.0, 3.0, 4.5, 7.0) for w in (0.3, 0.5, 0.8, 1.0)]


def load_player_logs(seasons: Optional[Iterable[int]] = None) -> pd.DataFrame:
    """player_stats shaped exactly like the production loader's frame (cockpit_form._load_nfl) for ALL given seasons."""
    from nfl.data.read import load
    df = load("player_stats", seasons=list(seasons) if seasons else None,
              columns=[c for c in NFL_COLS])
    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    for c in ("season", "week", "passing_yards", "passing_tds", "attempts", "rushing_yards", "carries",
              "receiving_yards", "receptions", "targets", "rushing_tds", "receiving_tds"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["season", "week"]).sort_values(["season", "week"], kind="stable").reset_index(drop=True)
    df["_name"] = df[name_col].map(cf.norm_name)
    df["_opp"] = df["opponent_team"]
    df["_sort"] = df["season"] * 100 + df["week"]
    return df


def _factor(raw, cnt, k) -> float:
    if k is None or raw is None:
        return 1.0
    w = cnt / (cnt + k)
    return float(min(max(1.0 + (raw - 1.0) * w, DP.OPP_CLIP[0]), DP.OPP_CLIP[1]))


def _eligible(stat: str, hist_rows: list) -> bool:
    if len(hist_rows) < DP.MIN_GAMES:
        return False
    if stat == "anytime_td":
        return any(_eligible(s, hist_rows) for s in ("rush_yds", "rec_yds"))
    col, thr = ELIG_USAGE[stat]
    use = [cf._sum_cols(r, (col,)) for r in hist_rows[-10:]]
    use = [u for u in use if u is not None]
    return bool(use) and sum(use) / len(use) >= thr


def replay(df: pd.DataFrame, test_seasons: Iterable[int], variants: Optional[list] = None,
           stats: Iterable[str] = STATS) -> pd.DataFrame:
    """One row per (target player-game, stat) with every variant's projection (`mu__<name>`), baselines and the as-of
    distribution context (alpha, cv). Regular-season target games only; history may include the postseason, exactly
    like production."""
    variants = variants or [SHIPPED]
    stats = list(stats)
    out = []
    for season in sorted(set(test_seasons)):
        weeks = sorted(df[(df["season"] == season) & (df["season_type"] == "REG")]["week"].unique())
        for week in weeks:
            t = season * 100 + int(week)
            hist_df = df[(df["_sort"] < t) & df["season"].isin([season - k for k in range(KEEP_SEASONS)])]
            if hist_df.empty:
                continue
            idx = cf._index(hist_df, "_name", None)
            logs = DP.GameLogs("nfl", {"all": idx})
            ctx_by_stat = {}
            targets = df[(df["_sort"] == t) & (df["season_type"] == "REG")]
            for r in targets.to_dict("records"):
                rows = idx.get(r["_name"])
                if not rows:
                    continue
                opp = DP.canon_team("nfl", r["_opp"]) if r["_opp"] is not None else None
                for stat in stats:
                    sdef = cf.NFL_STATS[stat]
                    if not _eligible(stat, rows):
                        continue
                    y = cf._sum_cols(r, sdef[0])
                    if y is None:
                        continue
                    h = DP._hist(rows, sdef)
                    if len(h) < DP.MIN_GAMES:
                        continue
                    ctx = ctx_by_stat.get(stat)
                    if ctx is None:
                        ctx = ctx_by_stat[stat] = logs.context(stat)
                    raw, cnt = ctx.opp_raw.get(opp), ctx.opp_cnt.get(opp, 0)
                    rec = {"season": season, "week": int(week), "player": r["_name"], "stat": stat, "y": float(y),
                           "alpha": ctx.alpha, "cv": ctx.cv, "n_games": len(h), "game_id": r.get("game_id"),
                           "team": DP.canon_team("nfl", r.get("team")), "opp": opp}
                    vals = np.array([g[0] for g in h], dtype=float)
                    rec["mu_l5"] = float(vals[-5:].mean())
                    cur = h[-1][2]
                    same = [g[0] for g in h if g[2] == cur]
                    rec["mu_szn"] = float(np.mean(same)) if same else float(vals.mean())
                    for v in variants:
                        hl, w, k = v.resolve(stat)
                        pj = DP.project_mu(h, hl, 1.0 if v.opp_off else _factor(raw, cnt, k), w)
                        rec[f"mu__{v.name}"] = None if pj is None else pj["mu"]
                    out.append(rec)
        logger.info("replayed season %s: %d rows so far", season, len(out))
    return pd.DataFrame(out)


# --------------------------------------------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------------------------------------------

FAMILY = {s: DP.PROJECT_STATS["nfl"][s] for s in STATS}


def _logscore(family: str, mu: np.ndarray, y: np.ndarray, alpha: np.ndarray, cv: np.ndarray,
              sd_mu: Optional[np.ndarray] = None) -> np.ndarray:
    """Log density/mass of y at mu under the engine's own family and as-of dispersion (mu floored like production).
    `sd_mu`: the mean the NORMAL family's spread is computed from (cv * sd_mu); defaults to mu. A calibrated mean keeps the
    spread of the uncalibrated one, because cv was measured relative to players' raw trailing means."""
    mu = np.clip(mu, 0.05, None)
    sd_mu = mu if sd_mu is None else np.clip(sd_mu, 0.05, None)
    if family == "negbin":
        n = 1.0 / alpha
        return nbinom.logpmf(np.round(y), n, n / (n + mu))
    if family == "anytime":
        p = 1.0 - np.exp(-mu)
        return np.where(y >= 1, np.log(np.clip(p, 1e-9, 1.0)), np.log(np.clip(1.0 - p, 1e-9, 1.0)))
    sd = np.maximum(np.maximum(cv * sd_mu, DP.MIN_SD_FRAC * sd_mu), 1e-6)
    return norm.logpdf(y, mu, sd)


def _p_over(family: str, mu: np.ndarray, line: np.ndarray, alpha, cv, sd_mu=None) -> np.ndarray:
    mu = np.clip(mu, 0.05, None)
    sd_mu = mu if sd_mu is None else np.clip(sd_mu, 0.05, None)
    if family == "negbin":
        n = 1.0 / alpha
        return 1.0 - nbinom.cdf(np.floor(line), n, n / (n + mu))
    if family == "anytime":
        return 1.0 - np.exp(-mu)
    sd = np.maximum(np.maximum(cv * sd_mu, DP.MIN_SD_FRAC * sd_mu), 1e-6)
    return 1.0 - norm.cdf((line - mu) / sd)


def proxy_line(base: np.ndarray, stat: str) -> np.ndarray:
    if stat == "anytime_td":
        return np.full_like(base, 0.5, dtype=float)
    return np.maximum(np.round(base - 0.5) + 0.5, 0.5)


def _score_col(res: pd.DataFrame, stat: str, col: str, line: np.ndarray, sd_col: Optional[str] = None) -> dict:
    fam = FAMILY[stat]
    mu = res[col].to_numpy(dtype=float)
    sd_mu = None if sd_col is None else res[sd_col].to_numpy(dtype=float)
    y = res["y"].to_numpy(dtype=float)
    a, c = res["alpha"].to_numpy(dtype=float), res["cv"].to_numpy(dtype=float)
    ls = _logscore(fam, mu, y, a, c, sd_mu)
    over = (y > line).astype(float)
    p = _p_over(fam, mu, line, a, c, sd_mu)
    return {"ls": ls, "ae": np.abs(y - mu), "br": (p - over) ** 2, "err": y - mu}


def paired(res: pd.DataFrame, stat: str, cand: str, base: str, cand_sd: Optional[str] = None,
           base_sd: Optional[str] = None) -> Optional[dict]:
    """Paired candidate-vs-base comparison on the rows where both exist (candidate/base are mu columns; `*_sd` name
    the column the normal family's spread is taken from, default = the mu column itself)."""
    r = res[(res["stat"] == stat)].dropna(subset=[cand, base]).reset_index(drop=True)
    if len(r) < MIN_ROWS:
        return None
    line = proxy_line(r["mu_l5"].to_numpy(dtype=float), stat)
    c, b = _score_col(r, stat, cand, line, cand_sd), _score_col(r, stat, base, line, base_sd)

    def se(x):
        return float(np.std(x, ddof=1) / math.sqrt(len(x)))
    d_ls, d_ae, d_br = c["ls"] - b["ls"], c["ae"] - b["ae"], c["br"] - b["br"]
    return {"n": len(r), "d_logscore": float(d_ls.mean()), "d_logscore_se": se(d_ls),
            "d_mae": float(d_ae.mean()), "d_mae_se": se(d_ae), "mae_base": float(b["ae"].mean()),
            "mae_cand": float(c["ae"].mean()), "d_brier": float(d_br.mean()), "d_brier_se": se(d_br),
            "bias_cand": float(c["err"].mean()), "bias_base": float(b["err"].mean())}


def select_variants(res: pd.DataFrame, variants: list, train_seasons: Iterable[int]) -> dict:
    """Per stat: the explicit-grid variant with the best mean log score on the TRAIN seasons only; ties and stats with
    too little data keep the REFERENCE (the original hand-set engine, LEGACY). The shipped variants are not candidates:
    they are what a previous run of this selection produced."""
    train = res[res["season"].isin(list(train_seasons))]
    chosen = {}
    cands = [v for v in variants if not v.shipped]
    for stat in STATS:
        r = train[train["stat"] == stat]
        best, best_ls = LEGACY.name, -np.inf
        if len(r) >= MIN_ROWS:
            line = proxy_line(r["mu_l5"].to_numpy(dtype=float), stat)
            for v in cands:
                col = f"mu__{v.name}"
                ok = r[col].notna().to_numpy()
                if ok.sum() < len(r) * 0.98:
                    continue
                ls = float(_score_col(r[ok], stat, col, line[ok])["ls"].mean())
                if ls > best_ls + (1e-9 if v.name != LEGACY.name else 0.0):
                    best, best_ls = v.name, ls
        chosen[stat] = best
    return chosen


def evaluate(res: pd.DataFrame, variants: list, tune_through: int, test_seasons: list) -> dict:
    """The full report data: (1) the shipped engine vs trailing baselines, vs no-opponent and vs the original
    hand-set engine on all test seasons; (2) the variant SELECTED on the early seasons vs the original engine on the
    held-out seasons only -- the evidence behind ev.daily_projections.STAT_PARAMS."""
    train = [s for s in test_seasons if s <= tune_through]
    held = [s for s in test_seasons if s > tune_through]
    res = res.copy()
    res["mu_default"] = res[f"mu__{SHIPPED.name}"]
    # A trailing mean of exactly 0 is a degenerate prediction (infinite loss on any non-zero outcome): floor the
    # BASELINES at 10% of the stat's mean outcome, as nfl.models.walkforward does -- generous to them, so beating
    # them is not an artefact.
    for stat in STATS:
        m = res["stat"] == stat
        if m.any():
            floor = max(0.1 * float(res.loc[m, "y"].mean()), 0.05)
            for c in ("mu_l5", "mu_szn"):
                res.loc[m, c] = res.loc[m, c].clip(lower=floor)
    have = set(res.columns)
    out = {"baselines": {}, "chosen": {}, "heldout": {}, "tuned_in_sample": {}, "shipped_vs_legacy_held": {}}
    for stat in STATS:
        sub = res[res["season"].isin(test_seasons)]
        out["baselines"][stat] = {
            "vs_l5": paired(sub, stat, "mu_default", "mu_l5"), "vs_szn": paired(sub, stat, "mu_default", "mu_szn"),
            "vs_no_opp": paired(sub, stat, "mu_default", f"mu__{SHIPPED_NO_OPP.name}")
            if f"mu__{SHIPPED_NO_OPP.name}" in have else None}
    chosen = select_variants(res, variants, train) if train and held and f"mu__{LEGACY.name}" in have else {}
    out["chosen"] = chosen
    if chosen:
        ref = f"mu__{LEGACY.name}"
        hs, ts = res[res["season"].isin(held)], res[res["season"].isin(train)]
        for stat in STATS:
            name = chosen[stat]
            sel = f"mu__{name}"
            out["heldout"][stat] = paired(hs, stat, sel, ref) if name != LEGACY.name else None
            out["tuned_in_sample"][stat] = paired(ts, stat, sel, ref) if name != LEGACY.name else None
            out["shipped_vs_legacy_held"][stat] = paired(hs, stat, "mu_default", ref)
    out["train_seasons"], out["held_seasons"] = train, held
    return out


CAL_MIN_SEASONS_AGREE = 4
CAL_NONINFERIOR_Z = -1.0          # log score may not be significantly WORSE than the baseline it calibrates


def calibration_walkforward(res: pd.DataFrame, env_tbl: pd.DataFrame, test_seasons: list) -> dict:
    """Walk-forward test of the two calibrations in nfl.props.env_adjust on top of the shipped engine.

    For each test season S the parameters are fitted on replayed rows of seasons < S ONLY. Per stat it returns, with
    pooled and per-season paired differences: `scale` (k * mu) vs the engine, `env` (spread/total term, incl. intercept)
    vs the engine, and `env_vs_scale` (what the game term adds on top of plain scaling). The normal family's spread is
    always taken from the UNCALIBRATED mean (sd_col = mu_default)."""
    r = res.merge(env_tbl[["game_id", "team", "team_spread", "implied_total"]], on=["game_id", "team"], how="left")
    r["x1"], r["x2"] = EA.env_features(r["implied_total"], r["team_spread"])
    r["mu_default"] = r[f"mu__{SHIPPED.name}"]
    out = {}
    for stat in STATS:
        folds = []
        params = {"scale": {}, "env": {}}
        for S in test_seasons:
            tr = r[(r["stat"] == stat) & (r["season"] < S)]
            ck = EA.fit_scale(tr["y"], tr["mu_default"])
            ce = EA.fit_coefs(tr["y"], tr["mu_default"], tr["x1"], tr["x2"])
            te = r[(r["stat"] == stat) & (r["season"] == S)].copy()
            te["mu_scale"] = EA.adjust(te["mu_default"], te["x1"], te["x2"], ck)
            te["mu_env"] = EA.adjust(te["mu_default"], te["x1"], te["x2"], ce)
            params["scale"][S], params["env"][S] = ck, ce
            if ck and ce:
                folds.append((S, te))
        entry = {"params": params}
        for key, (cand, base) in {"scale": ("mu_scale", "mu_default"), "env": ("mu_env", "mu_default"),
                                  "env_vs_scale": ("mu_env", "mu_scale")}.items():
            per = {S: paired(te, stat, cand, base, "mu_default", "mu_default") for S, te in folds}
            allr = pd.concat([te for _, te in folds]) if folds else None
            entry[key] = {"pooled": paired(allr, stat, cand, base, "mu_default", "mu_default") if allr is not None else None,
                          "per_season": per}
        out[stat] = entry
    return out


def _primary_metric(stat: str) -> tuple:
    """The metric a calibration must improve: MAE, or Brier for the 0/1 anytime_td (MAE is not proper there)."""
    return ("d_brier", "d_brier_se") if stat == "anytime_td" else ("d_mae", "d_mae_se")


def cal_verdict(stat: str, c: dict) -> str:
    """ADOPT when the primary metric improves by >= ADOPT_Z standard errors pooled AND in >= 4 test seasons, and the
    log score (spread held at the uncalibrated mean) is not significantly worse. (Rule fixed after an exploratory look
    that showed the over-projection bias is near-universal; the evidence it now needs is replication across seasons.)"""
    p = c["pooled"]
    if not p:
        return "no data"
    k, kse = _primary_metric(stat)
    z_primary = -p[k] / max(p[kse], 1e-12)                           # all three primary metrics: lower is better
    z_ls = p["d_logscore"] / max(p["d_logscore_se"], 1e-12)
    seasons = [q for q in c["per_season"].values() if q]
    better = sum(q[k] < 0 for q in seasons)
    if z_primary >= ADOPT_Z and better >= min(CAL_MIN_SEASONS_AGREE, len(seasons)) and z_ls >= CAL_NONINFERIOR_Z:
        return "ADOPT"
    if z_primary <= -ADOPT_Z:
        return "hurts"
    return "inconclusive: do not ship"


def render_calibration(cal: dict, test_seasons: list) -> str:
    L = ["", f"## Calibration of the projection mean on top of the shipped engine, walk-forward {test_seasons[0]}-{test_seasons[-1]}", "",
         "Parameters for season S are fitted on replayed rows of seasons < S only. `scale`: mu * k. `env`: mu * exp(b0 + b1 "
         "ln(implied team total / 22.5) + b2 team spread / 7). The normal family's spread stays tied to the UNCALIBRATED "
         "mean. Adopt = the primary metric (MAE; Brier for anytime_td) improves >= ADOPT_Z standard errors AND in >= 4 test "
         "seasons AND log score is not significantly worse. `env vs scale` = what the game term adds on top of scaling.", "",
         "| stat | candidate | n | d_logscore (se) | d_mae (se) | d_brier (se) | seasons better (primary) | verdict |",
         "|---|---|---:|---:|---:|---:|---:|---|"]
    for stat, e in cal.items():
        for key, lab in (("scale", "scale"), ("env", "env"), ("env_vs_scale", "env vs scale")):
            c = e[key]
            p = c["pooled"]
            if not p:
                L.append(f"| {stat} | {lab} | - | - | - | - | - | no data |")
                continue
            k, _ = _primary_metric(stat)
            seasons = [q for q in c["per_season"].values() if q]
            better = sum(q[k] < 0 for q in seasons)
            L.append(f"| {stat} | {lab} | {p['n']} | {_cell(p, 'd_logscore', 'd_logscore_se')} | "
                     f"{_cell(p, 'd_mae', 'd_mae_se', 3)} | {_cell(p, 'd_brier', 'd_brier_se', 5)} | "
                     f"{better}/{len(seasons)} | {cal_verdict(stat, c)} |")
    L += ["", "Final-fold parameters (fitted on seasons before " + str(test_seasons[-1]) + "):", "",
          "| stat | k (scale) | b0 | b1 (total) | b2 (spread) |", "|---|---:|---:|---:|---:|"]
    for stat, e in cal.items():
        ck = e["params"]["scale"].get(test_seasons[-1]) or {}
        ce = e["params"]["env"].get(test_seasons[-1]) or {}
        L.append(f"| {stat} | {ck.get('k', float('nan')):.4f} | {ce.get('b0', float('nan')):+.3f} | "
                 f"{ce.get('b1', float('nan')):+.3f} | {ce.get('b2', float('nan')):+.3f} |")
    return "\n".join(L) + "\n"


def _secondary_ok(stat: str, h: dict) -> bool:
    """The guard that must agree with the log-score gain. MAE for count / yardage stats; Brier for anytime_td, where
    the outcome is 0/1 and MAE of a probability against it is NOT a proper score (it rewards hugging 0)."""
    return h["d_brier"] < 0 if stat == "anytime_td" else h["d_mae"] < 0


def _cell(p: Optional[dict], k: str, se: str, nd: int = 4) -> str:
    return "-" if not p else f"{p[k]:+.{nd}f} ({p[se]:.{nd}f})"


def render(ev: dict, test_seasons: list, n_rows: int) -> str:
    L = ["# Serving-engine replay (`empirical_ewm_v1`), walk-forward, accuracy only", "",
         f"Seasons {test_seasons[0]}-{test_seasons[-1]}, {n_rows:,} player-stat rows replayed as of each week (history strictly "
         "before it, two-season window like production). **There are no historical prop lines in this repo: nothing here "
         "measures edge.** Paired differences are `shipped engine - baseline`; positive d_logscore / negative d_mae "
         "favour the engine. Standard errors are paired over rows.", "",
         "## Shipped engine vs baselines", "",
         "| stat | vs | n | d_logscore (se) | d_mae (se) | engine MAE | baseline MAE | d_brier (se) |",
         "|---|---|---:|---:|---:|---:|---:|---:|"]
    for stat, b in ev["baselines"].items():
        for lab, key in (("L5 mean", "vs_l5"), ("season mean", "vs_szn"), ("same engine, no opp factor", "vs_no_opp")):
            p = b.get(key)
            if p:
                L.append(f"| {stat} | {lab} | {p['n']} | {_cell(p, 'd_logscore', 'd_logscore_se')} | "
                         f"{_cell(p, 'd_mae', 'd_mae_se', 3)} | {p['mae_cand']:.3f} | {p['mae_base']:.3f} | "
                         f"{_cell(p, 'd_brier', 'd_brier_se', 5)} |")
    if ev["chosen"]:
        L += ["", f"## Parameter variants vs the ORIGINAL engine: selected on {ev['train_seasons']}, scored on held-out {ev['held_seasons']}", "",
              "| stat | selected variant | held-out n | held-out d_logscore (se) | held-out d_mae (se) | "
              "held-out d_brier (se) | in-sample d_logscore (se) | verdict |", "|---|---|---:|---:|---:|---:|---:|---|"]
        for stat in STATS:
            name = ev["chosen"].get(stat, LEGACY.name)
            h, t = ev["heldout"].get(stat), ev["tuned_in_sample"].get(stat)
            if name == LEGACY.name or not h:
                L.append(f"| {stat} | {LEGACY.name} | - | - | - | - | - | keep original |")
                continue
            z = h["d_logscore"] / max(h["d_logscore_se"], 1e-12)
            verdict = ("ADOPT" if z >= ADOPT_Z and _secondary_ok(stat, h)
                       else ("hurts" if z <= -ADOPT_Z else "inconclusive: keep original"))
            L.append(f"| {stat} | {name} | {h['n']} | {_cell(h, 'd_logscore', 'd_logscore_se')} | "
                     f"{_cell(h, 'd_mae', 'd_mae_se', 3)} | {_cell(h, 'd_brier', 'd_brier_se', 5)} | "
                     f"{_cell(t, 'd_logscore', 'd_logscore_se')} | {verdict} |")
    if ev.get("shipped_vs_legacy_held"):
        L += ["", f"## Shipped engine vs the original hand-set engine, held-out seasons {ev['held_seasons']} (paired)", "",
              "| stat | n | d_logscore (se) | d_mae (se) | d_brier (se) |", "|---|---:|---:|---:|---:|"]
        for stat in STATS:
            p = ev["shipped_vs_legacy_held"].get(stat)
            if p:
                L.append(f"| {stat} | {p['n']} | {_cell(p, 'd_logscore', 'd_logscore_se')} | "
                         f"{_cell(p, 'd_mae', 'd_mae_se', 3)} | {_cell(p, 'd_brier', 'd_brier_se', 5)} |")
    return "\n".join(L) + "\n"


def _write_calibration(res: pd.DataFrame, env_tbl: pd.DataFrame, cal: dict, seasons: list, path: str) -> None:
    """Fit on every replayed row and write JSON, but ONLY for stats whose walk-forward verdict is ADOPT. Mode `env` is
    written only when the game term adds to scaling (env_vs_scale ADOPT) -- otherwise `scale`. A stat that did not clear
    the bar gets no entry, so the serving path leaves it untouched."""
    import json
    from datetime import datetime, timezone
    r = res.merge(env_tbl[["game_id", "team", "team_spread", "implied_total"]], on=["game_id", "team"], how="left")
    r["x1"], r["x2"] = EA.env_features(r["implied_total"], r["team_spread"])
    r["mu_default"] = r[f"mu__{SHIPPED.name}"]
    coefs, evidence = {}, {}
    for stat in STATS:
        e = cal[stat]
        v_scale, v_env_inc = cal_verdict(stat, e["scale"]), cal_verdict(stat, e["env_vs_scale"])
        mode = "env" if (v_env_inc == "ADOPT" and v_scale == "ADOPT") else ("scale" if v_scale == "ADOPT" else "none")
        sub = r[r["stat"] == stat]
        if mode == "env":
            c = EA.fit_coefs(sub["y"], sub["mu_default"], sub["x1"], sub["x2"])
        elif mode == "scale":
            c = EA.fit_scale(sub["y"], sub["mu_default"])
        else:
            c = None
        coefs[stat] = c
        ev = {"mode": mode, "scale_verdict": v_scale, "env_vs_scale_verdict": v_env_inc}
        for key in ("scale", "env_vs_scale"):
            p = e[key]["pooled"]
            if p:
                ev[key] = {k: p[k] for k in ("n", "d_logscore", "d_logscore_se", "d_mae", "d_mae_se", "d_brier", "d_brier_se")}
        evidence[stat] = ev
    payload = {"model": "mode scale: mu*k | mode env: mu*exp(b0 + b1*ln(implied_team_total/22.5) + b2*team_spread/7)",
               "fitted_on_seasons": [int(res["season"].min()), int(res["season"].max())],
               "fitted_at": datetime.now(timezone.utc).isoformat(),
               "validation": f"walk-forward {seasons[0]}-{seasons[-1]} (python -m nfl.props.engine_eval --calibrate)",
               "coefs": coefs, "evidence": evidence}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    logger.info("wrote %s (calibrated stats: %s)", path, {k: ("env" if v and "b0" in v else "scale") for k, v in coefs.items() if v})


def parse_seasons(spec: str) -> list:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", default="2021-2025", help="test seasons to replay")
    ap.add_argument("--tune-through", type=int, default=2023, help="variants are selected on seasons <= this")
    ap.add_argument("--no-grid", action="store_true", help="score only the shipped engine (fast)")
    ap.add_argument("--warmup", type=int, default=2,
                    help="extra seasons replayed BEFORE the first test season (training rows for the env fit)")
    ap.add_argument("--calibrate", action="store_true",
                    help="also run the mean-calibration walk-forward (scale, and spread/total game term)")
    ap.add_argument("--write-calibration", metavar="PATH",
                    help="fit the ADOPTED calibrations on ALL replayed rows and write them as JSON (implies --calibrate)")
    ap.add_argument("--cache", help="parquet path: load replayed rows if it exists, else replay + save")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seasons = parse_seasons(args.seasons)
    variants = [SHIPPED] if args.no_grid else GRID
    import os
    if args.cache and os.path.exists(args.cache):
        res = pd.read_parquet(args.cache)
    else:
        replay_seasons = list(range(min(seasons) - args.warmup, max(seasons) + 1))
        df = load_player_logs(range(min(replay_seasons) - KEEP_SEASONS + 1, max(seasons) + 1))
        res = replay(df, replay_seasons, variants)
        if args.cache:
            res.to_parquet(args.cache)
    ev = evaluate(res[res["season"].isin(seasons)], variants, args.tune_through, seasons)
    report = render(ev, seasons, int(res["season"].isin(seasons).sum()))
    if args.calibrate or args.write_calibration:
        from nfl.data.read import load
        env_tbl = EA.game_env_table(load("schedules"))
        cal = calibration_walkforward(res, env_tbl, seasons)
        report += render_calibration(cal, seasons)
        if args.write_calibration:
            _write_calibration(res, env_tbl, cal, seasons, args.write_calibration)
    print(report)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
