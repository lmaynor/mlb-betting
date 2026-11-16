"""
ev.res11_shadow -- RES-11 Stage 2: forward live shadow logger for the intraday
distribution-pop screen (RUL-20261116-intraday-pop-approach), VARIANT 2.

WHAT IT DOES. One firing rebuilds the Stage 1 replay frame for the current
window from the live banks -- OddsAccum raw snapshots (the same DEC-08
accumulator stream Stage 1 replayed), the EVSweep production-board archives
(the frozen distributions), the official NBA injury reports, and the NBA
box-score master (the G2 rotation input) -- runs the Stage 1 engine
(scripts/run_res11_stage1.py, imported unchanged) at the primary delta
0.045 with the 2-snapshot persistence rule as the firing rule, and banks
every NEW trigger to

    RES11/shadow/chunks/{run_id}-{sha8}.jsonl.gz     (create-only, append-only)
    RES11/shadow/runs/{run_id}.json                  (per-firing summary)
    RES11/shadow/finalized/{ET day}.jsonl.gz         (closes + fill linkage)
    RES11/shadow/state/mpg_cache_{hour}.json.gz      (derived G2 input cache)

G6 (stale opener) is a HARD VETO in shadow, per the ruling (it was flag-only
in the Stage 1 replay): a trigger whose G6 flag evaluates True is banked
with disposition "g6_vetoed" and is not a shadow trigger; the row is kept so
the veto itself stays auditable. G7 window tags are recorded, not gated.

Each trigger row carries the full guard-funnel state (the Stage 1 funnel is a
strict chain, so `funnel_stage_reached` plus the stage list is the complete
per-stage record), the frozen distribution (mu / dispersion / family /
P_dist), the target book's two-way quote, every active peer's de-vigged fair
at first sighting, and the t0 consensus. The finalize step (same firing, for
the two prior ET days) attaches the closing consensus (Stage 1's
leave-target-out last-pre-commence definition) and the next-snapshot fill
observation the Stage 2 drift bars need, so the Dec 13, 2026 judgment
computes closing price-ratio CLV, retention, drift, settled ROI and
calibration straight from the bank plus the outcome masters.

WHY REBUILD-PER-FIRING. The bank is the only state. Every firing recomputes
the frame from the raw banks and banks only trigger keys not already banked
(natural key: sport|event|player|stat|line|book|side|t0). Trigger
qualification is backward-looking (variant-2 confirmation, G4 velocity, the
re-arm scan's effect on later triggers), so a trigger banked from a partial
frame stays valid as the frame grows, and a later firing reproduces the same
t0 for the same key. Nothing depends on process memory, so the logger
survives VM reboots and Cloud Run task restarts by construction; durability
is verified by NEW banked rows, never by process existence.

ISOLATION (ruling section 5). Read-only against OddsAccum / EVSweep /
NBA injuries / NBA master; writes only under RES11/. No order path, no venue
calls, no contact with AUT-01 or PRD-04 jobs, windows, tapes or schedulers.

KILL SWITCH. Banking requires RES11_SHADOW_ARMED=1 in the job environment
(set only by deploy/setup_res11_shadow.sh). Without it the module exits 3
before touching the bank. Pausing the scheduler also disarms collection.

FRAME. Snapshots: ET days [today-5 .. today] (a game's pre-commence
snapshots live in the commence-day and prior folders; the extra days keep
G6 first-seen state and the re-arm series whole). Archives span the whole
frame plus 2 days forward, [today-5 .. today+2] -- an invariant, not a
tuning choice: the engine's distribution day-fallback (ct -> et -> utc)
matches a quote to the next game's distribution when the true one is
absent, so every game whose snapshots are in frame must have its archive
in the set (verified live in testing: a slid-out archive produced phantom
triggers priced off the following game's distribution). Injury folders
span the frame's game days plus one, [today-5 .. today+1], for the same
reason: the injury book is keyed by game date, and a game whose folder
slid out silently loses its G2 veto (also verified in testing). One disclosed
approximation vs the Stage 1 replay: G6 "first seen" is frame-relative; a
prop first posted more than 5 days before its game day would carry
frame-first state instead of true-first state. Props post 1-2 days out in
practice, and Stage 1 found the flag firing on 1 of 429 triggers. The
sliding frame has one more guard: a recomputed trigger that is the same
pop episode as an already-banked one (no reprice back inside the delta
between the two sightings -- the Stage 1 re-arm condition) is suppressed
rather than banked twice under a later t0 (see same_episode()).

Usage:  python -m ev.res11_shadow [--store gcs|gcloud|dir:PATH] [--bucket B]
        [--now ISO8601] [--dry-run]
        (--dry-run computes and prints the summary but banks nothing; the
        arm check is skipped only when --store is a dir: store)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# The Stage 1 engine lives in scripts/run_res11_stage1.py (not a package);
# put the repo root and scripts/ on sys.path and import it unchanged.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

from ev import res11_pop as R  # noqa: E402
import run_res11_stage1 as S1  # noqa: E402
from ev.novig.shadow_bank import make_store  # noqa: E402

PREFIX = "RES11/shadow"
RULING = "RUL-20261116-intraday-pop-approach"
DELTA = R.PRIMARY_DELTA                    # 0.045, the ruling's primary operating point
VARIANT = 2                                # 2-snapshot persistence is the firing rule
ARM_ENV = "RES11_SHADOW_ARMED"
FEEDS = {"nba": "basketball_nba", "nhl": "icehockey_nhl"}   # sport -> OddsAccum feed key
MPG_CACHE_MAX_AGE = timedelta(hours=12)


# --------------------------------------------------------------------------- small helpers
def _write_local(root: str, rel: str, data: bytes) -> None:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _json_default(o):
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, (bool,)):
        return o
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
    except Exception:
        pass
    return str(o)


def natural_key(row: dict) -> str:
    """Dedup identity of a banked trigger (first-banked wins; append-only bank)."""
    return "|".join([row["sport"], row["event_id"], row["player_norm"], row["stat"],
                     f"{float(row['line']):.2f}", row["book"], row["side"], row["ts"]])


def base_key(row: dict) -> str:
    """The natural key without t0: one pop episode's identity across firings."""
    return "|".join([row["sport"], row["event_id"], row["player_norm"], row["stat"],
                     f"{float(row['line']):.2f}", row["book"], row["side"]])


