"""
ev.kalshi_game_legs -- game-winner (moneyline) fair values for the Kalshi RFQ fair book.

The RFQ flow is dominated by combos of GAME WINNERS (KXNFLGAME / KXNBAGAME / KXNHLGAME ...) and NBA props, while the
props feed only covers NFL player props, so nearly every leg was unpriceable. This module turns one ParlayAPI slate pull
(1 credit PER MARKET per sport for the whole slate: h2h, spreads, totals) into feed rows for `ev.kalshi_rfq_pricer.leg_from_row`:

    Kalshi ticker  KX<LG>GAME-<YY><MON><DD><AWAY><HOME>-<CODE>      yes == <CODE> wins the game
    p_yes          median over books of each book's own 2-way de-vigged P(team wins); Kalshi and DFS books excluded
                   (leave-Kalshi-out), Kambi clones folded into their original.
Also spreads (KX<LG>SPREAD-<tail>-<CODE><N>) and totals (KX<LG>TOTAL-<tail>-<N>); see slate_rows. NHL totals are never emitted.

Tickers are BUILT from the book event and round-tripped through ev.kalshi_map.parse_event, so the grammar is the strict
mapper's, and a team name that does not resolve to a code in the league's complete Kalshi code table drops the game (fail
closed -- a wrong code is a wrong price). Not covered (stay unpriced): NCAAF/NCAAWB/MLS game winners, spreads, totals.
NFL ties settle $0.50 per team on Kalshi; a two-way de-vig ignores them (a ~0.5pp effect, inside the safety pad).
"""

from __future__ import annotations

import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

from ev.kalshi_map import game_key, parse_event
from ev.props import DFS_BOOKS
from ev.sports import ADAPTERS, kalshi_code
from mlb.execution.kalshi.mapping import MapFail
from mlb_core.alerts.discord_routes import BOOK_ALIASES, canonical_book
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

_ET = ZoneInfo("America/New_York")
_ND = statistics.NormalDist()

# Rung interpolation for spreads / totals. Books quote one or two lines per game; Kalshi lists EVERY half-point rung ("wins by over
# 3.5 / 4.5 / ... / 17.5"). Between and beside the quoted lines the margin / total is modelled as Normal(mu, sd) with mu fitted to the
# books' de-vigged probabilities. UNVALIDATED: the sd values are rough league-typical figures, not fitted to this repo's data; rows are
# tagged pooled=True, inherit the thinnest anchor's n_books, and carry extra dispersion that grows with the distance from the nearest
# quoted line, so the leg gate rejects the far ones. Nothing beyond GAME_INTERP_MAX_SD standard deviations from a quoted line is emitted.
GAME_SD = {"nfl": {"margin": 13.5, "total": 12.0}, "nba": {"margin": 12.0, "total": 18.0}, "nhl": {"margin": 2.3, "total": 2.5}}
GAME_INTERP_MAX_SD = 1.0
DISP_PER_SD = 0.02
P_LO, P_HI = 0.01, 0.99
PARLAY_SPORT_KEYS = {"nfl": "americanfootball_nfl", "nba": "basketball_nba", "nhl": "icehockey_nhl"}
_MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
EXCLUDED_BOOKS = DFS_BOOKS | {"kalshi"}


def team_code_fn(sport: str):
    """full team name -> that league's Kalshi code, or None. Resolution uses each pillar's own table, then the league crosswalk
    (NHL: repo LAK/NJD/SJS/TBL -> Kalshi LA/NJ/SJ/TB); anything outside the league's complete Kalshi code table is None."""
    if sport == "nfl":
        from ev.sports.nfl import TEAM_NAMES
        base = lambda name: TEAM_NAMES.get(name)                    # noqa: E731
    elif sport == "nhl":
        from nhl.config import team_abbrev as base
    else:
        from nba.teams import team_code as base
    return lambda name: kalshi_code(sport, base(name))


def _mad(x) -> float:
    m = statistics.median(x)
    return float(statistics.median(abs(v - m) for v in x))


def _fair_pair(pa: int, pb: int) -> float:
    """De-vigged P(side A) from the two American prices of one two-way market."""
    fair, _ = devig_two_way(american_to_implied_prob(int(pa)), american_to_implied_prob(int(pb)), "proportional")
    return float(fair)


def _p_home(book: dict, home: str, away: str):
    """One book's own de-vigged P(home wins) from its h2h market, or None (no h2h / 3-way / unmatched names)."""
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "h2h":
            continue
        px = {}
        for o in mk.get("outcomes", []) or []:
            if o.get("name") in (home, away) and o.get("price") is not None:
                px.setdefault(o["name"], int(o["price"]))
        if len(px) != 2 or len(mk.get("outcomes", []) or []) != 2:        # a Draw outcome => 3-way: not our proposition
            return None
        return _fair_pair(px[home], px[away])
    return None


