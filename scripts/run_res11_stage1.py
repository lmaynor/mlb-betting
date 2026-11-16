"""
RES-11 Stage 1 driver -- offline intraday distribution-pop replay
(RUL-20261116-intraday-pop-approach), over the banked intraday snapshots only.

Stages:
  dists     frozen production distributions from the EVSweep board archives
  outcomes  settled actuals (+ NBA minutes for the G2 rotation test, NHL starters)
  quotes    OddsAccum raw snapshots -> long two-sided quote frame
  engine    per-(book, side) Delta_pop series -> definition hygiene -> corroboration
            lock -> guards G1-G5 (G6 flag, G7 tags) -> t0 dedupe / re-arm ->
            1-snapshot and 2-snapshot trigger variants -> closes + settlement
  metrics   the ruling's five co-primary metrics with game-day-clustered CIs,
            pooled and per (sport, stat) slice, across the delta grid

Read-only against every bank and running job. Usage:
  python scripts/run_res11_stage1.py --data DIR --out DIR [--stage all]
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pandas as pd

from ev import res11_pop as R
from ev.props import norm_name

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")

NBA_TEAM_NAME = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}
NHL_TEAM_NAME = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
    "CGY": "Calgary Flames", "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks",
    "CBJ": "Columbus Blue Jackets", "COL": "Colorado Avalanche", "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
    "NSH": "Nashville Predators", "NJD": "New Jersey Devils", "NYI": "New York Islanders",
    "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
}
TEAM_NAME = {"nba": NBA_TEAM_NAME, "nhl": NHL_TEAM_NAME}


def snorm(x) -> str:
    """norm_name tolerant of NaN / non-string cells in banked CSVs."""
    return norm_name(x) if isinstance(x, str) else ""


def ct_day(ts: datetime) -> str:
    return ts.astimezone(CT).date().isoformat()


def et_day(ts: datetime) -> str:
    return ts.astimezone(ET).date().isoformat()


# =========================================================================== distributions
def build_distributions(data: str) -> tuple[pd.DataFrame, dict]:
    rows, meta = [], {"rows_in": 0, "no_line_or_prob": 0, "inversion_failed": 0,
                      "points_plausibility_excluded": 0, "saves_non_trained_excluded": 0,
                      "kept": 0}
    for sport in ("nba", "nhl"):
        ddir = os.path.join(data, "evs", sport)
        for fname in sorted(os.listdir(ddir)):
            if not fname.endswith(".json"):
                continue
            arch = json.load(open(os.path.join(ddir, fname)))
            for r in (arch.get("rows") or {}).values():
                stat = r.get("stat")
                if stat not in R.STATS_BY_SPORT[sport]:
                    continue
                meta["rows_in"] += 1
                if sport == "nhl" and stat == "saves" and r.get("nhl_model") != "trained_v1":
                    meta["saves_non_trained_excluded"] += 1
                    continue
                line, p_over, mu = r.get("line"), r.get("p_over_model"), r.get("proj_mean")
                if line is None or p_over is None or mu is None or not r.get("commence"):
                    meta["no_line_or_prob"] += 1
                    continue
                family = "normal" if (sport == "nba" and stat == "points") else "negbin"
                disp = R.invert_dispersion(family, float(mu), float(line), float(p_over))
                if disp is None:
                    meta["inversion_failed"] += 1
                    continue
                if family == "normal" and not (0.15 <= disp / float(mu) <= 1.5):
                    meta["points_plausibility_excluded"] += 1
                    continue
                comm = R.utc(r["commence"])
                rows.append({
                    "sport": sport, "stat": stat, "player_norm": r.get("player_norm") or norm_name(r.get("player", "")),
                    "player": r.get("player"), "team": r.get("team"), "opp": r.get("opp"),
                    "mu": float(mu), "disp": float(disp), "family": family,
                    "commence": comm, "ct_day": ct_day(comm), "et_day": et_day(comm),
                    "utc_day": comm.date().isoformat(),
                    "team_full": TEAM_NAME[sport].get(r.get("team", ""), ""),
                    "opp_full": TEAM_NAME[sport].get(r.get("opp", ""), ""),
                    "model": r.get("nhl_model") or r.get("nba_model") or arch.get("engine"),
                })
                meta["kept"] += 1
    df = pd.DataFrame(rows)
    # one distribution per (sport, player, stat, ct_day): archives merge intraday runs;
    # the file already holds the merged final state, so duplicates should not occur --
    # if they do, keep the last.
    df = df.drop_duplicates(["sport", "player_norm", "stat", "ct_day"], keep="last")
    return df, meta


# =========================================================================== outcomes
def _minutes(val) -> float:
    """Master minutes arrive as float minutes or 'MM:SS' strings."""
    if val is None:
        return 0.0
    s = str(val)
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def build_outcomes(data: str) -> tuple[dict, dict, dict, dict]:
    """Returns (outcomes, nba_mpg, nhl_starters, meta).
    outcomes: (sport, player_norm, et_day, stat) -> actual value
    nba_mpg:  (player_norm, game_date) -> season-to-date mpg BEFORE that date
    nhl_starters: (et_day, team_abbr) -> starter player_norm"""
    outcomes, meta = {}, {"nba_rows": 0, "nhl_skater_rows": 0, "nhl_goalie_rows": 0}
    nba = pd.read_csv(os.path.join(data, "masters", "nba_boxscores.csv"),
                      usecols=["date", "team_abbr", "player_id", "name", "played",
                               "points", "minutes", "three_points_made", "rebounds"])
    meta["nba_rows"] = len(nba)
    nba = nba[nba["played"].fillna(0).astype(float) > 0]
    nba["player_norm"] = nba["name"].map(snorm)
    for rec in nba.itertuples(index=False):
        key = ("nba", rec.player_norm, rec.date)
        outcomes[key + ("points",)] = float(rec.points)
        outcomes[key + ("rebounds",)] = float(rec.rebounds)
        outcomes[key + ("threes",)] = float(rec.three_points_made)
    # season-to-date mpg before each game date (rotation test for G2)
    nba_mpg: dict = {}
    nba["mins_num"] = nba["minutes"].map(_minutes)
    g = nba.sort_values("date").groupby(["player_id", "date"], as_index=False).agg(
        mins=("mins_num", "sum"))
    for pid, sub in g.groupby("player_id"):
        prior_m, prior_n = 0.0, 0
        for rec in sub.itertuples(index=False):
            if prior_n > 0:
                nba_mpg[(pid, rec.date)] = prior_m / prior_n
            prior_m += float(rec.mins or 0.0)
            prior_n += 1
    # mpg keyed by player_norm for the injury-report join (reports carry names only)
    pid_norm = dict(zip(nba["player_id"], nba["player_norm"]))
    mpg_by_norm = {}
    for (pid, day), v in nba_mpg.items():
        mpg_by_norm[(pid_norm.get(pid, ""), day)] = v

    sk = pd.read_csv(os.path.join(data, "masters", "nhl_skaters.csv"),
                     usecols=["date", "team_abbr", "name", "played", "sog"])
    meta["nhl_skater_rows"] = len(sk)
    sk = sk[sk["played"].fillna(0).astype(float) > 0]
    for rec in sk.itertuples(index=False):
        outcomes[("nhl", snorm(rec.name), rec.date, "sog")] = float(rec.sog)
    go = pd.read_csv(os.path.join(data, "masters", "nhl_goalies.csv"),
                     usecols=["date", "team_abbr", "name", "played", "starter", "saves"])
    meta["nhl_goalie_rows"] = len(go)
    starters: dict = {}
    for rec in go.itertuples(index=False):
        if float(rec.played or 0) > 0:
            outcomes[("nhl", snorm(rec.name), rec.date, "saves")] = float(rec.saves)
        if str(rec.starter).lower() in ("1", "1.0", "true"):
            starters[(rec.date, rec.team_abbr)] = snorm(rec.name)
    return outcomes, mpg_by_norm, starters, meta


# =========================================================================== injuries (NBA)
class InjuryBook:
    """Official NBA injury reports (15-min cadence, parsed CSVs banked per game date).
    State = the last report at or before t for the game's date."""

    def __init__(self, data: str):
        self.series: dict = defaultdict(list)      # (game_date, player_norm) -> [(ts, status)]
        self.game_players: dict = defaultdict(set)  # game_date -> {player_norm}
        self.player_team: dict = {}                 # (game_date, player_norm) -> team_code
        root = os.path.join(data, "injuries")
        files = []
        for day in sorted(os.listdir(root)):
            ddir = os.path.join(root, day)
            if os.path.isdir(ddir):
                files += [os.path.join(ddir, f) for f in sorted(os.listdir(ddir))
                          if f.startswith("report_") and f.endswith(".csv")]
        seen = set()
        for f in files:
            try:
                df = pd.read_csv(f)
            except Exception:
                continue
            for rec in df.itertuples(index=False):
                ts = R.utc(rec.report_ts_et)
                pn = snorm(rec.player)
                key = (rec.game_date, pn)
                if (key, ts) in seen:
                    continue
                seen.add((key, ts))
                self.series[key].append((ts, str(rec.status)))
                self.game_players[rec.game_date].add(pn)
                self.player_team[key] = rec.team_code
        for k in self.series:
            self.series[k].sort()
        self._ts = {k: [t for t, _ in v] for k, v in self.series.items()}

    def status_at(self, game_date: str, player_norm: str, t: datetime):
        lst = self.series.get((game_date, player_norm))
        if not lst:
            return None
        i = bisect.bisect_right(self._ts[(game_date, player_norm)], t) - 1
        return lst[i][1] if i >= 0 else None