def same_episode(dser: dict, cand: dict, banked: dict) -> bool:
    """True when a recomputed trigger is the same pop episode as an already
    banked one, not a genuine re-arm. The frame slides daily, so a pop whose
    first sighting slides out of the frame reappears with a later t0; banking
    it again would double-count one pop. A genuine re-arm (Stage 1 dedupe
    semantics) requires the book to reprice back inside the delta between
    episodes, so: same episode iff the frame's delta series for the key shows
    NO dip below the firing delta between the two sightings. A dip hidden
    before the frame start would require the whole first episode to predate
    the frame (props post 1-2 days out; the frame spans 6), so the rule is
    conservative in the right direction."""
    series = dser.get((cand["sport"], cand["event_id"], cand["player_norm"],
                       cand["stat"], float(cand["line"]), cand["book"],
                       cand["side"]), [])
    t_lo = min(pd.Timestamp(cand["ts"]), pd.Timestamp(banked["ts"]))
    t_hi = max(pd.Timestamp(cand["ts"]), pd.Timestamp(banked["ts"]))
    return not any(d < DELTA for ts, d in series if t_lo < ts < t_hi)


def disposition_for(g6) -> str:
    """G6 is a hard veto in shadow: only a positively-evaluated flag vetoes.
    An unevaluable flag (None) does not veto -- the same treatment the replay
    gave it (counted separately, never silently converted into a veto)."""
    return "g6_vetoed" if g6 is True else "trigger"


