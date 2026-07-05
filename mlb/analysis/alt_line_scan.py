"""
mlb.analysis.alt_line_scan -- price EVERY quoted line with the count models.

The K/OUTS/BATTER_TB/BATTER_HITS models are NegBin DISTRIBUTIONS, so they
price any line for free -- but the live runners only ever see one canonical
line per player. Meanwhile the BettingPros tracker banks every (book, line)
quote into odds_history, including alt lines and off-main books (K ladder
0.5..9.5 observed). Books price alt lines by formula off the main and update
them lazily: it is the neglected tail inventory, and the one place a
distribution model can meaningfully disagree with the book's tail shape.

For a given date, this scanner:
  1. scores the system's feature rows with the PRODUCTION artifacts
     (mlb.analysis.gen_preds -> mu, nb_alpha per player),
  2. joins every quote in odds_history at the LATEST snapshot (all lines,
     all books, offshore excluded),
  3. computes model EV per quote:  p_model(side, line) * decimal - 1,
  4. flags each quote MAIN (the modal line for that player) or ALT,
  5. prints the ranked board and (--log) appends to
     Alerts/{date}/altline.parquet so quote_survival / odds_alert-style
     resolution can grade whether alt-line EV survives to close.

DISCIPLINE: this is model-vs-line EV, the thing that showed ~0 edge on MAIN
lines. The bet here is that ALT lines are softer. Paper-log first; the
go/no-go is CLV/resolution on the logged alerts, not the pretty EV column.

Run (Cloud Shell):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.alt_line_scan --systems K,BATTER_TB --date 2026-07-06 --min-ev 0.04 --log
"""

from __future__ import annotations

import argparse
import io
import logging

import pandas as pd

from mlb.analysis import gen_preds as gp
from mlb.analysis import odds_history as oh
from mlb.analysis import backtest_market as bt
from mlb_core import storage

log = logging.getLogger("alt_line_scan")

_NON_BOOK = bt.OFFSHORE | {"open", "consensus"}

# system -> odds_history market (count systems only; distributions price lines)
SYS_MARKET = {"K": "k_ou", "OUTS": "outs_ou",
              "BATTER_TB": "btb_ou", "BATTER_HITS": "bhits_ou"}


def _latest_quotes(market: str, date: str) -> pd.DataFrame:
    """Every (player, line, selection, book) quote at its latest snapshot."""
    df = oh.read_history(market, since=date, until=date)
    if df is None or not len(df):
        return pd.DataFrame()
    df = oh.dedupe_by_source(df)
    df = df[~df["book"].str.lower().isin(_NON_BOOK)]
    df = df[df["decimal"].notna() & df["line"].notna() & df["player_id"].notna()]
    if not len(df):
        return df
    df = df.sort_values("snapshot_ts")
    return df.groupby(["game_pk", "player_id", "line", "selection", "book"],
                      dropna=False, as_index=False).tail(1)


def scan_system(system: str, date: str, min_ev: float = 0.04) -> pd.DataFrame:
    market = SYS_MARKET[system]
    preds = gp.gen_preds(system, since=date, until=date)
    preds = preds[preds["mu"].notna()]
    if not len(preds):
        log.warning("%s: no scored feature rows for %s (build run yet?)", system, date)
        return pd.DataFrame()
    mu_by_pid = {int(r.player_id): (float(r.mu), float(r.nb_alpha))
                 for r in preds.itertuples(index=False) if pd.notna(r.player_id)}

    quotes = _latest_quotes(market, date)
    if not len(quotes):
        log.warning("%s: no %s quotes banked for %s", system, market, date)
        return pd.DataFrame()

    # modal (most-quoted) line per player = the MAIN line; everything else ALT
    main_line = (quotes.groupby("player_id")["line"]
                 .agg(lambda s: s.mode().iloc[0]).to_dict())

    rows = []
    for r in quotes.itertuples(index=False):
        pid = int(r.player_id)
        if pid not in mu_by_pid:
            continue
        mu, alpha = mu_by_pid[pid]
        po = gp.p_over(float(r.line), mu, alpha)
        p = po if str(r.selection).upper() == "OVER" else 1.0 - po
        ev = p * float(r.decimal) - 1.0
        if ev < min_ev:
            continue
        rows.append({
            "system": system, "market": market, "game_date": date,
            "game_pk": r.game_pk, "player_id": pid,
            "selection": r.selection, "line": float(r.line),
            "is_alt": float(r.line) != float(main_line.get(pid, r.line)),
            "book": r.book, "american": r.american, "decimal": float(r.decimal),
            "mu": round(mu, 3), "p_model": round(p, 4), "ev": round(ev, 4),
            "snapshot_ts": str(r.snapshot_ts),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("ev", ascending=False).reset_index(drop=True)
    return out


def _append_log(df: pd.DataFrame, date: str) -> str:
    key = f"Alerts/{date}/altline.parquet"
    try:
        prior = pd.read_parquet(io.BytesIO(storage.read_bytes(key)))
        df = pd.concat([prior, df], ignore_index=True)
    except Exception:  # noqa: BLE001 -- first write today
        pass
    keys = ["system", "game_pk", "player_id", "line", "selection", "book", "snapshot_ts"]
    df = df.drop_duplicates(subset=[c for c in keys if c in df.columns], keep="last")
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    storage.write_bytes(buf.getvalue(), key)
    return key


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Model-EV scan over every quoted line (main + alt)")
    p.add_argument("--systems", default="K,BATTER_TB",
                   help=f"comma list of {', '.join(SYS_MARKET)}")
    p.add_argument("--date", required=True, help="game_date YYYY-MM-DD")
    p.add_argument("--min-ev", type=float, default=0.04)
    p.add_argument("--log", action="store_true",
                   help="append hits to Alerts/{date}/altline.parquet for grading")
    args = p.parse_args(argv)
    systems = [s.strip().upper() for s in args.systems.split(",") if s.strip()]
    bad = [s for s in systems if s not in SYS_MARKET]
    if bad:
        p.error(f"unknown systems {bad}; count systems only: {list(SYS_MARKET)}")

    frames = [scan_system(s, args.date, min_ev=args.min_ev) for s in systems]
    frames = [f for f in frames if len(f)]
    print(f"\nalt-line scan {args.date}  min_ev={args.min_ev:.0%}  systems={systems}")
    if not frames:
        print("  no quotes clear the EV bar (or no preds/quotes for the date).")
        return 0
    out = pd.concat(frames, ignore_index=True).sort_values("ev", ascending=False)
    n_alt = int(out["is_alt"].sum())
    print(f"  {len(out)} +EV quotes ({n_alt} on ALT lines):\n")
    show = out.copy()
    show["ev"] = (show["ev"] * 100).round(1)
    show["tag"] = show["is_alt"].map({True: "ALT", False: "main"})
    cols = ["system", "player_id", "tag", "selection", "line", "book",
            "american", "mu", "p_model", "ev", "snapshot_ts"]
    with pd.option_context("display.width", 220, "display.max_rows", 50):
        print(show[cols].to_string(index=False))
    if args.log:
        key = _append_log(out, args.date)
        print(f"\nlogged -> {key}  (grade later: did the quote survive / move our way?)")
    print("\nDISCIPLINE: paper-log and grade before betting -- model-vs-MAIN-line "
          "edge was ~0; the hypothesis is that ALT lines are softer. CLV decides.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
