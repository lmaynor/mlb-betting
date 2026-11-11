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
closed -- a wrong code is a wrong price). NCAAF / NCAAB (men's) game winners, spreads and totals ARE covered as of
2026-11-10 (ev.sports.ncaaf / ncaab adapters + ev.sports.ncaa_teams census tables; the live-slate name path resolves
only verbatim Kalshi school names -- the priced route is the BettingPros banked feed in ev.kalshi_game_banked).
Not covered (stay unpriced): NCAAWB (women's) / MLS game winners, spreads, totals.
NFL ties settle $0.50 per team on Kalshi; a two-way de-vig ignores them (a ~0.5pp effect, inside the safety pad).
"""

from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ev.kalshi_map import game_key, parse_event
from ev.props import DFS_BOOKS
from ev.sports import ADAPTERS, kalshi_code
from mlb.execution.kalshi.mapping import MapFail
from mlb_core.alerts.discord_routes import BOOK_ALIASES, canonical_book
from mlb_core.odds.utils import american_to_implied_prob, devig_three_way, devig_two_way

_ET = ZoneInfo("America/New_York")
_ND = statistics.NormalDist()
log = logging.getLogger("ev.kalshi_game_legs")

# Rung interpolation for spreads / totals. Books quote one or two lines per game; Kalshi lists EVERY half-point rung ("wins by over
# 3.5 / 4.5 / ... / 17.5"). Between and beside the quoted lines the margin / total is modelled as Normal(mu, sd) with mu fitted to the
# books' de-vigged probabilities. UNVALIDATED: the sd values are rough league-typical figures, not fitted to this repo's data; rows are
# tagged pooled=True, inherit the thinnest anchor's n_books, and carry extra dispersion that grows with the distance from the nearest
# quoted line, so the leg gate rejects the far ones. Nothing beyond GAME_INTERP_MAX_SD standard deviations from a quoted line is emitted.
GAME_SD = {"nfl": {"margin": 13.5, "total": 12.0}, "nba": {"margin": 12.0, "total": 18.0}, "nhl": {"margin": 2.3, "total": 2.5},
           # NCAA (2026-11-10): UNVALIDATED league-typical starting points in the same sense as the rows above -- NOT fitted.
           # College margins run wider than the pros' (blowout-heavy schedules): FBS margin sd is commonly put near 15-16
           # points with totals near 13-14; D1 basketball margins near 11 with totals near 13-14. No settled-outcome fit
           # exists in this repo for either (the banked NCAA files carry lines, not results), so treat exactly like the
           # pro entries: pooled rows inherit the thinnest anchor's books and grow dispersion with distance, and the far
           # rungs fail the leg gate by construction. Fit before trusting; see the coverage measurement of 2026-11-10.
           "ncaaf": {"margin": 15.5, "total": 13.5}, "ncaab": {"margin": 11.0, "total": 13.5}}
GAME_INTERP_MAX_SD = 1.0
DISP_PER_SD = 0.02
P_LO, P_HI = 0.01, 0.99
PARLAY_SPORT_KEYS = {"nfl": "americanfootball_nfl", "nba": "basketball_nba", "nhl": "icehockey_nhl",
                     "soccer_epl": "soccer_epl", "soccer_ucl": "soccer_uefa_champs_league"}
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
    elif sport in ("ncaaf", "ncaab"):
        # Only a VERBATIM Kalshi school name resolves (ev.sports.ncaa_teams census). Book feeds that carry mascots or
        # their own spellings fail closed here; the BettingPros banked path does not come through this function at all
        # (it translates BettingPros CODES through the crosswalk in ev.kalshi_game_banked).
        from ev.sports.ncaa_teams import name_to_kalshi
        return lambda name: name_to_kalshi(sport, name)
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


def _p_three_way(book: dict, home: str, away: str):
    """One book's own de-vigged (P(home), P(draw), P(away)) from a THREE-outcome h2h market (soccer 1X2), or None.

    The 2-outcome guard in _p_home refuses these markets outright (a Draw outcome means the proposition is
    three-way); here all three prices are de-vigged TOGETHER with devig_three_way(method="power"), so the draw
    leg carries its proper share of the overround instead of being dropped or folded into the sides."""
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "h2h":
            continue
        outcomes = mk.get("outcomes", []) or []
        if len(outcomes) != 3:
            return None
        px = {}
        for o in outcomes:
            name = str(o.get("name", "")).strip()
            if o.get("price") is None:
                continue
            if name == home:
                px["home"] = int(o["price"])
            elif name == away:
                px["away"] = int(o["price"])
            elif name.lower() in ("draw", "tie"):
                px["draw"] = int(o["price"])
        if len(px) != 3:
            return None
        try:
            return devig_three_way(american_to_implied_prob(px["home"]), american_to_implied_prob(px["draw"]),
                                   american_to_implied_prob(px["away"]), method="power")
        except ValueError:
            return None
    return None


def _btts(book: dict):
    """One book's own de-vigged P(BTTS Yes) from its btts market (Yes / No outcomes), or None."""
    for mk in book.get("markets", []) or []:
        if mk.get("key") != "btts":
            continue
        px = {}
        for o in mk.get("outcomes", []) or []:
            side = str(o.get("name", "")).strip().lower()
            if side in ("yes", "no") and o.get("price") is not None:
                px[side] = int(o["price"])
        if len(px) == 2:
            return _fair_pair(px["yes"], px["no"])
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


# ---- model payloads: read ONCE per process (TTL), never recomputed on a miss, indexed by a canonical game key --------------------
_PRED_TTL_S = 300.0
_PRED_CACHE: dict = {}          # sport -> (monotonic ts, {canonical game_key: priced game dict})


def canon_game_key(sport: str, gk: str) -> str:
    """'nhl:2026-01-10:LAK@TBL' and 'nhl:2026-01-10:LA@TB' are the same game: map both teams through the league's Kalshi
    code table (ev.sports.kalshi_code). The NHL model keys games by NHL-API abbreviations (LAK/NJD/SJS/TBL) while the Kalshi
    side uses LA/NJ/SJ/TB; an unmappable key is returned unchanged."""
    try:
        sp, day, teams = str(gk).split(":", 2)
        away, home = teams.split("@", 1)
        a, h = kalshi_code(sport, away), kalshi_code(sport, home)
        if a and h:
            return f"{sp}:{day}:{a}@{h}"
    except ValueError:
        pass
    return str(gk)


def _priced_games(sport: str) -> dict:
    """{canonical game_key: priced_game dict} from the stored model payload, cached per process for _PRED_TTL_S."""
    hit = _PRED_CACHE.get(sport)
    if hit is not None and time.monotonic() - hit[0] < _PRED_TTL_S:
        return hit[1]
    index: dict = {}
    try:
        if sport == "nba":
            from nba.game.run_daily import read_cached_game_predictions as reader
        else:
            from nhl.game.run_daily import read_cached_game_predictions as reader
        for g in (reader() or {}).get("priced_games", []) or []:
            if isinstance(g, dict) and g.get("game_key"):
                index[canon_game_key(sport, g["game_key"])] = g
    except Exception:                                                                   # noqa: BLE001
        log.exception("%s game model payload unavailable; game legs use league-typical sd", sport)
    _PRED_CACHE[sport] = (time.monotonic(), index)
    return index


def _reset_pred_cache() -> None:
    _PRED_CACHE.clear()


def _get_nba_game_sd(game_key: str) -> dict[str, float]:
    """Model outcome sd (margin & total) for an NBA game. The model's sigma_* are Student-t SCALES: the true sd is
    scale * sqrt(nu / (nu - 2)). Payloads carry sd_* already; an older payload is converted here."""
    g = _priced_games("nba").get(canon_game_key("nba", game_key))
    if g:
        try:
            from nba.game.model import NU_MARGIN, NU_TOTAL, t_scale_to_sd
            sd_m = g.get("sd_margin") or t_scale_to_sd(float(g["sigma_margin"]), NU_MARGIN)
            sd_t = g.get("sd_total") or t_scale_to_sd(float(g["sigma_total"]), NU_TOTAL)
            return {"margin": float(sd_m), "total": float(sd_t)}
        except (KeyError, TypeError, ValueError):
            log.warning("nba priced game %s has no usable sigma/sd fields; using league-typical sd", game_key)
    else:
        log.info("no NBA model payload for %s; using league-typical sd %s", game_key, GAME_SD["nba"])
    return GAME_SD.get("nba", {})


def _get_nhl_game_sd(game_key: str) -> dict[str, float]:
    """Model OUTCOME sd (margin & total) for an NHL game, from the score matrix (payload fields sd_margin / sd_total).
    sigma_margin / sigma_total in the payload are Kalman parameter-uncertainty stds (~0.7) and must never be used as an
    outcome dispersion; a payload without sd_* (written before 2026-11-01) falls back to the league-typical defaults."""
    g = _priced_games("nhl").get(canon_game_key("nhl", game_key))
    if g:
        try:
            sd_m, sd_t = float(g["sd_margin"]), float(g["sd_total"])
            if sd_m > 0 and sd_t > 0:
                return {"margin": sd_m, "total": sd_t}
        except (KeyError, TypeError, ValueError):
            pass
        log.warning("nhl priced game %s lacks outcome sd_margin/sd_total (legacy payload); using league-typical sd", game_key)
    else:
        log.info("no NHL model payload for %s; using league-typical sd %s", game_key, GAME_SD["nhl"])
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


def slate_rows(slate: list, sport: str, now: float, team_code=None, valid_codes=None) -> tuple[list[dict], dict]:
    """ParlayAPI slate events (h2h / spreads / totals) -> ([feed row per Kalshi market], stats). `now` stamps updated_ts.

    `valid_codes` is the set of team codes an event's resolved teams must belong to; it defaults to the adapter's
    (parse) codes. NCAA callers pass the FULL census instead: their codes arrive pre-translated through the
    name-verified crosswalk, never by splitting a separator-free ticker blob, so parse-dropped schools price
    normally there while parse_event below still round-trips the built tickers against the adapter's parse set
    (a blob that only splits through a dropped code is still refused, never guessed).

    KX<LG>GAME   -<tail>-<CODE>    yes == CODE wins
    KX<LG>SPREAD -<tail>-<CODE><N> yes == CODE wins by over N-0.5   (== the book's CODE -(N-0.5), de-vigged against the other side)
    KX<LG>TOTAL  -<tail>-<N>       yes == Over N-0.5                (never emitted when the adapter disables totals: NHL shootout rule)
    """
    adapter = ADAPTERS[sport]
    valid = adapter.codes if valid_codes is None else valid_codes
    team_code = team_code or team_code_fn(sport)
    rows: list[dict] = []
    stats = {"events": 0, "games_priced": 0, "dropped_unknown_team": 0, "dropped_thin": 0, "spread_rows": 0, "total_rows": 0,
             "spread_interp": 0, "total_interp": 0}
    for ev in slate or []:
        stats["events"] += 1
        home_n, away_n = ev.get("home_team"), ev.get("away_team")
        home, away = team_code(home_n), team_code(away_n)
        if home not in valid or away not in valid or home == away or not ev.get("commence_time"):
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
            pg = _priced_games("nhl").get(canon_game_key("nhl", gk))
            for mk in (pg or {}).get("markets", []) or []:
                try:
                    model_markets[(mk.get("system"), mk.get("side"), mk.get("line"))] = float(mk.get("p_model"))
                except (TypeError, ValueError):
                    continue
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
            add(f"{adapter.series_prefix('total')}-{tail}-{int(pt + 0.5)}", "game_total", "total", pt, vals)
            stats["total_rows"] += 1
        for (fav_name, line), vals in spr.items():
            code = home if fav_name == home_n else away
            sp_side = "home" if code == home else "away"
            sp_line = -line      # the favourite's own line is -L whichever side it is (an away favourite is the away -L market)
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
            rows.append({"ticker": f"{adapter.series_prefix('total')}-{tail}-{N}", "sport": sport, "game_key": gk,
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


# ---- soccer: three-way match contracts (EPL / UCL) ---------------------------------------------------------------------
_SOCCER_PRED_CACHE: dict = {}          # (monotonic ts, {game_key: priced game dict})


def _soccer_priced_games() -> dict:
    """{game_key: priced game} from the soccer model payload (soccer.game.run_daily), cached per process."""
    hit = _SOCCER_PRED_CACHE.get("payload")
    if hit is not None and time.monotonic() - hit[0] < _PRED_TTL_S:
        return hit[1]
    index: dict = {}
    try:
        from soccer.game.run_daily import read_cached_game_predictions
        for g in (read_cached_game_predictions() or {}).get("priced_games", []) or []:
            if isinstance(g, dict) and g.get("game_key"):
                index[g["game_key"]] = g
    except Exception:                                                                   # noqa: BLE001
        log.exception("soccer game model payload unavailable; soccer legs carry no p_model_yes")
    _SOCCER_PRED_CACHE["payload"] = (time.monotonic(), index)
    return index


def soccer_slate_rows(slate: list, league: str, now: float) -> tuple[list[dict], dict]:
    """ParlayAPI soccer slate (h2h 3-way / totals / btts) -> Kalshi fair-book rows for one league.

    Emits, per game: the three match contracts (home / TIE / away) from the median of each book's own power
    de-vigged triple (the medians are renormalized to sum to 1), the quoted totals rungs, and a BTTS row.
    Team identity resolves through the Reep bridge (soccer.data.reep_register, provider "parlayapi") and the
    ticker tail round-trips through the league adapter's strict parser -- an unresolved club drops the game.
    Kalshi soccer has no verified margin grammar, so no spread rows are emitted (adapter.disabled). BTTS rows
    use a {prefix}BTTS- series name: no Kalshi BTTS series has been verified, so those rows serve the fair
    book / board only and simply never match an RFQ leg until a real series is confirmed by the probe.
    Dixon-Coles model probabilities ride along as p_model_yes when the soccer payload has the game.
    """
    from ev.sports.soccer import LEAGUE_ADAPTERS
    from soccer.data.reep_register import resolve_club

    adapter = LEAGUE_ADAPTERS[league]
    model = _soccer_priced_games()
    rows: list[dict] = []
    stats = {"events": 0, "games_priced": 0, "dropped_unknown_team": 0, "dropped_thin": 0,
             "tie_rows": 0, "total_rows": 0, "btts_rows": 0}
    for ev in slate or []:
        stats["events"] += 1
        home_n, away_n = ev.get("home_team"), ev.get("away_team")
        home = resolve_club(home_n, league=league, provider="parlayapi")
        away = resolve_club(away_n, league=league, provider="parlayapi")
        if home not in adapter.codes or away not in adapter.codes or home == away or not ev.get("commence_time"):
            stats["dropped_unknown_team"] += 1
            continue
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(_ET)
        tail = f"{start:%y}{_MON[start.month - 1]}{start:%d}{away}{home}"
        if parse_event(adapter, f"{adapter.series('ml')}-{tail}") != (start.date().isoformat(), away, home):
            stats["dropped_unknown_team"] += 1                                    # round-trip failed: do not guess
            continue
        gk = game_key("soccer", start.date().isoformat(), away, home)
        pg = model.get(gk)
        triples: dict[str, tuple] = {}
        tot: dict[float, list] = {}
        btts_vals: list[float] = []
        for b in ev.get("bookmakers", []) or []:
            raw = str(b.get("key", "")).strip().lower()
            name = canonical_book(b.get("key", "")) if raw in BOOK_ALIASES else raw
            if name in EXCLUDED_BOOKS or raw in EXCLUDED_BOOKS:
                continue
            t = _p_three_way(b, home_n, away_n)
            if t is not None and all(0.0 < p < 1.0 for p in t):
                triples[name] = t
            for pt, fair in _totals(b).items():
                tot.setdefault(pt, []).append(fair)
            bt = _btts(b)
            if bt is not None and 0.0 < bt < 1.0:
                btts_vals.append(bt)
        if triples:
            legs = [float(statistics.median(v[i] for v in triples.values())) for i in range(3)]
            s = sum(legs)
            legs = [v / s for v in legs]
            disps = [_mad([v[i] for v in triples.values()]) for i in range(3)]
            model_p = {"HOME": (pg or {}).get("p_home"), "TIE": (pg or {}).get("p_draw"),
                       "AWAY": (pg or {}).get("p_away")}
            for suffix, p, disp in ((home, legs[0], disps[0]), ("TIE", legs[1], disps[1]), (away, legs[2], disps[2])):
                r = {"ticker": f"{adapter.series('ml')}-{tail}-{suffix}", "sport": "soccer", "game_key": gk,
                     "market_class": "game_ml", "player_norm": None, "stat": "ml", "strike": 0.0,
                     "p_yes": p, "n_books": len(triples), "disp": disp, "updated_ts": now}
                mp = model_p["HOME" if suffix == home else "AWAY" if suffix == away else "TIE"]
                if mp is not None:
                    r["p_model_yes"] = float(mp)
                rows.append(r)
            stats["tie_rows"] += 1
        for pt, vals in tot.items():
            r = {"ticker": f"{adapter.series('total')}-{tail}-{int(pt + 0.5)}", "sport": "soccer", "game_key": gk,
                 "market_class": "game_total", "player_norm": None, "stat": "total", "strike": pt,
                 "p_yes": float(statistics.median(vals)), "n_books": len(vals), "disp": _mad(vals),
                 "updated_ts": now}
            mt = ((pg or {}).get("totals") or {}).get(f"{pt:g}")
            if mt and mt.get("p_over") is not None:
                r["p_model_yes"] = float(mt["p_over"])
            rows.append(r)
            stats["total_rows"] += 1
        if btts_vals:
            r = {"ticker": f"{adapter.kalshi_prefix}BTTS-{tail}-YES", "sport": "soccer", "game_key": gk,
                 "market_class": "game_btts", "player_norm": None, "stat": "btts", "strike": 0.0,
                 "p_yes": float(statistics.median(btts_vals)), "n_books": len(btts_vals),
                 "disp": _mad(btts_vals), "updated_ts": now}
            if pg and pg.get("p_btts_yes") is not None:
                r["p_model_yes"] = float(pg["p_btts_yes"])
            rows.append(r)
            stats["btts_rows"] += 1
        if triples or tot or btts_vals:
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
            if sport in ("soccer_epl", "soccer_ucl"):
                slate = client.get_slate(PARLAY_SPORT_KEYS[sport], markets=["h2h", "totals", "btts"])
                r, s = soccer_slate_rows(slate, sport.split("_", 1)[1], now)
            else:
                slate = client.get_slate(PARLAY_SPORT_KEYS[sport], markets=list(markets))
                r, s = slate_rows(slate, sport, now)
            rows += r
            stats[sport] = s
        except Exception as e:                                                  # noqa: BLE001
            stats[sport] = {"error": repr(e)[:200]}
    return rows, stats
