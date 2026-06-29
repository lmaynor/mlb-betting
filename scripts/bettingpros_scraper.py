"""
bettingpros_scraper.py
======================
Scrapes BettingPros game-prop odds pages for three prop types:
  - YRFI/NRFI        : /mlb/odds/game-props/run-in-first-inning/
  - Team Total Runs  : /mlb/odds/game-props/team-total-runs/
  - Team Score First : /mlb/odds/game-props/team-to-score-first/

Each prop type writes to its own CSV. Shared Selenium driver, shared
backfill/daily loop. Parsers are prop-specific.

NOTE: team-total-runs parser uses the same 2-cell-per-item structure
confirmed on the other two props. Verify column labels (over/under)
match live markup if results look wrong.

Usage
-----
  python bettingpros_scraper.py backfill [yrfi|totals|scorefirst|all]
  python bettingpros_scraper.py daily    [yrfi|totals|scorefirst|all] [YYYY-MM-DD]
"""

import atexit
import csv
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
from bs4 import BeautifulSoup

# ── try tqdm ──────────────────────────────────────────────────────────────────
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# =============================================================================
# CONFIGURATION
# =============================================================================

OUTPUT_DIR = "."   # change to your preferred output directory

ODDS_CSV = {
    "yrfi":       os.path.join(OUTPUT_DIR, "yrfi_odds.csv"),
    "totals":     os.path.join(OUTPUT_DIR, "team_totals_odds.csv"),
    "scorefirst": os.path.join(OUTPUT_DIR, "team_score_first_odds.csv"),
}

PROP_URLS = {
    "yrfi":       "https://www.bettingpros.com/mlb/odds/game-props/run-in-first-inning/",
    "totals":     "https://www.bettingpros.com/mlb/odds/game-props/team-total-runs/",
    "scorefirst": "https://www.bettingpros.com/mlb/odds/game-props/team-to-score-first/",
}

BACKFILL_START = "2024-04-01"
BACKFILL_END   = "2025-10-31"
DELAY          = 3.0   # seconds between pages

MLB_SEASON_RANGES = {
    2024: ("2024-03-20", "2024-11-03"),
    2025: ("2025-03-20", "2025-11-03"),
    2026: ("2026-03-20", "2026-11-03"),
    2027: ("2027-03-20", "2027-11-03"),
}

SLUG_TO_ABBR = {
    "arizona-diamondbacks": "ARI",   "atlanta-braves": "ATL",
    "baltimore-orioles": "BAL",      "boston-red-sox": "BOS",
    "chicago-cubs": "CHC",           "chicago-white-sox": "CWS",
    "cincinnati-reds": "CIN",        "cleveland-guardians": "CLE",
    "colorado-rockies": "COL",       "detroit-tigers": "DET",
    "houston-astros": "HOU",         "kansas-city-royals": "KC",
    "los-angeles-angels": "LAA",     "los-angeles-dodgers": "LAD",
    "miami-marlins": "MIA",          "milwaukee-brewers": "MIL",
    "minnesota-twins": "MIN",        "new-york-mets": "NYM",
    "new-york-yankees": "NYY",       "oakland-athletics": "OAK",
    "philadelphia-phillies": "PHI",  "pittsburgh-pirates": "PIT",
    "san-diego-padres": "SD",        "san-francisco-giants": "SF",
    "seattle-mariners": "SEA",       "st-louis-cardinals": "STL",
    "tampa-bay-rays": "TB",          "texas-rangers": "TEX",
    "toronto-blue-jays": "TOR",      "washington-nationals": "WSH",
    "athletics": "OAK",              "sacramento-athletics": "OAK",
}

# Books we always want columns for (fill empty string if not present on a given day)
FIXED_BOOK_COLUMNS = [
    "Open", "Best Odds", "Consensus",
    "bet365", "DraftKings", "BetMGM", "FanDuel", "theScore Bet",
    "BetRivers", "SugarHouse", "PartyCasino", "Fliff",
    "Caesars", "PointsBet", "Hard Rock Bet", "ESPNBet", "Fanatics",
]

