"""
ev.betmgm_under_shadow -- Forward shadow logger and settlement finalizer for BetMGM Unders.

Governing Protocol: RUL-20261119 (BetMGM unders shadow design, Neon).
Specification: RES-17 Build Packet (2026-11-19).

Adaptations to the ruling (A1-A5):
  - A1: Trigger snapshot is the daily BNK-11 partition captured the following morning.
        t_trigger is partition scraped_at (max). minutes_to_start is logged as computed
        (typically negative reflecting next-morning collection cadence). Closing-only
        replication is evaluated over ClosingPanel presence, not a second BNK-11 capture.
  - A2: Winner's-curse 12.0pp cap from the capability mask (ev/capability_mask.py) is
        implemented as the boundary for the 'overflow' logging bucket (gate_delta > 0.12).
  - A3: Standing 4-book closing consensus floor is adopted from the discovery screen's
        coverage gate (scripts/discovery_screen.py MIN_BOOKS = 4) as the subset restriction
        for p_cons_under_close.
  - A4: Sibling distribution builder with our stat set (NBA: points, rebounds, assists, threes;
        NHL: assists, points), using ev.res11_pop.invert_dispersion and dist_side_prob.
  - A5: Consensus line is the leave-BetMGM-out modal line across reference books (not AN book 15).
        cons_median_age_min is logged as 0.0 by construction (single partition timestamp).

Isolation fence:
  Read-only against BNK-11 Action Network partitions (Odds/actionnetwork_{league}/{market}/{date}.parquet),
  EVSweep archives (EVSweep/{sport}_props/archive/{day}.json), ClosingPanel (ClosingPanel/{sport}/{day}.json.gz),
  and outcome masters (NBA/player_boxscores_master.csv, NHL/skater_boxscores_master.csv).
  Writes strictly under RES17/shadow/:
    chunks/{run_id}-{sha8}.jsonl.gz
    runs/{run_id}.json
    telemetry/{date}.json
    finalized/{et_day}.jsonl.gz
  No staking, no order path, no venue calls.

Kill switch:
  Banking requires BETMGM_UNDER_SHADOW_ARMED=1 in the environment unless --dry-run or a dir: store is used.
  Exits with code 3 if not armed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ev import props
from ev import res11_pop
from ev.novig.shadow_bank import make_store

_ET = ZoneInfo("America/New_York")

# Protocol & Ruling Identifiers
RULING = "RUL-20261119"
PREFIX = "RES17/shadow"
ARM_ENV = "BETMGM_UNDER_SHADOW_ARMED"

# Book IDs (packet §1.1)
BOOK_CONSENSUS = 15
BOOK_OPEN = 30
BOOK_DRAFTKINGS = 68
BOOK_FANDUEL = 69
BOOK_BETMGM = 75
BOOK_BET365 = 79
BOOK_CAESARS = 123

REAL_BOOKS = frozenset({68, 69, 75, 79, 123})
REF_BOOKS = frozenset({68, 69, 79, 123})  # Leave-BetMGM-out

# ClosingPanel book names
CP_REF_BOOKS = frozenset({"draftkings", "fanduel", "bet365", "caesars"})

# Markets by League (market slug == stat name)
MARKETS_BY_LEAGUE = {
    "nba": ("points", "rebounds", "assists", "threes"),
    "nhl": ("assists", "points"),
}
ALL_MARKETS = frozenset({"points", "rebounds", "assists", "threes"})

# ClosingPanel market mapping (§1.3)
CP_MARKET_MAP = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "threes": "player_threes",
}

# Master stat columns (§1.4)
NBA_MASTER_STAT_COLS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes": "three_points_made",
}
NHL_MASTER_STAT_COLS = {
    "points": "points",
    "assists": "assists",
}

# Team aliases from scripts/discovery_screen.py:630
NBA_TEAM_ALIAS = {
    "GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "PHX": "PHO",
    "UTAH": "UTA", "WSH": "WAS",
}


# --------------------------------------------------------------------------- serialization helper
def _json_default(o):
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, bool):
        return o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


# --------------------------------------------------------------------------- name normalizers
def settler_norm_name(s) -> str:
    """House identity normalization verbatim from scripts/discovery_screen.py:140.

    Retains generational suffixes (unlike ev.props.norm_name).
    """
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


SETTLE_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
NHL_TEAM_DIALECT = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL"}


def settle_canon_name(s) -> str:
    """Normalize name dropping trailing generational suffix tokens."""
    norm = settler_norm_name(s)
    tokens = norm.split()
    while tokens and tokens[-1] in SETTLE_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def settle_canon_team(sport: str, abbr: str) -> str:
    """Map team abbreviation to canonical master dialect for given sport."""
    sp = sport.lower() if isinstance(sport, str) else ""
    if sp == "nhl":
        return NHL_TEAM_DIALECT.get(abbr, abbr)
    if sp == "nba":
        return NBA_TEAM_ALIAS.get(abbr, abbr)
    return abbr


# --------------------------------------------------------------------------- pure core
def implied(odds: int | float) -> float:
    """American odds -> implied probability."""
    o = float(odds)
    return (-o) / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)


def decimal(odds: int | float) -> float:
    """American odds -> decimal payout multiplier."""
    o = float(odds)
    return 1.0 + (100.0 / (-o) if o < 0 else o / 100.0)


def core_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Filter raw Action Network partition rows to the main-line core frame (§1.1).

    ~is_alt_market & (line_status == "normal") & side in ('over', 'under').
    Does not filter on period.
    """
    if df.empty:
        return df.copy()
    mask = (
        (~df["is_alt_market"]) &
        (df["line_status"] == "normal") &
        (df["side"].isin(["over", "under"]))
    )
    return df[mask].copy()


