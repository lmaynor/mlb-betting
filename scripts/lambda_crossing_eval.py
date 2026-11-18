#!/usr/bin/env python3
"""scripts/lambda_crossing_eval.py -- CLI for Point-in-Time Lambda-Crossing Evaluation.

Loads snapshot rows from a CSV or JSON file, applies Module 1's CapabilityDecision
mask, runs Module 2's evaluate_intraday_trajectory across [T-14h, T-30m], and prints:
  1. Side-by-side performance table:
     - Unmasked Close ROI
     - Masked Close ROI
     - First-Passage (t_0) Settled ROI
     - Two-Snapshot Persistent (t_1) Settled ROI
     - Peak-Odds (t_max) Settled ROI
     Grouped by (book, hour_before_tip) to identify soft-book posting vulnerability.
  2. Realized Calibration Gap: y_bar_fired - P_bar_lambda,fired (threshold: >= -3.0pp).

Usage:
  python scripts/lambda_crossing_eval.py <path_to_snapshots.csv_or_json>
  python scripts/lambda_crossing_eval.py --synthetic
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path when script is executed directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ev.capability_mask import apply_capability_mask
from ev.lambda_crossing import QuoteSnapshot, TrajectoryGrade, evaluate_intraday_trajectory


def generate_synthetic_data() -> list[dict[str, Any]]:
    """Generates synthetic trajectories across books for offline evaluation."""
    now_epoch = 1770000000.0  # reference game start epoch
    game_start = now_epoch

    books = ["fanduel", "draftkings", "novig", "bet365"]
    records: list[dict[str, Any]] = []

    for b_idx, book in enumerate(books):
        # 1. High-3PAr wing alt 30+ pts (OVER_ELIGIBLE)
        # Opens +145 (d=2.45) at T-10h, peaks +155 (d=2.55) at T-6h, steams to +105 (d=2.05) at close
        p_lambda = 0.45
        outcome = 32.0  # won
        features = {"fg3a_per_fga": 0.60, "vacated_usage_share": 0.06, "spread": -3.0}

        # 20 snapshots from T-12h to T-5m
        hours = [12.0, 11.0, 10.0, 9.5, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0, 0.75, 0.5, 0.3, 0.15, 0.08]
        for i, h in enumerate(hours):
            t_offset = h * 3600.0
            ts = game_start - t_offset
            if h > 10.0:
                odds_d = 2.20
            elif h == 10.0:
                odds_d = 2.45  # t_0 crossing
            elif h >= 6.0:
                odds_d = 2.55  # peak
            elif h >= 2.0:
                odds_d = 2.30
            else:
                odds_d = 2.05  # steam to close

            records.append({
                "game_id": f"game_synth_{b_idx}_1",
                "player_norm": "mikal_bridges",
                "sport": "nba",
                "stat": "points",
                "line": 29.5,
                "main_line": 21.5,
                "side": "over",
                "book": book,
                "timestamp": ts,
                "game_start": game_start,
                "odds_decimal": odds_d,
                "p_cons": 0.44 + (0.01 if h < 6.0 else 0.0),
                "age_sec": 30.0,
                "outcome": outcome,
                "p_model": 0.46,
                "q_book": 1.0 / 2.45,
                "p_peer_med": 0.44,
                "n_peer_books": 3,
                "features": features,
            })

        # 2. Drummond impossible alt line (CAPABILITY_CEILING_ZERO)
        # Should be masked to ROUTE_TO_NO_TAKER
        drummond_hist = [12.0, 14.0, 18.0, 10.0, 16.0, 22.0, 15.0, 11.0] * 5
        drummond_mins = [26.0, 28.0, 27.0, 25.0, 29.0, 28.0, 26.0, 27.0] * 5
        for h in [8.0, 6.0, 4.0, 2.0, 0.5]:
            ts = game_start - (h * 3600.0)
            records.append({
                "game_id": f"game_synth_{b_idx}_2",
                "player_norm": "andre_drummond",
                "sport": "nba",
                "stat": "points",
                "line": 49.5,
                "main_line": 12.5,
                "side": "over",
                "book": book,
                "timestamp": ts,
                "game_start": game_start,
                "odds_decimal": 15.0,
                "p_cons": 0.005,
                "age_sec": 30.0,
                "outcome": 14.0,  # lost
                "p_model": 0.08,  # phantom unmasked edge
                "q_book": 1.0 / 15.0,
                "p_peer_med": 0.01,
                "n_peer_books": 2,
                "history_values": drummond_hist,
                "minutes_proj": 27.0,
                "minutes_hist": drummond_mins,
            })

    return records


def load_snapshots(file_path: str) -> list[dict[str, Any]]:
    """Loads snapshot dictionaries from a JSON or CSV file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "snapshots" in data:
                return data["snapshots"]
            raise ValueError("JSON file must be a list of records or contain a 'snapshots' key")

    # CSV load
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed: dict[str, Any] = dict(row)
            for float_field in ("line", "main_line", "odds_decimal", "p_cons", "odds_american", "age_sec", "outcome", "p_model", "q_book", "p_peer_med", "minutes_proj"):
                if float_field in parsed and parsed[float_field] != "":
                    try:
                        parsed[float_field] = float(parsed[float_field])
                    except (ValueError, TypeError):
                        pass
            if "n_peer_books" in parsed and parsed["n_peer_books"] != "":
                parsed["n_peer_books"] = int(parsed["n_peer_books"])
            if "features" in parsed and isinstance(parsed["features"], str) and parsed["features"].startswith("{"):
                try:
                    parsed["features"] = json.loads(parsed["features"])
                except Exception:
                    pass
            records.append(parsed)
    return records


