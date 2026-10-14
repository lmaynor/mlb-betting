"""
mlb.execution.kalshi.consensus -- the sportsbook book-pack fair probability (the "truth").

p_B for a proposition = MEDIAN of each book's own 2-way-devigged fair_prob at the latest
snapshot, over real books only (excludes kalshi, the synthetic 'consensus' row, and 'open').
Same truth estimate as kalshi_vs_books._kalshi_book_fair / outlier_scan, which is what the
mirror backtest validated (never a vig-loaded implied prob -- that would make every book's own
vig look like free Kalshi edge).

Also holds the history needed for the lag signature: the consensus ~lookback ago and Kalshi's
own banked mid ~lookback ago (odds_history carries both).

KNOWN LIMIT: consensus freshness is bounded by the odds_history snapshot cadence (8 ParlayAPI
snapshots/day). The single-event refresh in design doc s3 S3 is NOT built for MLB (it IS for NFL props:
ev.props_exec --refresh-consensus); the age gate (`cons_max_age_min`) rejects stale consensus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

EXCLUDE_BOOKS = {"kalshi", "consensus", "open"}
_NRFI_MAP = {"YES": "YRFI", "NO": "NRFI"}
_KALSHI_SANE = (0.03, 0.97)            # stale thin prints outside this are not real (see docs/solutions)
LINE_NONE = -99.0


@dataclass(frozen=True)
class ConsPoint:
    p: float                    # median devigged fair prob of the YES-side selection
    n_books: int
    disp: float                 # MAD of per-book fair_prob
    ts: datetime                # snapshot time (UTC)
    prev_p: float | None        # consensus >= lookback earlier (None if no such snapshot)


def _lk(line) -> float:
    return LINE_NONE if line is None or (isinstance(line, float) and np.isnan(line)) else round(float(line), 2)


def _mad(x: pd.Series) -> float:
    return float(np.median(np.abs(x - np.median(x)))) if len(x) else 0.0


def _ts(s) -> datetime:
    return pd.Timestamp(s).to_pydatetime().replace(tzinfo=timezone.utc)


class ConsensusStore:
    def __init__(self, cons: dict, kalshi: dict):
        self._cons = cons       # (market, game_pk, line_key, selection) -> [(ts, p, n, disp)] sorted
        self._kal = kalshi      # same key -> [(ts, mid)] sorted

    @classmethod
    def from_frames(cls, frames: dict) -> "ConsensusStore":
        cons, kal = {}, {}
        for market, df in frames.items():
            if df is None or not len(df):
                continue
            d = df[df["game_pk"].notna() & df["fair_prob"].notna()].copy()
            if not len(d):
                continue
            d["game_pk"] = d["game_pk"].astype("int64")
            d["selection"] = d["selection"].astype(str).str.upper()
            if market == "nrfi_ou":
                d["selection"] = d["selection"].map(lambda s: _NRFI_MAP.get(s, s))
            d["lkey"] = d["line"].map(_lk)

            books = d[~d["book"].str.lower().isin(EXCLUDE_BOOKS)]
            if len(books):
                g = (books.groupby(["game_pk", "lkey", "selection", "snapshot_ts"])["fair_prob"]
                     .agg(p="median", n="count", disp=_mad).reset_index())
                for r in g.itertuples(index=False):
                    cons.setdefault((market, int(r.game_pk), r.lkey, r.selection), []).append(
                        (_ts(r.snapshot_ts), float(r.p), int(r.n), float(r.disp)))
            k = d[d["book"].str.lower() == "kalshi"]
            lo, hi = _KALSHI_SANE
            k = k[k["fair_prob"].between(lo, hi)]
            for r in k.itertuples(index=False):
                kal.setdefault((market, int(r.game_pk), r.lkey, r.selection), []).append(
                    (_ts(r.snapshot_ts), float(r.fair_prob)))
        for v in cons.values():
            v.sort()
        for v in kal.values():
            v.sort()
        return cls(cons, kal)

    @classmethod
    def load(cls, markets, dates, reader=None) -> "ConsensusStore":
        import logging
        import time
        from mlb.analysis import odds_history as oh
        log = logging.getLogger("kalshi_exec.consensus")
        reader = reader or oh.read_history
        frames = {}
        for m in markets:
            frames[m] = None
            for d in dates:
                t0 = time.time()
                df = reader(m, since=d, until=d)
                log.info("read_history %s %s: %s rows in %.1fs", m, d, 0 if df is None else len(df), time.time() - t0)
                if df is not None and len(df):
                    df = oh.dedupe_by_source(df)
                    frames[m] = df if frames[m] is None else pd.concat([frames[m], df], ignore_index=True)
        return cls.from_frames(frames)

    def point(self, market: str, game_pk: int, line, selection: str,
              lookback_min: float = 60.0) -> ConsPoint | None:
        series = self._cons.get((market, int(game_pk), _lk(line), selection))
        if not series:
            return None
        ts, p, n, disp = series[-1]
        cutoff = ts - timedelta(minutes=lookback_min)
        prev = [x for x in series if x[0] <= cutoff]
        return ConsPoint(p=p, n_books=n, disp=disp, ts=ts, prev_p=prev[-1][1] if prev else None)

    def kalshi_prev(self, market: str, game_pk: int, line, selection: str, now: datetime,
                    lookback_min: float = 60.0) -> float | None:
        series = self._kal.get((market, int(game_pk), _lk(line), selection))
        if not series:
            return None
        cutoff = now - timedelta(minutes=lookback_min)
        prev = [x for x in series if x[0] <= cutoff]
        return prev[-1][1] if prev else None
