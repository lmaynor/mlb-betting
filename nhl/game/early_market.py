"""nhl.game.early_market -- NHL early-market open-to-close CLV shadow logger (zero stake, paper only).

Spec: ~/workspace/beezy/nhl-early-market-logger-spec-2026-11-11.md (Workstream C, Argon game-lines
Direction 2). The logger reads ONLY data that is already banked -- the OddsAccum game_lines snapshots
written 6x/day by the existing `odds-bank-nhl-game-lines` job (~10 books/event, per-book `last_update`)
and the daily engine payload `EVSweep/nhl_games/predictions_latest.json`. It buys no odds, changes no
board output, and provisions nothing: the `log` / `settle` / `report` subcommands below are the whole
build; wiring them to a job/scheduler is a separate, later step.

    python -m nhl.game.early_market log                          one pass: gate today's+tomorrow's slate, append picks
    python -m nhl.game.early_market settle [--day YYYY-MM-DD]    attach close capture + result to unsettled picks
    python -m nhl.game.early_market report [--through DATE]      the judgment table (see JUDGMENT below)
    python -m nhl.game.early_market fit-temperature --events-dir <dir from `nhl.game.market_eval --save-events`>

GATE (per game, market, side; spec section 4.2): a pick is logged at the FIRST qualifying pass inside
[drop - 36h, drop - 6h] when a real book/exchange quote in the snapshot the predictions payload was
priced from implies >= +2.5% EV against the de-vigged early consensus of >= 4 books (target book
excluded from its own consensus, modal line for spread/total), AND the temperature-calibrated model
probability sits on the same side of that consensus as the outlier (p_cal > p_fair_early for the picked
side). "Opener" here is operationalised as the target book's quote in that snapshot, frozen at log time
(spec ambiguity A2): the stored fields are `early_*`, never `open_*`, except `target_dec_open` /
`roi_open`, which name the logged price the judgment is defined on. Goalie status (RotoWire cache state
carried on the payload) is RECORDED on every pick, never gated on (spec A6).

CLOSE (spec section 3.6, adopted v1): the last banked OddsAccum game_lines snapshot STRICTLY BEFORE
puck drop -- the repo's closing-snapshot pattern (nba/props/forward.py::closing_snapshot) generalised
from kind=props to kind=game_lines -- with `close_age_min` stored on every settled row. Expected age is
~85-145 min for evening games; the judgment below is defined on THIS close, not on a T-15 capture.

JUDGMENT (pre-registered; Nov 11, 2026 ruling, FIXED -- do not retune after looking):
  * Jan 15, 2027 is a CHECKPOINT ONLY: kill early iff mean same-book CLV <= 0 at that date
    (on n_clv >= 20; below that the checkpoint read is reported as underpowered, not acted on).
  * The BINDING judgment is at n = 200 settled picks: PROMOTE iff mean same-book CLV >= +1.5%
    (date-clustered t >= 2.5) AND realised ROI at logged opening prices > +1.0%; otherwise the NHL
    game engine is killed.
  * Same-book CLV = target_dec_open / target_dec_close - 1, only where the target book quotes the
    close and (spread/total) the line did not move. Rows failing either are counted in `clv_missing`;
    a clv_missing share above 20% invalidates the CLV leg (surfaced, never silently dropped).
  * Pooled across moneyline / puck line / total; per-market figures printed alongside. Pushes are
    excluded from both metrics and counted. DRY_RUN rows are excluded from the judgment pool.

TEMPERATURE (spec section 4.3): one temperature T per market on the logit scale,
p_cal = sigmoid(logit(p_model) / T), fit by minimising log loss on the DEV seasons 2021-22..2023-24
harness events (nhl.game.market_eval --save-events), validated (ECE / log loss) on the held-out
2024-25 + 2025-26 seasons, then FROZEN as constants below. p_cal feeds the gate's agreement test
only; it is never blended into pricing and never changes board output.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

log = logging.getLogger("nhl.game.early_market")

_ET = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------------------------------- judgment (FIXED)
CHECKPOINT_DATE = "2027-01-15"          # checkpoint ONLY (see module docstring); not a binding kill date
CHECKPOINT_MIN_N_CLV = 20               # below this the checkpoint read is underpowered and does not kill
CHECKPOINT_KILL_MEAN_CLV = 0.0          # early kill iff mean same-book CLV <= 0 at the checkpoint
JUDGMENT_N = 200                        # binding judgment population: settled picks
JUDGMENT_MEAN_CLV_MIN = 0.015           # promote requires mean CLV >= +1.5%
JUDGMENT_T_MIN = 2.5                    # ... with date-clustered t >= 2.5
JUDGMENT_ROI_MIN = 0.010                # ... AND ROI at opens > +1.0% (strictly greater)
CLV_MISSING_INVALID_SHARE = 0.20        # clv_missing share above this invalidates the CLV leg

# --------------------------------------------------------------------------------------------------- gate (spec 4.2)
EV_GATE = 0.025                         # target quote EV vs excluded-target consensus >= +2.5%
MIN_BOOKS_EARLY = 4                     # books in the de-vigged early consensus, target book excluded
WINDOW_MIN_H, WINDOW_MAX_H = 6.0, 36.0  # log only inside [drop - 36h, drop - 6h]
# Never a target: the Consensus pseudo-book and the DFS books (mirrors ev.props.DFS_BOOKS / nba.config.DFS_BOOKS;
# duplicated here so this module stays import-light, same convention as the two existing copies).
NON_TARGET_BOOKS = frozenset({"consensus", "sleeper", "underdog", "prizepicks", "dabble", "betr", "fliff"})

MARKETS = ("moneyline", "spread", "total")
SYSTEM_OF_MARKET = {"moneyline": "NHL_ML", "spread": "NHL_SPREAD", "total": "NHL_TOTAL"}
SIDES_OF_MARKET = {"moneyline": ("home", "away"), "spread": ("home", "away"), "total": ("over", "under")}

PICKS_KEY = "NHL/early_market/picks/{day}.jsonl"
SETTLED_KEY = "NHL/early_market/settled/{day}.jsonl"
PICKS_PREFIX = "NHL/early_market/picks/"
SETTLED_PREFIX = "NHL/early_market/settled/"
PREDICTIONS_KEY = "EVSweep/nhl_games/predictions_latest.json"
SPORT_KEY = "icehockey_nhl"

# --------------------------------------------------------------------------------------------------- temperature (spec 4.3)
TEMP_VERSION = "temperature_v0_unfitted"
DEV_SEASONS = (2021, 2022, 2023)        # season START years: 2021-22 .. 2023-24
VAL_SEASONS = (2024, 2025)              # held out: 2024-25 + 2025-26
TEMP_SEARCH_BOUNDS = (0.5, 5.0)
# FROZEN per-market temperatures, applied as p_cal = sigmoid(logit(p_model) / T).
# PROVENANCE: NOT YET FIT. All three are the identity T = 1.0 (p_cal == p_model), frozen as a placeholder
# at build time (2026-11-11) because the harness events the spec's fit runs on
# (`python -m nhl.game.market_eval --save-events <dir>` over the BettingPros bank in GCS) were not
# available in the build environment, and no number is fabricated here. Until
# `python -m nhl.game.early_market fit-temperature --events-dir <dir>` is run on real harness events and
# its output replaces these constants (bumping TEMP_VERSION to temperature_v1), the gate's agreement
# test runs on the RAW model lean and no calibration benefit (ECE 0.054-0.083 toward the market's
# ~0.012) may be claimed for it. A refit after the clock starts restarts the pre-registration (spec A7).
FROZEN_TEMPERATURES: Dict[str, float] = {"moneyline": 1.0, "spread": 1.0, "total": 1.0}


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def calibrate_prob(p: float, temperature: float) -> float:
    """Temperature-scale one probability on the logit scale. T = 1 is the identity; T > 1 shrinks
    toward 0.5 (an overconfident model), T < 1 sharpens. Monotone in p for any fixed T > 0."""
    if temperature <= 0:
        raise ValueError(f"temperature must be > 0, got {temperature}")
    return _sigmoid(_logit(p) / float(temperature))


def log_loss(p: Sequence[float], y: Sequence[float]) -> float:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
    y = np.asarray(y, dtype=float)
    return float(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)).mean())


def fit_temperature(p: Sequence[float], y: Sequence[float],
                    bounds: Tuple[float, float] = TEMP_SEARCH_BOUNDS) -> float:
    """The T in `bounds` minimising log loss of sigmoid(logit(p)/T) against y. Golden-section search
    (log loss in 1/T is well behaved for this 1-D problem; deterministic, no scipy dependency)."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(p) < 10:
        raise ValueError(f"need >= 10 events to fit a temperature, got {len(p)}")
    lo, hi = float(bounds[0]), float(bounds[1])
    gr = (math.sqrt(5.0) - 1.0) / 2.0

    def loss(t: float) -> float:
        cal = np.array([calibrate_prob(v, t) for v in p])
        return log_loss(cal, y)

    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fc, fd = loss(c), loss(d)
    for _ in range(200):
        if hi - lo < 1e-6:
            break
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = loss(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = loss(d)
    return float((lo + hi) / 2.0)


def fit_temperatures(events_by_market: Dict[str, "object"]) -> dict:
    """Spec 4.3 protocol over market_eval events tables ({market: DataFrame with season, p_model, y}):
    fit T per market on DEV_SEASONS, validate ECE + log loss (raw vs calibrated) on VAL_SEASONS with T
    frozen. Returns the artifact dict that becomes temperature_v1.json / the FROZEN_TEMPERATURES source."""
    from mlb_core.eval.market_scoring import ece
    out: dict = {"version": "temperature_v1", "dev_seasons": list(DEV_SEASONS), "val_seasons": list(VAL_SEASONS),
                 "bounds": list(TEMP_SEARCH_BOUNDS), "markets": {}}
    for market, ev in events_by_market.items():
        dev = ev[ev["season"].isin(DEV_SEASONS)]
        val = ev[ev["season"].isin(VAL_SEASONS)]
        if len(dev) < 10 or len(val) == 0:
            out["markets"][market] = {"skipped": f"dev n={len(dev)}, val n={len(val)}"}
            continue
        t = fit_temperature(dev["p_model"], dev["y"])
        cal_dev = [calibrate_prob(v, t) for v in dev["p_model"]]
        cal_val = [calibrate_prob(v, t) for v in val["p_model"]]
        out["markets"][market] = {
            "T": round(t, 4), "n_dev": int(len(dev)), "n_val": int(len(val)),
            "dev_ll_raw": round(log_loss(dev["p_model"], dev["y"]), 4),
            "dev_ll_cal": round(log_loss(cal_dev, dev["y"]), 4),
            "val_ll_raw": round(log_loss(val["p_model"], val["y"]), 4),
            "val_ll_cal": round(log_loss(cal_val, val["y"]), 4),
            "val_ece_raw": round(float(ece(val["p_model"], val["y"])), 4),
            "val_ece_cal": round(float(ece(cal_val, val["y"])), 4),
        }
    return out


# --------------------------------------------------------------------------------------------------- odds helpers
def _dec(american) -> Optional[float]:
    """American price -> decimal, or None for missing / non-numeric / zero (a null price never aborts)."""
    try:
        v = float(american)
    except (TypeError, ValueError):
        return None
    if v != v or v == 0.0:
        return None
    return 1.0 + (v / 100.0 if v > 0 else 100.0 / abs(v))


def _american(dec: float) -> int:
    return int(round((dec - 1.0) * 100.0)) if dec >= 2.0 else int(round(-100.0 / (dec - 1.0)))


def _devig_pair(dec_a: float, dec_b: float) -> Tuple[float, float]:
    """Proportional de-vig of a two-sided decimal pair -> (fair P(a), fair P(b))."""
    ia, ib = 1.0 / dec_a, 1.0 / dec_b
    s = ia + ib
    return ia / s, ib / s


def parse_snapshot_event(event: dict) -> Dict[str, List[dict]]:
    """One ParlayAPI game_lines event -> {market: [two-sided per-book quotes]}. Each quote:
    {book, line, dec_a, dec_b, odds_a, odds_b} with a = home (moneyline/spread) or over (total),
    b = away or under; spread `line` is the HOME handicap, total `line` the total. One-sided or
    unparseable book markets are dropped, never guessed."""
    from nhl.config import team_abbrev
    home = team_abbrev(str(event.get("home_team", "")))
    away = team_abbrev(str(event.get("away_team", "")))
    out: Dict[str, List[dict]] = {m: [] for m in MARKETS}
    if not home or not away:
        return out
    for b in event.get("bookmakers") or []:
        if not isinstance(b, dict):
            continue
        book = str(b.get("key") or b.get("name") or "unknown")
        for mk in b.get("markets") or []:
            if not isinstance(mk, dict):
                continue
            key = mk.get("key")
            outcomes = mk.get("outcomes") or []
            if key == "h2h" and len(outcomes) == 2:
                pm = {team_abbrev(str(o.get("name"))): o for o in outcomes}
                ho, ao = _dec((pm.get(home) or {}).get("price")), _dec((pm.get(away) or {}).get("price"))
                if ho and ao:
                    out["moneyline"].append({"book": book, "line": None, "dec_a": ho, "dec_b": ao,
                                             "odds_a": _american(ho), "odds_b": _american(ao)})
            elif key == "spreads" and len(outcomes) == 2:
                pm = {team_abbrev(str(o.get("name"))): o for o in outcomes}
                h_out, a_out = pm.get(home) or {}, pm.get(away) or {}
                ho, ao = _dec(h_out.get("price")), _dec(a_out.get("price"))
                if ho and ao and h_out.get("point") is not None:
                    out["spread"].append({"book": book, "line": float(h_out["point"]), "dec_a": ho, "dec_b": ao,
                                          "odds_a": _american(ho), "odds_b": _american(ao)})
            elif key == "totals" and len(outcomes) == 2:
                pm = {str(o.get("name", "")).lower(): o for o in outcomes}
                o_out, u_out = pm.get("over") or {}, pm.get("under") or {}
                oo, uo = _dec(o_out.get("price")), _dec(u_out.get("price"))
                if oo and uo and o_out.get("point") is not None:
                    out["total"].append({"book": book, "line": float(o_out["point"]), "dec_a": oo, "dec_b": uo,
                                         "odds_a": _american(oo), "odds_b": _american(uo)})
    return out


def _modal_line(quotes: List[dict], market: str) -> Optional[float]:
    if market == "moneyline":
        return None
    if not quotes:
        return None
    counts = Counter(q["line"] for q in quotes)
    top = max(counts.values())
    tied = sorted(l for l, c in counts.items() if c == top)
    if market == "spread":
        return float(min(tied, key=abs))                       # harness convention: closest to 0
    med = float(np.median([q["line"] for q in quotes]))
    return float(min(tied, key=lambda l: abs(l - med)))        # harness convention: closest to median


def consensus_at(quotes: List[dict], market: str, line: Optional[float],
                 exclude_book: Optional[str] = None) -> Optional[dict]:
    """De-vigged consensus at `line`: median across books of each book's proportionally de-vigged
    P(a), over books quoting BOTH sides at that line. Returns {p_a, p_b, n_books} or None."""
    qs = [q for q in quotes if (market == "moneyline" or q["line"] == line)
          and (exclude_book is None or q["book"] != exclude_book)]
    if not qs:
        return None
    pa = [_devig_pair(q["dec_a"], q["dec_b"])[0] for q in qs]
    med = float(np.median(pa))
    return {"p_a": med, "p_b": 1.0 - med, "n_books": len(qs)}


def _parse_ts(s) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------------- the gate / log
def evaluate_market(quotes: List[dict], market: str, p_model_by_side: Dict[str, float],
                    temperature: float) -> Optional[dict]:
    """The section 4.2 gate for one (game, market). `p_model_by_side` = the payload's raw model
    probability per side. Returns the winning candidate {side, target_book, target_dec, target_odds,
    line, p_fair_early, n_books_early, ev_consensus, p_model_raw, p_cal, ev_model} -- the best-price
    qualifying outlier, larger EV breaking a both-sides tie -- or None when nothing qualifies."""
    if not quotes:
        return None
    line = _modal_line(quotes, market)
    at_line = [q for q in quotes if market == "moneyline" or q["line"] == line]
    if len(at_line) < MIN_BOOKS_EARLY + 1:                     # consensus excl. target needs >= MIN_BOOKS_EARLY
        return None
    best: Optional[dict] = None
    for q in at_line:
        if q["book"].lower() in NON_TARGET_BOOKS:
            continue
        cons = consensus_at(quotes, market, line, exclude_book=q["book"])
        if cons is None or cons["n_books"] < MIN_BOOKS_EARLY:
            continue
        for i, side in enumerate(SIDES_OF_MARKET[market]):
            dec_t = q["dec_a"] if i == 0 else q["dec_b"]
            p_fair = cons["p_a"] if i == 0 else cons["p_b"]
            ev = p_fair * dec_t - 1.0
            if ev < EV_GATE:
                continue
            p_raw = p_model_by_side.get(side)
            if p_raw is None:
                continue
            p_cal = calibrate_prob(float(p_raw), temperature)
            if not p_cal > p_fair:                             # calibrated lean must agree (spec 4.2.4)
                continue
            cand = {"side": side, "target_book": q["book"], "target_dec": dec_t,
                    "target_odds": q["odds_a"] if i == 0 else q["odds_b"], "line": line,
                    "p_fair_early": p_fair, "n_books_early": cons["n_books"], "ev_consensus": ev,
                    "p_model_raw": float(p_raw), "p_cal": p_cal, "ev_model": p_cal * dec_t - 1.0}
            if best is None or cand["ev_consensus"] > best["ev_consensus"]:
                best = cand
    return best


def build_picks(payload: dict, snapshot_events: List[dict], snapshot_key: Optional[str],
                snapshot_ts: Optional[datetime], now: datetime,
                temperatures: Optional[Dict[str, float]] = None,
                temp_version: str = TEMP_VERSION, dry_run: bool = False) -> List[dict]:
    """One `log` pass, pure: predictions payload + the snapshot it was priced from -> pick rows
    (spec 4.4 schema). Games are matched to snapshot events on (home, away) tricodes; a game-market
    appears at most once per pass and cmd_log enforces first-qualifying-pass idempotency on pick_id."""
    from nhl.config import team_abbrev
    temps = temperatures or FROZEN_TEMPERATURES
    rows_by_game: Dict[str, Dict[Tuple[str, str], dict]] = {}
    for r in payload.get("rows", []):
        rows_by_game.setdefault(r.get("game_key"), {})[(r.get("system"), r.get("side"))] = r
    events_by_pair: Dict[Tuple[str, str], dict] = {}
    for ev in snapshot_events or []:
        h, a = team_abbrev(str(ev.get("home_team", ""))), team_abbrev(str(ev.get("away_team", "")))
        if h and a:
            events_by_pair[(h, a)] = ev
    snap_age = None
    if snapshot_ts is not None:
        snap_age = round((now - snapshot_ts).total_seconds() / 60.0, 1)
    picks: List[dict] = []
    for g in payload.get("games", []):
        gk = g.get("game_key")
        ev = events_by_pair.get((g.get("home"), g.get("away")))
        if ev is None or not gk:
            continue
        commence = _parse_ts(g.get("commence_time") or g.get("commence") or ev.get("commence_time"))
        if commence is None:
            continue
        hours_to_drop = (commence - now).total_seconds() / 3600.0
        if not WINDOW_MIN_H <= hours_to_drop <= WINDOW_MAX_H:
            continue
        quotes_by_market = parse_snapshot_event(ev)
        grows = rows_by_game.get(gk, {})
        for market in MARKETS:
            system = SYSTEM_OF_MARKET[market]
            p_by_side = {}
            for side in SIDES_OF_MARKET[market]:
                row = grows.get((system, side))
                if row is not None and row.get("p_model") is not None:
                    p_by_side[side] = float(row["p_model"])
            cand = evaluate_market(quotes_by_market[market], market, p_by_side, temps[market])
            if cand is None:
                continue
            picks.append({
                "pick_id": f"nhl-early:{gk}:{system}:{cand['side']}",
                "logged_at_utc": now.isoformat(), "game_key": gk,
                "game_date_et": commence.astimezone(_ET).strftime("%Y-%m-%d"),
                "commence_utc": commence.isoformat(), "home": g.get("home"), "away": g.get("away"),
                "market": market, "system": system, "side": cand["side"], "line": cand["line"],
                "target_book": cand["target_book"], "target_dec_open": round(cand["target_dec"], 4),
                "target_odds_open": cand["target_odds"],
                "p_fair_early": round(cand["p_fair_early"], 5), "n_books_early": cand["n_books_early"],
                "ev_consensus": round(cand["ev_consensus"], 5),
                "p_model_raw": cand["p_model_raw"], "p_cal": round(cand["p_cal"], 5),
                "temp_version": temp_version, "ev_model": round(cand["ev_model"], 5),
                "snapshot_key": snapshot_key, "snapshot_age_min": snap_age,
                "hours_to_drop": round(hours_to_drop, 2),
                "goalie_home": g.get("home_goalie"), "goalie_away": g.get("away_goalie"),
                "goalie_home_confirmed": bool(g.get("home_goalie_confirmed")),
                "goalie_away_confirmed": bool(g.get("away_goalie_confirmed")),
                "engine_version": payload.get("engine_version"), "payload_ts": payload.get("ts"),
                "dry_run": bool(dry_run), "validated": False,
            })
    return picks


# --------------------------------------------------------------------------------------------------- close capture
def snapshot_time_from_key(key: str) -> Optional[datetime]:
    """OddsAccum/{sport}/raw/{ET date}/game_lines_{HHMM}.json -> the snapshot's ET wall-clock time.
    The accumulator names snapshots by ET date + ET HHMM (nba/odds/accumulator.py::_now), the same
    convention nba/props/forward.py::closing_snapshot parses for props."""
    name = key.rsplit("/", 1)[-1]
    date = key.rsplit("/", 2)[-2] if "/" in key else ""
    if not name.startswith("game_lines_") or not name.endswith(".json"):
        return None
    hhmm = name[len("game_lines_"):-len(".json")]
    try:
        return datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=_ET)
    except ValueError:
        return None