def extract_pairs(core: pd.DataFrame) -> pd.DataFrame:
    """Extract BetMGM (book_id == 75) two-sided pairs from the core frame (§1.1, §3.1).

    One row per (event_id, player_id, line) having both 'over' and 'under' quotes.
    """
    cols = [
        "game_date", "event_id", "player_id", "player_name", "team_abbr",
        "home_abbr", "away_abbr", "start_time", "stat", "line_mgm",
        "odds_under_mgm", "odds_over_mgm", "scraped_at", "sport"
    ]
    if core.empty:
        return pd.DataFrame(columns=cols)

    mgm = core[core["book_id"] == BOOK_BETMGM].copy()
    if mgm.empty:
        return pd.DataFrame(columns=cols)

    # Scraped timestamp per partition
    scraped_max = str(mgm["scraped_at"].max()) if "scraped_at" in mgm.columns else ""

    # Group by (event_id, player_id, market, line)
    # A BetMGM pair requires both 'over' and 'under' sides present at that line
    pv = (
        mgm.pivot_table(
            index=["date", "event_id", "player_id", "market", "line"],
            columns="side",
            values="odds",
            aggfunc="first",
        )
        .dropna(subset=["over", "under"])
        .reset_index()
    )

    if pv.empty:
        return pd.DataFrame(columns=cols)

    # Attach player metadata from mgm rows
    meta_cols = ["date", "event_id", "player_id", "market", "player_name",
                 "team_abbr", "home_abbr", "away_abbr", "start_time"]
    if "sport" in mgm.columns:
        meta_cols.append("sport")
    meta = mgm.drop_duplicates(["date", "event_id", "player_id", "market"])[meta_cols]

    pairs = pv.merge(meta, on=["date", "event_id", "player_id", "market"], how="left")
    pairs = pairs.rename(columns={
        "date": "game_date",
        "market": "stat",
        "line": "line_mgm",
        "under": "odds_under_mgm",
        "over": "odds_over_mgm",
    })
    pairs["scraped_at"] = scraped_max
    if "sport" not in pairs.columns:
        # Infer sport from stat if not explicitly tagged
        pairs["sport"] = pairs["stat"].map(lambda s: "nhl" if s in ("sog", "saves") else "nba")

    pairs["line_mgm"] = pairs["line_mgm"].astype(float)
    pairs["odds_under_mgm"] = pairs["odds_under_mgm"].astype(int)
    pairs["odds_over_mgm"] = pairs["odds_over_mgm"].astype(int)

    return pairs[[c for c in cols if c in pairs.columns]].copy()


