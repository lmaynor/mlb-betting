#!/usr/bin/env python3
"""MOD-04 Cluster (g) G0 + combo shadow sidecar banker.

SHADOW-ONLY: combo weight c=0.00.
Per RUL-20261124, market-anchored marginals (recovered from served board quantiles)
are a serving-input fallback ONLY -- NOT training features. Prop lines and odds are
banned as training features and must never enter any fit.

Does NOT import or modify ev/daily_projections.py or ev/sgp_book_pricer.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mlb_core.data.player_names import norm_name
from nba.props.copula_combo import (
    COMBO_STATS,
    STATS_PRA,
    BoardMarginalProvider,
    CopulaEngine,
    archetype_for_position,
    derive_from_draws,
    marginal_gate,
    p_over_from_pmf,
)


def _extract_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "predictions"):
            if key in payload:
                block = payload[key]
                if isinstance(block, list):
                    return [r for r in block if isinstance(r, dict)]
                if isinstance(block, dict):
                    return [r for r in block.values() if isinstance(r, dict)]
    return []


def run_shadow_bank(
    board_path: Path,
    date_str: str,
    fit_artifact_path: Path,
    n_draws: int = 2000,
) -> tuple[dict, float]:
    with board_path.open("r", encoding="utf-8") as fh:
        board_payload = json.load(fh)
    with fit_artifact_path.open("r", encoding="utf-8") as fh:
        fit_artifact = json.load(fh)

    raw_rows = _extract_rows(board_payload)
    nba_rows = [
        r for r in raw_rows
        if r.get("sport") is None or str(r.get("sport")).lower() == "nba"
    ]

    cv_min = fit_artifact.get("cv_min", 0.18)
    sigma_role = fit_artifact["sigma_role"]
    ece_by_cell = fit_artifact.get("ece_by_cell", {})
    ablation = fit_artifact.get("ablation") or {}
    adopt_t = bool(ablation.get("adopt_student_t", False))
    best_nu = ablation.get("best_t_nu")
    copula_type = "t" if (adopt_t and best_nu is not None) else "gaussian"

    provider = BoardMarginalProvider(nba_rows, cv=cv_min)

    # Collect per-player metadata and combo board lines
    player_meta: dict[str, dict] = {}
    combo_lines: dict[str, dict[str, dict]] = {}
    for row in nba_rows:
        pn = row.get("player_norm") or norm_name(str(row.get("player", "")))
        if not pn:
            continue
        meta = player_meta.setdefault(
            pn,
            {
                "player": row.get("player") or pn,
                "player_norm": pn,
                "team": row.get("team"),
                "opp": row.get("opp"),
                "game_key": row.get("game_key"),
                "position": row.get("position"),
            },
        )
        if not meta.get("position") and row.get("position"):
            meta["position"] = row.get("position")

        stat = str(row.get("stat", "")).strip().lower()
        if stat in COMBO_STATS and row.get("line") is not None and row.get("has_line", True):
            c_map = combo_lines.setdefault(pn, {})
            if stat not in c_map:
                c_map[stat] = {
                    "line": float(row["line"]),
                    "p_served": row.get("p_over_blend"),
                    "board_engine": row.get("engine"),
                }

    t0 = time.perf_counter()
    engine = CopulaEngine(
        sigma_by_archetype=sigma_role,
        cv_min_by_archetype=cv_min,
        n_draws=n_draws,
        copula=copula_type,
        nu=best_nu if copula_type == "t" else None,
    )

    shadow_rows: list[dict] = []
    n_g1_excluded = 0
    g1_failing_stats: set[str] = set()

    for pn in sorted(player_meta):
        margs = provider.marginals(pn)
        if not margs or not all(s in margs for s in STATS_PRA):
            continue
        meta = player_meta[pn]
        pos = meta.get("position")
        archetype = archetype_for_position(str(pos)) if pos else "wing"

        ok, failing = marginal_gate(ece_by_cell, archetype)
        if not ok:
            n_g1_excluded += 1
            g1_failing_stats.update(failing)
            continue

        if isinstance(cv_min, dict):
            arch_cv = float(cv_min.get(archetype, cv_min.get("*", 0.18)))
        else:
            arch_cv = float(cv_min)

        draws = engine.sample_player(margs, archetype, cv=arch_cv)
        derived = derive_from_draws(draws)
        player_combos = combo_lines.get(pn, {})

        families: dict[str, dict] = {}
        for stat in COMBO_STATS:
            pmf = derived["pmfs"][stat]
            fam_entry: dict = {
                "pmf": {str(k): v for k, v in pmf.items()},
            }
            if stat in player_combos:
                info = player_combos[stat]
                fam_entry["line"] = info["line"]
                fam_entry["p_model"] = p_over_from_pmf(pmf, info["line"])
                fam_entry["p_served"] = info["p_served"]
                fam_entry["board_engine"] = info["board_engine"]
            families[stat] = fam_entry

        shadow_rows.append({
            "date": date_str,
            "player": meta["player"],
            "player_norm": pn,
            "team": meta["team"],
            "opp": meta["opp"],
            "game_key": meta["game_key"],
            "engine": "mod04_copula_shadow_v1",
            "marginal_source": "market_anchored_served_blend",
            "archetype": archetype,
            "cv_min": arch_cv,
            "g1_excluded": False,
            "families": families,
            "p_dd_model": derived["p_dd"],
            "p_td_model": derived["p_td"],
            "dd_td": {
                "p_model": {"dd": derived["p_dd"], "td": derived["p_td"]},
                "p_served": {"dd": derived["p_dd"], "td": derived["p_td"]},
                "c": None,
                "note": "shadow construct, c n/a — not a board family",
            },
            "mean_pra": derived["mean_pra"],
            "var_pra_pred": derived["var_pra"],
        })

    elapsed = time.perf_counter() - t0
    payload = {
        "date": date_str,
        "engine": "mod04_copula_shadow_v1",
        "n_players_board": len(player_meta),
        "n_banked": len(shadow_rows),
        "n_g1_excluded": n_g1_excluded,
        "g1_failing_stats": sorted(g1_failing_stats),
        "fit_artifact_as_of": fit_artifact.get("as_of"),
        "marginal_source": "market_anchored_served_blend",
        "c": 0.00,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rows": shadow_rows,
    }
    return payload, float(elapsed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MOD-04 Cluster (g) shadow sidecar banker")
    ap.add_argument("--board", required=True, type=Path)
    ap.add_argument("--date", required=True, help="Board date YYYY-MM-DD")
    ap.add_argument("--fit-artifact", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n-draws", type=int, default=2000)
    args = ap.parse_args(argv)

    payload, elapsed = run_shadow_bank(
        args.board, args.date, args.fit_artifact, n_draws=args.n_draws
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(json.dumps({
        "date": payload["date"],
        "engine": payload["engine"],
        "n_players_board": payload["n_players_board"],
        "n_banked": payload["n_banked"],
        "n_g1_excluded": payload["n_g1_excluded"],
        "c": payload["c"],
        "elapsed_sec": round(elapsed, 4),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