# =========================================================================== quotes
def build_quotes(data: str) -> tuple[pd.DataFrame, dict]:
    rows, inv = [], {"snapshots": {}, "one_sided_cells": 0}
    for sport, feed in (("nba", "basketball_nba_raw"), ("nhl", "icehockey_nhl_raw")):
        root = os.path.join(data, "oddsaccum", feed)
        n_snaps = 0
        for day in sorted(os.listdir(root)):
            ddir = os.path.join(root, day)
            if not os.path.isdir(ddir):
                continue
            for fname in sorted(os.listdir(ddir)):
                ts = R.snap_ts(day, fname)
                if ts is None:
                    continue
                try:
                    payload = json.load(open(os.path.join(ddir, fname)))
                except Exception:
                    continue
                snap = R.parse_snapshot(payload, ts, sport)
                n_snaps += 1
                inv["one_sided_cells"] += snap.n_one_sided
                for q in snap.quotes:
                    rows.append({
                        "sport": sport, "ts": ts, "event_id": q.event_id,
                        "commence": q.commence, "et_day": et_day(q.commence),
                        "ct_day": ct_day(q.commence), "utc_day": q.commence.date().isoformat(),
                        "home": q.home, "away": q.away,
                        "book": q.book, "stat": q.stat, "player": q.player,
                        "player_norm": q.player_norm, "line": q.line,
                        "over_am": q.over_am, "under_am": q.under_am,
                        "age_sec": q.age_sec, "alt": q.alt_ladder,
                        "q_over": q.q_over, "q_under": q.q_under, "hold": q.hold,
                        "fair_over": q.fair_over(),
                    })
        inv["snapshots"][sport] = n_snaps
    return pd.DataFrame(rows), inv


