"""
ev.hurdleledger.runner -- the ledger CLI.

    python -m ev.hurdleledger.runner report [--out PATH]
        Print the plain-language monthly scoreboard (and write it to PATH if given).

    python -m ev.hurdleledger.runner rec log --book draftkings \
        --bet "Tatum over 26.5 points" --stake 25 --price -110 [--source chat]
        Log one recreational bet the moment it is fired. One command, no prompts;
        prints the bet id and the exact settle command for later.

    python -m ev.hurdleledger.runner rec settle --id rec-20261116-ab12cd34 \
        --result win [--pnl 22.73] [--supersede]
        Settle a logged bet. P&L is computed from stake and price unless --pnl
        gives the book's own figure.

    python -m ev.hurdleledger.runner rec list
        Open (unsettled) recreational bets, so nothing fired goes ungraded.
"""

from __future__ import annotations

import argparse
import sys

from . import rec as rec_mod
from . import streams
from .config import load_config
from .ledger import build_ledger
from .report import render_text


def _cmd_report(args) -> int:
    cfg = load_config()
    text = render_text(build_ledger(cfg))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text, end="")
    return 0


def _cmd_rec_log(args) -> int:
    cfg = load_config()
    row = rec_mod.log_bet(
        cfg.rec_log, book=args.book, description=args.bet, stake_usd=args.stake,
        price_american=args.price, price_decimal=args.decimal, source=args.source)
    price = f"{row['price_american']:+d}"
    print(f"Logged {row['id']}: {args.book}, {args.bet}, "
          f"${row['stake_usd']:,.2f} at {price}.")
    print(f"When it settles: python -m ev.hurdleledger.runner rec settle "
          f"--id {row['id']} --result win|loss|push|void")
    return 0


def _cmd_rec_settle(args) -> int:
    cfg = load_config()
    row = rec_mod.settle_bet(cfg.rec_log, args.id, args.result,
                             pnl_usd=args.pnl, supersede=args.supersede)
    sign = "+" if row["pnl_usd"] > 0 else ""
    print(f"Settled {args.id}: {args.result}, P&L {sign}${row['pnl_usd']:,.2f}.")
    return 0


def _cmd_rec_list(args) -> int:
    cfg = load_config()
    read = streams.read_rec(cfg.rec_log)
    if not read.open_bets:
        print("No open recreational bets.")
        return 0
    for b in read.open_bets:
        print(f"{b['id']}  {b.get('ts_placed', '')[:10]}  {b.get('book', '')}  "
              f"{b.get('description', '')}  ${float(b.get('stake_usd') or 0):,.2f} "
              f"at {int(b.get('price_american') or 0):+d}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hurdleledger", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="monthly profit scoreboard")
    r.add_argument("--out", default="", help="also write the report to this path")
    r.set_defaults(fn=_cmd_report)

    rec = sub.add_parser("rec", help="recreational bet log")
    rsub = rec.add_subparsers(dest="rec_cmd", required=True)

    lg = rsub.add_parser("log", help="log a bet as it is fired")
    lg.add_argument("--book", required=True)
    lg.add_argument("--bet", required=True, help="what the bet is, in plain words")
    lg.add_argument("--stake", required=True, type=float)
    lg.add_argument("--price", type=int, default=None, help="american odds, e.g. -110 or 150")
    lg.add_argument("--decimal", type=float, default=None, help="decimal odds, e.g. 1.91")
    lg.add_argument("--source", default="manual",
                    choices=["manual", "chat", "gambly-deeplink"])
    lg.set_defaults(fn=_cmd_rec_log)

    st = rsub.add_parser("settle", help="settle a logged bet")
    st.add_argument("--id", required=True)
    st.add_argument("--result", required=True, choices=list(rec_mod.RESULTS))
    st.add_argument("--pnl", type=float, default=None,
                    help="the book's own P&L figure, if it differs from stake/price math")
    st.add_argument("--supersede", action="store_true",
                    help="replace an existing settlement with this one")
    st.set_defaults(fn=_cmd_rec_settle)

    li = rsub.add_parser("list", help="open (unsettled) bets")
    li.set_defaults(fn=_cmd_rec_list)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
