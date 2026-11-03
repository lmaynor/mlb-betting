"""soccer.data.ingest -- the free ($0) data sources for Phase 1, as deterministic cached loaders.

Sources (licenses in docs/DATA_LICENSES.md):
  * football-data.co.uk season CSVs: results + shots + Pinnacle opening/closing 1X2 and 2.5-totals odds. This is
    the backtest's ground truth: closing columns PSCH/PSCD/PSCA and PC>2.5 / PC<2.5, opening PSH/PSD/PSA and
    P>2.5 / P<2.5, market max/average MaxCH.. / AvgCH...
  * Understat league pages: match-level xG for the Big 5 DOMESTIC leagues only (xg_variant "understat_shot_v1",
    the ONE xG definition this vertical uses). Understat has no UCL page (understat.com/league/UCL/{year} 404s);
    UCL ratings come from the pooled domestic fit (soccer.game.run_daily). The league matchesData payload carries
    xG and goals only; npxG / PPDA are match-page and team-season level on Understat, so they are returned as
    None, never scraped per match here.
  * ClubElo daily CSV API: team Elo ratings, the ratings prior and sanity anchor.

Every loader takes an injectable `fetch(url) -> str` so tests run 100% offline against fixtures; the default fetch
is requests with a browser UA and a hard timeout. Club identity goes through the Reep bridge
(soccer.data.reep_register) and unresolved clubs are SKIPPED with a warning, never guessed. Raw payloads are
cached to storage under Soccer/ best-effort (a cache failure never fails the load).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date, datetime

from soccer.config import SOCCER_LEAGUES
from soccer.data.reep_register import resolve_club

log = logging.getLogger("soccer.ingest")

_UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-soccer-ingest/1.0)"}
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"
UNDERSTAT_BASE = "https://understat.com/league"
CLUBELO_BASE = "http://api.clubelo.com"


def _default_fetch(url: str) -> str:
    import requests
    resp = requests.get(url, headers=_UA, timeout=30)
    resp.raise_for_status()
    return resp.text


def _cache_put(key: str, text: str) -> None:
    try:
        from mlb_core import storage
        storage.write_bytes(text.encode("utf-8"), key)
    except Exception:  # noqa: BLE001 -- cache is best-effort by design
        log.debug("soccer ingest cache write failed for %s", key)


def _f(value) -> float | None:
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _i(value) -> int | None:
    v = _f(value)
    return int(v) if v is not None else None


def season_code(season) -> str:
    """football-data.co.uk season stem: a start year (2021, '2021', 2024) -> '2122' / '2425'; an already-coded
    stem ('2122', '2425') passes through. A 4-digit string is a start year only when it reads as one (19xx/20xx);
    a coded stem's first pair is a year's last two digits, which never starts with 19/20 for our seasons."""
    if isinstance(season, int):
        return f"{season % 100:02d}{(season + 1) % 100:02d}"
    s = str(season).strip()
    if len(s) == 4 and s.isdigit() and s[:2] in ("19", "20"):
        y = int(s)
        return f"{y % 100:02d}{(y + 1) % 100:02d}"
    return s


def _parse_fd_date(raw: str) -> date | None:
    raw = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def fetch_football_data_csv(league: str, season, fetch=None) -> list[dict]:
    """One football-data.co.uk season CSV -> normalized match dicts (played matches with a result only).

    Each match: {league, date, home, away (canonical codes), home_goals, away_goals, hs, as, hst, ast,
    odds: {psh, psd, psa, psch, pscd, psca, maxch, maxcd, maxca, avgch, avgcd, avgca,
           p_over25, p_under25 (Pinnacle OPENING 2.5), pc_over25, pc_under25 (Pinnacle CLOSING 2.5)}}.
    Odds are DECIMAL prices as published. Rows whose clubs do not resolve through the Reep bridge are skipped.
    """
    cfg = SOCCER_LEAGUES[league]
    code = season_code(season)
    url = f"{FOOTBALL_DATA_BASE}/{code}/{cfg['fd_code']}.csv"
    text = (fetch or _default_fetch)(url)
    _cache_put(f"Soccer/football_data/{league}_{code}.csv", text)
    return parse_football_data_csv(text, league)


def parse_football_data_csv(text: str, league: str) -> list[dict]:
    """football-data.co.uk CSV text -> the normalized match dicts of fetch_football_data_csv (shared so the
    backtest's local files and the live pull can never drift apart)."""
    code = ""
    out: list[dict] = []
    skipped = 0
    for row in csv.DictReader(io.StringIO(text)):
        home = resolve_club(row.get("HomeTeam"), league=league, provider="football_data")
        away = resolve_club(row.get("AwayTeam"), league=league, provider="football_data")
        day = _parse_fd_date(row.get("Date", ""))
        hg, ag = _i(row.get("FTHG")), _i(row.get("FTAG"))
        if not home or not away or day is None or hg is None or ag is None:
            skipped += 1
            continue
        out.append({
            "league": league, "date": day, "home": home, "away": away,
            "home_goals": hg, "away_goals": ag,
            "hs": _i(row.get("HS")), "as": _i(row.get("AS")),
            "hst": _i(row.get("HST")), "ast": _i(row.get("AST")),
            "odds": {
                "psh": _f(row.get("PSH")), "psd": _f(row.get("PSD")), "psa": _f(row.get("PSA")),
                "psch": _f(row.get("PSCH")), "pscd": _f(row.get("PSCD")), "psca": _f(row.get("PSCA")),
                "maxch": _f(row.get("MaxCH")), "maxcd": _f(row.get("MaxCD")), "maxca": _f(row.get("MaxCA")),
                "avgch": _f(row.get("AvgCH")), "avgcd": _f(row.get("AvgCD")), "avgca": _f(row.get("AvgCA")),
                "p_over25": _f(row.get("P>2.5")), "p_under25": _f(row.get("P<2.5")),
                "pc_over25": _f(row.get("PC>2.5")), "pc_under25": _f(row.get("PC<2.5")),
            },
        })
    if skipped:
        log.warning("football-data %s %s: skipped %d rows (unresolved club or missing result)", league, code, skipped)
    out.sort(key=lambda m: m["date"])
    return out


_MATCHES_DATA_RE = re.compile(r"matchesData\s*=\s*JSON\.parse\('(.*?)'\)", re.DOTALL)


def _decode_understat_payload(html: str) -> list:
    m = _MATCHES_DATA_RE.search(html)
    if not m:
        raise ValueError("understat page has no matchesData payload")
    raw = m.group(1)
    # The payload is a JS single-quoted string carrying JSON with \xNN / \uNNNN escapes.
    decoded = raw.encode("utf-8").decode("unicode_escape")
    return json.loads(decoded)


def fetch_understat_matches(league: str, season, fetch=None) -> list[dict]:
    """One Understat league season -> match dicts: {league, date, datetime, home, away, is_result, home_goals,
    away_goals, xg_home, xg_away, npxg_home: None, npxg_away: None, ppda_home: None, ppda_away: None}.

    `season` is the season START year (2025 = 2025/26), Understat's own convention. Unplayed fixtures come back
    with is_result False and no goals/xG; run_daily uses them as the fixture list. xG here is the metric-card
    variant "understat_shot_v1" (soccer.config.XG_VARIANT).
    """
    cfg = SOCCER_LEAGUES[league]
    if cfg["understat_league"] is None:
        raise ValueError(f"league {league!r} has no Understat page (understat_league is None); "
                         "its ratings come from the pooled domestic fit, not a per-league pull")
    year = str(season)[:4]
    url = f"{UNDERSTAT_BASE}/{cfg['understat_league']}/{year}"
    html = (fetch or _default_fetch)(url)
    _cache_put(f"Soccer/understat/{league}_{year}.html", html)
    out: list[dict] = []
    skipped = 0
    for m in _decode_understat_payload(html):
        home = resolve_club((m.get("h") or {}).get("title"), league=league, provider="understat")
        away = resolve_club((m.get("a") or {}).get("title"), league=league, provider="understat")
        try:
            dt = datetime.strptime(str(m.get("datetime")), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            dt = None
        if not home or not away or dt is None:
            skipped += 1
            continue
        goals, xg = m.get("goals") or {}, m.get("xG") or {}
        is_result = bool(m.get("isResult"))
        out.append({
            "league": league, "date": dt.date(), "datetime": dt, "home": home, "away": away,
            "is_result": is_result,
            "home_goals": _i(goals.get("h")) if is_result else None,
            "away_goals": _i(goals.get("a")) if is_result else None,
            "xg_home": _f(xg.get("h")) if is_result else None,
            "xg_away": _f(xg.get("a")) if is_result else None,
            "npxg_home": None, "npxg_away": None, "ppda_home": None, "ppda_away": None,
        })
    if skipped:
        log.warning("understat %s %s: skipped %d matches (unresolved club or bad datetime)", league, year, skipped)
    out.sort(key=lambda m: m["datetime"])
    return out


def fetch_clubelo_ratings(on: date | None = None, fetch=None) -> dict[str, float]:
    """ClubElo daily ratings -> {canonical club code: Elo}. Clubs the register does not know are skipped."""
    day = (on or date.today()).isoformat()
    text = (fetch or _default_fetch)(f"{CLUBELO_BASE}/{day}")
    _cache_put(f"Soccer/clubelo/{day}.csv", text)
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        code = resolve_club(row.get("Club"), provider="clubelo")
        elo = _f(row.get("Elo"))
        if code and elo is not None:
            out[code] = elo
    return out


def elo_probs(elo_home: float, elo_away: float, hfa_elo: float = 60.0) -> tuple[float, float, float]:
    """ClubElo difference -> a (p_home, p_draw, p_away) PRIOR. This is an anchor / sanity reference only, never
    the priced model: win expectancy is the standard Elo logistic on (home - away + HFA), and the draw share is a
    documented heuristic that starts at 0.30 for an even match and decays with the Elo gap."""
    diff = float(elo_home) - float(elo_away) + float(hfa_elo)
    w = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    p_draw = min(0.30, max(0.14, 0.30 - 0.00045 * abs(diff)))
    return w * (1.0 - p_draw), p_draw, (1.0 - w) * (1.0 - p_draw)
