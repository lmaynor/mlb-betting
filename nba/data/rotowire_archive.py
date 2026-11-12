"""RotoWire NBA player-news archive puller (historic prose) for the minutes-restriction program.

STATUS: BUILT BUT NOT RUN. Luc's instruction (2026-11-11): "do the build but don't trigger the run yet."
This module ships DISABLED BY DEFAULT and stays that way until BOTH gates are open at run time:

  * the caller passes execute=True (the CLI's --execute flag), AND
  * the environment carries ROTOWIRE_PULL_ENABLED=1.

With either gate closed, `run_historic_pull` / `run_rss_snapshot` return {"status": "disabled"} and make
ZERO HTTP requests and ZERO storage writes -- they only build a dry-run plan. Nothing in this module
fetches at import time, and there is no __main__ entry point here (use scripts/pull_rotowire_prose.py).

Why this exists: a verification check (2026-11-11, corrected version)
proved RotoWire's per-player news archive serves dated, full-text factual blurbs
continuously back through 2021-22, so the minutes-restriction text arm is backtestable on the same span
as the structured arm. The same check is explicit that explicit pre-return minutes-cap language appeared
in 0 of 3 sampled return windows -- the corpus's value is trajectory / ramp-up language, not stated caps
-- and that the archive preserves superseded and occasionally WRONG updates raw. This puller therefore
banks everything verbatim and never cleans, reconciles, or drops a superseded update.

Access surface (measured in the check, not inferred):
  * Player page  GET https://www.rotowire.com/basketball/player/{slug}-{id}
    server-renders the latest ~6 updates with full factual text (only RotoWire's fantasy ANALYSIS is gated).
  * Archive      GET https://www.rotowire.com/basketball/ajax/get-more-updates.php
                     ?type=player&itemID={id}&lastUpdateTime={cursor}&numUpdates={n}
    -> JSON {updatesHTML, newLastUpdateTime, noMoreUpdates}; numUpdates=100 is honored. Paging is
    strictly backward from the present; there is no date jump, so one backward pass per player banks
    every season's windows at once and the pass stops once the cursor passes the player's oldest
    needed window. Case windows are sliced LOCALLY at analysis time (see `slice_window`).
  * Forward feed GET https://www.rotowire.com/rss/news.php?sport=NBA (RSS banking variant, same record
    shape, banked as dated snapshots; also disabled by default).

Timestamps: displayed blurb dates are day-granular, but the pagination cursor is timestamp-precise and
cursor order is chronological. Parsed records keep BOTH the displayed date (`published_date`) and the
precise timestamp when the fragment carries one (`published_ts`, seconds since epoch); when a fragment
carries none, `published_ts` falls back to the displayed date at 00:00 UTC so dedupe keys stay stable.

Storage (via mlb_core.storage, like the other banking code):
    NBA/news/rotowire_archive/raw/{rotowire_id}/page.html            player-page response (verbatim)
    NBA/news/rotowire_archive/raw/{rotowire_id}/ajax_{seq:04d}.json  one raw AJAX JSON response per page
    NBA/news/rotowire_archive/parsed/{rotowire_id}.jsonl             parsed blurb records, sorted by ts
    NBA/news/rotowire_archive/rss/{ET day}/rss_{HHMMSS}.xml          raw RSS snapshot (verbatim)
    NBA/news/rotowire_archive/rss/{ET day}/rss_{HHMMSS}.jsonl        parsed RSS records, same shape

Politeness: every request carries a generic desktop browser User-Agent and no
identifying headers -- no login, no cookies, no project or contact tokens. Requests are paced
>= 2.5 s apart by default (configurable; optional jitter). Estimated full default run:
~1,700-2,400 requests, ~200-280 MB raw, ~1.5 h at default pacing (see the check, section 3).

Player IDs: the puller takes an input player map (CSV or JSON: player name, rotowire_id, slug).
`build_case_set` emits the case list + the distinct player list from banked box scores; `resolve_player`
can populate rotowire_id/slug from the site search box's typeahead endpoint when a run assembles the
map (it performs ONE typeahead fetch per name through the injectable session -- it is never called by
the disabled paths).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Iterable, Optional

import pandas as pd

from mlb_core import storage
from nba.config import NBA_PREFIX

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
ENABLE_ENV = "ROTOWIRE_PULL_ENABLED"

BASE = "https://www.rotowire.com"
PLAYER_URL_TEMPLATE = BASE + "/basketball/player/{slug}-{rotowire_id}"
AJAX_URL = BASE + "/basketball/ajax/get-more-updates.php"
RSS_URL = BASE + "/rss/news.php?sport=NBA"
TYPEAHEAD_URL = BASE + "/frontend/ajax/search-players.php"
TYPEAHEAD_SPORT = "NBA"

PREFIX = f"{NBA_PREFIX}/news/rotowire_archive"
DEFAULT_DELAY_S = 2.5          # polite pacing floor between requests
DEFAULT_PAGE_SIZE = 100        # numUpdates; the site's own UI asks 25, 100 was honored in the check
DEFAULT_TIMEOUT_S = 30.0
WINDOW_DAYS = 10               # a case window is [return_date - WINDOW_DAYS, return_date]
EST_REQUESTS_PER_PLAYER = 3    # check section 3: weighted average for rotation regulars
EST_BYTES_PER_REQUEST = 117_000  # ~117 KB per 100-update AJAX batch, measured in the check

# Default (design) case population: injury returns, missed >= 4 team games, prior-8 baseline >= 18 min.
DEFAULT_MIN_MISSED = 4
DEFAULT_MIN_BASELINE = 18.0
# Widened population (config option): missed >= 2, baseline >= 15.
WIDE_MIN_MISSED = 2
WIDE_MIN_BASELINE = 15.0
BASELINE_GAMES = 8

VERIFIED_SEED_PLAYERS = (  # IDs verified live in the 2026-11-11 check; the seed for a future map build
    {"player": "Joel Embiid", "rotowire_id": 3572, "slug": "joel-embiid"},
    {"player": "Kawhi Leonard", "rotowire_id": 3195, "slug": "kawhi-leonard"},
    {"player": "Paolo Banchero", "rotowire_id": 5700, "slug": "paolo-banchero"},
)

# Box-score reasons that are NOT injury returns (a DNP for these is not an absence spell for this program).
_NON_INJURY_REASONS = ("COACH'S DECISION", "REST", "PERSONAL REASONS", "NOT WITH TEAM", "G LEAGUE")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class PlayerRef:
    """One row of the input player map."""
    player: str
    rotowire_id: int
    slug: str

    @property
    def page_url(self) -> str:
        return PLAYER_URL_TEMPLATE.format(slug=self.slug, rotowire_id=self.rotowire_id)


@dataclass
class BlurbRecord:
    """One parsed RotoWire update, in the shared shape for page / AJAX / RSS sources.

    Superseded updates are retained verbatim: nothing here reconciles a later update against an
    earlier one, and dedupe (see `dedupe_records`) only removes exact re-fetches of the same update.
    """
    player: str
    rotowire_id: Optional[int]
    published_date: str                 # displayed date, ISO (YYYY-MM-DD); day-granular at the source
    published_ts: Optional[float]       # precise ts when the fragment carries one, else date at 00:00 UTC
    headline: str
    body: str
    injury_tag: Optional[str]
    source: str                         # "page" | "ajax" | "rss"
    fetched_at: str                     # ISO timestamp of the fetch that produced this record
    link: str = ""                      # RSS only: the item link (page/ajax records leave it empty)
    guid: str = ""                      # RSS only: the item guid

    def dedupe_key(self) -> tuple:
        return (self.rotowire_id, self.published_ts, self.headline)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReturnCase:
    """One injury-return case sliced from banked box scores."""
    player: str
    athlete_id: Optional[int]
    team: str
    season: Optional[int]
    return_date: str                    # ISO date of the first played game after the spell
    spell_start: str                    # ISO date of the first missed team game
    spell_end: str                      # ISO date of the last missed team game
    missed: int                         # team games missed in the spell
    baseline_minutes: float             # mean minutes over the prior BASELINE_GAMES played games
    return_minutes: Optional[float]
    reasons: list = field(default_factory=list)  # box-score DNP reasons seen during the spell

    @property
    def window_start(self) -> str:
        return (date.fromisoformat(self.return_date) - timedelta(days=WINDOW_DAYS)).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Player map + slug / URL helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """'Kawhi Leonard' -> 'kawhi-leonard' (RotoWire slug style: lowercase, hyphenated, ASCII)."""
    txt = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")


_PLAYER_URL_RE = re.compile(r"/basketball/player/([a-z0-9][a-z0-9\-]*)-(\d+)")


def parse_player_url(url: str) -> Optional[tuple]:
    """(slug, rotowire_id) from a RotoWire player-page URL, or None when the pattern does not match."""
    m = _PLAYER_URL_RE.search(str(url or ""))
    return (m.group(1), int(m.group(2))) if m else None


def load_player_map(source) -> list:
    """Load the input player map from a CSV path, JSON path, file-like object, or list of dicts.

    Expected fields per row: player (name), rotowire_id, slug (slug is derived from the name when absent).
    Rows without a usable rotowire_id are skipped with a warning -- an unresolved player is a map-build
    problem (see `resolve_player`), never a reason to guess an ID.
    """
    if isinstance(source, (list, tuple)):
        rows = list(source)
    elif hasattr(source, "read"):
        text = source.read()
        rows = json.loads(text) if text.lstrip().startswith("[") else list(csv.DictReader(io.StringIO(text)))
    else:
        text = open(source, encoding="utf-8").read()
        rows = json.loads(text) if str(source).endswith(".json") else list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        try:
            rid = int(r.get("rotowire_id"))
        except (TypeError, ValueError):
            log.warning("player map: skipping %r (no rotowire_id)", r.get("player"))
            continue
        name = str(r.get("player") or r.get("name") or "").strip()
        out.append(PlayerRef(player=name, rotowire_id=rid, slug=str(r.get("slug") or slugify(name))))
    return out


def _slug_match_key(slug: str) -> str:
    """Slug comparison key for resolver matching. RotoWire drops some punctuation outright
    where `slugify` hyphenates it (live example: "P.J. Tucker" -> link slug "pj-tucker" vs
    slugified "p-j-tucker"), so slugs are compared on their alphanumeric skeleton."""
    return re.sub(r"[^a-z0-9]", "", (slug or "").lower())


def resolve_player(session, name: str, timeout: float = DEFAULT_TIMEOUT_S) -> Optional[PlayerRef]:
    """Resolve a player name to its RotoWire (slug, id) via ONE typeahead fetch.

    The live route is the site search box's own endpoint (verified live 2026-11-11 against the
    three VERIFIED_SEED_PLAYERS after the /search page this replaces returned 404):
        GET {TYPEAHEAD_URL}?searchTerm=<name>&sport=NBA
        -> {"success": true, "players": [{"name", "rotoPlayerID", "link", ...}, ...]}
    where `link` is the player-page path ("/basketball/player/{slug}-{id}").

    A resolution counts only on a CONFIDENT match: exactly one returned player whose link
    parses, whose `rotoPlayerID` agrees with the link's id, and whose link slug matches
    slugify(name) on the alphanumeric skeleton (see `_slug_match_key`: the endpoint
    ASCII-folds names and drops some punctuation outright, so "P.J. Tucker" -> "pj-tucker").
    Zero matches, an ambiguous payload, or an entry whose id
    field disagrees with its link all return None -- an unresolved player is a map-build
    problem, never a reason to guess an ID. A non-200 response or a non-JSON payload raises,
    like the archive fetcher: a blocked or changed route must fail loudly, never silently
    empty the map.

    For the map-assembly run only: this performs a live request through the injectable
    `session`, so tests drive it with a fake session and the disabled pull paths never call it.
    """
    resp = session.get(TYPEAHEAD_URL, params={"searchTerm": name, "sport": TYPEAHEAD_SPORT},
                       headers=HEADERS, timeout=timeout)
    if getattr(resp, "status_code", 200) != 200:
        raise RuntimeError(f"rotowire resolver: HTTP {resp.status_code} resolving {name!r}")
    try:
        payload = resp.json()
    except Exception:
        try:
            payload = json.loads(getattr(resp, "text", "") or "")
        except Exception as exc:
            raise RuntimeError(f"rotowire resolver: typeahead payload was not JSON for {name!r}") from exc
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, list):
        raise RuntimeError(f"rotowire resolver: unexpected typeahead payload for {name!r}")
    want = _slug_match_key(slugify(name))
    by_id: dict = {}
    for entry in players:
        if not isinstance(entry, dict):
            continue
        parsed = parse_player_url(str(entry.get("link") or ""))
        if parsed is None:
            continue
        slug, rid = parsed
        try:
            rid_field = int(entry.get("rotoPlayerID"))
        except (TypeError, ValueError):
            rid_field = None
        if rid_field is not None and rid_field != rid:
            continue  # the payload's two id carriers disagree: not a confident match
        if _slug_match_key(slug) == want:
            by_id[rid] = PlayerRef(player=name, rotowire_id=rid, slug=slug)
    if len(by_id) != 1:
        return None
    return next(iter(by_id.values()))


# ---------------------------------------------------------------------------
# Update-fragment parsing (player page HTML and AJAX updatesHTML share the shape)
# ---------------------------------------------------------------------------

_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y")
_DATE_TEXT_RE = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2}")


def _parse_display_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    m = _DATE_TEXT_RE.search(text)
    if m:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(m.group(0), fmt).date()
            except ValueError:
                continue
    return None


def _norm_ts(value) -> Optional[float]:
    """Cursor/fragment timestamps arrive as epoch seconds or milliseconds; normalise to seconds."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return ts / 1000.0 if ts > 1e11 else ts


