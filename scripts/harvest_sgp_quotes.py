#!/usr/bin/env python3
"""
scripts/harvest_sgp_quotes.py -- Local Residential Playwright & Deep-Link Harvester CLI.

Modes:
  1. Parameter Calibration (--fit-only):
     Fits (rho_book, hold_book) from data/sgp_quotes.csv using scipy.optimize.curve_fit
     and writes data/sgp_book_params.json.
     Usage:
       python -m scripts.harvest_sgp_quotes --fit-only --csv data/sgp_quotes.csv

  2. Deep Link & Browser Harvester (--open-links / --playwright):
     Opens pre-filtered multi-leg SGP betslip deep links on a residential machine,
     records live displayed SGP odds, appends to data/sgp_quotes.csv, and re-fits params.
     Playwright is imported lazily so CI / Cloud Run never fails if Playwright is absent.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import webbrowser
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ev.sgp_book_pricer import (
    DEFAULT_PARAMS_PATH,
    SGP_ARCHETYPES,
    fit_book_sgp_params,
    load_book_sgp_params,
)

DEFAULT_CSV_PATH = "data/sgp_quotes.csv"
CSV_COLUMNS = [
    "book", "archetype", "leg1_odds", "leg2_odds", "sgp_odds",
    "leg3_odds", "timestamp", "source", "player", "event",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("harvest_sgp_quotes")


def read_quotes_csv(csv_path: str) -> List[Dict[str, Any]]:
    """Read recorded SGP quotes from CSV."""
    if not os.path.exists(csv_path):
        return []
    out = []
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            if not r.get("book") or not r.get("archetype") or not r.get("sgp_odds"):
                continue
            out.append(r)
    return out


def append_quote_to_csv(row: Dict[str, Any], csv_path: str = DEFAULT_CSV_PATH) -> None:
    """Append a single recorded quote to CSV."""
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def run_fit_only(csv_path: str, params_path: str) -> int:
    """Run parameter fitting on CSV and save JSON."""
    quotes = read_quotes_csv(csv_path)
    if not quotes:
        log.warning("No quotes found in %s to fit.", csv_path)
        return 1

    log.info("Fitting book SGP parameters on %d quotes from %s...", len(quotes), csv_path)
    fitted = fit_book_sgp_params(quotes, out_path=params_path)
    log.info("Fitted %d (book, archetype) parameter sets:", len(fitted))
    for key, p in sorted(fitted.items()):
        log.info(
            "  %s: rho=%.4f, hold=%.4f (n=%d, rmse_implied=%.6f, rmse_dec=%.2f)",
            key, p["rho_book"], p["hold_book"], p["n"], p["rmse_implied"], p["rmse_decimal"],
        )
    log.info("Saved parameters to %s", params_path)
    return 0


def harvest_browser_links(
    candidates_path: Optional[str],
    csv_path: str,
    params_path: str,
    limit: int = 10,
    use_playwright: bool = False,
) -> int:
    """Open candidate deep links in browser and record displayed quotes."""
    cands: List[Dict[str, Any]] = []
    if candidates_path and os.path.exists(candidates_path):
        with open(candidates_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
            cands = raw.get("candidates", []) if isinstance(raw, dict) else raw
    else:
        # Generate on the fly if odds_board exists
        try:
            from ev.odds_board import read_board
            from ev.sgp_proxy_builder import build_sgp_proxy_candidates
            for sport in ("nba", "nhl", "nfl"):
                board = read_board(sport)
                if board and board.get("rows"):
                    cands.extend([c.to_dict() for c in build_sgp_proxy_candidates(board["rows"])])
        except Exception as exc:
            log.warning("Could not auto-generate candidates: %s", exc)

    if not cands:
        log.warning("No SGP candidates available to harvest.")
        return 1

    cands.sort(key=lambda c: float(c.get("ev_pct", 0.0)), reverse=True)
    top_cands = cands[:limit]
    log.info("Selected top %d candidates for quote harvesting:", len(top_cands))

    playwright_inst = None
    browser = None
    context = None

    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright  # lazy import
            playwright_inst = sync_playwright().start()
            browser = playwright_inst.chromium.launch(headless=False)
            context = browser.new_context()
            log.info("Playwright browser launched successfully.")
        except ImportError:
            log.warning("Playwright is not installed. Falling back to system default browser.")
            log.warning("To enable automated Playwright harvesting: pip install playwright && playwright install chromium")
            use_playwright = False
        except Exception as exc:
            log.warning("Could not launch Playwright: %s. Falling back to default browser.", exc)
            use_playwright = False

    recorded_count = 0
    try:
        for idx, cand in enumerate(top_cands):
            player = cand.get("player", "Unknown")
            book = cand.get("book", "Unknown")
            arch = cand.get("archetype", "Unknown")
            legs = cand.get("legs", [])
            link = cand.get("deeplink")
            est_am = cand.get("est_sgp_american", 0)

            print("\n" + "=" * 70)
            print(f"[{idx + 1}/{len(top_cands)}] {player} @ {book.upper()} ({arch})")
            for leg in legs:
                print(f"  - {leg.get('label')}: {leg.get('book_american', 0):+d}")
            print(f"  Est. SGP Odds: {est_am:+d} | Deep link: {link}")
            print("=" * 70)

            if not link:
                continue

            if use_playwright and context:
                page = context.new_page()
                try:
                    page.goto(link, timeout=20000)
                except Exception as exc:
                    log.warning("Failed to navigate to %s: %s", link, exc)
            else:
                webbrowser.open(link)

            prompt = input("Enter live SGP American odds displayed on book (or press Enter to skip, 'q' to quit): ").strip()
            if prompt.lower() == "q":
                break
            if not prompt:
                continue

            try:
                sgp_val = int(prompt.replace("+", ""))
                l1_val = legs[0].get("book_american") if len(legs) >= 1 else 0
                l2_val = legs[1].get("book_american") if len(legs) >= 2 else 0
                l3_val = legs[2].get("book_american") if len(legs) >= 3 else ""

                row = {
                    "book": book,
                    "archetype": arch,
                    "leg1_odds": l1_val,
                    "leg2_odds": l2_val,
                    "sgp_odds": sgp_val,
                    "leg3_odds": l3_val,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "playwright" if use_playwright else "manual_open",
                    "player": player,
                    "event": cand.get("game_key", ""),
                }
                append_quote_to_csv(row, csv_path)
                recorded_count += 1
                log.info("Recorded quote for %s: %d", player, sgp_val)
            except ValueError:
                print(f"Invalid odds value: '{prompt}'. Skipped.")
    finally:
        if browser:
            browser.close()
        if playwright_inst:
            playwright_inst.stop()

    if recorded_count > 0:
        log.info("Recorded %d new quotes. Re-fitting parameters...", recorded_count)
        run_fit_only(csv_path, params_path)

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fit-only", action="store_true", help="Fit parameters from quotes CSV and exit")
    ap.add_argument("--csv", default=DEFAULT_CSV_PATH, help=f"Path to quotes CSV (default: {DEFAULT_CSV_PATH})")
    ap.add_argument("--params", default=DEFAULT_PARAMS_PATH, help=f"Path to params JSON (default: {DEFAULT_PARAMS_PATH})")
    ap.add_argument("--candidates", default=None, help="Path to sgp_candidates JSON")
    ap.add_argument("--limit", type=int, default=10, help="Number of candidates to open")
    ap.add_argument("--playwright", action="store_true", help="Use Playwright browser context")
    ap.add_argument("--open-links", action="store_true", help="Open links in system default browser")

    args = ap.parse_args(argv)

    if args.fit_only:
        return run_fit_only(args.csv, args.params)

    if args.open_links or args.playwright:
        return harvest_browser_links(
            args.candidates,
            args.csv,
            args.params,
            limit=args.limit,
            use_playwright=args.playwright,
        )

    # If neither mode passed, print help
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