def _half(x) -> bool:
    return abs(x * 2 - round(x * 2)) < 1e-9 and abs(x - round(x)) > 1e-9


def _totals(book: dict) -> dict:
    """{point L: fair P(Over L)} for each half-point line where this book quotes BOTH Over and Under at L."""
    out: dict = {}
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "totals":
            continue
        px: dict = {}
        for o in mk.get("outcomes", []) or []:
            side, pt = str(o.get("name", "")).strip().lower(), o.get("point")
            if side in ("over", "under") and pt is not None and o.get("price") is not None and _half(float(pt)):
                px.setdefault((float(pt), side), int(o["price"]))
        for (pt, side), price in px.items():
            if side == "over" and (pt, "under") in px:
                out[pt] = _fair_pair(price, px[(pt, "under")])
    return out


def _spreads(book: dict, home: str, away: str) -> dict:
    """{(favourite name, L): fair P(favourite covers -L)} for each half-point line L > 0 where both sides are quoted at +-L.
    The underdog's +L is the other half of the pair; Kalshi's market is 'FAV wins by over L'."""
    out: dict = {}
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "spreads":
            continue
        px: dict = {}
        for o in mk.get("outcomes", []) or []:
            if o.get("name") in (home, away) and o.get("point") is not None and o.get("price") is not None:
                px.setdefault((o["name"], float(o["point"])), int(o["price"]))
        for (name, pt), price in px.items():
            other = away if name == home else home
            if pt < 0 and _half(pt) and (other, -pt) in px:
                out[(name, -pt)] = _fair_pair(price, px[(other, -pt)])
    return out


def _get_nba_game_sd(game_key: str) -> dict[str, float]:
    """Retrieve model-predicted heteroscedastic scale (margin & total) for NBA game if available."""
    try:
        from nba.game.run_daily import get_nba_game_predictions
        preds = get_nba_game_predictions()
        for g in (preds or {}).get("priced_games", []):
            if g.get("game_key") == game_key:
                return {
                    "margin": float(g.get("sigma_margin", 12.0)),
                    "total": float(g.get("sigma_total", 18.0)),
                }
    except Exception:
        pass
    return GAME_SD.get("nba", {})


def _get_nhl_game_sd(game_key: str) -> dict[str, float]:
    """Retrieve model-predicted scale (margin & total) for NHL game if available."""
    try:
        from nhl.game.run_daily import get_nhl_game_predictions
        preds = get_nhl_game_predictions()
        for g in (preds or {}).get("priced_games", []):
            if g.get("game_key") == game_key:
                return {
                    "margin": float(g.get("sigma_margin", 2.3)),
                    "total": float(g.get("sigma_total", 2.5)),
                }
    except Exception:
        pass
    return GAME_SD.get("nhl", {})


def _interp_total_rows(sport: str, tot: dict, game_key: str | None = None) -> list[tuple]:
    """[(N, x, p_over, n_books, disp)] for every half-point rung near the quoted total lines that is NOT itself quoted."""
    sd = GAME_SD.get(sport, {}).get("total")
    if sport == "nba" and game_key:
        dyn = _get_nba_game_sd(game_key)
        if dyn.get("total"):
            sd = dyn["total"]
    elif sport == "nhl" and game_key:
        dyn = _get_nhl_game_sd(game_key)
        if dyn.get("total"):
            sd = dyn["total"]
    if not tot or not sd:
        return []
    anchors = [(pt, float(statistics.median(v)), len(v), _mad(v)) for pt, v in tot.items()]
    mu = float(statistics.median(pt - sd * _ND.inv_cdf(1.0 - p) for pt, p, _, _ in anchors))
    n, d0 = min(a[2] for a in anchors), max(a[3] for a in anchors)
    lo, hi = min(a[0] for a in anchors) - sd * GAME_INTERP_MAX_SD, max(a[0] for a in anchors) + sd * GAME_INTERP_MAX_SD
    out = []
    for N in range(max(1, int(lo + 0.5)), int(hi + 0.5) + 1):
        x = N - 0.5
        if x in tot or not (lo <= x <= hi):
            continue
        p = 1.0 - _ND.cdf((x - mu) / sd)
        if P_LO <= p <= P_HI:
            dist = min(abs(x - a[0]) for a in anchors) / sd
            out.append((N, x, p, n, d0 + DISP_PER_SD * dist))
    return out


