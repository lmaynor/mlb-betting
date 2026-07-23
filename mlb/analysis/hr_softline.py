"""
mlb.analysis.hr_softline -- HR-YES soft-book +EV vs a SHARP low-vig anchor.

The viable HR edge is NOT out-predicting the market (proven dead: model AUC 0.63 <
market 0.68, YES side -22%). It's catching a SOFT book's stale HR-YES price before
it corrects, measured against the SHARP low-vig consensus.

Why a new tool vs outlier_scan: outlier_scan anchors on the full-book median or a
single book (Pinnacle, which rarely quotes HR props). This anchors on the SET of
low-vig sharp books (prophetx/novig/bet365/pinnacle per book_vig) -- robust when any
one is absent -- and tags every quote soft/sharp so you bet only the SOFT outlets
(where size + staleness live), not a sharp book that's merely leading the move.

Per (game, player, line, selection):
  sharp_fair = median over SHARP books of devig_unilateral(implied, that book's vig)
  for each SOFT book:  ev = sharp_fair * book_decimal - 1   (bet the soft book's price)
Kalshi is intentionally NOT the anchor here -- it's too thin on props (use
kalshi_vs_books for game_ml/total/nrfi). This is books-vs-books on hr_yn.

EV vs a sharp anchor, not a settled backtest: it needs no outcomes, but it's only as
good as the sharp mid. Confirm flagged soft books hold up on realized CLV before sizing.

Run (Cloud Shell):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.hr_softline --date 2026-07-23 --min-ev 0.03
  PYTHONPATH=. python3 -m mlb.analysis.hr_softline --market hr_yn --since 2026-07-01
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from mlb.analysis import odds_history as oh
from mlb.analysis import book_vig
from mlb.analysis.backtest_market import OFFSHORE
from mlb_core.odds.utils import devig_unilateral

# Thresholds calibrated to the hr_yn vig clusters (diagnostic 2026-07-23):
# sharp = {bet365 4.2%, novig 6.0%, parx 6.0%} sit under 6.5%; the ~6.64% pack is
# book_vig's fallback (no fitted entry); soft = {fliff 9.1%, hardrock 7.4%}.
SHARP_MAX_VIG = 0.065   # books at/under this empirical hold = sharp anchor set
SOFT_MIN_VIG = 0.072    # books at/over this = bettable soft outlets
# No real HR-YES price is longer than ~+1500. Anything beyond is a stale/placeholder
# /broken line (e.g. hardrock's +20000 in odds_history) that manufactures fake +EV --
# reject it before it pollutes the scan (2026-07-23 validation caught -100% on these).
MAX_AMERICAN = 1500
NON_BOOK = OFFSHORE | {"kalshi", "open", "consensus", "average"}
KEYS = ["game_pk", "player_id", "line", "selection"]


def _decimal(df: pd.DataFrame) -> pd.Series:
    dec = pd.to_numeric(df.get("decimal"), errors="coerce")
    am = pd.to_numeric(df.get("american"), errors="coerce")
    from_am = np.where(am > 0, 1.0 + am / 100.0, 1.0 + 100.0 / am.abs())
    return dec.where(dec.notna() & (dec > 1.0), pd.Series(from_am, index=df.index))


def score_softline(raw: pd.DataFrame, market: str, min_ev: float = 0.03,
                   min_sharp: int = 1, latest_only: bool = True,
                   sharp_max_vig: float = SHARP_MAX_VIG,
                   soft_min_vig: float = SOFT_MIN_VIG,
                   max_american: float = MAX_AMERICAN) -> pd.DataFrame:
    """Pure core: soft-book +EV vs the sharp low-vig anchor. No I/O.
    Returns one row per flagged (game,player,line,selection,soft-book) quote.

    The anchor pool is ALL low-vig books (incl. exchanges/Pinnacle) -- NON_BOOK
    only excludes them from the BETTABLE soft set, never from the anchor."""
    if raw is None or not len(raw):
        return pd.DataFrame()
    df = raw.copy()
    df = df[pd.to_numeric(df["implied_prob"], errors="coerce").notna()]
    df["book_l"] = df["book"].astype(str).str.lower()
    df = df[df["book_l"] != "open"]            # drop only the 'open' pseudo-marker
    if not len(df):
        return pd.DataFrame()
    df["line"] = pd.to_numeric(df["line"], errors="coerce").fillna(-999.0)
    if latest_only and "snapshot_ts" in df.columns:
        df = (df.sort_values("snapshot_ts")
                .groupby(KEYS + ["book_l"], dropna=False, as_index=False).tail(1))

    df["dec"] = _decimal(df)
    df["impl"] = pd.to_numeric(df["implied_prob"], errors="coerce")
    df["vig"] = [book_vig.get_vig(market, b, default=0.07) for b in df["book_l"]]
    # each book's own no-vig fair estimate (unilateral -- HR-YES is one-sided)
    df["fair"] = [devig_unilateral(p, vig_pct=v) for p, v in zip(df["impl"], df["vig"])]
    df["is_sharp"] = df["vig"] <= sharp_max_vig                       # anchor: any low-vig book
    df["is_soft"] = (df["vig"] >= soft_min_vig) & (~df["book_l"].isin(NON_BOOK))  # bettable soft only

    sharp = df[df["is_sharp"]]
    if not len(sharp):
        return pd.DataFrame()
    anchor = (sharp.groupby(KEYS, dropna=False)
                   .agg(sharp_fair=("fair", "median"), n_sharp=("book_l", "nunique"))
                   .reset_index())
    anchor = anchor[anchor["n_sharp"] >= min_sharp]
    if not len(anchor):
        return pd.DataFrame()

    soft = df[df["is_soft"]].merge(anchor, on=KEYS, how="inner")
    if not len(soft):
        return pd.DataFrame()
    # Odds sanity: drop stale/placeholder longshots (+20000-type lines) that fabricate EV.
    soft = soft[pd.to_numeric(soft["american"], errors="coerce") <= max_american]
    if not len(soft):
        return pd.DataFrame()
    soft["ev"] = (soft["sharp_fair"] * soft["dec"] - 1.0).round(4)
    soft["edge_prob"] = (soft["sharp_fair"] - soft["impl"]).round(4)
    soft["market"] = market
    hits = soft[soft["ev"] >= min_ev].copy()
    return hits.sort_values("ev", ascending=False).reset_index(drop=True)


def scan(markets, since, until, min_ev, min_sharp, latest_only,
         sharp_max_vig=SHARP_MAX_VIG, soft_min_vig=SOFT_MIN_VIG, max_american=MAX_AMERICAN):
    frames = []
    for m in markets:
        try:
            raw = oh.read_history(m, since=since, until=until)
        except Exception as e:  # noqa: BLE001
            print(f"  {m}: read failed -- {e}")
            continue
        raw = oh.dedupe_by_source(raw) if len(raw) else raw
        hits = score_softline(raw, m, min_ev=min_ev, min_sharp=min_sharp,
                              latest_only=latest_only, sharp_max_vig=sharp_max_vig,
                              soft_min_vig=soft_min_vig, max_american=max_american)
        sharp_names = []
        if len(raw):
            bl = raw["book"].astype(str).str.lower()
            vigs = {b: book_vig.get_vig(m, b, default=0.07) for b in bl.unique()}
            sharp_names = sorted(b for b, v in vigs.items() if v <= sharp_max_vig)
        print(f"  {m:<10} rows={len(raw):>6}  sharp_books={len(sharp_names)} {sharp_names}  "
              f"+EV_soft_quotes={len(hits):>4}")
        if len(hits):
            frames.append(hits)
    if not frames:
        print("\nno soft-book +EV vs sharp anchor (thin sharp coverage, or books in line).")
        return pd.DataFrame()
    allq = pd.concat(frames, ignore_index=True)

    cols = ["market", "game_date", "away_team", "home_team", "player_id", "selection",
            "line", "book_l", "american", "impl", "sharp_fair", "n_sharp", "edge_prob", "ev"]
    cols = [c for c in cols if c in allq.columns]
    print(f"\n=== HR SOFT-LINE +EV (soft book vs sharp low-vig anchor, EV >= {min_ev:.0%}) : {len(allq)} ===")
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.max_rows", 40):
        print(allq[cols].head(40).to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    tbl = (allq.groupby("book_l")
              .agg(n=("ev", "size"), mean_ev=("ev", "mean"), max_ev=("ev", "max"))
              .sort_values("mean_ev", ascending=False))
    print("\n=== soft books ranked by mean +EV vs the sharp anchor ===")
    print(tbl.to_string(float_format=lambda x: f"{x:,.3f}"))
    print("\nCAVEAT: +EV only if the sharp low-vig consensus is the true prob and the soft\n"
          "book is LAGGING (corrects toward it). Confirm on realized CLV / quote_survival\n"
          "before sizing. Kalshi is not the anchor here (too thin on props).")
    return allq


def _realized_hr():
    """{(game_pk, batter): hr>=1} from the HR feature table -- the actual outcome."""
    from mlb.analysis import gen_preds as gp
    spec = gp.SPECS["HR"]
    df = gp._read_csv(spec.feature_csv, low_memory=False)[["game_pk", spec.id_col, spec.label_col]].dropna()
    g = pd.to_numeric(df["game_pk"], errors="coerce")
    b = pd.to_numeric(df[spec.id_col], errors="coerce")
    h = pd.to_numeric(df[spec.label_col], errors="coerce")
    return {(int(gi), int(bi)): int(hi >= 1) for gi, bi, hi in zip(g, b, h)
            if pd.notna(gi) and pd.notna(bi) and pd.notna(hi)}


def validate(allq: pd.DataFrame) -> None:
    """Settle the flagged soft +EV quotes vs the REAL HR outcome -- the go/no-go.
    A soft-line edge is only real if these flagged bets actually PROFIT."""
    if allq is None or not len(allq):
        print("\nvalidate: no flagged quotes to settle.")
        return
    real = _realized_hr()
    q = allq.copy()
    q["hr"] = [real.get((int(g), int(p))) if pd.notna(g) and pd.notna(p) else None
               for g, p in zip(pd.to_numeric(q["game_pk"], errors="coerce"),
                               pd.to_numeric(q["player_id"], errors="coerce"))]
    q = q[q["hr"].notna()].copy()
    if not len(q):
        print("\nvalidate: 0 flagged quotes matched a settled HR outcome (future games / id mismatch).")
        return
    yes = q["selection"].str.upper().isin(["OVER", "YES"])
    q["won"] = ((q["hr"] >= 1) == yes).astype(int)
    q["roi"] = q["won"].mul(q["dec"] - 1.0).where(q["won"] == 1, -1.0)
    print("\n=== REALIZED validation: flagged soft +EV quotes settled vs actual HR ===")
    print(f"  n={len(q)}  hit%={q['won'].mean()*100:.1f}  ROI={q['roi'].mean()*100:+.1f}%  "
          f"units={q['roi'].sum():+.1f}")
    by = q.groupby("book_l").agg(n=("won", "size"), hit=("won", "mean"), roi=("roi", "mean"))
    by["hit"] = (by["hit"] * 100).round(1)
    by["roi"] = (by["roi"] * 100).round(1)
    print(by.sort_values("roi", ascending=False).to_string())
    print("  REAL edge => ROI > 0 on decent n. ROI <= 0 => the '+EV vs anchor' was illusory\n"
          "  (anchor mispriced, or soft prices not actually beatable). CLV/quote_survival next.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="HR-YES soft-book +EV vs a sharp low-vig anchor")
    p.add_argument("--market", default="hr_yn", help="canonical market (default hr_yn)")
    p.add_argument("--markets", default=None, help="comma list override (else --market)")
    p.add_argument("--date", default=None, help="single game_date YYYY-MM-DD")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-ev", type=float, default=0.03, help="EV threshold, 0.03 = +3%")
    p.add_argument("--min-sharp", type=int, default=1, help="require >= N sharp books for the anchor")
    p.add_argument("--sharp-vig", type=float, default=SHARP_MAX_VIG,
                   help=f"max vig to count a book as sharp (default {SHARP_MAX_VIG})")
    p.add_argument("--soft-vig", type=float, default=SOFT_MIN_VIG,
                   help=f"min vig to count a book as a bettable soft outlet (default {SOFT_MIN_VIG})")
    p.add_argument("--validate", action="store_true",
                   help="settle flagged quotes vs actual HR outcomes (go/no-go); best over full history")
    p.add_argument("--max-odds", type=float, default=MAX_AMERICAN,
                   help=f"reject soft quotes longer than +this (stale/fake lines; default {MAX_AMERICAN})")
    p.add_argument("--all-snapshots", action="store_true", help="scan every snapshot, not just freshest")
    args = p.parse_args(argv)
    markets = ([m.strip() for m in args.markets.split(",")] if args.markets else [args.market])
    since = args.date or args.since
    until = args.date or args.until
    print(f"HR soft-line scan | markets={markets} min_ev={args.min_ev:.0%} "
          f"min_sharp={args.min_sharp} | sharp<= {args.sharp_vig:.0%} vig, soft>= {args.soft_vig:.0%}")
    allq = scan(markets, since, until, args.min_ev, args.min_sharp, not args.all_snapshots,
                sharp_max_vig=args.sharp_vig, soft_min_vig=args.soft_vig, max_american=args.max_odds)
    if args.validate:
        validate(allq)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