# --------------------------------------------------------------------------- mirror (live banks -> Stage 1 layout)
def build_mirror(store, root: str, frame_days, archive_days, injury_days) -> dict:
    """Download the live banks into the local layout the Stage 1 builders read.
    Source keys are the production bucket layout; missing days/files are
    skipped (off-days and not-yet-published archives are normal)."""
    counts: dict = {"read_errors": 0}
    for sport, feed in FEEDS.items():
        n = 0
        for day in frame_days:
            for key in store.list(f"OddsAccum/{feed}/raw/{day}/"):
                name = key.rsplit("/", 1)[-1]
                if name.startswith("props_") and name.endswith(".json"):
                    try:
                        _write_local(root, f"oddsaccum/{feed}_raw/{day}/{name}",
                                     store.read(key))
                        n += 1
                    except Exception:
                        counts["read_errors"] += 1
        counts[f"snapshots_{sport}"] = n
        os.makedirs(os.path.join(root, "oddsaccum", f"{feed}_raw"), exist_ok=True)
        n_arch = 0
        for day in archive_days:
            try:
                data = store.read(f"EVSweep/{sport}_props/archive/{day}.json")
            except Exception:
                continue
            _write_local(root, f"evs/{sport}/{day}.json", data)
            n_arch += 1
        counts[f"archives_{sport}"] = n_arch
        os.makedirs(os.path.join(root, "evs", sport), exist_ok=True)
    n_inj = 0
    for day in injury_days:
        for key in store.list(f"NBA/injuries/official/{day}/"):
            name = key.rsplit("/", 1)[-1]
            if name.startswith("report_") and name.endswith(".csv"):
                try:
                    _write_local(root, f"injuries/{day}/{name}", store.read(key))
                    n_inj += 1
                except Exception:
                    counts["read_errors"] += 1
    counts["injury_reports"] = n_inj
    os.makedirs(os.path.join(root, "injuries"), exist_ok=True)
    os.makedirs(os.path.join(root, "masters"), exist_ok=True)
    return counts


# --------------------------------------------------------------------------- G2 rotation input (mpg), cached in the bank
def build_mpg_by_norm(masters_dir: str) -> dict:
    """Minutes-per-game before each game date, keyed (player_norm, game_date).

    The accumulation is exactly run_res11_stage1.build_outcomes' (master-to-date
    mean of minutes over prior game dates, per player), minus the outcome maps
    the shadow never uses -- settlement belongs to the Dec 13 judgment, which
    reads the masters itself. Column subset differs from the driver's only in
    dropping columns the accumulation never touches."""
    nba = pd.read_csv(os.path.join(masters_dir, "nba_boxscores.csv"),
                      usecols=["date", "player_id", "name", "played", "minutes"])
    nba = nba[nba["played"].fillna(0).astype(float) > 0]
    nba["player_norm"] = nba["name"].map(S1.snorm)
    nba["mins_num"] = nba["minutes"].map(S1._minutes)
    g = nba.sort_values("date").groupby(["player_id", "date"], as_index=False).agg(
        mins=("mins_num", "sum"))
    nba_mpg: dict = {}
    for pid, sub in g.groupby("player_id"):
        prior_m, prior_n = 0.0, 0
        for rec in sub.itertuples(index=False):
            if prior_n > 0:
                nba_mpg[(pid, rec.date)] = prior_m / prior_n
            prior_m += float(rec.mins or 0.0)
            prior_n += 1
    pid_norm = dict(zip(nba["player_id"], nba["player_norm"]))
    return {(pid_norm.get(pid, ""), day): v for (pid, day), v in nba_mpg.items()}


def load_or_build_mpg(store, root: str, now_utc: datetime,
                      allow_cache_write: bool = True) -> tuple[dict, str]:
    """The mpg table rebuilt from the 68MB master at most twice a day; the
    derived table is cached in the bank (create-only, hour-stamped) so a
    firing after a restart reuses it. Staleness is immaterial: G2's rotation
    test is a >= 15 mpg threshold."""
    for key in sorted(store.list(f"{PREFIX}/state/"), reverse=True):
        if "/mpg_cache_" not in key:
            continue
        try:
            payload = json.loads(gzip.decompress(store.read(key)))
            if now_utc - datetime.fromisoformat(payload["built_at"]) < MPG_CACHE_MAX_AGE:
                mpg = {(pn, day): float(v) for pn, day, v in payload["mpg"]}
                return mpg, f"cache:{key.rsplit('/', 1)[-1]}"
        except Exception:
            continue
    _write_local(root, "masters/nba_boxscores.csv", store.read("NBA/player_boxscores_master.csv"))
    mpg = build_mpg_by_norm(os.path.join(root, "masters"))
    payload = {"built_at": now_utc.isoformat(),
               "mpg": [[pn, day, v] for (pn, day), v in mpg.items()]}
    if allow_cache_write:
        blob = gzip.compress(json.dumps(payload).encode())
        name = f"mpg_cache_{now_utc.strftime('%Y%m%d%H')}.json.gz"
        store.write_new(f"{PREFIX}/state/{name}", blob)
    return mpg, "built"


