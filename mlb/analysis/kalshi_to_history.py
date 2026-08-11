"""
mlb.analysis.kalshi_to_history -- Kalshi exchange capture -> odds_history.

FORWARD-ONLY live feed (like parlayapi_to_history): pulls the current active
Kalshi MLB markets, normalizes them into the SAME odds_history Parquet schema,
tagged book="kalshi" source="kalshi", with fair_prob = order-book MID (Kalshi's
no-vig fair-probability estimate -- the sharp reference the soft-line +EV
strategy needs). Also banks the raw snapshot (incl. bid/ask sizes, volume, open
interest -- depth the fixed odds_history schema can't hold) to
Odds/kalshi/raw/{date}/snapshot_{HHMM}.json so nothing is lost.

Public market-data only: NO Kalshi API key / auth needed.

Priority (from the 2026-07-22 liquidity probe): NRFI + GAME_ML + TOTALS +
RUNLINE are deep and tight (1-3c spreads) = trustworthy mids; player props
(HR/K/TB/HITS/OUTS) are captured too but thin, so treat their mids as soft.

Run (Cloud Shell / Cloud Run; needs GCS + pyarrow + network):
  # dry run first -- resolves teams/game_pk/player_id, writes NOTHING:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_to_history --dry-run
  # then capture for real (append -- multiple runs/day accumulate):
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_to_history
  # the closing (post-lineup) capture can flag is_closing:
  PYTHONPATH=. python3 -m mlb.analysis.kalshi_to_history --closing
"""

from __future__ import annotations

import argparse
import json

from mlb_core import storage
from mlb_core.data import id_resolver
from mlb_core.odds import kalshi
from mlb.analysis import odds_history as oh

# Kalshi series ticker -> (canonical odds_history market, registry system, kind).
# kind drives normalization: rfi (yes=YRFI/no=NRFI), ml (one team-side row),
# over (yes=OVER/no=UNDER + line=floor_strike), spread (one team-side + line).
SERIES_MAP = {
    "KXMLBRFI":   ("nrfi_ou",  "1IOU",        "rfi"),
    "KXMLBGAME":  ("game_ml",  "GAME",        "ml"),
    "KXMLBF5":    ("f5_ml",    "F5",          "ml"),
    "KXMLBF3":    ("f3_ml",    "",            "ml"),
    "KXMLBHR":    ("hr_yn",    "HR",          "over"),
    "KXMLBKS":    ("k_ou",     "K",           "over"),
    "KXMLBOUTS":  ("outs_ou",  "OUTS",        "over"),
    "KXMLBTB":    ("btb_ou",   "BATTER_TB",   "over"),
    "KXMLBHIT":   ("bhits_ou", "BATTER_HITS", "over"),
    "KXMLBTOTAL": ("game_total", "",          "over"),
    "KXMLBSPREAD": ("game_rl", "",            "spread"),
}

PLAYER_KINDS = {"over"}  # kinds whose markets are per-player (need player_id)
PLAYER_SERIES = {"KXMLBHR", "KXMLBKS", "KXMLBOUTS", "KXMLBTB", "KXMLBHIT"}


def _resolve_pid(name, away, home, date, game_pk):
    if not id_resolver.is_player_name(name):
        return None
    return (id_resolver.resolve_player_id(name, away, date, game_pk)
            or id_resolver.resolve_player_id(name, home, date, game_pk))


def _selections(kind, market, p):
    """-> list of (selection, ask_price, mid_price). Empty if unpriceable."""
    if kind == "rfi":
        return [("YRFI", p["yes_ask"], p["yes_mid"]),
                ("NRFI", p["no_ask"], p["no_mid"])]
    if kind in ("ml", "spread"):
        # caller maps the raw outcome abbrev -> HOME/AWAY/TIE (needs event teams)
        return [(kalshi.market_outcome(market), p["yes_ask"], p["yes_mid"])]
    # "over" (props + totals)
    return [("OVER", p["yes_ask"], p["yes_mid"]),
            ("UNDER", p["no_ask"], p["no_mid"])]


