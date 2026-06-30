"""
mlb.analysis.bettingpros_to_parquet -- BettingPros -> odds_history (roadmap P0.3).

Reads the partitioned BettingPros CSV store (Odds/bettingpros/{market}/{date}.csv,
written by mlb.runners.backfill_bettingpros) and normalizes it into the
odds_history Parquet schema (mlb.analysis.odds_history), one row per
(selection, book, snapshot).

What this does fully:
- Melts each market's WIDE per-book CSV into LONG odds_history rows.
- Maps display book names -> canonical onshore names; drops derived/offshore.
- Emits the opening line (is_open) and the latest/consensus line (is_closing)
  as two snapshots so line movement is preserved.
- De-vigs each two-way book pair (shin) into fair_prob.
- Writes partitions + a coverage report per market.

What is STUBBED (the genuinely hard bridge -- roadmap calls it out):
- game_pk / player_id resolution. `resolve_game_pk` / `resolve_player_id` return
  None for now (columns left null). Fill these with the norm_team + MLB schedule
  bridge to make the store join-ready. Everything else is wired so that's the
  only remaining work.

Run (Cloud Shell / Cloud Run; needs GCS + pyarrow):
  PYTHONPATH=. python3 -m mlb.analysis.bettingpros_to_parquet \
      --markets all --ingested-at "2026-06-29T00:00:00Z"
"""

from __future__ import annotations

import argparse
import sys

from mlb_core.data import id_resolver
from mlb_core.odds import bettingpros as bp
from mlb_core.odds.utils import (
    american_to_decimal,
    american_to_implied_prob,
    devig_two_way,
)
from mlb.analysis import odds_history as oh

# BettingPros market name -> (canonical odds_history market, system or "").
# System keys match mlb_core.registry; "" means a market we carry but that has
# no production system yet.
BP_TO_HISTORY = {
    "home_runs":               ("hr_yn", "HR"),
    "strikeouts":              ("k_ou", "K"),
    "outs_recorded":           ("outs_ou", "OUTS"),
    "hits":                    ("bhits_ou", "BATTER_HITS"),
    "total_bases":             ("btb_ou", "BATTER_TB"),
    "earned_runs":             ("per_ou", "PITCHER_ER"),
    "moneyline":               ("game_ml", "GAME"),
    "5th_inning_moneyline":    ("f5_ml", "F5"),
    "run_in_1st_inning":       ("nrfi_ou", "NRFI"),
    # carried, no system yet:
    "runs":                    ("runs_ou", ""),
    "rbi":                     ("rbi_ou", ""),
    "singles":                 ("singles_ou", ""),
    "doubles":                 ("doubles_ou", ""),
    "triples":                 ("triples_ou", ""),
    "steals":                  ("steals_ou", ""),
    "hits_runs_rbis":          ("hrr_ou", ""),
    "hits_allowed":            ("phits_ou", ""),
    "walks_allowed":           ("pwalks_ou", ""),
    "run_line":                ("game_rl", ""),
    "total_runs":              ("game_total", ""),
    "1st_inning_moneyline":    ("f1_ml", ""),
    "1st_inning_runs":         ("1i_total", ""),
    "5th_inning_runs":         ("5i_total", ""),
    "2nd_inning_runs":         ("2i_total", ""),
    "1st_inning_spread":       ("1i_spread", ""),
    "5th_inning_spread":       ("5i_spread", ""),
    "team_total_runs":         ("team_total", ""),
    "fifth_inning_team_runs":  ("5i_team_total", ""),
    "first_to_score":          ("first_to_score", ""),
}

# Display name -> canonical book. Drops "Open"/"Best Odds" (derived, handled
# separately) and offshore/DFS. ESPNBet absent (rebranded -> theScore).
BOOK_DISPLAY_TO_CANON = {
    "Consensus": "consensus", "DraftKings": "draftkings", "FanDuel": "fanduel",
    "bet365": "bet365", "BetMGM": "betmgm", "Caesars": "caesars",
    "Hard Rock Bet": "hardrock", "theScore Bet": "thescore", "Fanatics": "fanatics",
    "Fliff": "fliff", "BetRivers": "betrivers", "SugarHouse": "sugarhouse",
    "PartyCasino": "partycasino", "PointsBet": "pointsbet",
}