# --------------------------------------------------------------------------- bank restore / write
def restore_bank(store) -> tuple[set, list]:
    keys: set = set()
    rows: list = []
    for key in store.list(f"{PREFIX}/chunks/"):
        if not key.endswith(".jsonl.gz"):
            continue
        for line in gzip.decompress(store.read(key)).decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            keys.add(natural_key(row))
            rows.append(row)
    return keys, rows


def write_chunk(store, rows: list, run_id: str) -> str:
    body = "".join(json.dumps(r, default=_json_default, sort_keys=True) + "\n" for r in rows)
    blob = gzip.compress(body.encode())
    sha8 = hashlib.sha1(blob).hexdigest()[:8]
    key = f"{PREFIX}/chunks/{run_id}-{sha8}.jsonl.gz"
    if not store.write_new(key, blob):
        raise RuntimeError(f"chunk name collision on {key} (same run id, different content)")
    return key


# --------------------------------------------------------------------------- trigger rows
def trigger_rows(eng, trig: pd.DataFrame, run_id: str, banked_at: datetime) -> list:
    """Banked form of the variant-2 trigger set: full candidate context, the
    frozen distribution, peer quotes at first sighting, G6 disposition, G7 tag."""
    q = eng.q
    qkey = {}
    for r in q.itertuples(index=False):
        qkey[(r.sport, r.ts, r.event_id, r.player_norm, r.stat, round(float(r.line), 2),
              r.book)] = r
    rows = []
    for rec in trig.itertuples(index=False):
        qr = qkey.get((rec.sport, rec.ts, rec.event_id, rec.player_norm, rec.stat,
                       round(float(rec.line), 2), rec.book))
        # active peers at t0 (leave-target-out de-vigged fairs), from the engine's
        # consensus series at the trigger's own snapshot
        peers: dict = {}
        series = eng.cons.get((rec.sport, rec.event_id, rec.player_norm, rec.stat,
                               rec.line), [])
        for ts_i, books in series:
            if ts_i == rec.ts:
                peers = {b: float(f) for b, f in books.items() if b != rec.book}
                break
        g6 = S1.g6_flag(eng, rec)
        rows.append({
            "ruling": RULING, "variant": VARIANT, "delta": DELTA,
            "run_id": run_id, "banked_at": banked_at.isoformat(),
            "sport": rec.sport, "ts": _json_default(rec.ts),
            "event_id": rec.event_id, "commence": _json_default(rec.commence),
            "et_day": rec.et_day, "ct_day": rec.ct_day,
            "home": rec.home, "away": rec.away, "book": rec.book,
            "stat": rec.stat, "player": rec.player, "player_norm": rec.player_norm,
            "team_full": rec.team_full, "line": float(rec.line), "side": rec.side,
            "delta_pop": float(rec.delta), "p_dist": float(rec.p_dist),
            "q_book": float(rec.q_book), "am": float(rec.am),
            "over_am": (float(qr.over_am) if qr is not None else None),
            "under_am": (float(qr.under_am) if qr is not None else None),
            "mu": (float(qr.mu) if qr is not None and qr.mu == qr.mu else None),
            "disp": (float(qr.disp) if qr is not None and qr.disp == qr.disp else None),
            "family": (qr.family if qr is not None else None),
            "p_dist_over": (float(qr.p_dist_over)
                            if qr is not None and qr.p_dist_over == qr.p_dist_over else None),
            "age_sec": (float(rec.age_sec) if rec.age_sec is not None else None),
            "hold": float(rec.hold), "alt_ladder": bool(rec.alt),
            "mins_to_start": float(rec.mins_to_start),
            "n_peers": int(rec.n_peers), "n_sharp_peers": int(rec.n_sharp_peers),
            "peer_med_side": (float(rec.peer_med_side)
                              if rec.peer_med_side is not None else None),
            "peer_mad": (float(rec.peer_mad)
                         if rec.peer_mad is not None and rec.peer_mad == rec.peer_mad else None),
            "peers_at_t0": peers,
            "funnel_stage_reached": int(rec.stage_reached),
            "funnel_stages": list(S1.Engine.FUNNEL_STAGES),
            "g4_evaluable": (None if rec.g4_evaluable is None else bool(rec.g4_evaluable)),
            "g6_flag": g6, "g7_tag": S1.window_tag(rec),
            "disposition": disposition_for(g6),
        })
    return rows