def select_closing_snapshot(candidates: Sequence[Tuple[str, datetime]], commence: datetime
                            ) -> Optional[Tuple[str, datetime, float]]:
    """Last snapshot STRICTLY BEFORE `commence` -> (key, ts, close_age_min), or None. Pure: the
    selection rule the whole judgment's close definition rests on (spec 3.6 adopted v1)."""
    best: Optional[Tuple[str, datetime]] = None
    for key, ts in candidates:
        if ts < commence and (best is None or ts > best[1]):
            best = (key, ts)
    if best is None:
        return None
    return best[0], best[1], round((commence - best[1]).total_seconds() / 60.0, 1)


def closing_snapshot_game_lines(commence: datetime, sport_key: str = SPORT_KEY,
                                list_keys=None, read_bytes=None) -> Optional[Tuple[str, datetime, float, list]]:
    """The closing snapshot for a game, generalising nba/props/forward.py::closing_snapshot from
    kind=props to kind=game_lines: scan the OddsAccum raw keys for the commence ET date and the two
    prior ET dates (an early snapshot for tonight's game banks under last night's ET run date),
    take the last one strictly before drop. Returns (key, ts, close_age_min, events) or None.
    Storage callables are injectable for tests; defaults hit mlb_core.storage."""
    if list_keys is None or read_bytes is None:
        from mlb_core import storage
        list_keys = list_keys or storage.list_keys
        read_bytes = read_bytes or storage.read_bytes
    from nba.config import ODDSACCUM_PREFIX
    et_day = commence.astimezone(_ET).date()
    candidates: List[Tuple[str, datetime]] = []
    for back in range(3):
        day = (et_day - timedelta(days=back)).isoformat()
        for k in list_keys(f"{ODDSACCUM_PREFIX}/{sport_key}/raw/{day}/"):
            ts = snapshot_time_from_key(k)
            if ts is not None:
                candidates.append((k, ts))
    sel = select_closing_snapshot(candidates, commence)
    if sel is None:
        return None
    key, ts, age = sel
    events = json.loads(read_bytes(key))
    if isinstance(events, dict):
        events = events.get("events") or []
    return key, ts, age, events


