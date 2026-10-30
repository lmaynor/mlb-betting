"""
ev.bettingpros_live -- BettingPros /v3/offers -> ev.props.Quote: a ZERO-credit live multi-sport prop feed.

Why: ParlayAPI props cost credits per market per event from a shared 20k/month pool. BettingPros' public /v3/offers
returns 11-14 books per NFL prop and 5-10 per NHL prop for free, INCLUDING Novig and ProphetX
(docs/analysis/bettingpros_multisport_probe_2026-10-14.md). This module turns those offers into the same `Quote`
objects the sweep already consumes (build_consensus / scan_book_outliers / pooled_consensus), and merges them under
ParlayAPI quotes so BettingPros only FILLS gaps (missing books / missing points).

Built on mlb_core.odds.bettingpros_props (long-format parser, live /v3/markets resolution by slug). It deliberately does
NOT use mlb_core.odds.bettingpros.BOOK_ID_TO_NAME: that MLB map is trimmed and drops Novig/ProphetX.

What is excluded, and why (every one is a correctness rule, not a preference):
  * DFS / pick'em books (book_kind 'dfs'), the BettingPros 'consensus' pseudo-book (id 0), opening-line rows
    ('open') and sharp (id 2, never actually quotes): not two-sided sportsbook prices.
  * Kalshi (and the other prediction markets): Kalshi is the venue the +EV sweep TRADES against; it is never a
    reference for its own price. Any book id not in LIVE_BOOKS is dropped, so a new feed book can never leak in.
  * One-sided books: a (book, player, stat, line) with only one side is still emitted as a one-sided Quote (the
    consumers count it as n_onesided and never de-vig it), exactly like ParlayAPI's 'Yes' ladders.

BettingPros has no player-id crosswalk here: quotes carry ev.props.norm_name(player) as `player_norm` and no team hint
(BettingPros team codes differ from the Kalshi/ParlayAPI codes at JAX/JAC, LA/LAR, ...). When the ParlayAPI side was
parsed WITH a league-id resolver (player_norm = 'id<ID>'), names will not line up -- merge only name-keyed sets
(NFL today).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ev.props import DFS_BOOKS, Quote, norm_name
from mlb_core.odds import bettingpros_props as bpp

log = logging.getLogger("ev.bettingpros_live")

# BettingPros /v3/books id -> the ParlayAPI-style book key used everywhere else in ev/. Only real US sportsbooks and
# the two exchanges that quote two-sided prices. Ids deliberately ABSENT: 0 consensus, 2 pinnacle, 68 kalshi, 72-75
# crypto/prediction markets, every DFS id (bpp.DFS_IDS), and the long tail of Canadian/offshore/casino skins.
LIVE_BOOKS = {
    10: "fanduel", 12: "draftkings", 13: "caesars", 14: "fanatics", 18: "betrivers", 19: "betmgm", 24: "bet365",
    33: "espnbet", 49: "hardrock",                       # theScore Bet is ParlayAPI's `espnbet`
    60: "novig", 38: "prophetx",
}

# BettingPros stat label (the keys of bpp.SPORTS[...]) -> the canonical ev.props stat key for that sport. These are
# ev.props.STAT_KEYS_BY_SPORT's own names, so a BettingPros quote and a ParlayAPI quote land in ONE consensus group.
LABEL_TO_STAT = {
    "nfl": {"pass_yds": "pass_yds", "pass_tds": "pass_tds", "pass_att": "pass_attempts", "pass_cmp": "pass_completions",
            "pass_int": "interceptions", "rec_yds": "rec_yds", "receptions": "receptions", "rush_yds": "rush_yds",
            "rush_att": "rush_attempts", "anytime_td": "anytime_td"},
    "nhl": {"goals": "goals", "points": "points", "assists": "assists", "sog": "sog", "saves": "saves",
            "blocks": "blocked_shots"},
    "nba": {"pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "pra", "pr": "pr",
            "pa": "pa", "ra": "ra", "stl": "steals", "blk": "blocks",
            "td": "triple_double", "triple_double": "triple_double",
            "dd": "double_double", "double_double": "double_double"},
}
# Catalog slug view of the same mapping (documentation / tests): e.g. nfl 'passing-yards' -> 'pass_yds'.
SLUG_TO_STAT = {sp: {bpp.SPORTS[sp.upper()][lab]: st for lab, st in m.items() if lab in bpp.SPORTS[sp.upper()]}
                for sp, m in LABEL_TO_STAT.items()}

_OVER = {"over", "yes"}
_UNDER = {"under", "no"}
_DONE_STATUS = {"complete", "completed", "closed", "final", "cancelled", "canceled", "postponed", "suspended"}


def rows_to_quotes(rows: list[dict], sport: str) -> list[Quote]:
    """Long rows from bpp.build_prop_rows -> [Quote]. Pure: no I/O, no clock.

    Pairs the over and under prices a book quotes at the SAME line into one Quote; a lone side becomes a one-sided
    Quote. `alt` is True for a line the book does not flag as its main line (a ladder rung)."""
    sp = sport.lower()
    stat_of_label = LABEL_TO_STAT[sp]
    cell: dict = {}
    for r in rows:
        if r.get("is_open") or r.get("book_kind") not in ("book", "exchange"):
            continue
        bkey = LIVE_BOOKS.get(r.get("book_id"))
        if bkey is None or bkey in DFS_BOOKS or bkey == "kalshi":
            continue
        stat = stat_of_label.get(_label_of(r, sp))
        if stat is None or r.get("line") is None or r.get("odds") is None or not r.get("player"):
            continue
        side = str(r.get("side") or "").strip().lower()
        if side not in _OVER and side not in _UNDER:
            continue
        pn = norm_name(r["player"])
        if not pn:
            continue
        rec = cell.setdefault((bkey, pn, stat, float(r["line"])),
                              {"player": r["player"], "over": None, "under": None, "main": r.get("is_main")})
        slot = "over" if side in _OVER else "under"
        if rec[slot] is None:                                    # first price wins, same as ev.props.parse_event_props
            rec[slot] = int(r["odds"])
        if r.get("is_main") is True:
            rec["main"] = True
    return [Quote(bk, stat, rec["main"] is False, rec["player"], pn, None, pt, rec["over"], rec["under"])
            for (bk, pn, stat, pt), rec in cell.items()]


def _label_of(row: dict, sp: str):
    """The stat label a row was fetched under: build_prop_rows stores the catalog label in `market`."""
    m = row.get("market")
    labels = LABEL_TO_STAT[sp]
    if m in labels:
        return m
    slug_to_label = {slug: lab for lab, slug in bpp.SPORTS[sp.upper()].items()}
    return slug_to_label.get(m)


def _upcoming(events: dict, now: datetime) -> dict:
    """Drop finished / already-started events: a quote on a game under way is dead."""
    out = {}
    for eid, e in events.items():
        if str(e.get("status") or "").strip().lower() in _DONE_STATUS:
            continue
        try:
            start = datetime.strptime(str(e.get("scheduled")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                start = datetime.fromisoformat(str(e.get("scheduled")).replace("Z", "+00:00"))
            except ValueError:
                out[eid] = e                                       # unparseable time: keep, the consumer gates on kick
                continue
        if start > now:
            out[eid] = e
    return out


def fetch_bp_quotes(sport: str, date_str: str, markets: list[str] | None = None, delay: float = 0.8,
                    now: datetime | None = None, session=None) -> list[Quote]:
    """Live BettingPros props for `sport` ('nfl' | 'nhl' | 'nba') on `date_str` (YYYY-MM-DD) as [Quote]. Costs ZERO
    ParlayAPI credits (it is a different, free API). SLOW BY DESIGN: bpp.fetch_offers_paged sleeps `delay` seconds
    (+-40%) between pages (the API caps limit at 10), so a full NFL slate takes a couple of minutes -- run it from
    a job, not a request path. `markets` = optional subset of stat labels/slugs. A market that errors is logged and
    skipped; it never aborts the others."""
    sp = sport.lower()
    if sp not in LABEL_TO_STAT:
        raise ValueError(f"unsupported sport {sport!r}; expected one of {sorted(LABEL_TO_STAT)}")
    api_sport = sp.upper()
    now = now or datetime.now(timezone.utc)
    sess = session or bpp.make_session(api_sport)
    events = _upcoming(bpp.fetch_events_detail(sess, api_sport, date_str), now)
    if not events:
        return []
    resolved = bpp.resolve_markets(sess, api_sport, markets)
    quotes: list[Quote] = []
    for label, m in resolved.items():
        if label not in LABEL_TO_STAT[sp]:
            continue
        try:
            offers = bpp.fetch_offers_paged(sess, api_sport, m["market_id"], list(events), delay=delay)
        except Exception as e:                                                          # noqa: BLE001
            log.warning("bettingpros %s %s offers failed: %r", sp, label, str(e)[:160])
            continue
        rows = bpp.build_prop_rows(api_sport, label, m["market_id"], date_str, offers, events)
        quotes.extend(rows_to_quotes(rows, sp))
    log.info("bettingpros %s %s: %d events, %d quotes", sp, date_str, len(events), len(quotes))
    return quotes


def merge_quotes(parlay_quotes: list[Quote], bp_quotes: list[Quote]) -> list[Quote]:
    """ParlayAPI quotes + BettingPros quotes, ParlayAPI winning: a BettingPros quote is added ONLY when no ParlayAPI
    quote exists for the same (book, player_norm, stat, point). BettingPros therefore fills in books ParlayAPI did not
    return (Novig/ProphetX/Fanatics...) and points it did not price, and never overrides a price ParlayAPI saw."""
    have = {(q.book, q.player_norm, q.stat, q.point) for q in parlay_quotes}
    return list(parlay_quotes) + [q for q in bp_quotes if (q.book, q.player_norm, q.stat, q.point) not in have]
