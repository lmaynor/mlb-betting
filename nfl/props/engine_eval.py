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
OPTIONAL_COLS = ["completions", "passing_interceptions", "passing_air_yards", "passing_yards_after_catch", "passing_cpoe",
                 "receiving_air_yards", "receiving_yards_after_catch"]
STATS = ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td")
# usage the player must average (last 10) to count as "book-quoted"; pass_tds follows pass_yds, anytime_td follows
# either rush or receiving usage (the board's rotation thresholds)
ELIG_USAGE = {"pass_yds": ("attempts", 15.0), "pass_tds": ("attempts", 15.0), "rush_yds": ("carries", 8.0),
              "rec_yds": ("targets", 4.0), "receptions": ("targets", 4.0),
              "pass_attempts": ("attempts", 15.0), "pass_completions": ("attempts", 15.0),
              "interceptions": ("attempts", 15.0), "rush_attempts": ("carries", 8.0)}
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
    seas = list(seasons) if seasons else None
    try:                                  # optional columns (hierarchical engine); older mirrors lack some of them
        df = load("player_stats", seasons=seas, columns=NFL_COLS + OPTIONAL_COLS)
    except Exception:                                                                    # noqa: BLE001
        df = load("player_stats", seasons=seas, columns=[c for c in NFL_COLS])
    for c in OPTIONAL_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
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



# --------------------------------------------------------------------------------------------------------------
# hierarchical engine vs the shipped Normal / NegBin baseline (nfl.models.hierarchical_props), held-out seasons
# --------------------------------------------------------------------------------------------------------------
HIER_STATS = ("pass_attempts", "pass_completions", "pass_yds", "pass_tds", "interceptions", "rush_attempts", "rush_yds",
              "receptions", "rec_yds", "anytime_td")
MU_FLOOR = 0.05                  # = the floor _logscore applies to the baseline's mean
MASS_FLOOR = 1e-4                # common floor on a count / 1-unit-bin mass (both engines)
ALT_P = {"plus200": 1.0 / 3.0, "plus400": 1.0 / 5.0}          # fair P(over) at +200 / +400 American odds
ADOPT_Z_HIER = float(norm.ppf(1 - 0.05 / (2 * len(HIER_STATS))))
ADOPT_Z_BRIER = 1.645           # the secondary guard must itself be significant (one-sided 5%), not merely the right sign
_CRPS_GRID = {"yards": np.arange(0, 601, 2.0), "count": np.arange(0, 61, 1.0)}


class _BaseDist:
    """The shipped engine's distribution at (mu, sd_mu) with the as-of dispersion, exposing cdf / mass / ppf / sf."""

    def __init__(self, family: str, mu: float, sd_mu: float, alpha: float, cv: float):
        self.family, self.mu = family, max(mu, 0.05)
        self.sd_mu = max(sd_mu, 0.05)
        self.alpha, self.cv = alpha, cv

    def _sd(self) -> float:
        return max(self.cv * self.sd_mu, DP.MIN_SD_FRAC * self.sd_mu, 1e-6)

    def cdf(self, x) -> np.ndarray:
        x = np.floor(np.asarray(x, dtype=float))
        if self.family == "negbin":
            n = 1.0 / self.alpha
            return nbinom.cdf(x, n, n / (n + self.mu))
        if self.family == "anytime":
            return np.where(x < 0, 0.0, np.where(x < 1, math.exp(-self.mu), 1.0))
        return norm.cdf((x + 0.5 - self.mu) / self._sd())

    def mass(self, y) -> np.ndarray:
        y = np.round(np.asarray(y, dtype=float))
        if self.family == "anytime":                              # 0/1 indicator, like the engine's own log score
            p = 1.0 - math.exp(-self.mu)
            return np.clip(np.where(y >= 1, p, 1.0 - p), 1e-9, 1.0)
        return np.clip(self.cdf(y) - self.cdf(y - 1.0), 1e-12, 1.0)

    def sf(self, line: float) -> float:
        return float(1.0 - self.cdf(math.floor(line)))

    def ppf(self, q: float) -> float:
        if self.family == "negbin":
            n = 1.0 / self.alpha
            return float(nbinom.ppf(q, n, n / (n + self.mu)))
        if self.family == "anytime":
            return 0.0 if q <= math.exp(-self.mu) else 1.0
        return float(max(norm.ppf(q, self.mu, self._sd()), 0.0))


