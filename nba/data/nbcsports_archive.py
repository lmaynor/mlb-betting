"""NBC Sports (Rotoworld) NBA player-news archive puller (historic prose) for the
minutes-restriction program -- the PRIMARY prose corpus from 2026-11-11, when Luc
redirected the prose pull from RotoWire to NBC Sports ("Switch the prose pull to NBC
as the primary source"; same parent company per Luc's representation as relationship
holder). The RotoWire puller (nba/data/rotowire_archive.py, PR #240) is untouched by
this module; the case population it consumes is the same validated team-calendar
population (1,318 cases / 417 players), which is source-independent.

DISABLED BY DEFAULT, exactly like the RotoWire puller: a run needs BOTH the caller's
execute=True (the CLI's --execute flag) AND NBCSPORTS_PULL_ENABLED=1 in the
environment. With either gate closed, `run_historic_pull` returns {"status":
"disabled"} and makes ZERO HTTP requests and ZERO storage writes.

Access surface (verified live 2026-11-11 against De'Anthony Melton, id 10744, and the
three seed players):
  * Player news  GET https://www.nbcsports.com/nba/{slug}/{nbc_id}/news[?p=N]
    Server-rendered `PlayerNewsPost` blocks, 10 per page, newest first. Pagination is
    a plain query parameter; there are no numbered links in the UI, and requesting a
    page past the end CLAMPS to the last page (its items repeat). Melton's full
    archive is 62 pages / 612 items back to Sep 2018.
  * Each block carries the SUBJECT player's link (`/nba/{slug}/{id}` in the block
    header), a headline (h3), the full analysis text with its author, an optional
    `Source:` line (NBA Injury Report, NBA Lineups, named beat writers, ...), an item
    type (Injury / Recap / Transaction / ...), and a precise UTC timestamp in the
    date element's `data-date` attribute (ISO 8601). The page's feed is TAGGED: most
    items on a player's page are about teammates who merely mention him, so parsing
    records the subject of every item and flags subject-vs-mention for the page's
    player. NOTHING is filtered at banking time.
  * Resolution   GET https://www.nbcsports.com/search?q={name}
    The results page links the player's page; a resolution counts only on a confident
    match (exactly one distinct player id whose link slug matches the name on the
    alphanumeric skeleton -- NBC drops apostrophes outright, "De'Anthony Melton" ->
    "deanthony-melton"). Seed gate verified live 2026-11-11: Joel Embiid 10488,
    Kawhi Leonard 9568, Paolo Banchero 11269.

Streaming dedupe at three levels (Luc's design note, 2026-11-11), enforced AS WE GO
against a checkpointed corpus ledger, never as a post-pass:
  1. Page level -- a fetched page whose items are ALL already in the seen-set is a
     clamp or overlap: paging for that player stops there, and the page is not
     re-banked. (An item-identical repeat of the previous page is the site's clamp;
     a merely all-seen page is an overlap stop. Both are reported distinctly.)
  2. Item level -- every item is checked against the seen-set BEFORE writing; the
     seen-set is the corpus ledger, persisted across checkpoint resumes, so a repeat
     appearance inside one player's own pages is dropped before banking, not after.
  3. Cross-player -- the same blurb surfaces on several case players' pages
     (teammate tagging). It is banked ONCE, in full, under the first page player
     who surfaced it, keyed corpus-wide by (published_ts, headline, subject). Each
     later appearance is recorded as a mention reference (mentions/{id}.jsonl)
     pointing at the banking player, never as a duplicate parsed row -- so Arm T's
     token accounting stays clean.

Migration stratum: the oldest items in a long archive can share a repeated
2018-09-14 timestamp (a platform migration/backfill artifact). Items carrying that
date are flagged `migration_stratum` in the parsed output; their dates are not
trusted. The case windows start Oct 2021, so the stratum should never intersect a
case window; the flag exists so it can never do so silently.

Window rule: the backward pass stops once a page's items predate the player's
earliest case window start minus WINDOW_BUFFER_DAYS (30). Windows are sliced
locally at analysis time from the banked corpus, as with the RotoWire corpus.

Storage (via mlb_core.storage):
    NBA/news/nbcsports_archive/raw/{nbc_id}/page_{page:04d}.html   verbatim pages
    NBA/news/nbcsports_archive/parsed/{nbc_id}.jsonl               items FIRST banked
        from this player's pages (full rows; corpus-wide unique by corpus key)
    NBA/news/nbcsports_archive/mentions/{nbc_id}.jsonl             later appearances
        of items banked under another player (reference rows, no prose repeated)

Politeness: generic desktop browser User-Agent, no identifying headers, no login,
no cookies; requests paced >= 2.5 s apart by default. Internal calibration use only.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Callable, Iterable, Optional

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
ENABLE_ENV = "NBCSPORTS_PULL_ENABLED"

BASE = "https://www.nbcsports.com"
NEWS_URL_TEMPLATE = BASE + "/nba/{slug}/{nbc_id}/news"
SEARCH_URL = BASE + "/search"

PREFIX = f"{NBA_PREFIX}/news/nbcsports_archive"
DEFAULT_DELAY_S = 2.5          # polite pacing floor between requests
DEFAULT_TIMEOUT_S = 30.0
ITEMS_PER_PAGE = 10            # measured on the live surface, 2026-11-11
WINDOW_DAYS = 10               # a case window is [return_date - WINDOW_DAYS, return_date]
WINDOW_BUFFER_DAYS = 30        # paging stops 30 days before the earliest window start
MAX_PAGES = 1000               # runaway guard: no real archive approaches this
MIGRATION_DATE = "2018-09-14"  # the platform migration/backfill stratum date

VERIFIED_SEED_PLAYERS = (  # resolved live via the search route, 2026-11-11
    {"player": "Joel Embiid", "nbc_id": 10488, "slug": "joel-embiid"},
    {"player": "Kawhi Leonard", "nbc_id": 9568, "slug": "kawhi-leonard"},
    {"player": "Paolo Banchero", "nbc_id": 11269, "slug": "paolo-banchero"},
)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class PlayerRef:
    """One row of the input player map."""
    player: str
    nbc_id: int
    slug: str

    @property
    def page_url(self) -> str:
        return NEWS_URL_TEMPLATE.format(slug=self.slug, nbc_id=self.nbc_id)


@dataclass
class ItemRecord:
    """One parsed NBC Sports news item from a case player's (tagged) news feed.

    `player` / `nbc_player_id` identify the PAGE the item was first banked from (the
    case player whose feed surfaced it first). `subject_*` identify the player the
    item is actually about, from the block header link; `is_subject` is True only
    when subject and page player agree. Mention items are banked verbatim alongside
    subject items -- the tagged feed is the evidence; analysis filters, banking
    never does. A full row exists exactly once per corpus key; later appearances
    on other players' pages are mention references, not rows.
    """
    player: str
    nbc_player_id: int
    subject_player: Optional[str]
    subject_nbc_id: Optional[int]
    subject_slug: Optional[str]
    is_subject: bool
    published_ts: float                  # precise, from the block's data-date (UTC)
    published_date: str                  # UTC calendar date, ISO
    headline: str
    body: str
    author: Optional[str]
    source_line: Optional[str]           # the item's "Source:" credit, when carried
    item_type: Optional[str]             # Injury / Recap / Transaction / ...
    migration_stratum: bool              # date is the 2018-09-14 migration artifact
    source: str                          # always "nbcsports" for this corpus
    fetched_at: str

    def corpus_key(self) -> tuple:
        """The corpus-wide identity of the blurb: (published_ts, headline, subject)."""
        return (self.published_ts, self.headline, self.subject_nbc_id)

    def dedupe_key(self) -> tuple:
        """Within one player's banking, the level-2 identity (page player + key)."""
        return (self.nbc_player_id,) + self.corpus_key()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["corpus_key"] = list(self.corpus_key())
        return d