# =========================================================================== engine
class Engine:
    def __init__(self, quotes: pd.DataFrame, dists: pd.DataFrame, outcomes: dict,
                 mpg_by_norm: dict, starters: dict, injuries: InjuryBook | None):
        self.q = quotes
        self.outcomes = outcomes
        self.mpg = mpg_by_norm
        self.starters = starters
        self.injuries = injuries
        self._g2_rel_cache: dict = {}
        self.meta = defaultdict(int)
        # distribution lookup: register ct_day first, then et/utc fallbacks if unclaimed
        self.dist: dict = {}
        for rec in dists.itertuples(index=False):
            key0 = (rec.sport, rec.player_norm, rec.stat)
            for day in (rec.ct_day, rec.et_day, rec.utc_day):
                self.dist.setdefault(key0 + (day,), rec)
        self._prep()

    def _dist_for(self, row):
        for day in (row.ct_day, row.et_day, row.utc_day):
            rec = self.dist.get((row.sport, row.player_norm, row.stat, day))
            if rec is not None:
                if rec.team_full and rec.team_full not in (row.home, row.away):
                    continue
                return rec
        return None

    def _prep(self):
        import time
        t0 = time.time()
        q = self.q
        recs = [self._dist_for(r) for r in q.itertuples(index=False)]
        q["mu"] = [r.mu if r is not None else math.nan for r in recs]
        q["disp"] = [r.disp if r is not None else math.nan for r in recs]
        q["family"] = [r.family if r is not None else None for r in recs]
        q["team_full"] = [r.team_full if r is not None else "" for r in recs]
        self.meta["quote_rows"] = len(q)
        self.meta["quote_rows_with_dist"] = int(q["mu"].notna().sum())
        # vectorized P_dist(over) per family
        import numpy as np
        from scipy import stats as _st
        p_over = np.full(len(q), np.nan)
        mu_v, disp_v, line_v = q["mu"].to_numpy(), q["disp"].to_numpy(), q["line"].to_numpy()
        nbmask = (q["family"] == "negbin").to_numpy()
        if nbmask.any():
            r_ = 1.0 / disp_v[nbmask]
            p_over[nbmask] = _st.nbinom.sf(np.floor(line_v[nbmask]), r_, r_ / (r_ + mu_v[nbmask]))
        nmask = (q["family"] == "normal").to_numpy()
        if nmask.any():
            p_over[nmask] = _st.norm.sf((line_v[nmask] - mu_v[nmask]) / disp_v[nmask])
        q["p_dist_over"] = p_over
        q["delta_over"] = q["p_dist_over"] - q["q_over"]
        q["delta_under"] = (1.0 - q["p_dist_over"]) - q["q_under"]
        # integer-line push adjustment for count stats: P(under win) excludes push mass
        intline = (q["line"] % 1 == 0).to_numpy()
        nb = nbmask & intline & ~np.isnan(mu_v)
        if nb.any():
            r_ = 1.0 / disp_v[nb]
            push = _st.nbinom.pmf(line_v[nb], r_, r_ / (r_ + mu_v[nb]))
            q.loc[nb, "delta_under"] = q.loc[nb, "delta_under"].to_numpy() - push
        q["mins_to_start"] = (q["commence"] - q["ts"]).dt.total_seconds() / 60.0
        self.q = q
        print(f"[prep] dist join + deltas {time.time() - t0:.1f}s", flush=True)
        # peer contexts per (sport, ts, event, player, stat, line): one pass, numpy
        t1 = time.time()
        n_peers = np.zeros(len(q), dtype=int)
        n_sharp = np.zeros(len(q), dtype=int)
        peer_med = np.full(len(q), np.nan)
        peer_mad = np.full(len(q), np.nan)
        self.cons: dict = defaultdict(list)
        grp = q.groupby(["sport", "ts", "event_id", "player_norm", "stat", "line"], sort=False)
        books_v = q["book"].to_numpy()
        fair_v = q["fair_over"].to_numpy()
        age_v = q["age_sec"].to_numpy()
        for key, idx in grp.indices.items():
            fairs_d = {}
            for i in idx:
                fairs_d[books_v[i]] = fair_v[i]
            self.cons[key[0:1] + key[2:]].append((key[1], fairs_d))
            for i in idx:
                peers = [(books_v[j], fair_v[j]) for j in idx
                         if books_v[j] != books_v[i]
                         and not (age_v[j] is not None and age_v[j] == age_v[j]
                                  and age_v[j] > R.PEER_AGE_MAX_MIN * 60.0)]
                if not peers:
                    continue
                fs = sorted(f for _, f in peers)
                med = float(np.median(fs))
                n_peers[i] = len(fs)
                n_sharp[i] = sum(1 for b, _ in peers if b in R.SHARP_BOOKS)
                peer_med[i] = med
                peer_mad[i] = float(np.median([abs(x - med) for x in fs]))
        q["n_peers"], q["n_sharp"] = n_peers, n_sharp
        q["peer_med_over"], q["peer_mad"] = peer_med, peer_mad
        for k in self.cons:
            self.cons[k].sort(key=lambda x: x[0])
        self._cons_ts = {k: [t for t, _ in v] for k, v in self.cons.items()}
        print(f"[prep] peer contexts + consensus {time.time() - t1:.1f}s", flush=True)
        # modal line per prop per snapshot + first snapshot per prop
        self.modal: dict = {}
        self.prop_first_ts: dict = {}
        for (sport, ts, eid, pn, stat), sub in q.groupby(
                ["sport", "ts", "event_id", "player_norm", "stat"], sort=False):
            counts = sub.groupby("line")["book"].nunique()
            ml = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]
            self.modal[(sport, ts, eid, pn, stat)] = ml
            k = (sport, eid, pn, stat)
            if k not in self.prop_first_ts or ts < self.prop_first_ts[k]:
                self.prop_first_ts[k] = ts
        print(f"[prep] modal lines done", flush=True)
        # per-book first-seen market last_update per prop (G6)
        self.book_first_lu: dict = {}
        qq = q.dropna(subset=["age_sec"]).copy()
        qq["last_update"] = qq["ts"] - pd.to_timedelta(qq["age_sec"], unit="s")
        for (sport, eid, pn, stat, book), sub in qq.groupby(
                ["sport", "event_id", "player_norm", "stat", "book"], sort=False):
            first = sub.sort_values("ts").iloc[0]
            self.book_first_lu[(sport, eid, pn, stat, book)] = first["last_update"]
        del qq
        print(f"[prep] book first-seen done", flush=True)
        # NHL market-consensus starters per (ts, event, team_full)
        self.goalie_cons: dict = {}
        self.goalie_first: dict = {}
        nhl_sv = q[(q["sport"] == "nhl") & (q["stat"] == "saves")]
        for (ts, eid, team), sub in nhl_sv.groupby(["ts", "event_id", "team_full"], sort=False):
            if not team:
                continue
            counts = sub.groupby("player_norm")["book"].nunique()
            top = counts.max()
            leaders = counts[counts == top].index.tolist()
            starter = leaders[0] if len(leaders) == 1 else None
            self.goalie_cons[(ts, eid, team)] = starter
            k = (eid, team)
            if starter is not None and k not in self.goalie_first:
                self.goalie_first[k] = starter
        print(f"[prep] goalie consensus done", flush=True)

    # ------------------------------------------------------------------ consensus helpers
    def cons_side_at(self, key, ts, target_book, side):
        """Leave-target-out median fair (side prob) at an exact snapshot ts, or None."""
        series = self.cons.get(key)
        if not series:
            return None
        i = bisect.bisect_left(self._cons_ts[key], ts)
        if i >= len(series) or series[i][0] != ts:
            return None
        fairs = [f for b, f in series[i][1].items() if b != target_book]
        if not fairs:
            return None
        med = median(fairs)
        return med if side == "over" else 1.0 - med

    def cons_side_before(self, key, ts_exclusive, target_book, side, min_gap_min=0.0):
        """(ts, value) of the latest consensus snapshot strictly before ts_exclusive
        (and at least min_gap_min earlier), or (None, None)."""
        series = self.cons.get(key)
        if not series:
            return None, None
        limit = ts_exclusive - timedelta(minutes=min_gap_min)
        i = bisect.bisect_left(self._cons_ts[key], limit) - 1
        if i < 0:
            return None, None
        ts_i, books = series[i]
        fairs = [f for b, f in books.items() if b != target_book]
        if not fairs:
            return None, None
        med = median(fairs)
        return ts_i, (med if side == "over" else 1.0 - med)

    # ------------------------------------------------------------------ guards
    def g2_veto(self, row, side_player_norm) -> bool:
        """NBA injury guard (official reports). NHL: not banked -> never vetoes here."""
        if row.sport != "nba" or self.injuries is None:
            return False
        inj = self.injuries
        day, t = row.et_day, row.ts
        own = inj.status_at(day, side_player_norm, t)
        if own in ("Out", "Doubtful"):
            return True
        # relevant set: the player + rotation players (>=15 mpg, either team in this
        # game) carrying a designation in this game date's reports (cached per game)
        ckey = (day, row.home, row.away, side_player_norm)
        rel = self._g2_rel_cache.get(ckey)
        if rel is None:
            rel = {side_player_norm}
            for pn in inj.game_players.get(day, ()):
                tc = inj.player_team.get((day, pn))
                if TEAM_NAME["nba"].get(tc or "") in (row.home, row.away) and \
                        self.mpg.get((pn, day), 0.0) >= 15.0:
                    rel.add(pn)
            self._g2_rel_cache[ckey] = rel
        t20 = t - timedelta(minutes=20)
        for pn in rel:
            s_now = inj.status_at(day, pn, t)
            s_prev = inj.status_at(day, pn, t20)
            if s_now != s_prev and not (s_now is None and s_prev is None):
                if s_prev is not None or s_now is not None:
                    return True
            if row.mins_to_start <= 90 and s_now == "Questionable":
                return True
        return False

    def g3_veto(self, row) -> bool:
        if row.sport != "nhl":
            return False
        if row.stat == "saves":
            starter = self.goalie_cons.get((row.ts, row.event_id, row.team_full))
            return starter is None or starter != row.player_norm
        if row.stat == "sog":
            opp_team = row.away if row.team_full == row.home else row.home
            cur = self.goalie_cons.get((row.ts, row.event_id, opp_team))
            first = self.goalie_first.get((row.event_id, opp_team))
            return cur is not None and first is not None and cur != first
        return False

    def g4_veto(self, row, side) -> tuple[bool, bool]:
        """Returns (veto, evaluable)."""
        key = (row.sport, row.event_id, row.player_norm, row.stat, row.line)
        now = self.cons_side_at(key, row.ts, row.book, side)
        if now is None:
            return False, False
        ts_a, past = self.cons_side_before(key, row.ts - timedelta(minutes=25), row.book, side)
        if past is None:
            return False, False
        move = now - past                       # signed move of consensus toward 'over' prob of the side
        toward_dist = move if side == "over" else move  # cons_side_* already side-adjusted
        if toward_dist <= -0.020:               # moved against P_dist by >= 2.0pp over ~[t-30m, t]
            return True, True
        ts_b, past_b = self.cons_side_before(key, row.ts, row.book, side, min_gap_min=5.0)
        if past_b is not None and ts_b is not None:
            gap_min = (row.ts - ts_b).total_seconds() / 60.0
            if gap_min < 15.0 and (now - past_b) >= 0.035:   # steam toward P_dist, fast
                return True, True
        return False, True

    # ------------------------------------------------------------------ candidate evaluation
    FUNNEL_STAGES = ["raw_pop", "cap_12pp", "odds_window", "freshness", "hold",
                     "gap_cap", "corroboration", "g1_window", "g2_injury", "g3_goalie",
                     "g4_velocity", "g5_dispersion", "armed"]

    def evaluate(self) -> pd.DataFrame:
        import time
        t0 = time.time()
        q = self.q
        pre = q[(q["mins_to_start"] >= 0) & q["mu"].notna()]
        self.meta["pre_tip_quote_rows_with_dist"] = len(pre)
        rows = []
        for r in pre.itertuples(index=False):
            for side in ("over", "under"):
                delta = r.delta_over if side == "over" else r.delta_under
                if not (delta >= R.DELTAS[0]):
                    continue
                p_dist = r.p_dist_over if side == "over" else 1.0 - r.p_dist_over
                if side == "under" and r.family == "negbin" and float(r.line).is_integer():
                    p_dist = p_dist - R.negbin_pmf_at(r.line, r.mu, r.disp)
                g4_ev = None
                q_side = r.q_over if side == "over" else r.q_under
                am = r.over_am if side == "over" else r.under_am
                pmo = r.peer_med_over
                peer_med = None if (pmo is None or pmo != pmo) else (
                    pmo if side == "over" else 1.0 - pmo)
                pc_n, pc_sharp, pc_mad = int(r.n_peers), int(r.n_sharp), r.peer_mad
                stage = 0
                ok = [False] * len(self.FUNNEL_STAGES)
                ok[0] = True
                if delta <= R.POP_CAP:
                    ok[1] = True
                    if R.ODDS_MIN_AM <= am <= R.ODDS_MAX_AM:
                        ok[2] = True
                        if r.age_sec is not None and r.age_sec <= R.QUOTE_AGE_MAX_SEC:
                            ok[3] = True
                            hold_cap = R.HOLD_MAX_ALT_LADDER if (r.alt and r.book in ("fanduel", "hardrock")) else R.HOLD_MAX
                            if r.hold <= hold_cap:
                                ok[4] = True
                                if peer_med is not None and (p_dist - peer_med) <= R.PEER_GAP_CAP:
                                    ok[5] = True
                                    if (pc_n >= 2 or pc_sharp >= 1) and (peer_med - q_side) >= R.CORROBORATION_MIN:
                                        ok[6] = True
                                        if R.WINDOW_MIN_MINUTES <= r.mins_to_start <= R.WINDOW_MAX_MINUTES:
                                            ok[7] = True
                                            if not self.g2_veto(r, r.player_norm):
                                                ok[8] = True
                                                if not self.g3_veto(r):
                                                    ok[9] = True
                                                    veto4, g4_ev = self.g4_veto(r, side)
                                                    if not veto4:
                                                        ok[10] = True
                                                        disp_veto = (pc_n >= 3 and pc_mad is not None and pc_mad == pc_mad and
                                                                     (pc_mad > 0.060 or (pc_n == 3 and pc_mad > 0.045)))
                                                        if not disp_veto:
                                                            ok[11] = True
                                                            ok[12] = True
                last = max(i for i, v in enumerate(ok) if v)
                rows.append({
                    "sport": r.sport, "ts": r.ts, "event_id": r.event_id, "commence": r.commence,
                    "et_day": r.et_day, "ct_day": r.ct_day, "utc_day": r.utc_day, "home": r.home, "away": r.away,
                    "book": r.book, "stat": r.stat, "player": r.player, "player_norm": r.player_norm,
                    "team_full": r.team_full, "line": r.line, "side": side, "delta": delta,
                    "p_dist": p_dist, "q_book": q_side, "am": am, "age_sec": r.age_sec,
                    "hold": r.hold, "alt": bool(r.alt), "mins_to_start": r.mins_to_start,
                    "n_peers": pc_n, "n_sharp_peers": pc_sharp,
                    "peer_med_side": peer_med, "peer_mad": (None if pc_mad != pc_mad else pc_mad),
                    "stage_reached": last, "armed": ok[12],
                    "g4_evaluable": g4_ev,
                })
        print(f"[evaluate] {len(rows)} candidate rows in {time.time() - t0:.1f}s", flush=True)
        return pd.DataFrame(rows)


    # ------------------------------------------------------------------ triggers
    def delta_series(self) -> dict:
        """(sport, event, player, stat, line, book, side) -> [(ts, delta)] pre-tip."""
        out: dict = defaultdict(list)
        pre = self.q[(self.q["mins_to_start"] >= 0) & self.q["mu"].notna()]
        for r in pre.itertuples(index=False):
            base = (r.sport, r.event_id, r.player_norm, r.stat, r.line, r.book)
            out[base + ("over",)].append((r.ts, r.delta_over))
            out[base + ("under",)].append((r.ts, r.delta_under))
        for k in out:
            out[k].sort()
        return out

    def dedupe(self, cand: pd.DataFrame, delta_series: dict, delta: float) -> pd.DataFrame:
        """First-sighting t0 per key at this delta, with the 30-minute re-arm after
        the book reprices back inside threshold."""
        cand = cand[cand["delta"] >= delta].sort_values("ts")
        keep = []
        for key, sub in cand.groupby(["sport", "event_id", "player_norm", "stat",
                                      "line", "book", "side"], sort=False):
            series = delta_series.get(key, [])
            s_ts = [t for t, _ in series]
            rearmed, next_ok = True, None
            for rec in sub.itertuples():
                if not rearmed:
                    continue
                if next_ok is not None and rec.ts < next_ok:
                    continue
                keep.append(rec.Index)
                # disarm until a later series row shows delta < delta (repriced inside)
                rearmed = False
                next_ok = rec.ts + timedelta(minutes=R.REARM_MINUTES)
                j = bisect.bisect_right(s_ts, rec.ts)
                while j < len(series):
                    if series[j][1] < delta:
                        rearmed = True
                        break
                    j += 1
        return cand.loc[keep].copy() if keep else cand.iloc[0:0].copy()

    def build_triggers(self, cand: pd.DataFrame, delta_series: dict, delta: float,
                       variant: int) -> pd.DataFrame:
        armed = cand[cand["armed"]].copy()
        if variant == 2:
            ok_idx = []
            for rec in armed.itertuples():
                key = (rec.sport, rec.event_id, rec.player_norm, rec.stat, rec.line)
                ts_p, cons_p = self.cons_side_before(key, rec.ts, rec.book, rec.side,
                                                     min_gap_min=R.PERSIST_MIN_GAP_MIN)
                if ts_p is None or cons_p is None:
                    continue
                # the same book must have popped at that earlier snapshot too
                dmap = dict(delta_series.get(key + (rec.book, rec.side), []))
                if dmap.get(ts_p, -1.0) < delta:
                    continue
                cons_now = self.cons_side_at(key, rec.ts, rec.book, rec.side)
                if cons_now is None:
                    continue
                # cons_* are side-adjusted: adverse = consensus fell in side space
                if (cons_p - cons_now) >= R.PERSIST_ADVERSE_MAX_PP:
                    continue
                ok_idx.append(rec.Index)
            armed = armed.loc[ok_idx].copy()
        return self.dedupe(armed, delta_series, delta)

    # ------------------------------------------------------------------ closes + settlement
    def attach_close(self, trig: pd.DataFrame) -> pd.DataFrame:
        out = trig.copy()
        p_close = []
        for rec in out.itertuples(index=False):
            key = (rec.sport, rec.event_id, rec.player_norm, rec.stat, rec.line)
            series = self.cons.get(key, [])
            cand = [(t, b) for t, b in series if rec.ts < t < rec.commence]
            val = None
            if cand:
                _, books = cand[-1]
                fairs = [f for b, f in books.items() if b != rec.book]
                if fairs:
                    med = median(fairs)
                    val = med if rec.side == "over" else 1.0 - med
            p_close.append(val)
        out["p_cons_close"] = p_close
        return out

    def settle(self, trig: pd.DataFrame) -> pd.DataFrame:
        out = trig.copy()
        abbr_of = {}
        for sport, mp in TEAM_NAME.items():
            for ab, full in mp.items():
                abbr_of[(sport, full)] = ab
        ys, wins = [], []
        for rec in out.itertuples(index=False):
            y = None
            for day in (rec.et_day, rec.utc_day, rec.ct_day):
                y = self.outcomes.get((rec.sport, rec.player_norm, day, rec.stat))
                if y is not None:
                    break
            ys.append(y)
            if y is None:
                wins.append(None)
            elif y == rec.line:
                wins.append("push")
            elif rec.side == "over":
                wins.append("win" if y > rec.line else "loss")
            else:
                wins.append("win" if y < rec.line else "loss")
        out["y"] = ys
        out["result"] = wins
        return out


