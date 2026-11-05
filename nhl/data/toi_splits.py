"""NHL skater time-on-ice SPLITS -- even strength / power play / penalty kill (+ a PP1-vs-PP2 guess) from the shift charts.

What the repo already had: the boxscore skater row carries ONE total `toi` (and `powerPlayGoals`); nhl.data.powerplay derives power-play
POINTS from play-by-play goals. Nothing carried time on ice by game state or PP unit deployment (see
docs/solutions/integration-issues/nhl-boxscore-has-no-power-play-points.md, "Still not available").

Source A (the builder, what this module is for): the shift chart `https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId=<id>`
(one request per game; typeCode 517 rows are shifts, 505 rows are goal events and are ignored) + the play-by-play
`api-web.nhle.com/v1/gamecenter/<id>/play-by-play` (rosterSpots give each player's position/team, so goalies are told apart; play-by-play
is ALREADY mirrored in GCS `NHL/raw/pbp/<id>.json.gz` -- `--pbp-from-gcs` reads it there, so only the shift chart costs a request).

Game state per second of game clock:
    skaters(team, s)  = skater shifts covering second s   (a shift is the half-open interval [start, end); goalies excluded)
    goalie_in(team,s) = a goalie shift covers s            (no goalie on ice -> the net is empty, one skater is an extra attacker)
    eff(team, s)      = skaters - (0 if goalie_in else 1)  -- the SAME effective-skater rule as nhl.data.powerplay.goal_is_power_play
    own > opp  -> PP for that team's skaters;  own < opp -> PK;  equal -> EV (5v5, 4v4, 3v3 overtime, 6v5 empty-net)
Raw shift counts flicker at line changes (a one-second overlap or gap), while real penalty states last >= 30 s, so the per-second counts are
smoothed with a rolling MEDIAN of `SMOOTH_S` seconds before classifying. A player's toi_ev / toi_pp / toi_pk are the seconds of HIS OWN
shifts that fall in each state. By construction toi_ev + toi_pp + toi_pk == the sum of his shift durations; the validation compares that
to the box score TOI (`reconcile`), to the strength the play-by-play records at every event (`strength_agreement`), and to the NHL's own
published split (`compare_official`, the `skater/timeonice` report: `evTimeOnIce`, `ppTimeOnIce`, `shTimeOnIce`).

PP unit: `pp_share` = toi_pp / (seconds the team was on the power play that game). A first-unit player is on the ice for most of a power
play, a second-unit player for the rest: `pp_unit_guess` = 1 when pp_share >= PP1_SHARE_MIN, 2 when 0 < pp_share < PP1_SHARE_MIN (and at
least MIN_PP_S seconds), else 0. A GUESS from one game -- short power plays and 5-on-3s blur it; see the analysis doc for how it checks out.

Pure functions except the cache/CLI. Raw responses are cached under a gitignored dir (default data/nhl_toi_cache/), the output is a LOCAL
CSV (default data/nhl_toi_splits/toi_splits.csv) and the run is resumable (a game already in the output is skipped). GCS is written ONLY
with --write-gcs. Be gentle: default delay 1.0 s between requests, 429 -> Retry-After (nhl.data.client does this), never parallel.

    PYTHONPATH=. python -m nhl.data.toi_splits --data-dir <dir with NHL/> --dates 2024-11-20,2025-01-14 --max-per-date 8 --pbp-from-gcs
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

from nhl.data.client import NhlClient

log = logging.getLogger("nhl.data.toi_splits")

STATS_BASE = "https://api.nhle.com/stats/rest/en"
DEFAULT_CACHE_DIR = "data/nhl_toi_cache"
DEFAULT_OUT_DIR = "data/nhl_toi_splits"
GCS_KEY = "NHL/toi_splits/toi_splits.csv"

SMOOTH_S = 5                     # rolling-median window (seconds) over the per-second skater counts
PERIOD_S = 1200                  # period length on the abs-second axis (regular-season OT is 300 s long but starts at 3600 all the same)
SHIFT_TYPE = 517                 # shiftcharts typeCode of a real shift (505 = a goal event row)
PP1_SHARE_MIN = 0.5              # pp_share at or above this -> first unit
MIN_PP_S = 10                    # fewer seconds of power play than this -> pp_unit_guess 0 (incidental)

OUT_COLS = ["game_id", "player_id", "team", "toi_total", "toi_ev", "toi_ev5", "toi_pp", "toi_pk", "n_shifts",
            "team_pp_s", "team_pk_s", "pp_share", "pp_unit_guess"]


# ---------------------------------------------------------------------------------------------------------------------
# API client + raw cache
# ---------------------------------------------------------------------------------------------------------------------
class StatsClient(NhlClient):
    """api.nhle.com/stats/rest/en -- same retry / 429 / 404 behaviour as the web-API client, a different base URL."""

    def __init__(self, delay: float = 1.0, session=None):
        super().__init__(base=STATS_BASE, delay=delay, session=session)

    def shiftcharts(self, game_id):
        return self._get(f"shiftcharts?cayenneExp=gameId={int(game_id)}")

    def official_toi(self, day_from: str, day_to: str | None = None):
        """The NHL's own per-game skater TOI report (isGame) for games dated day_from..day_to: one row per skater-game with
        evTimeOnIce / ppTimeOnIce / shTimeOnIce / timeOnIce in seconds. The endpoint caps a query at 10,000 rows (~ a week)."""
        day_to = day_to or day_from
        expr = urllib.parse.quote(f'gameDate>="{day_from}" and gameDate<="{day_to}"')
        return self._get(f"skater/timeonice?isGame=true&limit=-1&cayenneExp={expr}")


class RawCache:
    """gzip-JSON files `<root>/<kind>/<key>.json.gz`. A cached 404 (None) is stored as JSON null so it is not asked again."""

    _MISS = object()

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, kind: str, key) -> Path:
        return self.root / kind / f"{key}.json.gz"

    def get(self, kind: str, key):
        p = self._path(kind, key)
        if not p.exists():
            return self._MISS
        return json.loads(gzip.decompress(p.read_bytes()))

    def put(self, kind: str, key, obj) -> None:
        p = self._path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(json.dumps(obj).encode()))
        os.replace(tmp, p)

    def fetch(self, kind: str, key, loader):
        """cache hit -> the stored object (possibly None); else `loader()` once and store its answer."""
        hit = self.get(kind, key)
        if hit is not self._MISS:
            return hit
        obj = loader()
        self.put(kind, key, obj)
        return obj


def _gcs_pbp(game_id):
    """Read-only: the play-by-play already mirrored by nhl.data.ingest (None when absent or unreadable)."""
    from mlb_core import storage
    from nhl.data.settings import raw_pbp_key
    try:
        return json.loads(gzip.decompress(storage.read_bytes(raw_pbp_key(game_id))))
    except Exception as exc:                                                               # noqa: BLE001
        log.info("pbp %s not in storage (%s)", game_id, exc)
        return None


# ---------------------------------------------------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------------------------------------------------
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def parse_clock(s):
    """'MM:SS' -> seconds; anything else -> None."""
    m = _CLOCK.match(s) if isinstance(s, str) else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def shifts_frame(chart: dict | None, shootout_ok: bool = False) -> pd.DataFrame:
    """Shift chart JSON -> one row per real shift: player_id, team_id, team, period, start, end (seconds on the abs game axis,
    period p starts at (p-1)*1200). Goal-event rows (typeCode 505), shifts without a start/end and zero-length shifts are dropped."""
    cols = ["player_id", "team_id", "team", "period", "start", "end"]
    rows = []
    for r in (chart or {}).get("data") or []:
        if r.get("typeCode") != SHIFT_TYPE:
            continue
        a, b = parse_clock(r.get("startTime")), parse_clock(r.get("endTime"))
        per = r.get("period")
        if a is None or b is None or per is None or b <= a:
            continue
        off = (int(per) - 1) * PERIOD_S
        rows.append((int(r["playerId"]), int(r["teamId"]), r.get("teamAbbrev"), int(per), off + a, off + b))
    return merge_overlaps(pd.DataFrame(rows, columns=cols))


def merge_overlaps(sh: pd.DataFrame) -> pd.DataFrame:
    """A player cannot be on the ice twice at once, but the chart does carry exact duplicate rows (seen live: game 2024020302 lists one
    57 s shift twice, which made a team look like it had 6 skaters and flipped a whole game's PP/PK split). Union each player's
    overlapping or touching-by-overlap shifts (shifts that merely touch, end == next start, stay separate: they are two shifts)."""
    if sh.empty:
        return sh
    out = []
    for (pid, tid, team), g in sh.sort_values(["player_id", "start", "end"]).groupby(["player_id", "team_id", "team"], sort=False,
                                                                                      dropna=False):
        cur_a = cur_b = cur_p = None
        for a, b, per in zip(g["start"], g["end"], g["period"]):
            if cur_a is not None and a < cur_b:
                cur_b = max(cur_b, b)
                continue
            if cur_a is not None:
                out.append((pid, tid, team, cur_p, cur_a, cur_b))
            cur_a, cur_b, cur_p = a, b, per
        out.append((pid, tid, team, cur_p, cur_a, cur_b))
    return pd.DataFrame(out, columns=sh.columns).sort_values(["start", "player_id"]).reset_index(drop=True)


def roster_frame(pbp: dict) -> pd.DataFrame:
    """play-by-play rosterSpots -> player_id, team_id, pos (positionCode: C L R D G)."""
    rs = (pbp or {}).get("rosterSpots") or []
    return pd.DataFrame([(int(r["playerId"]), int(r["teamId"]), r.get("positionCode")) for r in rs],
                        columns=["player_id", "team_id", "pos"])


# ---------------------------------------------------------------------------------------------------------------------
# the per-second state timeline
# ---------------------------------------------------------------------------------------------------------------------
def _cover(starts: np.ndarray, ends: np.ndarray, n: int) -> np.ndarray:
    """Number of half-open intervals [start, end) covering each integer second 0..n-1."""
    d = np.zeros(n + 1, dtype=np.int64)
    np.add.at(d, starts, 1)
    np.add.at(d, ends, -1)
    return np.cumsum(d)[:n]


def _median(x: np.ndarray, w: int) -> np.ndarray:
    """Rolling median, odd window `w`, edge-replicated (w <= 1 -> unchanged)."""
    if w <= 1 or len(x) == 0:
        return x.astype(float)
    w = w + 1 if w % 2 == 0 else w
    pad = w // 2
    xp = np.pad(x.astype(float), pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(xp, w), axis=1)


def timeline(shifts: pd.DataFrame, roster: pd.DataFrame, home_id: int, away_id: int, smooth: int = SMOOTH_S) -> dict:
    """Per-second effective skater counts for both teams -> dict of arrays {n, eff_home, eff_away, sk_home, sk_away, g_home, g_away}.
    Goalies are the roster spots with pos == 'G'; shift rows of players missing from the roster are skipped (counted in `unknown`)."""
    pos = dict(zip(roster["player_id"], roster["pos"]))
    sh = shifts[shifts["player_id"].isin(pos.keys())]
    unknown = int(len(shifts) - len(sh))
    n = int(sh["end"].max()) + 1 if len(sh) else 0
    out = {"n": n, "unknown": unknown}
    for side, tid in (("home", home_id), ("away", away_id)):
        t = sh[sh["team_id"] == tid]
        is_g = t["player_id"].map(pos) == "G"
        sk, g = t[~is_g], t[is_g]
        out[f"sk_{side}"] = _median(_cover(sk["start"].to_numpy(), sk["end"].to_numpy(), n), smooth)
        out[f"g_{side}"] = _cover(g["start"].to_numpy(), g["end"].to_numpy(), n) > 0
    for side in ("home", "away"):
        out[f"eff_{side}"] = np.round(out[f"sk_{side}"]).astype(int) - np.where(out[f"g_{side}"], 0, 1)
    return out


def _state_codes(tl: dict, side: str) -> np.ndarray:
    """+1 power play, 0 even, -1 penalty kill for `side`'s skaters, per second."""
    own, opp = tl[f"eff_{side}"], tl["eff_away" if side == "home" else "eff_home"]
    return np.sign(own - opp).astype(int)


def split_players(shifts: pd.DataFrame, roster: pd.DataFrame, tl: dict, home_id: int) -> pd.DataFrame:
    """Per skater: seconds of his own shifts in each state. Columns toi_total toi_ev toi_ev5 toi_pp toi_pk n_shifts + team_id."""
    pos = dict(zip(roster["player_id"], roster["pos"]))
    sh = shifts[shifts["player_id"].map(pos).notna() & (shifts["player_id"].map(pos) != "G")]
    n = tl["n"]
    cum = {}
    for side in ("home", "away"):
        st = _state_codes(tl, side)
        other = "away" if side == "home" else "home"
        five = ((tl[f"eff_{side}"] == 5) & (tl[f"eff_{other}"] == 5)).astype(int)
        cum[side] = {k: np.concatenate([[0], np.cumsum(v)]) for k, v in
                     (("pp", (st > 0).astype(int)), ("pk", (st < 0).astype(int)), ("ev", (st == 0).astype(int)), ("ev5", five))}
    rows: dict = {}
    for pid, tid, a, b in zip(sh["player_id"], sh["team_id"], sh["start"], sh["end"]):
        a, b = int(min(a, n)), int(min(b, n))
        c = cum["home" if tid == home_id else "away"]
        r = rows.setdefault(int(pid), {"team_id": int(tid), "toi_total": 0, "toi_ev": 0, "toi_ev5": 0, "toi_pp": 0, "toi_pk": 0,
                                       "n_shifts": 0})
        r["toi_total"] += b - a
        r["n_shifts"] += 1
        for k in ("ev", "ev5", "pp", "pk"):
            r[f"toi_{k}"] += int(c[k][b] - c[k][a])
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "player_id"
    return df.reset_index()


def add_pp_unit(df: pd.DataFrame, tl: dict, home_id: int, away_id: int) -> pd.DataFrame:
    """team_pp_s / team_pk_s (seconds the player's team was on the PP / PK), pp_share, pp_unit_guess."""
    st_home, st_away = _state_codes(tl, "home"), _state_codes(tl, "away")
    pp_s = {home_id: int((st_home > 0).sum())}
    pk_s = {home_id: int((st_home < 0).sum())}
    pp_s[away_id], pk_s[away_id] = int((st_away > 0).sum()), int((st_away < 0).sum())
    df = df.copy()
    df["team_pp_s"] = df["team_id"].map(pp_s)
    df["team_pk_s"] = df["team_id"].map(pk_s)
    df["pp_share"] = np.where(df["team_pp_s"] > 0, df["toi_pp"] / df["team_pp_s"].where(df["team_pp_s"] > 0), np.nan)
    df["pp_unit_guess"] = np.select(
        [(df["toi_pp"] >= MIN_PP_S) & (df["pp_share"] >= PP1_SHARE_MIN), df["toi_pp"] >= MIN_PP_S], [1, 2], default=0)
    return df


def anchor_to_pbp(tl: dict, pbp: dict) -> dict:
    """Pin the shift-derived state to the play-by-play's recorded state (a COPY of `tl` is returned). Shift counts see things the
    penalty ledger does not (a sixth skater who is on the ice for 20 s before a too-many-men call, a duplicated or missing shift row),
    while the play-by-play situationCode is the official strength at every event. Between two consecutive events:
      * both events record the SAME state S -> every second in between is S (no penalty started or ended unnoticed: a change needs an
        event, a penalty call, or its expiry, which is followed by the next event only later -- accepted, see the analysis doc);
      * the states differ (a penalty began/ended in between, possibly two changes: a minor expiring and then a coincident one) -> keep the
        shift-derived state untouched.
    Seconds before the first / after the last usable event keep the shift-derived state. Nothing is anchored when no event parses."""
    n = tl["n"]
    ev = {}
    for p in (pbp or {}).get("plays") or []:
        per = p.get("periodDescriptor") or {}
        if per.get("periodType") == "SO":
            continue
        sc, t = situation_skaters(p.get("situationCode")), parse_clock(p.get("timeInPeriod"))
        if sc is None or t is None or per.get("number") is None:
            continue
        ag, a_sk, h_sk, hg = sc
        sec = (int(per["number"]) - 1) * PERIOD_S + t
        if 0 <= sec < n:
            ev.setdefault(sec, (h_sk - (0 if hg else 1), a_sk - (0 if ag else 1)))
    out = dict(tl)
    eh, ea = tl["eff_home"].copy(), tl["eff_away"].copy()
    secs = sorted(ev)
    for s0, s1 in zip(secs, secs[1:]):
        if ev[s0] == ev[s1]:
            eh[s0:s1], ea[s0:s1] = ev[s0]
    out["eff_home"], out["eff_away"] = eh, ea
    return out


def build_game(game_id: int, chart: dict | None, pbp: dict | None, smooth: int = SMOOTH_S,
               anchor: bool = True) -> tuple[pd.DataFrame, dict]:
    """(rows of OUT_COLS, qa dict) for one game; ({}, {status}) when a source is missing/unusable. Never raises on bad data.
    anchor=True pins the state to the play-by-play's recorded strength (anchor_to_pbp); False = pure shift-count states."""
    if not chart or not (chart.get("data") or []):
        return pd.DataFrame(columns=OUT_COLS), {"game_id": int(game_id), "status": "no_shiftchart"}
    if not pbp or not pbp.get("rosterSpots"):
        return pd.DataFrame(columns=OUT_COLS), {"game_id": int(game_id), "status": "no_pbp_roster"}
    home_id, away_id = int((pbp.get("homeTeam") or {}).get("id", -1)), int((pbp.get("awayTeam") or {}).get("id", -1))
    sh, ro = shifts_frame(chart), roster_frame(pbp)
    if sh.empty or home_id < 0 or away_id < 0:
        return pd.DataFrame(columns=OUT_COLS), {"game_id": int(game_id), "status": "empty_shifts"}
    gtype = int(pbp.get("gameType") or 2)
    if gtype == 2:                                      # regular-season shootout (period 5) carries no skater time
        sh = sh[sh["period"] <= 4]
    tl_raw = timeline(sh, ro, home_id, away_id, smooth)
    tl = anchor_to_pbp(tl_raw, pbp) if anchor else tl_raw
    df = split_players(sh, ro, tl, home_id)
    df = add_pp_unit(df, tl, home_id, away_id)
    abbr = sh.drop_duplicates("team_id").set_index("team_id")["team"].to_dict()
    df["team"] = df["team_id"].map(abbr)
    df["game_id"] = int(game_id)
    agree = strength_agreement(tl_raw, pbp)               # agreement of the UNANCHORED shift counts with the play-by-play
    qa = {"game_id": int(game_id), "status": "ok", "n_players": int(len(df)), "unknown_shift_players": tl["unknown"],
          "game_seconds": tl["n"], "pbp_events": agree["n"], "pbp_agree": agree["agree"], "pbp_state_agree": agree["state_agree"],
          "home_pp_s": int((_state_codes(tl, "home") > 0).sum()), "away_pp_s": int((_state_codes(tl, "away") > 0).sum())}
    return df[OUT_COLS], qa


# ---------------------------------------------------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------------------------------------------------
def situation_skaters(code):
    """situationCode '1551' -> (away_goalie, away_skaters, home_skaters, home_goalie) ints, or None."""
    try:
        s = str(int(code)).zfill(4)
    except (TypeError, ValueError):
        return None
    return tuple(int(c) for c in s) if len(s) == 4 else None


def strength_agreement(tl: dict, pbp: dict) -> dict:
    """Compare the shift-derived state with the state the play-by-play records. For every play with a situationCode (shootout and
    unparseable plays skipped) take the effective skater counts the code implies (same pulled-goalie rule) and compare with the
    timeline at the event second: `agree` = both teams' effective counts equal; `state_agree` = same PP/EV/PK direction for the home side.
    Events within 2 s of a boundary are looked up at the second that agrees best (a goal's situationCode is the PRE-goal state)."""
    n_ev = agree = state_agree = 0
    n = tl["n"]
    for p in (pbp or {}).get("plays") or []:
        per = p.get("periodDescriptor") or {}
        if per.get("periodType") == "SO":
            continue
        sc, t = situation_skaters(p.get("situationCode")), parse_clock(p.get("timeInPeriod"))
        if sc is None or t is None or per.get("number") is None:
            continue
        ag, a_sk, h_sk, hg = sc
        eff_a, eff_h = a_sk - (0 if ag else 1), h_sk - (0 if hg else 1)
        s0 = (int(per["number"]) - 1) * PERIOD_S + t
        if not (0 <= s0 < n):
            continue
        n_ev += 1
        cand = [s for s in (s0 - 1, s0, s0 + 1) if 0 <= s < n]
        if any(tl["eff_home"][s] == eff_h and tl["eff_away"][s] == eff_a for s in cand):
            agree += 1
        if any(np.sign(tl["eff_home"][s] - tl["eff_away"][s]) == np.sign(eff_h - eff_a) for s in cand):
            state_agree += 1
    return {"n": n_ev, "agree": agree, "state_agree": state_agree}


def reconcile(splits: pd.DataFrame, box: pd.DataFrame) -> dict:
    """Do the split sums reconcile with the box score TOI?  `box` = skater master rows (game_id, player_id, toi_sec).
    Reports, over every split row that has a box row: the share with |splits_sum - box| == 0, <= 2 s, <= 5 s, > 5 s, plus the mean/max
    absolute difference, the same for toi_total (shift sum) vs box, and the number of split rows with no box row / box rows with
    TOI > 0 but no split row (skaters whose shifts are missing)."""
    b = box[["game_id", "player_id", "toi_sec"]].copy()
    b["game_id"], b["player_id"] = b["game_id"].astype("int64"), b["player_id"].astype("int64")
    s = splits.copy()
    s["game_id"], s["player_id"] = s["game_id"].astype("int64"), s["player_id"].astype("int64")
    s["split_sum"] = s["toi_ev"] + s["toi_pp"] + s["toi_pk"]
    m = s.merge(b, on=["game_id", "player_id"], how="left")
    have = m["toi_sec"].notna()
    d = (m.loc[have, "split_sum"] - m.loc[have, "toi_sec"]).abs()
    d_tot = (m.loc[have, "toi_total"] - m.loc[have, "toi_sec"]).abs()
    games = set(s["game_id"].unique())
    bg = b[b["game_id"].isin(games) & (b["toi_sec"] > 0)]
    missing = bg.merge(s[["game_id", "player_id"]], on=["game_id", "player_id"], how="left", indicator=True)
    n = int(len(d))

    def rate(x, lo, hi=None):
        sel = (x > lo) if hi is None else ((x > lo) & (x <= hi))
        return float(sel.mean()) if n else float("nan")

    return {"n_rows": n, "n_split_rows_without_box": int((~have).sum()),
            "n_box_rows_without_split": int((missing["_merge"] == "left_only").sum()),
            "exact": float((d == 0).mean()) if n else float("nan"), "within_2s": float((d <= 2).mean()) if n else float("nan"),
            "within_5s": float((d <= 5).mean()) if n else float("nan"), "over_5s": rate(d, 5),
            "mean_abs_s": float(d.mean()) if n else float("nan"), "max_abs_s": float(d.max()) if n else float("nan"),
            "total_vs_box_exact": float((d_tot == 0).mean()) if n else float("nan"),
            "total_vs_box_within_2s": float((d_tot <= 2).mean()) if n else float("nan")}


def official_frame(payload: dict | None) -> pd.DataFrame:
    """skater/timeonice?isGame=true JSON -> game_id, player_id, team, date, off_total, off_ev, off_pp, off_pk (seconds). Only regular-season
    and playoff games (game id type digits 02 / 03) are kept."""
    cols = ["game_id", "player_id", "team", "date", "off_total", "off_ev", "off_pp", "off_pk"]
    rows = (payload or {}).get("data") or []
    if not rows:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(rows)
    d = d[d["gameId"].astype("int64").map(lambda g: int(str(g)[4:6]) in (2, 3))]
    return pd.DataFrame({"game_id": d["gameId"].astype("int64"), "player_id": d["playerId"].astype("int64"),
                         "team": d["teamAbbrev"], "date": d["gameDate"], "off_total": d["timeOnIce"], "off_ev": d["evTimeOnIce"],
                         "off_pp": d["ppTimeOnIce"], "off_pk": d["shTimeOnIce"]}, columns=cols).reset_index(drop=True)


def date_windows(first: str, last: str, days: int = 14) -> list:
    """[(from, to)] inclusive YYYY-MM-DD windows of `days` days covering first..last."""
    out, a, end = [], pd.Timestamp(first), pd.Timestamp(last)
    while a <= end:
        b = min(a + pd.Timedelta(days=days - 1), end)
        out.append((a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")))
        a = b + pd.Timedelta(days=1)
    return out


def fetch_official(first: str, last: str, cache: RawCache, client: StatsClient, days: int = 14) -> pd.DataFrame:
    """The NHL's published per-game skater EV/PP/SH TOI for every game dated first..last, `days`-day windows (about 10 requests a month),
    cached per window and resumable. The endpoint caps one query at 10,000 rows: a window that comes back with exactly that many is an
    error, not a result (the window is too wide)."""
    parts = []
    for a, b in date_windows(first, last, days):
        payload = cache.fetch("official", f"{a}_{b}", lambda a=a, b=b: client.official_toi(a, b))
        n = len((payload or {}).get("data") or [])
        if n >= 10000:
            raise RuntimeError(f"official TOI window {a}..{b} returned {n} rows (the endpoint cap): use a narrower window")
        parts.append(official_frame(payload))
    out = pd.concat(parts, ignore_index=True) if parts else official_frame(None)
    return out.drop_duplicates(["game_id", "player_id"]).reset_index(drop=True)


def official_as_splits(off: pd.DataFrame) -> pd.DataFrame:
    """Official frame -> the split table's column names (toi_total / toi_ev / toi_pp / toi_pk), plus an ESTIMATED team_pp_s (the team's
    summed skater power-play seconds / 5; the shift builder measures it exactly) and pp_share / pp_unit_guess from it. A cheap stand-in
    for the shift-derived table: ~10 requests a month instead of one per game."""
    d = off.rename(columns={"off_total": "toi_total", "off_ev": "toi_ev", "off_pp": "toi_pp", "off_pk": "toi_pk"}).copy()
    d["team_pp_s"] = d.groupby(["game_id", "team"])["toi_pp"].transform("sum") / 5.0
    d["team_pk_s"] = d.groupby(["game_id", "team"])["toi_pk"].transform("sum") / 5.0
    d["pp_share"] = np.where(d["team_pp_s"] > 0, d["toi_pp"] / d["team_pp_s"].where(d["team_pp_s"] > 0), np.nan)
    d["pp_unit_guess"] = np.select([(d["toi_pp"] >= MIN_PP_S) & (d["pp_share"] >= PP1_SHARE_MIN), d["toi_pp"] >= MIN_PP_S], [1, 2], default=0)
    return d


def compare_official(splits: pd.DataFrame, official: pd.DataFrame) -> dict:
    """Shift-derived split vs the NHL's published split, per state: mean/median absolute difference in seconds, share within 5 s and
    10 s, and the mean signed difference (ours minus theirs). Also the off-ice check that the NHL's own three parts sum to its total."""
    m = splits.merge(official, on=["game_id", "player_id"], how="inner")
    out = {"n_rows": int(len(m))}
    if not len(m):
        return out
    for k in ("ev", "pp", "pk"):
        d = m[f"toi_{k}"] - m[f"off_{k}"]
        a = d.abs()
        out[k] = {"mean_abs_s": float(a.mean()), "median_abs_s": float(a.median()), "within_5s": float((a <= 5).mean()),
                  "within_10s": float((a <= 10).mean()), "mean_signed_s": float(d.mean()),
                  "corr": float(np.corrcoef(m[f"toi_{k}"], m[f"off_{k}"])[0, 1]) if m[f"off_{k}"].std() > 0 else float("nan")}
    out["official_parts_sum_eq_total"] = float(((m["off_ev"] + m["off_pp"] + m["off_pk"]) == m["off_total"]).mean())
    return out


def unit_structure(splits: pd.DataFrame, min_team_pp_s: int = 90) -> dict:
    """How plausible the PP1/PP2 guess is: among team-games with at least `min_team_pp_s` seconds on the power play, the distribution
    of the number of pp_unit_guess == 1 skaters (a real first unit is 5: 3 F + 2 D)."""
    t = splits[splits["team_pp_s"] >= min_team_pp_s]
    cnt = t[t["pp_unit_guess"] == 1].groupby(["game_id", "team"]).size()
    all_tg = t.groupby(["game_id", "team"]).size().index
    cnt = cnt.reindex(all_tg, fill_value=0)
    return {"n_team_games": int(len(cnt)), "share_exactly_5": float((cnt == 5).mean()) if len(cnt) else float("nan"),
            "share_4_to_6": float(cnt.between(4, 6).mean()) if len(cnt) else float("nan"),
            "dist": {int(k): int(v) for k, v in cnt.value_counts().sort_index().items()}}


# ---------------------------------------------------------------------------------------------------------------------
# the resumable builder
# ---------------------------------------------------------------------------------------------------------------------
def select_games(games: pd.DataFrame, dates=None, seasons=None, day_from=None, day_to=None, max_per_date=None,
                 limit=None, seed=None, game_types=(2, 3)) -> list:
    """games master -> ordered list of game ids to process (final regular-season/playoff games only)."""
    g = games[games["game_type"].isin(game_types)].copy()
    if "state" in g.columns:
        g = g[g["state"].isin(["OFF", "FINAL"])]
    g["date"] = g["date"].astype(str)
    if dates:
        g = g[g["date"].isin(set(dates))]
    if seasons:
        g = g[g["season_year"].isin({int(s) for s in seasons})]
    if day_from:
        g = g[g["date"] >= day_from]
    if day_to:
        g = g[g["date"] <= day_to]
    g = g.sort_values(["date", "game_id"])
    if max_per_date:
        g = g.groupby("date", sort=False).head(int(max_per_date))
    ids = [int(x) for x in g["game_id"]]
    if limit and len(ids) > int(limit):
        if seed is not None:
            rng = np.random.default_rng(int(seed))
            ids = sorted(int(x) for x in rng.choice(ids, size=int(limit), replace=False))
        else:
            ids = ids[: int(limit)]
    return ids


def _read_out(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=OUT_COLS)


def run(game_ids, cache_dir=DEFAULT_CACHE_DIR, out_dir=DEFAULT_OUT_DIR, delay: float = 1.0, pbp_from_gcs: bool = False,
        client: StatsClient | None = None, web_client: NhlClient | None = None, flush_every: int = 10) -> dict:
    """Process `game_ids` (skipping any already in `<out_dir>/toi_splits.csv`), caching raw responses; returns a summary dict.
    Resumable: the output CSV and the per-game QA CSV are rewritten atomically every `flush_every` games and at the end, and a
    re-run reads them back. A game whose shift chart / play-by-play is missing is logged in the QA CSV with its status (never
    silently dropped, never filled)."""
    out, cache = Path(out_dir), RawCache(cache_dir)
    out.mkdir(parents=True, exist_ok=True)
    f_out, f_qa = out / "toi_splits.csv", out / "toi_splits_games.csv"
    done = _read_out(f_out)
    qa = pd.read_csv(f_qa) if f_qa.exists() else pd.DataFrame()
    seen = set(done["game_id"].astype("int64")) | (set(qa["game_id"].astype("int64")) if len(qa) else set())
    todo = [int(g) for g in game_ids if int(g) not in seen]
    log.info("%d games requested, %d already built, %d to do", len(list(game_ids)), len(seen & {int(g) for g in game_ids}), len(todo))
    client = client or StatsClient(delay=delay)
    web = web_client
    parts, qrows = [done], ([qa] if len(qa) else [])

    def flush():
        pd.concat(parts, ignore_index=True)[OUT_COLS].to_csv(str(f_out) + ".tmp", index=False)
        os.replace(str(f_out) + ".tmp", f_out)
        pd.concat(qrows, ignore_index=True).to_csv(str(f_qa) + ".tmp", index=False)
        os.replace(str(f_qa) + ".tmp", f_qa)

    for i, gid in enumerate(todo, 1):
        chart = cache.fetch("shifts", gid, lambda g=gid: client.shiftcharts(g))

        def load_pbp(g=gid):
            if pbp_from_gcs:
                p = _gcs_pbp(g)
                if p is not None:
                    return p
            nonlocal web
            web = web or NhlClient(delay=delay)
            return web.play_by_play(g)

        pbp = cache.fetch("pbp", gid, load_pbp)
        df, q = build_game(gid, chart, pbp)
        parts.append(df)
        qrows.append(pd.DataFrame([q]))
        if i % flush_every == 0:
            flush()
            log.info("%d/%d games done", i, len(todo))
    if todo or not f_out.exists():
        flush()
    res = pd.read_csv(f_out)
    q = pd.read_csv(f_qa) if f_qa.exists() else pd.DataFrame()
    return {"games_built": int(res["game_id"].nunique()), "rows": int(len(res)), "new_games": len(todo),
            "status_counts": q["status"].value_counts().to_dict() if len(q) else {}, "out": str(f_out)}


def write_gcs(out_dir=DEFAULT_OUT_DIR, key: str = GCS_KEY) -> str:
    """Upload the local output through mlb_core.storage (the ONLY path that writes to the bucket; called only by --write-gcs)."""
    from mlb_core import storage
    storage.write_csv(pd.read_csv(Path(out_dir) / "toi_splits.csv"), key)
    return key


def validate(out_dir=DEFAULT_OUT_DIR, data_dir=None, official_csv=None) -> dict:
    """Validation report for a built output dir: reconciliation with the box score TOI, the shift-vs-play-by-play state agreement (from
    the per-game QA csv), the PP-unit structure and, when `official_csv` is given, the comparison with the NHL's published split."""
    out = Path(out_dir)
    sp, qa = pd.read_csv(out / "toi_splits.csv"), pd.read_csv(out / "toi_splits_games.csv")
    if data_dir:
        box = pd.read_csv(os.path.join(data_dir, "NHL", "skater_boxscores_master.csv"), low_memory=False)
    else:
        from mlb_core import storage
        from nhl.data.settings import SKATER_BOX_MASTER
        box = storage.read_csv(SKATER_BOX_MASTER, low_memory=False)
    ok = qa[qa["status"] == "ok"]
    rep = {"games_ok": int(len(ok)), "games_total": int(len(qa)), "status_counts": qa["status"].value_counts().to_dict(),
           "pbp_event_agree": float(ok["pbp_agree"].sum() / ok["pbp_events"].sum()) if len(ok) else float("nan"),
           "pbp_state_agree": float(ok["pbp_state_agree"].sum() / ok["pbp_events"].sum()) if len(ok) else float("nan"),
           "reconcile_vs_box": reconcile(sp, box), "pp_unit": unit_structure(sp)}
    joined = sp.merge(box[["game_id", "player_id", "toi_sec"]], on=["game_id", "player_id"])
    rep["n_games"] = int(sp["game_id"].nunique())
    rep["games_with_shift_total_off_box"] = int(sum(1 for _, d in joined.groupby("game_id") if (d["toi_total"] - d["toi_sec"]).abs().max() > 0))
    if official_csv:
        rep["compare_official"] = compare_official(sp, pd.read_csv(official_csv))
    return rep


def _load_games(data_dir):
    if data_dir:
        return pd.read_csv(os.path.join(data_dir, "NHL", "games_master.csv"), low_memory=False)
    from mlb_core import storage
    from nhl.data.settings import GAMES_MASTER
    return storage.read_csv(GAMES_MASTER, low_memory=False)


def main(argv=None) -> int:
    """Build skater EV / PP / PK time-on-ice splits for a set of games (LOCAL output; --write-gcs is the only bucket write)."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--data-dir", help="dir holding NHL/games_master.csv (default: mlb_core.storage)")
    ap.add_argument("--game-ids", help="comma list of game ids (overrides the selectors below)")
    ap.add_argument("--dates", help="comma list of YYYY-MM-DD game dates")
    ap.add_argument("--seasons", help="comma list of season start years")
    ap.add_argument("--from", dest="day_from")
    ap.add_argument("--to", dest="day_to")
    ap.add_argument("--max-per-date", type=int)
    ap.add_argument("--limit", type=int, help="cap the number of games (random subset when --seed is given)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--official-from", help="instead of building from shifts, pull the NHL's published EV/PP/SH report for games from this date")
    ap.add_argument("--official-to", help="... through this date (writes <out-dir>/official_toi.csv; ~one request per 14 days)")
    ap.add_argument("--validate", action="store_true", help="validate an existing --out-dir (reconcile vs box, vs the pbp, vs --official-csv)")
    ap.add_argument("--official-csv", help="official_toi.csv (from --official-from) to compare against with --validate")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between API requests (default 1.0)")
    ap.add_argument("--pbp-from-gcs", action="store_true", help="read play-by-play from the NHL/raw/pbp mirror (a READ) before the API")
    ap.add_argument("--write-gcs", action="store_true", help=f"after building, upload the CSV to {GCS_KEY} (default: local only)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.validate:
        rep = validate(a.out_dir, a.data_dir, a.official_csv)
        (Path(a.out_dir) / "validation.json").write_text(json.dumps(rep, indent=1, default=str))
        print(json.dumps(rep, indent=1, default=str))
        return 0
    if a.official_from:
        off = fetch_official(a.official_from, a.official_to or a.official_from, RawCache(a.cache_dir), StatsClient(delay=a.delay))
        Path(a.out_dir).mkdir(parents=True, exist_ok=True)
        off.to_csv(Path(a.out_dir) / "official_toi.csv", index=False)
        log.info("official TOI: %d skater-games over %d games -> %s", len(off), off["game_id"].nunique(), Path(a.out_dir) / "official_toi.csv")
        return 0
    if a.game_ids:
        ids = [int(x) for x in a.game_ids.split(",") if x.strip()]
    else:
        ids = select_games(_load_games(a.data_dir), dates=a.dates.split(",") if a.dates else None,
                           seasons=a.seasons.split(",") if a.seasons else None, day_from=a.day_from, day_to=a.day_to,
                           max_per_date=a.max_per_date, limit=a.limit, seed=a.seed)
    log.info("selected %d games", len(ids))
    summary = run(ids, a.cache_dir, a.out_dir, a.delay, a.pbp_from_gcs)
    log.info("summary: %s", summary)
    if a.write_gcs:
        log.info("uploaded to %s", write_gcs(a.out_dir))
    else:
        log.info("local only: pass --write-gcs to upload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
