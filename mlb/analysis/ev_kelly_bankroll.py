"""
mlb.analysis.ev_kelly_bankroll -- "how much would a Kelly-sized EV bettor
actually be making?"

The `bets` table's system="EV" rows all carry a flat stake
(fast_alert_loop._EV_STAKE_UNIT, $100) by deliberate design -- that keeps
ROI% directly comparable across alerts of very different edge sizes (see
fast_alert_loop._log_ev_bets's docstring). Since 2026-09-18 every EV row also
carries kelly_pct (mlb_core.odds.utils.kelly_pct(), the same function every
model system uses), computed off the same model_prob/odds already stored --
purely informational, it does not change the real stake/profit history.

This is the read-only report built on top of that field: it does NOT touch
stake or profit. For each settled EV bet it rescales the REAL graded outcome
(profit/stake ratio already encodes the correct payout multiplier for that
bet's actual odds and result) to what a `kelly_pct * bankroll` stake would
have paid out, against a fixed reference bankroll (not compounding -- the
bankroll used to SIZE every bet stays constant; only the running total P&L
accumulates). Reports that total alongside the existing flat-stake ROI so
the two are directly comparable.

kelly_pct itself is stored UNCAPPED (kelly_pct()'s own docstring: "for signal
gating") -- every model system in this repo caps the derived STAKE via
kelly_stake()'s own max_pct (typically 0.05), never the raw pct. This report
does the same: max_pct below caps kelly_stake, not kelly_pct. Skipping this
cap is not academic -- confirmed on the real recovered EV history
(2026-09-18): a cluster of HR_yn rows carry model_prob near 0.99 (implausible
for a single-batter HR prop; a likely pre-existing data issue in
outlier_scan.py's hr_yn consensus_fair, not something this session
root-caused) at odds around -2000 to -2800, producing uncapped kelly_pct up
to 0.2185 (21.85% of bankroll on ONE prop bet) -- almost all of which lost,
driving an uncapped run to a negative ending bankroll from a $1000 start.
Capping at the platform's own default max_pct bounds any single bad
probability estimate to a sane fraction of bankroll, exactly like every
other system's real Kelly sizing already does.

Usage:
    PYTHONPATH=. python3 -m mlb.analysis.ev_kelly_bankroll
    PYTHONPATH=. python3 -m mlb.analysis.ev_kelly_bankroll --bankroll 5000
    PYTHONPATH=. python3 -m mlb.analysis.ev_kelly_bankroll --max-pct 0.04
    PYTHONPATH=. python3 -m mlb.analysis.ev_kelly_bankroll --csv ev_kelly.csv
"""
from __future__ import annotations

import argparse

import pandas as pd
from sqlalchemy import text


def load_settled_ev(engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT * FROM bets WHERE system='EV' AND result IS NOT NULL "
                 "ORDER BY game_date, created_at"),
            conn,
        )
    return df


def kelly_bankroll_report(df: pd.DataFrame, bankroll: float = 1000.0, max_pct: float = 0.05) -> dict:
    """Returns a dict with both the existing flat-stake stats and the
    Kelly-at-`bankroll` rescaled stats, plus a per-bet DataFrame with a
    running (non-compounding) bankroll column for charting.

    max_pct caps the STAKE fraction (mirrors kelly_stake()'s own max_pct
    across every model system, default 0.05) -- kelly_pct is read uncapped
    from the row (it's stored that way deliberately, "for signal gating"),
    so this is the one place that discipline has to be applied for a bettor
    who'd actually be risking real money bet-to-bet."""
    d = df.copy()
    d = d[d["stake"].notna() & (d["stake"] > 0)]
    d = d[d["kelly_pct"].notna()]

    d["kelly_stake"] = d["kelly_pct"].clip(lower=0, upper=max_pct) * bankroll
    # profit scales linearly with stake for a fixed odds+result -- reuse the
    # REAL graded profit/stake ratio rather than re-deriving win/loss/push
    # payout math that already lives in settle_bets._calc_profit.
    d["kelly_profit"] = d["kelly_stake"] * (d["profit"] / d["stake"])
    d["running_bankroll"] = bankroll + d["kelly_profit"].cumsum()

    n = len(d)
    flat_staked, flat_pnl = d["stake"].sum(), d["profit"].sum()
    kelly_staked, kelly_pnl = d["kelly_stake"].sum(), d["kelly_profit"].sum()
    wins = (d["result"] == "win").sum()
    decided = d["result"].isin(["win", "loss"]).sum()

    return {
        "n_bets": n,
        "hit_rate": wins / decided if decided else None,
        "flat_stake_unit": float(d["stake"].iloc[0]) if n else None,
        "flat_total_staked": round(float(flat_staked), 2),
        "flat_total_pnl": round(float(flat_pnl), 2),
        "flat_roi_pct": round(float(flat_pnl / flat_staked * 100), 2) if flat_staked else None,
        "bankroll": bankroll,
        "kelly_total_staked": round(float(kelly_staked), 2),
        "kelly_total_pnl": round(float(kelly_pnl), 2),
        "kelly_roi_pct": round(float(kelly_pnl / kelly_staked * 100), 2) if kelly_staked else None,
        "kelly_ending_bankroll": round(bankroll + float(kelly_pnl), 2),
        "detail": d,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bankroll", type=float, default=1000.0,
                   help="Fixed reference bankroll used to size every bet (default: 1000)")
    p.add_argument("--max-pct", type=float, default=0.05,
                   help="Cap on kelly_stake as a fraction of bankroll per bet (default: 0.05, "
                        "matching the platform's typical kelly_stake() max_pct)")
    p.add_argument("--csv", default=None, help="Optional path to write the per-bet detail + running bankroll")
    args = p.parse_args(argv)

    from mlb_core.tracking.bet_tracker import _make_engine
    engine = _make_engine(db_path="unused")

    df = load_settled_ev(engine)
    if df.empty:
        print("No settled system='EV' bets found.")
        return 0

    report = kelly_bankroll_report(df, bankroll=args.bankroll, max_pct=args.max_pct)
    detail = report.pop("detail")

    print(f"=== EV bets: flat ${report['flat_stake_unit']:.0f} stake vs Kelly @ ${args.bankroll:.0f} "
          f"bankroll (capped {args.max_pct:.0%}/bet) ===")
    print(f"n_bets:            {report['n_bets']}")
    print(f"hit_rate:          {report['hit_rate']:.1%}" if report["hit_rate"] is not None else "hit_rate:          n/a")
    print()
    print(f"Flat stake  -- staked: ${report['flat_total_staked']:,.2f}  "
          f"pnl: ${report['flat_total_pnl']:,.2f}  roi: {report['flat_roi_pct']}%")
    print(f"Kelly sized -- staked: ${report['kelly_total_staked']:,.2f}  "
          f"pnl: ${report['kelly_total_pnl']:,.2f}  roi: {report['kelly_roi_pct']}%")
    print(f"Ending bankroll (started at ${args.bankroll:,.2f}): ${report['kelly_ending_bankroll']:,.2f}")

    if args.csv:
        cols = ["game_date", "bet_type", "player", "odds", "model_prob", "edge",
                "kelly_pct", "stake", "profit", "kelly_stake", "kelly_profit",
                "running_bankroll", "result"]
        detail[cols].to_csv(args.csv, index=False)
        print(f"\nWrote per-bet detail to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