def _crps(dist, y: float, stat: str, family: str) -> float:
    """Discrete CRPS on a fixed integer grid (yards: step 2). The binary anytime market's CRPS equals its Brier score."""
    if family == "anytime":
        p = float(1.0 - dist.cdf(0.0)) if isinstance(dist, _BaseDist) else float(1.0 - dist.cdf(0.0))
        return (p - float(y >= 1)) ** 2
    grid = _CRPS_GRID["yards" if stat in ("pass_yds", "rush_yds", "rec_yds") else "count"]
    step = float(grid[1] - grid[0])
    f = np.asarray(dist.cdf(grid), dtype=float)
    return float(np.sum((f - (grid >= y).astype(float)) ** 2) * step)


def _as_of_env(env_tbl: pd.DataFrame) -> dict:
    """{(season, week, team): namespace(implied_total, team_spread, roof, wind, temp, season, week, home)} from the schedule."""
    import types
    out = {}
    cols = set(env_tbl.columns)
    for r in env_tbl.to_dict("records"):
        out[(int(r["season"]), int(r["week"]), r["team"])] = types.SimpleNamespace(
            implied_total=r["implied_total"], team_spread=r["team_spread"],
            roof=r.get("roof") if "roof" in cols else None, wind=r.get("wind") if "wind" in cols else None,
            temp=r.get("temp") if "temp" in cols else None, season=int(r["season"]), week=int(r["week"]),
            home=r.get("home_team"))
    return out


MARKET_STAT = {"pass_att": "pass_attempts", "pass_cmp": "pass_completions", "pass_int": "interceptions", "pass_yds": "pass_yds",
               "pass_tds": "pass_tds", "rec_yds": "rec_yds", "receptions": "receptions", "rush_att": "rush_attempts",
               "rush_yds": "rush_yds"}                       # BettingPros loader label -> this module's stat


def _over_under(dist, line: float) -> tuple:
    """(P(over), P(under)) at `line` for a baseline / hierarchical distribution; an integer line can push."""
    over = float(dist.sf(line))
    if float(line) == math.floor(line):
        under = float(np.asarray(dist.cdf(line - 1.0)).reshape(-1)[0])
    else:
        under = 1.0 - over
    return over, under


def build_market_table(consensus: pd.DataFrame, resolver, min_books: int = 3) -> tuple:
    """Real-line market rows -> ({(gsis_id, game_date, stat): {line, p_market, n_books}}, coverage counts).

    Same identity and screening rules as nfl.props.history_join (fail-closed resolver, >= min_books two-sided reference books,
    no postgame quotes, duplicates dropped and counted)."""
    from mlb_core.odds import model_vs_market as MVM
    from nfl.props import history_join as HJ
    cons, n_in, dropped = MVM.screen_consensus(consensus, MARKET_STAT, min_books)
    dropped = dict(dropped, unresolved=0, ambiguous=0, duplicate=0)
    out: dict = {}
    if len(cons):
        ids, bad = HJ._resolve_rows(cons, resolver)
        dropped["unresolved"], dropped["ambiguous"] = bad["unresolved"], bad["ambiguous"]
        cons = cons.assign(gsis_id=ids)
        cons = cons[cons["gsis_id"].notna()]
        dup = cons.duplicated(["gsis_id", "game_date", "model_stat"], keep=False)
        dropped["duplicate"] = int(dup.sum())
        for r in cons[~dup].to_dict("records"):
            out[(r["gsis_id"], str(r["game_date"])[:10], r["model_stat"])] = {
                "line": float(r["line"]), "p_market": float(r["p_over_cons"]), "n_books": int(r["n_books"])}
    return out, {"consensus_rows": n_in, "matched_identity": len(out), **dropped}


