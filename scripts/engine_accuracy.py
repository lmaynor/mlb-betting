"""Accuracy of the SERVING projection engine's mean (ev.daily_projections.project_mu) on past games, per stat.

    python -m scripts.engine_accuracy --sport nhl --test-seasons 2024,2025 [--out report.json]

For every player-game in the test seasons (with at least MIN_GAMES earlier games and, like the board, a rotation-level
average usage), the engine's projection as of that game (history strictly BEFORE it, the production window of the last
HISTORY_GAMES games) is scored against what happened, next to the trailing-5 mean a reader could compute by hand.

What it is and is not
  * ACCURACY of the mean only: MAE, bias, and the share of games the engine beat the L5 mean. There are no historical
    NHL / NBA prop lines in this repo's serving path, so nothing here is edge against a price.
  * The opponent-allowed factor is NOT applied (it needs a league context rebuilt per date; for NFL the replay in
    nfl.props.engine_eval measured it as worth ~0). `method` says so in the output. NFL has its own full-fidelity replay
    (python -m nfl.props.engine_eval); this script covers NHL and NBA.
  * Test seasons are scored as they are; nothing here selects parameters, so there is no tuning to leak.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf

log = logging.getLogger("scripts.engine_accuracy")

METHOD = "empirical_ewm mean, no opponent factor; as-of history (strictly earlier games); baseline = trailing-5 mean"


def _season_of(row) -> int | None:
    """Season label of a game-log row: the year the season started (NHL 2025-26 and NBA 2025-26 are both 2025)."""
    s = row.get("season")
    if s is None or s != s:
        s = row.get("season_year")
    if s is not None and s == s:
        try:
            return int(s)
        except (TypeError, ValueError):
            pass
    d = row.get("_sort")
    if d is None:
        return None
    ts = str(d)[:10]
    try:
        y, m = int(ts[:4]), int(ts[5:7])
    except ValueError:
        return None
    return y if m >= 8 else y - 1


def replay_stat(logs: DP.GameLogs, stat: str, test_seasons: set) -> dict | None:
    table, sdef = logs.table_and_def(stat)
    if sdef is None or not table:
        return None
    thr = DP.ROTATION_MIN_USAGE[logs.sport].get(stat, 0.0)
    halflife, wgt, _ = DP.stat_params(logs.sport, stat)
    ae_e, ae_b, err = [], [], []
    for name, rows in table.items():
        if str(name).startswith("id:") or len(rows) <= DP.MIN_GAMES:
            continue
        # DP._hist keeps only the last HISTORY_GAMES; the replay needs the whole career, so build it without that cut
        vals = []
        for r in rows:
            v = cf._sum_cols(r, sdef[0])
            if v is None:
                continue
            vals.append((v, cf._sum_cols(r, sdef[1]), _season_of(r), r.get("_opp")))
        for i in range(DP.MIN_GAMES, len(vals)):
            if vals[i][2] not in test_seasons:
                continue
            hist = vals[max(0, i - DP.HISTORY_GAMES):i]
            use = [g[1] for g in hist[-10:] if g[1] is not None]
            if not use or sum(use) / len(use) < thr:
                continue
            pj = DP.project_mu(hist, halflife, 1.0, wgt)
            if pj is None:
                continue
            y = vals[i][0]
            l5 = float(np.mean([g[0] for g in hist[-5:]]))
            ae_e.append(abs(y - pj["mu"]))
            ae_b.append(abs(y - l5))
            err.append(y - pj["mu"])
    if len(ae_e) < 200:
        return None
    ae_e, ae_b, err = np.array(ae_e), np.array(ae_b), np.array(err)
    d = ae_e - ae_b
    return {"n": int(len(ae_e)), "mae": round(float(ae_e.mean()), 4), "baselineMae": round(float(ae_b.mean()), 4),
            "bias": round(float(err.mean()), 4), "dMae": round(float(d.mean()), 4),
            "dMaeSe": round(float(d.std(ddof=1) / np.sqrt(len(d))), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=("nhl", "nba"), required=True)
    ap.add_argument("--test-seasons", default="2024,2025", help="comma list of season START years to score")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seasons = {int(s) for s in a.test_seasons.split(",")}
    logs = DP.GameLogs.load(a.sport)
    if logs is None:
        log.error("%s game-log master unavailable", a.sport)
        return 1
    out = {}
    for stat in DP.PROJECT_STATS[a.sport]:
        res = replay_stat(logs, stat, seasons)
        log.info("%s %s -> %s", a.sport, stat, res)
        if res:
            out[stat] = res
    payload = {"sport": a.sport, "method": METHOD, "test_seasons": sorted(seasons), "engine": DP.ENGINE,
               "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "stats": out}
    text = json.dumps(payload, indent=1)
    print(text)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