# --------------------------------------------------------------------------- finalize (closes + fill linkage)
def filled_next(eng, sport: str, event_id: str, ts, player_norm: str, stat: str,
                line: float, book: str, side: str) -> dict:
    """Next-snapshot fill observation: the first snapshot after t0 in which the
    event appears; whether the target book still quotes the same line two-sided
    there, and at what price. (The quotes frame holds two-sided cells only, so
    presence at the line IS a two-sided quote.)"""
    q = eng.q
    sub = q[(q["sport"] == sport) & (q["event_id"] == event_id)]
    later = sorted({t for t in sub["ts"] if t > ts})
    if not later:
        return {"next_snap_ts": None, "filled_next": None,
                "q_book_next": None, "am_next": None}
    nts = later[0]
    cell = sub[(sub["ts"] == nts) & (sub["player_norm"] == player_norm)
               & (sub["stat"] == stat) & (sub["line"] == line)
               & (sub["book"] == book)]
    if not len(cell):
        return {"next_snap_ts": _json_default(nts), "filled_next": False,
                "q_book_next": None, "am_next": None}
    r0 = cell.iloc[0]
    return {"next_snap_ts": _json_default(nts), "filled_next": True,
            "q_book_next": float(r0["q_over"] if side == "over" else r0["q_under"]),
            "am_next": float(r0["over_am"] if side == "over" else r0["under_am"])}


def finalize_day(store, eng, banked_rows: list, et_day: str, run_id: str,
                 now_utc: datetime, dry_run: bool) -> str | None:
    """Attach closing consensus + fill linkage to one ET day's triggers and
    bank the enriched day file (create-only). Returns the key, or None when
    there is nothing to do yet (no triggers, games not all commenced, or the
    day is already finalized)."""
    key = f"{PREFIX}/finalized/{et_day}.jsonl.gz"
    if store.list(key):
        return None
    trig = [r for r in banked_rows
            if r["et_day"] == et_day and r["disposition"] == "trigger"]
    if not trig:
        return None
    if any(datetime.fromisoformat(r["commence"]) >= now_utc - timedelta(minutes=15)
           for r in trig):
        return None                                    # a game has not tipped yet
    df = pd.DataFrame(trig)
    df["ts"] = pd.to_datetime(df["ts"])
    df["commence"] = pd.to_datetime(df["commence"])
    closed = eng.attach_close(df)          # row order/index preserved from df
    out = []
    for i in range(len(closed)):
        crow = closed.iloc[i]
        enriched = dict(trig[i])
        pc = crow["p_cons_close"]
        enriched["p_cons_close"] = None if pc is None or pc != pc else float(pc)
        enriched.update(filled_next(eng, crow["sport"], crow["event_id"], crow["ts"],
                                    crow["player_norm"], crow["stat"],
                                    float(crow["line"]), crow["book"], crow["side"]))
        enriched["finalized_at"] = now_utc.isoformat()
        enriched["finalized_run_id"] = run_id
        out.append(enriched)
    if dry_run:
        return f"{key} (dry-run, {len(out)} rows)"
    body = "".join(json.dumps(r, default=_json_default, sort_keys=True) + "\n" for r in out)
    if not store.write_new(key, gzip.compress(body.encode())):
        return None                                    # a concurrent firing won
    return key


