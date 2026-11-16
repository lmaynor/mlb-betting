"""
ev.res11_pop -- RES-11 Stage 1: intraday distribution-pop replay primitives.

Implements RUL-20261116-intraday-pop-approach exactly as written:

  pop signal      Delta_pop(b,t) = P_dist(side) - q_book(b,side,t), q_book the RAW
                  vig-inclusive implied probability of the bettable price.
                  Delta grid {0.030, 0.045, 0.060, 0.080} (0.045 primary), hard cap
                  Delta_pop <= 0.120, model-vs-peer gap cap P_dist - P_peer,med <= 0.060,
                  American odds window [-250, +300], target quote age <= 300 s, two-way
                  quote with hold <= 7.5% (<= 9.5% on FanDuel / Hard Rock alt ladders),
                  evaluation window [T-14h, T-30m], first-sighting t0 per
                  (book, game, player, stat, line, side) with a 30-minute re-arm after
                  the book reprices back inside threshold.
  corroboration   >= 2 active peer books (or >= 1 sharp book in {pinnacle, novig,
                  prophetx}) with P_peer,med - q_book >= 0.015. Mandatory.
  guards          G1 window (hard), G2 injury state (hard, NBA banked / NHL not banked),
                  G3 NHL goalie gate (hard), G4 line-move velocity (hard), G5 cross-book
                  dispersion (hard), G6 stale-opener (replay FLAG), G7 window tags (FLAG).
  variants        1-snapshot (t0) and 2-snapshot persistence (t1) triggers.
  metrics         closing price-ratio CLV, dislocation retention R_ret, post-sighting
                  consensus drift, settled flat ROI (0.37% drag on exchange venues),
                  fired-slice calibration vs P_dist and vs P_cons,close -- all with
                  game-day-clustered 95% CIs on G-1 df.

P_dist is the frozen production distribution as served on the day's production board
(EVSweep archive): (mu, line, p_over_model) recovered to (mu, dispersion) under the
production family -- NegBin for rebounds / threes / SOG / saves, Normal for points (the
LAD-05 / NBA-05 recovery convention, points plausibility filter sd/mu in [0.15, 1.5]).

No order path, no writes to any running program's state: read-only analysis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from scipy import stats as _stats

from mlb_core.odds.utils import american_to_decimal

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")

# --------------------------------------------------------------------------- ruling constants
DELTAS = (0.030, 0.045, 0.060, 0.080)
PRIMARY_DELTA = 0.045
POP_CAP = 0.120                    # hard upper divergence cap
PEER_GAP_CAP = 0.060               # P_dist - P_peer,med cap
CORROBORATION_MIN = 0.015          # P_peer,med - q_book >= 1.5pp
ODDS_MIN_AM, ODDS_MAX_AM = -250, 300
QUOTE_AGE_MAX_SEC = 300.0
HOLD_MAX = 0.075
HOLD_MAX_ALT_LADDER = 0.095
WINDOW_MIN_MINUTES = 30.0          # T-30m
WINDOW_MAX_MINUTES = 840.0         # T-14h
REARM_MINUTES = 30.0
PEER_AGE_MAX_MIN = 25.0            # "active" peers (G5 / corroboration)
SHARP_BOOKS = {"pinnacle", "novig", "prophetx"}
EXCHANGE_BOOKS = {"novig", "prophetx", "kalshi"}
EXCHANGE_DRAG = 0.0037
PERSIST_MIN_GAP_MIN = 5.0          # 2-snapshot variant: snapshots >= 5m apart
PERSIST_ADVERSE_MAX_PP = 0.010     # ... with no adverse consensus move >= 1.0pp

NBA_MARKETS = {"player_points": "points", "player_rebounds": "rebounds",
               "player_threes": "threes", "player_threes_made": "threes"}
NHL_MARKETS = {"player_shots_on_goal": "sog", "player_saves": "saves"}
ALT_MARKET_SUFFIX = "_alternate"

STATS_BY_SPORT = {"nba": ("points", "rebounds", "threes"), "nhl": ("sog", "saves")}


def utc(ts) -> datetime:
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- distribution math
def negbin_sf(line: float, mu: float, alpha: float) -> float:
    """P(X > line) for X ~ NegBin(mean=mu, var = mu + alpha*mu^2). X integer-valued."""
    if mu <= 0 or alpha <= 0:
        return 0.0
    r = 1.0 / alpha
    p = r / (r + mu)
    k = math.floor(line)
    return float(_stats.nbinom.sf(k, r, p))


def negbin_pmf_at(line: float, mu: float, alpha: float) -> float:
    if mu <= 0 or alpha <= 0:
        return 0.0
    r = 1.0 / alpha
    p = r / (r + mu)
    return float(_stats.nbinom.pmf(int(round(line)), r, p))


def normal_sf(line: float, mu: float, sd: float) -> float:
    if sd <= 0:
        return 0.0
    return float(_stats.norm.sf((line - mu) / sd))


def dist_sf(family: str, mu: float, disp: float, line: float) -> float:
    """P(X > line) under the recovered production distribution."""
    return negbin_sf(line, mu, disp) if family == "negbin" else normal_sf(line, mu, disp)


def dist_side_prob(family: str, mu: float, disp: float, line: float, side: str) -> float:
    """P(win on `side`) at `line`. Push mass (integer lines, count stats) belongs to
    neither side; for the Normal points form the push mass is zero by construction."""
    p_over = dist_sf(family, mu, disp, line)
    if side == "over":
        return p_over
    if family == "negbin" and float(line).is_integer():
        return 1.0 - p_over - negbin_pmf_at(line, mu, disp)
    return 1.0 - p_over


def invert_dispersion(family: str, mu, line, p_target):
    """Recover production dispersion from (mu, line, p_over_model). None if not
    identifiable. Mirrors the LAD-05 / RES-10 recovery (alt-rung analyze)."""
    if mu is None or line is None or p_target is None:
        return None
    if not (0.001 < p_target < 0.999) or mu <= 0:
        return None
    if family == "negbin":
        # sf(line) is NOT monotone in alpha over the full range (extreme dispersion
        # piles mass at zero and the upper tail falls again), so a naive bracket on
        # [1e-3, 3] misses identifiable rows. Scan a log grid and take the FIRST root
        # -- the branch continuous with alpha -> 0, where production alphas live.
        import numpy as np
        from scipy.optimize import brentq
        grid = np.logspace(-3, math.log10(3.0), 240)
        r = 1.0 / grid
        vals = _stats.nbinom.sf(math.floor(line), r, r / (r + mu)) - p_target
        for i in range(len(grid) - 1):
            if vals[i] == 0:
                return float(grid[i])
            if vals[i] * vals[i + 1] < 0:
                try:
                    return float(brentq(lambda a: negbin_sf(line, mu, a) - p_target,
                                        grid[i], grid[i + 1], xtol=1e-6))
                except Exception:
                    return None
        return None
    if line == mu:
        return None
    z = _stats.norm.ppf(1.0 - p_target)
    if z == 0:
        return None
    sd = (line - mu) / z
    return float(sd) if sd > 0 else None


# --------------------------------------------------------------------------- quotes
@dataclass
class Quote:
    event_id: str
    commence: datetime
    home: str
    away: str
    book: str
    stat: str
    player: str
    player_norm: str
    line: float
    over_am: float
    under_am: float
    age_sec: float | None          # snapshot ts - market last_update
    alt_ladder: bool = False       # quote came from an *_alternate market key

    @property
    def q_over(self) -> float:
        return 1.0 / american_to_decimal(self.over_am)

    @property
    def q_under(self) -> float:
        return 1.0 / american_to_decimal(self.under_am)

    @property
    def hold(self) -> float:
        return self.q_over + self.q_under - 1.0

    def fair_over(self) -> float:
        qo, qu = self.q_over, self.q_under
        return qo / (qo + qu)

    def q_side(self, side: str) -> float:
        return self.q_over if side == "over" else self.q_under

    def am_side(self, side: str) -> float:
        return self.over_am if side == "over" else self.under_am


@dataclass
class Snapshot:
    ts: datetime
    sport: str
    quotes: list = field(default_factory=list)      # list[Quote], two-sided cells only
    n_events: int = 0
    n_one_sided: int = 0


def _age_sec(ts: datetime, last_update, book_last_update) -> float | None:
    for cand in (last_update, book_last_update):
        if cand:
            try:
                return max(0.0, (ts - utc(cand)).total_seconds())
            except (ValueError, TypeError):
                continue
    return None


def parse_snapshot(payload: list, ts: datetime, sport: str) -> Snapshot:
    """One OddsAccum raw payload -> two-sided Quote cells for the in-scope markets,
    keeping every line (not just the modal line) and the market's own last_update age."""
    markets = NBA_MARKETS if sport == "nba" else NHL_MARKETS
    snap = Snapshot(ts=ts, sport=sport)
    for ev in payload or []:
        try:
            commence = utc(ev["commence_time"])
        except (KeyError, ValueError, TypeError):
            continue
        event_id = str(ev.get("canonical_event_id") or ev.get("id") or "")
        if not event_id:
            continue
        snap.n_events += 1
        home, away = str(ev.get("home_team", "")), str(ev.get("away_team", ""))
        for bk in ev.get("bookmakers", []):
            book = str(bk.get("key", "")).lower()
            if not book:
                continue
            for m in bk.get("markets", []):
                mk = str(m.get("key", ""))
                alt = mk.endswith(ALT_MARKET_SUFFIX)
                stat = markets.get(mk[: -len(ALT_MARKET_SUFFIX)] if alt else mk)
                if stat is None:
                    continue
                age = _age_sec(ts, m.get("last_update"), bk.get("last_update"))
                per: dict = {}
                for o in m.get("outcomes", []):
                    name = str(o.get("name", ""))
                    if name.startswith("Over "):
                        side_i, player = 0, o.get("description") or name[5:]
                    elif name.startswith("Under "):
                        side_i, player = 1, o.get("description") or name[6:]
                    else:
                        continue
                    if o.get("price") is None or o.get("point") is None or not player:
                        continue
                    cell = per.setdefault((player, round(float(o["point"]), 2)), [None, None])
                    if cell[side_i] is None:
                        cell[side_i] = float(o["price"])
                from ev.props import norm_name
                for (player, line), (ov, un) in per.items():
                    if ov is None or un is None:
                        snap.n_one_sided += 1
                        continue
                    snap.quotes.append(Quote(
                        event_id=event_id, commence=commence, home=home, away=away,
                        book=book, stat=stat, player=player, player_norm=norm_name(player),
                        line=line, over_am=ov, under_am=un, age_sec=age, alt_ladder=alt))
    return snap