# =============================================================================
# SELENIUM DRIVER  (shared, auto-cleanup)
# =============================================================================

_driver = None


def _cleanup():
    global _driver
    if _driver:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


atexit.register(_cleanup)


def get_driver(headless=True):
    global _driver
    if _driver:
        try:
            _ = _driver.title
            return _driver
        except Exception:
            _driver = None

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    for arg in [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-extensions", "--window-size=1920,1080",
    ]:
        opts.add_argument(arg)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    _driver = webdriver.Chrome(options=opts)
    _driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return _driver


def quit_driver():
    global _driver
    if _driver:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def fetch_html(prop_key, target_date, headless=True):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    url = f"{PROP_URLS[prop_key]}?date={target_date}"
    driver = get_driver(headless)
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 ".odds-offers__tables-container, .odds-offer, .no-events-message")
            )
        )
    except Exception:
        pass
    time.sleep(2)
    return driver.page_source

# =============================================================================
# SHARED HTML HELPERS
# =============================================================================

def _parse_book_columns(soup):
    """Extract ordered book column names from the header row."""
    cols = []
    hdr = soup.select_one(".odds-offers-header")
    if hdr:
        for item in hdr.select(".odds-offers-header__item"):
            logo = item.select_one("img.book-logo")
            if logo:
                cols.append(logo.get("alt", "").replace("Logo for ", "").strip())
            else:
                txt = item.get_text(strip=True)
                if txt in ("Open", "Consensus"):
                    cols.append(txt)
                elif "Best Odds" in txt or item.select_one(".best-odds-badge"):
                    cols.append("Best Odds")
    if not cols:
        cols = ["Open", "Best Odds", "Consensus", "bet365", "DraftKings",
                "BetMGM", "FanDuel", "theScore Bet", "BetRivers", "SugarHouse"]
    return cols


def _parse_matchups(soup):
    """Return list of (away_slug, home_slug) from matchup links."""
    pairs = []
    for link in soup.select('a.view-matchup-link[href*="matchups"]'):
        m = re.search(r'/matchups/(.+?)-vs-(.+?)/', link.get("href", ""))
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def _slug(s):
    return SLUG_TO_ABBR.get(s, s.split("-")[-1].upper()[:3])


def _cell_line(cell):
    sp = cell.select_one("span.odds-cell__line")
    if not sp:
        return ""
    raw = sp.get_text(strip=True)
    if raw == "NL":
        return ""
    return re.sub(r'^[YN]\s*', '', raw).strip() or raw

# =============================================================================
# PROP-SPECIFIC PARSERS
# =============================================================================

# ── YRFI / NRFI ──────────────────────────────────────────────────────────────

def _yrfi_headers():
    h = ["Date", "Matchup", "Away", "Home", "Away_Score", "Home_Score"]
    for b in FIXED_BOOK_COLUMNS:
        h += [f"{b}_YRFI", f"{b}_NRFI"]
    return h


def _yrfi_row(g):
    r = [g["date"], g["matchup"], g["away"], g["home"],
         g.get("away_score", ""), g.get("home_score", "")]
    for b in FIXED_BOOK_COLUMNS:
        o = g.get("books", {}).get(b, {})
        r += [o.get("yes", ""), o.get("no", "")]
    return r


