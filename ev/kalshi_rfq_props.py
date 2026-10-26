"""
ev.kalshi_rfq_props -- player-prop fair values for the Kalshi RFQ fair book, resolved from the TICKER alone.

The RFQ flow's biggest leg family is NBA player props (~78% of the first demo tape's legs), then NFL/NHL props. Kalshi's REST catalog is
neither needed nor used: a leg's market_ticker carries sport, game, team, player code and N+ rung (ev.kalshi_props.parse_prop_ticker),
and the sportsbook side is the ALREADY-BANKED prop feeds (ev.daily_projections.load_events: ParlayAPI bank + BettingPros live), so this
costs ZERO ParlayAPI credits.

    PropFairIndex.resolve(parsed ticker) -> LegFairValue | None
      exact rung   >= 2 real two-sided books quote that exact strike -> their de-vigged median (pooled=False)
      curve        otherwise a monotone curve over the player's multi-book rungs (ev.odds_board.fit_curve) when it can speak at the
                   strike (interpolates; extrapolates at most the anchors' own span) -> pooled=True, n_books = the thinnest anchor
      thin         a single-book exact rung is returned with n_books=1 so the leg gate reports REJ_LOW_BOOKS, not "unmapped"
      None         player not priced in that game / ambiguous player code / team hint contradicts the ticker's team

Fail closed everywhere: two candidate players for one code, a team hint that contradicts the ticker, an unresolvable team, or a started
game all return None. DFS apps and Kalshi are never in a consensus (leave-Kalshi-out).
"""

from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timezone

from ev.kalshi_props import ParsedPropTicker, kalshi_player_codes
from ev.odds_board import CURVE_MIN_BOOKS, fit_curve
from ev.props import DFS_BOOKS, parse_event_props
from ev.sports import kalshi_code
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

log = logging.getLogger("ev.kalshi_rfq_props")
EXCLUDED_BOOKS = DFS_BOOKS | {"kalshi"}


def _mad(x) -> float:
    m = statistics.median(x)
    return float(statistics.median(abs(v - m) for v in x))


class _PlayerStat:
    """One player's rungs for one stat in one game."""
    __slots__ = ("rungs", "curve", "anchor_n", "anchor_disp")

    def __init__(self):
        self.rungs: dict[float, tuple[float, int, float]] = {}      # point -> (median P(over), n two-sided books, MAD)
        self.curve = None
        self.anchor_n = 0
        self.anchor_disp = 0.0


class PropFairIndex:
    def __init__(self, sport: str, updated_ts: float):
        self.sport, self.updated_ts = sport, updated_ts
        self.games: dict[str, dict] = {}          # game_key -> {"codes": {code: [(player_norm, team_set)]}, "data": {(pn, stat): _PlayerStat}}

    def __len__(self) -> int:
        return sum(len(g["data"]) for g in self.games.values())

    def resolve(self, p: ParsedPropTicker):
        """ParsedPropTicker -> LegFairValue | None (see the module docstring)."""
        return self.resolve_why(p)[0]

    def resolve_why(self, p: ParsedPropTicker):
        """-> (LegFairValue | None, why). `why` explains a miss ('no_game' / 'no_player' / 'ambiguous_player' / 'team_mismatch' /
        'no_stat' / 'no_rung') and is 'exact' | 'curve' | 'thin' on a hit; it lands in the tape's per-leg status."""
        from ev.kalshi_rfq_pricer import LegFairValue
        g = self.games.get(p.game_key)
        if g is None:
            return None, "no_game"
        entries = g["codes"].get(p.player_code, [])
        if not entries:
            return None, "no_player"
        cands = {pn for pn, teams in entries if not teams or p.team in teams}
        if not cands:
            return None, "team_mismatch"
        if len(cands) != 1:                                         # two players share the code: never guess
            return None, "ambiguous_player"
        pn = next(iter(cands))
        ps = g["data"].get((pn, p.stat))
        if ps is None:
            return None, "no_stat"
        exact = ps.rungs.get(p.strike)
        if exact is not None and exact[1] >= CURVE_MIN_BOOKS:
            p_over, n, disp, pooled, why = exact[0], exact[1], exact[2], False, "exact"
        elif ps.curve is not None and ps.curve(p.strike) is not None:
            p_over, n, disp, pooled, why = ps.curve(p.strike), ps.anchor_n, ps.anchor_disp, True, "curve"
        elif exact is not None:
            p_over, n, disp, pooled, why = exact[0], exact[1], exact[2], False, "thin"
        else:
            return None, "no_rung"
        return LegFairValue(ticker=p.ticker, sport=p.sport, game_key=p.game_key, market_class="player_prop", player_norm=pn,
                            stat=p.stat, strike=p.strike, p_cons_yes=float(p_over), n_books=int(n), disp=float(disp),
                            p_model_yes=None, pooled=pooled, updated_ts=self.updated_ts), why


