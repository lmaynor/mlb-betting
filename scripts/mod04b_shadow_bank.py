"""Daily shadow prediction banking driver for MOD-04 Cluster (b).

SHADOW ONLY: scores `saves` (`nhl_saves_sota_v2`), `sog` (`nhl_sog_trained_v1`), and
`rebounds` (`nba_rebounds_decomp_shadow_v1`) strictly on history before `--date`, writing
per-family JSON payloads via `nhl.props.mod04_shadow.write_shadow_predictions` (and
optionally GCS via `write_shadow_predictions_gcs`). Never fabricates rows: if required
history or slate inputs are missing, fails non-zero with a clear BLOCKED message and
writes zero rows.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nba.props import config as NC
from nba.props import data as ND
from nba.props import rebounds_decomp as RD
from nhl.props import countmodel as CM
from nhl.props import mod04_features as F
from nhl.props import mod04_shadow as SH
from nhl.props import saves_sota as SS
from nhl.props import sog_trained as ST
from nhl.props import xgfeat as X
from nhl.xg import data as XD

FAMILY_CONFIG: dict[str, dict[str, Any]] = {
    "saves": {
        "sport": "nhl",
        "engine": SS.ENGINE_LABEL,
        "main_line": 24.5,
        "target": "saves",
    },
    "sog": {
        "sport": "nhl",
        "engine": ST.ENGINE_LABEL,
        "main_line": 2.5,
        "target": "sog",
    },
    "rebounds": {
        "sport": "nba",
        "engine": RD.ENGINE_LABEL,
        "main_line": 5.5,
        "target": "rebounds",
    },
}


class BlockedShadowBankError(RuntimeError):
    """Raised when required history or target-date slate inputs are missing."""


def _py_scalar(val: Any) -> Any:
    if isinstance(val, np.generic):
        return val.item()
    return val


def _split_history_and_slate(
    df: pd.DataFrame,
    target_date: str,
    target_col: str,
    family: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        raise BlockedShadowBankError(f"BLOCKED: {family} frame is empty for {target_date}")
    if "date" not in df.columns:
        raise BlockedShadowBankError(f"BLOCKED: {family} frame missing 'date' column")

    work = df.copy()
    work["_d"] = work["date"].astype(str).str[:10]

    if "is_target_date" in work.columns:
        is_tgt = work["is_target_date"].astype(bool)
        score_df = work[is_tgt].copy()
        hist_df = work[(~is_tgt) & (work["_d"] < target_date)].copy()
    else:
        score_df = work[work["_d"] == target_date].copy()
        hist_df = work[work["_d"] < target_date].copy()

    if hist_df.empty:
        raise BlockedShadowBankError(
            f"BLOCKED: {family} has 0 historical rows strictly before {target_date}"
        )
    if target_col not in hist_df.columns or hist_df[target_col].notna().sum() == 0:
        raise BlockedShadowBankError(
            f"BLOCKED: {family} history missing non-null target column {target_col!r} before {target_date}"
        )
    if score_df.empty:
        raise BlockedShadowBankError(
            f"BLOCKED: {family} has 0 target-date slate rows on {target_date}"
        )
    return hist_df, score_df


def _build_row_dict(
    row: Mapping[str, Any],
    target_date: str,
    family: str,
    sport: str,
    engine: str,
    main_line: float,
    mu: float,
    alpha: float,
    pmf_vals: list[float],
) -> dict[str, Any]:
    player_name = row.get("player_name")
    if player_name is None or (isinstance(player_name, float) and math.isnan(player_name)):
        player_name = row.get("name", "")
    out: dict[str, Any] = {
        "date": target_date,
        "sport": sport,
        "family": family,
        "player_id": _py_scalar(row.get("player_id")),
        "player_name": "" if player_name is None else str(player_name),
        "game_id": _py_scalar(row.get("game_id")),
        "engine": engine,
        "mu": float(mu),
        "alpha": float(alpha),
        "pmf": [float(x) for x in pmf_vals],
        "main_line": float(main_line),
        "c": 0.0,
    }
    p_mkt = row.get("p_market", row.get("p_mkt"))
    if p_mkt is not None:
        if isinstance(p_mkt, Mapping) or (isinstance(p_mkt, (int, float)) and math.isfinite(float(p_mkt))):
            out["p_market"] = p_mkt
    return out


def _score_saves_family(
    hist_df: pd.DataFrame,
    score_df: pd.DataFrame,
    target_date: str,
) -> list[dict[str, Any]]:
    cfg = FAMILY_CONFIG["saves"]
    # Pre-registered walk-forward verdicts (~/workspace/mod04b-data/results/saves_backtest.json):
    # xgfeat = KEEP_XG (SAVES_FEATURES), alpha_x = REVERT_TO_FIXED (global nb_alpha).
    features = list(X.SAVES_FEATURES)
    prior_col = "g_n_prior" if "g_n_prior" in hist_df.columns else None
    try:
        mu_art = CM.fit(
            hist_df,
            target="saves",
            features=features,
            cutoff=target_date,
            prior_col=prior_col,
        )
    except RuntimeError as exc:
        raise BlockedShadowBankError(f"BLOCKED: saves countmodel fit failed: {exc}") from exc

    mu_arr = np.maximum(CM.predict_mu(mu_art, score_df), 1e-6)
    alpha_arr = np.full(len(mu_arr), float(mu_art["meta"]["nb_alpha"]), dtype=float)
    kmax = 100
    for mu_i, a_i in zip(mu_arr, alpha_arr):
        sd_i = math.sqrt(float(mu_i) + max(float(a_i), 0.0) * (float(mu_i) ** 2))
        kmax = max(kmax, int(math.ceil(float(mu_i) + 10.0 * sd_i)))

    rows: list[dict[str, Any]] = []
    for idx, rec in enumerate(score_df.to_dict(orient="records")):
        mu_i = float(mu_arr[idx])
        alpha_i = float(alpha_arr[idx])
        pmf_i = SS.pmf(mu_i, alpha_i, kmax=kmax).tolist()
        rows.append(
            _build_row_dict(
                rec,
                target_date=target_date,
                family="saves",
                sport=cfg["sport"],
                engine=cfg["engine"],
                main_line=cfg["main_line"],
                mu=mu_i,
                alpha=alpha_i,
                pmf_vals=pmf_i,
            )
        )
    return rows


def _score_sog_family(
    hist_df: pd.DataFrame,
    score_df: pd.DataFrame,
    target_date: str,
) -> list[dict[str, Any]]:
    cfg = FAMILY_CONFIG["sog"]
    kmax = 15
    try:
        model = ST.SogStructuralModel(kmax=kmax).fit(hist_df, target="sog")
    except ValueError as exc:
        raise BlockedShadowBankError(f"BLOCKED: sog model fit failed: {exc}") from exc

    mu_arr = model.predict_mu(score_df)
    pmf_mat = model.predict_pmf(score_df, kmax=kmax)
    alpha_val = float(model.alpha)

    rows: list[dict[str, Any]] = []
    for idx, rec in enumerate(score_df.to_dict(orient="records")):
        rows.append(
            _build_row_dict(
                rec,
                target_date=target_date,
                family="sog",
                sport=cfg["sport"],
                engine=cfg["engine"],
                main_line=cfg["main_line"],
                mu=float(mu_arr[idx]),
                alpha=alpha_val,
                pmf_vals=pmf_mat[idx].tolist(),
            )
        )
    return rows


def _score_rebounds_family(
    hist_df: pd.DataFrame,
    score_df: pd.DataFrame,
    target_date: str,
) -> list[dict[str, Any]]:
    cfg = FAMILY_CONFIG["rebounds"]
    kmax = 25
    try:
        model = RD.ReboundsDecompModel(kmax=kmax).fit(hist_df, target="rebounds")
    except ValueError as exc:
        raise BlockedShadowBankError(f"BLOCKED: rebounds model fit failed: {exc}") from exc

    mu_arr = model.predict_mu(score_df)
    pmf_mat = model.predict_pmf(score_df, kmax=kmax)
    alpha_val = float(0.5 * (model.alpha_oreb + model.alpha_dreb))

    rows: list[dict[str, Any]] = []
    for idx, rec in enumerate(score_df.to_dict(orient="records")):
        rows.append(
            _build_row_dict(
                rec,
                target_date=target_date,
                family="rebounds",
                sport=cfg["sport"],
                engine=cfg["engine"],
                main_line=cfg["main_line"],
                mu=float(mu_arr[idx]),
                alpha=alpha_val,
                pmf_vals=pmf_mat[idx].tolist(),
            )
        )
    return rows


_SCORERS = {
    "saves": _score_saves_family,
    "sog": _score_sog_family,
    "rebounds": _score_rebounds_family,
}


def bank_day(
    date: str,
    frames_by_family: Mapping[str, pd.DataFrame],
    out_dir: str | Path,
    write_gcs: bool = False,
) -> dict[str, dict[str, Any]]:
    """Score each family on `date` using strictly prior history and write shadow JSON files.

    Validates and scores all provided families before writing any outputs; if any family is
    missing history or target-date slate rows, raises `BlockedShadowBankError` and writes ZERO
    files/rows.
    """
    if not frames_by_family:
        raise BlockedShadowBankError("BLOCKED: frames_by_family is empty")

    target_date = str(date)[:10]
    scored_by_family: dict[str, list[dict[str, Any]]] = {}

    for family, frame in frames_by_family.items():
        if family not in FAMILY_CONFIG:
            raise BlockedShadowBankError(f"BLOCKED: unknown shadow family {family!r}")
        cfg = FAMILY_CONFIG[family]
        hist_df, score_df = _split_history_and_slate(
            frame, target_date=target_date, target_col=cfg["target"], family=family
        )
        rows = _SCORERS[family](hist_df, score_df, target_date)
        if not rows:
            raise BlockedShadowBankError(f"BLOCKED: {family} produced 0 scored rows on {target_date}")
        scored_by_family[family] = rows

    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict[str, Any]] = {}

    for family, rows in scored_by_family.items():
        cfg = FAMILY_CONFIG[family]
        out_path = out_base / f"mod04b_shadow_{family}_{target_date}.json"
        payload = SH.write_shadow_predictions(rows, out_path)
        gcs_key = None
        if write_gcs:
            gcs_key = SH.write_shadow_predictions_gcs(rows, sport=cfg["sport"], family=family)
        summary[family] = {
            "n": len(payload["predictions"]),
            "out_path": str(out_path),
            "gcs_key": gcs_key,
            "engine": cfg["engine"],
        }

    return summary


def _derive_nba_team_box(player_box: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    from scripts.mod04b_rebounds_backtest import derive_team_box_from_player_box

    return derive_team_box_from_player_box(player_box, games)


def _load_cli_frames(
    target_date: str,
    data_dir: str | None,
    xg_dir: str | None,
    nba_dir: str | None,
) -> dict[str, pd.DataFrame]:
    if not data_dir:
        raise BlockedShadowBankError(
            "BLOCKED: missing NHL/NBA archive data (--data-dir not provided)"
        )
    dpath = Path(data_dir)
    if not dpath.is_dir():
        raise BlockedShadowBankError(f"BLOCKED: --data-dir does not exist: {dpath}")
    if not xg_dir or not Path(xg_dir).is_dir():
        raise BlockedShadowBankError(
            f"BLOCKED: missing NHL xG directory (--xg-dir={xg_dir!r}) required for saves archive"
        )

    xpath = Path(xg_dir)
    for fname in ("xg_goalie_game.parquet", "xg_team_game.parquet"):
        if not (xpath / fname).exists():
            raise BlockedShadowBankError(f"BLOCKED: missing required xG parquet: {xpath / fname}")

    try:
        nhl_m = XD.load_masters(str(dpath))
    except FileNotFoundError as exc:
        raise BlockedShadowBankError(f"BLOCKED: missing NHL master file: {exc}") from exc

    xgl = pd.read_parquet(xpath / "xg_goalie_game.parquet")
    xt = pd.read_parquet(xpath / "xg_team_game.parquet")
    tt = X.team_table(nhl_m["games"], xt)
    gt = X.goalie_table(nhl_m["games"], nhl_m["goalies"], xgl)
    saves_df = X.saves_features(gt, tt)
    saves_df["is_target_date"] = saves_df["date"].astype(str).str[:10] == target_date

    sog_feat = ST.build_sog_features({
        "games": nhl_m["games"],
        "skater_box": nhl_m["skaters"],
        "goalie_box": nhl_m["goalies"],
    })
    sk_labels = nhl_m["skaters"][["game_id", "player_id", "sog"]].drop_duplicates(["game_id", "player_id"])
    sog_df = F.join_labels(sog_feat, sk_labels, how="left")
    sog_df["is_target_date"] = sog_df["date"].astype(str).str[:10] == target_date

    nba_root = Path(nba_dir) if nba_dir else (dpath / "NBA" if (dpath / "NBA").is_dir() else dpath)
    for fname in ("player_boxscores_master.csv", "games_master.csv"):
        if not (nba_root / fname).exists():
            raise BlockedShadowBankError(f"BLOCKED: missing NBA master file: {nba_root / fname}")

    pbox, ngames = ND.load_masters(str(nba_root))
    if "season_type" in pbox.columns:
        pbox = pbox[~pbox["season_type"].isin(NC.EXCLUDE_SEASON_TYPES)].copy()
    if "season_type" in ngames.columns:
        ngames = ngames[~ngames["season_type"].isin(NC.EXCLUDE_SEASON_TYPES)].copy()
    tbox = _derive_nba_team_box(pbox, ngames)
    reb_feat = RD.build_rebound_features({"player_box": pbox, "team_box": tbox})
    reb_cols = ["game_id", "player_id", "rebounds"] + [
        c for c in ("rebounds_offensive", "rebounds_defensive") if c in pbox.columns
    ]
    reb_labels = pbox[reb_cols].drop_duplicates(["game_id", "player_id"])
    reb_df = F.join_labels(reb_feat, reb_labels, how="left")
    reb_df["is_target_date"] = reb_df["date"].astype(str).str[:10] == target_date

    return {"saves": saves_df, "sog": sog_df, "rebounds": reb_df}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily shadow banking driver for MOD-04 Cluster (b).")
    ap.add_argument("--date", required=True, help="Target slate date (YYYY-MM-DD)")
    ap.add_argument("--out-dir", default="artifacts/mod04b_shadow", help="Local output directory for shadow JSON files")
    ap.add_argument("--gcs", action="store_true", help="Also upload shadow payloads to GCS")
    ap.add_argument("--data-dir", default=None, help="Optional root directory containing NHL/ (and NBA/) masters")
    ap.add_argument("--xg-dir", default=None, help="Optional directory containing NHL xG parquets")
    ap.add_argument("--nba-dir", default=None, help="Optional directory containing NBA master CSVs")
    args = ap.parse_args(argv)

    target_date = str(args.date)[:10]
    try:
        frames = _load_cli_frames(target_date, args.data_dir, args.xg_dir, args.nba_dir)
        res = bank_day(target_date, frames, out_dir=args.out_dir, write_gcs=bool(args.gcs))
    except BlockedShadowBankError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"BLOCKED: shadow banking failed: {exc}", file=sys.stderr)
        return 1

    for fam, info in res.items():
        print(f"{fam}: banked {info['n']} rows -> {info['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
