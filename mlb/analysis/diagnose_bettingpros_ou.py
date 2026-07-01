"""
mlb.analysis.diagnose_bettingpros_ou -- inspect BettingPros O/U offer structure.

Purpose: pin down the OVER/UNDER price-labeling bug in the odds ingest. The
symptom (odds_history): for the same player/line, some books quote OVER as a heavy
favorite and others as a heavy dog -- internally-consistent MIRROR IMAGES. The
suspects in mlb_core.odds.bettingpros._rows_player_ou / _sel_odds:
  1. `_sel_odds` takes book["lines"][0] -- if a book returns MULTIPLE line points
     (alternate lines 0.5/1.5/2.5), [0] grabs an arbitrary one.
  2. `_rows_player_ou` defaults Line="0.5" and only sets it from the OVER selection,
     and _assign OVERWRITES the same {book}_Over/_Under columns across selections --
     so multiple alternate lines collapse into one row with mismatched side<->line.

This fetches TODAY's live offers (structure is date-independent) and prints:
  - one full raw offer (JSON) so we see the exact nesting
  - per selection: its label + how many line points each book carries
  - a flag if any book has >1 line, or if OVER vs UNDER selections disagree on line

Run in Cloud Shell (BettingPros reachable there):
    PYTHONPATH=. python3 -m mlb.analysis.diagnose_bettingpros_ou            # total_bases (293)
    PYTHONPATH=. python3 -m mlb.analysis.diagnose_bettingpros_ou --market 293 --date 2026-07-01
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from mlb_core.odds import bettingpros as bp


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Dump BettingPros O/U offer structure")
    p.add_argument("--market", type=int, default=293, help="market id (293=total_bases, 291=hits)")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--n-offers", type=int, default=8, help="offers to summarize")
    args = p.parse_args(argv)

    name, kind = bp.MARKETS.get(args.market, ("?", "?"))
    print(f"market {args.market} = {name} ({kind})  date={args.date}\n")

    sess = bp.make_session()
    ev = bp.fetch_events(sess, args.date)
    if not ev:
        print("no events for that date (try today's date)"); return 1
    eids = list(ev.keys())
    offers = bp.fetch_offers(sess, args.market, eids)
    if not offers:
        print("no offers returned"); return 1
    print(f"{len(offers)} offers; {len(eids)} events\n")

    # 1) one full raw offer
    print("=== RAW OFFER[0] (truncated 3500 chars) ===")
    print(json.dumps(offers[0], indent=2)[:3500])

    # 2) per-selection / per-book line structure
    print("\n=== PER-SELECTION LINE STRUCTURE (first offers) ===")
    multi_line_books = 0
    for o in offers[:args.n_offers]:
        parts = o.get("participants") or []
        who = (parts[0].get("name") if parts else "?") or "?"
        print(f"\nplayer: {who}  (event {o.get('event_id')})")
        for sel in o.get("selections") or []:
            lab = sel.get("selection") or sel.get("label") or "?"
            books = sel.get("books") or []
            # per book: list of (line, cost) across that book's lines[]
            summ = []
            for b in books[:6]:
                lines = b.get("lines") or []
                if len(lines) > 1:
                    multi_line_books += 1
                summ.append(f"bk{b.get('id')}:{[(ln.get('line'), ln.get('cost')) for ln in lines]}")
            open_ln = (sel.get('opening_line') or {}).get('line')
            print(f"  selection={lab!r:8} opening_line={open_ln}  "
                  f"n_books={len(books)}")
            for s in summ:
                print(f"      {s}")

    print(f"\n=== VERDICT ===")
    print(f"books with >1 line point (alternate lines bundled): {multi_line_books}")
    print("If >0, `_sel_odds` taking lines[0] grabs an arbitrary line -> the bug. Fix:")
    print("  match each book line to the offer/selection's target line, and emit one")
    print("  odds_history row PER (line), pairing OVER/UNDER of the SAME line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
