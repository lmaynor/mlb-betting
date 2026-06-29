"""
Patient BettingPros MLB home-run prop scraper.

This is built for one-time backfills where reliability matters more than speed.
BettingPros virtualizes the player list, so a single driver.page_source only
contains the rows currently mounted in the DOM. The scraper scrolls slowly,
parses every visible batch, dedupes by player/matchup, and checkpoints after
each completed date so interrupted runs can resume.

Usage:
  python scripts/bettingpros_hr_backfill.py daily 2026-06-28
  python scripts/bettingpros_hr_backfill.py backfill --start 2024-04-01 --end 2026-06-28

Optional:
  --headed              show Chrome while scraping
  --delay 0.9           seconds between scroll steps
  --stable-rounds 12    more rounds means more patient near page bottom
  --output data/bettingpros_hr_odds.csv
"""

from __future__ import annotations

import argparse
import atexit
import csv
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - runtime environment guard
    raise SystemExit(
        "Missing dependency: beautifulsoup4. Install with: pip install beautifulsoup4"
    ) from exc

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError as exc:  # pragma: no cover - runtime environment guard
    raise SystemExit(
        "Missing dependency: selenium. Install with: pip install selenium"
    ) from exc


URL = "https://www.bettingpros.com/mlb/odds/player-props/homeruns/"
DEFAULT_OUTPUT = Path("data/bettingpros_hr_odds.csv")

FIXED_BOOK_COLUMNS = [
    "Open",
    "Best Odds",
    "Consensus",
    "bet365",
    "DraftKings",
    "BetMGM",
    "FanDuel",
    "theScore Bet",
    "BetRivers",
    "SugarHouse",
    "PartyCasino",
    "Fliff",
    "Caesars",
    "PointsBet",
    "Hard Rock Bet",
    "ESPNBet",
    "Fanatics",
]

MLB_SEASON_RANGES = {
    2024: ("2024-03-20", "2024-11-03"),
    2025: ("2025-03-20", "2025-11-03"),
    2026: ("2026-03-20", "2026-11-03"),
    2027: ("2027-03-20", "2027-11-03"),
}

_driver = None


@dataclass
class ScrapeStats:
    rows: int
    scrolls: int
    max_visible_batch: int
    scroll_target: str


def _cleanup() -> None:
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


atexit.register(_cleanup)


def get_driver(headless: bool = True):
    global _driver
    if _driver is not None:
        try:
            _ = _driver.title
            return _driver
        except Exception:
            _driver = None

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    for arg in [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--window-size=1920,1400",
    ]:
        opts.add_argument(arg)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})

    _driver = webdriver.Chrome(options=opts)
    _driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return _driver


def quit_driver() -> None:
    _cleanup()


def headers() -> list[str]:
    cols = ["Date", "Player", "Player_Page", "Matchup", "Team", "Position", "Line"]
    for book in FIXED_BOOK_COLUMNS:
        cols.extend([f"{book}_Over", f"{book}_Under"])
    return cols


def _parse_book_columns(soup: BeautifulSoup) -> list[str]:
    cols: list[str] = []
    header = soup.select_one(".odds-offers-header")
    if header:
        for item in header.select(".odds-offers-header__item"):
            logo = item.select_one("img.book-logo")
            if logo:
                name = logo.get("alt", "").replace("Logo for ", "").strip()
                if name:
                    cols.append(name)
                    continue
            txt = item.get_text(" ", strip=True)
            if txt in ("Open", "Consensus"):
                cols.append(txt)
            elif "Best Odds" in txt or item.select_one(".best-odds-badge"):
                cols.append("Best Odds")

    return cols or FIXED_BOOK_COLUMNS[:]


def _clean_odds(text: str) -> str:
    text = text.strip()
    if not text or text == "NL":
        return ""
    text = text.strip("()")
    return text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")


def _cell_parts(cell) -> tuple[str, str]:
    line_el = cell.select_one(".odds-cell__line")
    cost_el = cell.select_one(".odds-cell__cost")
    line = line_el.get_text(" ", strip=True) if line_el else ""
    cost = _clean_odds(cost_el.get_text(" ", strip=True) if cost_el else "")
    return line, cost


def _split_team_position(text: str) -> tuple[str, str]:
    if " - " not in text:
        return text.strip(), ""
    team, pos = text.split(" - ", 1)
    return team.strip(), pos.strip()


