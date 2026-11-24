#!/usr/bin/env python
"""scripts/rfq_item9_admission_order.py -- RUL-20261123 item 9 forensic reproduction.

Question: which admission order produced the 188 ADMITTED rows in the Nov 18 full-bank grading --
first-come (the harness's shared ComboLiabilityGuard fed in creation-ts order) or scan_combos'
raw-roi_no_taker sort? The dossier asserted both in different sections (D-2 vs section 8).

Method: take the 533 banked fires (evaluable_records.jsonl, fires=true) with their banked
`admitted` flags, rebuild ComboCandidates from their tickers, and run the FROZEN guard twice --
once in ts order, once in roi-descending order -- with the stake formula the Nov 18 run used
(min($15, ask_no * 100), the pre-fee-fix code). Whichever order reproduces the banked admitted
set is the order that ran. Player/game exposure keys are approximated from the tickers (player
code for prop legs, event segment for game legs): grouping-consistent within the simulation.

Static evidence (same conclusion, see the staging memo): scan_combos has exactly one caller in
the repo -- its own unit test; the banked file's header log (harness_run.log) shows it was
written by the harness CLI run, whose admission path is run_candidates -> evaluate_taker_combo
-> one shared guard in iteration (ts) order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.combo_taker_scanner import ComboCandidate, ComboLeg, ComboLiabilityGuard
from ev.kalshi_props import ParsedPropTicker, parse_prop_ticker

_EVENT_RE = re.compile(r"^[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2}[A-Z]+)-")


def _candidate(rec: dict, legs_with_sides) -> ComboCandidate:
    legs = []
    for ticker, side in legs_with_sides:
        p = parse_prop_ticker(ticker)
        if isinstance(p, ParsedPropTicker):
            legs.append(ComboLeg(ticker=ticker, venue="kalshi", player_norm=p.player_code,
                                 game_id=p.game_key, sport=p.sport, stat=p.stat, line=p.strike,
                                 direction=1 if side == "yes" else -1, p_lambda=0.5, routing="",
                                 q_implied=0.5, is_hot_scratch=False, game_start_ts=0.0))
        else:
            m = _EVENT_RE.match(ticker)
            legs.append(ComboLeg(ticker=ticker, venue="kalshi", player_norm=None,
                                 game_id=(m.group(1) if m else ticker), sport="", stat="", line=0.0,
                                 direction=1 if side == "yes" else -1, p_lambda=0.5, routing="",
                                 q_implied=0.5, is_hot_scratch=False, game_start_ts=0.0))
    return ComboCandidate(combo_id=rec["combo_id"], venue="kalshi", legs=legs,
                          bid_yes=float(rec["bid_yes"]), age_since_creation_sec=0.0,
                          is_rfq_live=True, now_ts=float(rec["ts"]), stake=None)


def _run_order(fires, key) -> set:
    guard = ComboLiabilityGuard()
    admitted = set()
    for rec, cand in sorted(fires, key=key):
        cand.stake = min(15.0, float(rec["ask_no"]) * 100.0)   # the Nov 18 run's stake formula
        if guard.admit(cand):        # admit() reads only the candidate (liability + exposure keys)
            admitted.add(rec["combo_id"])
    return admitted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    base = os.path.expanduser("~/workspace/beezy/rfq-fullbank-run")
    ap.add_argument("--records", default=os.path.join(base, "evaluable_records.jsonl"))
    ap.add_argument("--combos", default=os.path.join(base, "evaluable_combos.jsonl"))
    args = ap.parse_args(argv)

    combos = {}
    with open(args.combos, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                c = json.loads(line)
                combos[c["ticker"]] = c["legs"]

    fires = []
    banked_admitted = set()
    with open(args.records, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if not r.get("fires"):
                continue
            cand = _candidate(r, combos[r["combo_id"]])
            fires.append((r, cand))
            if r.get("admitted"):
                banked_admitted.add(r["combo_id"])

    ts_order = _run_order(fires, key=lambda rc: (float(rc[0]["ts"]), rc[0]["combo_id"]))
    roi_order = _run_order(fires, key=lambda rc: (-float(rc[0]["roi_no_taker"]), rc[0]["combo_id"]))

    def _cmp(name, got):
        print(f"{name}: admitted={len(got)} exact_match={got == banked_admitted} "
              f"overlap={len(got & banked_admitted)} only_sim={len(got - banked_admitted)} "
              f"only_banked={len(banked_admitted - got)}")

    print(f"banked fires={len(fires)} banked admitted={len(banked_admitted)}")
    _cmp("ts order (first-come)", ts_order)
    _cmp("roi-desc order (scan_combos sort)", roi_order)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
