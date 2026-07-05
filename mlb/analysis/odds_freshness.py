"""
mlb.analysis.odds_freshness -- is the intraday odds feed actually flowing?

A silent 3-day outage (ParlayAPI reverted to SGO + the converter failing on a
permission error) cost a week of confused backtesting because nothing alerted that
odds_history had gone stale. This checks, per recent day and source, how many
distinct snapshots landed -- and flags today if it's below the expected intraday
cadence or missing entirely. Run it daily (job) or ad-hoc; exit code 1 on WARN so a
scheduler surfaces the failure.

Expected intraday cadence once healthy: ~7 ParlayAPI + ~5 BettingPros snapshots/day.
The historical daily converter adds 2 synthetic (open/close) bettingpros snapshots --
so a healthy TODAY should show clearly more than 2, from multiple sources.

Run:
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.odds_freshness
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from mlb.analysis import odds_history as oh

DEFAULT_MARKETS = ["hr_yn", "outs_ou", "btb_ou", "k_ou", "bhits_ou"]
MIN_TODAY_SNAPSHOTS = 4   # below this today = WARN (intraday feed likely down)


def check(markets=None, days: int = 4, today: str | None = None) -> dict:
    markets = markets or DEFAULT_MARKETS
    today = today or date.today().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()

    # union snapshots across the tracked markets, per (date, source)
    import pandas as pd
    frames = []
    for m in markets:
        df = oh.read_history(m, since=since)
        if len(df):
            frames.append(df[["game_date", "source", "snapshot_ts"]])
    if not frames:
        return {"ok": False, "reason": "no odds_history rows at all", "table": None, "today": today}
    allrows = pd.concat(frames, ignore_index=True)
    tbl = (allrows.groupby(["game_date", "source"])["snapshot_ts"].nunique()
           .unstack(fill_value=0).sort_index())

    latest = allrows["game_date"].max()
    today_snaps = int(allrows[allrows["game_date"] == today]["snapshot_ts"].nunique())
    parlay_today = int(allrows[(allrows["game_date"] == today)
                               & (allrows["source"] == "parlayapi")]["snapshot_ts"].nunique())

    reasons = []
    if latest < today:
        reasons.append(f"latest odds_history date is {latest} (< today {today}) -- feed stale")
    if today_snaps < MIN_TODAY_SNAPSHOTS:
        reasons.append(f"only {today_snaps} snapshots today (< {MIN_TODAY_SNAPSHOTS}) -- intraday feed thin/down")
    if parlay_today == 0:
        reasons.append("0 ParlayAPI snapshots today -- provider may have reverted to SGO")
    return {"ok": not reasons, "reasons": reasons, "table": tbl,
            "today": today, "today_snaps": today_snaps, "parlay_today": parlay_today}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check odds_history intraday freshness")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    p.add_argument("--days", type=int, default=4)
    p.add_argument("--today", default=None, help="override 'today' (YYYY-MM-DD)")
    args = p.parse_args(argv)
    res = check(args.markets.split(","), days=args.days, today=args.today)

    print(f"\nodds_history freshness (through {res['today']})")
    if res["table"] is not None:
        print(res["table"].to_string())
    if res["ok"]:
        print(f"\n  OK -- {res['today_snaps']} snapshots today "
              f"({res['parlay_today']} ParlayAPI). Intraday feed healthy.")
        return 0
    print("\n  WARN -- intraday feed problem:")
    for r in res.get("reasons", [res.get("reason", "unknown")]):
        print(f"    - {r}")
    print("  Fixes: confirm snapshot logs 'provider=parlay'; ODDS_PRIMARY=parlay on the "
          "service; mlb-track-bettingpros + mlb-parlayapi-history jobs running clean.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