def snap_ts(day: str, fname: str) -> datetime | None:
    """Accumulator file props_HHMM.json: HHMM is America/New_York wall time on the
    folder day (the production AUT-01 stream convention; validated against the
    payloads' own last_update timestamps, which cohere only under this reading)."""
    import re
    m = re.match(r"^props_(\d{2})(\d{2})\.json$", fname)
    if not m:
        return None
    d = datetime.strptime(day, "%Y-%m-%d")
    local = datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=ET)
    return local.astimezone(timezone.utc)


# --------------------------------------------------------------------------- peer context
@dataclass
class PeerContext:
    n_peers: int
    peer_med_fair_over: float | None     # median leave-target-out devigged fair P(over)
    peer_mad: float | None               # MAD of peer fairs around their median
    n_sharp_peers: int

    def med_side(self, side: str) -> float | None:
        if self.peer_med_fair_over is None:
            return None
        return self.peer_med_fair_over if side == "over" else 1.0 - self.peer_med_fair_over


def peer_context(quotes_at_line: list, target_book: str) -> PeerContext:
    """Active peers = other books two-sided at the exact line with quote age <= 25m."""
    fairs, sharp = [], 0
    for q in quotes_at_line:
        if q.book == target_book:
            continue
        if q.age_sec is not None and q.age_sec > PEER_AGE_MAX_MIN * 60.0:
            continue
        fairs.append(q.fair_over())
        if q.book in SHARP_BOOKS:
            sharp += 1
    if not fairs:
        return PeerContext(0, None, None, 0)
    med = median(fairs)
    return PeerContext(len(fairs), med, median([abs(x - med) for x in fairs]), sharp)