def parse_homeruns(html: str, target_date: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    book_columns = _parse_book_columns(soup)
    out: list[dict[str, str]] = []

    for offer in soup.select("div.odds-offer"):
        player_el = offer.select_one(".odds-player__heading")
        if not player_el:
            continue

        player = player_el.get_text(" ", strip=True)
        player_page = player_el.get("href", "")
        matchup_el = offer.select_one(".odds-player__matchup-tag")
        subheading_el = offer.select_one(".odds-player__subheading")
        matchup = matchup_el.get_text(" ", strip=True) if matchup_el else ""
        team, position = _split_team_position(
            subheading_el.get_text(" ", strip=True) if subheading_el else ""
        )

        row = {
            "Date": target_date,
            "Player": player,
            "Player_Page": player_page,
            "Matchup": matchup,
            "Team": team,
            "Position": position,
            "Line": "0.5",
        }
        for book in FIXED_BOOK_COLUMNS:
            row[f"{book}_Over"] = ""
            row[f"{book}_Under"] = ""

        items = [
            item
            for item in offer.select("div.odds-offer__item")
            if "odds-offer__item--first" not in (item.get("class") or [])
        ]

        for idx, item in enumerate(items):
            if idx >= len(book_columns):
                break
            book = book_columns[idx]
            if book not in FIXED_BOOK_COLUMNS:
                continue
            cells = item.select("button.odds-cell, div.odds-cell")
            for cell in cells[:2]:
                line, cost = _cell_parts(cell)
                if not cost:
                    continue
                if line.startswith("O"):
                    row[f"{book}_Over"] = cost
                    line_match = re.search(r"(\d+(?:\.\d+)?)", line)
                    if line_match:
                        row["Line"] = line_match.group(1)
                elif line.startswith("U"):
                    row[f"{book}_Under"] = cost

        out.append(row)

    return out


def _scroll_odds_view(driver) -> dict:
    """Scroll the element BettingPros is most likely virtualizing.

    The page sometimes keeps the body still while the odds table lives inside a
    nested overflow container. We score scrollable elements by whether they
    contain mounted odds rows, then dispatch both scroll and wheel events so Vue
    listeners get a normal browser-shaped signal.
    """
    return driver.execute_script(
        """
        const amount = Math.max(500, Math.floor(window.innerHeight * 0.78));
        const all = Array.from(document.querySelectorAll('body, body *'));
        const candidates = all
          .filter((el) => (el.scrollHeight - el.clientHeight) > 40)
          .map((el) => {
            const cls = typeof el.className === 'string' ? el.className : '';
            const oddsRows = el.querySelectorAll ? el.querySelectorAll('.odds-offer').length : 0;
            const oddsClass = /odds|grouped-items|layout-wrapper/.test(cls) ? 250 : 0;
            const score = oddsRows * 1000 + oddsClass + (el.scrollHeight - el.clientHeight);
            return { el, score, oddsRows, cls };
          })
          .sort((a, b) => b.score - a.score);

        let picked = candidates.length ? candidates[0].el : document.scrollingElement;
        if (!picked) picked = document.scrollingElement || document.body;

        const before = picked.scrollTop || window.scrollY || 0;
        if (picked === document.body || picked === document.documentElement || picked === document.scrollingElement) {
          window.scrollBy(0, amount);
        } else {
          picked.scrollTop = Math.min(picked.scrollTop + amount, picked.scrollHeight);
          picked.dispatchEvent(new Event('scroll', { bubbles: true }));
        }

        const wheelTarget = document.querySelector('.odds-offer') || picked;
        wheelTarget.dispatchEvent(new WheelEvent('wheel', {
          bubbles: true,
          cancelable: true,
          deltaY: amount,
          clientX: Math.floor(window.innerWidth / 2),
          clientY: Math.floor(window.innerHeight / 2),
        }));
        window.dispatchEvent(new Event('scroll'));

        const after = picked.scrollTop || window.scrollY || 0;
        const cls = typeof picked.className === 'string' ? picked.className : '';
        return {
          target: picked.tagName + (cls ? '.' + cls.trim().replace(/\\s+/g, '.') : ''),
          before,
          after,
          clientHeight: picked.clientHeight || window.innerHeight,
          scrollHeight: picked.scrollHeight || document.scrollingElement.scrollHeight,
          windowY: window.scrollY,
          windowHeight: window.innerHeight,
          docHeight: document.scrollingElement.scrollHeight,
        };
        """
    )


def debug_date(
    target_date: str,
    *,
    headless: bool = False,
    delay: float = 1.0,
    steps: int = 8,
) -> None:
    driver = get_driver(headless=headless)
    driver.get(f"{URL}?date={target_date}")

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".odds-offer, .no-events-message"))
        )
    except Exception:
        time.sleep(5)

    print(f"url={driver.current_url}")
    for step in range(steps + 1):
        rows = parse_homeruns(driver.page_source, target_date)
        players = [row["Player"] for row in rows[:10]]
        metrics = driver.execute_script(
            """
            const scrollables = Array.from(document.querySelectorAll('body, body *'))
              .filter((el) => (el.scrollHeight - el.clientHeight) > 40)
              .map((el) => {
                const cls = typeof el.className === 'string' ? el.className : '';
                return {
                  tag: el.tagName,
                  cls,
                  scrollTop: el.scrollTop || 0,
                  clientHeight: el.clientHeight || 0,
                  scrollHeight: el.scrollHeight || 0,
                  oddsRows: el.querySelectorAll ? el.querySelectorAll('.odds-offer').length : 0,
                  text: (el.innerText || '').slice(0, 90).replace(/\\s+/g, ' '),
                };
              })
              .sort((a, b) => {
                const as = a.oddsRows * 1000 + (a.scrollHeight - a.clientHeight);
                const bs = b.oddsRows * 1000 + (b.scrollHeight - b.clientHeight);
                return bs - as;
              })
              .slice(0, 10);
            const buttons = Array.from(document.querySelectorAll('button, a'))
              .map((el) => (el.innerText || el.getAttribute('aria-label') || '').trim())
              .filter(Boolean)
              .filter((txt) => /more|show|load|next|view|expand/i.test(txt))
              .slice(0, 20);
            return {
              windowY: window.scrollY,
              windowHeight: window.innerHeight,
              docHeight: document.scrollingElement.scrollHeight,
              offerCount: document.querySelectorAll('.odds-offer').length,
              playerCount: document.querySelectorAll('.odds-player__heading').length,
              scrollables,
              buttons,
            };
            """
        )
        print(
            f"step={step} rows={len(rows)} offerCount={metrics['offerCount']} "
            f"playerCount={metrics['playerCount']} windowY={metrics['windowY']} "
            f"docHeight={metrics['docHeight']}"
        )
        print(f"players={players}")
        if step == 0:
            print("scrollable candidates:")
            for item in metrics["scrollables"]:
                cls = item["cls"].strip().replace("\n", " ")[:90]
                print(
                    f"  {item['tag']}.{cls} top={item['scrollTop']} "
                    f"h={item['clientHeight']}/{item['scrollHeight']} "
                    f"oddsRows={item['oddsRows']} text={item['text']!r}"
                )
            print(f"buttons={metrics['buttons']}")

        if step < steps:
            info = _scroll_odds_view(driver)
            print(f"scroll_target={info}")
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.PAGE_DOWN)
            except Exception:
                pass
            time.sleep(delay)

    seen_urls: set[str] = set()
    try:
        for entry in driver.get_log("performance"):
            msg = json.loads(entry["message"]).get("message", {})
            if msg.get("method") not in {
                "Network.requestWillBeSent",
                "Network.responseReceived",
            }:
                continue
            params = msg.get("params", {})
            req = params.get("request") or {}
            resp = params.get("response") or {}
            url = req.get("url") or resp.get("url") or ""
            if not url or url in seen_urls:
                continue
            if any(token in url.lower() for token in ["api", "odds", "markets", "events", "props"]):
                seen_urls.add(url)
        print("network urls:")
        for url in sorted(seen_urls):
            print(f"  {url}")
    except Exception as exc:
        print(f"performance log unavailable: {exc}")


