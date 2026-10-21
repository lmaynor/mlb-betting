"""
ev.props -- sportsbook player-prop quotes (ParlayAPI) -> per-(player, stat, point) fair-probability consensus.

Shapes observed in a REAL ParlayAPI NFL event payload (2026-10-04; fixture tests/fixtures/ev/):
  * market KEYS differ by book for the same stat (player_rec_yds / player_receiving_yards /
    player_reception_yds, player_rush_yards / _rush_yds / _rushing_yards, ...); aliases below.
  * outcome name "Over <player>" / "Under <player>" with description = player (two-sided), or a one-sided
    "Yes" outcome at a ladder `point` (bovada pass TDs, anytime TD) -- treated as Over at that point.
  * some books append a team to the description: "Jonathan Taylor (IND)".
  * DFS / pick'em apps (sleeper, underdog, ...) quote ~-112/-112 everywhere: they carry the NUMBER, not a
    price, so they are EXCLUDED from the fair-probability median (counted in n_dfs for visibility).

Fair probability = median over real sportsbooks of each book's own 2-way de-vigged P(over). One-sided
quotes are counted (n_onesided) but NOT used: de-vigging them needs an assumed vig that has no NFL
calibration yet, so they are not decision-grade.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from statistics import NormalDist

from mlb_core.alerts.discord_routes import BOOK_ALIASES, canonical_book
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way

DFS_BOOKS = frozenset({"sleeper", "underdog", "prizepicks", "dabble", "betr", "fliff"})

# Offshore books are never shown as a place to bet (best price, flagged outlier). They still count as REFERENCE prices in the
# consensus (Pinnacle is the sharpest line in the pack). Same set as mlb_core.odds.sgo.OFFSHORE_BOOKS plus matchbook, kept
# literal here so ev/ does not import the SGO client.
OFFSHORE_BOOKS = frozenset({
    "bovada", "betfair", "pinnacle", "betonline", "betonlineag", "mybookie", "bookmakereu", "heritagesports", "lowvig",
    "betanysports", "sportsbetting", "betus", "matchbook",
})

# canonical stat -> every ParlayAPI market key seen (or requested) for it
STAT_KEYS = {
    "pass_yds": {"player_pass_yds", "player_passing_yards"},
    "rush_yds": {"player_rush_yds", "player_rush_yards", "player_rushing_yards"},
    "rec_yds": {"player_reception_yds", "player_rec_yds", "player_receiving_yards"},
    "receptions": {"player_receptions"},
    "pass_tds": {"player_pass_tds", "player_passing_touchdowns", "player_passing_tds"},
    "anytime_td": {"player_anytime_td", "player_anytime_touchdown_scorer"},
    # Count stats with no Kalshi series (2026-10-14): book-vs-book only, never pooled (POOL_MAX_GAP is yardage-only).
    "pass_attempts": {"player_pass_attempts", "player_passing_attempts"},
    "pass_completions": {"player_pass_completions", "player_passing_completions"},
    "rush_attempts": {"player_rush_attempts", "player_rushing_attempts"},
    "interceptions": {"player_interceptions", "player_pass_interceptions"},
}
_KEY_TO_STAT = {k: s for s, ks in STAT_KEYS.items() for k in ks}

# NHL / NBA (2026-10-14). Keys verified against the live ParlayAPI /v1/markets catalog AND real payloads. Per-sport
# tables because the SAME market key means different things by sport (player_points in the NBA is a points O/U; in
# the NHL it is a skater points market, mostly one-sided "Yes" anytime prices). Goalie saves arrive as
# `player_saves` although the catalog lists `player_total_saves` -- both are mapped.
STAT_KEYS_BY_SPORT = {
    "nfl": STAT_KEYS,
    "nhl": {
        "goals": {"player_goals", "player_goal_scorer_anytime"}, "sog": {"player_shots_on_goal"}, "points": {"player_points"},
        "assists": {"player_assists"}, "saves": {"player_saves", "player_total_saves"},
        "pp_points": {"player_power_play_points"}, "blocked_shots": {"player_blocked_shots"},
        "hits": {"player_hits"}, "pim": {"player_penalty_minutes"},
    },
    "nba": {
        "points": {"player_points"}, "rebounds": {"player_rebounds"}, "assists": {"player_assists"},
        "threes": {"player_threes"}, "pra": {"player_points_rebounds_assists"},
        "pr": {"player_points_rebounds"}, "pa": {"player_points_assists"}, "ra": {"player_rebounds_assists"},
        "steals": {"player_steals"}, "blocks": {"player_blocks"}, "turnovers": {"player_turnovers"},
        "stocks": {"player_steals_blocks"}, "minutes": {"player_minutes"},
    },
}
_KEY_TO_STAT_BY_SPORT = {sp: {k: st for st, ks in tbl.items() for k in ks} for sp, tbl in STAT_KEYS_BY_SPORT.items()}

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
_TEAM_TAIL = re.compile(r"\s*\(([A-Za-z]{2,4})\)\s*$")


def stat_of(market_key: str, sport: str = "nfl"):
    """ParlayAPI market key -> (canonical stat | None, is_alternate). `sport` selects the key table (default NFL,
    the original behaviour)."""
    alt = market_key.endswith("_alternate")
    base = market_key[: -len("_alternate")] if alt else market_key
    return _KEY_TO_STAT_BY_SPORT.get(sport, _KEY_TO_STAT).get(base), alt


def split_player(raw: str):
    """'Jonathan Taylor (IND)' -> ('Jonathan Taylor', 'IND'); no team -> (raw, None)."""
    raw = (raw or "").strip()
    m = _TEAM_TAIL.search(raw)
    return (raw[: m.start()].strip(), m.group(1).upper()) if m else (raw, None)


def norm_name(name: str) -> str:
    """Accent-fold, lowercase, drop punctuation and generational suffixes: "Ja'Marr Chase Jr." -> 'jamarr chase'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`\u2019]", "", s)                       # \u2019 = typographic apostrophe
    s = re.sub(r"[-_]", " ", s)
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if t not in _SUFFIX_TOKENS]
    return " ".join(toks)


@dataclass(frozen=True)
class Quote:
    book: str
    stat: str
    alt: bool
    player: str            # display name (team suffix stripped)
    player_norm: str
    team_hint: str | None
    point: float
    over: int | None       # american price; for a one-sided 'Yes' ladder this is the Yes price
    under: int | None
    player_id: object = None   # league id when a roster resolver identified the player (player_norm is then 'id<ID>')

    @property
    def two_sided(self) -> bool:
        return self.over is not None and self.under is not None


def _fold_team(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace(".", "").strip()


def parse_event_props(event_obj: dict, sport: str = "nfl", resolver=None, team_fn=None) -> tuple[list[Quote], dict]:
    """One ParlayAPI single-event odds object -> ([Quote], {'unmapped_keys': {...}, 'unresolved': {...}}).

    sport     : picks the market-key table (nfl default, nhl, nba).
    resolver  : optional mlb_core.data.player_names.PlayerResolver. When given, each quote is resolved to the league's
                own player id using the book's team hint and the event's two teams; the id becomes player_norm
                ('id<ID>') so the SAME player spelled differently by different books ("Alex Ovechkin" /
                "Alexander Ovechkin" / "Alex Ovechkin (WSH)") lands in ONE consensus group. AMBIGUOUS names are
                DROPPED (a wrong merge prices a bet off another player's line); names not in the roster at all are
                kept under their normalized string.
    team_fn   : team name/abbrev -> that sport's team code (needed with a resolver for the team hint + event teams).
    Quotes whose 'player' is really a TEAM line (ParlayAPI's NHL player_goals carries team totals) are skipped.
    """
    quotes: list[Quote] = []
    unmapped: dict = {}
    unresolved = {"ambiguous": 0, "unknown": 0}
    team_rows = {_fold_team(event_obj.get("home_team")), _fold_team(event_obj.get("away_team"))} - {""}
    within = None
    if resolver is not None and team_fn is not None:
        within = [c for c in (team_fn(event_obj.get("away_team")), team_fn(event_obj.get("home_team"))) if c]
    books = (event_obj or {}).get("bookmakers", []) or []
    present = {str(b.get("key", "")).strip().lower() for b in books}
    for book in books:
        raw_key = str(book.get("key", "")).strip().lower()
        # Kambi clones quote identical lines: when the original (betrivers) is in the payload the clone
        # (sugarhouse) is skipped; alone, it is folded into the original so it counts as ONE book either way.
        if raw_key in BOOK_ALIASES and BOOK_ALIASES[raw_key] in present:
            continue
        bkey = canonical_book(book.get("key", "")) if raw_key in BOOK_ALIASES else book.get("key", "")
        for market in book.get("markets", []) or []:
            mkey = market.get("key", "")
            stat, alt = stat_of(mkey, sport)
            if stat is None:
                if mkey.startswith("player_"):
                    unmapped[mkey] = unmapped.get(mkey, 0) + 1
                continue
            cell: dict = {}
            for o in market.get("outcomes", []) or []:
                raw, price, point = o.get("description"), o.get("price"), o.get("point")
                name = (o.get("name") or "").strip()
                if not raw or price is None or point is None or "{" in raw or " @ " in raw:
                    continue
                if _fold_team(raw) in team_rows:
                    continue                                   # a team total, not a player
                low = name.lower()
                side = "over" if (low.startswith("over") or low == "yes") else \
                       "under" if low.startswith("under") else None
                if side is None:
                    continue
                player, team = split_player(raw)
                pn, pid = norm_name(player), None
                if resolver is not None:
                    hint = team_fn(team) if (team and team_fn) else team
                    pid, how = resolver.resolve(player, hint, within=within)
                    if how == "ambiguous":
                        unresolved["ambiguous"] += 1
                        continue
                    if pid is not None:
                        pn = f"id{pid}"
                    else:
                        unresolved["unknown"] += 1
                rec = cell.setdefault((pn, float(point)),
                                      {"player": player, "team": team, "over": None, "under": None, "pid": pid})
                if rec[side] is None:                       # first price wins; a book repeats a point rarely
                    rec[side] = int(price)
            for (pn, pt), rec in cell.items():
                quotes.append(Quote(bkey, stat, alt, rec["player"], pn, rec["team"], pt, rec["over"], rec["under"],
                                    rec["pid"]))
    return quotes, {"unmapped_keys": unmapped, "unresolved": unresolved}


@dataclass(frozen=True)
class PropConsensus:
    p_over: float
    n_books: int           # two-sided real sportsbooks used in the median
    disp: float            # MAD of per-book fair P(over)
    books: tuple
    n_onesided: int
    n_dfs: int
    team_hints: tuple = ()  # distinct team codes any book attached to this player's quote ('IND')
    book_fair: tuple = ()   # ((book, fair P(over)), ...) -- lets a venue be left OUT of its own reference price
    book_quotes: tuple = ()  # ((book, over_american, under_american, fair P(over)), ...) sorted by book -- line shopping


def _mad(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(np.median(np.abs(x - np.median(x)))) if len(x) else 0.0


def build_consensus(quotes: list[Quote], devig_method: str = "proportional") -> dict:
    """{(player_norm, stat, point): PropConsensus} for every point with >= 1 usable two-sided real book."""
    groups: dict = {}
    for q in quotes:
        groups.setdefault((q.player_norm, q.stat, q.point), []).append(q)
    out = {}
    for key, qs in groups.items():
        per_book: dict = {}
        prices: dict = {}
        n_dfs = 0
        onesided = set()
        hints = {q.team_hint for q in qs if q.team_hint}
        for q in qs:
            if q.book in DFS_BOOKS:
                n_dfs += 1
                continue
            if not q.two_sided:
                onesided.add(q.book)
                continue
            fair_over, _ = devig_two_way(american_to_implied_prob(q.over), american_to_implied_prob(q.under),
                                         devig_method)
            per_book[q.book] = float(fair_over)
            prices[q.book] = (int(q.over), int(q.under))
        if not per_book:
            continue
        vals = list(per_book.values())
        out[key] = PropConsensus(
            p_over=float(np.median(vals)), n_books=len(vals), disp=_mad(vals), books=tuple(sorted(per_book)),
            n_onesided=len(onesided - set(per_book)), n_dfs=n_dfs, team_hints=tuple(sorted(hints)),
            book_fair=tuple(sorted(per_book.items())),
            book_quotes=tuple((b, prices[b][0], prices[b][1], per_book[b]) for b in sorted(per_book)))
    return out


def _all_books(c: PropConsensus, q: Quote) -> list[dict]:
    """Per-book line-shopping strip for one (player, stat, point): every two-sided real book's own American prices and
    de-vigged fair P(over). A one-sided quote that is NOT in the consensus (a 'Yes'-only ladder) is appended with
    under/fair = None so the book that triggered the outlier is always visible."""
    out = [{"book": b, "over": o, "under": u, "fair": round(f, 4)} for b, o, u, f in c.book_quotes]
    if q.book not in {r["book"] for r in out}:
        out.append({"book": q.book, "over": q.over, "under": q.under, "fair": None})
    return out


def scan_book_outliers(quotes: list[Quote], min_books: int = 4, min_ev: float = 0.03, max_disp: float = 0.06,
                       devig_method: str = "proportional") -> list[dict]:
    """Sink A: individual real-book / exchange quotes whose own price beats the LEAVE-SELF-OUT consensus at the same
    (player, stat, point). Same reference as build_consensus (2-way de-vigged, DFS excluded) so it costs no extra
    ParlayAPI credits. EV = p_fair * decimal - 1 at the quote's actual price; one-sided 'Yes' quotes are
    scannable on their Over side. Evidence only -- the +7.56% paper figure behind this idea is MLB soft-book vs
    Pinnacle-anchored consensus, not NFL."""
    cons = build_consensus(quotes, devig_method=devig_method)
    out = []
    for q in quotes:
        if q.book in DFS_BOOKS or q.book in OFFSHORE_BOOKS:
            continue
        c = cons.get((q.player_norm, q.stat, q.point))
        if c is None:
            continue
        others = [p for b, p in c.book_fair if b != q.book]
        if len(others) < min_books:
            continue
        disp = _mad(others)
        if disp > max_disp:
            continue
        p_over = float(np.median(others))
        all_books = _all_books(c, q)
        for side, price, p_fair in (("OVER", q.over, p_over), ("UNDER", q.under, 1.0 - p_over)):
            if price is None:
                continue
            dec = american_to_decimal(price)
            ev = p_fair * dec - 1.0
            if ev >= min_ev:
                out.append({"book": q.book, "player": q.player, "player_norm": q.player_norm, "player_id": q.player_id,
                            "stat": q.stat,
                            "point": q.point, "alt": q.alt, "side": side, "american": price,
                            "decimal": round(dec, 4), "p_fair": round(p_fair, 4), "ev": round(ev, 4),
                            "n_books": len(others), "disp": round(disp, 4), "all_books": all_books})
    return sorted(out, key=lambda r: -r["ev"])


# ---------------------------------------------------------------------------- pooled (adjacent half-point) consensus
# EVIDENCE ONLY (logged next to the exact-point consensus, never used for flags/orders until shadow data says so).
# Books split their main line across adjacent half-points (63.5 / 64.5 / 65.5), so an exact-point match often has 1-2
# books where 3+ quote the player. Pooling moves each book's de-vigged P(over) from its own point to the target
# point under a normal model of the stat with sd = POOL_SD_FRAC * target (UNVALIDATED rough CVs; the shift is
# ~0.4/sd per yard so a 30% sd error moves a 1-yard shift by < 0.4pp). Discrete count stats are never pooled:
# one reception is a ~12-15pp step, not a rounding error.
POOL_MAX_GAP = {"pass_yds": 1.0, "rush_yds": 1.0, "rec_yds": 1.0}
POOL_SD_FRAC = {"pass_yds": 0.22, "rush_yds": 0.65, "rec_yds": 0.55}


def book_points(quotes: list[Quote], devig_method: str = "proportional") -> dict:
    """{(player_norm, stat): [(book, point, fair P(over)), ...]} -- one entry per real two-sided book quote."""
    out: dict = {}
    for q in quotes:
        if q.book in DFS_BOOKS or not q.two_sided:
            continue
        fair_over, _ = devig_two_way(american_to_implied_prob(q.over), american_to_implied_prob(q.under), devig_method)
        out.setdefault((q.player_norm, q.stat), []).append((q.book, q.point, float(fair_over)))
    return out


def shift_p_over(p_over: float, from_point: float, to_point: float, sd: float) -> float:
    """Re-express P(X > from_point) as P(X > to_point) under X ~ Normal(mu, sd) with mu implied by the price."""
    if from_point == to_point:
        return p_over
    nd = NormalDist()
    p = min(max(p_over, 1e-4), 1 - 1e-4)
    mu = from_point + sd * nd.inv_cdf(p)                 # P(X > from) = p  ->  (from - mu)/sd = -inv_cdf(p)
    return float(1.0 - nd.cdf((to_point - mu) / sd))


def pooled_consensus(entries: list, stat: str, target: float, min_books: int = 1):
    """Pooled consensus at `target` from [(book, point, fair)]: each book contributes its quote NEAREST the target
    (within POOL_MAX_GAP[stat]), shifted to the target. None if the stat is not poolable or < min_books books."""
    gap = POOL_MAX_GAP.get(stat)
    if gap is None:
        return None
    sd = POOL_SD_FRAC[stat] * max(target, 1.0)
    best: dict = {}
    for book, pt, fair in entries:
        d = abs(pt - target)
        if d <= gap and (book not in best or d < best[book][0]):
            best[book] = (d, pt, fair)
    if len(best) < min_books:
        return None
    vals = [shift_p_over(f, pt, target, sd) for _, pt, f in best.values()]
    n_exact = sum(1 for d, _, _ in best.values() if d == 0)
    return {"p_over": float(np.median(vals)), "n_books": len(vals), "disp": _mad(vals), "n_exact": n_exact,
            "interpolated": n_exact < len(vals), "books": sorted(best)}