@dataclass
class CorpusLedger:
    """The streaming seen-set for the three-level dedupe, shared across players and
    persisted by the driver across checkpoint resumes.

    seen:        corpus_key -> nbc_id of the player under whom the item is banked
    player_seen: nbc_id -> set of corpus keys already recorded on that player's
                 pages (as a banked row or a mention reference)
    level1_stops / level2_dupes / level3_mentions: running catch counters.
    """
    seen: dict = field(default_factory=dict)
    player_seen: dict = field(default_factory=dict)
    level1_stops: int = 0
    level2_dupes: int = 0
    level3_mentions: int = 0

    def to_jsonable(self) -> dict:
        return {
            "seen": [[list(k), v] for k, v in self.seen.items()],
            "player_seen": {str(pid): [list(k) for k in keys]
                            for pid, keys in self.player_seen.items()},
            "level1_stops": self.level1_stops,
            "level2_dupes": self.level2_dupes,
            "level3_mentions": self.level3_mentions,
        }

    @classmethod
    def from_jsonable(cls, data: dict) -> "CorpusLedger":
        led = cls()
        led.seen = {tuple(k): v for k, v in data.get("seen", [])}
        led.player_seen = {int(pid): {tuple(k) for k in keys}
                           for pid, keys in data.get("player_seen", {}).items()}
        led.level1_stops = data.get("level1_stops", 0)
        led.level2_dupes = data.get("level2_dupes", 0)
        led.level3_mentions = data.get("level3_mentions", 0)
        return led


