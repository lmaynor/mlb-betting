"""Forward audit of the LIVE board's own picks: archived pre-game board rows -> ev.model_picks selection -> production settlement.

    GCS_BUCKET=<bucket> PYTHONPATH=. python -m scripts.props_forward_audit --sports nba nhl nfl [--through 2026-11-01] --out audit.json

The board archive (EVSweep/{sport}_props/archive/{CT day}.json) keeps each prop's LAST pre-game state: the model's price, the
consensus, the best quotes. That is a forward, leak-free record -- nothing in it was computed with the result known. This script
replays what the tracked-picks job would have logged (ev.model_picks.qualifies / to_pick, into a THROWAWAY sqlite ledger) and
settles it with the PRODUCTION winter settler (mlb.runners.settle_bets._settle_winter_bets: league player id, fail-closed,
void only on a proven DNP), so it audits the settlement path as well as the picks.

Honest limits (repeated in the output):
  * The archive holds the last state before the game, not the first sighting, so the 30-minute-to-start gate is waived and the
    entry price is the best quote at (near) the close. That makes the EV / edge figures closing-line figures, not what a bettor
    who acted earlier would have seen; the CLV column cannot be computed from this source.
  * Rows were produced by the engine version that was deployed that day (check `engine` in the output), not the new engines.
  * Small samples. Every ROI carries a day-clustered bootstrap CI; nothing here is a verdict.
  * Controls: the same rows bet on BOTH sides at the best price (the pure cost of the book's hold) and the model side on every
    row with a model side and a price, whatever its confluence.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ev import daily_projections as DP
from ev import model_picks as MP

log = logging.getLogger("scripts.props_forward_audit")


def _load_days(sport: str, through: str) -> dict:
    from mlb_core import storage
    out = {}
    for key in storage.list_keys(f"EVSweep/{sport}_props/archive/"):
        day = key.rsplit("/", 1)[-1].replace(".json", "")
        if day <= through:
            out[day] = json.loads(storage.read_bytes(key))
    return out


def archive_picks(sport: str, arch: dict) -> list[dict]:
    """Picks the tracked-picks job would have logged from one archived day (30-minute gate waived, see module doc)."""
    picks = []
    for r in (arch.get("rows") or {}).values():
        r = {**r, "mins_to_start": 999.0}
        tier = MP.qualifies(r)
        if tier is None:
            continue
        picks.append({**MP.to_pick(r), "tier": tier})
    return picks


def settle_picks(sport: str, picks: list[dict], settle_date: str) -> pd.DataFrame:
    """Log into a throwaway sqlite ledger and settle with the production winter settler. -> the ledger frame with result/profit."""
    from sqlalchemy import text
    from mlb.runners import settle_bets as SB
    from mlb_core.tracking import BetTracker
    with tempfile.TemporaryDirectory() as td:
        tr = BetTracker(os.path.join(td, "audit.db"), system=MP.SYSTEM, sport=sport)
        MP.log_picks(sport, picks, tr)
        with tr.engine.connect() as c:
            df = pd.read_sql(text("SELECT * FROM bets"), c)
    if df.empty:
        return df
    outs = SB._settle_winter_bets(df, settle_date)
    res = {o["id"]: o for o in outs}
    df["result"] = df["id"].map(lambda i: res.get(int(i), {}).get("result"))
    df["profit"] = df["id"].map(lambda i: res.get(int(i), {}).get("profit"))
    return df


def _boot_roi(g: pd.DataFrame, n_boot: int = 2000, seed: int = 7):
    """Flat-stake ROI of graded win/loss rows with a day-clustered bootstrap 95% CI."""
    g = g[g["result"].isin(["win", "loss"])]
    if g.empty:
        return {"n": 0}
    stake, pnl = float(g["stake"].sum()), float(g["profit"].sum())
    days = g.groupby("game_date")[["stake", "profit"]].sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(n_boot, len(days)))
    s, p = days["stake"].to_numpy()[idx].sum(1), days["profit"].to_numpy()[idx].sum(1)
    roi = p / s
    return {"n": int(len(g)), "wins": int((g["result"] == "win").sum()), "hit": round(float((g["result"] == "win").mean()), 4),
            "roi": round(pnl / stake, 4), "ci95": [round(float(np.percentile(roi, 2.5)), 4), round(float(np.percentile(roi, 97.5)), 4)],
            "days": int(len(days)), "avg_model_p": round(float(g["model_prob"].mean()), 4),
            "avg_implied": round(float(g["market_prob"].mean()), 4)}


def _bucket(df: pd.DataFrame, col: str) -> dict:
    return {str(k): _boot_roi(v) for k, v in df.groupby(col)}


def audit_sport(sport: str, days: dict, settle_date: str) -> dict:
    picks, engines = [], {}
    for day, arch in sorted(days.items()):
        engines[arch.get("engine")] = engines.get(arch.get("engine"), 0) + 1
        picks += archive_picks(sport, arch)
    df = settle_picks(sport, picks, settle_date)
    if df.empty:
        return {"sport": sport, "days": len(days), "picks": 0, "engines": engines}
    df["tier"] = df["notes"].map(lambda s: json.loads(s).get("tier"))
    df["stat"] = df["notes"].map(lambda s: json.loads(s).get("stat"))
    df["edge_bucket"] = pd.cut(df["edge"].astype(float), [-1, 0.03, 0.06, 0.10, 1.0], labels=["<3%", "3-6%", "6-10%", ">10%"])
    status = df["result"].fillna("pending").value_counts().to_dict()
    return {"sport": sport, "days": len(days), "day_range": [min(days), max(days)], "engines": engines, "picks": int(len(df)),
            "status": {k: int(v) for k, v in status.items()},
            "all_picks": _boot_roi(df), "official": _boot_roi(df[df["tier"] == "official"]),
            "tracked_only": _boot_roi(df[df["tier"] == "tracked"]),
            "by_stat": _bucket(df, "stat"), "by_model_edge": _bucket(df.dropna(subset=["edge_bucket"]), "edge_bucket")}


def control_both_sides(sport: str, days: dict, settle_date: str, cap: int = 4000) -> dict:
    """Every priced line bet on BOTH sides at the best price: the hold paid by indiscriminate betting (a floor for any ROI above)."""
    picks = []
    for arch in days.values():
        for r in (arch.get("rows") or {}).values():
            if not r.get("has_line") or r.get("line") is None or str(r.get("best_over_book") or "").lower() in MP.DFS_BOOKS:
                continue
            for side in ("over", "under"):
                best = MP._best(r, side)
                if best is None or best[0].lower() in MP.DFS_BOOKS:
                    continue
                picks.append({**MP.to_pick({**r, "side": side, "mins_to_start": 999.0}), "tier": "control"})
    rng = np.random.default_rng(3)
    if len(picks) > cap:
        picks = [picks[i] for i in sorted(rng.choice(len(picks), cap, replace=False))]
    df = settle_picks(sport, picks, settle_date)
    return {"n_logged": len(picks), **(_boot_roi(df) if not df.empty else {"n": 0})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sports", nargs="+", default=["nba", "nhl", "nfl"])
    ap.add_argument("--through", default=None, help="last archive day to include (default: yesterday UTC)")
    ap.add_argument("--controls", action="store_true", help="also settle the both-sides control (network-heavy)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    through = a.through or (datetime.now(timezone.utc).date().isoformat())
    res = {"generated": datetime.now(timezone.utc).isoformat(), "through": through, "sports": {}}
    for sport in a.sports:
        days = _load_days(sport, through)
        res["sports"][sport] = audit_sport(sport, days, through)
        if a.controls:
            res["sports"][sport]["control_both_sides"] = control_both_sides(sport, days, through)
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