def find_event(events: Sequence[dict], home: str, away: str) -> Optional[dict]:
    from nhl.config import team_abbrev
    for ev in events or []:
        if team_abbrev(str(ev.get("home_team", ""))) == home and team_abbrev(str(ev.get("away_team", ""))) == away:
            return ev
    return None


def settle_outcome(pick: dict, result: dict) -> Optional[int]:
    """y for a pick from the final score: 1 = picked side won, 0 = lost, None = push.
    Moneyline: final score incl. OT/SO. Spread: final margin at the logged home handicap.
    Total: goals EXCLUDING the shootout against the logged line."""
    hs, as_ = float(result["home_goals"]), float(result["away_goals"])
    market, side, line = pick["market"], pick["side"], pick.get("line")
    if market == "moneyline":
        return int(hs > as_) if side == "home" else int(as_ > hs)
    if market == "spread":
        m = (hs - as_) + float(line)
        if abs(m) < 1e-9:
            return None
        return int(m > 0) if side == "home" else int(m < 0)
    tot = float(result["home_goals_ex_so"]) + float(result["away_goals_ex_so"])
    m = tot - float(line)
    if abs(m) < 1e-9:
        return None
    return int(m > 0) if side == "over" else int(m < 0)


def settle_pick(pick: dict, close: Optional[Tuple[str, datetime, float, list]], result: Optional[dict],
                now: datetime) -> Optional[dict]:
    """Pick row + close capture + final result -> settled row (spec 4.4), or None while the game is
    not final / no result is available. A missing close snapshot settles the row for ROI with every
    close field null and the row counted in clv_missing (reason no_close_snapshot)."""
    if result is None:
        return None
    row = dict(pick)
    commence = _parse_ts(pick["commence_utc"])
    y = settle_outcome(pick, result)
    row.update({"final_home_goals": result["home_goals"], "final_away_goals": result["away_goals"],
                "final_goals_ex_so": float(result["home_goals_ex_so"]) + float(result["away_goals_ex_so"]),
                "y": y, "roi_open": None if y is None else round(y * float(pick["target_dec_open"]) - 1.0, 5),
                "close_snapshot_key": None, "close_age_min": None, "target_dec_close": None,
                "line_close": None, "p_fair_close": None, "clv_same_book": None,
                "clv_missing_reason": "no_close_snapshot", "settled_at_utc": now.isoformat()})
    if close is None:
        return row
    key, _ts, age, events = close
    row["close_snapshot_key"], row["close_age_min"] = key, age
    ev = find_event(events, pick["home"], pick["away"])
    if ev is None:
        row["clv_missing_reason"] = "no_close_event"
        return row
    quotes = parse_snapshot_event(ev)[pick["market"]]
    line_close = _modal_line(quotes, pick["market"])
    row["line_close"] = line_close
    cons = consensus_at(quotes, pick["market"], line_close)
    if cons is not None:
        row["p_fair_close"] = round(cons["p_a"] if pick["side"] in ("home", "over") else cons["p_b"], 5)
    tgt = next((q for q in quotes if q["book"] == pick["target_book"]
                and (pick["market"] == "moneyline" or q["line"] == line_close)), None)
    if tgt is None:
        row["clv_missing_reason"] = "no_target_close"
        return row
    dec_close = tgt["dec_a"] if pick["side"] in ("home", "over") else tgt["dec_b"]
    row["target_dec_close"] = round(dec_close, 4)
    if pick["market"] != "moneyline" and line_close != pick.get("line"):
        row["clv_missing_reason"] = "line_moved"               # CLV is defined on identical lines only
        return row
    row["clv_same_book"] = round(float(pick["target_dec_open"]) / dec_close - 1.0, 5)
    row["clv_missing_reason"] = None
    return row


