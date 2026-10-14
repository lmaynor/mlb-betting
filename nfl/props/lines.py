"""nfl.props.lines -- the banked NFL prop MARKET-LINES table (raw ParlayAPI payloads -> per-prop market history).

There is no historical NFL prop-odds dataset; the only market lines this repo can ever judge a model against are
the raw ParlayAPI payloads the shadow sweep (`ev/sweep_nfl_props.py`) has been banking since 2026-10-04 at
`EVSweep/nfl_props/raw/{ET day}/{event_id}_{HHMMSS}.json`. This module turns them into

  * a SERIES table  -- one row per (snapshot, game, player, stat, point): per-book de-vigged P(over) summarised as the
                       consensus (`ev.props.build_consensus`, reused verbatim: median over real two-sided sportsbooks,
                       DFS books excluded, one-sided quotes counted not used), n_books, dispersion;
  * a BOOKS table   -- one row per (snapshot, quote): every book's own over/under price, hold and de-vigged P(over);
  * a collapse      -- per-prop OPENING (first snapshot) and CLOSING PROXY (last PRE-KICK snapshot) + the move.

Facts that shape the design (verified on the real bank, 2026-10-14):
  * File name = `{ParlayAPI event id}_{HHMMSS}`; HHMMSS is the UTC wall time the sweep CYCLE started, but the folder is
    the EASTERN day. Both are needed to recover the full UTC timestamp (`snapshot_ts`). A cycle fetches its events one
    after another, so the stamp can precede the actual fetch of a later event by tens of seconds; each book's own
    `last_update` (carried as `age_*_min`) is the real freshness of its quote.
  * The sweep only fetches games that start more than `close_buffer_min` (20) minutes in the future, so the LAST
    snapshot of a game is >= ~20 min before kick -- a closing PROXY, not the true close. When the sweep was not running
    near kick the "last pre-kick snapshot" may be hours old: `close_lead_min` says how stale, and consumers must gate
    on it (`CLOSE_FRESH_MIN`).
  * PRE-KICK ONLY: any snapshot at/after its payload's `commence_time`, or whose commence time is not marked reported,
    is dropped (in-play contamination was this repo's worst past bug).
  * `player_norm` is `ev.props.norm_name` of the book's description; there is no roster resolver here, so two players
    with the same normalized name in ONE game would merge (not seen in the bank).
  * Props are keyed on the EXACT point. If a book moves its main line (63.5 -> 64.5) the old point stops being quoted:
    that prop then has no true close (`alive_at_close` False). `player_stat_moves` compares the MAIN line instead.

Everything here is pure and offline except the thin storage adapters (`storage_source`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ev.props import (DFS_BOOKS, POOL_MAX_GAP, book_points, build_consensus, parse_event_props, pooled_consensus,
                      stat_of)
from ev.sports.nfl import TEAM_NAMES
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way
from nfl.data.keys import make_game_key

PREFIX = "EVSweep/nfl_props"
RAW_PREFIX = f"{PREFIX}/raw/"
ET = ZoneInfo("America/New_York")
# A "closing proxy" older than this many minutes before kick is NOT a usable closing line (the sweep stops fetching a
# game inside its last 20 min; anything staler means the sweep was not running near kick).
CLOSE_FRESH_MIN = 60.0

_RAW_RE = re.compile(r"(?:^|/)raw/(\d{4}-\d{2}-\d{2})/([0-9A-Za-z]+)_(\d{6})\.json$")


# ----------------------------------------------------------------------------------------------- raw object keys
@dataclass(frozen=True)
class RawRef:
    key: str
    et_day: str
    event_id: str
    ts: datetime            # UTC snapshot timestamp (cycle start)


def snapshot_ts(et_day: str, hhmmss: str) -> Optional[datetime]:
    """(ET folder day, UTC HHMMSS) -> aware UTC datetime. The sweep names the folder with the ET day of `now` and the
    file with now's UTC clock, so exactly one of {day, day+1} UTC makes the ET date equal the folder (EDT/EST both)."""
    try:
        d = datetime.fromisoformat(et_day).date()
        h, m, s = int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
    except (ValueError, TypeError):
        return None
    for add in (0, 1):
        try:
            cand = datetime(d.year, d.month, d.day, h, m, s, tzinfo=timezone.utc) + timedelta(days=add)
        except ValueError:
            return None
        if cand.astimezone(ET).date() == d:
            return cand
    return None