def collect_date(
    target_date: str,
    *,
    headless: bool = True,
    delay: float = 0.9,
    stable_rounds: int = 12,
    max_scrolls: int = 500,
) -> tuple[list[dict[str, str]], ScrapeStats]:
    driver = get_driver(headless=headless)
    driver.get(f"{URL}?date={target_date}")

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".odds-offer, .no-events-message"))
        )
    except Exception:
        time.sleep(5)

    rows_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    stable = 0
    last_count = 0
    max_visible_batch = 0
    last_scroll_target = ""

    for scroll_num in range(1, max_scrolls + 1):
        visible_rows = parse_homeruns(driver.page_source, target_date)
        max_visible_batch = max(max_visible_batch, len(visible_rows))
        for row in visible_rows:
            key = (row["Date"], row["Player"], row["Matchup"])
            rows_by_key[key] = row

        current_count = len(rows_by_key)
        scroll_info = _scroll_odds_view(driver)
        last_scroll_target = str(scroll_info.get("target") or "")
        target_bottom = (
            float(scroll_info.get("after") or 0)
            + float(scroll_info.get("clientHeight") or 0)
            >= float(scroll_info.get("scrollHeight") or 0) - 20
        )
        window_bottom = (
            float(scroll_info.get("windowY") or 0)
            + float(scroll_info.get("windowHeight") or 0)
            >= float(scroll_info.get("docHeight") or 0) - 20
        )
        near_bottom = target_bottom or window_bottom

        if current_count == last_count and near_bottom:
            stable += 1
        else:
            stable = 0
        if stable >= stable_rounds:
            break

        last_count = current_count
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.PAGE_DOWN)
        except Exception:
            pass
        time.sleep(delay * random.uniform(0.75, 1.35))

    rows = sorted(rows_by_key.values(), key=lambda r: (r["Matchup"], r["Team"], r["Player"]))
    stats = ScrapeStats(
        rows=len(rows),
        scrolls=scroll_num,
        max_visible_batch=max_visible_batch,
        scroll_target=last_scroll_target,
    )
    return rows, stats


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers()).writeheader()


