"""
ev.prd04_openers -- PRD-04 slice 4: the opener cohort machinery, SHADOW ONLY (adopted Nov 15, 2026;
RUL-20261115-prd04-officials-scope rounds 2 + 3; build spec ~/workspace/beezy/prd-04-build-spec-2026-11-15.md).

Slices 1-3 (ev.prd04_officials) judge each board snapshot on its own. This module adds the time dimension the
opener thesis needs: it tracks every tracked market's first >= 3-book sighting across passes, classifies locks
into the OPENER cohort (market_age <= 45 min) or the INTRADAY cohort (after the opener window for that market
closes), and logs the c = 0 line-shopping control on the same slates -- all to the shadow cohort tape
PRD04Openers/{ct_day}/{sport}.json. Nothing here feeds the Daily Card, the PROPS ledger, Discord, or a bet.

Polling (round 2): league windows in CT with 10-minute polls -- NBA Wave 1 22:30-01:30, NBA Wave 2 + NHL
mornings 09:00-13:00, NFL Wed 12:00-16:00 and Sun 07:30-10:00 -- over the existing hourly baseline. The windows
are served by a DEDICATED shadow runner (this module's main(), provisioned by
deploy/setup_prd04_opener_shadow.sh) whose only outputs are shadow tapes; the live jobs' schedules and effects
are untouched, and this module is also called from ev.model_picks' shadow block so the hourly baseline keeps
the cohort state warm. Payload freshness: <= 900s inside a window, <= 1800s intraday; a staler payload locks
nothing.

Cohort locking (round 2): the first qualifying sighting in a cohort locks permanently at that price; an
OPENER lock suppresses the INTRADAY duplicate (one lock per game/player/stat/side across cohorts); a row
rejected at open stays INTRADAY-eligible. OPENER locks apply the round-2 gate parameters on top of the
PRD-04 verdict: Tier 1 + Tier 2 markets only, n_books >= 3 (the cohort's adopted relaxation of the gate's
4-book floor at exactly 3 books), dispersion <= 0.045 at 3 books / <= 0.060 at 4+, mins_to_start >= 180
(>= 600 for overnight NBA), the unconfirmed-sharp buffer (market_ev >= 3.5% and blend_ev >= 3.0% when the
sharp check bypassed), and the lineup/injury guard. INTRADAY locks require the unmodified PRD-04 verdict.

Every lock record carries the full logging field set: lock_cohort, n_books_at_lock, disp_at_lock,
sharp_status_at_lock, open_cons_fair, and CLV_to_1h / CLV_to_close / Control_Delta_CLV / Adverse_News_Rate --
the four measurement fields are stamped None at lock time and filled by the judging pass once the T+60m and
T-15m references exist (the lock is immutable; measurements never rewrite the locked price).

The c = 0 control is computed at board assembly (ev.daily_projections.prd04_control_fields, stamped on the
row, because per-book quotes do not survive onto the board) and locked here on first sighting per
market + control side: the best non-DFS/non-offshore book beating its leave-self-out consensus by >= +3.0%,
no model involved -- the attribution baseline for Prong 2 of the opener proof.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")

OPENER_KEY_FMT = "PRD04Openers/{day}/{sport}.json"

# -- polling windows (CT) and freshness --------------------------------------------------------------------------------
WAVE1_START, WAVE1_END = time(22, 30), time(1, 30)          # NBA overnight openers (crosses midnight)
MORNING_START, MORNING_END = time(9, 0), time(13, 0)        # NBA wave 2 + NHL mornings
NFL_WED_START, NFL_WED_END = time(12, 0), time(16, 0)       # Wednesday (weekday 2)
NFL_SUN_START, NFL_SUN_END = time(7, 30), time(10, 0)       # Sunday (weekday 6)
POLL_INTERVAL_MIN = 10
FRESHNESS_WINDOW_SEC = 900
FRESHNESS_INTRADAY_SEC = 1800
BASELINE_MINUTE_CT = 40                                     # the hourly baseline tick (the :40 cadence)

# -- OPENER cohort parameters (round 2) ---------------------------------------------------------------------------------
OPENER_MAX_MARKET_AGE_MIN = 45.0
OPENER_MIN_BOOKS = 3
OPENER_DISP_CAP_3_BOOKS = 0.045
OPENER_DISP_CAP_4_BOOKS = 0.060
OPENER_MIN_MINS_TO_START = 180.0
OPENER_MIN_MINS_OVERNIGHT_NBA = 600.0
UNCONFIRMED_MIN_MARKET_EV = 0.035
UNCONFIRMED_MIN_BLEND_EV = 0.030
B2B_BLOCK_MIN = 60.0
INJURY_BLOCK_STATUSES = frozenset({"QUESTIONABLE", "DOUBTFUL", "GTD", "Q", "D", "DTD"})


def opener_key(sport: str, day: str) -> str:
    return OPENER_KEY_FMT.format(day=day, sport=sport)


def poll_window(sport: str, now: datetime) -> str | None:
    """The opener polling window `sport` is in at `now`, or None outside every window."""
    sport = str(sport or "").lower()
    ct = now.astimezone(_CT)
    t = ct.time()
    if sport == "nba" and (t >= WAVE1_START or t < WAVE1_END):
        return "nba_wave1"
    if sport in ("nba", "nhl") and MORNING_START <= t < MORNING_END:
        return "morning"
    if sport == "nfl" and ct.weekday() == 2 and NFL_WED_START <= t < NFL_WED_END:
        return "nfl_wed"
    if sport == "nfl" and ct.weekday() == 6 and NFL_SUN_START <= t < NFL_SUN_END:
        return "nfl_sun"
    return None


def poll_due(sport: str, now: datetime) -> bool:
    """Whether a shadow-runner tick at `now` should do work for `sport`: every tick inside an opener
    window (the scheduler fires every POLL_INTERVAL_MIN minutes), otherwise only the hourly baseline
    tick at :40 CT."""
    if poll_window(sport, now) is not None:
        return True
    return now.astimezone(_CT).minute == BASELINE_MINUTE_CT


def freshness_limit_sec(sport: str, now: datetime) -> int:
    """900s inside an opener window, 1800s intraday (round 2, tightened from the live 45 min)."""
    return FRESHNESS_WINDOW_SEC if poll_window(sport, now) is not None else FRESHNESS_INTRADAY_SEC


def payload_fresh_prd04(payload: dict | None, sport: str, now: datetime) -> bool:
    from ev import daily_projections as DP          # lazy, module-load order
    age_min = DP.payload_age_min(payload, now)
    return age_min is not None and age_min * 60.0 <= freshness_limit_sec(sport, now)


# -- cohort conditions ----------------------------------------------------------------------------------------------------

def market_key(r: dict) -> str:
    """The dedupe identity of a market: one lock per (game, player, stat, side) across cohorts (round 2)."""
    return "|".join(str(r.get(k) or "") for k in ("game_key", "player_norm", "stat", "side"))


def disp_cap(n_books) -> float | None:
    if n_books is None:
        return None
    if n_books == 3:
        return OPENER_DISP_CAP_3_BOOKS
    if n_books >= 4:
        return OPENER_DISP_CAP_4_BOOKS
    return None


def injury_guard(r: dict) -> str | None:
    """The opener lineup/injury guard (round 2): a lock is blocked while the player carries QUESTIONABLE /
    DOUBTFUL / GTD on the latest status feed, or the player's team finished a back-to-back game < 60 min
    ago (rest/lineup news has not settled). Status sources on today's rows: `lineup_status` (NHL, RotoWire:
    OUT/IR/DTD -- DTD is the day-to-day GTD equivalent) and `injury_status` when a feed stamps it;
    `b2b_finished_min_ago` is read when stamped. Returns the block reason or None."""
    from ev import model_picks as MP          # lazy, module-load order
    for field in ("injury_status", "lineup_status"):
        s = str(r.get(field) or "").strip().upper()
        if s in INJURY_BLOCK_STATUSES:
            return f"injury_guard_{field}_{s.lower()}"
    b2b = MP._num(r.get("b2b_finished_min_ago"))
    if b2b is not None and b2b < B2B_BLOCK_MIN:
        return "injury_guard_back_to_back_within_60m"
    return None


def opener_conditions(r: dict, verdict: dict, age_min: float | None, now: datetime) -> list[str]:
    """The round-2 OPENER gate parameters on top of a shadow verdict computed with cohort='opener'.
    Returns the failed conditions (empty = the row may lock as OPENER, subject to the verdict itself)."""
    from ev import model_picks as MP          # lazy, module-load order
    out: list[str] = []
    if verdict.get("tier") not in (1, 2):
        out.append("tier_not_opener_eligible")
    if age_min is None or age_min > OPENER_MAX_MARKET_AGE_MIN:
        out.append("market_too_old")
    overnight = poll_window(str(r.get("sport") or ""), now) == "nba_wave1"
    min_start = OPENER_MIN_MINS_OVERNIGHT_NBA if overnight else OPENER_MIN_MINS_TO_START
    mts = MP._num(r.get("mins_to_start"))
    if mts is None or mts < min_start:
        out.append("too_close_to_start")
    n_books = MP._num(r.get("n_books"))
    cap = disp_cap(int(n_books)) if n_books is not None else None
    if n_books is None or n_books < OPENER_MIN_BOOKS:
        out.append("n_books_below_3")
    disp = MP._num(r.get("cons_disp"))
    if cap is not None and (disp is None or disp > cap):
        out.append("disp_above_cap")
    if verdict.get("sharp_status") == "opener_bypass":
        # the unconfirmed-sharp buffer: a bypassed lock must clear tighter EV bars than a confirmed one
        mev = MP._num(r.get("market_ev"))
        if mev is None or mev < UNCONFIRMED_MIN_MARKET_EV:
            out.append("unconfirmed_market_ev_below_buffer")
        bev = verdict.get("blend_ev_prd04")
        if bev is None or bev < UNCONFIRMED_MIN_BLEND_EV:
            out.append("unconfirmed_blend_ev_below_buffer")
    guard = injury_guard(r)
    if guard:
        out.append(guard)
    return out


def _verdict_reasons_for_cohort(verdict: dict, r: dict, cohort: str) -> list[str]:
    """The verdict's own failed checks as they apply to a cohort lock. The OPENER cohort adopts the
    round-2 relaxation of the book floor at exactly 3 books, so the gate's n_books<4 check is waived
    there (and only there); every other failed check binds both cohorts."""
    from ev import prd04_officials as P4      # lazy, module-load order
    reasons = list(verdict.get("reasons") or [])
    if cohort == "OPENER":
        from ev import model_picks as MP          # lazy, module-load order
        if MP._num(r.get("n_books")) == OPENER_MIN_BOOKS:
            reasons = [x for x in reasons if x != f"n_books<{P4.MIN_BOOKS}"]
    return reasons


# -- lock records -----------------------------------------------------------------------------------------------------------

def _side_fair(p_over, side):
    if p_over is None:
        return None
    return p_over if side == "over" else 1.0 - p_over


def lock_record(r: dict, verdict: dict, cohort: str, age_min: float | None, first_seen: str | None,
                now: datetime) -> dict:
    """One immutable cohort lock, carrying the full round-2 logging field set. The four measurement
    fields are None at lock; the judging pass fills them from later references without touching the
    locked price."""
    from ev import model_picks as MP          # lazy, module-load order
    side = str(r.get("side") or "")
    best = MP._best(r, side) if side in ("over", "under") else None
    return {
        "record_key": f"{market_key(r)}|{cohort}",
        "lock_cohort": cohort,
        "locked_at": now.isoformat(),
        "sport": r.get("sport"), "stat": r.get("stat"), "player": r.get("player"),
        "player_norm": r.get("player_norm"), "team": r.get("team"), "opp": r.get("opp"),
        "game_key": r.get("game_key"), "commence": r.get("commence"), "line": r.get("line"),
        "side": side,
        "american": best[1] if best else None, "book": best[0] if best else None,
        "tier": verdict.get("tier"), "floor": verdict.get("floor"), "beezy_score": verdict.get("score"),
        "n_books_at_lock": r.get("n_books"), "disp_at_lock": r.get("cons_disp"),
        "sharp_status_at_lock": verdict.get("sharp_status"), "sharp_tag": verdict.get("sharp_tag"),
        "open_cons_fair": _side_fair(verdict.get("p_cons_w_over"), side),
        "p_blend_side": verdict.get("p_blend_side_prd04"), "blend_ev": verdict.get("blend_ev_prd04"),
        "market_ev": r.get("market_ev"), "model_edge": r.get("model_edge"),
        "market_age_min_at_lock": None if age_min is None else round(age_min, 2),
        "first_seen_at": first_seen,
        "CLV_to_1h": None, "CLV_to_close": None, "Control_Delta_CLV": None, "Adverse_News_Rate": None,
    }


def control_record(r: dict, now: datetime) -> dict | None:
    """A c = 0 line-shopping control lock from the row's board-assembly stamps, or None when no book
    qualified on this sighting. Locked on first sighting per market + control side, immutable like the
    cohort locks; its CLV fields are filled by the same judging pass."""
    if not r.get("control_book") or not r.get("control_side"):
        return None
    key = f"{market_key(r)}|CONTROL|{r.get('control_side')}"
    return {
        "record_key": key,
        "lock_cohort": "CONTROL",
        "locked_at": now.isoformat(),
        "sport": r.get("sport"), "stat": r.get("stat"), "player": r.get("player"),
        "player_norm": r.get("player_norm"), "team": r.get("team"), "opp": r.get("opp"),
        "game_key": r.get("game_key"), "commence": r.get("commence"), "line": r.get("line"),
        "side": r.get("control_side"),
        "control_book": r.get("control_book"), "control_side": r.get("control_side"),
        "control_american": r.get("control_american"), "control_ev": r.get("control_ev"),
        "control_fair_side": r.get("control_fair_side"), "control_n_books": r.get("control_n_books"),
        "n_books_at_lock": r.get("n_books"), "disp_at_lock": r.get("cons_disp"),
        "CLV_to_1h": None, "CLV_to_close": None,
    }


# -- the cohort pass (pure over an explicit state) ---------------------------------------------------------------------------

def empty_state() -> dict:
    return {"first_seen": {}, "locks": {}}


def cohort_pass(payload: dict, now: datetime, state: dict) -> tuple[list[dict], dict, dict]:
    """One pass over a board payload: update first->=3-book sightings, lock what qualifies, lock control
    rows. Mutates and returns `state` ({"first_seen": {key: iso}, "locks": {record_key: record}}).
    Only rows in the live path's tracked funnel are cohort business (the same funnel slices 1-3 judge)."""
    from ev import model_picks as MP          # lazy, module-load order
    from ev import prd04_officials as P4      # lazy, module-load order
    first_seen: dict = state.setdefault("first_seen", {})
    locks: dict = state.setdefault("locks", {})
    fresh: list[dict] = []
    stats = {"tracked": 0, "locks_opener": 0, "locks_intraday": 0, "control_locks": 0}
    for r in payload.get("rows") or []:
        live_tier = MP.qualifies(r, now)
        if live_tier is None:
            continue
        stats["tracked"] += 1
        key = market_key(r)
        n_books = MP._num(r.get("n_books"))
        if n_books is not None and n_books >= OPENER_MIN_BOOKS and key not in first_seen:
            first_seen[key] = now.isoformat()
        fs = first_seen.get(key)
        age_min = None
        if fs:
            try:
                age_min = (now - datetime.fromisoformat(fs)).total_seconds() / 60.0
            except ValueError:
                age_min = None
        # the c = 0 control locks independently of the model row's fate -- that independence is the point
        ctl = control_record(r, now)
        if ctl is not None and ctl["record_key"] not in locks:
            locks[ctl["record_key"]] = ctl
            fresh.append(ctl)
            stats["control_locks"] += 1
        if any(k in locks for k in (f"{key}|OPENER", f"{key}|INTRADAY")):
            continue                                    # one lock per market across cohorts; first wins
        locked = None
        if age_min is not None and age_min <= OPENER_MAX_MARKET_AGE_MIN:
            v = P4.shadow_verdict(r, live_tier=live_tier, cohort="opener")
            if not _verdict_reasons_for_cohort(v, r, "OPENER") \
                    and not opener_conditions(r, v, age_min, now):
                locked = lock_record(r, v, "OPENER", age_min, fs, now)
                stats["locks_opener"] += 1
        if locked is None and age_min is not None and age_min > OPENER_MAX_MARKET_AGE_MIN:
            v = P4.shadow_verdict(r, live_tier=live_tier)
            if v["would_be_official"]:
                locked = lock_record(r, v, "INTRADAY", age_min, fs, now)
                stats["locks_intraday"] += 1
        if locked is not None:
            locks[locked["record_key"]] = locked
            fresh.append(locked)
    return fresh, state, stats