def parse_raw_key(key: str) -> Optional[RawRef]:
    m = _RAW_RE.search(key)
    if not m:
        return None
    day, eid, hms = m.groups()
    ts = snapshot_ts(day, hms)
    return RawRef(key, day, eid, ts) if ts is not None else None


def list_raw_refs(lister: Callable[[str], list], start: str | None = None, end: str | None = None,
                  prefix: str = RAW_PREFIX) -> list[RawRef]:
    """Raw payload keys under `prefix` -> RawRefs sorted by (ts, event). start/end are inclusive ET days (YYYY-MM-DD)."""
    refs = [r for r in (parse_raw_key(k) for k in lister(prefix)) if r is not None]
    if start:
        refs = [r for r in refs if r.et_day >= start]
    if end:
        refs = [r for r in refs if r.et_day <= end]
    return sorted(refs, key=lambda r: (r.ts, r.event_id))


def storage_source():
    """(lister, reader) over mlb_core.storage (GCS when GCS_BUCKET is set, else local MLB_BASE_DATA)."""
    from mlb_core import storage
    return storage.list_keys, storage.read_bytes


# ----------------------------------------------------------------------------------------------- one snapshot
@dataclass
class Snapshot:
    event_id: str
    ts: datetime                      # UTC
    commence: datetime                # UTC (from the payload at this snapshot)
    away: str                         # ev/Kalshi team codes
    home: str
    quotes: list
    ages: dict = field(default_factory=dict)      # (book, stat) -> minutes between the book's last_update and ts
    commence_reported: bool = True
    meta: dict = field(default_factory=dict)

    @property
    def mins_to_kick(self) -> float:
        return (self.commence - self.ts).total_seconds() / 60.0

    @property
    def date_et(self) -> str:
        return self.commence.astimezone(ET).date().isoformat()

    @property
    def game_key(self) -> Optional[str]:
        return make_game_key(self.date_et, self.away, self.home)


