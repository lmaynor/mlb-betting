"""
Shared DraftKings CDP scraper.
All systems import from here. Each system passes its own market URL.
"""
import json
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# -- Canonical team name map ---------------------------------------------------
DK_NAME_TO_ABBR: dict = {
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC":  "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD":  "SD",  "SF":  "SF",
    "SEA": "SEA", "STL": "STL", "TB":  "TB",  "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSH",
    "A's": "OAK", "AZ": "ARI", "ATH": "OAK",
    "KAN": "KC",  "ROY": "KC", "WAS": "WSH", "MIN Twins": "MIN",
    "Athletics":    "OAK", "Diamondbacks": "ARI", "Braves":    "ATL",
    "Orioles":      "BAL", "Red Sox":      "BOS", "Cubs":      "CHC",
    "White Sox":    "CWS", "Reds":         "CIN", "Guardians": "CLE",
    "Rockies":      "COL", "Tigers":       "DET", "Astros":    "HOU",
    "Royals":       "KC",  "Angels":       "LAA", "Dodgers":   "LAD",
    "Marlins":      "MIA", "Brewers":      "MIL", "Twins":     "MIN",
    "Mets":         "NYM", "Yankees":      "NYY", "Phillies":  "PHI",
    "Pirates":      "PIT", "Padres":       "SD",  "Giants":    "SF",
    "Mariners":     "SEA", "Cardinals":    "STL", "Rays":      "TB",
    "Rangers":      "TEX", "Blue Jays":    "TOR", "Nationals": "WSH",
}

TEAM_NAME_TO_ABBREV: dict = {
    "Arizona Diamondbacks":  "ARI", "Atlanta Braves":       "ATL",
    "Baltimore Orioles":     "BAL", "Boston Red Sox":       "BOS",
    "Chicago Cubs":          "CHC", "Chicago White Sox":    "CWS",
    "Cincinnati Reds":       "CIN", "Cleveland Guardians":  "CLE",
    "Cleveland Indians":     "CLE", "Colorado Rockies":     "COL",
    "Detroit Tigers":        "DET", "Houston Astros":       "HOU",
    "Kansas City Royals":    "KC",  "Los Angeles Angels":   "LAA",
    "Los Angeles Dodgers":   "LAD", "Miami Marlins":        "MIA",
    "Milwaukee Brewers":     "MIL", "Minnesota Twins":      "MIN",
    "New York Mets":         "NYM", "New York Yankees":     "NYY",
    "Oakland Athletics":     "OAK", "Athletics":            "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates":   "PIT",
    "San Diego Padres":      "SD",  "San Francisco Giants": "SF",
    "Seattle Mariners":      "SEA", "St. Louis Cardinals":  "STL",
    "Tampa Bay Rays":        "TB",  "Texas Rangers":        "TEX",
    "Toronto Blue Jays":     "TOR", "Washington Nationals": "WSH",
}


def resolve_team(name: str) -> Optional[str]:
    """Resolve any DK team name/fragment to standard 3-letter abbreviation."""
    if not name:
        return None
    if name in DK_NAME_TO_ABBR:
        return DK_NAME_TO_ABBR[name]
    for fragment, abbr in DK_NAME_TO_ABBR.items():
        if fragment and fragment in name:
            return abbr
    return None


def dk_to_int(s) -> Optional[int]:
    """Parse DK American odds string to int. Handles Unicode minus variants."""
    if not s:
        return None
    s = (
        str(s)
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .strip()
    )
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _init_driver():
    """Headless Chrome with performance logging. Caller must call driver.quit()."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=opts)


def fetch_dk_payloads(
    url: str,
    wait: float = 5.0,
    extra_wait: float = 0.0,
    tab_xpath: Optional[str] = None,
) -> list:
    """
    Fetch DK sportscontent API payloads for a given market URL via CDP.

    Args:
        url:        Full DK market URL.
        wait:       Seconds to wait after page load.
        extra_wait: Additional wait after tab click.
        tab_xpath:  Optional XPath for a tab to click before capture.

    Returns:
        List of raw payload dicts. Empty list on error.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    driver = _init_driver()
    payloads = []
    try:
        driver.get(url)
        time.sleep(wait)
        if tab_xpath:
            try:
                tab = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
                tab.click()
                time.sleep(extra_wait or 4.0)
            except Exception as e:
                logger.warning(f"Tab click failed: {e}")
        for entry in driver.get_log("performance"):
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") != "Network.responseReceived":
                    continue
                resp_url = msg["params"]["response"].get("url", "")
                if "sportscontent" not in resp_url and "sportsbook" not in resp_url:
                    continue
                req_id = msg["params"]["requestId"]
                try:
                    body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": req_id})
                    parsed = json.loads(body.get("body", ""))
                    if parsed.get("events") or parsed.get("eventGroup") or parsed.get("markets"):
                        payloads.append(parsed)
                except Exception:
                    pass
            except Exception:
                pass
    except Exception as e:
        logger.error(f"fetch_dk_payloads failed for {url}: {e}")
    finally:
        driver.quit()
    return payloads