# --------------------------------------------------------------------------- one firing
def run(store, now_utc: datetime, dry_run: bool = False) -> dict:
    t_start = time.time()
    run_id = now_utc.strftime("%Y%m%dT%H%M%SZ")
    et_today = now_utc.astimezone(_ET).date()
    day = timedelta(days=1)
    frame_days = [(et_today - 5 * day + i * day).isoformat() for i in range(6)]
    # Archives span the whole snapshot frame (plus 2 days forward): the
    # engine's distribution day-fallback (ct -> et -> utc) will match a quote
    # to the NEXT game's distribution if the true one is absent, so every
    # game whose snapshots are in frame must have its archive in the set.
    archive_days = [(et_today - 5 * day + i * day).isoformat() for i in range(8)]
    # Injury folders span the frame's game days plus one: the injury book is
    # keyed by GAME date, so a game in frame whose folder slid out would
    # silently lose its G2 veto (verified in testing: 4 phantom triggers).
    injury_days = [(et_today - 5 * day + i * day).isoformat() for i in range(7)]

    with tempfile.TemporaryDirectory() as td:
        counts = build_mirror(store, td, frame_days, archive_days, injury_days)
        mpg, mpg_source = load_or_build_mpg(store, td, now_utc,
                                            allow_cache_write=not dry_run)
        dists, dist_meta = S1.build_distributions(td)
        quotes, inv = S1.build_quotes(td)
        injuries = S1.InjuryBook(td)

        banked_keys, banked_rows = restore_bank(store)
        rows, new_rows, chunk_key, finalized = [], [], None, []
        funnel, n_cand_primary, n_suppressed = {}, 0, 0
        if len(quotes):
            eng = S1.Engine(quotes, dists, {}, mpg, {}, injuries)
            cand = eng.evaluate()
            dser = eng.delta_series()
            if len(cand):
                trig = eng.build_triggers(cand, dser, DELTA, VARIANT)
                rows = trigger_rows(eng, trig, run_id, now_utc)
                sub = cand[cand["delta"] >= DELTA]
                n_cand_primary = int(len(sub))
                for i, stage in enumerate(S1.Engine.FUNNEL_STAGES):
                    funnel[stage] = int((sub["stage_reached"] >= i).sum())
            banked_base: dict = {}
            for r in banked_rows:
                banked_base.setdefault(base_key(r), []).append(r)
            for r in rows:
                if natural_key(r) in banked_keys:
                    continue
                if any(same_episode(dser, r, p)
                       for p in banked_base.get(base_key(r), [])):
                    n_suppressed += 1
                    continue
                new_rows.append(r)
            if new_rows and not dry_run:
                chunk_key = write_chunk(store, new_rows, run_id)
            # Finalize only on a healthy frame: an empty-feed firing must not
            # stamp a day finalized with no closes behind it.
            all_rows = banked_rows + new_rows
            for fd in ((et_today - 2 * day).isoformat(), (et_today - day).isoformat()):
                res = finalize_day(store, eng, all_rows, fd, run_id, now_utc, dry_run)
                if res:
                    finalized.append(res)

        summary = {
            "run_id": run_id, "ruling": RULING, "variant": VARIANT, "delta": DELTA,
            "started_at": now_utc.isoformat(), "et_today": et_today.isoformat(),
            "frame_days": frame_days, "mirror": counts, "mpg_source": mpg_source,
            "dist_meta": dist_meta,
            "quote_rows": int(len(quotes)), "snapshots": inv["snapshots"],
            "candidates_primary_delta": n_cand_primary,
            "funnel_primary_delta": funnel,
            "armed_v2": int(len(rows)),
            "g6_vetoed": int(sum(1 for r in rows if r["disposition"] == "g6_vetoed")),
            "suppressed_same_episode": n_suppressed,
            "banked_new": int(len(new_rows)),
            "banked_new_triggers": int(sum(1 for r in new_rows
                                           if r["disposition"] == "trigger")),
            "banked_new_g6_vetoed": int(sum(1 for r in new_rows
                                            if r["disposition"] == "g6_vetoed")),
            "chunk": chunk_key, "finalized": finalized,
            "bank_total_rows": int(len(banked_rows) + len(new_rows)),
            "dry_run": dry_run,
            "elapsed_sec": round(time.time() - t_start, 1),
        }
        if not dry_run:
            store.write_new(f"{PREFIX}/runs/{run_id}.json",
                            json.dumps(summary, indent=1, default=_json_default).encode())
        return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="gcs", help="gcs | gcloud | dir:PATH")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--now", default=None, help="ISO8601 override (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print the summary; bank nothing")
    args = ap.parse_args(argv)
    if not args.dry_run and os.environ.get(ARM_ENV) != "1":
        print(f"res11_shadow: not armed ({ARM_ENV} != 1); exiting without banking")
        return 3
    store = make_store(args.store, args.bucket)
    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    summary = run(store, now, dry_run=args.dry_run)
    print(json.dumps(summary, indent=1, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
