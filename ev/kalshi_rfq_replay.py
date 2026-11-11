"""
ev.kalshi_rfq_replay -- point-in-time replay of REAL settled Kalshi combos through the RFQ pricer.

Question it answers: are retail sports combos priced richly enough, against our banked sportsbook consensus, to lay after fees?
Inputs are public (Kalshi's unauthenticated /markets lists every combo with its legs, last price, volume and result) plus the
sweep snapshots we already bank (EVSweep/nfl_props/{day}/cycle_*.csv, keyed by Kalshi ticker, ~30 min cadence on game days).

    combo created at t  ->  latest snapshot <= t and no older than max_age  ->  InMemoryFairBook  ->  KalshiRFQPricer.price(now=t)

No look-ahead: only a snapshot stamped BEFORE the combo's created_time is ever used. A combo market is created when its RFQ is
accepted, so created_time ~ trade time.

Two passes per combo:
  measure  permissive config (every combo with >= 1 priced leg gets p_true / p_cons; unpriced legs are dropped, which makes p an
           UPPER bound) -> ex-ante edge `yes_price - p_true` and the realized lay result at the traded price.
  quote    the config the daemon would run -> would it quote, at what no_bid, and would that bid have beaten the traded price.

Limits (read before trusting a number): `last_price` is the market's LAST trade, not necessarily the RFQ's price; combos share
legs and games, so rows are heavily clustered (report per-day, do not read the pooled t-stat as independent); only NFL player
props are covered by the banked snapshots (everything else is an unpriced leg); the fee is the taker formula (maker schedule
undocumented); fills are assumed at OUR bid when it is >= the traded NO price, which ignores competing quotes and queueing.
"""

from __future__ import annotations

import bisect
import json
import math
import time
from dataclasses import replace
from datetime import datetime, timezone

from ev.kalshi_rfq_pricer import InMemoryFairBook, KalshiRFQPricer, PricerConfig, leg_from_row
from mlb.execution.kalshi.fees import taker_fee

API = "https://api.elections.kalshi.com/trade-api/v2"
SPORT_PREFIXES = ("KXNBA", "KXNHL", "KXNFL", "KXNCAAF", "KXNCAAM", "KXNCAAW", "KXMLB", "KXMLS", "KXSERIEA", "KXEPL", "KXUCL",
                  "KXLALIGA", "KXBUNDES")
KEEP = ("ticker", "close_time", "created_time", "volume_fp", "last_price_dollars", "result", "settlement_ts", "open_interest_fp")

# snapshots refresh every ~30 min, so the live 1500 s staleness gate would reject half of them
MAX_SNAPSHOT_AGE_SEC = 2400.0
MEASURE_CFG = PricerConfig(allow_same_game_combos=True, max_unpriced_legs=10 ** 6, longshot_max_p=1.0, min_collateral_roi=-1.0,
                           max_staleness_sec=MAX_SNAPSHOT_AGE_SEC, max_legs=10 ** 6, sgp_mc_draws=20_000)
QUOTE_CFG = PricerConfig(allow_same_game_combos=True, max_unpriced_legs=16, max_staleness_sec=MAX_SNAPSHOT_AGE_SEC,
                         sgp_mc_draws=20_000)


def epoch(x) -> float:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


# ------------------------------------------------------------------------------------------ combos (public API)
def is_all_sports(legs) -> bool:
    return bool(legs) and all(str(l["market_ticker"]).startswith(SPORT_PREFIXES) for l in legs)