def parse_yrfi(html, target_date):
    soup = BeautifulSoup(html, "html.parser")
    book_columns = _parse_book_columns(soup)
    matchups = _parse_matchups(soup)
    games = []

    for idx, offer in enumerate(soup.select("div.odds-offer")):
        # Teams: prefer /teams/ links, fall back to matchup slug
        tl = [a for a in offer.select("a[href]") if "/teams/" in a.get("href", "")]
        if len(tl) >= 2:
            abbrs = [a.get_text(strip=True) for a in tl]
            if idx < len(matchups):
                a_s, h_s = matchups[idx]
                away, home = SLUG_TO_ABBR.get(a_s, abbrs[1]), SLUG_TO_ABBR.get(h_s, abbrs[0])
            else:
                away, home = abbrs[1], abbrs[0]
        elif idx < len(matchups):
            a_s, h_s = matchups[idx]
            away, home = _slug(a_s), _slug(h_s)
        else:
            continue

        scores = [s.get_text(strip=True) for s in offer.select("span.odds-offer-label__score")]
        away_score = scores[1] if len(scores) >= 2 else ""
        home_score = scores[0] if len(scores) >= 1 else ""

        items = [i for i in offer.select("div.odds-offer__item")
                 if "odds-offer__item--first" not in (i.get("class") or [])]

        books = {}
        for ci, div in enumerate(items):
            if ci >= len(book_columns):
                break
            cells = div.select("button.odds-cell, div.odds-cell")
            yes_val = _cell_line(cells[0]) if len(cells) > 0 else ""
            no_val  = _cell_line(cells[1]) if len(cells) > 1 else ""
            books[book_columns[ci]] = {"yes": yes_val, "no": no_val}

        games.append({
            "date": target_date,
            "away": away, "home": home,
            "matchup": f"{away} @ {home}",
            "away_score": away_score, "home_score": home_score,
            "books": books,
        })
    return games


# ── TEAM TO SCORE FIRST ──────────────────────────────────────────────────────
# Structure confirmed from live HTML: 1 offer per game, 2 cells per book item
# cell[0] = away team odds, cell[1] = home team odds

def _scorefirst_headers():
    h = ["Date", "Matchup", "Away", "Home"]
    for b in FIXED_BOOK_COLUMNS:
        h += [f"{b}_Away", f"{b}_Home"]
    return h


def _scorefirst_row(g):
    r = [g["date"], g["matchup"], g["away"], g["home"]]
    for b in FIXED_BOOK_COLUMNS:
        o = g.get("books", {}).get(b, {})
        r += [o.get("away", ""), o.get("home", "")]
    return r


def parse_scorefirst(html, target_date):
    soup = BeautifulSoup(html, "html.parser")
    book_columns = _parse_book_columns(soup)
    matchups = _parse_matchups(soup)
    games = []

    for idx, offer in enumerate(soup.select("div.odds-offer")):
        if idx >= len(matchups):
            continue
        a_s, h_s = matchups[idx]
        away, home = _slug(a_s), _slug(h_s)

        items = [i for i in offer.select("div.odds-offer__item")
                 if "odds-offer__item--first" not in (i.get("class") or [])]

        books = {}
        for ci, div in enumerate(items):
            if ci >= len(book_columns):
                break
            cells = div.select("button.odds-cell, div.odds-cell")
            away_val = _cell_line(cells[0]) if len(cells) > 0 else ""
            home_val = _cell_line(cells[1]) if len(cells) > 1 else ""
            books[book_columns[ci]] = {"away": away_val, "home": home_val}

        games.append({
            "date": target_date,
            "away": away, "home": home,
            "matchup": f"{away} @ {home}",
            "books": books,
        })
    return games


# ── TEAM TOTAL RUNS ──────────────────────────────────────────────────────────
# Assumed structure: 2 cells per book item = [over_odds, under_odds]
# Each offer = one team's total (so 2 offers per game: away then home)
# VERIFY: if you see garbled data, dump raw HTML and check cell structure.

def _totals_headers():
    h = ["Date", "Matchup", "Away", "Home", "Team", "Line"]
    for b in FIXED_BOOK_COLUMNS:
        h += [f"{b}_Over", f"{b}_Under"]
    return h


def _totals_row(g):
    r = [g["date"], g["matchup"], g["away"], g["home"], g["team"], g.get("line", "")]
    for b in FIXED_BOOK_COLUMNS:
        o = g.get("books", {}).get(b, {})
        r += [o.get("over", ""), o.get("under", "")]
    return r