def modal_line(quotes_at_prop: list) -> float | None:
    """Modal two-sided line across books (production stream tie-break: most books,
    then smallest |line|)."""
    counts: dict = {}
    for q in quotes_at_prop:
        counts[q.line] = counts.get(q.line, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]


# --------------------------------------------------------------------------- clustered CIs
def clustered_mean_ci(values, clusters, conf: float = 0.95):
    """Cluster-robust mean + CI for a mean, t critical value on G-1 df (the ruling's
    'game-day-clustered 95% CIs on G-1 df'). Returns (mean, lo, hi, G)."""
    import numpy as np
    v = np.asarray(values, dtype=float)
    c = np.asarray(clusters)
    n = len(v)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"), 0)
    mean = float(v.mean())
    groups = {}
    for x, g in zip(v, c):
        groups.setdefault(g, []).append(x)
    G = len(groups)
    if G < 2:
        return (mean, float("nan"), float("nan"), G)
    # CR1 cluster-robust variance of the mean
    s = 0.0
    for g, xs in groups.items():
        s += (sum(xs) - len(xs) * mean) ** 2
    var = (G / (G - 1.0)) * s / (n * n)
    se = math.sqrt(var)
    tcrit = float(_stats.t.ppf(0.5 + conf / 2.0, G - 1))
    return (mean, mean - tcrit * se, mean + tcrit * se, G)


def roi_profit(am_odds: float, won: bool, book: str) -> float:
    """Flat 1-unit settled profit at the fired price. Exchange venues carry the
    ruling's 0.37% execution drag, applied as a haircut on the decimal return
    (dec_eff = dec * (1 - 0.0037)); a losing exchange bet still loses its unit.
    Retail books: no drag."""
    dec = american_to_decimal(am_odds)
    if book in EXCHANGE_BOOKS:
        dec = dec * (1.0 - EXCHANGE_DRAG)
    return (dec - 1.0) if won else -1.0
