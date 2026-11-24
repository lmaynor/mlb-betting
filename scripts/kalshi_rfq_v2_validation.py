#!/usr/bin/env python
"""scripts/kalshi_rfq_v2_validation.py -- RUL-20261123 item 12: the pre-registered
ROTTEN_LEG_V2 banked validation (pre-registration section 5), run PARTIAL.

Protocol (locked, pre-registration section 5): the 533 banked fires split by close day --
first 9 days tune, last 6 days holdout (no threshold selection on the holdout); theta grid
{-0.020, 0, +0.010}; target flag rate 5%-25% of fires; promotion to a 0.50x score de-rank
requires, OUT-OF-SAMPLE: unflagged-minus-flagged mean pnl gap >= +0.050/contract OR flagged
calibration worse than unflagged by >= 3.0pp (predicted minus realized NO win rate). Plus the
section's monotonic calibration check on all 2,068 evaluated combos. Promotion to hard-block
additionally requires flagged mean < +0.010 on the full bank AND the holdout (and consumes an
ADJUST slot regardless -- not evaluated here beyond reporting the flagged means).

PARTIAL, by data -- never fabricated: the banked fires carry no per-leg stress inputs, so the
legs are re-priced point-in-time through the same store the full-bank run used, and the stress
inputs are taken off the reconstructed fair values (Branch-1 wiring: disp + age + p_max).
  - q_kalshi is ABSENT for every leg (Kalshi single-leg prices were never banked; they exist
    only on the live recorder's tape). The q component drops out of the stress max everywhere.
  - p_max is present only where the serving index retained per-book values (raw prop index
    exact rungs, directly-quoted game-line rungs); cycle-frame consensus rows and curve /
    interpolated rungs carry none.
The script reports the component-presence accounting alongside the numbers so the partiality
is quantified, and grades outcomes from the BANKED pnl/outcome fields (the reconstruction
supplies only the stress inputs and the signal; its re-fire fidelity is reported too).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.combo_taker_scanner import evaluate_combo
from ev.combo_taker_window2 import Window2Config, rotten_leg_v2
from ev.kalshi_rfq_replay import (
    CompositeStore,
    SnapshotStore,
    fetch_cycle_frames,
    raw_game_line_snapshots,
    raw_prop_snapshots,
    store_from_game_lines,
    store_from_raw,
)
from ev.kalshi_rfq_taker_replay import candidate_from_legs, commence_lookup

THETAS = (-0.020, 0.0, 0.010)
T_CRIT = {5: 2.571, 8: 2.306}   # two-sided 95% t critical values for G-1 df (holdout G=6, tune G=9)


def _clustered_gap(rows_f: list[dict], rows_u: list[dict], key: str = "pnl", weighted: bool = False) -> dict:
    """Flagged-vs-unflagged mean gap (unflagged - flagged) with a game-day-clustered CI.

    Cluster-robust (CR1) variance of the difference in means, clusters = close days, G-1 df.
    weighted=True (the collateral-weighted ROI gap only -- section 5's pnl gap is the plain
    mean) weights rows by their "w" collateral; the default is always unweighted, explicitly."""
    import statistics as st
    if not rows_f or not rows_u:
        return {"gap": None, "ci_lo": None, "ci_hi": None, "clusters": 0}

    def _mean(rows):
        if weighted:
            tw = sum(r["w"] for r in rows)
            return sum(r["w"] * r[key] for r in rows) / tw if tw > 0 else 0.0
        return st.fmean(r[key] for r in rows)

    def _infl(rows, mean):
        if weighted:
            tw = sum(r["w"] for r in rows)
            return [(r["day"], r["w"] * (r[key] - mean) / tw) for r in rows] if tw > 0 else []
        return [(r["day"], (r[key] - mean) / len(rows)) for r in rows]

    mf, mu = _mean(rows_f), _mean(rows_u)
    gap = mu - mf
    clusters: dict[str, float] = {}
    for d, v in _infl(rows_u, mu):
        clusters[d] = clusters.get(d, 0.0) + v
    for d, v in _infl(rows_f, mf):
        clusters[d] = clusters.get(d, 0.0) - v
    g = len(clusters)
    if g < 2:
        return {"gap": gap, "ci_lo": None, "ci_hi": None, "clusters": g}
    var = (g / (g - 1.0)) * sum(v * v for v in clusters.values())
    se = math.sqrt(max(var, 0.0))
    crit = T_CRIT.get(g - 1, 1.96)
    return {"gap": gap, "ci_lo": gap - crit * se, "ci_hi": gap + crit * se, "clusters": g, "se": se}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    base = os.path.expanduser("~/workspace/beezy/rfq-fullbank-run")
    ap.add_argument("--records", default=os.path.join(base, "evaluable_records.jsonl"))
    ap.add_argument("--combos", default=os.path.join(base, "evaluable_combos.jsonl"))
    ap.add_argument("--cache-dir", default=os.path.join(base, "cache"))
    ap.add_argument("--out-dir", default=os.path.expanduser("~/workspace/beezy/rfq-prelock-outputs"))
    ap.add_argument("--max-age-min", type=float, default=120.0)
    args = ap.parse_args(argv)
    max_age = args.max_age_min * 60.0

    combos = {}
    with open(args.combos, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                c = json.loads(line)
                combos[c["ticker"]] = c
    fires = []
    with open(args.records, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                if r.get("fires") and r.get("pnl_per_contract") is not None:
                    fires.append(r)
    days_sorted = sorted({r["day"] for r in fires})
    tune_days, holdout_days = set(days_sorted[:9]), set(days_sorted[9:])
    print(f"banked fires: {len(fires)} | days: {len(days_sorted)} "
          f"(tune {len(tune_days)}: {min(tune_days)}..{max(tune_days)}; "
          f"holdout {len(holdout_days)}: {min(holdout_days)}..{max(holdout_days)})", file=sys.stderr)

    store_days = sorted({
        (datetime.fromtimestamp(float(r["ts"]), timezone.utc).date() + timedelta(days=k)).isoformat()
        for r in fires for k in (-1, 0)
    })
    stores = [SnapshotStore.from_cycle_frames(fetch_cycle_frames(store_days))]
    for sport in ("nfl", "nba", "nhl"):
        snaps = raw_prop_snapshots(sport, store_days, cache_dir=args.cache_dir)
        print(f"raw {sport}: {len(snaps)} snapshots", file=sys.stderr)
        stores.append(store_from_raw(sport, snaps))
        if sport in ("nba", "nhl"):
            gl = raw_game_line_snapshots(sport, store_days, cache_dir=args.cache_dir)
            print(f"game lines {sport}: {len(gl)} snapshots", file=sys.stderr)
            stores.append(store_from_game_lines(sport, gl))
    store = CompositeStore(stores)

    # Reconstruct each fire point-in-time; banked fields supply the outcome.
    recon = []
    n_unpriced = n_refire_fail = 0
    comp = {"legs": 0, "p_max": 0, "disp": 0, "q_kalshi": 0, "age_pos": 0}
    for r in sorted(fires, key=lambda x: float(x["ts"])):
        t = float(r["ts"])
        book = store.book_at(t, max_age)
        if book is None:
            n_unpriced += 1
            continue
        cand, status = candidate_from_legs(
            combo_id=r["combo_id"], venue="kalshi", legs_with_sides=combos[r["combo_id"]]["legs"],
            book=book, t=t, bid_yes=float(r["bid_yes"]),
            game_start_lookup=commence_lookup(store, t, max_age))
        if cand is None:
            n_unpriced += 1
            continue
        sig = evaluate_combo(cand)
        if not sig.fires:
            n_refire_fail += 1
        for s in cand.stress_inputs:
            comp["legs"] += 1
            comp["p_max"] += s.p_max is not None
            comp["disp"] += s.disp is not None
            comp["q_kalshi"] += s.q_kalshi is not None
            comp["age_pos"] += s.age_sec > 0
        recon.append({"rec": r, "cand": cand, "sig": sig})
    print(f"reconstructed: {len(recon)} | unpriced: {n_unpriced} | reconstructed-but-not-refiring: {n_refire_fail}",
          file=sys.stderr)

    # Section 5's second component: the monotonic calibration check on ALL 2,068 evaluated
    # combos (not just fires): realized NO win rate must be non-decreasing across deciles of
    # predicted NO win probability (1 - p_true_yes from the reconstructed signal).
    evaluated = []
    with open(args.records, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                if r.get("roi_no_taker") is not None:
                    evaluated.append(r)
    buckets: dict[int, list[float]] = {}
    n_eval_unpriced = 0
    for r in sorted(evaluated, key=lambda x: float(x["ts"])):
        t = float(r["ts"])
        book = store.book_at(t, max_age)
        if book is None:
            n_eval_unpriced += 1
            continue
        cand, _status = candidate_from_legs(
            combo_id=r["combo_id"], venue="kalshi", legs_with_sides=combos[r["combo_id"]]["legs"],
            book=book, t=t, bid_yes=float(r["bid_yes"]),
            game_start_lookup=commence_lookup(store, t, max_age))
        if cand is None:
            n_eval_unpriced += 1
            continue
        sig = evaluate_combo(cand)
        pred_no = 1.0 - float(sig.p_true_yes)
        b = min(9, int(pred_no * 10))
        buckets.setdefault(b, []).append(0.0 if r["yes_won"] else 1.0)
    import statistics as _st
    deciles = [{"bucket": b, "n": len(v), "realized_no_win_rate": _st.fmean(v)}
               for b, v in sorted(buckets.items()) if v]
    inversions = sum(1 for a, b in zip(deciles, deciles[1:])
                     if b["realized_no_win_rate"] < a["realized_no_win_rate"] - 1e-9)
    monotonic_check = {"n_evaluated_banked": len(evaluated), "n_reconstructed": sum(d["n"] for d in deciles),
                       "n_unpriced_reconstruction": n_eval_unpriced, "deciles": deciles,
                       "adjacent_inversions": inversions,
                       "monotonic_non_decreasing": inversions == 0}
    print(f"monotonic check: {monotonic_check['n_reconstructed']} evaluated reconstructed, "
          f"inversions={inversions}", file=sys.stderr)

    base_cfg = Window2Config()
    results = {"label": "PARTIAL -- q_kalshi absent for all legs (never banked); p_max partial (see component_presence)",
               "n_banked_fires": len(fires), "n_reconstructed": len(recon),
               "n_unpriced_reconstruction": n_unpriced, "n_reconstructed_not_refiring": n_refire_fail,
               "tune_days": sorted(tune_days), "holdout_days": sorted(holdout_days),
               "component_presence": {k: (v / comp["legs"] if comp["legs"] else None) for k, v in comp.items()},
               "monotonic_calibration_check_all_evaluated": monotonic_check,
               "thetas": {}}
    for theta in THETAS:
        cfg = replace(base_cfg, theta_stress=theta)
        per_split = {}
        for split_name, split_days in (("tune", tune_days), ("holdout", holdout_days)):
            rows = []
            for item in recon:
                r = item["rec"]
                if r["day"] not in split_days:
                    continue
                res = rotten_leg_v2(item["cand"], item["sig"], item["cand"].stress_inputs, cfg,
                                    timing_approx=bool(getattr(item["cand"], "timing_approx", False)))
                ask = float(r["ask_no"])
                fee = 0.07 * ask * (1.0 - ask)
                rows.append({"day": r["day"], "pnl": float(r["pnl_per_contract"]),
                             "roi": float(r["pnl_per_contract"]) / (ask + fee),
                             "w": min(15.0, ask * 100.0),
                             "flag": bool(res.flag), "conditions": list(res.conditions),
                             "p_no_win_pred": 1.0 - float(item["sig"].p_true_yes),
                             "no_won": not bool(r["yes_won"])})
            flagged = [x for x in rows if x["flag"]]
            unflagged = [x for x in rows if not x["flag"]]
            import statistics as st
            gap_stats = _clustered_gap(flagged, unflagged)
            roi_gap_stats = _clustered_gap(flagged, unflagged, key="roi", weighted=True)

            def _calib(slice_rows):
                if not slice_rows:
                    return None
                return (st.fmean(x["p_no_win_pred"] for x in slice_rows)
                        - st.fmean(1.0 if x["no_won"] else 0.0 for x in slice_rows))

            calib_f, calib_u = _calib(flagged), _calib(unflagged)
            per_split[split_name] = {
                "n": len(rows), "n_flagged": len(flagged),
                "flag_rate": (len(flagged) / len(rows)) if rows else None,
                "flagged_mean_pnl": (st.fmean(x["pnl"] for x in flagged) if flagged else None),
                "unflagged_mean_pnl": (st.fmean(x["pnl"] for x in unflagged) if unflagged else None),
                "gap_unflagged_minus_flagged": gap_stats,
                "collateral_weighted_roi_gap_unflagged_minus_flagged": roi_gap_stats,
                # Section 5's calibration prong is RELATIVE: flagged calibration worse than
                # unflagged by >= 3.0pp (signed: predicted minus realized NO win rate --
                # overconfidence in the NO price).
                "flagged_calibration_gap_pred_minus_realized_no_win": calib_f,
                "unflagged_calibration_gap_pred_minus_realized_no_win": calib_u,
                "calibration_prong_flagged_worse_by": (
                    (calib_f - calib_u) if (calib_f is not None and calib_u is not None) else None),
                "flag_conditions_hist": {c: sum(c in x["conditions"] for x in flagged)
                                         for c in ("EDGE_COLLAPSE", "TAIL_RATIO_ANOMALY")},
            }
        tune_rate = per_split["tune"]["flag_rate"]
        per_split["theta_in_flag_band_on_tune"] = (tune_rate is not None and 0.05 <= tune_rate <= 0.25)
        results["thetas"][str(theta)] = per_split

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "v2_validation.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(json.dumps({k: results[k] for k in ("n_banked_fires", "n_reconstructed", "component_presence")}, indent=2))
    for theta, per in results["thetas"].items():
        for split in ("tune", "holdout"):
            s = per[split]
            print(f"theta={theta} {split}: n={s['n']} flag_rate={s['flag_rate']:.3f} "
                  f"flagged_mean={s['flagged_mean_pnl']} gap={s['gap_unflagged_minus_flagged']['gap']} "
                  f"CI=({s['gap_unflagged_minus_flagged']['ci_lo']}, {s['gap_unflagged_minus_flagged']['ci_hi']}) "
                  f"calib_prong_flagged_worse_by={s['calibration_prong_flagged_worse_by']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
