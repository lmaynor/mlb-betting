"""mlb_core.odds.canonical.writers -- Ingest Writers for Canonical Odds (BNK-12).

One writer per source, taking raw inputs/frames + MasterIndex + registry,
returning (canonical_df, quarantine_df, report_dict).

Mapping decisions documented per spec:
1. AN: props & gamelines detected by columns. Milestones -> class milestone, side yes, line=threshold.
   Football leagues key rows by event date from start_time; all other leagues derive game_date_et
   from start_time and compare against requested_date (V1).
2. BP: is_open=True -> opener, else last_quote_close_proxy. source_updated_at from updated.
   Incomplete is not invalid: rows missing updated or one-sided milestones are kept with NULLs.
3. OddsAccum: raw ParlayAPI/Odds-API JSON payloads. Per-book rows with last_update preserved.
4. Kalshi Futures: market_class binary_contract, price_kind exchange_book, side yes.
   price_decimal = 1/yes_ask (executable ask for YES buy in decimal odds), bid_decimal = 1/yes_bid,
   mid_prob = (bid+ask)/2 when bid>0 else None, price_american = None (never fabricated).
   player_id is set to None (player columns are for props; outcomes are carried in source_extras).
   Game-line nullability rule applies by analogy: binary_contract rows with null player are valid.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from mlb_core.odds.canonical.normalize import split_team_tail
from mlb_core.odds.canonical.registry import (
    book_from_an_id,
    book_from_bp_id,
    book_from_odds_api_key,
    market_from_an,
    market_from_bp,
    market_from_odds_api,
    normalize_team,
)
from mlb_core.odds.canonical.resolve import MasterIndex
from mlb_core.odds.canonical.schema import (
    CANONICAL_COLUMNS,
    empty_frame,
    validate_published_frame,
)
from mlb_core.odds.canonical.validate import (
    check_v1_slate_match,
    check_v2_resolution_accounting,
    check_v3_team_pair_consistency,
    check_v4_event_sanity,
    check_v5_duplicate_detection,
    check_v6_price_sanity,
)

logger = logging.getLogger(__name__)


def _american_to_decimal(odds: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Convert American odds to (price_american, price_decimal, implied_raw)."""
    if odds is None or pd.isna(odds):
        return None, None, None
    try:
        val = float(odds)
    except (ValueError, TypeError):
        return None, None, None

    if val == 0:
        return 0.0, None, None

    if val > 0:
        dec = round(1.0 + (val / 100.0), 6)
    else:
        dec = round(1.0 + (100.0 / abs(val)), 6)

    imp = round(1.0 / dec, 6) if dec > 0 else None
    return val, dec, imp


def _quarantine_frame() -> pd.DataFrame:
    """Return an empty quarantine DataFrame with CANONICAL_COLUMNS + reject fields."""
    cols = list(CANONICAL_COLUMNS) + ["reject_reason", "reject_detail"]
    return pd.DataFrame(columns=cols)


# ===========================================================================
# 1. Action Network Writer
# ===========================================================================