# (column suffix in the CSV, odds_history selection label) per kind.
KIND_SIDES = {
    "player_ou":  [("Over", "OVER"), ("Under", "UNDER")],
    "total":      [("Over", "OVER"), ("Under", "UNDER")],
    "team_total": [("Over", "OVER"), ("Under", "UNDER")],
    "yesno":      [("Yes", "YES"), ("No", "NO")],
    "moneyline":  [("Away", "AWAY"), ("Home", "HOME")],
    "spread":     [("Away", "AWAY"), ("Home", "HOME")],
}

DEVIG_METHOD = "shin"  # two-way de-vig; roadmap default for ML/O-U markets


# --- resolution: delegate to the shared MLB Stats API bridge -----------------
# Cached per date/season in id_resolver. Any failure -> None (column left null),
# so a resolution miss never aborts the conversion.

def resolve_game_pk(game_date: str, away: str, home: str):
    try:
        return id_resolver.resolve_game_pk(game_date, away, home)
    except Exception:  # noqa: BLE001
        return None


def resolve_player_id(name: str, team: str, game_date: str):
    try:
        return id_resolver.resolve_player_id(name, team, game_date)
    except Exception:  # noqa: BLE001
        return None


def _parse_american(cell):
    if cell is None:
        return None
    s = str(cell).strip().replace("+", "")
    if not s or s.lower() == "nan":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _away_home(kind: str, row: dict) -> tuple:
    if kind == "player_ou":
        mu = str(row.get("Matchup", "") or "")
        if " at " in mu:
            a, h = mu.split(" at ", 1)
            return a.strip(), h.strip()
        return "", ""
    return str(row.get("Away", "") or ""), str(row.get("Home", "") or "")


def _line_for(kind: str, row: dict, selection: str):
    if kind == "spread":
        return row.get("Away_Line") if selection == "AWAY" else row.get("Home_Line")
    if kind in ("player_ou", "total", "team_total", "yesno"):
        return row.get("Line")
    return None  # moneyline: no line


def _emit_quotes(row, kind, market, system, source, ingested_at,
                 book_suffix_lookup, snapshot_ts, is_open, is_closing):
    """Emit odds_history rows for one CSV row at one snapshot.

    book_suffix_lookup(book_display, selection_suffix) -> CSV column name.
    De-vigs each book's two sides into fair_prob.
    """
    game_date = str(row.get("Date", "") or "")
    away, home = _away_home(kind, row)
    player = str(row.get("Player", "") or "")
    team = str(row.get("Team", "") or "")
    player_id = resolve_player_id(player, team, game_date) if kind == "player_ou" else None
    game_pk = resolve_game_pk(game_date, away, home)
    sides = KIND_SIDES[kind]

    out = []
    for book_display, book_canon in BOOK_DISPLAY_TO_CANON.items():
        # collect both sides for this book, then de-vig the pair
        per_side = {}
        for suffix, selection in sides:
            col = book_suffix_lookup(book_display, suffix)
            am = _parse_american(row.get(col))
            if am is None:
                continue
            per_side[selection] = am
        if not per_side:
            continue
        fair = {}
        if len(per_side) == 2:
            (sa, am_a), (sb, am_b) = list(per_side.items())
            fa, fb = devig_two_way(
                american_to_implied_prob(am_a),
                american_to_implied_prob(am_b),
                method=DEVIG_METHOD,
            )
            fair = {sa: fa, sb: fb}
        for suffix, selection in sides:
            if selection not in per_side:
                continue
            am = per_side[selection]
            line = _line_for(kind, row, selection)
            out.append({
                "sport": "mlb", "market": market, "system": system,
                "game_pk": game_pk, "game_date": game_date,
                "event_id": str(row.get("Player_Page", "") or ""),
                "away_team": away, "home_team": home,
                "player_id": player_id, "selection": selection,
                "line": float(line) if _is_num(line) else None,
                "book": book_canon, "american": am,
                "decimal": round(american_to_decimal(am), 4),
                "implied_prob": round(american_to_implied_prob(am), 6),
                "fair_prob": round(fair[selection], 6) if selection in fair else None,
                "snapshot_ts": snapshot_ts, "is_open": is_open, "is_closing": is_closing,
                "source": "bettingpros", "ingested_at": ingested_at,
            })
    return out