# =========================================================================== metrics
def _ci(values, clusters):
    if len(values) == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0, "G": 0}
    m, lo, hi, G = R.clustered_mean_ci(values, clusters)
    return {"mean": m, "lo": lo, "hi": hi, "n": len(values), "G": G}


def metric_block(trig: pd.DataFrame) -> dict:
    """The five co-primary metrics for one trigger set."""
    if len(trig) == 0:
        return {"n_triggers": 0, "G": 0}
    day = trig["et_day"]
    closed = trig[trig["p_cons_close"].notna()]
    clv = _ci([100.0 * (r.p_cons_close / r.q_book - 1.0) for r in closed.itertuples(index=False)],
              list(closed["et_day"]))
    anchored = closed[closed["n_peers"] >= 3]
    if len(anchored):
        entry = [100.0 * (r.peer_med_side / r.q_book - 1.0) for r in anchored.itertuples(index=False)]
        clv_a = [100.0 * (r.p_cons_close / r.q_book - 1.0) for r in anchored.itertuples(index=False)]
        r_ret = (sum(clv_a) / len(clv_a)) / (sum(entry) / len(entry)) if sum(entry) != 0 else None
        drift = _ci([100.0 * (r.p_cons_close - r.peer_med_side) for r in anchored.itertuples(index=False)],
                    list(anchored["et_day"]))
    else:
        r_ret, drift = None, {"mean": None, "lo": None, "hi": None, "n": 0, "G": 0}
    settled = trig[trig["result"].isin(["win", "loss", "push"])]
    nonpush = settled[settled["result"] != "push"]
    roi = _ci([100.0 * R.roi_profit(r.am, r.result == "win", r.book) for r in settled.itertuples(index=False)],
              list(settled["et_day"]))
    calib_d = _ci([100.0 * ((1.0 if r.result == "win" else 0.0) - r.p_dist)
                   for r in nonpush.itertuples(index=False)], list(nonpush["et_day"]))
    np_closed = nonpush[nonpush["p_cons_close"].notna()]
    calib_c = _ci([100.0 * ((1.0 if r.result == "win" else 0.0) - r.p_cons_close)
                   for r in np_closed.itertuples(index=False)], list(np_closed["et_day"]))
    return {
        "n_triggers": int(len(trig)), "G": int(trig["et_day"].nunique()),
        "n_close_attached": int(len(closed)), "n_anchored": int(len(anchored)),
        "n_settled": int(len(settled)), "n_push": int((settled["result"] == "push").sum()),
        "clv_pct": clv, "retention": r_ret, "drift_pp": drift,
        "roi_pct": roi, "calib_vs_dist_pp": calib_d, "calib_vs_close_pp": calib_c,
    }