# -- state storage (merge-on-write so the hourly baseline and the window runner converge) -------------------------------------

def merge_states(stored: dict, working: dict) -> dict:
    """Earliest first_seen wins; a stored lock is never displaced by a working one."""
    out = {"first_seen": dict(stored.get("first_seen") or {}), "locks": dict(stored.get("locks") or {})}
    for k, v in (working.get("first_seen") or {}).items():
        if k not in out["first_seen"] or str(v) < str(out["first_seen"][k]):
            out["first_seen"][k] = v
    for k, v in (working.get("locks") or {}).items():
        out["locks"].setdefault(k, v)
    return out


def load_state(sport: str, day: str) -> dict:
    from mlb_core import storage
    key = opener_key(sport, day)
    try:
        if storage.exists(key):
            doc = json.loads(storage.read_bytes(key).decode("utf-8")) or {}
            return {"first_seen": doc.get("first_seen") or {}, "locks": doc.get("locks") or {}}
    except Exception:                                                                    # noqa: BLE001
        log.warning("prd04_openers: state read failed for %s", sport, exc_info=True)
    return empty_state()


def save_state(sport: str, day: str, state: dict) -> None:
    from mlb_core import storage
    merged = merge_states(load_state(sport, day), state)
    doc = {"date": day, "sport": sport, "first_seen": merged["first_seen"], "locks": merged["locks"],
           "records": sorted(merged["locks"].values(), key=lambda x: str(x.get("record_key")))}
    storage.write_bytes(json.dumps(doc, indent=2, default=str).encode("utf-8"), opener_key(sport, day))