def completed_dates(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", newline="", encoding="utf-8") as f:
        return {row["Date"] for row in csv.DictReader(f) if row.get("Date")}


def append_rows(path: Path, rows: list[dict[str, str]]) -> None:
    ensure_csv(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers(), extrasaction="ignore")
        for row in rows:
            writer.writerow(row)


def date_range(start: str, end: str):
    current = datetime.strptime(start, "%Y-%m-%d").date()
    final = datetime.strptime(end, "%Y-%m-%d").date()
    while current <= final:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def in_season(ds: str) -> bool:
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    bounds = MLB_SEASON_RANGES.get(d.year)
    if not bounds:
        return True
    lo = datetime.strptime(bounds[0], "%Y-%m-%d").date()
    hi = datetime.strptime(bounds[1], "%Y-%m-%d").date()
    return lo <= d <= hi


def run_one_date(args, target_date: str) -> int:
    rows, stats = collect_date(
        target_date,
        headless=not args.headed,
        delay=args.delay,
        stable_rounds=args.stable_rounds,
        max_scrolls=args.max_scrolls,
    )
    if rows:
        append_rows(args.output, rows)
    print(
        f"{target_date}: {stats.rows} rows, {stats.scrolls} scrolls, "
        f"max visible batch={stats.max_visible_batch}, target={stats.scroll_target}"
    )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill BettingPros MLB HR prop odds")
    parser.add_argument("mode", choices=["daily", "backfill", "debug"])
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD for daily mode")
    parser.add_argument("--start", default="2024-04-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--headed", action="store_true", help="show Chrome")
    parser.add_argument("--delay", type=float, default=0.9)
    parser.add_argument("--stable-rounds", type=int, default=12)
    parser.add_argument("--max-scrolls", type=int, default=500)
    parser.add_argument("--retry", type=int, default=2)
    parser.add_argument("--debug-steps", type=int, default=8)
    args = parser.parse_args(argv)

    if args.mode == "debug":
        if not args.date:
            parser.error("debug mode requires a date")
        debug_date(
            args.date,
            headless=not args.headed,
            delay=args.delay,
            steps=args.debug_steps,
        )
        quit_driver()
        return 0

    if args.mode == "daily":
        if not args.date:
            parser.error("daily mode requires a date")
        ensure_csv(args.output)
        run_one_date(args, args.date)
        quit_driver()
        return 0

    ensure_csv(args.output)
    done = completed_dates(args.output)
    todo = [d for d in date_range(args.start, args.end) if in_season(d) and d not in done]
    print(f"Backfill {args.start} -> {args.end}: {len(todo)} dates remaining")

    errors = 0
    for ds in todo:
        for attempt in range(1, args.retry + 2):
            try:
                run_one_date(args, ds)
                break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                errors += 1
                print(f"{ds}: attempt {attempt} failed: {exc}", file=sys.stderr)
                quit_driver()
                time.sleep(args.delay * 5 * attempt)
        else:
            print(f"{ds}: giving up after retries", file=sys.stderr)
        time.sleep(args.delay * random.uniform(2.0, 4.0))

    quit_driver()
    print(f"Done. errors={errors}, output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