def _interp_spread_rows(sport: str, spr: dict, home_n: str, game_key: str | None = None) -> list[tuple]:
    """[(is_home, N, x, p, n_books, disp)]: P(team wins by over x) for every half-point rung near the quoted spreads (both teams)."""
    sd = GAME_SD.get(sport, {}).get("margin")
    if sport == "nba" and game_key:
        dyn = _get_nba_game_sd(game_key)
        if dyn.get("margin"):
            sd = dyn["margin"]
    elif sport == "nhl" and game_key:
        dyn = _get_nhl_game_sd(game_key)
        if dyn.get("margin"):
            sd = dyn["margin"]
    if not spr or not sd:
        return []
    anchors = [(fav, line, float(statistics.median(v)), len(v), _mad(v)) for (fav, line), v in spr.items()]
    m_homes = []
    for fav, line, p, _, _ in anchors:                       # P(fav margin > line) = p  ->  fav mean margin
        m_fav = line - sd * _ND.inv_cdf(1.0 - p)
        m_homes.append(m_fav if fav == home_n else -m_fav)
    m_home = float(statistics.median(m_homes))
    n, d0 = min(a[3] for a in anchors), max(a[4] for a in anchors)
    out = []
    for is_home in (True, False):
        m_t = m_home if is_home else -m_home
        for N in range(1, 61):
            x = N - 0.5
            # distance in LINE space: this team's rung is its line -x; each quoted spread is that team's line -L (favourite) or +L
            dist = min(abs(-x - (-a[1] if (a[0] == home_n) == is_home else a[1])) for a in anchors) / sd
            if dist > GAME_INTERP_MAX_SD:
                continue
            if any(a[1] == x and (a[0] == home_n) == is_home for a in anchors):
                continue                                      # quoted directly
            p = 1.0 - _ND.cdf((x - m_t) / sd)
            if P_LO <= p <= P_HI:
                out.append((is_home, N, x, p, n, d0 + DISP_PER_SD * dist))
    return out


