"""
ev.prop_clv_banker -- the prop CLV banker (Argon Data Direction 1, 2026-11-10).

WHY THIS EXISTS. True prop CLV was UNMEASURABLE historically: the archives hold a single daily snapshot per
prop, so no settled row could ever be paired with the market's closing price. Argon D1 fixes that forward at
$0 new spend by (a) banking a T-15 closing snapshot for every game, (b) persisting price-ratio CLV on every
settled prop row, and (c) judging the whole thing on a pre-registered coverage test:

    Nov 24, 2026, after a 14-day live run: >= 92% of settled NBA / NHL / NFL prop rows carry a valid paired
    CLV, with ZERO ParlayAPI credit-cap overruns.

WHAT IS HERE (code only -- no job is provisioned by the change that introduces this module; see
deploy/setup_prop_clv_banker.sh, which is NOT run by that PR):

  1. CAPTURE (`capture`): a T-15 closing-snapshot job for the accumulator sports (NBA, NHL). It reuses the
     existing guarded spend path -- nba.odds.credit_ledger allowance BEFORE, record AFTER, the shared-pool
     reserve floor, and the per-sport accumulator caps in nba/config.py, NONE of which this module raises.
     Argon's 10,000-credit reallocation is a CARVE-OUT INSIDE those caps (CLV_CLOSING_MONTHLY_BUDGET in
     nba/config.py: 5,000 per sport, tracked on a closing sub-ledger), not new budget: parlay_budget_total()
     is unchanged. Only events starting within CLV_CLOSING_CAPTURE_WINDOW_MIN are bought, soonest first, so a
     firing with no imminent game costs exactly one slate credit.

  2. SNAPSHOT WINDOWS. Every banked snapshot row carries `mins_to_kickoff` and a `window` class, so the
     decision snapshots of the soccer path measurement (~/workspace/beezy/soccer-path-measurement-2026-11-11.md)
     are derivable from the bank: decision snapshots at T-24h / T-6h / T-1h (the last snapshot at or before
     each horizon) plus the CLOSE (the last snapshot strictly before game start). The OPEN is NOT a decision
     snapshot: opening deviations are vig-shape noise, so early snapshots are classed "early" and are kept
     only as a movement reference. close_age_min (the closing snapshot's own mins_to_kickoff) is stored on
     every closing row, generalising nba/props/forward.py::closing_snapshot (last-bank-before-tip) and
     nfl/props/lines.py (close_lead_min / CLOSE_FRESH_MIN) to every accumulator sport.

  3. CLV (`attach`): for each settled prop row, the closing consensus for the same (player, stat, line) --
     ev.props.build_consensus, median over real two-sided books of each book's proportional de-vig, DFS books
     excluded -- and the row's price-ratio CLV against its decision-time (entry) price, Argon D1 Step 4:

         clv_cons_pct = (entry_decimal / closing_fair_decimal - 1) * 100,
         closing_fair_decimal = 1 / de-vigged consensus P(side) at the close.

     Naming follows nba/props/forward.py: `clv_pct` is the SAME-BOOK, same-side price-ratio CLV (present when
     the row's own book still quotes the line at the close); `clv_cons_pct` is the consensus CLV above and is
     the D1 headline. FAIL CLOSED: a row without a valid closing snapshot gets clv fields = None and an
     explicit marker in `clv_missing_reason` (never a fabricated number):
         missing_no_closing_snapshot | missing_prop_not_quoted_at_close | missing_close_stale |
         missing_entry_price | missing_event_id
     A close older than CLV_CLOSING_MAX_AGE_MIN before start is stale (the T-15 job was not running near
     tip): its consensus fields are attached for diagnosis but its CLV is NOT valid and does not count
     toward coverage.

  4. COVERAGE (`report`): the D1 test statistic. Denominator, stated precisely: settled prop rows = input
     rows whose `result` is one of win / loss / push (void, pending and ungradable rows are NOT settled and
     are excluded; if the input has no `result` column every input row is counted and the report says so).
     Numerator: denominator rows with clv_missing_reason empty AND clv_cons_pct present. The report also
     carries the credit-cap overrun count for the window (zero required to pass).

NFL note: the NFL accumulator has no banker budget (ODDS_BANK_DEFAULT_BUDGET = 0 by design); NFL prop
snapshots are banked by the NFL sweep (EVSweep/nfl_props/raw/**, read by nfl.props.lines). The CLV attach
and coverage layers here are sport-agnostic and run on any settled rows + closing table; the capture job
covers the two accumulator sports Argon funded.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from ev.props import build_consensus, parse_event_props
from mlb_core.odds.utils import american_to_decimal, clv_pct_from_prices, implied_to_american
from nba.config import (CLV_CLOSING_CAPTURE_WINDOW_MIN, CLV_CLOSING_MAX_AGE_MIN, CLV_CLOSING_MONTHLY_BUDGET,
                        CLV_CLOSING_TARGET_LEAD_MIN, CLV_COVERAGE_TARGET, ODDSACCUM_PREFIX, PARLAY_PROP_MARKETS,
                        PARLAY_PROP_MARKETS_ALT, TIMEZONE, oddsaccum_csv_key, oddsaccum_latest_kind_key,
                        oddsaccum_raw_key)

log = logging.getLogger("ev.prop_clv_banker")

ET = ZoneInfo(TIMEZONE)

# sport_key (ParlayAPI / OddsAccum) -> the short sport name ev.props.parse_event_props expects
SPORT_SHORT = {"basketball_nba": "nba", "icehockey_nhl": "nhl", "americanfootball_nfl": "nfl",
               "baseball_mlb": "mlb"}
SHORT_TO_KEY = {v: k for k, v in SPORT_SHORT.items()}

# Decision horizons (minutes before start) and their names. The close is horizon 0 by selection, not a row here.
DECISION_HORIZONS = (("t24h", 1440.0), ("t6h", 360.0), ("t1h", 60.0))
DECISION_WINDOWS = tuple(name for name, _ in DECISION_HORIZONS) + ("close",)
NON_DECISION_WINDOWS = ("early",)          # the open lives here: a movement reference, never a decision snapshot

SETTLED_RESULTS = frozenset({"win", "loss", "push"})

MISSING_NO_CLOSING = "missing_no_closing_snapshot"
MISSING_NOT_QUOTED = "missing_prop_not_quoted_at_close"
MISSING_CLOSE_STALE = "missing_close_stale"
MISSING_ENTRY_PRICE = "missing_entry_price"
MISSING_EVENT_ID = "missing_event_id"

CLV_COLUMNS = ["close_p_over", "close_p_side", "close_fair_decimal", "close_n_books", "close_ts",
               "close_age_min", "close_fresh", "clv_pct", "clv_cons_pct", "clv_missing_reason"]


# ----------------------------------------------------------------------------------------------- windows
def window_label(mins_to_kickoff) -> str | None:
    """Class of ONE banked snapshot from its mins_to_kickoff. Bands are anchored on the decision horizons:
    a snapshot belongs to the band of the latest decision point it can still inform. `early` (> 48h out,
    which includes every open) is NOT a decision class. None / NaN -> None; <= 0 -> 'post_start' (such
    snapshots are dropped by the series builder, never banked as closes)."""
    try:
        m = float(mins_to_kickoff)
    except (TypeError, ValueError):
        return None
    if m != m:
        return None
    if m <= 0:
        return "post_start"
    if m <= 45.0:
        return "close"
    if m <= 210.0:
        return "t1h"
    if m <= 900.0:
        return "t6h"
    if m <= 2880.0:
        return "t24h"
    return "early"


def _utc(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pt(x) -> float:
    return round(float(x), 3)


# ----------------------------------------------------------------------------------------------- snapshot series
def snapshot_rows_for_event(event_obj: dict, sport_key: str, snap_ts: datetime, resolver=None,
                            team_fn=None) -> list[dict]:
    """One banked single-event ParlayAPI payload at one snapshot time -> consensus series rows (one per
    player/stat/point). Same parsing + consensus as the scorer (ev.props), so keys line up with settled rows.
    Snapshots at/after commence produce NO rows (pre-start only, fail closed)."""
    commence = _utc((event_obj or {}).get("commence_time"))
    if commence is None or snap_ts is None:
        return []
    mins = (commence - snap_ts).total_seconds() / 60.0
    if mins <= 0:
        return []
    sport = SPORT_SHORT.get(sport_key, "nfl")
    quotes, _ = parse_event_props(event_obj, sport, resolver, team_fn)
    quotes = [q for q in quotes if not q.alt]
    cons = build_consensus(quotes, "proportional")
    names: dict = {}
    for q in quotes:
        names.setdefault(q.player_norm, q.player)
    base = {"sport_key": sport_key, "sport": sport, "event_id": str(event_obj.get("id")),
            "snap_ts": snap_ts, "commence": commence, "mins_to_kickoff": round(mins, 2),
            "window": window_label(mins)}
    rows = []
    for (pn, stat, point), c in cons.items():
        rows.append({**base, "player": names.get(pn, pn), "player_norm": pn, "stat": stat, "point": point,
                     "p_over": c.p_over, "n_books": c.n_books, "disp": c.disp,
                     "books": tuple(c.books),
                     "book_quotes": tuple((b, o, u) for b, o, u, _f in c.book_quotes)})
    return rows


def banked_snapshot_events(sport_key: str, date: str, lister=None, reader=None):
    """Yield (snap_ts_utc, event_obj) for every event in the day's banked OddsAccum props snapshots.
    File names are ET date + ET HHMM (nba/odds/accumulator.py::_now) -- the same convention
    nba/props/forward.py::closing_snapshot reads. `lister`/`reader` default to mlb_core.storage."""
    if lister is None or reader is None:
        from mlb_core import storage
        lister, reader = storage.list_keys, storage.read_bytes
    prefix = f"{ODDSACCUM_PREFIX}/{sport_key}/raw/{date}/"
    out = []
    for k in sorted(lister(prefix)):
        name = k.rsplit("/", 1)[-1]
        if not name.startswith("props_") or not name.endswith(".json"):
            continue
        hhmm = name[len("props_"):-len(".json")]
        try:
            ts_et = datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=ET)
        except ValueError:
            continue
        try:
            payload = json.loads(reader(k))
        except Exception:                                                                    # noqa: BLE001
            continue
        for ev in payload if isinstance(payload, list) else []:
            if isinstance(ev, dict):
                out.append((ts_et.astimezone(timezone.utc), ev))
    return out


def build_series(sport_key: str, dates, lister=None, reader=None, resolver=None, team_fn=None):
    """Banked snapshots over ET `dates` -> (series DataFrame, report). Pre-start snapshots only; the report
    counts what was dropped and why, so a thin bank is visible rather than silently thin."""
    report = {"snapshots": 0, "events_seen": 0, "rows": 0, "dropped_post_start_or_bad": 0}
    rows = []
    for date in dates:
        for ts, ev in banked_snapshot_events(sport_key, date, lister, reader):
            report["snapshots"] += 1
            r = snapshot_rows_for_event(ev, sport_key, ts, resolver, team_fn)
            if r:
                report["events_seen"] += 1
                rows += r
            else:
                report["dropped_post_start_or_bad"] += 1
    report["rows"] = len(rows)
    return pd.DataFrame(rows), report


# ----------------------------------------------------------------------------------------------- closing table
def select_last_at_or_before(rows: pd.DataFrame, horizon_min: float) -> pd.DataFrame:
    """The decision snapshot at one horizon, per prop: among snapshots taken at or before T-horizon
    (mins_to_kickoff >= horizon), the LAST one (max snap_ts). Mirrors the soccer path measurement's
    horizon-snapshot definition (a book's last print at or before that horizon)."""
    if rows.empty:
        return rows
    cand = rows[rows["mins_to_kickoff"] >= horizon_min]
    if cand.empty:
        return cand
    idx = cand.groupby(["event_id", "player_norm", "stat", "point"])["snap_ts"].idxmax()
    return cand.loc[idx]


def closing_table(series: pd.DataFrame) -> pd.DataFrame:
    """Series -> one row per (sport_key, event_id, player_norm, stat, point): the CLOSE (last pre-start
    snapshot quoting the prop, with close_age_min = its mins_to_kickoff) plus the snap_ts / age of each
    decision snapshot (t24h / t6h / t1h) where the prop was quoted. Empty in -> empty out (with columns)."""
    cols = ["sport_key", "sport", "event_id", "player_norm", "stat", "point", "player", "commence",
            "close_ts", "close_age_min", "close_fresh", "close_p_over", "close_n_books", "close_disp",
            "close_books", "close_book_quotes", "n_snapshots",
            "t24h_ts", "t24h_age_min", "t6h_ts", "t6h_age_min", "t1h_ts", "t1h_age_min"]
    if series is None or series.empty:
        return pd.DataFrame(columns=cols)
    s = series.sort_values("snap_ts", kind="stable")
    decision_idx = {}
    for name, horizon in DECISION_HORIZONS:
        sel = select_last_at_or_before(s, horizon)
        decision_idx[name] = {(r.event_id, r.player_norm, r.stat, _pt(r.point)): (r.snap_ts, r.mins_to_kickoff)
                              for r in sel.itertuples(index=False)} if not sel.empty else {}
    out = []
    for key, g in s.groupby(["sport_key", "event_id", "player_norm", "stat", "point"], sort=False):
        c = g.iloc[-1]                                   # last pre-start snapshot (series is pre-start only)
        row = {"sport_key": key[0], "sport": g["sport"].iloc[0], "event_id": key[1], "player_norm": key[2],
               "stat": key[3], "point": key[4], "player": c["player"], "commence": c["commence"],
               "close_ts": c["snap_ts"], "close_age_min": float(c["mins_to_kickoff"]),
               "close_fresh": bool(float(c["mins_to_kickoff"]) <= CLV_CLOSING_MAX_AGE_MIN),
               "close_p_over": float(c["p_over"]), "close_n_books": int(c["n_books"]),
               "close_disp": float(c["disp"]), "close_books": c["books"],
               "close_book_quotes": c["book_quotes"], "n_snapshots": int(len(g))}
        for name, _h in DECISION_HORIZONS:
            ts_age = decision_idx[name].get((key[1], key[2], key[3], _pt(key[4])))
            row[f"{name}_ts"], row[f"{name}_age_min"] = ts_age if ts_age else (None, None)
        out.append(row)
    return pd.DataFrame(out, columns=cols)


def event_close_index(series: pd.DataFrame) -> dict:
    """{event_id: (last pre-start snap_ts, its mins_to_kickoff)} over ALL props of the event -- lets the CLV
    attach distinguish 'no closing snapshot for this game at all' from 'game closed, this prop did not'."""
    if series is None or series.empty:
        return {}
    idx = series.groupby("event_id")["snap_ts"].idxmax()
    return {str(r.event_id): (r.snap_ts, float(r.mins_to_kickoff))
            for r in series.loc[idx].itertuples(index=False)}


# ----------------------------------------------------------------------------------------------- CLV attach
def _entry_decimal(row) -> float | None:
    dec = row.get("decimal")
    try:
        if dec is not None and float(dec) > 1.0:
            return float(dec)
    except (TypeError, ValueError):
        pass
    am = row.get("american")
    try:
        if am is not None:
            d = american_to_decimal(am)
            return float(d) if d == d and d > 1.0 else None
    except (TypeError, ValueError):
        pass
    return None


def attach_clv(settled: pd.DataFrame, closing: pd.DataFrame, event_close: dict | None = None) -> pd.DataFrame:
    """Settled prop rows + closing table -> the same rows with CLV_COLUMNS added. Pure: no I/O, input not
    mutated. Rows are matched on (event_id, player_norm, stat, point) -- the same key the scorer and
    nba.props.forward use. `event_close` (event_close_index over the underlying series) refines the missing
    marker; without it, a prop absent from the closing table is reported as no_closing_snapshot."""
    df = settled.copy()
    for c in CLV_COLUMNS:
        df[c] = None
    if df.empty:
        return df
    close_by_key = {}
    if closing is not None and not closing.empty:
        for r in closing.itertuples(index=False):
            close_by_key[(str(r.event_id), r.player_norm, r.stat, _pt(r.point))] = r
    event_close = event_close or {}
    for i, row in df.iterrows():
        eid = row.get("event_id")
        if eid is None or (isinstance(eid, float) and eid != eid) or str(eid) == "":
            df.at[i, "clv_missing_reason"] = MISSING_EVENT_ID
            continue
        eid = str(eid)
        try:
            key = (eid, row["player_norm"], row["stat"], _pt(row["point"] if "point" in row else row["line"]))
        except (KeyError, TypeError, ValueError):
            df.at[i, "clv_missing_reason"] = MISSING_NOT_QUOTED
            continue
        c = close_by_key.get(key)
        if c is None:
            df.at[i, "clv_missing_reason"] = (MISSING_NOT_QUOTED if eid in event_close
                                             else MISSING_NO_CLOSING)
            continue
        df.at[i, "close_ts"] = c.close_ts
        df.at[i, "close_age_min"] = float(c.close_age_min)
        df.at[i, "close_fresh"] = bool(c.close_fresh)
        df.at[i, "close_n_books"] = int(c.close_n_books)
        df.at[i, "close_p_over"] = float(c.close_p_over)
        side = str(row.get("side", "OVER")).strip().lower()
        is_over = side in ("over", "yes")
        p_side = float(c.close_p_over) if is_over else 1.0 - float(c.close_p_over)
        df.at[i, "close_p_side"] = p_side
        if not c.close_fresh:
            df.at[i, "clv_missing_reason"] = MISSING_CLOSE_STALE
            continue
        if not (0.0 < p_side < 1.0):
            df.at[i, "clv_missing_reason"] = MISSING_NOT_QUOTED
            continue
        fair_dec = 1.0 / p_side
        df.at[i, "close_fair_decimal"] = fair_dec
        entry_dec = _entry_decimal(row)
        if entry_dec is None:
            df.at[i, "clv_missing_reason"] = MISSING_ENTRY_PRICE
            continue
        df.at[i, "clv_cons_pct"] = round((entry_dec / fair_dec - 1.0) * 100.0, 4)
        df.at[i, "clv_missing_reason"] = None
        book = row.get("book")
        if book is not None:
            quotes = {b: (o, u) for b, o, u in (c.close_book_quotes or ())}
            pair = quotes.get(book)
            if pair:
                price = pair[0] if is_over else pair[1]
                if price is not None:
                    am = row.get("american")
                    entry_am = int(am) if am is not None and am == am else int(implied_to_american(1.0 / entry_dec))
                    v = clv_pct_from_prices(entry_am, int(price))
                    df.at[i, "clv_pct"] = None if v != v else float(v)
    return df


# ----------------------------------------------------------------------------------------------- coverage report
def coverage_report(rows: pd.DataFrame, target: float = CLV_COVERAGE_TARGET, cap_overruns: int = 0) -> dict:
    """The Argon D1 pre-registered test statistic (Nov 24, 2026: >= 92% valid paired CLV, zero cap overruns).

    DENOMINATOR (precise): rows of `rows` whose `result` is win / loss / push -- settled prop rows. Void,
    pending and ungradable rows are excluded. If `rows` has no `result` column, the input is assumed to be
    pre-filtered settled rows and EVERY row counts; the returned `denominator` string says which rule ran.
    NUMERATOR: denominator rows with clv_cons_pct present and clv_missing_reason empty."""
    if rows is None:
        rows = pd.DataFrame()
    if "result" in rows.columns:
        settled = rows[rows["result"].astype(str).str.lower().isin(SETTLED_RESULTS)]
        denom_note = ("settled prop rows: input rows with result in {win, loss, push} "
                      "(void / pending / ungradable excluded)")
    else:
        settled = rows
        denom_note = "settled prop rows: ALL input rows (input has no result column; assumed pre-filtered)"
    n = int(len(settled))
    if n and "clv_cons_pct" in settled.columns:
        valid_mask = settled["clv_cons_pct"].notna() & settled.get("clv_missing_reason").isna() \
            if "clv_missing_reason" in settled.columns else settled["clv_cons_pct"].notna()
        valid = settled[valid_mask]
    else:
        valid = settled.iloc[0:0]
    nv = int(len(valid))
    reasons: dict = {}
    if n and "clv_missing_reason" in settled.columns:
        for r in settled.loc[~settled.index.isin(valid.index), "clv_missing_reason"]:
            k = r if isinstance(r, str) and r else "missing_unknown"
            reasons[k] = reasons.get(k, 0) + 1
    by_sport: dict = {}
    col = "sport" if "sport" in settled.columns else ("sport_key" if "sport_key" in settled.columns else None)
    if col:
        for sname, g in settled.groupby(col):
            gv = valid[valid[col] == sname] if nv else g.iloc[0:0]
            by_sport[str(sname)] = {"settled": int(len(g)), "valid_clv": int(len(gv)),
                                    "share": (len(gv) / len(g)) if len(g) else None}
    share = (nv / n) if n else None
    return {"denominator": denom_note, "n_settled": n, "n_valid_clv": nv,
            "share_valid_clv": share, "target": target,
            "passes_target": bool(share is not None and share >= target and int(cap_overruns) == 0),
            "cap_overruns": int(cap_overruns), "zero_cap_overruns": int(cap_overruns) == 0,
            "missing_reasons": reasons, "by_sport": by_sport,
            "mean_clv_cons_pct": (float(valid["clv_cons_pct"].mean()) if nv else None)}


# ----------------------------------------------------------------------------------------------- T-15 capture
def closing_ledger_key(sport: str, month: str) -> str:
    return f"{ODDSACCUM_PREFIX}/{sport}/_credits_closing/{month}.json"


def closing_spent(sport: str, month: str) -> int:
    """Credits the closing job has spent this month (its carve-out sub-ledger). Unreadable -> raise:
    the caller fails closed, exactly like the shared ledger."""
    from mlb_core import storage
    key = closing_ledger_key(sport, month)
    if not storage.exists(key):
        return 0
    d = json.loads(storage.read_bytes(key))
    return int(d.get("credits", 0)) if d.get("month") == month else 0


def _record_closing_spent(sport: str, month: str, n: int) -> None:
    if n <= 0:
        return
    try:
        from mlb_core import storage
        total = closing_spent(sport, month) + int(n)
        storage.write_bytes(json.dumps({"month": month, "credits": total}).encode(),
                            closing_ledger_key(sport, month))
    except Exception as e:                                                                  # noqa: BLE001
        log.error("closing sub-ledger write FAILED for %s %s (+%s credits not recorded): %r", sport, month, n, e)


def imminent_events(slate: list, now: datetime, window_min: float = CLV_CLOSING_CAPTURE_WINDOW_MIN) -> list:
    """Events starting in (now, now + window_min], soonest first -- the T-15 capture set. Events already
    started or with no parseable commence are excluded (fail closed: never buy a started game)."""
    out = []
    for ev in slate or []:
        st = _utc((ev or {}).get("commence_time"))
        if st is None:
            continue
        mins = (st - now).total_seconds() / 60.0
        if 0.0 < mins <= window_min:
            out.append((mins, ev))
    out.sort(key=lambda t: t[0])
    return [ev for _m, ev in out]


def capture_closing(sport: str, client=None, guard: bool = True, now: datetime | None = None,
                    window_min: float = CLV_CLOSING_CAPTURE_WINDOW_MIN, max_credits: int = 400,
                    tier: str = "core") -> dict:
    """One T-15 closing-capture firing for one accumulator sport. Spends ONLY through the existing guards:
    nba.odds.credit_ledger allowance/record against the sport's UNCHANGED accumulator cap, the shared-pool
    reserve floor, plus this job's carve-out sub-cap (CLV_CLOSING_MONTHLY_BUDGET). Banks the same OddsAccum
    raw + best-book CSV shapes the accumulator writes, so every existing consumer (forward evaluation,
    lines tables, this module's series builder) reads the closing snapshots with no new plumbing."""
    from mlb_core import storage
    from nba.config import DFS_BOOKS
    from nba.odds import credit_ledger, extract, parlay_extract
    from nba.odds.accumulator import _credits_of, _month
    from nba.odds.parlayapi import ParlayApiClient

    client = client or ParlayApiClient()
    now = now or datetime.now(timezone.utc)
    now_et = now.astimezone(ET)
    date, hhmm = now_et.date().isoformat(), now_et.strftime("%H%M")
    today = now_et.date()
    month = _month(today)
    markets = list(PARLAY_PROP_MARKETS.get(sport, ["player_points"]))
    if tier == "full":
        markets += PARLAY_PROP_MARKETS_ALT.get(sport, [])
    result = {"sport": sport, "kind": "props_closing", "date": date, "hhmm": hhmm, "credits_spent": 0,
              "target_lead_min": CLV_CLOSING_TARGET_LEAD_MIN, "window_min": window_min}
    carve_cap = int(CLV_CLOSING_MONTHLY_BUDGET.get(sport, 0))
    if guard:
        if carve_cap <= 0:
            return {**result, "skipped": True, "reason": f"no closing carve-out configured for {sport}"}
        try:
            spent_close = closing_spent(sport, month)
        except Exception as e:                                                              # noqa: BLE001
            return {**result, "skipped": True, "reason": f"closing sub-ledger unreadable ({e!r}); failing closed"}
        if spent_close >= carve_cap:
            return {**result, "skipped": True,
                    "reason": f"closing carve-out exhausted: {spent_close} >= {carve_cap} for {month}"}
        a0 = credit_ledger.allowance(sport, 1, today, client.credits_remaining)
        if not a0.ok:
            return {**result, "skipped": True, "reason": a0.reason}
    slate = client.get_slate(sport)
    spent = _credits_of(client, 1)
    if guard:
        credit_ledger.record(sport, month, spent)
        _record_closing_spent(sport, month, spent)
    events = imminent_events(slate, now, window_min)
    result["events_imminent"] = len(events)
    if not events:
        result.update(events=0, events_priced=0, credits_spent=spent)
        log.info("closing capture: %s", result)
        return result
    if guard:
        want = len(events) * len(markets)
        a = credit_ledger.allowance(sport, want, today, client.credits_remaining, run_cap=max_credits)
        carve_room = carve_cap - closing_spent(sport, month)
        granted = (a.credits // len(markets)) if a.ok else 0
        granted = min(granted, carve_room // len(markets))
        if granted < len(events):
            log.warning("closing capture: budget buys %d of %d imminent events (%s; carve room %d)",
                        granted, len(events), a.reason, carve_room)
        events = events[:max(granted, 0)]
    raw_objs, all_rows, event_spend = [], [], 0
    try:
        for ev in events:
            eid = ev.get("id")
            if not eid:
                continue
            obj = client.get_event_props(sport, eid, markets)
            event_spend += _credits_of(client, len(markets))
            if not obj:
                continue
            raw_objs.append(obj)
            all_rows.extend(parlay_extract.flatten_parlay_props(obj, sport, include_one_sided=True))
    finally:
        if guard:
            credit_ledger.record(sport, month, event_spend)
            _record_closing_spent(sport, month, event_spend)
        spent += event_spend
    if raw_objs:
        storage.write_bytes(json.dumps(raw_objs).encode(), oddsaccum_raw_key(sport, date, "props", hhmm))
        real_rows = [r for r in all_rows if r["book"] not in DFS_BOOKS]
        best = extract.best_book_props(real_rows)
        if best:
            storage.write_csv(pd.DataFrame(best), oddsaccum_csv_key(sport, date, "props", hhmm))
        payload = {**result, "events": len(events), "events_priced": len(raw_objs),
                   "prop_rows": len(all_rows), "credits_spent": spent,
                   "credits_remaining": client.credits_remaining, "kind": "props",
                   "timestamp": datetime.now(timezone.utc).isoformat()}
        storage.write_bytes(json.dumps(payload, default=str).encode(), oddsaccum_latest_kind_key(sport, "props"))
    result.update(events=len(events), events_priced=len(raw_objs), prop_rows=len(all_rows),
                  credits_spent=spent, credits_remaining=client.credits_remaining)
    log.info("closing capture: %s", result)
    return result


# ----------------------------------------------------------------------------------------------- CLI
def _dates(start: str, end: str) -> list[str]:
    d0, d1 = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _load_resolver(sport_short: str):
    """NBA settled rows carry resolver ids as player_norm (nba.props.score convention); without the resolver
    the closing keys will not line up, so try to load it and fall back to normalised names with a warning."""
    if sport_short != "nba":
        return None, None
    try:
        from ev.sweep_props_alerts import SPORTS, load_resolver
        from nba.teams import team_code
        resolver, _ = load_resolver(SPORTS["nba"])
        return resolver, team_code
    except Exception as e:                                                                  # noqa: BLE001
        log.warning("NBA resolver unavailable (%r); closing keys use normalised names", e)
        return None, None


def attach_from_bank(settled: pd.DataFrame, sport_key: str, start: str, end: str) -> tuple[pd.DataFrame, dict]:
    """Settled rows + the banked OddsAccum snapshots for [start, end] (ET dates) -> (rows with CLV, report)."""
    resolver, team_fn = _load_resolver(SPORT_SHORT.get(sport_key, "nfl"))
    series, report = build_series(sport_key, _dates(start, end), resolver=resolver, team_fn=team_fn)
    closing = closing_table(series)
    out = attach_clv(settled, closing, event_close_index(series))
    return out, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prop CLV banker (Argon Data D1): T-15 closing capture, "
                                             "CLV attach on settled rows, D1 coverage report.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="one T-15 closing-snapshot firing for one sport (guarded spend)")
    c.add_argument("--sport", default="basketball_nba")
    c.add_argument("--window-min", type=float, default=CLV_CLOSING_CAPTURE_WINDOW_MIN)
    c.add_argument("--max-credits", type=int, default=400, help="per-run credit cap (shared ledger guard)")
    c.add_argument("--tier", choices=["core", "full"], default="core")
    c.add_argument("--no-guard", action="store_true", help="bypass the credit guards (manual probes only)")
    for name in ("attach", "report"):
        p = sub.add_parser(name, help=f"{name} CLV over banked snapshots for a settled-rows CSV")
        p.add_argument("--sport", default="basketball_nba", help="ParlayAPI sport key of the snapshot bank")
        p.add_argument("--settled-csv", required=True)
        p.add_argument("--start", required=True, help="first ET date of banked snapshots, YYYY-MM-DD")
        p.add_argument("--end", required=True, help="last ET date of banked snapshots, YYYY-MM-DD")
        if name == "attach":
            p.add_argument("--out", default=None, help="write the enriched CSV here")
        else:
            p.add_argument("--cap-overruns", type=int, default=0,
                           help="credit-cap overruns in the measured window (D1 pass requires 0)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "capture":
        print(json.dumps(capture_closing(a.sport, guard=not a.no_guard, window_min=a.window_min,
                                         max_credits=a.max_credits, tier=a.tier), indent=1, default=str))
        return 0
    settled = pd.read_csv(a.settled_csv)
    out, report = attach_from_bank(settled, a.sport, a.start, a.end)
    print(json.dumps({"bank_report": report}, indent=1, default=str))
    if a.cmd == "attach":
        if a.out:
            out.to_csv(a.out, index=False)
            print("wrote", a.out)
        else:
            print(out.to_string())
        return 0
    print(json.dumps(coverage_report(out, cap_overruns=a.cap_overruns), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