def replay_hierarchical(df: pd.DataFrame, test_seasons: Iterable[int], env_tbl: pd.DataFrame,
                        stats: Iterable[str] = HIER_STATS, live=None, market: Optional[dict] = None) -> pd.DataFrame:
    """One row per (target player-game, stat): the SHIPPED engine's and the HIERARCHICAL engine's as-of price of the same
    outcome, scored on identical rows. History for week w is games strictly before w (the production two-season window);
    the schedule's closing spread / total are the game environment both engines may use (they are pre-game numbers).

    Columns: season, week, player, stat, y, line (proxy = L5 mean rounded to x.5), mu_base / mu_hier, ls_base / ls_hier
    (log of the 1-unit probability mass of the outcome: identical binning for both), p_base / p_hier (P(over proxy line)),
    crps_base / crps_hier, and for each alt rung (fair +200 / +400, set from the BASELINE's own quantile) p_base_<k> /
    p_hier_<k> / hit_<k>."""
    from nfl.models import hierarchical_live as HL
    env_map = _as_of_env(env_tbl)
    stats = list(stats)
    gd_map = dict(zip(env_tbl["game_id"], env_tbl["gameday"].astype(str).str[:10])) if market is not None else {}
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
            team_ctx = HL.build_team_ctx(idx)
            alpha = HL.AlphaLookup(logs.context)
            targets = df[(df["_sort"] == t) & (df["season_type"] == "REG")]
            for r in targets.to_dict("records"):
                rows = idx.get(r["_name"])
                if not rows:
                    continue
                team = DP.canon_team("nfl", r.get("team"))
                opp = DP.canon_team("nfl", r["_opp"]) if r["_opp"] is not None else None
                env = env_map.get((int(season), int(week), team))
                for stat in stats:
                    mk = None
                    if market is not None:           # REAL-LINE mode: score at the book consensus line, only where one exists
                        mk = market.get((r.get("player_id"), gd_map.get(r.get("game_id")), stat))
                        if mk is None:
                            continue
                    elif not _eligible(stat, rows):
                        continue
                    y = cf._sum_cols(r, cf.NFL_STATS[stat][0])
                    h = DP._hist(rows, cf.NFL_STATS[stat])
                    if y is None or len(h) < DP.MIN_GAMES:
                        continue
                    family = DP.PROJECT_STATS["nfl"][stat]
                    ctx = logs.context(stat)
                    hl, w, k = DP.stat_params("nfl", stat)
                    raw, cnt = ctx.opp_raw.get(opp), ctx.opp_cnt.get(opp, 0)
                    pj = DP.project_mu(h, hl, _factor(raw, cnt, k), w)
                    if pj is None:
                        continue
                    coef = DP.calibration("nfl", stat)
                    it = getattr(env, "implied_total", None) if env is not None else None
                    sp = getattr(env, "team_spread", None) if env is not None else None
                    it = None if it is None or not math.isfinite(it) else it
                    sp = None if sp is None or not math.isfinite(sp) else sp
                    mult = EA.multiplier(coef, implied_total=it, team_spread=sp, stat="") if (coef or it is not None) else 1.0
                    mu_base = pj["mu"] * mult
                    base = _BaseDist(family, mu_base, pj["mu"], ctx.alpha, ctx.cv)
                    hr = HL.price_row(stat, rows, team, team_ctx, env if it is not None else None, alpha, coef, live=live,
                                      mu_engine=pj["mu"] if stat == "anytime_td" else None, pid=r.get("player_id"), opp=opp)
                    if hr is None:
                        continue
                    vals = np.array([g[0] for g in h], dtype=float)
                    l5 = float(vals[-5:].mean())
                    line = float(proxy_line(np.array([l5]), stat)[0]) if mk is None else mk["line"]
                    # Both engines are scored on the SAME footing: the baseline's log score floors mu at MU_FLOOR (see _logscore), so
                    # the hierarchical side gets the equivalent floor (a player with ~0 projected TDs who scores must not cost
                    # -20 nats on one side only), and count / yard masses share one floor.
                    if family == "anytime":      # the market is 1+ TD: the 0/1 indicator for BOTH engines
                        p1 = min(max(hr.p_over(0.5), 1.0 - math.exp(-MU_FLOOR)), 1.0 - 1e-9)
                        ls_hier = float(np.log(p1 if y >= 1 else 1.0 - p1))
                        ls_base = float(np.log(base.mass(y)))
                    else:
                        ls_hier = float(np.log(max(float(hr.dist.mass(y)), MASS_FLOOR)))
                        ls_base = float(np.log(max(float(base.mass(y)), MASS_FLOOR)))
                    rec = {"season": season, "week": int(week), "player": r["_name"], "stat": stat, "y": float(y),
                           "line": line, "mu_base": mu_base, "mu_hier": hr.mean,
                           "ls_base": ls_base, "ls_hier": ls_hier,
                           "p_base": base.sf(line), "p_hier": hr.p_over(line),
                           "crps_base": _crps(base, float(y), stat, family), "crps_hier": _crps(hr.dist, float(y), stat, family)}
                    if mk is not None:
                        ob, ub = _over_under(base, line)
                        oh, uh = _over_under(hr.dist, line)
                        if float(y) == line:         # push: no outcome on either side (removed from every real-line score)
                            continue
                        rec.update({"p_market": mk["p_market"], "n_books": mk["n_books"], "game_id": r.get("game_id"),
                                    "over": float(y > line), "p_base": ob / max(ob + ub, 1e-9), "p_hier": oh / max(oh + uh, 1e-9)})
                    if family != "anytime":
                        for name, p in ALT_P.items():
                            lq = base.ppf(1.0 - p)                     # a rung the baseline prices at ~p
                            rec[f"line_{name}"] = lq
                            rec[f"p_base_{name}"] = base.sf(lq)
                            rec[f"p_hier_{name}"] = hr.p_over(lq)
                            rec[f"hit_{name}"] = float(y > lq)
                    out.append(rec)
        logger.info("hierarchical replay season %s: %d rows so far", season, len(out))
    return pd.DataFrame(out)