def write_an(
    frame: pd.DataFrame,
    league: str,
    master: Optional[MasterIndex] = None,
    requested_date: str = "",
    source_key: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Canonical writer for Action Network banked rows."""
    lg = league.lower()
    c_rows: List[Dict[str, Any]] = []
    q_rows: List[Dict[str, Any]] = []
    step_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}

    if frame is None or frame.empty:
        rep = check_v2_resolution_accounting(0, 0, 0, step_counts, reason_counts)
        return empty_frame(), _quarantine_frame(), rep

    # Detect gamelines vs props
    is_gamelines = "market_type" in frame.columns

    # Event dates collection for V1
    event_dates: List[str] = []
    for st in frame.get("start_time", []):
        if pd.notna(st) and st:
            try:
                dt_utc = pd.to_datetime(st, utc=True)
                event_dates.append(dt_utc.tz_convert("America/New_York").strftime("%Y-%m-%d"))
            except Exception:
                event_dates.append(str(st)[:10])

    # Football vs other leagues date rule
    is_football = lg in ("nfl", "ncaaf")
    if not is_football and requested_date:
        v1_res = check_v1_slate_match(requested_date, event_dates)
        if v1_res.get("hard_failure"):
            # V1 hard failure: entire payload quarantined as slate_mismatch
            reason_counts["slate_mismatch"] = len(frame)
            for idx, r in frame.iterrows():
                q_row = dict(r)
                q_row["reject_reason"] = "slate_mismatch"
                q_row["reject_detail"] = v1_res.get("error", "V1 slate mismatch")
                q_rows.append(q_row)
            rep = check_v2_resolution_accounting(len(frame), 0, len(frame), step_counts, reason_counts)
            rep["v1"] = v1_res
            return empty_frame(), pd.DataFrame(q_rows), rep

    for idx, r in frame.iterrows():
        src_ref = f"{source_key}#{idx}" if source_key else f"actionnetwork_{lg}#{idx}"
        bid_raw = r.get("book_id")
        canonical_book = book_from_an_id(bid_raw)

        start_time = str(r.get("start_time") or "")
        # Date derivation from start_time
        if start_time and ("T" in start_time or ":" in start_time):
            try:
                game_date_et = pd.to_datetime(start_time, utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d")
            except Exception:
                game_date_et = str(r.get("date") or requested_date)[:10]
        else:
            game_date_et = str(r.get("date") or requested_date)[:10]

        away_team = normalize_team(lg, r.get("away_abbr") or r.get("away"))
        home_team = normalize_team(lg, r.get("home_abbr") or r.get("home"))

        odds_val = r.get("odds")
        p_amer, p_dec, imp_raw = _american_to_decimal(odds_val)

        if canonical_book is None:
            reason_counts["unknown_book"] = reason_counts.get("unknown_book", 0) + 1
            q_row = {
                "league": lg, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                "player_id": None, "player_name": str(r.get("player_name") or ""),
                "player_team": None, "market": str(r.get("market") or r.get("market_type") or ""),
                "market_class": "main", "line": r.get("line"), "side": r.get("side"),
                "book_id": str(bid_raw), "price_american": p_amer, "price_decimal": p_dec,
                "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                "is_main_line": False, "observed_at": r.get("scraped_at"), "source_updated_at": None,
                "price_kind": "closing_snapshot_unstamped", "source": "actionnetwork", "source_ref": src_ref,
                "source_player_id": str(r.get("player_id")) if pd.notna(r.get("player_id")) else None,
                "source_player_team": r.get("team_abbr"), "source_extras": json.dumps({"raw_book_id": bid_raw}),
                "reject_reason": "unknown_book",
                "reject_detail": f"Book id {bid_raw} not in canonical books registry",
            }
            q_rows.append(q_row)
            continue

        if is_gamelines:
            # Gameline row
            raw_mtype = str(r.get("market_type") or "game_line")
            m_res = market_from_an(raw_mtype)
            canonical_m = m_res[0] if m_res else raw_mtype
            m_class = m_res[1] if m_res else "game_line"

            line_val = float(r["line"]) if pd.notna(r.get("line")) else None
            side_val = str(r.get("side") or "").lower()

            c_row = {
                "league": lg, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                "player_id": None, "player_name": None, "player_team": None,
                "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                "is_main_line": True, "observed_at": r.get("scraped_at"), "source_updated_at": None,
                "price_kind": "closing_snapshot_unstamped", "source": "actionnetwork", "source_ref": src_ref,
                "source_player_id": None, "source_player_team": None,
                "source_extras": json.dumps({k: r[k] for k in ["tickets_pct", "money_pct", "is_live", "line_status"] if k in r and pd.notna(r[k])}),
            }
            c_rows.append(c_row)
            step_counts["gameline_pass"] = step_counts.get("gameline_pass", 0) + 1
        else:
            # Prop row
            raw_market = str(r.get("market") or "")
            is_ms = bool(r.get("is_milestone")) or "_milestone_" in raw_market
            if is_ms:
                m_class = "milestone"
                stat = raw_market.split("_milestone_")[0] if "_milestone_" in raw_market else raw_market
                canonical_m = f"{stat}_milestone"
                side_val = "yes"
                line_val = float(r["threshold"]) if pd.notna(r.get("threshold")) else (float(r["line"]) if pd.notna(r.get("line")) else 0.0)
            else:
                m_res = market_from_an(raw_market)
                canonical_m = m_res[0] if m_res else raw_market
                m_class = "alternate" if r.get("is_alt_market") else (m_res[1] if m_res else "main")
                side_val = str(r.get("side") or "").lower()
                line_val = float(r["line"]) if pd.notna(r.get("line")) else None

            # Player resolution
            raw_pname = str(r.get("player_name") or "")
            spid = str(r.get("player_id")) if pd.notna(r.get("player_id")) else None
            s_team = r.get("team_abbr")

            master_id, player_team, step = (None, None, "unresolved")
            if master:
                master_id, player_team, step = master.resolve_player(
                    source="actionnetwork",
                    source_player_id=spid,
                    player_name=raw_pname,
                    event_id=str(r.get("event_id") or ""),
                    game_date_et=game_date_et,
                    away_team=away_team,
                    home_team=home_team,
                    source_player_team=s_team,
                )

            if master_id is None:
                # Quarantine
                reason_counts[step] = reason_counts.get(step, 0) + 1
                q_row = {
                    "league": lg, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                    "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                    "player_id": None, "player_name": raw_pname, "player_team": None,
                    "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                    "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                    "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                    "is_main_line": not bool(r.get("is_alt_market")), "observed_at": r.get("scraped_at"),
                    "source_updated_at": None, "price_kind": "closing_snapshot_unstamped",
                    "source": "actionnetwork", "source_ref": src_ref, "source_player_id": spid,
                    "source_player_team": s_team,
                    "source_extras": json.dumps({"line_status": r.get("line_status")}),
                    "reject_reason": step,
                    "reject_detail": f"Player {raw_pname} (spid {spid}) resolution failed: {step}",
                }
                q_rows.append(q_row)
            else:
                step_counts[step] = step_counts.get(step, 0) + 1
                c_row = {
                    "league": lg, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                    "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                    "player_id": master_id, "player_name": raw_pname, "player_team": player_team,
                    "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                    "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                    "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                    "is_main_line": not bool(r.get("is_alt_market")), "observed_at": r.get("scraped_at"),
                    "source_updated_at": None, "price_kind": "closing_snapshot_unstamped",
                    "source": "actionnetwork", "source_ref": src_ref, "source_player_id": spid,
                    "source_player_team": s_team,
                    "source_extras": json.dumps({k: r[k] for k in ["line_status", "period", "outcome_id"] if k in r and pd.notna(r[k])}),
                }
                c_rows.append(c_row)

    c_df = pd.DataFrame(c_rows) if c_rows else empty_frame()
    q_df = pd.DataFrame(q_rows) if q_rows else _quarantine_frame()

    # V5 deduplication
    if not c_df.empty:
        c_df, v5_res = check_v5_duplicate_detection(c_df)
    else:
        v5_res = {"check": "V5", "passed": True, "duplicates_collapsed": 0}

    # V6 price sanity
    if not c_df.empty:
        malformed_idx, v6_res = check_v6_price_sanity(c_df)
        if malformed_idx:
            mal_sub = c_df.iloc[malformed_idx].copy()
            mal_sub["reject_reason"] = "malformed"
            mal_sub["reject_detail"] = "Null price or null line on lined market"
            q_df = pd.concat([q_df, mal_sub], ignore_index=True)
            reason_counts["malformed"] = len(malformed_idx)
            c_df = c_df.drop(c_df.index[malformed_idx]).reset_index(drop=True)
    else:
        v6_res = {"check": "V6", "passed": True}

    rep = check_v2_resolution_accounting(len(frame), len(c_df), len(q_df), step_counts, reason_counts)
    rep["v3"] = check_v3_team_pair_consistency(c_df)
    rep["v4"] = check_v4_event_sanity(c_df)
    rep["v5"] = v5_res
    rep["v6"] = v6_res
    return c_df, q_df, rep


# ===========================================================================
# 2. BettingPros Writer
# ===========================================================================

def write_bp(
    frame: pd.DataFrame,
    sport: str,
    master: Optional[MasterIndex] = None,
    source_key: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Canonical writer for BettingPros long props and game rows."""
    sp = sport.lower()
    c_rows: List[Dict[str, Any]] = []
    q_rows: List[Dict[str, Any]] = []
    step_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}

    if frame is None or frame.empty:
        rep = check_v2_resolution_accounting(0, 0, 0, step_counts, reason_counts)
        return empty_frame(), _quarantine_frame(), rep

    is_props = "player" in frame.columns or "player_id" in frame.columns

    for idx, r in frame.iterrows():
        src_ref = f"{source_key}#{idx}" if source_key else f"bettingpros_{sp}#{idx}"
        bid_raw = r.get("book_id")
        canonical_book = book_from_bp_id(bid_raw)

        game_date_et = str(r.get("date") or "")[:10]
        start_time = str(r.get("scheduled") or "")
        away_team = normalize_team(sp, r.get("away"))
        home_team = normalize_team(sp, r.get("home"))

        odds_val = r.get("odds")
        p_amer, p_dec, imp_raw = _american_to_decimal(odds_val)

        is_open = bool(r.get("is_open"))
        p_kind = "opener" if is_open else "last_quote_close_proxy"
        src_updated = r.get("updated") if pd.notna(r.get("updated")) and str(r.get("updated")).strip() != "" else None
        obs_at = src_updated or start_time or game_date_et

        if canonical_book is None:
            reason_counts["unknown_book"] = reason_counts.get("unknown_book", 0) + 1
            q_row = {
                "league": sp, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                "player_id": None, "player_name": str(r.get("player") or ""), "player_team": None,
                "market": str(r.get("market") or ""), "market_class": "main", "line": r.get("line"),
                "side": r.get("side"), "book_id": str(bid_raw), "price_american": p_amer,
                "price_decimal": p_dec, "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None,
                "mid_prob": None, "is_main_line": bool(r.get("is_main")), "observed_at": obs_at,
                "source_updated_at": src_updated, "price_kind": p_kind, "source": "bettingpros",
                "source_ref": src_ref, "source_player_id": str(r.get("player_id")) if pd.notna(r.get("player_id")) else None,
                "source_player_team": r.get("team"), "source_extras": json.dumps({"raw_book_id": bid_raw}),
                "reject_reason": "unknown_book", "reject_detail": f"Book id {bid_raw} not in canonical registry",
            }
            q_rows.append(q_row)
            continue

        raw_market = str(r.get("market") or "")
        m_res = market_from_bp(raw_market)
        canonical_m = m_res[0] if m_res else raw_market
        m_class = m_res[1] if m_res else ("main" if is_props else "game_line")

        line_val = float(r["line"]) if pd.notna(r.get("line")) else None
        side_val = str(r.get("side") or "").lower()

        if is_props:
            raw_pname = str(r.get("player") or "")
            spid = str(r.get("player_id")) if pd.notna(r.get("player_id")) else None
            s_team = r.get("team")

            master_id, player_team, step = (None, None, "unresolved")
            if master:
                master_id, player_team, step = master.resolve_player(
                    source="bettingpros",
                    source_player_id=spid,
                    player_name=raw_pname,
                    event_id=str(r.get("event_id") or ""),
                    game_date_et=game_date_et,
                    away_team=away_team,
                    home_team=home_team,
                    source_player_team=s_team,
                )

            if master_id is None:
                reason_counts[step] = reason_counts.get(step, 0) + 1
                q_row = {
                    "league": sp, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                    "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                    "player_id": None, "player_name": raw_pname, "player_team": None,
                    "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                    "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                    "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                    "is_main_line": bool(r.get("is_main")), "observed_at": obs_at,
                    "source_updated_at": src_updated, "price_kind": p_kind,
                    "source": "bettingpros", "source_ref": src_ref, "source_player_id": spid,
                    "source_player_team": s_team,
                    "source_extras": json.dumps({k: r[k] for k in ["is_best", "position"] if k in r and pd.notna(r[k])}),
                    "reject_reason": step,
                    "reject_detail": f"BP player {raw_pname} (spid {spid}) resolution failed: {step}",
                }
                q_rows.append(q_row)
            else:
                step_counts[step] = step_counts.get(step, 0) + 1
                c_row = {
                    "league": sp, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                    "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                    "player_id": master_id, "player_name": raw_pname, "player_team": player_team,
                    "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                    "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                    "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                    "is_main_line": bool(r.get("is_main")), "observed_at": obs_at,
                    "source_updated_at": src_updated, "price_kind": p_kind,
                    "source": "bettingpros", "source_ref": src_ref, "source_player_id": spid,
                    "source_player_team": s_team,
                    "source_extras": json.dumps({k: r[k] for k in ["is_best", "position"] if k in r and pd.notna(r[k])}),
                }
                c_rows.append(c_row)
        else:
            # Game line row
            step_counts["gameline_pass"] = step_counts.get("gameline_pass", 0) + 1
            c_row = {
                "league": sp, "event_id": str(r.get("event_id") or ""), "game_date_et": game_date_et,
                "start_time_utc": start_time, "away_team": away_team, "home_team": home_team,
                "player_id": None, "player_name": None, "player_team": None,
                "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                "is_main_line": bool(r.get("is_main")), "observed_at": obs_at,
                "source_updated_at": src_updated, "price_kind": p_kind,
                "source": "bettingpros", "source_ref": src_ref, "source_player_id": None,
                "source_player_team": None,
                "source_extras": json.dumps({k: r[k] for k in ["event_status", "season_type"] if k in r and pd.notna(r[k])}),
            }
            c_rows.append(c_row)

    c_df = pd.DataFrame(c_rows) if c_rows else empty_frame()
    q_df = pd.DataFrame(q_rows) if q_rows else _quarantine_frame()

    if not c_df.empty:
        c_df, v5_res = check_v5_duplicate_detection(c_df)
    else:
        v5_res = {"check": "V5", "passed": True, "duplicates_collapsed": 0}

    if not c_df.empty:
        malformed_idx, v6_res = check_v6_price_sanity(c_df)
        if malformed_idx:
            mal_sub = c_df.iloc[malformed_idx].copy()
            mal_sub["reject_reason"] = "malformed"
            mal_sub["reject_detail"] = "Null price or null line on lined market"
            q_df = pd.concat([q_df, mal_sub], ignore_index=True)
            reason_counts["malformed"] = len(malformed_idx)
            c_df = c_df.drop(c_df.index[malformed_idx]).reset_index(drop=True)
    else:
        v6_res = {"check": "V6", "passed": True}

    rep = check_v2_resolution_accounting(len(frame), len(c_df), len(q_df), step_counts, reason_counts)
    rep["v3"] = check_v3_team_pair_consistency(c_df)
    rep["v4"] = check_v4_event_sanity(c_df)
    rep["v5"] = v5_res
    rep["v6"] = v6_res
    return c_df, q_df, rep