def run_evaluation(records: list[dict[str, Any]]) -> None:
    """Groups records into trajectories, applies capability mask, and evaluates."""
    # Group snapshots by trajectory key: (game_id, player_norm, stat, line, side, book)
    grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        key = (
            str(r.get("game_id", "")),
            str(r.get("player_norm", "")),
            str(r.get("stat", "")),
            float(r.get("line", 0.0)),
            str(r.get("side", "over")),
            str(r.get("book", "")),
        )
        grouped[key].append(r)

    results_by_book_hour: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    calibration_fired_actual: list[float] = []
    calibration_fired_model: list[float] = []

    for key, snap_dicts in grouped.items():
        game_id, player_norm, stat, line, side, book = key
        first_r = snap_dicts[0]
        sport = str(first_r.get("sport", "nba"))
        main_line = float(first_r.get("main_line", line))
        p_model = float(first_r.get("p_model", 0.50))
        q_book = float(first_r.get("q_book", 0.50))
        features = first_r.get("features", {})
        history_values = first_r.get("history_values")
        minutes_proj = first_r.get("minutes_proj")
        minutes_hist = first_r.get("minutes_hist")
        p_peer_med = first_r.get("p_peer_med")
        n_peer_books = int(first_r.get("n_peer_books", 0))

        # 1. Apply Module 1 Capability Mask
        cap_dec = apply_capability_mask(
            p_model=p_model,
            q_book=q_book,
            sport=sport,
            stat=stat,
            line=line,
            main_line=main_line,
            features=features,
            history_values=history_values,
            minutes_proj=minutes_proj,
            minutes_hist=minutes_hist,
            p_peer_med=p_peer_med,
            n_peer_books=n_peer_books,
        )

        # 2. Build QuoteSnapshot objects
        snapshots: list[QuoteSnapshot] = []
        for d in snap_dicts:
            snapshots.append(QuoteSnapshot(
                game_id=game_id,
                player_norm=player_norm,
                stat=stat,
                line=line,
                side=side,
                book=book,
                timestamp=d["timestamp"],
                game_start=d["game_start"],
                odds_decimal=float(d["odds_decimal"]),
                p_cons=float(d.get("p_cons", 0.50)),
                odds_american=float(d["odds_american"]) if d.get("odds_american") is not None else None,
                age_sec=float(d.get("age_sec", 0.0)),
                outcome=float(d["outcome"]) if d.get("outcome") is not None else None,
            ))

        # 3. Evaluate trajectory
        # Masked trajectory uses P_lambda; unmasked uses raw P_model
        grade_masked = evaluate_intraday_trajectory(snapshots, p_lambda=cap_dec.p_lambda)
        grade_unmasked = evaluate_intraday_trajectory(snapshots, p_lambda=p_model)

        # Hour offset before tip (rounded)
        hour_offset = int(round(grade_masked.t_max_offset_min / 60.0)) if grade_masked.t_max_offset_min is not None else 0

        is_masked_active = (cap_dec.routing == "OVER_ELIGIBLE")

        # Track calibration for bets that fired at t_0
        if is_masked_active and grade_masked.crossed_t0 and grade_masked.settled_won is not None:
            calibration_fired_actual.append(1.0 if grade_masked.settled_won else 0.0)
            calibration_fired_model.append(cap_dec.p_lambda)

        results_by_book_hour[(book, hour_offset)].append({
            "is_masked_active": is_masked_active,
            "unmasked_close_roi": grade_unmasked.settled_roi_unmasked_close,
            "masked_close_roi": grade_masked.settled_roi_masked_close if is_masked_active else 0.0,
            "t0_roi": grade_masked.settled_roi_t0 if is_masked_active else 0.0,
            "t1_roi": grade_masked.settled_roi_t1 if is_masked_active else 0.0,
            "peak_roi": grade_masked.settled_roi_peak if is_masked_active else 0.0,
        })

    # Print Summary Table
    print("\n" + "=" * 88)
    print("INTRADAY LAMBDA-CROSSING EVALUATION & VULNERABILITY HEATMAP")
    print("=" * 88)
    header = f"{'Book':<15} {'Hour Before Tip':<18} {'Bets':<6} {'Unmasked Close':<16} {'Masked Close':<14} {'t_0 ROI':<11} {'t_1 ROI':<11} {'t_max ROI':<11}"
    print(header)
    print("-" * 88)

    def avg_roi(items: list[float | None]) -> float:
        valid = [x for x in items if x is not None]
        return (sum(valid) / len(valid) * 100.0) if valid else 0.0

    for (book, hour), res_list in sorted(results_by_book_hour.items()):
        n_bets = len(res_list)
        unmasked_roi = avg_roi([r["unmasked_close_roi"] for r in res_list])
        masked_roi = avg_roi([r["masked_close_roi"] for r in res_list if r["is_masked_active"]])
        t0_roi = avg_roi([r["t0_roi"] for r in res_list if r["is_masked_active"]])
        t1_roi = avg_roi([r["t1_roi"] for r in res_list if r["is_masked_active"]])
        peak_roi = avg_roi([r["peak_roi"] for r in res_list if r["is_masked_active"]])

        print(f"{book:<15} T-{hour}h{'':<14} {n_bets:<6} {unmasked_roi:>+14.1f}% {masked_roi:>+12.1f}% {t0_roi:>+9.1f}% {t1_roi:>+9.1f}% {peak_roi:>+9.1f}%")

    print("-" * 88)

    # Realized Calibration Gap
    if calibration_fired_actual and calibration_fired_model:
        y_bar = sum(calibration_fired_actual) / len(calibration_fired_actual)
        p_bar = sum(calibration_fired_model) / len(calibration_fired_model)
        gap = (y_bar - p_bar) * 100.0
        status = "PASSED (>= -3.0pp floor)" if gap >= -3.0 else "FAILED (< -3.0pp floor)"
        print(f"\nRealized Calibration Gap (y_bar - P_bar_lambda): {gap:>+.2f}pp [{status}]")
        print(f"  Sample Fired at t_0: N = {len(calibration_fired_actual)}, Win Rate: {y_bar*100.1:.1f}%, Mean Fair P: {p_bar*100.1:.1f}%\n")
    else:
        print("\nRealized Calibration Gap: N/A (No settled t_0 bets in evaluation sample)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Point-in-Time Lambda-Crossing Trajectory Evaluator")
    parser.add_argument("file_path", nargs="?", default=None, help="Path to input CSV or JSON snapshots")
    parser.add_argument("--synthetic", action="store_true", help="Run evaluation on generated synthetic data")
    args = parser.parse_args()

    if args.synthetic or args.file_path is None:
        print("Running offline evaluation with synthetic multi-book timeline...")
        data = generate_synthetic_data()
    else:
        print(f"Loading snapshots from {args.file_path}...")
        data = load_snapshots(args.file_path)

    run_evaluation(data)


if __name__ == "__main__":
    main()