def run_opener_pass(sport: str, payload: dict | None, now: datetime, dry_run: bool = False) -> dict:
    """The full slice-4 pass for one sport: freshness gate, cohort pass over the persisted state, tape
    write. Never raises -- a shadow failure must not sink any caller."""
    out: dict = {"sport": sport}
    try:
        if payload is None:
            return {**out, "status": "missing_board"}
        if not payload_fresh_prd04(payload, sport, now):
            return {**out, "status": "stale_board"}
        day = now.astimezone(_CT).strftime("%Y-%m-%d")
        state = load_state(sport, day)
        fresh, state, stats = cohort_pass(payload, now, state)
        if not dry_run:
            save_state(sport, day, state)
        return {**out, "status": "ok", "window": poll_window(sport, now), "new_records": len(fresh), **stats}
    except Exception:                                                                    # noqa: BLE001
        log.warning("prd04_openers: pass failed for %s", sport, exc_info=True)
        return {**out, "status": "error"}


# -- the shadow-only runner -------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    """The dedicated opener shadow runner: reads the stored board, runs the cohort pass for every sport
    whose poll is due, writes only PRD04Openers tapes. It imports no picks/ledger/Discord code path and
    cannot log a bet or post a card. Inert unless PRD04_SHADOW is on."""
    ap = argparse.ArgumentParser(description="PRD-04 opener cohort shadow runner (shadow only)")
    ap.add_argument("--sport", default="all")
    ap.add_argument("--force", action="store_true", help="run even when no poll is due (manual checks)")
    ap.add_argument("--dry-run", action="store_true", help="compute but write nothing")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from ev import daily_projections as DP          # lazy, module-load order
    from ev import model_picks as MP                # lazy, module-load order
    from ev import prd04_officials as P4            # lazy, module-load order
    if not P4.shadow_enabled():
        print(json.dumps({"status": "disabled", "reason": "PRD04_SHADOW is off"}))
        return 0
    now = datetime.now(timezone.utc)
    sports = MP.SPORTS if a.sport == "all" else (a.sport,)
    for sport in sports:
        if not a.force and not poll_due(sport, now):
            print(json.dumps({"sport": sport, "status": "not_due", "window": poll_window(sport, now)}))
            continue
        res = run_opener_pass(sport, DP.read_precomputed(sport), now, dry_run=a.dry_run)
        print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