def slate_rows(slate: list, sport: str, now: float, team_code=None) -> tuple[list[dict], dict]:
    """ParlayAPI slate events (h2h / spreads / totals) -> ([feed row per Kalshi market], stats). `now` stamps updated_ts.

    KX<LG>GAME   -<tail>-<CODE>    yes == CODE wins
    KX<LG>SPREAD -<tail>-<CODE><N> yes == CODE wins by over N-0.5   (== the book's CODE -(N-0.5), de-vigged against the other side)
    KX<LG>TOTAL  -<tail>-<N>       yes == Over N-0.5                (never emitted when the adapter disables totals: NHL shootout rule)
    """
    adapter = ADAPTERS[sport]
    team_code = team_code or team_code_fn(sport)
    rows: list[dict] = []
    stats = {"events": 0, "games_priced": 0, "dropped_unknown_team": 0, "dropped_thin": 0, "spread_rows": 0, "total_rows": 0,
             "spread_interp": 0, "total_interp": 0}
    for ev in slate or []:
        stats["events"] += 1
        home_n, away_n = ev.get("home_team"), ev.get("away_team")
        home, away = team_code(home_n), team_code(away_n)
        if home not in adapter.codes or away not in adapter.codes or home == away or not ev.get("commence_time"):
            stats["dropped_unknown_team"] += 1
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(_ET)
        tail = f"{start:%y}{_MON[start.month - 1]}{start:%d}{away}{home}"
        if parse_event(adapter, f"{adapter.series('ml')}-{tail}") != (start.date().isoformat(), away, home):
            stats["dropped_unknown_team"] += 1                                    # round-trip failed: do not guess
            continue
        gk = game_key(sport, start.date().isoformat(), away, home)
        books = ev.get("bookmakers", []) or []
        present = {str(b.get("key", "")).strip().lower() for b in books}
        ml: dict[str, float] = {}
        tot: dict[float, list] = {}
        spr: dict[tuple, list] = {}
        model_markets: dict[tuple[str, str, float | None], float] = {}
        if sport == "nhl":
            try:
                from nhl.game.run_daily import get_nhl_game_predictions
                nhl_preds = get_nhl_game_predictions()
                for pg in (nhl_preds or {}).get("priced_games", []):
                    if pg.get("game_key") == gk:
                        for mk in pg.get("markets", []):
                            model_markets[(mk.get("system"), mk.get("side"), mk.get("line"))] = float(mk.get("p_model"))
            except Exception:
                pass
        for b in books:
            raw = str(b.get("key", "")).strip().lower()
            if raw in BOOK_ALIASES and BOOK_ALIASES[raw] in present:
                continue                                                          # Kambi clone: count the original once
            name = canonical_book(b.get("key", "")) if raw in BOOK_ALIASES else raw
            if name in EXCLUDED_BOOKS or raw in EXCLUDED_BOOKS:
                continue
            p = _p_home(b, home_n, away_n)
            if p is not None and 0.0 < p < 1.0:
                ml[name] = p
            if "total" not in adapter.disabled:
                for pt, fair in _totals(b).items():
                    tot.setdefault(pt, []).append(fair)
            if "spread" not in adapter.disabled:
                for key, fair in _spreads(b, home_n, away_n).items():
                    spr.setdefault(key, []).append(fair)

        def add(ticker, market_class, stat, strike, vals, player=None):
            rows.append({"ticker": ticker, "sport": sport, "game_key": gk, "market_class": market_class, "player_norm": player,
                         "stat": stat, "strike": strike, "p_yes": float(statistics.median(vals)), "n_books": len(vals),
                         "disp": _mad(vals), "updated_ts": now})

        if ml:
            vals = list(ml.values())
            p_home = float(statistics.median(vals))
            for code, p in ((home, p_home), (away, 1.0 - p_home)):
                r_dict = {"ticker": f"{adapter.series('ml')}-{tail}-{code}", "sport": sport, "game_key": gk,
                          "market_class": "game_ml", "player_norm": None, "stat": "ml", "strike": 0.0, "p_yes": p,
                          "n_books": len(vals), "disp": _mad(vals), "updated_ts": now}
                p_mod = model_markets.get(("NHL_ML", "home" if code == home else "away", None))
                if p_mod is not None:
                    r_dict["p_model_yes"] = p_mod
                rows.append(r_dict)
        for pt, vals in tot.items():
            add(f"{adapter.series('total')}-{tail}-{int(pt + 0.5)}", "game_total", "total", pt, vals)
            stats["total_rows"] += 1
        for (fav_name, line), vals in spr.items():
            code = home if fav_name == home_n else away
            sp_side = "home" if code == home else "away"
            sp_line = -line if sp_side == "home" else line
            p_mod = model_markets.get(("NHL_SPREAD", sp_side, sp_line))
            ticker = f"{adapter.series('spread')}-{tail}-{code}{int(line + 0.5)}"
            r_dict = {"ticker": ticker, "sport": sport, "game_key": gk, "market_class": "game_spread", "player_norm": None,
                      "stat": "spread", "strike": line, "p_yes": float(statistics.median(vals)), "n_books": len(vals),
                      "disp": _mad(vals), "updated_ts": now}
            if p_mod is not None:
                r_dict["p_model_yes"] = p_mod
            rows.append(r_dict)
            stats["spread_rows"] += 1
        for N, x, p, n, disp in _interp_total_rows(sport, tot, game_key=gk):
            rows.append({"ticker": f"{adapter.series('total')}-{tail}-{N}", "sport": sport, "game_key": gk,
                         "market_class": "game_total", "player_norm": None, "stat": "total", "strike": x, "p_yes": p,
                         "n_books": n, "disp": disp, "pooled": True, "updated_ts": now})
            stats["total_interp"] += 1
        for is_home, N, x, p, n, disp in _interp_spread_rows(sport, spr, home_n, game_key=gk):
            rows.append({"ticker": f"{adapter.series('spread')}-{tail}-{home if is_home else away}{N}", "sport": sport,
                         "game_key": gk, "market_class": "game_spread", "player_norm": None, "stat": "spread", "strike": x,
                         "p_yes": p, "n_books": n, "disp": disp, "pooled": True, "updated_ts": now})
            stats["spread_interp"] += 1
        if ml or tot or spr:
            stats["games_priced"] += 1
        else:
            stats["dropped_thin"] += 1
    return rows, stats


def fetch_game_rows(client, sports, now: float, markets=("h2h", "spreads", "totals")) -> tuple[list[dict], dict]:
    """One slate pull per sport (1 credit PER MARKET per sport). A sport that fails is skipped and reported, never fatal."""
    rows: list[dict] = []
    stats: dict = {}
    for sport in sports:
        try:
            slate = client.get_slate(PARLAY_SPORT_KEYS[sport], markets=list(markets))
            r, s = slate_rows(slate, sport, now)
            rows += r
            stats[sport] = s
        except Exception as e:                                                  # noqa: BLE001
            stats[sport] = {"error": repr(e)[:200]}
    return rows, stats