MARKET_MIN_ROWS = 300      # real-line rows a stat needs before its Brier z may replace the proxy-line Brier z


def compare_hierarchical(res: pd.DataFrame, test_seasons: Iterable[int], market_z: Optional[dict] = None) -> dict:
    """Paired hierarchical-minus-baseline differences per stat on the held-out seasons (+ per-season consistency).

    d_logscore > 0, d_brier < 0, d_crps < 0 favour the hierarchical engine. `tails[k]` = mean predicted P(over) vs the realised
    hit rate at the fair +200 / +400 rung (calibration error = predicted - realised; closer to 0 is better).
    verdict ADOPT needs pooled log-score z >= ADOPT_Z_HIER (Bonferroni over the 10 stats), the Brier gain itself significant (z >= ADOPT_Z_BRIER), and the log score
    better in at least 4/5 test seasons (all of them when fewer than 5 are scored)."""
    seasons = sorted(set(test_seasons))
    r = res[res["season"].isin(seasons)]
    out: dict = {}

    def se(x):
        return float(np.std(x, ddof=1) / math.sqrt(len(x))) if len(x) > 1 else float("nan")
    for stat in HIER_STATS:
        d = r[r["stat"] == stat]
        if len(d) < MIN_ROWS:
            out[stat] = {"n": int(len(d)), "verdict": "INSUFFICIENT"}
            continue
        over = (d["y"] > d["line"]).astype(float).to_numpy()
        dls = (d["ls_hier"] - d["ls_base"]).to_numpy()
        dbr = ((d["p_hier"] - over) ** 2 - (d["p_base"] - over) ** 2).to_numpy()
        dcr = (d["crps_hier"] - d["crps_base"]).to_numpy()
        per_season = {int(s): float((g["ls_hier"] - g["ls_base"]).mean()) for s, g in d.groupby("season")}
        z = float(dls.mean() / se(dls)) if se(dls) > 0 else float("nan")
        zb = float(-dbr.mean() / se(dbr)) if se(dbr) > 0 else float("nan")
        zb = -float("inf") if zb != zb else zb
        brier_source = "proxy_line"
        mk = (market_z or {}).get(stat)
        if mk is not None and mk.get("n", 0) >= MARKET_MIN_ROWS and mk.get("z_brier") == mk.get("z_brier"):
            zb, brier_source = float(mk["z_brier"]), "real_line"       # the Brier at the BOOK's line is the honest secondary guard
        n_better = sum(1 for v in per_season.values() if v > 0)
        need = 4 if len(per_season) >= 5 else len(per_season)
        verdict = "ADOPT" if (z >= ADOPT_Z_HIER and zb >= ADOPT_Z_BRIER and n_better >= need) else (
            "WORSE" if z <= -ADOPT_Z_HIER else "NO CLEAR GAIN")
        tails = {}
        for name in ALT_P:
            if f"hit_{name}" in d.columns:
                t = d.dropna(subset=[f"hit_{name}"])
                if len(t):
                    tails[name] = {"n": int(len(t)), "realised": float(t[f"hit_{name}"].mean()),
                                   "pred_base": float(t[f"p_base_{name}"].mean()),
                                   "pred_hier": float(t[f"p_hier_{name}"].mean())}
        out[stat] = {"n": int(len(d)), "d_logscore": float(dls.mean()), "d_logscore_se": se(dls), "z_logscore": z,
                     "d_brier": float(dbr.mean()), "d_brier_se": se(dbr), "d_crps": float(dcr.mean()),
                     "d_crps_se": se(dcr), "mae_base": float(np.abs(d["y"] - d["mu_base"]).mean()),
                     "mae_hier": float(np.abs(d["y"] - d["mu_hier"]).mean()), "per_season_d_logscore": per_season,
                     "seasons_better": n_better, "tails": tails, "verdict": verdict,
                     "z_brier": zb, "brier_source": brier_source}
    return out