# ---------------------------------------------------------------------------
# Player map + slug / URL helpers + resolver
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """'Kawhi Leonard' -> 'kawhi-leonard' (lowercase, hyphenated, ASCII)."""
    txt = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")


def _slug_match_key(slug: str) -> str:
    """Slug comparison key: NBC drops some punctuation outright where `slugify`
    hyphenates it ("De'Anthony Melton" -> link slug "deanthony-melton" vs slugified
    "de-anthony-melton"), so slugs are compared on their alphanumeric skeleton."""
    return re.sub(r"[^a-z0-9]", "", (slug or "").lower())


_PLAYER_LINK_RE = re.compile(r'href="https://www\.nbcsports\.com/nba/([a-z0-9][a-z0-9\-]*)/(\d+)"')


def resolve_player(session, name: str, timeout: float = DEFAULT_TIMEOUT_S) -> Optional[PlayerRef]:
    """Resolve a player name to its NBC (slug, id) via ONE site-search fetch.

    The live route is the site's own search (verified live 2026-11-11 against the
    three VERIFIED_SEED_PLAYERS, each returning exactly one player link):
        GET {SEARCH_URL}?q=<name>
    A resolution counts only on a CONFIDENT match: exactly one distinct player id
    among the results whose link slug matches slugify(name) on the alphanumeric
    skeleton. Zero or ambiguous matches return None -- an unresolved player is a
    map-build problem, never a reason to guess an ID. A non-200 response raises,
    like the fetcher: a blocked or changed route must fail loudly.
    """
    resp = session.get(SEARCH_URL, params={"q": name}, headers=HEADERS, timeout=timeout)
    if getattr(resp, "status_code", 200) != 200:
        raise RuntimeError(f"nbcsports resolver: HTTP {resp.status_code} resolving {name!r}")
    want = _slug_match_key(slugify(name))
    by_id: dict = {}
    for slug, sid in _PLAYER_LINK_RE.findall(resp.text or ""):
        if _slug_match_key(slug) == want:
            by_id[int(sid)] = PlayerRef(player=name, nbc_id=int(sid), slug=slug)
    if len(by_id) != 1:
        return None
    return next(iter(by_id.values()))


# ---------------------------------------------------------------------------
# Item parsing (PlayerNewsPost blocks)
# ---------------------------------------------------------------------------

_BLOCK_MARKER = '<div class="PlayerNewsPost"'
_SUBJECT_RE = re.compile(
    r'PlayerNewsPost-name-container"><a href="https://www\.nbcsports\.com/nba/([a-z0-9\-]+)/(\d+)"[^>]*>(.*?)</a>',
    re.S)
_HEADLINE_RE = re.compile(r'PlayerNewsPost-headline">(.*?)</h3>', re.S)
_ANALYSIS_RE = re.compile(r'PlayerNewsPost-analysis">(.*?)(?:<div class="PlayerNewsPost-author">'
                          r'|<div class="PlayerNewsPost-footer">)', re.S)
