"""
ev.novig.maker -- the paper-only, one-sided passive-bid DECISION logic for the Novig maker
falsification study (NOV-03), implementing Argon's 2026-11-12 ruling exactly
(~/workspace/beezy/profitability/argon-novig-market-making-VERBATIM-2026-11-12.md, Q2/Q3/Q5).

WHAT THIS MODULE IS: pure functions. Given a candidate (a main-line prop point with a leave-Novig-out
consensus, a model probability, and Novig's current book), decide() returns the waterfall mode
(TAKER / MAKER_PAPER / WITNESS / ABSTAIN) and, for MAKER_PAPER, the resting bid price and size.
cancel_reasons() evaluates the ruling's mandatory cancel triggers for a resting quote.
WHAT THIS MODULE IS NOT: it places no orders, holds no venue, reads no feeds, and is wired into
NOTHING. The study ships DISABLED (ev.novig.maker_study.STUDY_ARMED is False; no driver, no
scheduler, no job). Turning it on is a separate, separately reviewed step at study launch, which
is also where the live loops the ruling mandates (<= 30s reference polling, the whole-game status
feeds behind hot_game_pks, confirm-cancel-before-replace against /v3/portfolio/orders) get built;
here those triggers exist as pure evaluations over injected state, unit-tested.

Quote construction (ruling Q2, verbatim parameters):
  anchor   P_fair_floor = min(P_cons, P_blend), P_cons the leave-Novig-out de-vigged consensus
           (n_books >= 4, disp <= 0.06 required), logit(P_blend) = a + b * logit(P_cons)
           + c_stat * (logit(P_model) - logit(P_cons)).
  guard    |P_model - P_cons| > 0.15 -> abstain completely (missing lineup/injury context).
  side     one-sided only, on a side where P_blend >= P_cons (agreement or neutral).
  bid      P_bid = floor_{0.005}(P_fair_floor - delta), capped at min(P_fair_floor - delta,
           P_novig_bid + 0.01) unless P_novig_bid is a stub > 15c below fair; the bid must sit
           >= P_novig_bid (queue position Q = 0) and strictly below Novig's ask on that side.
  delta    max(0.030, delta_0 + 0.50 * disp + delta_time + delta_news); delta_0 0.025 discrete
           count props / 0.035 high-variance workload props; delta_time +0.015 beyond 180m,
           0.0 inside 60-180m, +0.010 inside 15-60m; delta_news +0.015 on a flagged
           news-sensitive player (starter Questionable on his team).
  size     $5.00 notional liability per quote (~8-12 $1-payout contracts at mid prices);
           ev.novig.liability caps govern at placement time in the runner (not duplicated here).

Blend coefficients: the ruling fixes the FORM and the per-stat c values it measured (NHL sog
+0.31, NHL saves +0.75, NBA rebounds +0.13, NBA threes +0.19) and directs NFL to anchor on pure
P_cons (c = 0) unless a stat clears the 2026-11-13 c >= 0.12 gate -- no NFL stat carries a
cleared c for this surface, so all four NFL stats are c = 0 here. The ruling gives no fitted
(a, b); BLEND_A = 0, BLEND_B = 1 (the identity anchor, so c_stat carries the whole model tilt).

NEVER list (ruling Q2/Q4): NBA assists (banned as a fair input), NBA points, NFL passing yards,
every alternate / milestone ladder (anything not a main-line point), every stat not in the tier
tables, two-sided quoting, and any quote while a blackout window (ruling Q3) is active.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

# ------------------------------------------------------------------ study parameters (ruling Q2)
MIN_BOOKS = 4                       # reference books behind P_cons (maker bar; the taker path uses 3)
DISP_MAX = 0.06
DISAGREE_MAX = 0.15                 # |P_model - P_cons| guard (the RFQ pricer guard)
WIDE_SPREAD_MIN = 0.08              # yes_spread at or above which a resting bid sits at Q = 0
TAKER_EDGE_MIN = 0.025              # fair_floor - ask >= this is State 1 (TAKER), never a maker quote
DELTA_FLOOR = 0.030                 # hard floor: bid is always >= 3.0c below P_fair_floor
STUB_GAP = 0.15                     # an existing bid more than 15c below fair is a stub (cap lifts)
QUOTE_LIABILITY_USD = 5.00          # per-quote notional liability
BLEND_A, BLEND_B = 0.0, 1.0         # identity anchor; see module docstring

# delta_time bands (minutes to start): the ruling prices > 180m and 15-60m; inside 15m the adjacent
# band's +0.010 is used (quoting there is bounded by the T-5m hard cancel and the league blackouts).
def _delta_time(mins_to_start: float) -> float:
    if mins_to_start > 180.0:
        return 0.015
    if mins_to_start >= 60.0:
        return 0.0
    return 0.010


@dataclass(frozen=True)
class StatCfg:
    tier: int                       # 1 = quote any time outside blackouts; 2 = post-lineup window only
    delta_0: float
    c_stat: float
    window: tuple | None = None     # (min_mins, max_mins) for tier 2 (NBA: T-60m .. T-10m)


STAT_CFG = {
    # Tier 1 -- high coverage + proven orthogonal signal or low news shock (ruling Q2 order of entry)
    ("NHL", "sog"): StatCfg(1, 0.025, 0.31),
    ("NHL", "saves"): StatCfg(1, 0.035, 0.75),
    ("NFL", "receptions"): StatCfg(1, 0.025, 0.0),
    ("NFL", "pass_tds"): StatCfg(1, 0.025, 0.0),
    ("NFL", "rec_yds"): StatCfg(1, 0.035, 0.0),
    ("NFL", "rush_yds"): StatCfg(1, 0.035, 0.0),
    # Tier 2 -- strict post-lineup window only, clean injury reports (ruling Q2)
    ("NBA", "rebounds"): StatCfg(2, 0.035, 0.13, window=(10.0, 60.0)),
    ("NBA", "threes"): StatCfg(2, 0.025, 0.19, window=(10.0, 60.0)),
}
# Named for the record (they fail via STAT_CFG absence like everything unlisted, but the ruling bans
# them explicitly, so they are spelled out): NBA assists, NBA points, NFL passing yards.
NEVER_STATS = frozenset({("NBA", "assists"), ("NBA", "points"), ("NFL", "pass_yds")})

SHARP_BOOKS = frozenset({"pinnacle", "fanduel", "draftkings"})


# ------------------------------------------------------------------ anchor math
def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def p_blend(p_cons: float, p_model: float, c_stat: float) -> float:
    """The EB logit blend of the ruling: logit(P_blend) = a + b*logit(P_cons) + c*(logit(P_model)-logit(P_cons))."""
    return _sigmoid(BLEND_A + BLEND_B * _logit(p_cons) + c_stat * (_logit(p_model) - _logit(p_cons)))


def fair_floor(p_cons: float, p_bl: float) -> float:
    return min(p_cons, p_bl)


def delta_offset(cfg: StatCfg, disp: float, mins_to_start: float, news_surcharge: bool) -> float:
    d = cfg.delta_0 + 0.50 * disp + _delta_time(mins_to_start) + (0.015 if news_surcharge else 0.0)
    return max(DELTA_FLOOR, d)


def _floor_005(x: float) -> float:
    return math.floor(x / 0.005 + 1e-9) * 0.005


def quote_bid(fair: float, delta: float, novig_bid: float | None) -> float | None:
    """floor_{0.005}(fair - delta), capped 1.0c over the existing bid unless that bid is a stub
    (> 15c below fair). Returns None when the result cannot sit at Q = 0 (below the existing bid)
    or is not a positive price. The caller checks the ask (never cross)."""
    raw = fair - delta
    if novig_bid is not None and novig_bid < fair - STUB_GAP:
        cap = raw                                   # stub: the +1c cap does not apply
    elif novig_bid is not None:
        cap = novig_bid + 0.01
    else:
        cap = raw
    bid = _floor_005(min(raw, cap))
    if bid <= 0.0:
        return None
    if novig_bid is not None and bid < novig_bid - 1e-9:
        return None                                 # would sit behind the existing bid, not Q = 0
    return round(bid, 4)


def quote_contracts(bid_price: float, liability_usd: float = QUOTE_LIABILITY_USD) -> int:
    """$1-payout contracts whose worst-case cost (== liability when buying) is <= the per-quote
    liability: floor(liability / price). ~8-12 at mid-range prices, per the ruling."""
    if bid_price <= 0:
        return 0
    return int(liability_usd / bid_price)


# ------------------------------------------------------------------ the waterfall (ruling Q5)
@dataclass(frozen=True)
class MakerCandidate:
    league: str                     # 'NFL' | 'NHL' | 'NBA'
    stat: str
    game_key: str
    player_norm: str
    line: float
    is_alt: bool                    # alternate / milestone ladder rung (never quotable)
    p_cons: float                   # leave-Novig-out de-vigged consensus P(over)
    p_model: float                  # model P(over) behind the blend
    n_books: int                    # leave-Novig-out reference book count
    disp: float                     # leave-Novig-out dispersion
    novig_bid_over: float | None    # Novig's best bid on Over (None = no bid)
    novig_ask_over: float | None    # Novig's best ask on Over (= 1 - best bid on Under)
    mins_to_start: float
    snapshot_age_sec: float         # age of the reference snapshot at decision time
    news_surcharge: bool = False    # starter Questionable on this player's team (delta_news input)
    game_unresolved_q: bool = False  # NBA: a rotation player on either team is Q/GTD unresolved
    injury_reports_clean: bool = True  # NBA tier-2 requirement inside the window
    goalie_confirmed: bool = True   # NHL saves requirement


@dataclass(frozen=True)
class MakerDecision:
    mode: str                       # TAKER | MAKER_PAPER | WITNESS | ABSTAIN
    side: str | None = None         # 'yes' (over) | 'no' (under) for TAKER / MAKER_PAPER
    bid_price: float | None = None
    contracts: int = 0
    fair_floor: float | None = None
    delta: float | None = None
    reasons: tuple = ()


def _blackout(c: MakerCandidate) -> str | None:
    """Ruling Q3 hard windows. Returns the blackout name or None."""
    m = c.mins_to_start
    if m < 5.0:
        return "inside_t_minus_5"
    if c.league == "NBA":
        if c.game_unresolved_q and m > 60.0:
            return "nba_unresolved_q_before_t60"
    elif c.league == "NFL":
        if 75.0 <= m <= 100.0:
            return "nfl_inactives_window"
    elif c.league == "NHL":
        if c.stat == "saves":
            if not c.goalie_confirmed:
                return "nhl_saves_goalie_unconfirmed"
            if 15.0 <= m <= 35.0:
                return "nhl_warmup_window"
    return None


def decide(c: MakerCandidate) -> MakerDecision:
    """Assign exactly one waterfall mode (ruling Q5). State 1 (TAKER) is reported, not executed --
    the taker executor owns it; this function exists so the maker path can never quote through a
    price the taker gate would have taken."""
    cfg = STAT_CFG.get((c.league, c.stat))
    if cfg is None:
        return MakerDecision("ABSTAIN", reasons=("stat_not_approved",))
    if c.is_alt:
        return MakerDecision("ABSTAIN", reasons=("ladder_never_listed",))
    if c.n_books < MIN_BOOKS:
        return MakerDecision("ABSTAIN", reasons=("too_few_reference_books",))
    if c.disp > DISP_MAX:
        return MakerDecision("ABSTAIN", reasons=("dispersion_too_wide",))
    if abs(c.p_model - c.p_cons) > DISAGREE_MAX:
        return MakerDecision("ABSTAIN", reasons=("model_consensus_disagreement",))
    if c.snapshot_age_sec > 300.0:
        return MakerDecision("ABSTAIN", reasons=("reference_snapshot_stale",))
    bo = _blackout(c)
    if bo:
        return MakerDecision("ABSTAIN", reasons=(bo,))
    if cfg.tier == 2:
        lo, hi = cfg.window
        if not (lo <= c.mins_to_start <= hi):
            return MakerDecision("ABSTAIN", reasons=("outside_tier2_window",))
        if not c.injury_reports_clean or c.game_unresolved_q:
            return MakerDecision("ABSTAIN", reasons=("tier2_injury_reports_not_clean",))
    if c.novig_bid_over is None or c.novig_ask_over is None:
        return MakerDecision("ABSTAIN", reasons=("novig_book_incomplete",))
    spread = c.novig_ask_over - c.novig_bid_over

    # per-side fairs: the de-vigged consensus is a two-way fair, so the under side is the complement
    sides = (("yes", c.p_cons, c.p_model, c.novig_bid_over, c.novig_ask_over),
             ("no", 1.0 - c.p_cons, 1.0 - c.p_model,
              None if c.novig_ask_over is None else 1.0 - c.novig_ask_over,
              None if c.novig_bid_over is None else 1.0 - c.novig_bid_over))
    witness = False
    for side, p_cons_s, p_model_s, bid_s, ask_s in sides:
        bl = p_blend(p_cons_s, p_model_s, cfg.c_stat)
        fair = fair_floor(p_cons_s, bl)
        if fair - ask_s >= TAKER_EDGE_MIN:
            return MakerDecision("TAKER", side=side, fair_floor=fair,
                                 reasons=("taker_edge_clears_gate",))
        if bl < p_cons_s - 1e-12:
            continue                                # quote only where the blend agrees / is neutral
        if spread < WIDE_SPREAD_MIN:
            witness = True                          # tight book: State 3, do not trade on Novig
            continue
        d = delta_offset(cfg, c.disp, c.mins_to_start, c.news_surcharge)
        bid = quote_bid(fair, d, bid_s)
        if bid is None or bid >= ask_s:
            continue
        return MakerDecision("MAKER_PAPER", side=side, bid_price=bid,
                             contracts=quote_contracts(bid), fair_floor=fair, delta=d,
                             reasons=("wide_book_confluence_bid",))
    if witness:
        return MakerDecision("WITNESS", reasons=("book_tight_no_trade",))
    return MakerDecision("ABSTAIN", reasons=("no_side_qualifies",))


# ------------------------------------------------------------------ mandatory cancel controls (Q3)
QUOTE_TTL_SEC = 900.0               # every resting quote expires / revalidates at 15 minutes
RESTING_STALE_SEC = 900.0           # reference snapshot older than this while resting: pull all
HARD_CANCEL_BEFORE_SEC = 300.0     # unconditional cancel at commence - 300s
CONS_VELOCITY_PP = 0.015            # consensus moves against our side >= 1.5pp: cancel
SHARP_VELOCITY_PP = 0.030           # any single sharp book moves >= 3.0pp: cancel
LOCKOUT_SEC = 900.0                 # whole-game lockout after a status change (15 min)


@dataclass(frozen=True)
class RestingQuote:
    game_key: str
    league: str
    stat: str
    side: str                       # 'yes' | 'no'
    bid_price: float
    t_post: datetime
    commence: datetime
    p_cons_side_at_post: float      # consensus probability of OUR side when posted
    sharp_at_post: dict = field(default_factory=dict)   # {book: side probability at post}


def cancel_reasons(q: RestingQuote, *, now: datetime, p_cons_side_now: float,
                   sharp_now: dict, n_books_now: int, snapshot_age_sec_now: float,
                   status_changed_game_keys: frozenset = frozenset()) -> tuple:
    """The ruling's mandatory pulls, evaluated over injected live state. Empty tuple = keep resting.
    (The <= 30s polling loop that feeds this is launch machinery; the triggers are the load-bearing
    part and they are all here.)"""
    out = []
    if (q.commence - now).total_seconds() <= HARD_CANCEL_BEFORE_SEC:
        out.append("t_minus_5_hard_cancel")
    if (now - q.t_post).total_seconds() >= QUOTE_TTL_SEC:
        out.append("ttl_expired")
    if snapshot_age_sec_now > RESTING_STALE_SEC:
        out.append("reference_snapshot_stale")
    if p_cons_side_now <= q.p_cons_side_at_post - CONS_VELOCITY_PP:
        out.append("consensus_velocity_against")
    for book in SHARP_BOOKS:
        if book in q.sharp_at_post and book in sharp_now:
            if abs(sharp_now[book] - q.sharp_at_post[book]) >= SHARP_VELOCITY_PP:
                out.append("sharp_book_velocity")
                break
    if n_books_now < MIN_BOOKS:
        out.append("reference_books_dropped")
    if q.game_key in status_changed_game_keys:
        out.append("game_status_change")            # caller also sets the game lockout (lockout_until)
    return tuple(out)


def lockout_until(status_change_at: datetime) -> datetime:
    """Whole-game re-quote lockout after a status change: 15 minutes, and re-quoting additionally
    requires >= 4 reference books publishing post-news timestamps (the runner checks that flag)."""
    from datetime import timedelta
    return status_change_at + timedelta(seconds=LOCKOUT_SEC)


def requote_blocked(lockout_until_ts: datetime | None, now: datetime, post_news_books_ok: bool) -> bool:
    if lockout_until_ts is not None and now < lockout_until_ts:
        return True
    if lockout_until_ts is not None and not post_news_books_ok:
        return True
    return False