def render_hierarchical(cmp: dict, test_seasons: list) -> str:
    lines = ["", "== hierarchical engine vs shipped Normal/NegBin baseline "
             f"(held-out seasons {test_seasons[0]}-{test_seasons[-1]}; ACCURACY only, not edge) ==",
             "stat              n   d_logscore (se)      d_brier (se)         d_crps       MAE base->hier  seasons+  verdict"]
    for stat in HIER_STATS:
        c = cmp.get(stat, {})
        if "d_logscore" not in c:
            lines.append(f"{stat:<16}{c.get('n', 0):>5}   {c.get('verdict', 'n/a')}")
            continue
        lines.append(f"{stat:<16}{c['n']:>5}   {c['d_logscore']:+.4f} ({c['d_logscore_se']:.4f})   "
                     f"{c['d_brier']:+.4f} ({c['d_brier_se']:.4f})   {c['d_crps']:+.4f}   "
                     f"{c['mae_base']:.3f}->{c['mae_hier']:.3f}   {c['seasons_better']}/{len(c['per_season_d_logscore'])}"
                     f"       {c['verdict']}")
    lines.append("tail calibration at the fair +200 / +400 rung (predicted P(over) vs realised hit rate):")
    for stat in HIER_STATS:
        for name, t in cmp.get(stat, {}).get("tails", {}).items():
            lines.append(f"  {stat:<16}{name:<8} n={t['n']:<6} realised {t['realised']:.3f}  baseline {t['pred_base']:.3f}  "
                         f"hierarchical {t['pred_hier']:.3f}")
    lines.append(f"adoption bar: pooled z >= {ADOPT_Z_HIER:.2f} on log score, Brier z >= {ADOPT_Z_BRIER:.2f}, better in >= 4/5 seasons. "
                 "Until a stat clears it, NFL_ENGINE stays 'ewm'.")
    return "\n".join(lines) + "\n"