def parse_totals(html, target_date):
    """
    Team totals: BettingPros shows one offer per team (away then home),
    paired via matchup links. Each book item has 2 cells: over / under.
    The run line is embedded in the label or odds-cell__value span.
    """
    soup = BeautifulSoup(html, "html.parser")
    book_columns = _parse_book_columns(soup)
    matchups = _parse_matchups(soup)
    games = []

    offers = soup.select("div.odds-offer")

    # Pair offers 2-at-a-time (away, home) per matchup
    # If the page shows one offer per game (combined), fall back to scorefirst logic
    # Detect: if len(offers) == len(matchups), it's combined; if 2x, it's split
    split_mode = len(offers) == len(matchups) * 2

    for idx, offer in enumerate(offers):
        if split_mode:
            matchup_idx = idx // 2
            is_home = (idx % 2 == 1)
        else:
            matchup_idx = idx
            is_home = False  # combined mode handled below

        if matchup_idx >= len(matchups):
            continue

        a_s, h_s = matchups[matchup_idx]
        away, home = _slug(a_s), _slug(h_s)
        team = home if (split_mode and is_home) else away

        items = [i for i in offer.select("div.odds-offer__item")
                 if "odds-offer__item--first" not in (i.get("class") or [])]

        # Try to extract the run line from the label area
        line = ""
        label_el = offer.select_one(".odds-offer-label-market")
        if label_el:
            m = re.search(r'(\d+\.?\d*)', label_el.get_text())
            if m:
                line = m.group(1)

        books = {}
        for ci, div in enumerate(items):
            if ci >= len(book_columns):
                break
            cells = div.select("button.odds-cell, div.odds-cell")

            if split_mode or len(cells) == 2:
                # Two cells: over / under
                over_val  = _cell_line(cells[0]) if len(cells) > 0 else ""
                under_val = _cell_line(cells[1]) if len(cells) > 1 else ""
                books[book_columns[ci]] = {"over": over_val, "under": under_val}
            else:
                # Combined single-cell fallback
                books[book_columns[ci]] = {"over": _cell_line(cells[0]) if cells else "", "under": ""}

        if split_mode:
            games.append({
                "date": target_date,
                "away": away, "home": home,
                "matchup": f"{away} @ {home}",
                "team": team, "line": line,
                "books": books,
            })
        else:
            # Combined mode: emit two rows (away + home) from the same offer
            # cell[0]=away_over, cell[1]=away_under, cell[2]=home_over, cell[3]=home_under
            # This is speculative — verify against real markup
            for team_key, offset in [(away, 0), (home, 2)]:
                t_books = {}
                for ci, div in enumerate(items):
                    if ci >= len(book_columns):
                        break
                    cells = div.select("button.odds-cell, div.odds-cell")
                    over_val  = _cell_line(cells[offset])     if len(cells) > offset     else ""
                    under_val = _cell_line(cells[offset + 1]) if len(cells) > offset + 1 else ""
                    t_books[book_columns[ci]] = {"over": over_val, "under": under_val}
                games.append({
                    "date": target_date,
                    "away": away, "home": home,
                    "matchup": f"{away} @ {home}",
                    "team": team_key, "line": line,
                    "books": t_books,
                })

    return games

# =============================================================================
# PROP REGISTRY  (maps key → parser, headers, row builder)
# =============================================================================

PROPS = {
    "yrfi": {
        "parse":   parse_yrfi,
        "headers": _yrfi_headers,
        "row":     _yrfi_row,
    },
    "scorefirst": {
        "parse":   parse_scorefirst,
        "headers": _scorefirst_headers,
        "row":     _scorefirst_row,
    },
    "totals": {
        "parse":   parse_totals,
        "headers": _totals_headers,
        "row":     _totals_row,
    },
}

# =============================================================================
# CSV I/O
# =============================================================================

def _load_existing_dates(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return set()
    return set(pd.read_csv(path, usecols=["Date"])["Date"].astype(str).unique())


def _ensure_csv(path, prop_key):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(PROPS[prop_key]["headers"]())


def append_games(path, prop_key, games):
    row_fn = PROPS[prop_key]["row"]
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for g in games:
            w.writerow(row_fn(g))

# =============================================================================
# DATE UTILITIES
# =============================================================================

def _date_range(start, end):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end,   "%Y-%m-%d").date()
    while d <= e:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def _in_season(ds):
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    bounds = MLB_SEASON_RANGES.get(d.year)
    if not bounds:
        return True
    lo = datetime.strptime(bounds[0], "%Y-%m-%d").date()
    hi = datetime.strptime(bounds[1], "%Y-%m-%d").date()
    return lo <= d <= hi