def build_rows(series_filter, snapshot_ts, ingested_at, is_closing):
    """Return (history_rows, raw_rows, stats). No I/O."""
    date_part = snapshot_ts.split(" ")[0]
    hist, raw = [], []
    stats = {"series": {}, "no_date": 0, "no_gamepk": 0, "no_player": 0}

    for series, (market_canon, system, kind) in SERIES_MAP.items():
        if series_filter and series not in series_filter:
            continue
        markets = kalshi.fetch_active_markets(series)
        n_rows = 0
        for mk in markets:
            game_date, away, home = kalshi.parse_event_ticker(mk.get("event_ticker", ""))
            if not game_date:
                stats["no_date"] += 1
                continue
            game_pk = id_resolver.resolve_game_pk(game_date, away, home) if (away and home) else None
            if game_pk is None:
                stats["no_gamepk"] += 1
            p = kalshi.prices(mk)
            line = kalshi.ff(mk.get("floor_strike")) if kind in ("over", "spread") else None

            player_id, player_name = None, None
            if series in PLAYER_SERIES:
                player_name = kalshi.player_from_title(mk)
                player_id = _resolve_pid(player_name, away, home, game_date, game_pk)
                if player_id is None:
                    stats["no_player"] += 1

            # bank the full-depth raw row regardless of downstream mapping
            raw.append({
                "series": series, "ticker": mk.get("ticker"),
                "event_ticker": mk.get("event_ticker"), "title": mk.get("title"),
                "game_date": game_date, "away": away, "home": home,
                "game_pk": game_pk, "player": player_name, "player_id": player_id,
                "line": line, "close_time": mk.get("close_time"), **p,
            })

            for sel, ask, mid in _selections(kind, mk, p):
                # team-side markets: map the outcome abbrev to HOME/AWAY/TIE
                if kind in ("ml", "spread"):
                    if sel == "TIE":
                        selection = "TIE"
                    elif sel == home:
                        selection = "HOME"
                    elif sel == away:
                        selection = "AWAY"
                    else:
                        continue  # unknown outcome token -> skip
                else:
                    selection = sel
                if not ask or ask <= 0 or mid is None:
                    continue
                hist.append({
                    "sport": "mlb", "market": market_canon, "system": system,
                    "game_pk": game_pk, "game_date": game_date,
                    "event_id": mk.get("event_ticker"), "away_team": away,
                    "home_team": home, "player_id": player_id, "selection": selection,
                    "line": line, "book": "kalshi",
                    "american": kalshi.prob_to_american(ask),
                    "decimal": round(1.0 / ask, 4),
                    "implied_prob": round(ask, 6), "fair_prob": round(mid, 6),
                    "snapshot_ts": snapshot_ts, "is_open": False,
                    "is_closing": is_closing, "source": "kalshi",
                    "ingested_at": ingested_at,
                })
                n_rows += 1
        stats["series"][series] = {"active": len(markets), "rows": n_rows,
                                   "market": market_canon}
    stats["_date"] = date_part
    return hist, raw, stats


def convert(series_filter=None, snapshot_ts="", ingested_at="", is_closing=False,
            dry_run=False) -> dict:
    import pandas as pd

    hist, raw, stats = build_rows(series_filter, snapshot_ts, ingested_at, is_closing)
    hhmm = snapshot_ts.split(" ")[1].replace(":", "")[:4] if " " in snapshot_ts else "0000"

    written = 0
    markets_touched = set()
    if hist and not dry_run:
        df = pd.DataFrame(hist, columns=oh.SCHEMA_COLUMNS)
        for (market, gdate), part in df.groupby(["market", "game_date"]):
            written += oh.write_partition(part, market, gdate, append=True)
            markets_touched.add(market)
        for m in markets_touched:
            oh.coverage_report(m)
        # bank raw depth snapshot (forward feed, like OddsAccum)
        storage.write_bytes(
            json.dumps(raw, default=str).encode(),
            f"Odds/kalshi/raw/{stats['_date']}/snapshot_{hhmm}.json")

    return {"history_rows": len(hist), "written": written, "raw_rows": len(raw),
            "markets": sorted(markets_touched), "stats": stats}


def main(argv=None) -> int:
    from datetime import datetime, timezone
    ap = argparse.ArgumentParser(description="Capture Kalshi MLB markets -> odds_history")
    ap.add_argument("--series", default=None,
                    help="comma-list of Kalshi series tickers (default: all mapped)")
    ap.add_argument("--snapshot-ts", default=None,
                    help="'YYYY-MM-DD HH:MM:SS' (default: now UTC)")
    ap.add_argument("--ingested-at", default=None, help="ISO tag (default: now UTC)")
    ap.add_argument("--closing", action="store_true",
                    help="mark this capture as the closing snapshot (is_closing=True)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve + build rows, write NOTHING; print a summary")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    snapshot_ts = args.snapshot_ts or now.strftime("%Y-%m-%d %H:%M:%S")
    ingested_at = args.ingested_at or now.isoformat()
    series_filter = set(args.series.split(",")) if args.series else None

    res = convert(series_filter, snapshot_ts, ingested_at, args.closing, args.dry_run)
    st = res["stats"]
    print(f"snapshot_ts={snapshot_ts}  date={st['_date']}  "
          f"{'DRY RUN (no writes)' if args.dry_run else 'WROTE'}")
    for series, s in st["series"].items():
        print(f"  {series:13} -> {s['market']:10} active={s['active']:4} rows={s['rows']}")
    print(f"unresolved: game_pk={st['no_gamepk']} player={st['no_player']} "
          f"bad_event_ticker={st['no_date']}")
    print(f"history_rows={res['history_rows']} written={res['written']} "
          f"raw_rows={res['raw_rows']} markets={res['markets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
