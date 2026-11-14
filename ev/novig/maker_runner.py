"""
ev.novig.maker_runner -- the NOV-03 maker study launch runner (python -m ev.novig.maker_runner).

PAPER SANDBOX ONLY:
  * Quoting is strictly paper-only (api.paper.novig.com, signed with trading key).
  * Real-money production is refused by the client itself; no production client is ever built.
  * Public read-only catalog (GET /v3/public/*) provides books and market definitions.
  * No ParlayAPI calls or credits: consensus and projections are read as published to GCS.
  * maker.decide() is the ONLY quoting authority (waterfall mode MAKER_PAPER only).
  * Confirm-cancel-before-replace is strictly enforced.
  * NFL anchors on pure P_cons (c=0 for all NFL stats; model-consensus disagreement guard is
    inert by design for NFL).
  * NHL news_surcharge is False (no wired feed).
  * In-news window markout is evaluated against in-memory status changes / lockouts.
  * Book values at post (novig_bid_at_post, novig_ask_at_post) are recorded for OUR SIDE,
    consistent with p_cons_at_post / p_blend_at_post which are explicitly side probabilities.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import requests

from ev.novig import exec as nx
from ev.novig import maker, maker_bank, maker_study, nba_snapshot, nhl_snapshot
from ev.novig import shadow_bank as sb
from ev.novig import shadow_job
from ev.novig.client import NovigAPIError, NovigClient
from ev.novig.liability import Exposure, check_liability, ledger_exposures, load_liability_config
from ev.novig.maker_venue import MakerPaperVenue, UNIT
from ev.novig.paper_orders import PaperRiskGate
from ev.props import norm_name
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb_core import storage

log = logging.getLogger("ev.novig.maker_runner")

MAKER_MODE = "novig_maker"
STUDY_TAG = "nov03"
GOALIE_CACHE_KEY = "EVSweep/nhl_games/confirmed_goalies.json"
NBA_INJURY_LATEST = "NBA/injuries/espn/latest.json"
NBA_UNCERTAIN_STATUSES = frozenset({"Questionable", "Day-To-Day", "Doubtful"})


def jlog(step: str, **kw) -> None:
    """JSON line logger mirroring nba_job conventions."""
    payload = dict(executor_step=step, study=STUDY_TAG, **kw)
    print(json.dumps(payload, default=str), flush=True)


def _write_paper_env() -> None:
    """Materialize ~/.novig/paper.env from environment secrets if present."""
    if not (os.environ.get("NOVIG_PAPER_TRADING_KEY_ID") and os.environ.get("NOVIG_PAPER_TRADING_KEY_PATH")):
        return
    home_novig = os.path.expanduser("~/.novig")
    os.makedirs(home_novig, mode=0o700, exist_ok=True)
    env_path = os.path.join(home_novig, "paper.env")
    with open(env_path, "w") as fh:
        fh.write("NOVIG_PAPER_TRADING_KEY_ID=" + os.environ["NOVIG_PAPER_TRADING_KEY_ID"] + "\n")
        fh.write("NOVIG_PAPER_TRADING_KEY_PATH=" + os.environ["NOVIG_PAPER_TRADING_KEY_PATH"] + "\n")
    os.chmod(env_path, 0o600)


def _parse_ts(val: Any) -> datetime | None:
    """Parse integer ms, float s, or ISO timestamp into a timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        s = val / 1000.0 if val > 1e11 else float(val)
        return datetime.fromtimestamp(s, tz=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            try:
                f = float(val)
                s = f / 1000.0 if f > 1e11 else f
                return datetime.fromtimestamp(s, tz=timezone.utc)
            except ValueError:
                return None
    return None


@dataclass
class AssembledCandidate:
    league: str
    stat: str
    game_key: str
    player_norm: str
    player: str
    line: float
    leg: Any
    candidate: maker.MakerCandidate
    book_fair: dict
    commence: datetime
    mins_to_start: float
    snapshot_age_sec: float


# ------------------------------------------------------------------ candidate assembly helpers
# decide() evaluates every pre-book gate (books count, dispersion, disagreement guard, snapshot age,
# blackouts, tier windows) BEFORE it looks at Novig's book, so a candidate that fails one of those
# gates with a dummy wide book will fail it with the real book too. Only candidates that survive the
# dummy-book pass need a live book fetch -- this keeps a poll cycle inside its cadence when a slate
# carries hundreds of mapped legs.
_PRE_BOOK_REASONS = frozenset({
    "stat_not_approved", "ladder_never_listed", "too_few_reference_books", "dispersion_too_wide",
    "model_consensus_disagreement", "reference_snapshot_stale", "inside_t_minus_5",
    "nba_unresolved_q_before_t60", "nfl_inactives_window", "nhl_saves_goalie_unconfirmed",
    "nhl_warmup_window", "outside_tier2_window", "tier2_injury_reports_not_clean",
})


def _needs_book(mc: maker.MakerCandidate) -> bool:
    dummy = replace(mc, novig_bid_over=0.30, novig_ask_over=0.70)
    d = maker.decide(dummy)
    return not (d.mode == "ABSTAIN" and d.reasons and d.reasons[0] in _PRE_BOOK_REASONS)


def _fetch_book(public_nc, leg, pace_s: float, sleep_fn, league: str):
    """-> (novig_bid_over, novig_ask_over) floats or Nones; raises on fetch failure (caller skips)."""
    kb = public_nc.get_book(leg.market_id, depth=10)
    sleep_fn(pace_s)
    bid_over_raw = kb.best_bid(leg.over_id)
    bid_under_raw = kb.best_bid(leg.under_id)
    bid_over = float(bid_over_raw) if bid_over_raw is not None else None
    ask_over = (1.0 - float(bid_under_raw)) if bid_under_raw is not None else None
    return bid_over, ask_over


def assemble_nfl(load_snap_fn, legs: dict, now: datetime, horizon_hours: float,
                 public_nc: NovigClient, pace_s: float, sleep_fn) -> tuple[list[AssembledCandidate], dict[str, datetime]]:
    cands, game_commences = [], {}
    snap = load_snap_fn()
    if snap is None:
        log.info("NFL consensus snapshot missing; skipping NFL this poll")
        return [], {}
    age_min = shadow_job.snapshot_age_min(snap, now)
    if age_min > 25.0:
        log.info("NFL consensus snapshot stale (%.1f min > 25.0 min); skipping NFL this poll", age_min)
        return [], {}
    age_sec = age_min * 60.0

    for ev in snap.get("events", []):
        gk = ev.get("game_key")
        if not gk or not ev.get("commence"):
            continue
        try:
            comm = datetime.fromisoformat(str(ev["commence"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        game_commences[gk] = comm
        mins = (comm - now).total_seconds() / 60.0
        if mins < 5.0 or mins > horizon_hours * 60.0:
            continue

        for point in ev.get("points", []):
            stat = point.get("stat")
            if ("NFL", stat) not in maker.STAT_CFG:
                continue
            p_norm = point.get("player_norm")
            line = float(point.get("point", 0.0))
            lo = nx.leave_out(point)
            if lo is None:
                continue
            leg = legs.get((gk, stat, p_norm, line))
            if leg is None:
                continue

            p_cons = float(lo["p_over"])
            p_model = p_cons  # NFL anchors on pure P_cons (c=0)

            mc = maker.MakerCandidate(
                league="NFL",
                stat=stat,
                game_key=gk,
                player_norm=p_norm,
                line=line,
                is_alt=False,
                p_cons=p_cons,
                p_model=p_model,
                n_books=int(lo["n_books"]),
                disp=float(lo["disp"]),
                novig_bid_over=None,
                novig_ask_over=None,
                mins_to_start=mins,
                snapshot_age_sec=age_sec,
                news_surcharge=False,
                game_unresolved_q=False,
                injury_reports_clean=True,
                goalie_confirmed=True,
            )
            if _needs_book(mc):
                try:
                    bid_over, ask_over = _fetch_book(public_nc, leg, pace_s, sleep_fn, "NFL")
                except Exception as e:
                    log.warning("NFL book fetch failed for %s: %r", leg.market_id, e)
                    continue
                mc = replace(mc, novig_bid_over=bid_over, novig_ask_over=ask_over)
            cands.append(AssembledCandidate("NFL", stat, gk, p_norm, leg.player, line, leg, mc,
                                            point.get("book_fair", {}), comm, mins, age_sec))
    return cands, game_commences


def assemble_nhl(read_latest_fn, read_goalies_fn, legs: dict, now: datetime, horizon_hours: float,
                 public_nc: NovigClient, pace_s: float, sleep_fn) -> tuple[list[AssembledCandidate], dict[str, datetime]]:
    cands, game_commences = [], {}
    proj, board = read_latest_fn()
    if proj is None or not isinstance(proj.get("rows"), list):
        log.info("NHL projections missing; skipping NHL this poll")
        return [], {}
    age_min = nhl_snapshot.snapshot_age_min(proj, now)
    if age_min > 45.0:
        log.info("NHL snapshot stale (%.1f min > 45.0 min); skipping NHL this poll", age_min)
        return [], {}
    age_sec = age_min * 60.0

    # Goalie cache
    confirmed_goalies: dict[str, str] = {}
    goalie_bytes = read_goalies_fn()
    if goalie_bytes:
        try:
            gpayload = json.loads(goalie_bytes)
            g_fetched = datetime.fromisoformat(str(gpayload.get("fetched_at")).replace("Z", "+00:00"))
            g_age = (now - g_fetched).total_seconds() / 60.0
            if g_age <= 120.0:
                confirmed_goalies = gpayload.get("confirmed") or {}
        except Exception:
            confirmed_goalies = {}

    strips = nhl_snapshot._strip_fairs(board)

    for row in proj.get("rows", []):
        stat = row.get("stat")
        if ("NHL", stat) not in maker.STAT_CFG:
            continue
        if not row.get("has_line") or row.get("line") is None:
            continue
        if row.get("p_over_model") is None or not row.get("commence"):
            continue

        gk = row.get("game_key")
        try:
            comm = datetime.fromisoformat(str(row["commence"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        game_commences[gk] = comm
        mins = (comm - now).total_seconds() / 60.0
        if mins < 5.0 or mins > horizon_hours * 60.0:
            continue

        p_norm = row.get("player_norm")
        line = float(row["line"])
        strip_key = (gk, p_norm, stat, line)
        point = {"book_fair": strips.get(strip_key, {})}
        lo = nx.leave_out(point)
        if lo is None:
            continue
        leg_k = (gk, stat, p_norm, line)
        leg = legs.get(leg_k)
        if leg is None:
            continue

        # Goalie confirmation check for saves
        if stat == "saves":
            row_team = row.get("team")
            c_name = confirmed_goalies.get(row_team)
            # Match goalie day with game's ET day
            game_day = comm.astimezone(sb._ET).date().isoformat()
            payload_day = gpayload.get("day") if goalie_bytes and 'gpayload' in locals() else None
            if payload_day == game_day and c_name and norm_name(c_name) == p_norm:
                goalie_confirmed = True
            else:
                goalie_confirmed = False
        else:
            goalie_confirmed = True

        mc = maker.MakerCandidate(
            league="NHL",
            stat=stat,
            game_key=gk,
            player_norm=p_norm,
            line=line,
            is_alt=False,
            p_cons=float(lo["p_over"]),
            p_model=float(row["p_over_model"]),
            n_books=int(lo["n_books"]),
            disp=float(lo["disp"]),
            novig_bid_over=None,
            novig_ask_over=None,
            mins_to_start=mins,
            snapshot_age_sec=age_sec,
            news_surcharge=False,
            game_unresolved_q=False,
            injury_reports_clean=True,
            goalie_confirmed=goalie_confirmed,
        )
        if _needs_book(mc):
            try:
                bid_over, ask_over = _fetch_book(public_nc, leg, pace_s, sleep_fn, "NHL")
            except Exception as e:
                log.warning("NHL book fetch failed for %s: %r", leg.market_id, e)
                continue
            mc = replace(mc, novig_bid_over=bid_over, novig_ask_over=ask_over)
        cands.append(AssembledCandidate("NHL", stat, gk, p_norm, leg.player, line, leg, mc,
                                        point["book_fair"], comm, mins, age_sec))
    return cands, game_commences


def assemble_nba(read_latest_fn, read_injuries_fn, legs: dict, now: datetime, horizon_hours: float,
                 public_nc: NovigClient, pace_s: float, sleep_fn) -> tuple[list[AssembledCandidate], dict[str, datetime]]:
    cands, game_commences = [], {}
    proj, board = read_latest_fn()
    if proj is None or not isinstance(proj.get("rows"), list):
        log.info("NBA projections missing; skipping NBA this poll")
        return [], {}
    age_min = nba_snapshot.snapshot_age_min(proj, now)
    if age_min > 45.0:
        log.info("NBA snapshot stale (%.1f min > 45.0 min); skipping NBA this poll", age_min)
        return [], {}
    age_sec = age_min * 60.0

    # Injury feed
    uncertain_by_team: dict[str, list[str]] = {}
    injuries_fresh = False
    inj_pointer_bytes, inj_csv_bytes_fn = read_injuries_fn()
    if inj_pointer_bytes:
        try:
            ptr = json.loads(inj_pointer_bytes)
            checked_at_ts = datetime.fromisoformat(str(ptr.get("checked_at")).replace("Z", "+00:00"))
            if (now - checked_at_ts).total_seconds() / 60.0 <= 90.0:
                injuries_fresh = True
                csv_bytes = inj_csv_bytes_fn(ptr.get("key"))
                if csv_bytes:
                    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
                    for r in reader:
                        if r.get("status") in NBA_UNCERTAIN_STATUSES:
                            team = r.get("team_abbrev")
                            pn = norm_name(r.get("player") or "")
                            if team and pn:
                                uncertain_by_team.setdefault(team, []).append(pn)
        except Exception as e:
            log.warning("NBA injury parsing failed: %r", e)
            injuries_fresh = False

    strips = nba_snapshot._strip_fairs(board)

    for row in proj.get("rows", []):
        stat = row.get("stat")
        if ("NBA", stat) not in maker.STAT_CFG:
            continue
        if not row.get("has_line") or row.get("line") is None:
            continue
        if row.get("p_over_model") is None or not row.get("commence"):
            continue

        gk = row.get("game_key")
        try:
            comm = datetime.fromisoformat(str(row["commence"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        game_commences[gk] = comm
        mins = (comm - now).total_seconds() / 60.0
        if mins < 5.0 or mins > horizon_hours * 60.0:
            continue

        p_norm = row.get("player_norm")
        line = float(row["line"])
        strip_key = (gk, p_norm, stat, line)
        point = {"book_fair": strips.get(strip_key, {})}
        lo = nx.leave_out(point)
        if lo is None:
            continue
        leg_k = (gk, stat, p_norm, line)
        leg = legs.get(leg_k)
        if leg is None:
            continue

        # Parse game teams: "nba:YYYY-MM-DD:AWAY@HOME"
        teams_part = gk.split(":")[-1] if ":" in gk else ""
        if "@" in teams_part:
            away_team, home_team = teams_part.split("@", 1)
        else:
            away_team, home_team = "", ""

        if injuries_fresh:
            injury_reports_clean = True
            game_unresolved_q = bool(uncertain_by_team.get(away_team) or uncertain_by_team.get(home_team))
            news_surcharge = bool(uncertain_by_team.get(row.get("team")))
        else:
            # Stale / missing: fail closed both ways
            injury_reports_clean = False
            game_unresolved_q = True
            news_surcharge = False

        mc = maker.MakerCandidate(
            league="NBA",
            stat=stat,
            game_key=gk,
            player_norm=p_norm,
            line=line,
            is_alt=False,
            p_cons=float(lo["p_over"]),
            p_model=float(row["p_over_model"]),
            n_books=int(lo["n_books"]),
            disp=float(lo["disp"]),
            novig_bid_over=None,
            novig_ask_over=None,
            mins_to_start=mins,
            snapshot_age_sec=age_sec,
            news_surcharge=news_surcharge,
            game_unresolved_q=game_unresolved_q,
            injury_reports_clean=injury_reports_clean,
            goalie_confirmed=True,
        )
        if _needs_book(mc):
            try:
                bid_over, ask_over = _fetch_book(public_nc, leg, pace_s, sleep_fn, "NBA")
            except Exception as e:
                log.warning("NBA book fetch failed for %s: %r", leg.market_id, e)
                continue
            mc = replace(mc, novig_bid_over=bid_over, novig_ask_over=ask_over)
        cands.append(AssembledCandidate("NBA", stat, gk, p_norm, leg.player, line, leg, mc,
                                        point["book_fair"], comm, mins, age_sec))
    return cands, game_commences


# ------------------------------------------------------------------ runner main entry
def run(args, *, store=None, public_nc=None, paper_client=None, paper_venue=None,
        clock=None, sleep=time.sleep, snapshot_fns=None, ledger=None,
        legs_by_league=None, paper_legs_by_league=None) -> int:
    """The launch runner driver.

    Exit codes:
      0: ok / clean completion
      1: broken (preflight, banking error, or risk gate kill)
      2: un-armed or outside pre-registered study window
    """
    clock = clock or (lambda: datetime.now(timezone.utc))

    # 1. Armed check
    if not maker_study.STUDY_ARMED:
        jlog("armed", ok=False, reason="STUDY_ARMED is False")
        return 2

    # 2. Window guard
    now0 = clock()
    today_et = date.fromisoformat(sb.et_day(now0))
    if not (maker_study.WINDOW_START <= today_et <= maker_study.WINDOW_END):
        jlog("window", ok=False, reason=f"ET today {today_et} outside window [{maker_study.WINDOW_START}, {maker_study.WINDOW_END}]")
        return 2

    # 3. Store & Ledger restore
    store = store or sb.make_store(args.store, args.bucket)
    day_str = sb.et_day(now0)
    prev_day = (now0.astimezone(sb._ET) - timedelta(days=1)).date().isoformat()
    next_day = (now0.astimezone(sb._ET) + timedelta(days=1)).date().isoformat()
    if ledger is None:
        ledger = Ledger(":memory:")
    rs = sb.restore_into(ledger, store, days=[prev_day, day_str, next_day], tables=("orders", "fills", "kv"))
    known = rs["known"]
    jlog("restore", restored=str(rs["restored"]), chunks=rs["chunks"], days=[prev_day, day_str, next_day])

    # 4. Paper venue & Trading client setup
    _write_paper_env()
    if paper_venue is not None:
        venue = paper_venue
    else:
        if paper_client is None:
            try:
                from ev.novig import setup
                paper_client = setup.trading_client(setup.read_env())
            except Exception as e:
                log.exception("trading client setup failed")
                jlog("venue", ok=False, error=str(e)[:200])
                return 1
        try:
            paper_client.positions()
        except Exception as e:
            log.exception("positions preflight failed")
            jlog("venue", ok=False, error=str(e)[:200])
            return 1
        venue = MakerPaperVenue(paper_client)

    cfg = props_config()
    paper_risk = PaperRiskGate(cfg, ledger, MAKER_MODE)
    jlog("venue", ok=True, paper_risk_killed=paper_risk.killed, paper_kill_reason=paper_risk.kill_reason)

    public_nc = public_nc or NovigClient("public-data")
    liability_cfg = load_liability_config()

    # 5. Load resting states & initial reconciliation
    quote_states = maker_bank.load_quote_states(store)
    changed_quote_states: list[dict] = []
    active_resting_keys: set[str] = set()

    try:
        open_orders = venue.list_open_orders()
    except Exception as e:
        log.exception("venue list_open_orders failed at startup")
        jlog("venue", ok=False, error=str(e)[:200])
        return 1
    open_by_oid = {str(o.get("orderId") or o.get("id")): o for o in open_orders if o.get("orderId") or o.get("id")}
    open_by_cid = {str(o.get("clientId")): o for o in open_orders if o.get("clientId")}

    for k, st in quote_states.items():
        if st.get("terminal_status") in ("RESTING", "UNKNOWN", "PARTIAL"):
            cid = st.get("client_id")
            oid = st.get("order_id")
            is_open = (oid in open_by_oid) or (cid in open_by_cid)
            if not oid and cid in open_by_cid:
                matched_ord = open_by_cid[cid]
                st["order_id"] = str(matched_ord.get("orderId") or matched_ord.get("id"))
                oid = st["order_id"]

            fills = venue.fills_for(cid) if cid else []
            if fills:
                fill_qty_units = sum(int(f.get("qty", 0)) for f in fills) / UNIT
                total_cost = sum(float(f.get("cost", 0.0)) for f in fills)
                st["fill_qty_units"] = fill_qty_units
                st["p_fill"] = round(total_cost / fill_qty_units, 6) if fill_qty_units > 0 else None
                first_ts = fills[0].get("ts")
                parsed_ts = _parse_ts(first_ts)
                if parsed_ts and not st.get("t_fill"):
                    st["t_fill"] = parsed_ts.isoformat()

            wanted_units = float(st.get("contracts_units") or 0.0)
            fill_qty = float(st.get("fill_qty_units") or 0.0)

            if fill_qty >= wanted_units - 1e-9 and wanted_units > 0:
                st["terminal_status"] = "FILLED"
                if not st.get("t_end"):
                    st["t_end"] = now0.isoformat()
                changed_quote_states.append(st)
            elif is_open:
                st["terminal_status"] = "PARTIAL" if fill_qty > 0 else "RESTING"
                active_resting_keys.add(k)
            else:
                # Order not open on venue
                if fill_qty > 0:
                    st["terminal_status"] = "PARTIAL"
                    if not st.get("t_end"):
                        st["t_end"] = now0.isoformat()
                    changed_quote_states.append(st)
                elif st.get("terminal_status") == "UNKNOWN":
                    # Stays UNKNOWN with no venue trace
                    pass
                else:
                    st["terminal_status"] = "CANCELLED"
                    if not st.get("t_end"):
                        st["t_end"] = now0.isoformat()
                    if not st.get("cancel_reason"):
                        st["cancel_reason"] = "order_closed"
                    changed_quote_states.append(st)

    # Capture tracking: the study's measurements (T-15m close, fill +5m/+30m markouts) usually fall
    # due AFTER a quote leaves the resting set (filled / cancelled) and often in a later firing, so
    # terminal-but-incomplete states stay on a capture watch list across firings.
    def _capture_incomplete(st) -> bool:
        if st.get("p_cons_close") is None:
            return True
        if float(st.get("fill_qty_units") or 0.0) > 0:
            return st.get("p_cons_fill_plus_5m") is None or st.get("p_cons_fill_plus_30m") is None
        return False

    capture_keys: set[str] = {
        k for k, st in quote_states.items()
        if st.get("terminal_status") in ("RESTING", "PARTIAL", "FILLED", "CANCELLED", "EXPIRED")
        and _capture_incomplete(st)
    }

    # Startup kill check: if killed, cancel all resting, bank, return 1
    if paper_risk.killed:
        jlog("kill_switch", ok=False, kill_reason=paper_risk.kill_reason)
        for k in list(active_resting_keys):
            st = quote_states[k]
            if st.get("order_id"):
                try:
                    venue.cancel(st["order_id"])
                except Exception as exc:
                    log.warning("failed to cancel resting order %s on kill: %r", st["order_id"], exc)
            st["terminal_status"] = "CANCELLED"
            st["t_end"] = now0.isoformat()
            st["cancel_reason"] = f"paper_risk_killed:{paper_risk.kill_reason}"
            changed_quote_states.append(st)
        active_resting_keys.clear()
        try:
            sb.bank(ledger, store, known=known, modes=(MAKER_MODE,))
            if changed_quote_states:
                maker_bank.bank_rows(store, [("quote_state", s) for s in changed_quote_states], now=now0)
        except Exception:
            pass
        return 1

    # Snapshot and feed injectors
    snap_fns = snapshot_fns or {}
    load_nfl_fn = snap_fns.get("NFL", shadow_job.load_snapshot)
    read_nhl_fn = snap_fns.get("NHL", nhl_snapshot.read_latest)
    read_nba_fn = snap_fns.get("NBA", nba_snapshot.read_latest)
    read_goalies_fn = snap_fns.get("nhl_goalies", lambda: storage.read_bytes(GOALIE_CACHE_KEY) if storage.exists(GOALIE_CACHE_KEY) else None)

    def default_nba_injuries():
        ptr_b = storage.read_bytes(NBA_INJURY_LATEST) if storage.exists(NBA_INJURY_LATEST) else None
        csv_fn = lambda key: storage.read_bytes(key) if key and storage.exists(key) else None
        return ptr_b, csv_fn
    read_injuries_fn = snap_fns.get("nba_injuries", default_nba_injuries)

    # 6. Polling loop state
    start_time = clock()
    enabled_leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]
    game_commence_map: dict[str, datetime] = {}
    game_lockout_until: dict[str, datetime] = {}
    game_post_news_ok: dict[str, bool] = {}
    status_change_times: dict[str, datetime] = {}
    prev_snapshot_games: set[str] = set()
    prev_commence_map: dict[str, datetime] = {}

    last_legs_refresh: dict[str, datetime] = {lg: now0 for lg in (legs_by_league or {})}
    prod_legs: dict[str, dict] = dict(legs_by_league or {})
    paper_legs: dict[str, dict] = dict(paper_legs_by_league or {})
    if paper_legs:
        all_paper = {}
        for lg_dict in paper_legs.values():
            all_paper.update(lg_dict)
        venue.set_legs(all_paper)

    bank_errors = 0
    poll_idx = 0
    total_posted = 0
    total_cancelled = 0

    while True:
        poll_idx += 1
        now = clock()
        if (now - start_time).total_seconds() >= args.max_runtime_sec:
            break

        # Check breaker
        if paper_risk.killed:
            jlog("breaker_tripped", kill_reason=paper_risk.kill_reason)
            for k in list(active_resting_keys):
                st = quote_states[k]
                if st.get("order_id"):
                    try:
                        venue.cancel(st["order_id"])
                    except Exception as exc:
                        log.warning("cancel failed: %r", exc)
                st["terminal_status"] = "CANCELLED"
                st["t_end"] = now.isoformat()
                st["cancel_reason"] = f"paper_risk_killed:{paper_risk.kill_reason}"
                changed_quote_states.append(st)
                total_cancelled += 1
            active_resting_keys.clear()
            break

        # Refresh legs per league at most every 600s
        for lg in enabled_leagues:
            last_t = last_legs_refresh.get(lg)
            if last_t is None or (now - last_t).total_seconds() > 600.0:
                try:
                    p_legs = nx.load_novig_legs(public_nc, now, lg)
                    prod_legs[lg] = p_legs
                    v_legs = nx.load_novig_legs(venue.client, now, lg)
                    paper_legs[lg] = v_legs
                    last_legs_refresh[lg] = now
                except Exception as e:
                    log.warning("leg loading failed for %s: %r", lg, e)
        # Update venue with all paper legs
        all_paper = {}
        for lg_dict in paper_legs.values():
            all_paper.update(lg_dict)
        venue.set_legs(all_paper)

        # Assemble candidates across leagues
        assembled: list[AssembledCandidate] = []
        current_snapshot_games: set[str] = set()
        current_commence_map: dict[str, datetime] = {}
        cands_by_key: dict[tuple, AssembledCandidate] = {}

        for lg in enabled_leagues:
            if lg == "NFL":
                c_nfl, g_comm = assemble_nfl(load_nfl_fn, prod_legs.get("NFL", {}), now,
                                             args.horizon_hours, public_nc, args.pace_s, sleep)
                assembled.extend(c_nfl)
                current_snapshot_games.update(g_comm.keys())
                for gk, comm in g_comm.items():
                    game_commence_map[gk] = comm
                    current_commence_map[gk] = comm
            elif lg == "NHL":
                c_nhl, g_comm = assemble_nhl(read_nhl_fn, read_goalies_fn, prod_legs.get("NHL", {}), now,
                                             args.horizon_hours, public_nc, args.pace_s, sleep)
                assembled.extend(c_nhl)
                current_snapshot_games.update(g_comm.keys())
                for gk, comm in g_comm.items():
                    game_commence_map[gk] = comm
                    current_commence_map[gk] = comm
            elif lg == "NBA":
                c_nba, g_comm = assemble_nba(read_nba_fn, read_injuries_fn, prod_legs.get("NBA", {}), now,
                                             args.horizon_hours, public_nc, args.pace_s, sleep)
                assembled.extend(c_nba)
                current_snapshot_games.update(g_comm.keys())
                for gk, comm in g_comm.items():
                    game_commence_map[gk] = comm
                    current_commence_map[gk] = comm

        for ac in assembled:
            cands_by_key[(ac.game_key, ac.player_norm, ac.stat, ac.line)] = ac

        # Status change tracking: a game that vanishes from its league snapshot before start, or
        # whose commence time moves, is treated as a whole-game status change (ruling Q3).
        status_changed_games: set[str] = set()
        for gk, comm in game_commence_map.items():
            vanished = gk in prev_snapshot_games and gk not in current_snapshot_games
            moved = (gk in prev_commence_map and gk in current_commence_map
                     and current_commence_map[gk] != prev_commence_map[gk])
            if (vanished or moved) and now < comm - timedelta(minutes=5):
                status_changed_games.add(gk)
                game_lockout_until[gk] = maker.lockout_until(now)
                game_post_news_ok[gk] = False
                status_change_times[gk] = now
        prev_commence_map = dict(current_commence_map)

        # Update post-news flag for games currently in snapshot with >= 4 books
        for ac in assembled:
            if ac.candidate.n_books >= maker.MIN_BOOKS:
                game_post_news_ok[ac.game_key] = True

        prev_snapshot_games = set(current_snapshot_games)

        # ------------------------------------------------------------------ step 4: manage resting quotes
        cancelled_this_poll = 0
        for k in list(active_resting_keys):
            st = quote_states[k]
            cid = st["client_id"]
            oid = st["order_id"]

            # 4a. Check fills
            fills = venue.fills_for(cid) if cid else []
            if fills:
                fill_qty_units = sum(int(f.get("qty", 0)) for f in fills) / UNIT
                total_cost = sum(float(f.get("cost", 0.0)) for f in fills)
                st["fill_qty_units"] = fill_qty_units
                st["p_fill"] = round(total_cost / fill_qty_units, 6) if fill_qty_units > 0 else None
                first_ts = fills[0].get("ts")
                parsed_ts = _parse_ts(first_ts)
                if parsed_ts and not st.get("t_fill"):
                    st["t_fill"] = parsed_ts.isoformat()

                for f in fills:
                    fid = str(f.get("fillId"))
                    ledger.record_fill(
                        fill_id=fid,
                        order_id=str(f.get("orderId") or oid or ""),
                        ticker=str(f.get("marketId") or st.get("game_key")),
                        side=st["side"],
                        count=int(f.get("qty", 0)) / UNIT,
                        price=float(f.get("cost", 0.0)) / (int(f.get("qty", 0)) / UNIT) if int(f.get("qty", 0)) > 0 else 0.0,
                        fee=0.0,
                        ts=str(f.get("ts") or ""),
                        mode=MAKER_MODE,
                    )

            wanted_units = float(st.get("contracts_units") or 0.0)
            fill_qty = float(st.get("fill_qty_units") or 0.0)

            if fill_qty >= wanted_units - 1e-9 and wanted_units > 0:
                st["terminal_status"] = "FILLED"
                st["t_end"] = now.isoformat()
                changed_quote_states.append(st)
                active_resting_keys.discard(k)
                if _capture_incomplete(st):
                    capture_keys.add(k)
                continue

            # 4b. Find current point
            cand_key = (st["game_key"], st["player_norm"], st["stat"], float(st["line"]))
            cur_cand = cands_by_key.get(cand_key)
            if cur_cand:
                p_cons_side_now = cur_cand.candidate.p_cons if st["side"] == "yes" else (1.0 - cur_cand.candidate.p_cons)
                sharp_now = {}
                for b in maker.SHARP_BOOKS:
                    if b in cur_cand.book_fair:
                        f_over = cur_cand.book_fair[b]
                        sharp_now[b] = f_over if st["side"] == "yes" else (1.0 - f_over)
                n_books_now = cur_cand.candidate.n_books
                snap_age_now = cur_cand.snapshot_age_sec
            else:
                p_cons_side_now = float(st.get("p_cons_at_post") or 0.5)
                sharp_now = {}
                n_books_now = 0
                snap_age_now = 9999.0

            # 4c. Evaluate cancel reasons
            rq = maker.RestingQuote(
                game_key=st["game_key"],
                league=st["sport"],
                stat=st["stat"],
                side=st["side"],
                bid_price=float(st["quote_price"]),
                t_post=datetime.fromisoformat(st["t_post"]),
                commence=datetime.fromisoformat(st["commence"]),
                p_cons_side_at_post=float(st["p_cons_at_post"]),
                sharp_at_post=st.get("sharp_at_post") or {},
            )
            reasons = maker.cancel_reasons(
                rq,
                now=now,
                p_cons_side_now=p_cons_side_now,
                sharp_now=sharp_now,
                n_books_now=n_books_now,
                snapshot_age_sec_now=snap_age_now,
                status_changed_game_keys=frozenset(status_changed_games),
            )

            if reasons:
                # Cancel on venue
                if oid:
                    try:
                        venue.cancel(oid)
                    except Exception as exc:
                        log.warning("venue.cancel error for %s: %r", oid, exc)

                # Confirm cancel before replace
                confirmed = False
                if oid:
                    try:
                        ord_resp = venue.get_order(oid)
                        st_name = str((ord_resp or {}).get("status", "")).upper()
                        if st_name in ("CANCELLED", "FILLED", "EXPIRED", "CLOSED", "REJECTED"):
                            confirmed = True
                        elif not st_name:
                            # Not open in list
                            cur_open = venue.list_open_orders()
                            open_ids = {str(o.get("orderId") or o.get("id")) for o in cur_open}
                            if oid not in open_ids:
                                confirmed = True
                    except NovigAPIError as e:
                        if e.status == 404:
                            confirmed = True
                    except Exception:
                        try:
                            cur_open = venue.list_open_orders()
                            open_ids = {str(o.get("orderId") or o.get("id")) for o in cur_open}
                            if oid not in open_ids:
                                confirmed = True
                        except Exception:
                            pass
                else:
                    confirmed = True

                if confirmed:
                    st["terminal_status"] = "FILLED" if fill_qty >= wanted_units - 1e-9 and wanted_units > 0 else "PARTIAL" if fill_qty > 0 else "CANCELLED"
                    st["t_end"] = now.isoformat()
                    st["cancel_reason"] = ",".join(reasons)
                    active_resting_keys.discard(k)
                    if _capture_incomplete(st):
                        capture_keys.add(k)
                    changed_quote_states.append(st)
                    cancelled_this_poll += 1
                    total_cancelled += 1
                else:
                    # Confirm failed: mark state UNKNOWN, trip nothing, do NOT re-quote that leg
                    st["terminal_status"] = "UNKNOWN"
                    active_resting_keys.discard(k)
                    changed_quote_states.append(st)

            # 4d. Markout / close capture lives in the capture pass below (it must keep running
            # after a quote leaves the resting set). News window for fill:
            t_fill_dt = _parse_ts(st.get("t_fill"))
            if t_fill_dt is not None:
                sc_time = status_change_times.get(st["game_key"])
                lock_until = game_lockout_until.get(st["game_key"])
                in_news = False
                if sc_time is not None and sc_time <= t_fill_dt <= sc_time + timedelta(minutes=15):
                    in_news = True
                elif lock_until is not None and t_fill_dt <= lock_until:
                    in_news = True
                st["in_news_window"] = in_news

        # ------------------------------------------------------------------ step 4e: capture pass
        # T-15m closing consensus (+ Novig's own close) for every tracked quote, filled or not, and
        # the +5m/+30m consensus markouts for fills. Runs over the capture watch list, which keeps
        # terminal states from earlier in this firing and from previous firings.
        for k in list(capture_keys):
            st = quote_states.get(k)
            if st is None:
                capture_keys.discard(k)
                continue
            comm_dt = datetime.fromisoformat(st["commence"])
            if now > comm_dt + timedelta(minutes=60):
                capture_keys.discard(k)          # past every capture horizon; leave fields as-is
                continue
            cand_key = (st["game_key"], st["player_norm"], st["stat"], float(st["line"]))
            cur_cand = cands_by_key.get(cand_key)
            if cur_cand is None:
                continue
            p_cons_side_now = cur_cand.candidate.p_cons if st["side"] == "yes" else (1.0 - cur_cand.candidate.p_cons)
            t_fill_dt = _parse_ts(st.get("t_fill"))
            changed = False
            if t_fill_dt is not None:
                if st.get("p_cons_fill_plus_5m") is None and now >= t_fill_dt + timedelta(minutes=5):
                    st["p_cons_fill_plus_5m"] = p_cons_side_now
                    changed = True
                if st.get("p_cons_fill_plus_30m") is None and now >= t_fill_dt + timedelta(minutes=30):
                    st["p_cons_fill_plus_30m"] = p_cons_side_now
                    changed = True
            if st.get("p_cons_close") is None and now >= comm_dt - timedelta(minutes=15):
                st["p_cons_close"] = p_cons_side_now
                if st["side"] == "yes":
                    s_bid = cur_cand.candidate.novig_bid_over
                    s_ask = cur_cand.candidate.novig_ask_over
                else:
                    s_bid = (1.0 - cur_cand.candidate.novig_ask_over) if cur_cand.candidate.novig_ask_over is not None else None
                    s_ask = (1.0 - cur_cand.candidate.novig_bid_over) if cur_cand.candidate.novig_bid_over is not None else None
                if s_bid is not None and s_ask is not None:
                    st["p_novig_close"] = round((s_bid + s_ask) / 2.0, 4)
                changed = True
            if changed:
                changed_quote_states.append(st)
            if not _capture_incomplete(st):
                capture_keys.discard(k)

        # ------------------------------------------------------------------ step 5: post new quotes
        hist: dict[str, int] = {}
        posted_this_poll = 0
        et_today = sb.et_day(now)

        # Existing active player/stat/game set for study hygiene
        active_prop_keys = {
            (s["game_key"], s["player_norm"], s["stat"])
            for s in quote_states.values()
            if s.get("terminal_status") in ("RESTING", "UNKNOWN")
        }

        for ac in assembled:
            prop_key = (ac.game_key, ac.player_norm, ac.stat)
            if prop_key in active_prop_keys:
                continue

            # Lockout check
            locked_until = game_lockout_until.get(ac.game_key)
            post_news_ok = game_post_news_ok.get(ac.game_key, True)
            if maker.requote_blocked(locked_until, now, post_news_ok):
                continue

            d = maker.decide(ac.candidate)
            hist[d.mode] = hist.get(d.mode, 0) + 1
            if d.mode != "MAKER_PAPER":
                continue

            intent_id = f"mk-{et_today}-{ac.game_key}-{ac.player_norm}-{ac.stat}-{d.side}-{d.bid_price:.3f}"
            pid = "p-" + intent_id
            if ledger.has_intent(pid) or any(s.get("intent_id") == intent_id for s in quote_states.values()):
                hist["REJ_DUP"] = hist.get("REJ_DUP", 0) + 1
                continue

            # Liability check
            exposures = ledger_exposures(ledger, et_today, MAKER_MODE)
            for k in active_resting_keys:
                r_st = quote_states[k]
                r_cost = float(r_st.get("contracts_units", 0.0)) * float(r_st.get("quote_price", 0.0))
                exposures.append(Exposure(r_st["game_key"], r_st["player_norm"], r_st["stat"], r_cost))

            liab_res = check_liability(
                liability_cfg,
                contracts=d.contracts,
                price=d.bid_price,
                game_key=ac.game_key,
                player_norm=ac.player_norm,
                exposures=exposures,
                fee_charged=ac.leg.fee_charged,
            )
            if not liab_res.allowed:
                hist["REJ_LIABILITY"] = hist.get("REJ_LIABILITY", 0) + 1
                continue

            contracts_to_quote = liab_res.contracts

            # Place order on venue
            ack = venue.place_resting(
                ac.leg,
                intent_id=intent_id,
                side=d.side,
                price=d.bid_price,
                contracts_units=contracts_to_quote,
            )

            if ack.get("status") == "OPEN":
                paper_risk.note_submit_ok()
            elif ack.get("status") in ("ERROR", "UNKNOWN"):
                paper_risk.note_submit_error()

            # Record in ledger
            pmarket = ack.get("paper_market_id") or ac.leg.market_id
            ledger.record_order(
                day=et_today,
                mode=MAKER_MODE,
                intent_id=pid,
                client_order_id=ack["client_id"],
                order_id=ack.get("order_id"),
                ticker=pmarket,
                game_pk=ac.game_key,
                side=d.side,
                contracts=contracts_to_quote,
                limit_price=d.bid_price,
                status=ack["status"],
                fill_count=0,
                filled_cost=0.0,
                fee=0.0,
                raw=ack.get("raw") or {},
            )

            # Record quote_state
            p_cons_side = ac.candidate.p_cons if d.side == "yes" else (1.0 - ac.candidate.p_cons)
            p_model_side = ac.candidate.p_model if d.side == "yes" else (1.0 - ac.candidate.p_model)
            stat_cfg = maker.STAT_CFG[(ac.league, ac.stat)]
            p_blend_side = maker.p_blend(p_cons_side, p_model_side, stat_cfg.c_stat)

            if d.side == "yes":
                bid_side = ac.candidate.novig_bid_over
                ask_side = ac.candidate.novig_ask_over
            else:
                bid_side = (1.0 - ac.candidate.novig_ask_over) if ac.candidate.novig_ask_over is not None else None
                ask_side = (1.0 - ac.candidate.novig_bid_over) if ac.candidate.novig_bid_over is not None else None
            spread_side = (ask_side - bid_side) if (bid_side is not None and ask_side is not None) else None

            sharp_at_post = {}
            for b in maker.SHARP_BOOKS:
                if b in ac.book_fair:
                    f_over = ac.book_fair[b]
                    sharp_at_post[b] = f_over if d.side == "yes" else (1.0 - f_over)

            term_st = "RESTING" if ack["status"] == "OPEN" else ack["status"]
            game_day_et = ac.commence.astimezone(sb._ET).date().isoformat()

            new_st = maker_bank.new_quote_state(
                order_id=ack.get("order_id"),
                client_id=ack["client_id"],
                intent_id=intent_id,
                game_key=ac.game_key,
                game_day=game_day_et,
                sport=ac.league,
                stat=ac.stat,
                player_norm=ac.player_norm,
                player=ac.player,
                line=ac.line,
                side=d.side,
                t_post=now.isoformat(),
                t_end=now.isoformat() if term_st != "RESTING" else None,
                quote_price=d.bid_price,
                contracts_units=contracts_to_quote,
                qty=int(round(contracts_to_quote * UNIT)),
                novig_bid_at_post=bid_side,
                novig_ask_at_post=ask_side,
                novig_spread_at_post=spread_side,
                p_cons_at_post=p_cons_side,
                p_blend_at_post=p_blend_side,
                p_cons_close=None,
                terminal_status=term_st,
                cancel_reason=None if term_st == "RESTING" else ack.get("error"),
                fill_qty_units=0.0,
                p_fill=None,
                t_fill=None,
                p_cons_fill_plus_5m=None,
                p_cons_fill_plus_30m=None,
                p_novig_close=None,
                in_news_window=False,
                fair_floor=d.fair_floor,
                delta=d.delta,
                commence=ac.commence.isoformat(),
                sharp_at_post=sharp_at_post,
            )

            state_key = ack.get("order_id") or ack["client_id"]
            quote_states[state_key] = new_st
            changed_quote_states.append(new_st)
            if term_st == "RESTING":
                active_resting_keys.add(state_key)
                capture_keys.add(state_key)
                active_prop_keys.add(prop_key)
            posted_this_poll += 1
            total_posted += 1

        # ------------------------------------------------------------------ step 6: bank
        try:
            sb.bank(ledger, store, known=known, modes=(MAKER_MODE,))
            if changed_quote_states:
                maker_bank.bank_rows(store, [("quote_state", s) for s in changed_quote_states], now=now)
                changed_quote_states.clear()
        except Exception as e:
            log.exception("banking failed")
            jlog("bank", ok=False, error=str(e)[:200])
            bank_errors += 1

        # ------------------------------------------------------------------ step 7: poll summary
        jlog("poll", poll=poll_idx, candidates=len(assembled), modes=hist,
             posted=posted_this_poll, cancelled=cancelled_this_poll, resting=len(active_resting_keys))

        if getattr(args, "once", False):
            break

        sleep(args.poll_sec)

    # Final wrap up & banking
    now_end = clock()
    try:
        sb.bank(ledger, store, known=known, modes=(MAKER_MODE,))
        if changed_quote_states:
            maker_bank.bank_rows(store, [("quote_state", s) for s in changed_quote_states], now=now_end)
            changed_quote_states.clear()
        run_summary = {
            "poll_count": poll_idx,
            "total_posted": total_posted,
            "total_cancelled": total_cancelled,
            "resting_count": len(active_resting_keys),
            "bank_errors": bank_errors,
            "paper_risk_killed": paper_risk.killed,
            "start_time": start_time.isoformat(),
            "end_time": now_end.isoformat(),
        }
        maker_bank.bank_rows(store, [("run", run_summary)], now=now_end)
    except Exception as e:
        log.exception("final banking failed")
        bank_errors += 1

    exit_code = 1 if (bank_errors > 0 or paper_risk.killed) else 0
    jlog("done", exit_code=exit_code, total_posted=total_posted,
         total_cancelled=total_cancelled, resting_count=len(active_resting_keys))
    return exit_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NOV-03 Novig maker study launch runner (paper sandbox only)")
    ap.add_argument("--store", default="gcs", help="gcs | gcloud | dir:PATH")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--poll-sec", type=float, default=30.0, help="interval between polls")
    ap.add_argument("--max-runtime-sec", type=float, default=1500.0, help="maximum runner duration")
    ap.add_argument("--pace-s", type=float, default=0.2, help="pause between book fetches")
    ap.add_argument("--horizon-hours", type=float, default=30.0, help="maximum game commence horizon")
    ap.add_argument("--leagues", default="NFL,NHL,NBA", help="comma-separated leagues to quote")
    ap.add_argument("--once", action="store_true", help="run exactly one poll cycle")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