def _cluster_mean_se(x: np.ndarray, groups) -> tuple:
    """Mean and cluster-robust (by game) standard error of per-row values (props of one game are correlated)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n == 0:
        return float("nan"), float("nan")
    g = pd.Series(x).groupby(np.asarray(groups)).sum()
    return float(x.mean()), float(math.sqrt(float(np.sum(g.to_numpy() ** 2)) * n / max(n - 1, 1)) / n)


def score_vs_market(res: pd.DataFrame) -> dict:
    """Per stat, on rows scored at the REAL book consensus line (no pushes): Brier and log loss of the market, the shipped
    engine and the hierarchical engine; the paired hierarchical-minus-baseline differences with game-clustered standard errors
    (negative d_brier / d_logloss = hierarchical better); and each engine's blend coefficient c on top of the market
    (c > 0 and significant = the engine adds information the market does not already price). Calibration vs a market, NOT CLV."""
    from mlb_core.odds import model_vs_market as MVM
    out: dict = {}
    for stat, d in res.dropna(subset=["p_market"]).groupby("stat"):
        y = d["over"].to_numpy(dtype=float)
        pm, pb, ph = (d[c].clip(1e-4, 1 - 1e-4).to_numpy(dtype=float) for c in ("p_market", "p_base", "p_hier"))
        gid = d["game_id"].to_numpy()

        def ll(p):
            return -(y * np.log(p) + (1 - y) * np.log(1 - p))
        d_br, se_br = _cluster_mean_se((ph - y) ** 2 - (pb - y) ** 2, gid)
        d_ll, se_ll = _cluster_mean_se(ll(ph) - ll(pb), gid)
        sb = MVM.score_model_vs_market(y, pb, pm, gid)
        sh = MVM.score_model_vs_market(y, ph, pm, gid)
        per_season = {int(s): float(((g["p_hier"] - g["over"]) ** 2 - (g["p_base"] - g["over"]) ** 2).mean())
                      for s, g in d.groupby("season")}
        out[stat] = {"n": int(len(d)), "n_games": int(len(set(gid))), "over_rate": float(y.mean()),
                     "brier_market": float(np.mean((pm - y) ** 2)), "brier_base": float(np.mean((pb - y) ** 2)),
                     "brier_hier": float(np.mean((ph - y) ** 2)), "logloss_market": float(ll(pm).mean()),
                     "logloss_base": float(ll(pb).mean()), "logloss_hier": float(ll(ph).mean()),
                     "d_brier": d_br, "d_brier_se": se_br, "z_brier": float(-d_br / se_br) if se_br > 0 else float("nan"),
                     "d_logloss": d_ll, "d_logloss_se": se_ll, "z_logloss": float(-d_ll / se_ll) if se_ll > 0 else float("nan"),
                     "per_season_d_brier": per_season,
                     "blend_c_base": (sb.get("blend") or {}).get("c"), "blend_t_base": (sb.get("blend") or {}).get("t_c"),
                     "blend_c_hier": (sh.get("blend") or {}).get("c"), "blend_t_hier": (sh.get("blend") or {}).get("t_c")}
    return out


def render_vs_market(sc: dict, coverage: Optional[dict] = None) -> str:
    L = ["", "== at REAL book lines (BettingPros consensus: median of de-vigged two-sided reference books, last pregame quote) ==",
         "stat              n  games  over%  Brier mkt/base/hier           d_brier (z)      d_logloss (z)     blend c base -> hier (t)"]
    for stat, c in sorted(sc.items()):
        bc = lambda k: "n/a" if c.get(k) is None else f"{c[k]:+.2f}"                                      # noqa: E731
        bt = lambda k: "" if c.get(k) is None else f" ({c[k]:+.1f})"                                      # noqa: E731
        L.append(f"{stat:<16}{c['n']:>5}{c['n_games']:>6}  {c['over_rate']:.2f}  "
                 f"{c['brier_market']:.4f}/{c['brier_base']:.4f}/{c['brier_hier']:.4f}   "
                 f"{c['d_brier']:+.4f} ({c['z_brier']:+.1f})   {c['d_logloss']:+.4f} ({c['z_logloss']:+.1f})   "
                 f"{bc('blend_c_base')}{bt('blend_t_base')} -> {bc('blend_c_hier')}{bt('blend_t_hier')}")
    if coverage:
        L.append("coverage: " + ", ".join(f"{k}={v}" for k, v in coverage.items()))
    L.append("d < 0 / z > 0 = hierarchical better than the shipped engine at the book's own line. Blend c > 0 with t > 2 = the engine "
             "adds information beyond the market. Calibration, not edge: BettingPros keeps one last-quote snapshot per day (no CLV).")
    return "\n".join(L) + "\n"


def parse_seasons(spec: str) -> list:
    out = []
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(out))


def _run_hierarchical(args, seasons: list) -> str:
    import os
    from nfl.data.read import load
    from nfl.models import hierarchical_live as HL
    env_tbl = EA.game_env_table(load("schedules"))
    if args.hier_cache and os.path.exists(args.hier_cache):
        res = pd.read_parquet(args.hier_cache)
    else:
        df = load_player_logs(range(min(seasons) - KEEP_SEASONS + 1, max(seasons) + 1))
        live = HL.LiveContext.load(max(seasons), with_pbp=args.hier_context,
                                   seasons=list(range(min(seasons) - 1, max(seasons) + 1))) if args.hier_context else None
        res = replay_hierarchical(df, seasons, env_tbl, live=live)
        if args.hier_cache:
            res.to_parquet(args.hier_cache)
    market_z = None
    mreport = ""
    if args.hier_market:
        mreport, market_scores = _run_market(args, seasons, env_tbl)
        market_z = {st: {"n": c["n"], "z_brier": c["z_brier"]} for st, c in market_scores.items()}
    cmp = compare_hierarchical(res, seasons, market_z)
    report = render_hierarchical(cmp, seasons) + mreport
    if args.write_allowlist:
        report += _write_allowlist(cmp, seasons, args, market_z)
    return report


def _run_market(args, seasons: list, env_tbl: pd.DataFrame) -> str:
    import os
    from mlb_core.odds import bettingpros_loader as BL
    from nfl.data.read import load
    from nfl.props.resolve import PlayerResolver
    if args.hier_market_cache and os.path.exists(args.hier_market_cache):
        res, cov = pd.read_parquet(args.hier_market_cache), None
    else:
        cons = BL.build_market_consensus("nfl", start=f"{min(seasons)}-08-01", end=f"{max(seasons) + 1}-03-31", min_books=3)
        resolver = PlayerResolver.from_mirror(range(min(seasons) - 1, max(seasons) + 1))
        market, cov = build_market_table(cons, resolver)
        df = load_player_logs(range(min(seasons) - KEEP_SEASONS + 1, max(seasons) + 1))
        res = replay_hierarchical(df, seasons, env_tbl, stats=sorted(set(MARKET_STAT.values())), market=market)
        cov["scored_rows"] = int(len(res))
        if args.hier_market_cache:
            res.to_parquet(args.hier_market_cache)
    scores = score_vs_market(res)
    return render_vs_market(scores, cov), scores


def build_allowlist(cmp: dict, extra: list, confirm: Optional[dict]) -> dict:
    """{stat: evidence} for stats ADOPT in `cmp` and in every dict of `extra`, and not contradicted by `confirm`
    (a compare_hierarchical dict on clean seasons: d_logscore must be > 0 and not significantly negative)."""
    out = {}
    for stat, c in cmp.items():
        if c.get("verdict") != "ADOPT" or any(e.get(stat, {}).get("verdict") != "ADOPT" for e in extra):
            continue
        ev = {"d_logscore": round(c["d_logscore"], 4), "d_logscore_se": round(c["d_logscore_se"], 4),
              "d_brier": round(c["d_brier"], 4), "d_brier_se": round(c["d_brier_se"], 4), "n": c["n"],
              "brier_z": round(c.get("z_brier", float("nan")), 2), "brier_source": c.get("brier_source", "proxy_line")}
        if confirm is not None:
            k = confirm.get(stat, {})
            if "d_logscore" not in k or k["d_logscore"] <= 0 or k["z_logscore"] <= -1.645:
                continue
            ev["confirm"] = {"d_logscore": round(k["d_logscore"], 4), "se": round(k["d_logscore_se"], 4), "n": k["n"]}
        out[stat] = ev
    return out


def _write_allowlist(cmp: dict, seasons: list, args, market_z: Optional[dict] = None) -> str:
    import json
    from datetime import date
    extra = [compare_hierarchical(pd.read_parquet(p), seasons, market_z) for p in (args.hier_cache_extra or [])]
    confirm = None
    if args.hier_confirm:
        cres = pd.read_parquet(args.hier_confirm)
        confirm = compare_hierarchical(cres, sorted(cres["season"].unique()))
    keep = build_allowlist(cmp, extra, confirm)
    doc = {"generated": date.today().isoformat(), "seasons": f"{seasons[0]}-{seasons[-1]}",
           "rule": f"log-score z >= {ADOPT_Z_HIER:.2f}, Brier z >= {ADOPT_Z_BRIER:.2f} (at the BOOK's lines when >= {MARKET_MIN_ROWS} real-line rows, else the proxy line), better in the required seasons, on every "
                   "supplied run; and not contradicted on the confirm seasons",
           "stats": sorted(keep), "evidence": keep}
    with open(args.write_allowlist, "w") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return f"\nallowlist written to {args.write_allowlist}: {sorted(keep) or 'NO STAT cleared the bar'}\n"


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
    ap.add_argument("--hierarchical", action="store_true",
                    help="ALSO replay nfl.models.hierarchical_props against the shipped baseline on --seasons "
                         "(use 2024-2025 for the held-out comparison) and print the paired report")
    ap.add_argument("--hier-context", action="store_true",
                    help="with --hierarchical: also load injuries / pbp / participation / snap_counts (slow, big)")
    ap.add_argument("--hier-cache", help="parquet path for the hierarchical replay rows (load if present, else save)")
    ap.add_argument("--hier-market", action="store_true",
                    help="with --hierarchical: ALSO replay at the REAL BettingPros consensus lines of those seasons and report Brier / "
                         "log loss of market, shipped and hierarchical engine (needs Odds/bettingpros_nfl + rosters_weekly)")
    ap.add_argument("--hier-market-cache", help="parquet path for the real-line replay rows (load if present, else save)")
    ap.add_argument("--write-allowlist", metavar="PATH",
                    help="write the per-stat allowlist (stats ADOPTed on this run, on every --hier-cache-extra run, and not "
                         "contradicted by --hier-confirm) as JSON; the serving engine reads nfl/props/hierarchical_allowlist.json")
    ap.add_argument("--hier-cache-extra", nargs="*", default=[],
                    help="more replay-row parquets (e.g. the --hier-context run): a stat must be ADOPT on all of them too")
    ap.add_argument("--hier-confirm", metavar="PATH",
                    help="replay-row parquet from seasons NOTHING was tuned or selected on: an allowlisted stat must not be "
                         "worse there (point estimate > 0, not significantly negative)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seasons = parse_seasons(args.seasons)
    if args.hierarchical:
        report = _run_hierarchical(args, seasons)
        print(report)
        if args.out:
            with open(args.out, "w") as fh:
                fh.write(report)
        return 0
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