# ===========================================================================
# 3. OddsAccum Writer
# ===========================================================================

def write_oddsaccum(
    payload: Any,
    sport_key: str,
    captured_ts: str,
    master: Optional[MasterIndex] = None,
    source_key: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Canonical writer for raw ParlayAPI/Odds-API JSON payloads.

    Generates per-book rows (NEVER flattened CSV). Preserves per-bookmaker last_update.
    """
    sport_to_league = {
        "basketball_nba": "nba",
        "icehockey_nhl": "nhl",
        "baseball_mlb": "mlb",
        "americanfootball_nfl": "nfl",
    }
    lg = sport_to_league.get(sport_key.lower(), sport_key.lower())

    c_rows: List[Dict[str, Any]] = []
    q_rows: List[Dict[str, Any]] = []
    step_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}

    events = payload if isinstance(payload, list) else (payload.get("events", []) if isinstance(payload, dict) else [])
    total_quotes = 0

    for ev_idx, ev in enumerate(events):
        eid = str(ev.get("id") or "")
        commence = str(ev.get("commence_time") or "")
        if commence and ("T" in commence or ":" in commence):
            try:
                game_date_et = pd.to_datetime(commence, utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d")
            except Exception:
                game_date_et = commence[:10]
        else:
            game_date_et = commence[:10] if commence else captured_ts[:10]

        away_team = normalize_team(lg, ev.get("away_team"))
        home_team = normalize_team(lg, ev.get("home_team"))

        for b_idx, book in enumerate(ev.get("bookmakers", []) or []):
            bkey = book.get("key", "")
            canonical_book = book_from_odds_api_key(bkey)
            src_updated = book.get("last_update")

            for m_idx, market in enumerate(book.get("markets", []) or []):
                mkey = market.get("key", "")
                m_res = market_from_odds_api(mkey)
                canonical_m = m_res[0] if m_res else mkey
                m_class = m_res[1] if m_res else "main"

                for o_idx, outcome in enumerate(market.get("outcomes", []) or []):
                    total_quotes += 1
                    src_ref = f"{source_key}#ev{ev_idx}_b{b_idx}_m{m_idx}_o{o_idx}" if source_key else f"oddsaccum_{sport_key}#{eid}_{bkey}_{mkey}_{o_idx}"

                    odds_val = outcome.get("price")
                    p_amer, p_dec, imp_raw = _american_to_decimal(odds_val)
                    line_val = float(outcome["point"]) if outcome.get("point") is not None else None

                    # Parse side and player name
                    o_name = str(outcome.get("name") or "").strip()
                    desc = str(outcome.get("description") or "").strip()

                    if canonical_book is None:
                        reason_counts["unknown_book"] = reason_counts.get("unknown_book", 0) + 1
                        q_rows.append({
                            "league": lg, "event_id": eid, "game_date_et": game_date_et,
                            "start_time_utc": commence, "away_team": away_team, "home_team": home_team,
                            "player_id": None, "player_name": desc or o_name, "player_team": None,
                            "market": canonical_m, "market_class": m_class, "line": line_val,
                            "side": o_name.lower(), "book_id": bkey, "price_american": p_amer,
                            "price_decimal": p_dec, "implied_raw": imp_raw, "bid_decimal": None,
                            "ask_decimal": None, "mid_prob": None, "is_main_line": m_class == "main",
                            "observed_at": captured_ts, "source_updated_at": src_updated,
                            "price_kind": "live_snapshot", "source": "oddsaccum", "source_ref": src_ref,
                            "source_player_id": None, "source_player_team": None,
                            "source_extras": json.dumps({"raw_bookmaker_key": bkey}),
                            "reject_reason": "unknown_book", "reject_detail": f"Bookmaker key '{bkey}' unknown in registry",
                        })
                        continue

                    if m_class == "game_line":
                        # Game line
                        if mkey == "h2h":
                            side_val = "home" if o_name == ev.get("home_team") else "away"
                        elif mkey == "spreads":
                            side_val = "home" if o_name == ev.get("home_team") else "away"
                        else:
                            side_val = o_name.lower()

                        step_counts["gameline_pass"] = step_counts.get("gameline_pass", 0) + 1
                        c_rows.append({
                            "league": lg, "event_id": eid, "game_date_et": game_date_et,
                            "start_time_utc": commence, "away_team": away_team, "home_team": home_team,
                            "player_id": None, "player_name": None, "player_team": None,
                            "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                            "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                            "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                            "is_main_line": True, "observed_at": captured_ts, "source_updated_at": src_updated,
                            "price_kind": "live_snapshot", "source": "oddsaccum", "source_ref": src_ref,
                            "source_player_id": None, "source_player_team": None, "source_extras": "{}",
                        })
                    else:
                        # Prop outcome
                        clean_name, team_hint = split_team_tail(desc if desc else o_name)
                        if o_name.lower().startswith("over"):
                            side_val = "over"
                        elif o_name.lower().startswith("under"):
                            side_val = "under"
                        elif o_name.lower().startswith("yes"):
                            side_val = "yes"
                        elif o_name.lower().startswith("no"):
                            side_val = "no"
                        else:
                            side_val = o_name.lower()

                        master_id, player_team, step = (None, None, "unresolved")
                        if master:
                            master_id, player_team, step = master.resolve_player(
                                source="oddsaccum",
                                source_player_id=None,
                                player_name=clean_name,
                                event_id=eid,
                                game_date_et=game_date_et,
                                away_team=away_team,
                                home_team=home_team,
                                source_player_team=team_hint,
                            )
                        else:
                            master_id = clean_name
                            player_team = team_hint
                            step = "unresolved_no_master"

                        if master_id is None:
                            reason_counts[step] = reason_counts.get(step, 0) + 1
                            q_rows.append({
                                "league": lg, "event_id": eid, "game_date_et": game_date_et,
                                "start_time_utc": commence, "away_team": away_team, "home_team": home_team,
                                "player_id": None, "player_name": clean_name, "player_team": None,
                                "market": canonical_m, "market_class": m_class, "line": line_val,
                                "side": side_val, "book_id": canonical_book, "price_american": p_amer,
                                "price_decimal": p_dec, "implied_raw": imp_raw, "bid_decimal": None,
                                "ask_decimal": None, "mid_prob": None, "is_main_line": m_class == "main",
                                "observed_at": captured_ts, "source_updated_at": src_updated,
                                "price_kind": "live_snapshot", "source": "oddsaccum", "source_ref": src_ref,
                                "source_player_id": None, "source_player_team": team_hint,
                                "source_extras": "{}", "reject_reason": step,
                                "reject_detail": f"OddsAccum player '{clean_name}' resolution failed: {step}",
                            })
                        else:
                            step_counts[step] = step_counts.get(step, 0) + 1
                            c_rows.append({
                                "league": lg, "event_id": eid, "game_date_et": game_date_et,
                                "start_time_utc": commence, "away_team": away_team, "home_team": home_team,
                                "player_id": master_id, "player_name": clean_name, "player_team": player_team,
                                "market": canonical_m, "market_class": m_class, "line": line_val, "side": side_val,
                                "book_id": canonical_book, "price_american": p_amer, "price_decimal": p_dec,
                                "implied_raw": imp_raw, "bid_decimal": None, "ask_decimal": None, "mid_prob": None,
                                "is_main_line": m_class == "main", "observed_at": captured_ts,
                                "source_updated_at": src_updated, "price_kind": "live_snapshot",
                                "source": "oddsaccum", "source_ref": src_ref, "source_player_id": None,
                                "source_player_team": team_hint, "source_extras": "{}",
                            })

    c_df = pd.DataFrame(c_rows) if c_rows else empty_frame()
    q_df = pd.DataFrame(q_rows) if q_rows else _quarantine_frame()

    if not c_df.empty:
        c_df, v5_res = check_v5_duplicate_detection(c_df)
    else:
        v5_res = {"check": "V5", "passed": True, "duplicates_collapsed": 0}

    if not c_df.empty:
        malformed_idx, v6_res = check_v6_price_sanity(c_df)
        if malformed_idx:
            mal_sub = c_df.iloc[malformed_idx].copy()
            mal_sub["reject_reason"] = "malformed"
            mal_sub["reject_detail"] = "Null price or null line on lined market"
            q_df = pd.concat([q_df, mal_sub], ignore_index=True)
            reason_counts["malformed"] = len(malformed_idx)
            c_df = c_df.drop(c_df.index[malformed_idx]).reset_index(drop=True)
    else:
        v6_res = {"check": "V6", "passed": True}

    rep = check_v2_resolution_accounting(total_quotes, len(c_df), len(q_df), step_counts, reason_counts)
    rep["v3"] = check_v3_team_pair_consistency(c_df)
    rep["v4"] = check_v4_event_sanity(c_df)
    rep["v5"] = v5_res
    rep["v6"] = v6_res
    return c_df, q_df, rep


# ===========================================================================
# 4. Kalshi Futures Writer
# ===========================================================================

def write_kalshi_futures(
    df: pd.DataFrame,
    dt: str,
    source_key: str = "",
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Canonical writer for Kalshi Futures snapshot parquets.

    Design decision (spec 2.5):
    Futures outcomes are teams/players as outcomes, not prop players.
    For binary_contract rows, player columns are set to None and the outcome is
    carried in source_extras plus market = series/market key.
    Game-line nullability rule from spec 2.5 applies by analogy: binary_contract rows
    with null player are valid and validation must not quarantine them.
    """
    c_rows: List[Dict[str, Any]] = []
    q_rows: List[Dict[str, Any]] = []
    step_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}

    if df is None or df.empty:
        rep = check_v2_resolution_accounting(0, 0, 0, step_counts, reason_counts)
        return empty_frame(), _quarantine_frame(), rep

    for idx, r in df.iterrows():
        src_ref = f"{source_key}#{idx}" if source_key else f"kalshi_futures_{dt}#{idx}"
        yes_ask = float(r.get("yes_ask", 0.0)) if pd.notna(r.get("yes_ask")) else 0.0
        yes_bid = float(r.get("yes_bid", 0.0)) if pd.notna(r.get("yes_bid")) else 0.0

        # Ask-side decimal price: executable ask for a YES buy is 1 / p
        price_dec = round(1.0 / yes_ask, 6) if yes_ask > 0 else None
        ask_dec = price_dec
        bid_dec = round(1.0 / yes_bid, 6) if yes_bid > 0 else None
        implied_raw = yes_ask if yes_ask > 0 else None
        mid_prob = round((yes_bid + yes_ask) / 2.0, 6) if yes_bid > 0 and yes_ask > 0 else None

        extras = {
            "venue": r.get("venue"),
            "series_ticker": r.get("series_ticker"),
            "event_ticker": r.get("event_ticker"),
            "market_ticker": r.get("market_ticker"),
            "outcome": r.get("outcome"),
            "last_price": r.get("last_price"),
            "volume": r.get("volume"),
            "open_interest": r.get("open_interest"),
            "close_time": r.get("close_time"),
        }
        clean_extras = {k: v for k, v in extras.items() if pd.notna(v)}

        m_key = str(r.get("series_ticker") or r.get("market_ticker") or r.get("market") or "binary_contract")
        ev_id = str(r.get("event_ticker") or "")
        lg = str(r.get("league") or "mlb").lower()
        cap_at = str(r.get("captured_at") or "")

        c_row = {
            "league": lg,
            "event_id": ev_id,
            "game_date_et": dt[:10],
            "start_time_utc": cap_at,
            "away_team": "",
            "home_team": "",
            "player_id": None,
            "player_name": None,
            "player_team": None,
            "market": m_key,
            "market_class": "binary_contract",
            "line": None,
            "side": "yes",
            "book_id": "kalshi",
            "price_american": None,  # never fabricated
            "price_decimal": price_dec,
            "implied_raw": implied_raw,
            "bid_decimal": bid_dec,
            "ask_decimal": ask_dec,
            "mid_prob": mid_prob,
            "is_main_line": False,
            "observed_at": cap_at,
            "source_updated_at": None,
            "price_kind": "exchange_book",
            "source": "kalshi",
            "source_ref": src_ref,
            "source_player_id": None,
            "source_player_team": None,
            "source_extras": json.dumps(clean_extras),
        }
        c_rows.append(c_row)
        step_counts["binary_contract_pass"] = step_counts.get("binary_contract_pass", 0) + 1

    c_df = pd.DataFrame(c_rows) if c_rows else empty_frame()
    q_df = _quarantine_frame()

    if not c_df.empty:
        c_df, v5_res = check_v5_duplicate_detection(c_df)
    else:
        v5_res = {"check": "V5", "passed": True, "duplicates_collapsed": 0}

    rep = check_v2_resolution_accounting(len(df), len(c_df), len(q_df), step_counts, reason_counts)
    rep["v5"] = v5_res
    return c_df, q_df, rep