# --------------------------------------------------------------------------------------------------- judgment report
def _t_stat(values: Sequence[float]) -> Tuple[float, float, float]:
    """(mean, sd, plain t) of a sample; NaNs when undefined (n < 2 or zero dispersion)."""
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if len(v) == 0:
        return float("nan"), float("nan"), float("nan")
    mean = float(v.mean())
    if len(v) < 2:
        return mean, float("nan"), float("nan")
    sd = float(v.std(ddof=1))
    t = mean / (sd / math.sqrt(len(v))) if sd > 0 else float("nan")
    return mean, sd, float(t)


def _clustered_t(values: Sequence[float], groups: Sequence) -> float:
    """Date-clustered t via mlb_core.eval.market_scoring.clustered_mean (the harness precedent)."""
    from mlb_core.eval.market_scoring import clustered_mean
    r = clustered_mean(list(values), list(groups))
    if not r.get("se"):
        return float("nan")
    return float(r["mean"] / r["se"])


def aggregate_judgment(rows: Sequence[dict], through: Optional[str] = None) -> dict:
    """All settled rows -> the judgment aggregate (spec section 7). DRY_RUN rows and rows logged
    after `through` (an ET date) are excluded from the pool; pushes are counted, never scored."""
    pool = [r for r in rows if not r.get("dry_run")]
    if through:
        pool = [r for r in pool if str(r.get("game_date_et", "")) <= through]
    pushes = [r for r in pool if r.get("y") is None]
    scored = [r for r in pool if r.get("y") is not None]
    clv_rows = [r for r in scored if r.get("clv_same_book") is not None]
    missing = [r for r in scored if r.get("clv_same_book") is None]
    clv_vals = [float(r["clv_same_book"]) for r in clv_rows]
    roi_vals = [float(r["roi_open"]) for r in scored if r.get("roi_open") is not None]
    mean_clv, sd_clv, t_plain = _t_stat(clv_vals)
    t_clu = _clustered_t(clv_vals, [r.get("game_date_et") for r in clv_rows]) if len(clv_rows) >= 2 else float("nan")
    mean_roi = float(np.mean(roi_vals)) if roi_vals else float("nan")
    n, n_clv = len(scored), len(clv_rows)
    missing_share = (len(missing) / n) if n else 0.0
    ages = [float(r["close_age_min"]) for r in scored if r.get("close_age_min") is not None]
    per_market: Dict[str, dict] = {}
    for market in MARKETS:
        ms_ = [r for r in scored if r.get("market") == market]
        mc = [float(r["clv_same_book"]) for r in ms_ if r.get("clv_same_book") is not None]
        mr = [float(r["roi_open"]) for r in ms_ if r.get("roi_open") is not None]
        per_market[market] = {"n": len(ms_), "n_clv": len(mc),
                              "mean_clv": float(np.mean(mc)) if mc else None,
                              "mean_roi": float(np.mean(mr)) if mr else None}
    # Checkpoint (Jan 15, 2027): kill early iff mean CLV <= 0, and only on an adequately sized read.
    as_of = through or (max((str(r.get("game_date_et")) for r in pool), default=""))
    if as_of and as_of >= CHECKPOINT_DATE:
        if n_clv < CHECKPOINT_MIN_N_CLV:
            checkpoint = "CHECKPOINT_UNDERPOWERED_EXTEND"
        elif mean_clv <= CHECKPOINT_KILL_MEAN_CLV:
            checkpoint = "CHECKPOINT_KILL"
        else:
            checkpoint = "CHECKPOINT_PASS_EXTEND_TO_N200"
    else:
        checkpoint = "CHECKPOINT_PENDING"
    # Binding judgment at n = 200 settled picks.
    if missing_share > CLV_MISSING_INVALID_SHARE and n:
        binding = "INVALID_CLV_MISSING_SHARE"
    elif n < JUDGMENT_N:
        binding = "PENDING_N"
    elif mean_clv >= JUDGMENT_MEAN_CLV_MIN and t_clu >= JUDGMENT_T_MIN and mean_roi > JUDGMENT_ROI_MIN:
        binding = "PROMOTE"
    else:
        binding = "KILL_NHL_GAME_ENGINE"
    weeks = 0.0
    if pool:
        days = sorted(str(r.get("game_date_et")) for r in pool)
        span = (datetime.strptime(days[-1], "%Y-%m-%d") - datetime.strptime(days[0], "%Y-%m-%d")).days + 1
        weeks = max(span / 7.0, 1.0 / 7.0)
    return {"n_settled": n, "n_pushes": len(pushes), "n_clv": n_clv, "n_clv_missing": len(missing),
            "clv_missing_share": round(missing_share, 4),
            "clv_missing_reasons": dict(Counter(r.get("clv_missing_reason") for r in missing)),
            "mean_clv": None if math.isnan(mean_clv) else mean_clv, "sd_clv": None if math.isnan(sd_clv) else sd_clv,
            "t_clv_plain": None if math.isnan(t_plain) else t_plain,
            "t_clv_clustered": None if math.isnan(t_clu) else t_clu,
            "mean_roi": None if math.isnan(mean_roi) else mean_roi,
            "close_age_min_median": float(np.median(ages)) if ages else None,
            "picks_per_week": round(len(pool) / weeks, 2) if pool else 0.0,
            "per_market": per_market, "checkpoint_verdict": checkpoint, "binding_verdict": binding,
            "temp_version": TEMP_VERSION}