def consensus_fields(core: pd.DataFrame, pairs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute consensus fields over leave-BetMGM-out reference books (§1.1, §3.1).

    REF_BOOKS = {68, 69, 79, 123}.
    For each ref book:
      A quote requires both 'over' and 'under' present at a line.
      If a book quotes two-sided at multiple lines, its modal line is chosen (tie -> lower).
    Across ref books:
      line_cons = modal line.
      Ties broken toward line_mgm if known (or provided), then lower line.
      n_ref_books = count of ref books with any two-sided quote for that player-game.
    Also collects ref book two-sided under devigged probabilities per line for trigger rows.
    """
    res_cols = ["date", "event_id", "player_id", "stat", "line_cons", "n_ref_books", "quotes_by_line"]
    if core.empty:
        return pd.DataFrame(columns=res_cols)

    ref = core[core["book_id"].isin(REF_BOOKS)].copy()
    if ref.empty:
        return pd.DataFrame(columns=res_cols)

    # Stat / market column
    mkt_col = "market" if "market" in ref.columns else "stat"
    date_col = "date" if "date" in ref.columns else "game_date"

    # Pivot to find two-sided quotes per book and line
    pv = (
        ref.pivot_table(
            index=[date_col, "event_id", "player_id", mkt_col, "book_id", "line"],
            columns="side",
            values="odds",
            aggfunc="first",
        )
        .dropna(subset=["over", "under"])
        .reset_index()
    )

    if pv.empty:
        return pd.DataFrame(columns=res_cols)

    # Compute devigged under prob for each two-sided quote
    pv["imp_o"] = pv["over"].map(implied)
    pv["imp_u"] = pv["under"].map(implied)
    pv["fair_u"] = pv["imp_u"] / (pv["imp_o"] + pv["imp_u"])

    # Optional pair lookup for line_mgm tie-breaking
    pair_mgm_map: dict[tuple, float] = {}
    if pairs is not None and not pairs.empty:
        for r in pairs.itertuples(index=False):
            key = (getattr(r, "game_date", getattr(r, "date", None)),
                   r.event_id, r.player_id, getattr(r, "stat", getattr(r, "market", None)))
            pair_mgm_map[key] = float(r.line_mgm)

    rows = []
    grouped = pv.groupby([date_col, "event_id", "player_id", mkt_col])
    for (d_val, ev_id, p_id, stat_val), grp in grouped:
        n_ref = int(grp["book_id"].nunique())

        # Collect quotes by line
        quotes_by_line: dict[float, list[float]] = {}
        for r in grp.itertuples(index=False):
            ln = float(r.line)
            quotes_by_line.setdefault(ln, []).append(float(r.fair_u))

        # Each ref book contributes its modal line (most occurrences, tie -> lower line)
        book_lines: list[float] = []
        for b_id, b_grp in grp.groupby("book_id"):
            counts = b_grp["line"].value_counts()
            max_c = counts.max()
            tied_lines = sorted(counts[counts == max_c].index)
            book_lines.append(float(tied_lines[0]))

        # Modal line across ref books
        line_counts: dict[float, int] = {}
        for ln in book_lines:
            line_counts[ln] = line_counts.get(ln, 0) + 1

        max_count = max(line_counts.values()) if line_counts else 0
        candidate_lines = [ln for ln, c in line_counts.items() if c == max_count]

        # Mode ties across books -> closest to line_mgm, then lower line
        target_mgm = pair_mgm_map.get((d_val, ev_id, p_id, stat_val))
        if len(candidate_lines) == 1:
            best_line = candidate_lines[0]
        else:
            if target_mgm is not None:
                candidate_lines.sort(key=lambda x: (abs(x - target_mgm), x))
            else:
                candidate_lines.sort()
            best_line = candidate_lines[0]

        rows.append({
            "date": d_val,
            "event_id": ev_id,
            "player_id": p_id,
            "stat": stat_val,
            "line_cons": best_line,
            "n_ref_books": n_ref,
            "quotes_by_line": quotes_by_line,
        })

    return pd.DataFrame(rows)


def build_distributions(archives: dict[str, dict]) -> pd.DataFrame:
    """Reconstruct production distributions from EVSweep archive payloads (§1.2, §3.1).

    Adaptation A4: Scoped to our 6 markets:
      NBA: points, rebounds, assists, threes
      NHL: assists, points
    Columns: sport, stat, player_norm, player, team, opp, mu, disp, family, commence, et_day.
    """
    cols = ["sport", "stat", "player_norm", "player", "team", "opp",
            "mu", "disp", "family", "commence", "et_day"]
    if not archives:
        return pd.DataFrame(columns=cols)

    rows = []
    for key, arch in archives.items():
        if not isinstance(arch, dict):
            continue

        # Infer sport from key or archive payload
        key_lower = key.lower()
        if "nba" in key_lower:
            sport = "nba"
        elif "nhl" in key_lower:
            sport = "nhl"
        else:
            sport = arch.get("sport") or arch.get("engine") or "nba"

        arch_rows = arch.get("rows") or {}
        valid_stats = MARKETS_BY_LEAGUE.get(sport, ())

        for r in arch_rows.values():
            stat = r.get("stat")
            if stat not in valid_stats:
                continue

            line = r.get("line")
            p_over = r.get("p_over_model")
            mu = r.get("proj_mean")
            commence_raw = r.get("commence")

            if line is None or p_over is None or mu is None or not commence_raw:
                continue

            try:
                line_f = float(line)
                p_over_f = float(p_over)
                mu_f = float(mu)
            except (ValueError, TypeError):
                continue

            family = "normal" if (sport == "nba" and stat == "points") else "negbin"
            disp = res11_pop.invert_dispersion(family, mu_f, line_f, p_over_f)
            if disp is None:
                continue

            # Parse commence to tz-aware UTC
            comm = res11_pop.utc(commence_raw)
            et_day = comm.astimezone(_ET).date().isoformat()

            raw_player = r.get("player", "")
            p_norm = r.get("player_norm") or props.norm_name(raw_player)

            rows.append({
                "sport": sport,
                "stat": stat,
                "player_norm": p_norm,
                "player": raw_player,
                "team": r.get("team"),
                "opp": r.get("opp"),
                "mu": mu_f,
                "disp": float(disp),
                "family": family,
                "commence": comm,
                "et_day": et_day,
            })

    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(["sport", "player_norm", "stat", "et_day"], keep="last")
    return df[cols].copy()


def match_distribution(pair: dict | pd.Series, dists: pd.DataFrame) -> dict | None:
    """Match a BetMGM pair to its frozen served distribution row (§1.2, §3.1).

    Candidates match on:
      sport == pair.sport
      stat == pair.stat
      player_norm == props.norm_name(pair.player_name)
      et_day == pair.game_date
    Picks candidate with min |commence - start_time| requiring <= 6 hours.
    """
    if dists.empty:
        return None

    sport = pair.get("sport") if isinstance(pair, dict) else pair["sport"]
    stat = pair.get("stat") if isinstance(pair, dict) else pair["stat"]
    player_name = pair.get("player_name") if isinstance(pair, dict) else pair["player_name"]
    game_date = pair.get("game_date") if isinstance(pair, dict) else pair["game_date"]
    start_time_raw = pair.get("start_time") if isinstance(pair, dict) else pair["start_time"]

    p_norm = props.norm_name(player_name)
    cands = dists[
        (dists["sport"] == sport) &
        (dists["stat"] == stat) &
        (dists["player_norm"] == p_norm) &
        (dists["et_day"] == game_date)
    ]
    if cands.empty:
        return None

    try:
        start_dt = res11_pop.utc(start_time_raw)
    except Exception:
        return None

    best_row = None
    min_diff = None
    max_window = timedelta(hours=6)

    for r in cands.itertuples(index=False):
        diff = abs(r.commence - start_dt)
        if diff <= max_window:
            if min_diff is None or diff < min_diff:
                min_diff = diff
                best_row = r._asdict()

    return best_row


def gate_bucket(delta: float) -> str:
    """Bucket a net gate delta per ruling Q1 and adaptation A2 (§3.1).

    'overflow' if delta > 0.12 (winner's curse hard veto cap);
    '8pp' if delta >= 0.08;
    '5pp' if delta >= 0.05;
    '3pp' if delta >= 0.03;
    else 'sub_3pp'.
    """
    if delta > 0.12:
        return "overflow"
    if delta >= 0.08:
        return "8pp"
    if delta >= 0.05:
        return "5pp"
    if delta >= 0.03:
        return "3pp"
    return "sub_3pp"


def build_trigger_rows(
    pairs: pd.DataFrame,
    cons: pd.DataFrame,
    dists: pd.DataFrame,
    run_id: str,
    banked_at: str,
) -> tuple[list[dict], dict]:
    """Construct priced trigger rows from pairs, consensus, and distributions (§3.1).

    Unpriced pairs (no distribution match) are counted in stats, not logged.
    """
    stats = {"total_pairs": len(pairs), "priced": 0, "unpriced": 0}
    if pairs.empty:
        return [], stats

    # Fast consensus lookup by (date, event_id, player_id, stat)
    cons_map: dict[tuple, dict] = {}
    if not cons.empty:
        for r in cons.itertuples(index=False):
            key = (str(r.date), int(r.event_id), int(r.player_id), str(r.stat))
            cons_map[key] = {
                "line_cons": r.line_cons,
                "n_ref_books": int(r.n_ref_books),
                "quotes_by_line": getattr(r, "quotes_by_line", {}),
            }

    rows = []
    for p in pairs.itertuples(index=False):
        dist_match = match_distribution(p._asdict(), dists)
        if dist_match is None:
            stats["unpriced"] += 1
            continue

        stats["priced"] += 1

        game_date = str(p.game_date)
        game_key = str(p.event_id)
        sport = str(p.sport)
        stat = str(p.stat)
        player_id = int(p.player_id)
        player_name = str(p.player_name)
        player_norm = props.norm_name(player_name)
        team_abbr = str(p.team_abbr)
        home_abbr = str(p.home_abbr)
        away_abbr = str(p.away_abbr)
        side = "under"
        line_mgm = float(p.line_mgm)
        t_trigger = str(p.scraped_at)

        start_time_str = str(p.start_time)
        try:
            start_dt = res11_pop.utc(start_time_str)
            trig_dt = res11_pop.utc(t_trigger) if t_trigger else start_dt
            minutes_to_start = (start_dt - trig_dt).total_seconds() / 60.0
        except Exception:
            minutes_to_start = 0.0

        odds_under_mgm = int(p.odds_under_mgm)
        odds_over_mgm = int(p.odds_over_mgm)
        dec_under_mgm = decimal(odds_under_mgm)
        p_imp_under_mgm = implied(odds_under_mgm)
        p_imp_over_mgm = implied(odds_over_mgm)
        two_sided_sum_mgm = p_imp_under_mgm + p_imp_over_mgm
        p_devig_under_mgm = p_imp_under_mgm / two_sided_sum_mgm

        # Consensus lookups
        cons_info = cons_map.get((game_date, int(p.event_id), player_id, stat))
        if cons_info is not None:
            line_cons = cons_info["line_cons"]
            n_ref_books = cons_info["n_ref_books"]
            quotes_by_line = cons_info["quotes_by_line"]
        else:
            line_cons = None
            n_ref_books = 0
            quotes_by_line = {}

        if line_cons is not None:
            line_diff = line_mgm - float(line_cons)
            line_eq_cons = bool(abs(line_diff) < 1e-6)
        else:
            line_diff = None
            line_eq_cons = False

        # Per-pair consensus under prob and dispersion at line_mgm
        ref_probs_at_line = quotes_by_line.get(line_mgm, [])
        if ref_probs_at_line:
            p_cons_under_at_mgm_line = float(statistics.median(ref_probs_at_line))
            cons_dispersion = (
                float(statistics.pstdev(ref_probs_at_line))
                if len(ref_probs_at_line) >= 2 else None
            )
        else:
            p_cons_under_at_mgm_line = None
            cons_dispersion = None

        cons_median_age_min = 0.0  # Adaptation A5

        # Distribution fields
        mu = float(dist_match["mu"])
        disp = float(dist_match["disp"])
        family = str(dist_match["family"])
        p_dist_under = float(res11_pop.dist_side_prob(family, mu, disp, line_mgm, "under"))
        gate_delta = float(p_dist_under - p_imp_under_mgm)
        ev_net = float(p_dist_under * dec_under_mgm - 1.0)
        bucket = gate_bucket(gate_delta)

        row = {
            "ruling": RULING,
            "run_id": run_id,
            "banked_at": banked_at,
            "game_date": game_date,
            "game_key": game_key,
            "sport": sport,
            "stat": stat,
            "player_id": player_id,
            "player_name": player_name,
            "player_norm": player_norm,
            "team_abbr": team_abbr,
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
            "side": side,
            "line_mgm": line_mgm,
            "t_trigger": t_trigger,
            "commence_ts": start_time_str,
            "minutes_to_start": minutes_to_start,
            "odds_under_mgm": odds_under_mgm,
            "odds_over_mgm": odds_over_mgm,
            "dec_under_mgm": dec_under_mgm,
            "p_imp_under_mgm": p_imp_under_mgm,
            "p_imp_over_mgm": p_imp_over_mgm,
            "two_sided_sum_mgm": two_sided_sum_mgm,
            "p_devig_under_mgm": p_devig_under_mgm,
            "line_cons": line_cons,
            "line_diff": line_diff,
            "line_eq_cons": line_eq_cons,
            "n_ref_books": n_ref_books,
            "p_cons_under_at_mgm_line": p_cons_under_at_mgm_line,
            "cons_dispersion": cons_dispersion,
            "cons_median_age_min": cons_median_age_min,
            "mu": mu,
            "disp": disp,
            "family": family,
            "p_dist_under": p_dist_under,
            "gate_delta": gate_delta,
            "ev_net": ev_net,
            "gate_bucket": bucket,
            # Closing fields initialized to None
            "t_close": None,
            "mgm_present_at_close": None,
            "line_mgm_close": None,
            "odds_under_mgm_close": None,
            "p_imp_under_mgm_close": None,
            "line_cons_close": None,
            "p_cons_under_close": None,
            "n_ref_books_close": None,
            # Outcome fields initialized to None
            "actual_stat": None,
            "outcome_under": None,
            "flat_pnl_1u": None,
        }
        rows.append(row)

    return rows, stats


def natural_key(row: dict) -> str:
    """Deduplication natural key of a trigger row (§3.1).

    game_date|game_key|player_id|stat|line_mgm(2dp)
    """
    g_date = str(row["game_date"])
    g_key = str(row["game_key"])
    p_id = str(row["player_id"])
    stat = str(row["stat"])
    line_f = float(row["line_mgm"])
    return f"{g_date}|{g_key}|{p_id}|{stat}|{line_f:.2f}"


def telemetry_for_day(
    pairs_all: pd.DataFrame,
    cons: pd.DataFrame,
    date: str | None = None,
    unpriced_keys: set[str] | None = None,
) -> dict:
    """Compute day-level telemetry across all BetMGM two-sided pairs (§3.1).

    Captures: n_pairs (ALL pairs incl unpriced), n_pairs_unpriced, n_line_eq,
    and lists of two_sided_sum_mgm floats.
    """
    day_date = date
    if day_date is None and not pairs_all.empty:
        day_date = str(pairs_all["game_date"].iloc[0])

    # Pre-map line_cons
    cons_map = {}
    if not cons.empty:
        for r in cons.itertuples(index=False):
            key = (str(r.date), int(r.event_id), int(r.player_id), str(r.stat))
            cons_map[key] = r.line_cons

    by_stat: dict[str, dict] = {}
    for league, stats_list in MARKETS_BY_LEAGUE.items():
        for st in stats_list:
            key = f"{league}:{st}"
            by_stat[key] = {
                "sport": league,
                "stat": st,
                "n_pairs": 0,
                "n_pairs_unpriced": 0,
                "n_line_eq": 0,
                "sums": [],
            }

    nba_agg = {"n_pairs": 0, "n_pairs_unpriced": 0, "n_line_eq": 0, "sums": []}
    nhl_agg = {"n_pairs": 0, "n_pairs_unpriced": 0, "n_line_eq": 0, "sums": []}

    for p in pairs_all.itertuples(index=False):
        sp = str(p.sport)
        st = str(p.stat)
        stat_key = f"{sp}:{st}"
        if stat_key not in by_stat:
            by_stat[stat_key] = {
                "sport": sp, "stat": st, "n_pairs": 0,
                "n_pairs_unpriced": 0, "n_line_eq": 0, "sums": []
            }

        rec = by_stat[stat_key]
        rec["n_pairs"] += 1

        # Check line equality to consensus
        c_line = cons_map.get((str(p.game_date), int(p.event_id), int(p.player_id), st))
        if c_line is not None and abs(float(p.line_mgm) - float(c_line)) < 1e-6:
            rec["n_line_eq"] += 1

        # Implied sum
        pu = implied(int(p.odds_under_mgm))
        po = implied(int(p.odds_over_mgm))
        s_val = round(pu + po, 6)
        rec["sums"].append(s_val)

        # Unpriced check
        pair_nat_key = f"{p.game_date}|{p.event_id}|{p.player_id}|{st}|{float(p.line_mgm):.2f}"
        if unpriced_keys and pair_nat_key in unpriced_keys:
            rec["n_pairs_unpriced"] += 1

        target_agg = nba_agg if sp == "nba" else nhl_agg
        target_agg["n_pairs"] += 1
        if c_line is not None and abs(float(p.line_mgm) - float(c_line)) < 1e-6:
            target_agg["n_line_eq"] += 1
        target_agg["sums"].append(s_val)
        if unpriced_keys and pair_nat_key in unpriced_keys:
            target_agg["n_pairs_unpriced"] += 1

    total_pairs = sum(r["n_pairs"] for r in by_stat.values())
    total_unpriced = sum(r["n_pairs_unpriced"] for r in by_stat.values())
    total_eq = sum(r["n_line_eq"] for r in by_stat.values())
    all_sums = [s for r in by_stat.values() for s in r["sums"]]

    return {
        "date": day_date,
        "n_pairs": total_pairs,
        "n_pairs_unpriced": total_unpriced,
        "n_line_eq": total_eq,
        "sums": all_sums,
        "nba": nba_agg,
        "nhl": nhl_agg,
        "by_stat": by_stat,
    }


# --------------------------------------------------------------------------- chunk restore / bank
def restore_bank(store) -> tuple[set[str], list[dict]]:
    """Scan existing chunk files in RES17/shadow/chunks/ and restore banked keys and rows."""
    keys: set[str] = set()
    rows: list[dict] = []
    chunk_files = store.list(f"{PREFIX}/chunks/")
    for cf in chunk_files:
        if not cf.endswith(".jsonl.gz"):
            continue
        try:
            data = gzip.decompress(store.read(cf)).decode("utf-8")
            for line in data.splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                nk = natural_key(r)
                if nk not in keys:
                    keys.add(nk)
                    rows.append(r)
        except Exception as e:
            print(f"betmgm_under_shadow: warning: failed reading chunk {cf}: {e}", file=sys.stderr)
    return keys, rows


def write_chunk(store, rows: list[dict], run_id: str) -> str | None:
    """Bank rows create-only to RES17/shadow/chunks/{run_id}-{sha8}.jsonl.gz."""
    if not rows:
        return None
    # Sort keys for deterministic hashing
    body = "\n".join(json.dumps(r, sort_keys=True, default=_json_default) for r in rows) + "\n"
    gz_bytes = gzip.compress(body.encode("utf-8"))
    sha8 = hashlib.sha1(gz_bytes).hexdigest()[:8]
    key = f"{PREFIX}/chunks/{run_id}-{sha8}.jsonl.gz"
    ok = store.write_new(key, gz_bytes)
    return key if ok else None


# --------------------------------------------------------------------------- IO shell
def run(store, dates: list[str] | str, dry_run: bool = False) -> dict:
    """Execute forward logging pass for specified slate dates (§3.2)."""
    t_start = time.time()
    if isinstance(dates, str):
        dates = [dates]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    banked_at = datetime.now(timezone.utc).isoformat()

    banked_keys, _ = restore_bank(store)

    date_summaries = {}
    all_new_rows = []

    for d in dates:
        summary_d = {
            "date": d,
            "partitions_found": 0,
            "partitions_missing": 0,
            "pairs": 0,
            "priced": 0,
            "unpriced": 0,
            "banked_new": 0,
            "already_banked": 0,
        }

        # Read 6 market parquets
        market_frames = []
        for league, markets in MARKETS_BY_LEAGUE.items():
            for mkt in markets:
                p_key = f"Odds/actionnetwork_{league}/{mkt}/{d}.parquet"
                try:
                    data = store.read(p_key)
                    df_p = pd.read_parquet(io.BytesIO(data))
                    df_p["sport"] = league
                    if "market" not in df_p.columns:
                        df_p["market"] = mkt
                    market_frames.append(df_p)
                    summary_d["partitions_found"] += 1
                except Exception:
                    summary_d["partitions_missing"] += 1

        if not market_frames:
            date_summaries[d] = summary_d
            continue

        raw_all = pd.concat(market_frames, ignore_index=True)
        core = core_frame(raw_all)
        pairs = extract_pairs(core)
        cons = consensus_fields(core, pairs)

        summary_d["pairs"] = len(pairs)

        # Mirror EVSweep archives for [d-1, d, d+1]
        try:
            cur_dt = datetime.fromisoformat(d)
        except Exception:
            cur_dt = datetime.strptime(d, "%Y-%m-%d")

        archive_dates = [
            (cur_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
            d,
            (cur_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
        archives = {}
        for sp in ("nba", "nhl"):
            for ad in archive_dates:
                a_key = f"EVSweep/{sp}_props/archive/{ad}.json"
                try:
                    arch_bytes = store.read(a_key)
                    archives[a_key] = json.loads(arch_bytes.decode("utf-8"))
                except Exception:
                    pass

        dists = build_distributions(archives)
        trig_rows, b_stats = build_trigger_rows(pairs, cons, dists, run_id, banked_at)

        summary_d["priced"] = b_stats["priced"]
        summary_d["unpriced"] = b_stats["unpriced"]

        # Track unpriced natural keys for telemetry
        unpriced_keys = set()
        for p in pairs.itertuples(index=False):
            if match_distribution(p._asdict(), dists) is None:
                nk = f"{p.game_date}|{p.event_id}|{p.player_id}|{p.stat}|{float(p.line_mgm):.2f}"
                unpriced_keys.add(nk)

        # Day telemetry
        telem = telemetry_for_day(pairs, cons, date=d, unpriced_keys=unpriced_keys)
        if not dry_run:
            telem_key = f"{PREFIX}/telemetry/{d}.json"
            telem_bytes = json.dumps(telem, indent=1, default=_json_default).encode("utf-8")
            written = store.write_new(telem_key, telem_bytes)
            summary_d["telemetry_written"] = written

        # Deduplicate trigger rows against bank
        day_new_rows = []
        for r in trig_rows:
            nk = natural_key(r)
            if nk in banked_keys:
                summary_d["already_banked"] += 1
            else:
                banked_keys.add(nk)
                day_new_rows.append(r)
                summary_d["banked_new"] += 1

        all_new_rows.extend(day_new_rows)
        date_summaries[d] = summary_d

    # Bank chunk
    chunk_key = None
    if not dry_run and all_new_rows:
        chunk_key = write_chunk(store, all_new_rows, run_id)

    run_summary = {
        "run_id": run_id,
        "banked_at": banked_at,
        "dry_run": dry_run,
        "dates": date_summaries,
        "total_new_rows": len(all_new_rows),
        "chunk_key": chunk_key,
        "elapsed_sec": round(time.time() - t_start, 2),
    }

    if not dry_run:
        summary_bytes = json.dumps(run_summary, indent=1, default=_json_default).encode("utf-8")
        store.write_new(f"{PREFIX}/runs/{run_id}.json", summary_bytes)

    return run_summary


# --------------------------------------------------------------------------- finalize
def finalize_day(store, et_day: str, dry_run: bool = False) -> dict:
    """Enrich a day's banked triggers with ClosingPanel closes and master outcomes (§3.3)."""
    finalized_key = f"{PREFIX}/finalized/{et_day}.jsonl.gz"
    existing_finalized = store.list(finalized_key)
    if existing_finalized and finalized_key in existing_finalized:
        return {"already_finalized": True, "et_day": et_day}

    # Load trigger rows from chunks where game_date == et_day
    _, banked_rows = restore_bank(store)
    day_triggers = [r for r in banked_rows if str(r.get("game_date")) == et_day]

    summary = {
        "et_day": et_day,
        "triggers": len(day_triggers),
        "closes_paired": 0,
        "mgm_present": 0,
        "settled": 0,
        "pushes": 0,
        "voids": 0,
        "unresolved": 0,
        "alias_rescued": 0,
        "dry_run": dry_run,
    }

    if not day_triggers:
        return summary

    # Load ClosingPanel for NBA and NHL
    cp_rows_by_sport = {"nba": [], "nhl": []}
    for sp in ("nba", "nhl"):
        cp_key = f"ClosingPanel/{sp}/{et_day}.json.gz"
        try:
            cp_data = json.loads(gzip.decompress(store.read(cp_key)).decode("utf-8"))
            cp_rows_by_sport[sp] = cp_data.get("rows", [])
        except Exception:
            pass

    # Load outcome masters
    masters = {"nba": pd.DataFrame(), "nhl": pd.DataFrame()}
    try:
        nba_data = store.read("NBA/player_boxscores_master.csv")
        masters["nba"] = pd.read_csv(io.BytesIO(nba_data), low_memory=False)
    except Exception:
        pass

    try:
        nhl_data = store.read("NHL/skater_boxscores_master.csv")
        masters["nhl"] = pd.read_csv(io.BytesIO(nhl_data), low_memory=False)
    except Exception:
        pass

    # Pre-index masters by (settler_norm_name, date)
    master_index: dict[str, dict[tuple, list]] = {"nba": {}, "nhl": {}}
    for sp in ("nba", "nhl"):
        m_df = masters[sp]
        if m_df.empty:
            continue
        for row in m_df.itertuples(index=False):
            name_val = getattr(row, "name", "")
            d_val = str(getattr(row, "date", ""))
            s_name = settler_norm_name(name_val)
            master_index[sp].setdefault((s_name, d_val), []).append(row)

    # Pre-index masters by (settle_canon_name, date)
    master_canon_index: dict[str, dict[tuple, list]] = {"nba": {}, "nhl": {}}
    for sp in ("nba", "nhl"):
        m_df = masters[sp]
        if m_df.empty:
            continue
        for row in m_df.itertuples(index=False):
            name_val = getattr(row, "name", "")
            d_val = str(getattr(row, "date", ""))
            c_name = settle_canon_name(name_val)
            master_canon_index[sp].setdefault((c_name, d_val), []).append(row)

    enriched_rows = []
    for trig in day_triggers:
        row = dict(trig)
        sp = row["sport"]
        stat = row["stat"]
        p_norm = row["player_norm"]
        line_mgm = float(row["line_mgm"])
        commence_dt = res11_pop.utc(row["commence_ts"])

        # 1. Close pairing (§1.3, §3.3)
        cp_rows = cp_rows_by_sport.get(sp, [])
        cp_target_market = CP_MARKET_MAP.get(stat)

        # Candidates match on norm_name(player), market, and |commence - trigger commence| <= 6h
        cands = []
        for cp_r in cp_rows:
            if cp_r.get("market") != cp_target_market:
                continue
            if props.norm_name(cp_r.get("player", "")) != p_norm:
                continue
            try:
                cp_comm = res11_pop.utc(cp_r.get("commence_time"))
                if abs(cp_comm - commence_dt) <= timedelta(hours=6):
                    cands.append(cp_r)
            except Exception:
                continue

        # Same-line row at point == line_mgm
        same_line_cp = None
        for c in cands:
            if abs(float(c.get("point", -999.0)) - line_mgm) < 1e-9:
                same_line_cp = c
                break

        # Check BetMGM presence at close
        mgm_present = False
        mgm_quote_used = None
        mgm_point_used = None

        for c in cands:
            close_books = c.get("close", {}).get("books", {})
            if "betmgm" in close_books:
                prices = close_books["betmgm"].get("prices", {})
                # Require two-sided
                has_o = any(k.lower().startswith("over") for k in prices)
                has_u = any(k.lower().startswith("under") for k in prices)
                if has_o and has_u:
                    mgm_present = True
                    break

        if mgm_present:
            summary["mgm_present"] += 1

        # Determine line_mgm_close & odds_under_mgm_close
        if same_line_cp is not None:
            close_books = same_line_cp.get("close", {}).get("books", {})
            if "betmgm" in close_books:
                prices = close_books["betmgm"].get("prices", {})
                # Key: Under <Player> or prefix
                u_odds = None
                for k, v in prices.items():
                    if k.lower().startswith("under"):
                        u_odds = v
                        break
                if u_odds is not None:
                    mgm_quote_used = close_books["betmgm"]
                    mgm_point_used = line_mgm
                    row["line_mgm_close"] = line_mgm
                    row["odds_under_mgm_close"] = int(u_odds)

        if row.get("line_mgm_close") is None and mgm_present:
            # Elif present at another point -> closest to line_mgm
            other_points = []
            for c in cands:
                pt = float(c.get("point", -999.0))
                c_books = c.get("close", {}).get("books", {})
                if "betmgm" in c_books:
                    prices = c_books["betmgm"].get("prices", {})
                    u_odds = None
                    for k, v in prices.items():
                        if k.lower().startswith("under"):
                            u_odds = v
                            break
                    if u_odds is not None:
                        other_points.append((abs(pt - line_mgm), pt, u_odds, c_books["betmgm"]))
            if other_points:
                other_points.sort(key=lambda x: (x[0], x[1]))
                best_other = other_points[0]
                row["line_mgm_close"] = best_other[1]
                row["odds_under_mgm_close"] = int(best_other[2])
                mgm_quote_used = best_other[3]
                mgm_point_used = best_other[1]

        row["mgm_present_at_close"] = mgm_present
        if row.get("odds_under_mgm_close") is not None:
            row["p_imp_under_mgm_close"] = implied(row["odds_under_mgm_close"])

        # t_close
        if mgm_quote_used and mgm_quote_used.get("snap_ts"):
            row["t_close"] = str(mgm_quote_used["snap_ts"])
        elif same_line_cp is not None:
            # Max snap_ts among CP ref books
            c_books = same_line_cp.get("close", {}).get("books", {})
            ref_ts = [
                str(b_info.get("snap_ts")) for b_name, b_info in c_books.items()
                if b_name.lower() in CP_REF_BOOKS and b_info.get("snap_ts")
            ]
            row["t_close"] = max(ref_ts) if ref_ts else None
        else:
            row["t_close"] = None

        # Consensus at close for same-line row (§1.3, §3.3)
        if same_line_cp is not None:
            c_books = same_line_cp.get("close", {}).get("books", {})
            ref_devig_under = []
            for b_name, b_info in c_books.items():
                if b_name.lower() in CP_REF_BOOKS:
                    prices = b_info.get("prices", {})
                    o_odds, u_odds = None, None
                    for k, v in prices.items():
                        if k.lower().startswith("over"):
                            o_odds = v
                        elif k.lower().startswith("under"):
                            u_odds = v
                    if o_odds is not None and u_odds is not None:
                        io_val = implied(o_odds)
                        iu_val = implied(u_odds)
                        ref_devig_under.append(iu_val / (io_val + iu_val))

            n_ref_close = len(ref_devig_under)
            row["n_ref_books_close"] = n_ref_close
            row["p_cons_under_close"] = (
                float(statistics.median(ref_devig_under))
                if n_ref_close >= 4 else None
            )
        else:
            row["n_ref_books_close"] = 0
            row["p_cons_under_close"] = None

        # line_cons_close: modal point across candidate rows with CP ref books two-sided
        point_ref_counts: dict[float, int] = {}
        for c in cands:
            pt = float(c.get("point", -999.0))
            c_books = c.get("close", {}).get("books", {})
            ref_cnt = 0
            for b_name, b_info in c_books.items():
                if b_name.lower() in CP_REF_BOOKS:
                    prices = b_info.get("prices", {})
                    has_o = any(k.lower().startswith("over") for k in prices)
                    has_u = any(k.lower().startswith("under") for k in prices)
                    if has_o and has_u:
                        ref_cnt += 1
            if ref_cnt > 0:
                point_ref_counts[pt] = ref_cnt

        if point_ref_counts:
            max_c = max(point_ref_counts.values())
            tied_pts = [pt for pt, cnt in point_ref_counts.items() if cnt == max_c]
            tied_pts.sort(key=lambda pt: (abs(pt - line_mgm), pt))
            row["line_cons_close"] = tied_pts[0]
        else:
            row["line_cons_close"] = None

        if cands:
            summary["closes_paired"] += 1

        # 2. Outcome settlement (§1.4, §3.3)
        trig_pname = row["player_name"]
        settler_p = settler_norm_name(trig_pname)
        if sp == "nba":
            target_date = commence_dt.date().isoformat()  # UTC tip date (RES-13)
            m_team_req = NBA_TEAM_ALIAS.get(row["team_abbr"], row["team_abbr"])
        else:
            target_date = et_day  # Local slate date
            m_team_req = row["team_abbr"]

        m_cands = master_index[sp].get((settler_p, target_date), [])
        # Filter by team_abbr consistency
        valid_mcands = [
            c for c in m_cands
            if getattr(c, "team_abbr", "") in (m_team_req, row["team_abbr"], row["home_abbr"], row["away_abbr"])
        ]

        if len(valid_mcands) != 1:
            canon_teams = {
                settle_canon_team(sp, row["team_abbr"]),
                settle_canon_team(sp, row["home_abbr"]),
                settle_canon_team(sp, row["away_abbr"]),
            }
            fb_cands = master_canon_index[sp].get((settle_canon_name(trig_pname), target_date), [])
            fb_valid = [
                c for c in fb_cands
                if settle_canon_team(sp, getattr(c, "team_abbr", "")) in canon_teams
            ]
            if len(fb_valid) == 1:
                valid_mcands = fb_valid
                summary["alias_rescued"] += 1

        if len(valid_mcands) == 1:
            cand = valid_mcands[0]
            played_val = getattr(cand, "played", True)
            is_dnp = (played_val == 0 or played_val is False or str(played_val).lower() == "false")

            if is_dnp:
                row["actual_stat"] = None
                row["outcome_under"] = "void"
                row["flat_pnl_1u"] = None
                summary["voids"] += 1
            else:
                stat_col = (
                    NBA_MASTER_STAT_COLS.get(stat)
                    if sp == "nba" else NHL_MASTER_STAT_COLS.get(stat)
                )
                actual_val = getattr(cand, stat_col, None) if stat_col else None
                if actual_val is None or not math.isfinite(float(actual_val)):
                    row["actual_stat"] = None
                    row["outcome_under"] = None
                    row["flat_pnl_1u"] = None
                    summary["unresolved"] += 1
                else:
                    act_f = float(actual_val)
                    row["actual_stat"] = act_f
                    if abs(act_f - line_mgm) < 1e-9:
                        row["outcome_under"] = "push"
                        row["flat_pnl_1u"] = None
                        summary["pushes"] += 1
                    elif act_f < line_mgm:
                        row["outcome_under"] = "win"
                        row["flat_pnl_1u"] = float(row["dec_under_mgm"] - 1.0)
                        summary["settled"] += 1
                    else:
                        row["outcome_under"] = "loss"
                        row["flat_pnl_1u"] = -1.0
                        summary["settled"] += 1
        else:
            row["actual_stat"] = None
            row["outcome_under"] = None
            row["flat_pnl_1u"] = None
            summary["unresolved"] += 1

        enriched_rows.append(row)

    if not dry_run and enriched_rows:
        body = "\n".join(json.dumps(r, sort_keys=True, default=_json_default) for r in enriched_rows) + "\n"
        store.write_new(finalized_key, gzip.compress(body.encode("utf-8")))

    return summary


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BetMGM Unders Shadow Logger & Finalizer (RES-17)")
    ap.add_argument("--store", default="gcs", help="Store type: gcs | gcloud | dir:PATH")
    ap.add_argument("--bucket", default=None, help="GCS bucket name")
    ap.add_argument("--date", default=None, help="Single slate date (YYYY-MM-DD)")
    ap.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    ap.add_argument("--finalize-day", default=None, help="Run settlement finalize for this ET day")
    ap.add_argument("--dry-run", action="store_true", help="Compute summary but bank nothing")
    args = ap.parse_args(argv)

    # Arm check (§1.5): dir: stores and dry-run bypass
    is_dir = args.store.startswith("dir:")
    if not args.dry_run and not is_dir and os.environ.get(ARM_ENV) != "1":
        print(f"betmgm_under_shadow: not armed ({ARM_ENV} != 1); exiting without banking", file=sys.stderr)
        return 3

    store = make_store(args.store, args.bucket)

    if args.finalize_day:
        res = finalize_day(store, args.finalize_day, dry_run=args.dry_run)
        print(json.dumps(res, indent=1, default=_json_default))
        return 0

    # Determine dates to log
    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dt_start = datetime.strptime(args.start, "%Y-%m-%d")
        dt_end = datetime.strptime(args.end, "%Y-%m-%d")
        dates = []
        c = dt_start
        while c <= dt_end:
            dates.append(c.strftime("%Y-%m-%d"))
            c += timedelta(days=1)
    else:
        # Default = yesterday ET
        yest = (datetime.now(timezone.utc).astimezone(_ET) - timedelta(days=1)).date().isoformat()
        dates = [yest]

    res = run(store, dates, dry_run=args.dry_run)
    print(json.dumps(res, indent=1, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