def pull_combos(start: datetime, end: datetime, *, window_hours: int = 4, max_pages: int = 8, pause: float = 0.4,
                get=None, sleep=time.sleep):
    """Yield one dict per settled ALL-sports combo market that closed in [start, end). Each window is capped at `max_pages` x 1000
    markets (the API returns crypto/FX 15-minute combos too, which are filtered out), so a busy window is a SAMPLE: the
    yielded `_window` records say how many markets were scanned and whether the cap truncated it."""
    import requests
    from datetime import timedelta

    def _get(params):
        for i in range(8):
            r = requests.get(API + "/markets", params=params, timeout=40)
            if r.status_code == 200:
                return r.json()
            sleep(1.5 * (i + 1))
        raise RuntimeError(f"Kalshi /markets kept failing ({r.status_code})")

    get = get or _get
    lo = start
    while lo < end:
        hi = min(lo + timedelta(hours=window_hours), end)
        cur, scanned, pages = None, 0, 0
        while pages < max_pages:
            params = {"mve_filter": "only", "status": "settled", "limit": 1000,
                      "min_close_ts": int(lo.timestamp()), "max_close_ts": int(hi.timestamp())}
            if cur:
                params["cursor"] = cur
            j = get(params)
            pages += 1
            for m in j.get("markets", []):
                scanned += 1
                legs = m.get("mve_selected_legs") or []
                if is_all_sports(legs):
                    rec = {k: m.get(k) for k in KEEP}
                    rec["legs"] = [[l["market_ticker"], l["side"]] for l in legs]
                    rec["win"] = lo.isoformat()
                    yield rec
            cur = j.get("cursor")
            sleep(pause)
            if not cur:
                break
        yield {"_window": lo.isoformat(), "scanned": scanned, "truncated": bool(cur)}
        lo = hi


def load_combos(path: str) -> tuple[list[dict], list[dict]]:
    """jsonl written by `pull_combos` -> (traded combos with a result, window records)."""
    combos, windows = [], []
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            if "_window" in r:
                windows.append(r)
            elif float(r.get("volume_fp") or 0) > 0 and r.get("result") in ("yes", "no"):
                combos.append(r)
    return combos, windows


# ------------------------------------------------------------------------------------------ snapshots (point in time)
def build_rung_index(df, ts: float, sport: str = "nfl"):
    """One sweep cycle -> the same PropFairIndex the live daemon resolves tickers with, so a leg whose exact rung was not swept can
    still be interpolated from the player's other rungs. Only EXACT-consensus rows with >= CURVE_MIN_BOOKS books anchor a curve."""
    from ev.kalshi_props import kalshi_player_codes
    from ev.kalshi_rfq_props import PropFairIndex, _PlayerStat
    idx = PropFairIndex(sport, ts)
    seen = set()
    for r in df.to_dict("records"):
        if r.get("consensus_src") != "exact" or not r.get("game_key") or not r.get("player_norm"):
            continue
        p, n = r.get("p_over"), int(r.get("exact_n_books") or r.get("n_books") or 0)
        if p is None or not (0.0 < float(p) < 1.0) or n < 1:
            continue
        game = idx.games.setdefault(r["game_key"], {"codes": {}, "data": {}})
        pn, team = r["player_norm"], str(r.get("team") or "")
        if (r["game_key"], pn) not in seen:
            seen.add((r["game_key"], pn))
            for code in kalshi_player_codes(pn, r.get("player") or pn):
                game["codes"].setdefault(code, []).append((pn, {team} if team else set()))
        ps = game["data"].setdefault((pn, r["stat"]), _PlayerStat())
        ps.rungs[float(r["strike"])] = (float(p), n, float(r.get("disp") or 0.0))
    for game in idx.games.values():
        for (pn, stat), ps in game["data"].items():
            ps.finalize(stat, sport)
    return idx