def window_tag(rec) -> str:
    if rec.mins_to_start <= 90:
        return "late"
    ct_t = rec.ts.astimezone(CT)
    mins = ct_t.hour * 60 + ct_t.minute
    if mins >= 22 * 60 + 30 or mins < 90 or (9 * 60 <= mins < 13 * 60):
        return "opener"
    return "midday"


# =========================================================================== extras
def g6_flag(eng: Engine, rec) -> bool | None:
    """Stale-opener flag: the target book's market last_update at the trigger equals
    its first-seen last_update for the prop, while the consensus modal line moved
    >= 1.5 units (>= 0.5 for threes) since the prop's first snapshot."""
    if rec.age_sec is None:
        return None
    lu_now = rec.ts - timedelta(seconds=rec.age_sec)
    lu_first = eng.book_first_lu.get((rec.sport, rec.event_id, rec.player_norm, rec.stat, rec.book))
    if lu_first is None or lu_now != lu_first:
        return False
    k = (rec.sport, rec.event_id, rec.player_norm, rec.stat)
    first_ts = eng.prop_first_ts.get(k)
    ml_first = eng.modal.get((rec.sport, first_ts, rec.event_id, rec.player_norm, rec.stat))
    ml_now = eng.modal.get((rec.sport, rec.ts, rec.event_id, rec.player_norm, rec.stat))
    if ml_first is None or ml_now is None:
        return None
    thresh = 0.5 if rec.stat == "threes" else 1.5
    return abs(ml_now - ml_first) >= thresh