def _utc(s) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def snapshot_from_payload(payload, ref: RawRef) -> tuple[Optional[Snapshot], str]:
    """Raw ParlayAPI single-event object -> (Snapshot | None, reason). Reasons: 'ok', 'bad_payload', 'no_commence',
    'unknown_teams'. Parsing is ev.props.parse_event_props (no re-derivation)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bookmakers"), list):
        return None, "bad_payload"
    commence = _utc(payload.get("commence_time"))
    if commence is None:
        return None, "no_commence"
    away, home = TEAM_NAMES.get(payload.get("away_team")), TEAM_NAMES.get(payload.get("home_team"))
    if not away or not home:
        return None, "unknown_teams"
    quotes, pmeta = parse_event_props(payload)
    ages: dict = {}
    seen: dict = {}
    dup = 0
    for b in payload["bookmakers"]:
        bkey = b.get("key", "")
        for m in b.get("markets", []) or []:
            stat, _alt = stat_of(m.get("key", ""))
            lu = _utc(m.get("last_update")) or _utc(b.get("last_update"))
            if stat is not None and lu is not None:
                age = (ref.ts - lu).total_seconds() / 60.0
                k = (bkey, stat)
                ages[k] = min(ages[k], age) if k in ages else age        # freshest market of that stat
    for q in quotes:
        k = (q.book, q.stat, q.player_norm, q.point)
        dup += k in seen
        seen[k] = True
    meta = {"unmapped_keys": pmeta["unmapped_keys"], "dup_quotes": dup}
    return Snapshot(ref.event_id, ref.ts, commence, away, home, quotes, ages,
                    commence_reported=payload.get("commence_time_reported", True) is not False, meta=meta), "ok"


def is_pre_kick(snap: Snapshot, min_lead_min: float = 0.0) -> bool:
    """True iff the snapshot was taken strictly BEFORE kickoff (by `min_lead_min` or more) and the commence time is a
    reported one. Fail closed on anything else."""
    return snap.commence_reported and snap.mins_to_kick > min_lead_min


def iter_snapshots(refs: Iterable[RawRef], reader: Callable[[str], bytes], report: dict | None = None):
    """Yield Snapshots in `refs` order; unreadable / malformed payloads are counted in `report` and skipped."""
    rep = report if report is not None else {}
    for r in refs:
        try:
            payload = json.loads(reader(r.key))
        except Exception:                                                                    # noqa: BLE001
            rep["unreadable"] = rep.get("unreadable", 0) + 1
            continue
        snap, why = snapshot_from_payload(payload, r)
        if snap is None:
            rep[why] = rep.get(why, 0) + 1
            continue
        for k, v in snap.meta["unmapped_keys"].items():
            um = rep.setdefault("unmapped_keys", {})
            um[k] = um.get(k, 0) + v
        rep["dup_quotes"] = rep.get("dup_quotes", 0) + snap.meta["dup_quotes"]
        yield snap


# ----------------------------------------------------------------------------------------------- per-snapshot tables
def _json(d) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def snapshot_tables(snap: Snapshot, game_key: str | None = None, pooled: bool = True):
    """One Snapshot -> (series rows, book rows). Consensus is ev.props.build_consensus (unchanged)."""
    gk = game_key or snap.game_key
    cons = build_consensus(snap.quotes)
    pool_entries = book_points(snap.quotes) if pooled else {}
    base = {"snap_ts": snap.ts, "event_id": snap.event_id, "game_key": gk, "date_et": snap.date_et,
            "away": snap.away, "home": snap.home, "commence": snap.commence, "mins_to_kick": round(snap.mins_to_kick, 2)}
    names: dict = {}
    for q in snap.quotes:
        names.setdefault(q.player_norm, q.player)
    series, books = [], []
    for (pn, stat, point), c in cons.items():
        ages = [snap.ages[(b, stat)] for b in c.books if (b, stat) in snap.ages]
        fair = [v for _, v in c.book_fair]
        row = {**base, "player": names.get(pn, pn), "player_norm": pn, "stat": stat, "point": point,
               "p_over": c.p_over, "n_books": c.n_books, "disp": c.disp, "p_min": min(fair), "p_max": max(fair),
               "books": ",".join(c.books), "n_onesided": c.n_onesided, "n_dfs": c.n_dfs,
               "team_hint": ",".join(c.team_hints) or None, "book_fair": _json(dict(c.book_fair)),
               "age_med_min": round(float(np.median(ages)), 2) if ages else None,
               "age_max_min": round(max(ages), 2) if ages else None}
        if pooled:                                    # EVIDENCE ONLY (ev.props.pooled_consensus; yardage stats only)
            pc = pooled_consensus(pool_entries.get((pn, stat), []), stat, point) if stat in POOL_MAX_GAP else None
            row.update(pool_p_over=None if pc is None else pc["p_over"],
                       pool_n_books=None if pc is None else pc["n_books"],
                       pool_n_exact=None if pc is None else pc["n_exact"])
        series.append(row)
    uniq = {}
    for q in snap.quotes:                       # a book can list one stat under two market-key aliases (fanduel,
        uniq[(q.book, q.stat, q.player_norm, q.point)] = q          # draftkings): last wins, exactly as in build_consensus
    for q in uniq.values():
        two = q.two_sided
        pov, pun = (american_to_implied_prob(q.over) if q.over is not None else None,
                    american_to_implied_prob(q.under) if q.under is not None else None)
        fair = float(devig_two_way(pov, pun, "proportional")[0]) if two else None
        role = "dfs" if q.book in DFS_BOOKS else ("consensus" if two else "onesided")
        books.append({**{k: base[k] for k in ("snap_ts", "event_id", "game_key")}, "player": q.player,
                      "player_norm": q.player_norm, "stat": q.stat, "point": q.point, "book": q.book,
                      "over": q.over, "under": q.under, "hold": (pov + pun - 1.0) if two else None,
                      "fair_over": fair if role == "consensus" else None, "role": role,
                      "age_min": None if (q.book, q.stat) not in snap.ages else round(snap.ages[(q.book, q.stat)], 2)})
    return series, books


def build_lines(snaps: Iterable[Snapshot], min_lead_min: float = 0.0, pooled: bool = True,
                report: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Snapshots -> (series, books) DataFrames, PRE-KICK ONLY. Each ParlayAPI event gets ONE game_key, taken from its
    latest snapshot's commence (a flexed game keeps one key across its history). Duplicate (event, ts) snapshots keep
    the last one. Adds `is_game_last` (the event's final pre-kick snapshot) to the series."""
    rep = report if report is not None else {}
    snaps = list(snaps)
    rep["snapshots_parsed"] = len(snaps)
    latest: dict = {}
    for s in snaps:
        if s.event_id not in latest or s.ts >= latest[s.event_id].ts:
            latest[s.event_id] = s
    gkeys = {e: s.game_key for e, s in latest.items()}
    seen_gk: dict = {}
    for s in snaps:
        seen_gk.setdefault(s.event_id, set()).add(s.game_key)
    rep["events_with_changed_game_key"] = sorted(e for e, v in seen_gk.items() if len(v) > 1)
    keep: dict = {}
    for s in snaps:
        if not s.commence_reported:
            rep["dropped_commence_unreported"] = rep.get("dropped_commence_unreported", 0) + 1
            continue
        if not is_pre_kick(s, min_lead_min):
            rep["dropped_not_pre_kick"] = rep.get("dropped_not_pre_kick", 0) + 1
            continue
        if gkeys.get(s.event_id) is None:
            rep["dropped_no_game_key"] = rep.get("dropped_no_game_key", 0) + 1
            continue
        if (s.event_id, s.ts) in keep:
            rep["dropped_duplicate_snapshot"] = rep.get("dropped_duplicate_snapshot", 0) + 1
        keep[(s.event_id, s.ts)] = s
    rep["snapshots_kept"] = len(keep)
    srows, brows = [], []
    for (_, _), s in sorted(keep.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        a, b = snapshot_tables(s, gkeys[s.event_id], pooled)
        srows += a
        brows += b
    series, bk = pd.DataFrame(srows), pd.DataFrame(brows)
    if not series.empty:
        last = series.groupby("event_id")["snap_ts"].transform("max")
        series["is_game_last"] = series["snap_ts"] == last
    return series, bk


# ----------------------------------------------------------------------------------------------- collapse
PROP_KEY = ["game_key", "player_norm", "stat", "point"]


def _pick(g: pd.DataFrame, i: int, tag: str) -> dict:
    r = g.iloc[i]
    return {f"{tag}_ts": r["snap_ts"], f"{tag}_p_over": r["p_over"], f"{tag}_n_books": int(r["n_books"]),
            f"{tag}_disp": r["disp"], f"{tag}_lead_min": r["mins_to_kick"], f"{tag}_books": r["books"],
            f"{tag}_book_fair": r["book_fair"]}


def collapse_props(series: pd.DataFrame, min_books: int = 1) -> pd.DataFrame:
    """Per (game_key, player_norm, stat, point): OPENING (first snapshot where >= min_books two-sided books quote it)
    and CLOSING PROXY (last such PRE-KICK snapshot), the move, and `alive_at_close` = the prop is still quoted at the
    game's final pre-kick snapshot (False -> the line moved off this point; no true close exists for it).
    `close_fresh` = close_lead_min <= CLOSE_FRESH_MIN. Rows are NOT dropped for a stale close; filter on the flag."""
    if series.empty:
        return pd.DataFrame()
    s = series.sort_values("snap_ts", kind="stable")
    game_last = s.groupby("event_id")["snap_ts"].max()
    out = []
    for key, g_all in s.groupby(PROP_KEY, sort=False):
        g = g_all[g_all["n_books"] >= min_books]
        if g.empty:
            continue
        o, c = _pick(g, 0, "open"), _pick(g, len(g) - 1, "close")
        last_ts = game_last[g_all["event_id"].iloc[0]]
        first = g_all.iloc[0]
        # Like-for-like move: the median over ONLY the books quoting both ends, so a book joining/leaving the
        # consensus (a composition change) is not read as the market moving.
        fo, fc = json.loads(o["open_book_fair"]), json.loads(c["close_book_fair"])
        common = sorted(set(fo) & set(fc))
        move_common = (float(np.median([fc[b] for b in common]) - np.median([fo[b] for b in common]))
                       if common else None)
        out.append({**dict(zip(PROP_KEY, key)), "event_id": first["event_id"], "player": first["player"],
                    "team_hint": first["team_hint"], "away": first["away"], "home": first["home"],
                    "commence": first["commence"], "n_snaps": len(g), **o, **c,
                    "move": c["close_p_over"] - o["open_p_over"], "n_common": len(common),
                    "move_common": move_common,
                    "alive_at_close": bool(g_all["snap_ts"].iloc[-1] == last_ts),
                    "close_fresh": bool(c["close_lead_min"] <= CLOSE_FRESH_MIN),
                    "span_min": (c["close_ts"] - o["open_ts"]).total_seconds() / 60.0})
    return pd.DataFrame(out)


def main_lines(series: pd.DataFrame) -> pd.DataFrame:
    """Per (snapshot, game, player, stat): the MAIN point = the point with the most books (ties -> p_over closest to
    0.5). Used to measure line movement independent of any single exact point."""
    if series.empty:
        return pd.DataFrame()
    s = series.assign(_d=(series["p_over"] - 0.5).abs())
    s = s.sort_values(["n_books", "_d"], ascending=[False, True], kind="stable")
    keys = ["snap_ts", "event_id", "game_key", "player_norm", "stat"]
    return s.drop_duplicates(keys).drop(columns="_d")[keys + ["player", "point", "p_over", "n_books", "mins_to_kick"]]


def player_stat_moves(series: pd.DataFrame, min_books: int = 1) -> pd.DataFrame:
    """Per (game, player, stat): main point and its consensus P(over) at the first and last pre-kick snapshot where the
    player has a main line with >= min_books books, `point_move` (close - open, stat units) and `line_changed`."""
    ml = main_lines(series)
    ml = ml[ml["n_books"] >= min_books].sort_values("snap_ts", kind="stable")
    if ml.empty:
        return pd.DataFrame()
    g = ml.groupby(["game_key", "player_norm", "stat"], sort=False)
    o, c = g.head(1).set_index(["game_key", "player_norm", "stat"]), g.tail(1).set_index(["game_key", "player_norm", "stat"])
    out = pd.DataFrame({"player": o["player"], "open_ts": o["snap_ts"], "open_point": o["point"],
                        "open_p_over": o["p_over"], "open_n_books": o["n_books"], "open_lead_min": o["mins_to_kick"],
                        "close_ts": c["snap_ts"], "close_point": c["point"], "close_p_over": c["p_over"],
                        "close_n_books": c["n_books"], "close_lead_min": c["mins_to_kick"]})
    out["point_move"] = out["close_point"] - out["open_point"]
    out["line_changed"] = out["point_move"] != 0
    out["n_snaps"] = g.size()
    return out.reset_index()


# ----------------------------------------------------------------------------------------------- snapshot quality
def snapshot_quality(series: pd.DataFrame, books: pd.DataFrame) -> pd.DataFrame:
    """One row per snapshot: how COMPLETE that payload was (props priced, real two-sided books, >=3-book props).
    The bank's 2026-10-04 payloads are rotating partial views (books/markets appear and vanish between consecutive
    calls: DAL@HOU priced 8-85 props per snapshot); 2026-10-12's are stable (~450-515). Filter on these before
    trusting a single snapshot as 'the market', or use `asof_series`."""
    if series.empty:
        return pd.DataFrame()
    g = series.groupby(["event_id", "game_key", "snap_ts"])
    q = g.agg(mins_to_kick=("mins_to_kick", "first"), n_props=("p_over", "size"),
              n_props_ge3=("n_books", lambda x: int((x >= 3).sum()))).reset_index()
    real = books[books["role"] == "consensus"].groupby(["event_id", "snap_ts"])["book"].nunique().rename("n_real_books")
    q = q.merge(real.reset_index(), on=["event_id", "snap_ts"], how="left")
    q["n_real_books"] = q["n_real_books"].fillna(0).astype(int)
    peak = q.groupby("event_id")["n_props"].transform(lambda x: x.quantile(0.9))
    q["completeness"] = q["n_props"] / peak.clip(lower=1)
    return q


# ----------------------------------------------------------------------------------------------- as-of (carry-forward)
def asof_series(series: pd.DataFrame, books: pd.DataFrame, max_stale_min: float = 60.0) -> pd.DataFrame:
    """CARRY-FORWARD consensus. Because individual payloads are partial (books flap in and out between calls), the
    market at time t is better estimated by each book's LAST observed two-sided quote on the prop, if it is no older
    than `max_stale_min` at t -- not just the books that happened to be in snapshot t. Same per-book de-vig (the
    books table's `fair_over`) and the same median / MAD as ev.props.build_consensus; DFS and one-sided quotes are
    excluded exactly as there. A quote's time is snapshot time minus the book's own `last_update` age when known.
    Emitted at every snapshot time of the event for every prop with >= 1 live quote, in the SERIES schema (so
    `collapse_props` applies unchanged) plus `n_fresh` = books actually present in snapshot t.
    PRE-KICK only by construction (the books table is). NOT pooling across points: one exact point only."""
    if series.empty:
        return pd.DataFrame()
    cons = books[books["role"] == "consensus"].copy()
    qt = cons["snap_ts"] - pd.to_timedelta(cons["age_min"].where(cons["age_min"] >= 0, 0).fillna(0), unit="min")
    cons["qts"] = qt
    ev = (series.sort_values("snap_ts").groupby("event_id")
          .agg(game_key=("game_key", "last"), date_et=("date_et", "last"), away=("away", "last"),
               home=("home", "last"), commence=("commence", "last")))
    hint = series.dropna(subset=["team_hint"]).drop_duplicates(["game_key", "player_norm"]).set_index(
        ["game_key", "player_norm"])["team_hint"].to_dict()
    names = series.drop_duplicates(["game_key", "player_norm"]).set_index(["game_key", "player_norm"])["player"].to_dict()
    snap_times = series.groupby("event_id")["snap_ts"].apply(lambda x: sorted(x.unique()))
    stale = pd.Timedelta(minutes=max_stale_min)
    out = []
    for eid, ts_list in snap_times.items():
        cb = cons[cons["event_id"] == eid].sort_values("snap_ts")
        by_snap = {t: g for t, g in cb.groupby("snap_ts")}
        last: dict = {}                      # (pn, stat, point) -> {book: (qts, fair, seen_snap_ts)}
        info = ev.loc[eid]
        for t in ts_list:
            g = by_snap.get(t)
            if g is not None:
                for pn, st, pt, bk, f, qts in zip(g["player_norm"], g["stat"], g["point"], g["book"], g["fair_over"],
                                                  g["qts"]):
                    last.setdefault((pn, st, pt), {})[bk] = (qts, float(f), t)
            mins = (info["commence"] - t).total_seconds() / 60.0
            for (pn, st, pt), bd in last.items():
                live = {b: v for b, v in bd.items() if t - v[0] <= stale}
                if not live:
                    continue
                vals = [v[1] for v in live.values()]
                out.append({"snap_ts": t, "event_id": eid, "game_key": info["game_key"], "date_et": info["date_et"],
                            "away": info["away"], "home": info["home"], "commence": info["commence"],
                            "mins_to_kick": round(mins, 2), "player": names.get((info["game_key"], pn), pn),
                            "player_norm": pn, "stat": st, "point": pt, "p_over": float(np.median(vals)),
                            "n_books": len(vals), "disp": float(np.median(np.abs(np.array(vals) - np.median(vals)))),
                            "books": ",".join(sorted(live)), "team_hint": hint.get((info["game_key"], pn)),
                            "book_fair": _json({b: v[1] for b, v in live.items()}),
                            "n_fresh": sum(1 for v in live.values() if v[2] == t)})
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------------------------- one-call driver
def build_from_source(lister, reader, start: str | None = None, end: str | None = None, min_lead_min: float = 0.0,
                      pooled: bool = True):
    """list -> read -> parse -> pre-kick tables. Returns (series, books, report)."""
    report: dict = {}
    refs = list_raw_refs(lister, start, end)
    report["raw_refs"] = len(refs)
    series, books = build_lines(iter_snapshots(refs, reader, report), min_lead_min, pooled, report)
    return series, books, report