class SnapshotStore:
    """Time-ordered fair-value snapshots. `book_at(t)` never returns a snapshot stamped after t, and never prices a leg whose game
    had already kicked off by t (a pre-game consensus is not a fair value for a live parlay; the live index drops started games too)."""

    def __init__(self, snaps, sport: str = "nfl"):
        # snaps: (ts, legs) | (ts, legs, rung_index) | (ts, legs, rung_index, {game_key: commence_epoch})
        self.sport = sport
        norm = [(float(x[0]), list(x[1]), x[2] if len(x) > 2 else None, dict(x[3]) if len(x) > 3 and x[3] else {}) for x in snaps]
        self._snaps = sorted((s for s in norm if s[1] or s[2] is not None), key=lambda s: s[0])
        self._ts = [s[0] for s in self._snaps]
        self._books: dict = {}
        self.tickers = {leg.ticker for _, legs, _, _ in self._snaps for leg in legs}      # any snapshot: a cheap prefilter
        self.game_keys = ({leg.game_key for _, legs, _, _ in self._snaps for leg in legs}
                          | {g for _, _, idx, _ in self._snaps if idx is not None for g in idx.games})

    def __len__(self) -> int:
        return len(self._snaps)

    def may_price(self, legs) -> bool:
        """Cheap prefilter: some leg is a banked ticker, or a registered prop of a game some snapshot covers (a curve may resolve it)."""
        from ev.kalshi_props import ParsedPropTicker, parse_prop_ticker
        for t, _ in legs:
            if t in self.tickers:
                return True
            p = parse_prop_ticker(t)
            if isinstance(p, ParsedPropTicker) and p.game_key in self.game_keys:
                return True
        return False

    @classmethod
    def from_cycle_frames(cls, frames, curves: bool = True):
        """frames: pandas DataFrames of sweep `cycle_*.csv` (one cycle each; `ts` identical across a file)."""
        snaps = []
        for df in frames:
            if df is None or not len(df):
                continue
            t = epoch(df["ts"].iloc[0])
            legs = [lf for lf in (leg_from_row(r, t) for r in df.to_dict("records")) if lf is not None]
            commence = {}
            if "commence" in df.columns:
                for gk, c in zip(df["game_key"], df["commence"]):
                    if isinstance(gk, str) and isinstance(c, str):
                        commence.setdefault(gk, epoch(c))
            snaps.append((t, legs, build_rung_index(df, t) if curves else None, commence))
        return cls(snaps)

    def snapshot_index(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC):
        i = bisect.bisect_right(self._ts, t) - 1
        return None if i < 0 or t - self._ts[i] > max_age else i

    def started_games(self, t: float, i: int) -> frozenset:
        return frozenset(g for g, c in self._snaps[i][3].items() if c <= t)

    def has_snapshot(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> bool:
        return self.snapshot_index(t, max_age) is not None

    def live_games(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> frozenset:
        i = self.snapshot_index(t, max_age)
        return frozenset() if i is None else self.started_games(t, i)

    def parts(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC):
        """-> (ts, direct legs, rung index, live games) of the snapshot to price from, or None. Used by CompositeStore."""
        i = self.snapshot_index(t, max_age)
        if i is None:
            return None
        ts, legs, idx, _ = self._snaps[i]
        live = self.started_games(t, i)
        if live and idx is not None:
            from ev.kalshi_rfq_props import PropFairIndex
            view = PropFairIndex(idx.sport, idx.updated_ts)
            view.games = {g: v for g, v in idx.games.items() if g not in live}
            idx = view
        return ts, [l for l in legs if l.game_key not in live], idx, live

    def book_at(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC):
        i = self.snapshot_index(t, max_age)
        if i is None:
            return None
        live = self.started_games(t, i)
        key = (i, live)
        if key not in self._books:
            _, legs, idx, _ = self._snaps[i]
            book = InMemoryFairBook(clock=lambda: t)
            book.replace_all([l for l in legs if l.game_key not in live])
            if idx is not None:
                if live:
                    from ev.kalshi_rfq_props import PropFairIndex
                    view = PropFairIndex(idx.sport, idx.updated_ts)
                    view.games = {g: v for g, v in idx.games.items() if g not in live}
                    idx = view
                book.attach_prop_indices({self.sport: idx})      # direct rows still win; the curve/ladder fills the gaps
            self._books[key] = book
        return self._books[key]


class CompositeStore:
    """Several single-sport snapshot stores behind the SnapshotStore interface. Direct ticker rows from every store are merged; for each
    sport the FRESHEST snapshot's prop index wins (raw all-book payloads are a superset of the Kalshi-matched cycle rows)."""

    def __init__(self, stores):
        self.stores = list(stores)
        self._books: dict = {}

    def __len__(self) -> int:
        return sum(len(s) for s in self.stores)

    @property
    def tickers(self):
        return set().union(*(s.tickers for s in self.stores)) if self.stores else set()

    @property
    def game_keys(self):
        return set().union(*(s.game_keys for s in self.stores)) if self.stores else set()

    def may_price(self, legs) -> bool:
        return any(s.may_price(legs) for s in self.stores)

    def has_snapshot(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> bool:
        return any(s.has_snapshot(t, max_age) for s in self.stores)

    def live_games(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> frozenset:
        return frozenset().union(*(s.live_games(t, max_age) for s in self.stores)) if self.stores else frozenset()

    def book_at(self, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC):
        parts = [(s, s.parts(t, max_age)) for s in self.stores]
        parts = [(s, p) for s, p in parts if p is not None]
        if not parts:
            return None
        key = tuple((id(s), p[0], p[3]) for s, p in parts)
        if key not in self._books:
            book = InMemoryFairBook(clock=lambda: t)
            legs, freshest = [], {}
            for s, (ts, lg, idx, _live) in parts:
                legs += lg
                if idx is not None and (s.sport not in freshest or ts > freshest[s.sport][0]):
                    freshest[s.sport] = (ts, idx)
            book.replace_all(legs)
            book.attach_prop_indices({sp: v[1] for sp, v in freshest.items()})
            self._books[key] = book
        return self._books[key]


PARLAY_KEYS = {"nba": "basketball_nba", "nhl": "icehockey_nhl"}


def _list_with_ts(prefix: str, storage=None) -> list[tuple[str, float]]:
    """[(key, last-modified epoch)] under a prefix. GCS gives the object's updated time; a local tree falls back to mtime."""
    from mlb_core import storage as _st
    st = storage or _st
    if hasattr(st, "_get_bucket") and st._get_bucket():
        return [(b.name, b.updated.timestamp()) for b in st._gcs_client().list_blobs(st._get_bucket(), prefix=prefix)]
    import os
    return [(k, os.path.getmtime(st._local_path(k))) for k in st.list_keys(prefix)]


def _read_cached(key: str, cache_dir: str | None, storage=None) -> bytes:
    import os
    from mlb_core import storage as _st
    st = storage or _st
    if cache_dir:
        path = os.path.join(cache_dir, key.replace("/", "__"))
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    data = st.read_bytes(key)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return data


def commence_map(sport: str, events) -> dict:
    """{game_key: commence epoch} for a banked event list (same game key the prop index uses)."""
    from ev.daily_projections import canon_team, game_key_of
    from ev.sports import kalshi_code
    out = {}
    for ev in events:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        away = kalshi_code(sport, canon_team(sport, ev.get("away_team")))
        home = kalshi_code(sport, canon_team(sport, ev.get("home_team")))
        if away and home and away != home:
            out[game_key_of(sport, start, away, home)] = start.timestamp()
    return out


def raw_prop_snapshots(sport: str, days, *, cache_dir: str | None = None, storage=None, workers: int = 12, cluster_sec: float = 300.0):
    """Banked RAW ParlayAPI prop payloads (every book, every quoted line incl. alternates) -> [(ts, events)], time-ordered.
    nba / nhl: OddsAccum/{sport_key}/raw/{day}/props_{HHMM}.json (a list of events per snapshot).
    nfl: EVSweep/nfl_props/raw/{day}/{event}_{HHMMSS}.json, one file per event per sweep cycle; files within `cluster_sec` form one cycle."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    keyed = []
    for d in days:
        if sport == "nfl":
            keyed += [(k, ts) for k, ts in _list_with_ts(f"EVSweep/nfl_props/raw/{d}/", storage) if k.endswith(".json")]
        else:
            keyed += [(k, ts) for k, ts in _list_with_ts(f"OddsAccum/{PARLAY_KEYS[sport]}/raw/{d}/props_", storage) if k.endswith(".json")]
    keyed.sort(key=lambda x: x[1])
    with ThreadPoolExecutor(workers) as ex:
        blobs = list(ex.map(lambda kt: json.loads(_read_cached(kt[0], cache_dir, storage)), keyed))
    snaps, cur, cur_ts = [], [], None
    for (k, ts), obj in zip(keyed, blobs):
        events = obj if isinstance(obj, list) else [obj]
        if sport != "nfl":
            snaps.append((ts, events))
            continue
        if cur and ts - cur_ts > cluster_sec:
            snaps.append((cur_ts, cur)); cur = []
        cur_ts = ts
        cur += events
    if cur:
        snaps.append((cur_ts, cur))
    return snaps


def store_from_raw(sport: str, snaps) -> SnapshotStore:
    """[(ts, events)] -> SnapshotStore whose snapshots are full PropFairIndexes (alt lines included) with per-game commence times."""
    from ev.kalshi_rfq_props import build_sport_prop_index
    out = []
    for ts, events in snaps:
        idx, _ = build_sport_prop_index(sport, events, ts, ts)
        out.append((ts, [], idx, commence_map(sport, events)))
    return SnapshotStore(out, sport=sport)


def raw_game_line_snapshots(sport: str, days, *, cache_dir: str | None = None, storage=None, workers: int = 12):
    """Banked ParlayAPI GAME-LINE payloads (h2h / spreads / totals, main lines) -> [(ts, events)], time-ordered. nba / nhl only:
    OddsAccum/{sport_key}/raw/{day}/game_lines_{HHMM}.json. NFL game lines were never banked (the sweep banks props only), and BestBook's
    ladders are overwritten hourly, so neither can be replayed. `ts` is the object's write time, i.e. when the lines were true."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    if sport not in PARLAY_KEYS:
        return []
    keyed = []
    for d in days:
        keyed += [(k, ts) for k, ts in _list_with_ts(f"OddsAccum/{PARLAY_KEYS[sport]}/raw/{d}/game_lines_", storage) if k.endswith(".json")]
    keyed.sort(key=lambda x: x[1])
    with ThreadPoolExecutor(workers) as ex:
        blobs = list(ex.map(lambda kt: json.loads(_read_cached(kt[0], cache_dir, storage)), keyed))
    return [(ts, obj if isinstance(obj, list) else obj.get("events") or []) for (k, ts), obj in zip(keyed, blobs)]


def store_from_game_lines(sport: str, snaps) -> SnapshotStore:
    """[(ts, events)] -> SnapshotStore of DIRECT game / spread / total legs (ev.kalshi_game_legs.slate_rows, the live path's own consensus
    and rung interpolation), stamped with the snapshot time, with per-game commence times so a started game is never priced."""
    from ev.kalshi_game_legs import slate_rows
    out = []
    for ts, events in snaps:
        rows, _ = slate_rows(events, sport, ts)
        out.append((ts, [lf for lf in (leg_from_row(r, ts) for r in rows) if lf is not None], None, commence_map(sport, events)))
    return SnapshotStore(out, sport=sport)


def store_from_bettingpros_ladders(sport: str, snaps) -> SnapshotStore:
    """[(ts, banked ladder games)] -> SnapshotStore for an NCAA sport from its BettingPros banker file(s), via the SAME
    conversion the live fair book uses (ev.kalshi_game_banked.events_from_bettingpros -> slate_rows): BettingPros codes are
    translated through the name-joined crosswalk, unmapped schools drop out, and the commence map is keyed by the Kalshi
    game key so a started game is never priced. The banker keeps only a `latest` file (no history), so in practice this
    builds a single-snapshot store stamped with the file's own fetched_at -- point-in-time honest for combos created
    after that stamp, silent (NO_SNAPSHOT) for anything earlier."""
    from datetime import datetime, timezone
    from ev.kalshi_game_banked import events_from_bettingpros
    from ev.kalshi_game_legs import slate_rows
    from ev.kalshi_map import game_key as kx_game_key
    out = []
    for ts, games in snaps:
        now_dt = datetime.fromtimestamp(ts, timezone.utc)
        events, _ = events_from_bettingpros(sport, games, now_dt)
        from ev.sports.ncaa_teams import NCAAF_KALSHI_CODES, NCAAB_KALSHI_CODES
        census = NCAAF_KALSHI_CODES if sport == "ncaaf" else NCAAB_KALSHI_CODES
        rows, _ = slate_rows(events, sport, ts, team_code=lambda c, _c=census: c, valid_codes=census)
        commence = {}
        for ev in events:
            start = datetime.fromisoformat(str(ev["commence_time"]).replace("Z", "+00:00"))
            commence[kx_game_key(sport, start.date().isoformat(), ev["away_team"], ev["home_team"])] = start.timestamp()
        out.append((ts, [lf for lf in (leg_from_row(r, ts) for r in rows) if lf is not None], None, commence))
    return SnapshotStore(out, sport=sport)


def fetch_cycle_frames(days, storage=None, workers: int = 12):
    """Read every banked NFL sweep cycle for the given 'YYYY-MM-DD' folders (missing folders are skipped). Needs GCS_BUCKET /
    MLB_GCS_BUCKET set, otherwise mlb_core.storage looks in a local directory and finds nothing."""
    import io
    from concurrent.futures import ThreadPoolExecutor
    import pandas as pd
    from mlb_core import storage as _st
    st = storage or _st
    keys = [k for d in days for k in sorted(st.list_keys(f"EVSweep/nfl_props/{d}/")) if "/cycle_" in k]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda k: pd.read_csv(io.BytesIO(st.read_bytes(k))), keys))


# ------------------------------------------------------------------------------------------ replay + grading
def _rfq(combo: dict) -> dict:
    return {"id": combo["ticker"], "market_ticker": combo["ticker"], "contracts_fp": "1.00",
            "mve_selected_legs": [{"event_ticker": "", "market_ticker": t, "side": s} for t, s in combo["legs"]]}


def replay_one(combo: dict, store: SnapshotStore, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> dict | None:
    """-> one record, or None when no snapshot precedes the combo (or the result is not yes/no)."""
    t = epoch(combo["created_time"])
    book = store.book_at(t, max_age)
    y = float(combo.get("last_price_dollars") or 0.0)
    if book is None or combo.get("result") not in ("yes", "no") or not (0.0 < y < 1.0):
        return None
    rfq = _rfq(combo)
    m = KalshiRFQPricer(book, replace(MEASURE_CFG, max_staleness_sec=max_age)).price(rfq, t)
    n_priced = int(m.notes.get("n_priced", 0))
    rec = {"ticker": combo["ticker"], "day": combo["close_time"][:10], "created": combo["created_time"], "n_legs": len(combo["legs"]),
           "n_priced": n_priced, "n_unpriced": int(m.notes.get("n_unpriced", len(combo["legs"]))), "yes_price": y,
           "yes_won": combo["result"] == "yes", "volume": float(combo.get("volume_fp") or 0.0),
           "p_true": m.p_yes_true, "p_cons": m.p_yes_cons, "measure_reason": m.reason,
           "same_game": bool(m.notes.get("same_game"))}
    q = KalshiRFQPricer(book, replace(QUOTE_CFG, max_staleness_sec=max_age)).price(rfq, t)
    rec.update(quoted=q.quoted, quote_reason=q.reason, no_bid=q.no_bid if q.quoted else None)
    return grade(rec)


def leg_records(combo: dict, store, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> dict | None:
    """The priced legs of one combo as of its creation (fair P(YES) of each leg, side, stat, game, player) beside the combo's traded
    price and result. Input to the correlation studies (market-implied rho from the price, realized rho from outcomes)."""
    t = epoch(combo["created_time"])
    book = store.book_at(t, max_age)
    y = float(combo.get("last_price_dollars") or 0.0)
    if book is None or combo.get("result") not in ("yes", "no") or not (0.0 < y < 1.0):
        return None
    legs = []
    for tk, side in combo["legs"]:
        fv = book.get(tk, t)
        if fv is not None:
            legs.append({"ticker": tk, "side": side, "stat": fv.stat, "game_key": fv.game_key, "player": fv.player_norm,
                         "team": fv.team,          # SGP relation taxonomy: lets future rho runs stratify different-player pairs by team
                         "p_yes": round(float(fv.p_true_yes), 5), "n_books": fv.n_books, "disp": round(float(fv.disp), 4),
                         "pooled": bool(fv.pooled)})
    if not legs:
        return None
    return {"ticker": combo["ticker"], "created": combo["created_time"], "day": combo["close_time"][:10], "n_legs": len(combo["legs"]),
            "yes_price": y, "yes_won": combo["result"] == "yes", "volume": float(combo.get("volume_fp") or 0.0), "legs": legs}


def fee(no_price: float) -> float:
    return taker_fee(no_price, 100, 1.0) / 100.0


def grade(rec: dict) -> dict:
    y, won = rec["yes_price"], rec["yes_won"]
    rec["lay_pnl_at_market"] = y - (1.0 if won else 0.0) - fee(1.0 - y)          # buy NO at 1-y: win y if NO, lose 1-y if YES
    p = rec.get("p_true")
    rec["edge_ex_ante"] = None if p is None else y - p                            # lower bound on the true edge when n_unpriced > 0
    rec["filled"] = bool(rec.get("quoted") and rec["no_bid"] >= (1.0 - y) - 1e-9)
    rec["pnl_at_our_bid"] = ((0.0 if won else 1.0) - rec["no_bid"] - fee(rec["no_bid"])) if rec["filled"] else None
    return rec


def _stats(xs) -> dict:
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": round(mean, 4), "se_naive": round(sd / math.sqrt(n), 4) if n > 1 else None}


def summarize(recs: list[dict]) -> dict:
    """Pooled and grouped stats. `se_naive` assumes independent rows, which they are NOT (shared legs/games): use `by_day`."""
    def grp(rows):
        return {"edge_ex_ante": _stats([r["edge_ex_ante"] for r in rows]),
                "lay_pnl_at_market": _stats([r["lay_pnl_at_market"] for r in rows]),
                "yes_rate": _stats([1.0 if r["yes_won"] else 0.0 for r in rows]),
                "mean_yes_price": _stats([r["yes_price"] for r in rows]),
                "mean_p_true": _stats([r["p_true"] for r in rows])}
    priced = [r for r in recs if r["p_true"] is not None]
    full = [r for r in priced if r["n_unpriced"] == 0]
    bounded = [r for r in priced if r["n_unpriced"] > 0]
    filled = [r for r in recs if r["filled"]]
    out = {"combos": len(recs), "priced_any_leg": len(priced), "fully_priced": len(full), "bounded": len(bounded),
           "quoted": sum(1 for r in recs if r["quoted"]), "would_fill": len(filled),
           "fully_priced_stats": grp(full), "bounded_stats": grp(bounded),
           "pnl_at_our_bid": _stats([r["pnl_at_our_bid"] for r in filled]),
           "quote_reasons": {}, "by_day": {}}
    for r in recs:
        out["quote_reasons"][r["quote_reason"]] = out["quote_reasons"].get(r["quote_reason"], 0) + 1
    for d in sorted({r["day"] for r in recs}):
        rows = [r for r in recs if r["day"] == d]
        f = [r for r in rows if r["filled"]]
        out["by_day"][d] = {"combos": len(rows), "fully_priced": sum(1 for r in rows if r["p_true"] is not None and r["n_unpriced"] == 0),
                            "would_fill": len(f), "pnl_at_our_bid": _stats([r["pnl_at_our_bid"] for r in f])}
    return out


# ------------------------------------------------------------------------------------------ coverage audit
def audit_legs(combos, store: SnapshotStore, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> dict:
    """Every leg occurrence of every combo -> {series family: Counter(outcome)}. Outcomes: HIT_exact / HIT_curve, NO_SNAPSHOT (nothing
    stamped within max_age before the combo), STARTED (a registered prop whose game had kicked off), or MISS_<why> from the fair book."""
    from collections import Counter, defaultdict
    from ev.kalshi_props import ParsedPropTicker, parse_prop_ticker
    out = defaultdict(Counter)
    for c in combos:
        t = epoch(c["created_time"])
        book = store.book_at(t, max_age)
        live = store.live_games(t, max_age) if book is not None else frozenset()
        for tk, _ in c["legs"]:
            fam = tk.split("-")[0]
            if book is None:
                out[fam]["NO_SNAPSHOT"] += 1
                continue
            p = parse_prop_ticker(tk)
            if isinstance(p, ParsedPropTicker) and p.game_key in live:
                out[fam]["STARTED"] += 1
                continue
            if isinstance(p, ParsedPropTicker) and p.sport not in book._prop_indices and tk not in store.tickers:
                out[fam]["NO_SNAPSHOT"] += 1           # another sport's snapshot exists, but none for this sport within max_age
                continue
            leg = book.get(tk, t)
            if leg is not None:
                out[fam]["HIT_curve" if leg.pooled else "HIT_exact"] += 1
            else:
                out[fam]["MISS_" + str(book.why_unmapped(tk))] += 1
    return {k: dict(v) for k, v in out.items()}