_CURSOR_TEXT_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _cursor_date(value) -> Optional[date]:
    """Calendar date of an archive cursor, or None when it cannot be parsed.

    The live cursor is a datetime STRING ("2025-04-25 22:57:42.090", the player page's
    `data-last-update-time` shape, threaded through `newLastUpdateTime`); synthetic payloads
    carry epoch numbers instead. Both are accepted -- the shipped code ran the string form
    through the epoch-only `_norm_ts`, got None, and read the cursor as 1970-01-01, which
    would have stopped every real pass after its first AJAX page as "passed_oldest_window".
    """
    if value is None:
        return None
    ts = _norm_ts(value)
    if ts is not None:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    text = str(value).strip()
    for fmt in _CURSOR_TEXT_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_PAGE_CURSOR_RE = re.compile(r'data-last-update-time="([^"]+)"')


def _page_cursor(page_html: str) -> Optional[str]:
    """The archive's initial cursor, carried on the player page itself (the "Read More"
    button's `data-last-update-time` attribute). The AJAX endpoint rejects a request with
    no cursor (`{"error":"The Last Update Time cannot be empty."}`), so the first archive
    call must be seeded with this value verbatim, exactly as the site's own UI does."""
    m = _PAGE_CURSOR_RE.search(page_html or "")
    return m.group(1) if m else None