def simple_block(trig: pd.DataFrame) -> dict:
    """Light metrics for a subgroup (G6 / G7 breakdowns): n, CLV mean, ROI mean."""
    if len(trig) == 0:
        return {"n": 0}
    closed = trig[trig["p_cons_close"].notna()]
    settled = trig[trig["result"].isin(["win", "loss", "push"])]
    return {
        "n": int(len(trig)),
        "clv_mean_pct": (float((closed["p_cons_close"] / closed["q_book"] - 1.0).mean() * 100.0)
                         if len(closed) else None),
        "roi_mean_pct": (float(pd.Series([R.roi_profit(r.am, r.result == "win", r.book)
                                          for r in settled.itertuples(index=False)]).mean() * 100.0)
                         if len(settled) else None),
        "n_closed": int(len(closed)), "n_settled": int(len(settled)),
    }


# =========================================================================== main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="mirror root (oddsaccum/, evs/, masters/, injuries/)")
    ap.add_argument("--out", required=True, help="output dir for results JSON + trigger frames")
    ap.add_argument("--reuse", action="store_true", help="reuse distributions/quotes parquets in --out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    import time
    t0 = time.time()

    if args.reuse and os.path.exists(os.path.join(args.out, "distributions.parquet")):
        dists = pd.read_parquet(os.path.join(args.out, "distributions.parquet"))
        dist_meta = {"reused": True}
    else:
        dists, dist_meta = build_distributions(args.data)
        dists.to_parquet(os.path.join(args.out, "distributions.parquet"), index=False)
    print(f"[main] dists {time.time() - t0:.1f}s", flush=True)
    outcomes, mpg_by_norm, starters, out_meta = build_outcomes(args.data)
    print(f"[main] outcomes {time.time() - t0:.1f}s", flush=True)
    if args.reuse and os.path.exists(os.path.join(args.out, "quotes.parquet")):
        quotes = pd.read_parquet(os.path.join(args.out, "quotes.parquet"))
        inv = {"snapshots": {}, "one_sided_cells": 0}
        for sport in ("nba", "nhl"):
            inv["snapshots"][sport] = int(quotes[quotes["sport"] == sport]["ts"].nunique())
    else:
        quotes, inv = build_quotes(args.data)
        quotes.to_parquet(os.path.join(args.out, "quotes.parquet"), index=False)
    print(f"[main] quotes {time.time() - t0:.1f}s", flush=True)
    injuries = InjuryBook(args.data)
    print(f"[main] injuries {time.time() - t0:.1f}s", flush=True)

    eng = Engine(quotes, dists, outcomes, mpg_by_norm, starters, injuries)
    cand = eng.evaluate()
    cand.to_parquet(os.path.join(args.out, "candidates.parquet"), index=False)
    dser = eng.delta_series()

    results: dict = {"dist_meta": dist_meta, "outcome_meta": out_meta,
                     "inventory": {}, "engine_meta": dict(eng.meta)}
    # ---- inventory -------------------------------------------------------------
    inv2 = {}
    for sport, feed in (("nba", "basketball_nba_raw"), ("nhl", "icehockey_nhl_raw")):
        qs = quotes[quotes["sport"] == sport]
        feed_root = os.path.join(args.data, "oddsaccum", feed)
        n_files = sum(1 for day in os.listdir(feed_root)
                      if os.path.isdir(os.path.join(feed_root, day))
                      for f in os.listdir(os.path.join(feed_root, day))
                      if f.startswith("props_") and f.endswith(".json"))
        inv2[sport] = {
            "snapshots": int(n_files),
            "snapshot_days": int(qs["ts"].dt.date.nunique()) if len(qs) else 0,
            "quote_rows_two_sided": int(len(qs)),
            "events": int(qs["event_id"].nunique()),
            "game_days_et": sorted(qs["et_day"].unique().tolist()),
            "books": sorted(qs["book"].unique().tolist()),
            "dists_by_stat": {s: int(n) for s, n in
                              dists[dists["sport"] == sport].groupby("stat").size().items()},
            "dist_game_days": int(dists[dists["sport"] == sport]["ct_day"].nunique()),
        }
    inv2["one_sided_cells_dropped"] = int(inv["one_sided_cells"])
    inv2["injury_report_files"] = sum(len(files) for _, _, files in os.walk(
        os.path.join(args.data, "injuries")))
    results["inventory"] = inv2

    # ---- triggers across the grid ----------------------------------------------
    grid: dict = {}
    primary_trig = {}
    for variant in (1, 2):
        for delta in R.DELTAS:
            trig = eng.build_triggers(cand, dser, delta, variant)
            trig = eng.attach_close(trig)
            trig = eng.settle(trig)
            key = f"v{variant}_d{delta:.3f}"
            block = {"pooled": metric_block(trig), "slices": {}}
            for (sport, stat), sub in trig.groupby(["sport", "stat"]):
                block["slices"][f"{sport}_{stat}"] = metric_block(sub)
            grid[key] = block
            if delta == R.PRIMARY_DELTA:
                primary_trig[variant] = trig
                trig.to_parquet(os.path.join(args.out, f"triggers_v{variant}_primary.parquet"),
                                index=False)
    results["grid"] = grid

    # ---- funnel at the primary delta (variant 1 population, pre-dedupe) --------
    sub = cand[cand["delta"] >= R.PRIMARY_DELTA]
    funnel = {}
    for i, stage in enumerate(Engine.FUNNEL_STAGES):
        funnel[stage] = int((sub["stage_reached"] >= i).sum())
    funnel["triggers_after_dedupe_v1"] = int(len(primary_trig[1]))
    funnel["triggers_after_dedupe_v2"] = int(len(primary_trig[2]))
    funnel["g4_evaluable_share"] = (
        float(sub[sub["stage_reached"] >= 9]["g4_evaluable"].mean())
        if (sub["stage_reached"] >= 9).any() else None)
    results["funnel_primary_delta"] = funnel

    # ---- G6 measured effect + G7 tags (variant 1, primary delta) ----------------
    t1 = primary_trig[1].copy()
    if len(t1):
        t1["g6"] = [g6_flag(eng, r) for r in t1.itertuples(index=False)]
        t1["g7"] = [window_tag(r) for r in t1.itertuples(index=False)]
        results["g6"] = {
            "flagged": simple_block(t1[t1["g6"] == True]),   # noqa: E712
            "unflagged": simple_block(t1[t1["g6"] == False]),  # noqa: E712
            "unevaluable": int(t1["g6"].isna().sum()),
        }
        results["g7"] = {tag: simple_block(s) for tag, s in t1.groupby("g7")}
        t1.to_parquet(os.path.join(args.out, "triggers_v1_primary_tagged.parquet"), index=False)

    # ---- variant comparison at the primary delta --------------------------------
    results["variant_comparison"] = {
        "v1": grid[f"v1_d{R.PRIMARY_DELTA:.3f}"]["pooled"],
        "v2": grid[f"v2_d{R.PRIMARY_DELTA:.3f}"]["pooled"],
    }

    # ---- verdict components ------------------------------------------------------
    pooled = {d: grid[f"v1_d{d:.3f}"]["pooled"] for d in R.DELTAS}
    prim = pooled[R.PRIMARY_DELTA]
    frame_n = pooled[R.DELTAS[0]]["n_triggers"]
    frame_g = pooled[R.DELTAS[0]]["G"]
    # Stage 1 bars exactly as written in RUL-20261116-intraday-pop-approach.
    bars = {
        "clv": prim["clv_pct"]["mean"] is not None and prim["clv_pct"]["mean"] >= 1.00
               and prim["clv_pct"]["lo"] is not None and prim["clv_pct"]["lo"] > 0.0,
        "retention": prim["retention"] is not None and prim["retention"] >= 0.50
                     and prim["n_anchored"] >= 300,
        "drift": prim["drift_pp"]["mean"] is not None and prim["drift_pp"]["mean"] >= -0.50,
        "roi": prim["roi_pct"]["mean"] is not None and prim["roi_pct"]["mean"] > 0.0,
        "calib_dist": prim["calib_vs_dist_pp"]["mean"] is not None
                      and prim["calib_vs_dist_pp"]["mean"] >= -2.0,
        "calib_close": prim["calib_vs_close_pp"]["mean"] is not None
                       and prim["calib_vs_close_pp"]["mean"] >= 0.0,
    }
    kill_metric = (
        all((pooled[d]["clv_pct"]["mean"] or -99) < 0.50 for d in R.DELTAS)
        or all((pooled[d]["retention"] if pooled[d]["retention"] is not None else -99) < 0.40
               for d in R.DELTAS)
        or all((pooled[d]["roi_pct"]["mean"] or -99) <= 0.0 for d in R.DELTAS))
    kill_calib = (prim["calib_vs_dist_pp"]["mean"] is not None
                  and prim["calib_vs_dist_pp"]["mean"] < -4.0)
    frame_ok = frame_n >= 600 and frame_g >= 12
    if kill_metric or kill_calib:
        verdict = "KILL"
    elif not frame_ok:
        verdict = "COVERAGE_LIMITED"
    elif all(bars.values()):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    results["verdict"] = {"verdict": verdict, "bars": bars, "kill_metric": bool(kill_metric),
                          "kill_calib": bool(kill_calib),
                          "frame_n_at_min_delta": frame_n, "frame_g_at_min_delta": frame_g,
                          "frame_ok": bool(frame_ok),
                          "note": "Supply kill NOT executed per coordinator amendment: "
                                  "frame shortfalls are reported as coverage facts."}
    with open(os.path.join(args.out, "results.json"), "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(json.dumps({"verdict": results["verdict"],
                      "primary_pooled": prim,
                      "frame": {"n_d030": frame_n, "G_d030": frame_g}}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