def _is_num(x) -> bool:
    if x is None or x == "":
        return False
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def market_rows_to_history(bp_market_name: str, wide_df, ingested_at: str):
    """Pure transform: a market's wide BettingPros frame -> long odds_history rows.

    Emits TWO snapshots per CSV row: the opening consensus line (is_open, from
    the Open_* columns, 00:00) and the closing per-book lines (is_closing, from
    all sportsbook + Consensus columns, 23:30). Testable without GCS.
    """
    import pandas as pd

    market, system = BP_TO_HISTORY[bp_market_name]
    kind = next(k for n, k in bp.MARKETS.values() if n == bp_market_name)

    rows = []
    for rec in wide_df.to_dict("records"):
        game_date = str(rec.get("Date", "") or "")
        if not game_date:
            continue
        # closing: real sportsbooks + Consensus, at 23:30
        rows += _emit_quotes(
            rec, kind, market, system, "bettingpros", ingested_at,
            book_suffix_lookup=lambda b, s: f"{b}_{s}",
            snapshot_ts=f"{game_date} 23:30:00", is_open=False, is_closing=True,
        )
        # opening: the single Open_* consensus pair, at 00:00 (book=consensus)
        rows += _emit_quotes(
            rec, kind, market, system, "bettingpros", ingested_at,
            book_suffix_lookup=lambda b, s: (f"Open_{s}" if b == "Consensus" else "__none__"),
            snapshot_ts=f"{game_date} 00:00:00", is_open=True, is_closing=False,
        )
    return pd.DataFrame(rows, columns=oh.SCHEMA_COLUMNS)


def convert_market(bp_market_name: str, ingested_at: str,
                   since=None, until=None, dry_run=False) -> dict:
    """Read one BettingPros market, normalize, write odds_history partitions."""
    market, _ = BP_TO_HISTORY[bp_market_name]
    wide = bp.read_market(bp_market_name, start=since, end=until)
    if len(wide) == 0:
        return {"market": market, "dates": 0, "rows": 0}
    long_df = market_rows_to_history(bp_market_name, wide, ingested_at)
    dates = sorted(long_df["game_date"].dropna().unique().tolist())
    total = 0
    for d in dates:
        part = long_df[long_df["game_date"] == d]
        if dry_run:
            total += len(part)
        else:
            total += oh.write_partition(part, market, d)
    if not dry_run and dates:
        oh.coverage_report(market)
    return {"market": market, "dates": len(dates), "rows": total}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Normalize BettingPros CSVs -> odds_history Parquet")
    from datetime import datetime, timezone
    p.add_argument("--markets", default="all", help="bp groups (player,lines,innings,all) or names")
    p.add_argument("--ingested-at", default=None, help="ISO timestamp tag (default: now UTC)")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    args.ingested_at = args.ingested_at or datetime.now(timezone.utc).isoformat()

    # resolve group/id tokens -> bp market NAMES that we have a mapping for
    ids = bp.resolve_markets(args.markets)
    names = [bp.MARKETS[m][0] for m in ids if bp.MARKETS[m][0] in BP_TO_HISTORY]

    print(f"converting {len(names)} markets -> odds_history (dry_run={args.dry_run})")
    grand = 0
    for name in names:
        res = convert_market(name, args.ingested_at, args.since, args.until, args.dry_run)
        grand += res["rows"]
        print(f"  {name:<24} -> {res['market']:<14} {res['dates']:>4} dates, {res['rows']:>7} rows")
    print(f"DONE. {grand} odds_history rows{' (dry run, not written)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