class _UpdateHTMLParser(HTMLParser):
    """Collect the text of each news-update container, split by the fragment's landmarks.

    Tolerant by design: it keys off class-name fragments (headline / date / injury / body) rather than
    one exact markup revision, and falls back to a date-pattern scan for the date and the longest text
    block for the body. The ANALYSIS upsell text ("Subscribe now to instantly reveal our take...") is
    RotoWire's gated fantasy take, not news prose, and is dropped wherever it appears.
    """

    _CONTAINER_HINTS = ("news-update",)
    _SKIP_HINTS = ("analysis", "subscribe")
    # HTML void elements never have end tags, so they must never move the depth counter:
    # live news-update blocks carry an <img> logo (and <br> in the analysis upsell), and
    # counting them nested every block inside the first and collapsed a whole page into
    # one record (the 2026-11-11 smoke defect).
    _VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                            "link", "meta", "param", "source", "track", "wbr"})
    # Container chrome whose text is not prose: the meta row (position / team, around the
    # injury tag), the player-head wrapper, the player-name link, and the position marker.
    _SKIP_SEGMENTS = frozenset({"meta", "playerhead", "player-link", "pos"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.updates: list = []
        self._cur: Optional[dict] = None
        self._depth = 0
        self._field: Optional[str] = None
        self._field_depth = 0

    def _classify(self, classes: str) -> Optional[str]:
        # Classify on each class token's trailing segment (after "__") so the container's own
        # "news-update" prefix cannot false-match ("date" is a substring of "update").
        segs = " ".join(tok.rsplit("__", 1)[-1] for tok in classes.lower().split())
        tokens = segs.split()
        if any(h in segs for h in self._SKIP_HINTS):
            return "skip"
        if any(t in self._SKIP_SEGMENTS for t in tokens):
            return "skip"
        if "headline" in segs or "title" in segs:
            return "headline"
        if "injur" in segs or "inj" in tokens:
            return "injury_tag"
        if "date" in segs or "time" in segs:
            return "date"
        if "body" in segs or "text" in segs or "desc" in segs or "news" in tokens:
            return "body"
        return None

    def handle_starttag(self, tag, attrs):
        if tag in self._VOID_TAGS:
            return
        a = dict(attrs)
        classes = a.get("class", "") or ""
        if self._cur is None:
            if tag in ("div", "li", "article") and any(h in classes.lower() for h in self._CONTAINER_HINTS):
                self._cur = {"headline": "", "date": "", "body": "", "injury_tag": "",
                             "ts": _norm_ts(a.get("data-timestamp") or a.get("data-time"))}
                self._depth = 1
                self._field = None
            return
        self._depth += 1
        field = self._classify(classes)
        if field:
            self._field = field
            self._field_depth = self._depth
        if tag in ("p", "br") and self._field is None:
            self._field = "body"
            self._field_depth = self._depth

    def handle_endtag(self, tag):
        if tag in self._VOID_TAGS:
            return
        if self._cur is None:
            return
        if self._field is not None and self._depth == self._field_depth and tag in ("div", "span", "a", "p", "li"):
            self._field = None
        self._depth -= 1
        if self._depth <= 0:
            self.updates.append(self._cur)
            self._cur = None
            self._field = None

    def handle_data(self, data):
        if self._cur is None or self._field == "skip":
            return
        txt = " ".join(str(data).split())
        if not txt:
            return
        if self._field:
            key = self._field
            self._cur[key] = (self._cur[key] + " " + txt).strip() if self._cur[key] else txt
        else:
            # Untagged text: a date-looking node fills the date; anything else is body prose.
            if _DATE_TEXT_RE.fullmatch(txt) or (not self._cur["date"] and _DATE_TEXT_RE.search(txt) and len(txt) <= 20):
                self._cur["date"] = txt
            else:
                self._cur["body"] = (self._cur["body"] + " " + txt).strip() if self._cur["body"] else txt


def parse_updates_html(html: str, player: PlayerRef, source: str, fetched_at: str) -> list:
    """Parse an updates fragment (player-page HTML or an AJAX `updatesHTML` payload) into records.

    Order is preserved exactly as served (newest first). Superseded / contradictory updates are all
    returned -- the archive's raw state is the evidence. Fragments with no parseable date or headline
    are skipped (they are chrome, not updates).
    """
    parser = _UpdateHTMLParser()
    parser.feed(html or "")
    if parser._cur is not None:  # an unclosed final container still counts
        parser.updates.append(parser._cur)
    out = []
    for u in parser.updates:
        d = _parse_display_date(u.get("date", ""))
        headline = (u.get("headline") or "").strip()
        if d is None or not headline:
            continue
        body = (u.get("body") or "").strip()
        if body.startswith(headline):  # headline repeated as the body's lead-in on some revisions
            body = body[len(headline):].strip(" -:")
        ts = u.get("ts")
        if ts is None:
            ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
        out.append(BlurbRecord(
            player=player.player, rotowire_id=player.rotowire_id,
            published_date=d.isoformat(), published_ts=ts,
            headline=headline, body=body,
            injury_tag=(u.get("injury_tag") or "").strip() or None,
            source=source, fetched_at=fetched_at))
    return out


def dedupe_records(records: Iterable[BlurbRecord]) -> list:
    """Drop exact re-fetches on (player_id, published_ts, headline); keep first occurrence, keep order.

    This is the ONLY removal the pipeline performs: a superseded update has a different timestamp or
    headline, so it always survives.
    """
    seen, out = set(), []
    for r in records:
        k = r.dedupe_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def sort_records(records: Iterable[BlurbRecord]) -> list:
    """Parsed output is stored sorted by timestamp (oldest first)."""
    return sorted(records, key=lambda r: (r.published_ts if r.published_ts is not None else 0.0, r.headline))


def slice_window(records: Iterable[BlurbRecord], return_date, days_before: int = WINDOW_DAYS) -> list:
    """The blurbs belonging to one case: published in [return_date - days_before, return_date].

    Both bounds are inclusive: a blurb on the return game day itself belongs to the window (it may be
    the pre-tip status update), and a blurb exactly `days_before` out is in; one day earlier is out.
    """
    rd = return_date if isinstance(return_date, date) else date.fromisoformat(str(return_date))
    start = rd - timedelta(days=days_before)
    return [r for r in records if start <= date.fromisoformat(r.published_date) <= rd]


# ---------------------------------------------------------------------------
# Fetching (injectable session; pacing via injectable sleeper)
# ---------------------------------------------------------------------------

def _pace(sleeper: Callable[[float], None], delay: float, jitter: float, rng) -> None:
    wait = delay + (rng.uniform(0, jitter) if jitter > 0 else 0.0)
    sleeper(wait)


def fetch_player_archive(session, player: PlayerRef, oldest_date: Optional[date] = None,
                         delay: float = DEFAULT_DELAY_S, jitter: float = 0.0,
                         page_size: int = DEFAULT_PAGE_SIZE, timeout: float = DEFAULT_TIMEOUT_S,
                         sleeper: Callable[[float], None] = time.sleep,
                         rng: Optional[random.Random] = None,
                         now: Optional[datetime] = None) -> dict:
    """One backward pass over a player's archive: player page, then cursor-paged AJAX until the
    archive ends (`noMoreUpdates`) or the cursor passes `oldest_date` (the player's oldest needed
    window start). Returns {"records", "raw_pages", "requests", "stopped"}; raw_pages are the
    verbatim response bodies in fetch order, for banking BEFORE parsing is trusted.

    Raises on a non-200 or unparseable AJAX payload: a partial pass must fail loudly, never bank
    itself as complete.
    """
    rng = rng or random.Random()
    fetched_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    records: list = []
    raw_pages: list = []
    requests_made = 0

    def _get(url, params=None):
        nonlocal requests_made
        if requests_made:
            _pace(sleeper, delay, jitter, rng)
        resp = session.get(url, params=params, headers=HEADERS, timeout=timeout)
        requests_made += 1
        if getattr(resp, "status_code", 200) != 200:
            raise RuntimeError(f"rotowire archive: HTTP {resp.status_code} for {url}")
        return resp

    page = _get(player.page_url)
    raw_pages.append({"kind": "page", "url": player.page_url, "body": page.text})
    records.extend(parse_updates_html(page.text, player, "page", fetched_at))

    # The archive endpoint rejects a cursorless first call with an error payload; the
    # initial cursor is the player page's own `data-last-update-time` attribute, seeded
    # verbatim (probe-verified 2026-11-11: two paced calls, 100 updates each).
    cursor = _page_cursor(page.text)
    seq = 0
    stopped = "no_more_updates"
    while True:
        params = {"type": "player", "itemID": player.rotowire_id, "numUpdates": page_size}
        if cursor is not None:
            params["lastUpdateTime"] = cursor
        resp = _get(AJAX_URL, params=params)
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - re-raised as a loud failure below
            raise RuntimeError(f"rotowire archive: AJAX payload was not JSON for {player.player}") from exc
        raw_pages.append({"kind": "ajax", "url": AJAX_URL, "params": params, "body": resp.text})
        seq += 1
        if (not isinstance(payload, dict) or payload.get("error")
                or not any(k in payload for k in ("updatesHTML", "newLastUpdateTime", "noMoreUpdates"))):
            # An error payload (e.g. {"error":"The Last Update Time cannot be empty."}) is a
            # FAILED pass, never the end of the archive: the shipped code read one as a
            # clean finish and banked a truncated corpus as complete (2026-11-11 smoke).
            raise RuntimeError(
                f"rotowire archive: AJAX error payload for {player.player}: {str(payload)[:200]}")
        records.extend(parse_updates_html(payload.get("updatesHTML") or "", player, "ajax", fetched_at))
        new_cursor = payload.get("newLastUpdateTime")
        if payload.get("noMoreUpdates"):
            stopped = "no_more_updates"
            break
        if new_cursor is None:
            stopped = "no_more_updates"
            break
        if oldest_date is not None:
            cursor_date = _cursor_date(new_cursor)
            if cursor_date is not None and cursor_date < oldest_date:
                stopped = "passed_oldest_window"
                break
        if new_cursor == cursor:
            stopped = "no_more_updates"  # cursor did not move: no further progress to page
            break
        cursor = new_cursor
    return {"records": dedupe_records(records), "raw_pages": raw_pages,
            "requests": requests_made, "stopped": stopped, "fetched_at": fetched_at}


# ---------------------------------------------------------------------------
# RSS (forward variant, same record shape)
# ---------------------------------------------------------------------------

def parse_rss(xml_text: str, fetched_at: str) -> list:
    """Parse one RSS snapshot into BlurbRecords (source="rss").

    Item mapping: title -> headline (a leading "Player Name: " prefix, RotoWire's NBA RSS style,
    also fills `player`), description -> body, pubDate -> published_date/published_ts, link/guid
    carried through. An item whose title names a player also gets its rotowire_id when the link
    carries the player-page pattern.
    """
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        def _txt(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title, desc, link, guid = _txt("title"), _txt("description"), _txt("link"), _txt("guid")
        pub_raw = _txt("pubDate")
        try:
            pub = parsedate_to_datetime(pub_raw) if pub_raw else None
        except (TypeError, ValueError):
            pub = None
        if pub is None or not title:
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        player_name, headline = "", title
        if ": " in title:
            maybe_name, rest = title.split(": ", 1)
            if rest and 1 < len(maybe_name.split()) <= 4 and maybe_name[0].isupper():
                player_name, headline = maybe_name, rest
        parsed_url = parse_player_url(link)
        out.append(BlurbRecord(
            player=player_name, rotowire_id=parsed_url[1] if parsed_url else None,
            published_date=pub.date().isoformat(), published_ts=pub.timestamp(),
            headline=headline, body=desc, injury_tag=None,
            source="rss", fetched_at=fetched_at, link=link, guid=guid))
    return out


def fetch_rss(session, timeout: float = DEFAULT_TIMEOUT_S, now: Optional[datetime] = None) -> dict:
    """Fetch the NBA news RSS once. Returns {"records", "raw_xml", "fetched_at"}; raises on non-200."""
    fetched_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    resp = session.get(RSS_URL, headers=HEADERS, timeout=timeout)
    if getattr(resp, "status_code", 200) != 200:
        raise RuntimeError(f"rotowire rss: HTTP {resp.status_code}")
    return {"records": parse_rss(resp.text, fetched_at), "raw_xml": resp.text, "fetched_at": fetched_at}


# ---------------------------------------------------------------------------
# Case-set builder (from banked hoopR player box scores)
# ---------------------------------------------------------------------------

def _played_mask(frame: pd.DataFrame) -> pd.Series:
    if "did_not_play" in frame.columns:
        return ~frame["did_not_play"].fillna(False).astype(bool)
    mins = pd.to_numeric(frame["minutes"], errors="coerce")
    return mins.fillna(0) > 0


def build_case_set(boxscores: pd.DataFrame, min_missed: int = DEFAULT_MIN_MISSED,
                   min_baseline: float = DEFAULT_MIN_BASELINE, baseline_games: int = BASELINE_GAMES,
                   designations: Optional[pd.DataFrame] = None) -> list:
    """Injury-return cases from a banked player-box-score frame (hoopR `player_boxscores` shape).

    Spells are walked on the TEAM game calendar, not on the player's own rows: between two
    consecutive PLAYED games for the same team, every team game in between that the player did
    not appear in is a missed game, and the later played game is the return. Walking the
    player's own rows instead (the shipped method through 2026-11-11) structurally misses
    long-term injured players, because ESPN omits them from box scores entirely -- Kawhi
    Leonard has zero `did_not_play` rows in five seasons of banked box scores, so the own-rows
    walk emitted 182 cases and none for him; the calendar walk emits the design population
    (1,318 cases / 417 players on the 2022-2026 banked frame, validated 2026-11-11).
    Baseline is the mean minutes over the prior `baseline_games` PLAYED games (the player's
    full played history, across team stints). A spell is excluded only when every DNP reason
    recorded inside it is non-injury (coach's decision / rest / personal / G League); a spell
    with any injury-flavoured reason, or no recorded reasons at all (UNLISTED), is kept.

    With `designations` (columns: player or athlete_id, game_date, status) the population widens at
    the margins: a played game whose pre-game designation was Questionable/Probable after a spell of
    >= 1 missed game also emits a case flagged via reasons=["designation:<status>"] -- this is the
    design population's Q/P-at-decision-time class, available only when designations are provided.
    Without designations the population is exactly the calendar population above.
    """
    need = {"athlete_display_name", "game_date", "minutes", "game_id", "team_abbreviation"}
    missing = need - set(boxscores.columns)
    if missing:
        raise ValueError(f"boxscores missing columns: {sorted(missing)}")
    df = boxscores.copy()
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
    df["minutes"] = pd.to_numeric(df["minutes"], errors="coerce")
    df["played_flag"] = _played_mask(df)

    # Team calendars from the frame itself: distinct (team, game_id, date) in date order.
    cal = (df[["team_abbreviation", "game_id", "game_date"]]
           .drop_duplicates().sort_values("game_date"))
    calendars = {team: list(zip(g["game_date"], g["game_id"]))
                 for team, g in cal.groupby("team_abbreviation")}

    # DNP reasons by (player, game_id); games with no row at all are UNLISTED (no reasons).
    reasons_by_pg: dict = {}
    if "reason" in df.columns:
        for (name, gid), g in df[~df["played_flag"]].groupby(["athlete_display_name", "game_id"]):
            rs = [str(r).strip() for r in g["reason"].dropna() if str(r).strip()]
            if rs:
                reasons_by_pg[(name, gid)] = rs

    played = df[df["played_flag"]].sort_values(["athlete_display_name", "game_date", "game_id"])
    cases: list = []
    for name, g in played.groupby("athlete_display_name", sort=False):
        rows = list(g.itertuples(index=False))
        cols = list(g.columns)
        def _get(row, col):
            return getattr(row, col) if col in cols else None
        prior_minutes: list = []  # the player's played-minutes history before the current game
        for idx, row in enumerate(rows):
            m = _get(row, "minutes")
            if idx > 0:
                prev = rows[idx - 1]
                team = _get(row, "team_abbreviation")
                if team and team == _get(prev, "team_abbreviation"):
                    lo, hi = _get(prev, "game_date"), _get(row, "game_date")
                    missed_games = [(d, gid) for (d, gid) in calendars.get(team, [])
                                    if lo < d < hi]
                    if missed_games:
                        baseline = (float(pd.Series(prior_minutes[-baseline_games:]).mean())
                                    if len(prior_minutes) >= baseline_games else None)
                        if baseline is not None and baseline >= min_baseline:
                            reasons = []
                            for _, gid in missed_games:
                                reasons.extend(reasons_by_pg.get((name, gid), []))
                            injury_spell = (not reasons) or any(
                                not any(k in r.upper() for k in _NON_INJURY_REASONS)
                                for r in reasons)
                            athlete_id = _get(row, "athlete_id")
                            season = _get(row, "season")
                            base = dict(
                                player=str(name),
                                athlete_id=int(athlete_id) if athlete_id is not None
                                and not pd.isna(athlete_id) else None,
                                team=str(team),
                                season=int(season) if season is not None
                                and not pd.isna(season) else None,
                                return_date=_get(row, "game_date").isoformat(),
                                spell_start=missed_games[0][0].isoformat(),
                                spell_end=missed_games[-1][0].isoformat(),
                                missed=len(missed_games), baseline_minutes=round(baseline, 2),
                                return_minutes=float(m) if m is not None and not pd.isna(m)
                                else None)
                            if len(missed_games) >= min_missed and injury_spell:
                                cases.append(ReturnCase(reasons=sorted(set(reasons)), **base))
                            elif designations is not None:
                                st = _designation_for(designations, name, base["athlete_id"],
                                                      _get(row, "game_date"))
                                if st in ("Questionable", "Probable"):
                                    cases.append(ReturnCase(reasons=[f"designation:{st}"], **base))
            if m is not None and not pd.isna(m):
                prior_minutes.append(float(m))
    return cases


def _designation_for(designations: pd.DataFrame, name, athlete_id, game_date) -> Optional[str]:
    d = designations.copy()
    d["game_date"] = pd.to_datetime(d["game_date"]).dt.date
    mask = d["game_date"] == game_date
    if "athlete_id" in d.columns and athlete_id is not None:
        mask &= d["athlete_id"] == athlete_id
    elif "player" in d.columns:
        mask &= d["player"] == name
    else:
        return None
    hit = d[mask]
    return str(hit.iloc[0]["status"]).strip() if len(hit) else None


def distinct_players(cases: Iterable[ReturnCase]) -> list:
    """Distinct case players, sorted by name: the population the player map must cover."""
    return sorted({c.player for c in cases})


def oldest_window_by_player(cases: Iterable[ReturnCase]) -> dict:
    """player -> earliest window-start date across their cases (the backward pass stop point)."""
    out: dict = {}
    for c in cases:
        ws = date.fromisoformat(c.window_start)
        if c.player not in out or ws < out[c.player]:
            out[c.player] = ws
    return out


# ---------------------------------------------------------------------------
# Banking
# ---------------------------------------------------------------------------

def raw_key(rotowire_id: int, kind: str, seq: int = 0) -> str:
    return f"{PREFIX}/raw/{rotowire_id}/page.html" if kind == "page" else f"{PREFIX}/raw/{rotowire_id}/ajax_{seq:04d}.json"


def parsed_key(rotowire_id: int) -> str:
    return f"{PREFIX}/parsed/{rotowire_id}.jsonl"


def bank_player_pull(player: PlayerRef, pull: dict, store=storage) -> dict:
    """Write one player's raw responses + parsed JSONL corpus. Raw lands BEFORE the parsed file so a
    crash mid-write never leaves parsed output without its evidence."""
    seq = 0
    keys = []
    for page in pull["raw_pages"]:
        if page["kind"] == "ajax":
            seq += 1
            key = raw_key(player.rotowire_id, "ajax", seq)
        else:
            key = raw_key(player.rotowire_id, "page")
        store.write_bytes(page["body"].encode(), key)
        keys.append(key)
    records = sort_records(pull["records"])
    body = "".join(json.dumps(r.to_dict()) + "\n" for r in records)
    pkey = parsed_key(player.rotowire_id)
    store.write_bytes(body.encode(), pkey)
    keys.append(pkey)
    return {"player": player.player, "rotowire_id": player.rotowire_id, "records": len(records),
            "requests": pull["requests"], "stopped": pull["stopped"], "keys": keys}


def bank_rss_snapshot(rss: dict, store=storage, now: Optional[datetime] = None) -> dict:
    """Bank one RSS fetch as a dated snapshot: raw XML + parsed JSONL under rss/{ET day}/."""
    from zoneinfo import ZoneInfo
    ts = now or datetime.now(timezone.utc)
    et = ts.astimezone(ZoneInfo("America/New_York"))
    stem = f"{PREFIX}/rss/{et.date().isoformat()}/rss_{et.strftime('%H%M%S')}"
    store.write_bytes(rss["raw_xml"].encode(), stem + ".xml")
    records = sort_records(rss["records"])
    body = "".join(json.dumps(r.to_dict()) + "\n" for r in records)
    store.write_bytes(body.encode(), stem + ".jsonl")
    return {"records": len(records), "keys": [stem + ".xml", stem + ".jsonl"]}


def load_parsed_corpus(rotowire_id: int, store=storage) -> list:
    """Read back one player's parsed JSONL corpus ([] when nothing is banked yet)."""
    key = parsed_key(rotowire_id)
    if not store.exists(key):
        return []
    text = store.read_bytes(key).decode()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Plan + guarded runs (the disabled-by-default boundary)
# ---------------------------------------------------------------------------

def build_pull_plan(players: list, cases: list, delay: float = DEFAULT_DELAY_S) -> dict:
    """Dry-run plan for the historic pull: who, how far back, and what it would cost. Pure arithmetic
    over the case set -- no session, no storage, safe to call any time."""
    oldest = oldest_window_by_player(cases)
    est_requests = len(players) * EST_REQUESTS_PER_PLAYER
    return {
        "mode": "historic",
        "players": len(players),
        "cases": len(cases),
        "player_windows": {p.player: (oldest.get(p.player).isoformat() if oldest.get(p.player) else None)
                           for p in players},
        "estimated_requests": est_requests,
        "estimated_raw_mb": round(est_requests * EST_BYTES_PER_REQUEST / 1e6, 1),
        "estimated_hours_at_pacing": round(est_requests * delay / 3600, 2),
        "pacing_s": delay,
        "prefix": PREFIX,
    }


def pull_enabled(execute: bool, env: Optional[dict] = None) -> bool:
    """Both gates: the explicit execute flag AND ROTOWIRE_PULL_ENABLED=1 in the environment."""
    env = os.environ if env is None else env
    return bool(execute) and str(env.get(ENABLE_ENV, "")).strip() == "1"


def run_historic_pull(players: list, cases: list, session=None, execute: bool = False,
                      env: Optional[dict] = None, delay: float = DEFAULT_DELAY_S,
                      jitter: float = 0.0, store=storage,
                      sleeper: Callable[[float], None] = time.sleep) -> dict:
    """The historic pull, behind both gates. Disabled -> plan only, zero requests, zero writes."""
    plan = build_pull_plan(players, cases, delay=delay)
    if not pull_enabled(execute, env):
        return {"status": "disabled", "plan": plan,
                "note": f"no requests made, nothing banked; enable with --execute and {ENABLE_ENV}=1"}
    if session is None:
        import requests
        session = requests.Session()
    oldest = oldest_window_by_player(cases)
    by_name = {p.player: p for p in players}
    results = []
    for name in sorted(oldest):
        player = by_name.get(name)
        if player is None:
            log.warning("historic pull: case player %r has no player-map row; skipped", name)
            results.append({"player": name, "status": "skipped_no_map_row"})
            continue
        pull = fetch_player_archive(session, player, oldest_date=oldest[name], delay=delay,
                                    jitter=jitter, sleeper=sleeper)
        banked = bank_player_pull(player, pull, store=store)
        results.append({"status": "banked", **banked})
    return {"status": "done", "plan": plan, "players": results,
            "total_requests": sum(r.get("requests", 0) for r in results)}


def run_rss_snapshot(session=None, execute: bool = False, env: Optional[dict] = None,
                     store=storage, now: Optional[datetime] = None) -> dict:
    """One forward RSS banking pass, behind the same two gates as the historic pull."""
    if not pull_enabled(execute, env):
        return {"status": "disabled", "mode": "rss",
                "note": f"no requests made, nothing banked; enable with --execute and {ENABLE_ENV}=1"}
    if session is None:
        import requests
        session = requests.Session()
    rss = fetch_rss(session, now=now)
    banked = bank_rss_snapshot(rss, store=store, now=now)
    return {"status": "done", "mode": "rss", **banked}