_AUTHOR_RE = re.compile(r'PlayerNewsPost-author">-&nbsp;<a[^>]*>([^<]+)</a>')
_SOURCE_RE = re.compile(r'PlayerNewsPost-source">(.*?)</div>', re.S)
_TYPE_RE = re.compile(r'PlayerNewsPost-type">([^<]+)<')
_DATE_ATTR_RE = re.compile(r'data-date="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")

_TZ_OFFSETS = {  # US zone abbreviations carried by the site's rendered date strings
    "UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8,
    "HST": -10, "AST": -4, "ADT": -3,
}


def _clean_text(fragment: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", fragment or "")).split())


def parse_display_datetime(text: str) -> Optional[float]:
    """Epoch seconds for a rendered site date ("Oct 21, 2026, 7:40 PM CDT"), or None.

    The blocks' machine timestamp is the `data-date` attribute; this covers the
    human-rendered form (and pins the tz-abbreviation semantics in tests).
    """
    m = re.match(r"\s*([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}) ([AP]M) ([A-Z]{3,4})\s*$",
                 text or "")
    if not m or m.group(7) not in _TZ_OFFSETS:
        return None
    mon, day, year, hh, mm, ampm, tz = m.groups()
    try:
        base = datetime.strptime(f"{mon} {day} {year} {hh}:{mm} {ampm}", "%b %d %Y %I:%M %p")
    except ValueError:
        return None
    base = base.replace(tzinfo=timezone(timedelta(hours=_TZ_OFFSETS[tz])))
    return base.timestamp()


def _parse_iso_ts(value: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_items_html(html: str, player: PlayerRef, fetched_at: str) -> list:
    """Parse one news page's PlayerNewsPost blocks into ItemRecords, in served order
    (newest first). Blocks with no parseable timestamp or headline are skipped (they
    are chrome, not items). Every other block is returned -- subject and mention
    alike; streaming dedupe downstream decides what gets banked where.
    """
    out = []
    for block in (html or "").split(_BLOCK_MARKER)[1:]:
        ts = None
        dm = _DATE_ATTR_RE.search(block)
        if dm:
            ts = _parse_iso_ts(dm.group(1))
        hm = _HEADLINE_RE.search(block)
        headline = _clean_text(hm.group(1)) if hm else ""
        if ts is None or not headline:
            continue
        subj_slug = subj_id = subj_name = None
        sm = _SUBJECT_RE.search(block)
        if sm:
            subj_slug, subj_id = sm.group(1), int(sm.group(2))
            # The anchor's full text is the complete display name -- joining only
            # the first/last spans drops generational suffixes ("Gary Payton II"
            # came out as "Gary Payton" until the anchor text was used whole).
            subj_name = _clean_text(sm.group(3)) or None
        am = _ANALYSIS_RE.search(block)
        body = _clean_text(am.group(1)) if am else ""
        aum = _AUTHOR_RE.search(block)
        srcm = _SOURCE_RE.search(block)
        source_line = None
        if srcm:
            source_line = _clean_text(srcm.group(1))
            if source_line.lower().startswith("source:"):
                source_line = source_line[len("source:"):].strip() or None
        tym = _TYPE_RE.search(block)
        published_date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        out.append(ItemRecord(
            player=player.player, nbc_player_id=player.nbc_id,
            subject_player=subj_name, subject_nbc_id=subj_id, subject_slug=subj_slug,
            is_subject=(subj_id is not None and subj_id == player.nbc_id),
            published_ts=ts, published_date=published_date,
            headline=headline, body=body,
            author=_clean_text(aum.group(1)) if aum else None,
            source_line=source_line,
            item_type=_clean_text(tym.group(1)) if tym else None,
            migration_stratum=(published_date == MIGRATION_DATE),
            source="nbcsports", fetched_at=fetched_at))
    return out


def dedupe_records(records: Iterable[ItemRecord]) -> list:
    """Drop exact repeats on (page player, published_ts, headline, subject); keep
    first occurrence, keep order."""
    seen, out = set(), []
    for r in records:
        k = r.dedupe_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def sort_records(records: Iterable[ItemRecord]) -> list:
    """Parsed output is stored sorted by timestamp (oldest first)."""
    return sorted(records, key=lambda r: (r.published_ts, r.headline))


def slice_window(records: Iterable[ItemRecord], return_date, days_before: int = WINDOW_DAYS,
                 subject_only: bool = True) -> list:
    """The items belonging to one case: published in [return_date - days_before,
    return_date] (both bounds inclusive). Subject items by default -- the case
    player's own news trajectory; pass subject_only=False for the full tagged feed."""
    rd = return_date if isinstance(return_date, date) else date.fromisoformat(str(return_date))
    start = rd - timedelta(days=days_before)
    return [r for r in records
            if start <= date.fromisoformat(r.published_date) <= rd
            and (r.is_subject or not subject_only)]


# ---------------------------------------------------------------------------
# Fetching with streaming three-level dedupe (injectable session + sleeper)
# ---------------------------------------------------------------------------

def _pace(sleeper: Callable[[float], None], delay: float, jitter: float, rng) -> None:
    wait = delay + (rng.uniform(0, jitter) if jitter > 0 else 0.0)
    sleeper(wait)


def _mention_row(item: ItemRecord, banked_under: int) -> dict:
    """A cross-player appearance reference: the item's corpus identity + where its
    full row is banked + how it appeared on THIS page. No prose is repeated."""
    return {"corpus_key": list(item.corpus_key()),
            "published_ts": item.published_ts, "published_date": item.published_date,
            "headline": item.headline, "subject_nbc_id": item.subject_nbc_id,
            "subject_player": item.subject_player, "is_subject": item.is_subject,
            "banked_under_nbc_id": banked_under}


def fetch_player_archive(session, player: PlayerRef, oldest_date: Optional[date] = None,
                         delay: float = DEFAULT_DELAY_S, jitter: float = 0.0,
                         timeout: float = DEFAULT_TIMEOUT_S,
                         sleeper: Callable[[float], None] = time.sleep,
                         rng: Optional[random.Random] = None,
                         now: Optional[datetime] = None,
                         ledger: Optional[CorpusLedger] = None) -> dict:
    """One backward pass over a player's tagged news feed: page 1, then ?p=2, ?p=3,
    ... with the streaming three-level dedupe applied page by page against
    `ledger` (a fresh one is created when not shared). Returns {"records",
    "mentions", "raw_pages", "requests", "stopped", "level2_dupes"}: `records` are
    the items banked IN FULL under this player (corpus-first appearances),
    `mentions` the reference rows for items banked under other players.

    Termination, in check order per page after the first:
      * "clamped" -- the page repeats the previous page item-for-item (the site's
        past-the-end clamp); it is a level-1 stop and the page is not re-banked.
      * "all_seen_overlap" -- every item on the page was already in the ledger
        (shifted-boundary overlap); also a level-1 stop, page not re-banked.
        First-appearances FOR THIS PLAYER on such a page are still recorded as
        mention references before stopping, so appearance accounting stays exact.
      * "empty_page" -- a page with no items at all.
      * "passed_oldest_window" -- the page's oldest NEW item predates
        `oldest_date` (the page itself is still banked; slicing is local).
    Raises on a non-200, or on a first page with no items: a partial pass must fail
    loudly, never bank itself as complete.
    """
    rng = rng or random.Random()
    ledger = ledger if ledger is not None else CorpusLedger()
    mine = ledger.player_seen.setdefault(player.nbc_id, set())
    fetched_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    records: list = []
    mentions: list = []
    raw_pages: list = []
    requests_made = 0
    level2_dupes = 0
    prev_keys: Optional[list] = None
    stopped = "empty_page"
    page_no = 0
    while page_no < MAX_PAGES:
        page_no += 1
        if requests_made:
            _pace(sleeper, delay, jitter, rng)
        url = player.page_url if page_no == 1 else f"{player.page_url}?p={page_no}"
        resp = session.get(url, headers=HEADERS, timeout=timeout)
        requests_made += 1
        if getattr(resp, "status_code", 200) != 200:
            raise RuntimeError(f"nbcsports archive: HTTP {resp.status_code} for {url}")
        items = parse_items_html(resp.text, player, fetched_at)
        if not items:
            if page_no == 1:
                raise RuntimeError(
                    f"nbcsports archive: first page carried no items for {player.player}")
            stopped = "empty_page"
            break
        keys = [it.corpus_key() for it in items]
        if prev_keys is not None and keys == prev_keys:
            ledger.level1_stops += 1
            stopped = "clamped"
            break
        # Streaming classification of this page's items against the ledger.
        new_items, page_mentions = [], []
        all_seen = True
        for it in items:
            k = it.corpus_key()
            if k in mine:
                level2_dupes += 1            # repeat on this player's own pages
                continue
            if k in ledger.seen:
                page_mentions.append(_mention_row(it, ledger.seen[k]))
                mine.add(k)
                continue
            all_seen = False
            new_items.append(it)
            ledger.seen[k] = player.nbc_id
            mine.add(k)
        if page_no > 1 and all_seen and not new_items:
            # Level-1 stop: bank this page's mention references (appearances on this
            # player's page are real), but not its raw HTML or any full row.
            mentions.extend(page_mentions)
            ledger.level3_mentions += len(page_mentions)
            ledger.level1_stops += 1
            stopped = "all_seen_overlap"
            break
        raw_pages.append({"kind": "page", "page": page_no, "url": url, "body": resp.text})
        records.extend(new_items)
        mentions.extend(page_mentions)
        ledger.level3_mentions += len(page_mentions)
        prev_keys = keys
        if oldest_date is not None and new_items and \
                date.fromisoformat(new_items[-1].published_date) < oldest_date:
            stopped = "passed_oldest_window"
            break
    else:
        raise RuntimeError(f"nbcsports archive: exceeded {MAX_PAGES} pages for {player.player}")
    ledger.level2_dupes += level2_dupes
    return {"records": records, "mentions": mentions, "raw_pages": raw_pages,
            "requests": requests_made, "stopped": stopped, "fetched_at": fetched_at,
            "level2_dupes": level2_dupes}


# ---------------------------------------------------------------------------
# Banking
# ---------------------------------------------------------------------------

def raw_key(nbc_id: int, page_no: int) -> str:
    return f"{PREFIX}/raw/{nbc_id}/page_{page_no:04d}.html"


def parsed_key(nbc_id: int) -> str:
    return f"{PREFIX}/parsed/{nbc_id}.jsonl"


def mentions_key(nbc_id: int) -> str:
    return f"{PREFIX}/mentions/{nbc_id}.jsonl"


def bank_player_pull(player: PlayerRef, pull: dict, store=storage) -> dict:
    """Write one player's raw pages, full-item JSONL, and mention references. Raw
    lands BEFORE the parsed files so a crash mid-write never leaves parsed output
    without its evidence."""
    keys = []
    for page in pull["raw_pages"]:
        key = raw_key(player.nbc_id, page["page"])
        store.write_bytes(page["body"].encode(), key)
        keys.append(key)
    records = sort_records(pull["records"])
    body = "".join(json.dumps(r.to_dict()) + "\n" for r in records)
    pkey = parsed_key(player.nbc_id)
    store.write_bytes(body.encode(), pkey)
    keys.append(pkey)
    mentions = pull.get("mentions") or []
    if mentions:
        mbody = "".join(json.dumps(m) + "\n" for m in
                        sorted(mentions, key=lambda m: (m["published_ts"], m["headline"])))
        mkey = mentions_key(player.nbc_id)
        store.write_bytes(mbody.encode(), mkey)
        keys.append(mkey)
    return {"player": player.player, "nbc_id": player.nbc_id, "records": len(records),
            "subject_records": sum(1 for r in records if r.is_subject),
            "mentions": len(mentions), "level2_dupes": pull.get("level2_dupes", 0),
            "requests": pull["requests"], "stopped": pull["stopped"], "keys": keys}


def load_parsed_corpus(nbc_id: int, store=storage) -> list:
    """Read back one player's full-item JSONL ([] when nothing is banked yet)."""
    key = parsed_key(nbc_id)
    if not store.exists(key):
        return []
    text = store.read_bytes(key).decode()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_mentions(nbc_id: int, store=storage) -> list:
    """Read back one player's mention-reference JSONL ([] when none are banked)."""
    key = mentions_key(nbc_id)
    if not store.exists(key):
        return []
    text = store.read_bytes(key).decode()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Plan + guarded runs (the disabled-by-default boundary)
# ---------------------------------------------------------------------------

def _case_field(case, field_name):
    return case.get(field_name) if isinstance(case, dict) else getattr(case, field_name)


def oldest_cutoff_by_player(cases: Iterable) -> dict:
    """player -> paging cutoff: earliest case window start minus WINDOW_BUFFER_DAYS."""
    out: dict = {}
    for c in cases:
        ws = _case_field(c, "window_start")
        ws = ws if isinstance(ws, date) else date.fromisoformat(str(ws))
        cutoff = ws - timedelta(days=WINDOW_BUFFER_DAYS)
        name = _case_field(c, "player")
        if name not in out or cutoff < out[name]:
            out[name] = cutoff
    return out


def build_pull_plan(players: list, cases: list, delay: float = DEFAULT_DELAY_S) -> dict:
    """Dry-run plan for the historic pull: who, how far back. Pure arithmetic over the
    case set -- no session, no storage, safe to call any time. Request volume depends
    on each archive's depth to its cutoff, which only the live pass can measure."""
    cutoffs = oldest_cutoff_by_player(cases)
    return {
        "mode": "historic",
        "players": len(players),
        "cases": len(cases),
        "player_cutoffs": {p.player: (cutoffs.get(p.player).isoformat()
                                      if cutoffs.get(p.player) else None)
                           for p in players},
        "pacing_s": delay,
        "prefix": PREFIX,
    }


def pull_enabled(execute: bool, env: Optional[dict] = None) -> bool:
    """Both gates: the explicit execute flag AND NBCSPORTS_PULL_ENABLED=1."""
    env = os.environ if env is None else env
    return bool(execute) and str(env.get(ENABLE_ENV, "")).strip() == "1"


def run_historic_pull(players: list, cases: list, session=None, execute: bool = False,
                      env: Optional[dict] = None, delay: float = DEFAULT_DELAY_S,
                      jitter: float = 0.0, store=storage,
                      sleeper: Callable[[float], None] = time.sleep,
                      ledger: Optional[CorpusLedger] = None) -> dict:
    """The historic pull, behind both gates. Disabled -> plan only, zero requests,
    zero writes. `ledger` carries the streaming dedupe state across players (and,
    via the driver, across checkpoint resumes); a fresh one is created when not
    shared, which is correct for single-player runs."""
    plan = build_pull_plan(players, cases, delay=delay)
    if not pull_enabled(execute, env):
        return {"status": "disabled", "plan": plan,
                "note": f"no requests made, nothing banked; enable with --execute and {ENABLE_ENV}=1"}
    if session is None:
        import requests
        session = requests.Session()
    ledger = ledger if ledger is not None else CorpusLedger()
    cutoffs = oldest_cutoff_by_player(cases)
    by_name = {p.player: p for p in players}
    results = []
    for name in sorted(cutoffs):
        player = by_name.get(name)
        if player is None:
            log.warning("historic pull: case player %r has no player-map row; skipped", name)
            results.append({"player": name, "status": "skipped_no_map_row"})
            continue
        pull = fetch_player_archive(session, player, oldest_date=cutoffs[name], delay=delay,
                                    jitter=jitter, sleeper=sleeper, ledger=ledger)
        banked = bank_player_pull(player, pull, store=store)
        results.append({"status": "banked", **banked})
    return {"status": "done", "plan": plan, "players": results,
            "total_requests": sum(r.get("requests", 0) for r in results),
            "ledger": {"level1_stops": ledger.level1_stops,
                       "level2_dupes": ledger.level2_dupes,
                       "level3_mentions": ledger.level3_mentions,
                       "corpus_items": len(ledger.seen)}}