# =============================================================================
# BACKFILL
# =============================================================================

def run_backfill(prop_key, csv_path=None, start=BACKFILL_START, end=BACKFILL_END,
                 delay=DELAY, headless=True):
    csv_path = csv_path or ODDS_CSV[prop_key]
    end = min(end, date.today().strftime("%Y-%m-%d"))
    all_dates = [d for d in _date_range(start, end) if _in_season(d)]
    existing  = _load_existing_dates(csv_path)
    todo      = [d for d in all_dates if d not in existing]

    print(f"[{prop_key}] BACKFILL {start} → {end}  |  "
          f"{len(all_dates)} in-season  |  {len(todo)} remaining")

    if not todo:
        print(f"[{prop_key}] Nothing to scrape — all dates covered.")
        return

    _ensure_csv(csv_path, prop_key)

    gc = ec = 0
    it = tqdm(todo, desc=f"Backfill {prop_key}", unit="day") if HAS_TQDM else todo

    try:
        for d in it:
            if HAS_TQDM:
                it.set_postfix(date=d, games=gc, err=ec)
            try:
                html  = fetch_html(prop_key, d, headless=headless)
                games = PROPS[prop_key]["parse"](html, d)
            except Exception as ex:
                print(f"  ERROR {d}: {ex}")
                ec += 1
                time.sleep(delay * 3)
                try:
                    get_driver(headless)
                except Exception:
                    quit_driver()
                continue

            if games:
                append_games(csv_path, prop_key, games)
                gc += len(games)

            time.sleep(delay * random.uniform(0.5, 1.5))

    except KeyboardInterrupt:
        print(f"\n[{prop_key}] Interrupted — re-run to resume (completed dates are skipped)")
    finally:
        quit_driver()

    print(f"[{prop_key}] Done — {gc} game-rows saved, {ec} errors  →  {csv_path}")


# =============================================================================
# DAILY SCRAPE
# =============================================================================

def run_daily(prop_key, csv_path=None, target_date=None, headless=True):
    csv_path    = csv_path or ODDS_CSV[prop_key]
    target_date = target_date or date.today().strftime("%Y-%m-%d")
    print(f"[{prop_key}] DAILY: scraping {target_date}")

    try:
        html  = fetch_html(prop_key, target_date, headless=headless)
        games = PROPS[prop_key]["parse"](html, target_date)
    except Exception as ex:
        print(f"[{prop_key}] ERROR: {ex}")
        quit_driver()
        return
    finally:
        quit_driver()

    if games:
        _ensure_csv(csv_path, prop_key)
        append_games(csv_path, prop_key, games)
        print(f"[{prop_key}] {len(games)} row(s) appended  →  {csv_path}")
        for g in games:
            print(f"   {g['matchup']}")
    else:
        print(f"[{prop_key}] No games found for {target_date}")


# =============================================================================
# CLI
# =============================================================================

def _prop_keys(arg):
    if arg == "all":
        return list(PROPS.keys())
    if arg in PROPS:
        return [arg]
    print(f"Unknown prop '{arg}'. Choose: {', '.join(PROPS)} or 'all'")
    sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    mode = args[0].lower()

    if mode == "backfill":
        prop_arg = args[1] if len(args) > 1 else "all"
        for key in _prop_keys(prop_arg):
            run_backfill(key)

    elif mode == "daily":
        prop_arg  = args[1] if len(args) > 1 else "all"
        date_arg  = args[2] if len(args) > 2 else None
        for key in _prop_keys(prop_arg):
            run_daily(key, target_date=date_arg)

    else:
        print(f"Unknown mode '{mode}'. Use 'backfill' or 'daily'.")
        sys.exit(1)
