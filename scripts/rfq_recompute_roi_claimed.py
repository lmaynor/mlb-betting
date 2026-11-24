#!/usr/bin/env python
"""scripts/rfq_recompute_roi_claimed.py -- RUL-20261123 item 3 / Q5 pre-fit cleanup.

The banked full-bank evaluation (Nov 18) recorded roi_no_taker with the pre-fix denominator
(Ask_NO alone). The fix (ask + taker fee) landed on main afterwards, so the banked claimed ROIs
are inflated by up to ~7%. This script recomputes, for every banked EVALUATED row:

    net_cons_edge  = roi_claimed_banked * ask_no          (exact: the pre-registration section 8
                                                           audit reproduced all 2,068 banked values
                                                           bit-for-bit from the banked fields)
    roi_corrected  = net_cons_edge / (ask_no + taker_fee(ask_no, 1.0))     (canonical fee)

The archive is never overwritten: corrected values land in NEW files under the output dir,
alongside the banked ones. Outputs:
  roi_claimed_corrected.jsonl  one row per evaluated banked row (banked + corrected, side by side)
  fit_rows.jsonl               fit-ready rows for build_shadow_admission / fit_calibrator:
                               {family, day, is_fire, ask_no, roi_claimed (= corrected),
                                pnl_per_contract} for ALL evaluated rows (pnl computed for
                                non-fires too, from the banked outcome -- the calibrator's
                                estimation population is all evaluated rows, section 6)
  summary.json                 counts, means, and the Blade 3 threshold crossings: how many of
                               the 533 banked fires fall below roi 0.030 under the corrected
                               denominator (they would not have fired under corrected accounting)

Family labels come from the combo's legs via the engine's own combo_family() against a fresh
(all-ACCRUING) trust book -- the cold-start tie-break: the weakest-link label with every family
at the same regime is the first leg's family in leg order. Deterministic and reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.combo_taker_scanner import ComboLeg
from ev.combo_taker_window2 import FamilyTrustBook, combo_family
from ev.kalshi_props import ParsedPropTicker, parse_prop_ticker
from mlb.execution.kalshi.fees import taker_fee

BLADE3_ROI = 0.030


def _family_of(legs_with_sides, book: FamilyTrustBook) -> str:
    legs = []
    for ticker, _side in legs_with_sides:
        p = parse_prop_ticker(ticker)
        if isinstance(p, ParsedPropTicker):
            legs.append(ComboLeg(ticker=ticker, venue="kalshi", player_norm=p.player_code or "x",
                                 game_id=p.game_key, sport=p.sport, stat=p.stat, line=p.strike,
                                 direction=1, p_lambda=0.5, routing="", q_implied=0.5,
                                 is_hot_scratch=False, game_start_ts=0.0))
        else:
            # Game-line leg: sport from the game key's family taxonomy is not needed -- a leg
            # with no player is GAME_LINE regardless of sport.
            legs.append(ComboLeg(ticker=ticker, venue="kalshi", player_norm=None,
                                 game_id="", sport="", stat="", line=0.0,
                                 direction=1, p_lambda=0.5, routing="", q_implied=0.5,
                                 is_hot_scratch=False, game_start_ts=0.0))
    label, _regime, _tau = combo_family(legs, book)
    return label


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", default=os.path.expanduser("~/workspace/beezy/rfq-fullbank-run/evaluable_records.jsonl"))
    ap.add_argument("--combos", default=os.path.expanduser("~/workspace/beezy/rfq-fullbank-run/evaluable_combos.jsonl"))
    ap.add_argument("--out-dir", default=os.path.expanduser("~/workspace/beezy/rfq-prelock-outputs"))
    args = ap.parse_args(argv)

    combos = {}
    with open(args.combos, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                c = json.loads(line)
                combos[c["ticker"]] = c

    book = FamilyTrustBook()
    corrected_rows = []
    fit_rows = []
    n_evaluated = n_fires = 0
    fires_crossing_down = 0
    sum_banked = sum_corrected = 0.0
    max_pnl_diff = 0.0
    missing_family = 0

    with open(args.records, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("roi_no_taker") is None:
                continue
            n_evaluated += 1
            ask = float(r["ask_no"])
            roi_banked = float(r["roi_no_taker"])
            net_edge = roi_banked * ask
            roi_corr = net_edge / (ask + taker_fee(ask, 1.0))
            is_fire = bool(r.get("fires"))
            if is_fire:
                n_fires += 1
                if roi_corr < BLADE3_ROI <= roi_banked:
                    fires_crossing_down += 1
            sum_banked += roi_banked
            sum_corrected += roi_corr

            # pnl for every evaluated row from the banked outcome (canonical fee, 100-contract
            # quote convention -- identical to how the harness banked fire pnls; verified below).
            pnl = (0.0 if r["yes_won"] else 1.0) - ask - taker_fee(ask, 100.0) / 100.0
            if is_fire and r.get("pnl_per_contract") is not None:
                max_pnl_diff = max(max_pnl_diff, abs(pnl - float(r["pnl_per_contract"])))

            combo = combos.get(r["combo_id"])
            if combo is None:
                missing_family += 1
                family = "GAME_LINE"
            else:
                family = _family_of(combo["legs"], book)

            corrected_rows.append({
                "combo_id": r["combo_id"], "day": r.get("day"), "ask_no": ask,
                "roi_claimed_banked": roi_banked, "roi_claimed_corrected": roi_corr,
                "net_cons_edge": net_edge, "is_fire": is_fire, "family": family,
            })
            fit_rows.append({
                "family": family, "day": r.get("day"), "is_fire": is_fire,
                "ask_no": ask, "roi_claimed": roi_corr, "pnl_per_contract": pnl,
            })

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "roi_claimed_corrected.jsonl"), "w", encoding="utf-8") as fh:
        for row in corrected_rows:
            fh.write(json.dumps(row) + "\n")
    with open(os.path.join(args.out_dir, "fit_rows.jsonl"), "w", encoding="utf-8") as fh:
        for row in fit_rows:
            fh.write(json.dumps(row) + "\n")

    summary = {
        "n_evaluated_rows": n_evaluated,
        "n_fires": n_fires,
        "mean_roi_claimed_banked": (sum_banked / n_evaluated) if n_evaluated else None,
        "mean_roi_claimed_corrected": (sum_corrected / n_evaluated) if n_evaluated else None,
        "fires_crossing_below_blade3_0.030_under_corrected": fires_crossing_down,
        "max_abs_diff_recomputed_vs_banked_fire_pnl": max_pnl_diff,
        "rows_without_combo_legs_for_family": missing_family,
        "note": "Corrected = banked net_cons_edge / (ask_no + canonical taker_fee per contract). "
                "Banked archive untouched; corrected values live only in this output dir.",
    }
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