def build_sport_prop_index(sport: str, events: list | None, now: datetime | float, updated_ts: float | None = None):
    """Banked prop events (ParlayAPI-shaped; ev.daily_projections.load_events) -> (PropFairIndex, stats)."""
    from ev.daily_projections import canon_team, game_key_of
    now_dt = now if isinstance(now, datetime) else datetime.fromtimestamp(float(now), timezone.utc)
    idx = PropFairIndex(sport, updated_ts if updated_ts is not None else now_dt.timestamp())
    stats = {"events": 0, "started": 0, "unknown_team": 0, "players": 0}
    for ev in events or []:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now_dt:
            stats["started"] += 1
            continue
        away = kalshi_code(sport, canon_team(sport, ev.get("away_team")))
        home = kalshi_code(sport, canon_team(sport, ev.get("home_team")))
        if not away or not home or away == home:
            stats["unknown_team"] += 1
            continue
        stats["events"] += 1
        gk = game_key_of(sport, start, away, home)
        quotes, _ = parse_event_props(ev, sport)
        per_rung: dict = {}                 # (pn, stat, point) -> {book: fair P(over)}
        names: dict = {}
        hints: dict = {}                    # pn -> set of Kalshi team codes the books attached
        for q in quotes:
            if q.book in EXCLUDED_BOOKS:
                continue
            names.setdefault(q.player_norm, q.player)
            for h in ([q.team_hint] if q.team_hint else []):
                c = kalshi_code(sport, canon_team(sport, h))
                if c:
                    hints.setdefault(q.player_norm, set()).add(c)
            if q.over is None or q.under is None:
                continue
            fair, _ = devig_two_way(american_to_implied_prob(q.over), american_to_implied_prob(q.under), "proportional")
            per_rung.setdefault((q.player_norm, q.stat, q.point), {}).setdefault(q.book, float(fair))
        game = idx.games.setdefault(gk, {"codes": {}, "data": {}})
        for pn, display in names.items():
            teams = hints.get(pn, set())
            for code in kalshi_player_codes(pn, display):
                game["codes"].setdefault(code, []).append((pn, teams))
        by_ps: dict = {}
        for (pn, stat, point), books in per_rung.items():
            vals = list(books.values())
            ps = game["data"].setdefault((pn, stat), _PlayerStat())
            ps.rungs[point] = (float(statistics.median(vals)), len(vals), _mad(vals))
            by_ps[(pn, stat)] = ps
        for (pn, stat), ps in by_ps.items():
            anchors = [(pt, r[0], r[1]) for pt, r in ps.rungs.items() if r[1] >= CURVE_MIN_BOOKS]
            if not anchors:
                top = max(ps.rungs.items(), key=lambda kv: kv[1][1])          # single well-covered line (yardage stats only)
                anchors = [(top[0], top[1][0], top[1][1])]
            ps.curve = fit_curve(anchors, stat)
            if ps.curve is not None:
                ps.anchor_n = min(a[2] for a in anchors)
                ps.anchor_disp = max(ps.rungs[a[0]][2] for a in anchors)
        stats["players"] = len({k[0] for g in idx.games.values() for k in g["data"]})
    return idx, stats


def load_all_prop_indices(sports=("nfl", "nba", "nhl"), now: datetime | None = None, max_age_min: float = 240.0):
    """Zero-credit: read every sport's banked prop events and index them. -> ({sport: PropFairIndex}, {sport: stats}). One failing
    sport is reported and skipped, never fatal."""
    from ev.daily_projections import load_events
    now = now or datetime.now(timezone.utc)
    out: dict = {}
    stats: dict = {}
    for sport in sports:
        try:
            events, meta = load_events(sport, now, max_age_min)
            if not events:
                stats[sport] = {"events": 0, "reason": (meta or {}).get("reason")}
                continue
            age = (meta or {}).get("age_min")
            updated = now.timestamp() - float(age) * 60.0 if age is not None else now.timestamp()
            out[sport], stats[sport] = build_sport_prop_index(sport, events, now, updated)
            stats[sport]["age_min"] = age
            stats[sport]["lines_source"] = (meta or {}).get("lines_source")
        except Exception as e:                                                  # noqa: BLE001
            log.warning("prop index for %s failed: %r", sport, e)
            stats[sport] = {"error": repr(e)[:200]}
    return out, stats