def format_judgment_report(agg: dict) -> str:
    """The judgment as a read, not a new analysis (spec section 7): constants first, then the table."""
    def pct(v):
        return "n/a" if v is None else f"{v:+.3%}"

    def num(v, f="{:+.2f}"):
        return "n/a" if v is None else f.format(v)

    lines = [
        "NHL early-market shadow logger -- judgment report",
        f"Pre-registered (FIXED, Nov 11 2026 ruling): checkpoint {CHECKPOINT_DATE} is a CHECKPOINT ONLY "
        f"(early kill iff mean same-book CLV <= {CHECKPOINT_KILL_MEAN_CLV:+.1%} on n_clv >= {CHECKPOINT_MIN_N_CLV}); "
        f"binding judgment at n={JUDGMENT_N} settled picks: PROMOTE iff mean CLV >= {JUDGMENT_MEAN_CLV_MIN:+.1%} "
        f"AND date-clustered t >= {JUDGMENT_T_MIN} AND ROI at opens > {JUDGMENT_ROI_MIN:+.1%}, else KILL the NHL game engine.",
        f"Close definition: last banked game_lines snapshot strictly before puck drop (close_age_min on every row). "
        f"Temperatures: {TEMP_VERSION} {FROZEN_TEMPERATURES}.",
        "",
        f"n settled {agg['n_settled']} (pushes excluded: {agg['n_pushes']}); n with same-book CLV {agg['n_clv']}; "
        f"clv_missing {agg['n_clv_missing']} ({agg['clv_missing_share']:.1%}, reasons {agg['clv_missing_reasons']})",
        f"mean same-book CLV {pct(agg['mean_clv'])}  (t plain {num(agg['t_clv_plain'])}, "
        f"t date-clustered {num(agg['t_clv_clustered'])} -- the judgment takes the clustered t)",
        f"mean ROI at logged opens {pct(agg['mean_roi'])}",
        f"median close age {agg['close_age_min_median']} min; qualifying picks/week {agg['picks_per_week']}",
        "per market (n / n_clv / mean CLV / mean ROI):",
    ]
    for market, m in agg["per_market"].items():
        lines.append(f"  {market:9s} {m['n']:4d} / {m['n_clv']:4d} / {pct(m['mean_clv'])} / {pct(m['mean_roi'])}")
    lines += ["", f"checkpoint verdict: {agg['checkpoint_verdict']}", f"binding verdict: {agg['binding_verdict']}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------- storage (paper_track pattern)
def _read(key: str):
    from mlb_core import storage
    try:
        return storage.read_bytes(key)
    except Exception:                                                                   # noqa: BLE001
        return None


def _write(key: str, data: bytes) -> None:
    from mlb_core import storage
    storage.write_bytes(data, key)


def read_jsonl(key: str, read=_read) -> list:
    raw = read(key)
    return [json.loads(x) for x in raw.decode().splitlines() if x.strip()] if raw else []


def write_jsonl(key: str, rows: list, write=_write) -> None:
    write(key, ("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n").encode())


def _list(prefix: str) -> list:
    from mlb_core import storage
    return storage.list_keys(prefix)


# --------------------------------------------------------------------------------------------------- commands
def cmd_log(a) -> int:
    from nba.game import odds_feed
    now = datetime.now(timezone.utc)
    raw = _read(PREDICTIONS_KEY)
    if not raw:
        log.warning("no predictions payload at %s; nothing logged", PREDICTIONS_KEY)
        return 0
    payload = json.loads(raw)
    events, meta = odds_feed.read_banked_game_lines(SPORT_KEY, now)
    if not events:
        log.warning("no usable banked game_lines snapshot (%s); nothing logged", meta.get("reason"))
        return 0
    snap_ts = _parse_ts(meta.get("snapshot_ts")) if meta.get("snapshot_ts") else snapshot_time_from_key(meta.get("raw_key", ""))
    picks = build_picks(payload, events, meta.get("raw_key"), snap_ts, now, dry_run=a.dry_run)
    by_day: Dict[str, List[dict]] = {}
    for p in picks:
        by_day.setdefault(p["game_date_et"], []).append(p)
    n_new = 0
    for day, day_picks in by_day.items():
        key = PICKS_KEY.format(day=day)
        existing = read_jsonl(key)
        have = {r["pick_id"] for r in existing}
        new = [p for p in day_picks if p["pick_id"] not in have]   # first qualifying pass wins (idempotent)
        if new and not a.no_write:
            write_jsonl(key, existing + new)
        n_new += len(new)
    log.info("%d qualifying picks, %d newly logged", len(picks), n_new)
    return 0


def _result_for(pick: dict, games) -> Optional[dict]:
    """Final result for a pick from the games master (rows with date / home_abbr / away_abbr /
    home_score / away_score / home_goals_ex_so / away_goals_ex_so), or None if not final yet."""
    day = str(pick.get("game_date_et"))
    for g in games:
        if str(g.get("date"))[:10] == day and g.get("home_abbr") == pick["home"] and g.get("away_abbr") == pick["away"]:
            if g.get("home_score") is None or g.get("away_score") is None:
                return None
            return {"home_goals": g["home_score"], "away_goals": g["away_score"],
                    "home_goals_ex_so": g.get("home_goals_ex_so", g["home_score"]),
                    "away_goals_ex_so": g.get("away_goals_ex_so", g["away_score"])}
    return None


def cmd_settle(a) -> int:
    from mlb_core import storage
    from nhl.config import GAMES_MASTER
    now = datetime.now(timezone.utc)
    try:
        games = storage.read_csv(GAMES_MASTER).to_dict("records")
    except Exception as exc:                                                            # noqa: BLE001
        log.warning("games master unavailable (%r); cannot settle", exc)
        return 0
    pick_keys = [PICKS_KEY.format(day=a.day)] if a.day else _list(PICKS_PREFIX)
    for key in pick_keys:
        day = key.rsplit("/", 1)[-1].replace(".jsonl", "")
        picks = read_jsonl(key)
        if not picks:
            continue
        skey = SETTLED_KEY.format(day=day)
        done = {r["pick_id"] for r in read_jsonl(skey)}
        new = []
        for p in picks:
            if p["pick_id"] in done:
                continue
            result = _result_for(p, games)
            if result is None:
                continue
            commence = _parse_ts(p["commence_utc"])
            close = closing_snapshot_game_lines(commence) if commence else None
            s = settle_pick(p, close, result, now)
            if s is not None:
                new.append(s)
        log.info("%s: %d picks, %d newly settled", day, len(picks), len(new))
        if new and not a.no_write:
            write_jsonl(skey, read_jsonl(skey) + new)
    return 0


def cmd_report(a) -> int:
    keys = _list(SETTLED_PREFIX)
    rows = [r for k in keys for r in read_jsonl(k)]
    agg = aggregate_judgment(rows, through=a.through)
    print(format_judgment_report(agg))
    print(json.dumps(agg, indent=1, default=str))
    return 0


def cmd_fit_temperature(a) -> int:
    import os
    import pandas as pd
    events = {}
    for market in MARKETS:
        path = os.path.join(a.events_dir, f"nhl_events_{market}.csv")
        if os.path.exists(path):
            events[market] = pd.read_csv(path)
    if not events:
        print(f"no nhl_events_<market>.csv under {a.events_dir}; run "
              "`python -m nhl.game.market_eval --save-events <dir>` first")
        return 2
    artifact = fit_temperatures(events)
    print(json.dumps(artifact, indent=1))
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(artifact, fh, indent=1)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("log")
    s.add_argument("--dry-run", action="store_true", help="label picks DRY_RUN (excluded from the judgment pool)")
    s.add_argument("--no-write", action="store_true")
    t = sub.add_parser("settle")
    t.add_argument("--day", default=None, help="ET game day of the picks file (default: all pick days)")
    t.add_argument("--no-write", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--through", default=None, help="only picks with game_date_et <= this ET date")
    f = sub.add_parser("fit-temperature")
    f.add_argument("--events-dir", required=True, help="dir written by nhl.game.market_eval --save-events")
    f.add_argument("--out", default=None, help="write the artifact JSON here (e.g. temperature_v1.json)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return {"log": cmd_log, "settle": cmd_settle, "report": cmd_report,
            "fit-temperature": cmd_fit_temperature}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
