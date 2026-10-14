"""NHL SOG live scorer: banked ParlayAPI props snapshot -> model P(over) per (player, line) -> edge vs the books.

LOG-ONLY / paper. This module NEVER posts to Discord, NEVER writes the `bets` table, and is not in the MLB registry. Its
output is evidence rows (CSV) that a later job could grade. The validation study (docs/analysis/nhl_sog_validation_2026-10-14.md)
found NO_EDGE on 63 days of history, so nothing here may size real money; the flags exist so that, if forward data ever
supports it, the same code can be switched on.

Reuses (does not re-derive): ev.sweep_props_alerts.read_latest_props (stale-snapshot refusal), load_resolver, team_fn;
ev.props.parse_event_props / build_consensus (DFS apps excluded, two-sided de-vig); nhl.props.negbin (the repo's NegBin
P(over)); the SAME feature code as training (nhl.props.features.build_features) fed with upcoming-game placeholder rows.

Guards on a FLAG (all must hold): >= MIN_BOOKS two-sided real books, consensus dispersion <= MAX_DISP, |model - consensus|
within [MIN_EDGE, EDGE_CAP] (a huge gap is far more often model error than value -- the repo's winner's-curse rule), EV at the
best price <= EV_CAP, >= CLOSE_BUFFER_MIN to start, the player has >= MIN_PRIOR games of history, and the game log is fresh.

    PYTHONPATH=. python -m nhl.props.score --model-dir local_artifacts/nhl_sog          # one pass, local CSV, prints a summary
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ev.props import DFS_BOOKS, build_consensus, parse_event_props
from mlb_core.odds.utils import american_to_decimal
from nhl.props import train
from nhl.props.features import build_features
from nhl.props.negbin import p_over_vec

log = logging.getLogger("nhl.props.score")
_ET = ZoneInfo("America/New_York")

MIN_BOOKS = 3
MAX_DISP = 0.06
MIN_EDGE = 0.05            # model - consensus, pre-registered B1 threshold
EDGE_CAP = 0.10            # repo winner's-curse cap (profit review 2026-07-23: 0.20 -> 0.10-0.12)
EV_CAP = 0.15
CLOSE_BUFFER_MIN = 20.0
MIN_PRIOR = 10
MAX_LOG_LAG_DAYS = 4       # newest game in the history may trail the event by at most this (in-season)
MAX_AGE_MIN = 45.0
OUT_GCS_PREFIX = "EVSweep/nhl_props_model"


def _placeholder_rows(entries: list, gl: pd.DataFrame) -> pd.DataFrame:
    """Upcoming-game rows (NaN outcomes) shaped like the canonical game log; negative synthetic game ids."""
    rows = []
    for e in entries:
        rows.append({"player_id": e["player_id"], "game_id": e["gid"], "date": e["date"], "team": e["team"], "opp": e["opp"],
                     "is_home": e["is_home"], "position": e["position"], "toi_sec": np.nan, "sog": np.nan,
                     "attempts": np.nan, "pp_attempts": np.nan, "goals": np.nan, "assists": np.nan})
    return pd.DataFrame(rows, columns=list(gl.columns))


def score_events(events: list, now: datetime, artifact: dict, gamelog: pd.DataFrame, player_info: dict,
                 resolver=None, covered=None, team_fn=None) -> tuple[list, dict]:
    """Pure core (no IO). -> (rows, stats). One row per (event, player, line, side-with-model-edge)."""
    meta = artifact["meta"]
    alpha = float(meta["nb_alpha"])
    stats = {"events": 0, "events_skipped": 0, "players_scored": 0, "unresolved_quotes": 0, "no_history": 0,
             "team_mismatch": 0, "stale_log": 0, "one_sided_only": 0}
    log_latest = pd.to_datetime(gamelog["date"]).max() if len(gamelog) else None

    per_event, entries, gid = [], [], -1
    for ev in events:
        away, home = team_fn(ev.get("away_team")), team_fn(ev.get("home_team"))
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            stats["events_skipped"] += 1
            continue
        if not away or not home:
            stats["events_skipped"] += 1
            continue
        use_resolver = resolver
        if resolver is not None and covered is not None and not ({away, home} <= set(covered)):
            use_resolver = None                                    # partial crosswalk: fail closed (see players.covered_teams)
        quotes, qmeta = parse_event_props(ev, "nhl", use_resolver, team_fn)
        sog = [q for q in quotes if q.stat == "sog" and q.player_id is not None]
        stats["unresolved_quotes"] += sum(1 for q in quotes if q.stat == "sog" and q.player_id is None)
        if not sog:
            continue
        stats["events"] += 1
        edate = start.astimezone(_ET).date().isoformat()
        if log_latest is None or (pd.Timestamp(edate) - log_latest).days > MAX_LOG_LAG_DAYS:
            stats["stale_log"] += 1                                # history too old for rolling features to mean anything
            continue
        ids = {}
        for q in sog:
            info = player_info.get(int(q.player_id))
            if info is None or info["team"] not in (away, home):
                stats["team_mismatch"] += 1
                continue
            if int(q.player_id) not in ids:
                ids[int(q.player_id)] = gid
                entries.append({"player_id": int(q.player_id), "gid": gid, "date": edate, "team": info["team"],
                                "opp": home if info["team"] == away else away, "is_home": int(info["team"] == home),
                                "position": info["position"]})
                gid -= 1
        per_event.append((ev, start, away, home, sog, ids))

    if not entries:
        return [], stats
    feat = build_features(pd.concat([gamelog, _placeholder_rows(entries, gamelog)], ignore_index=True))
    # one synthetic (negative) game id per (player, event) entry
    fut = feat[feat["game_id"] < 0].set_index(["player_id", "game_id"])
    fut["mu"] = train.predict_mu(artifact, fut.reset_index())

    rows = []
    for ev, start, away, home, sog, ids in per_event:
        mins = (start - now).total_seconds() / 60.0
        cons = build_consensus(sog)
        gkey = f"nhl:{start.astimezone(_ET).date().isoformat()}:{away}@{home}"
        by_key: dict = {}
        for q in sog:
            by_key.setdefault((q.player_norm, q.point), []).append(q)
        for (pn, pt), qs in by_key.items():
            pid = int(qs[0].player_id)
            if pid not in ids:
                continue
            f = fut.loc[(pid, ids[pid])]
            n_prior = float(f["n_prior"])
            if n_prior < MIN_PRIOR:
                stats["no_history"] += 1
                continue
            mu = float(f["mu"])
            p_over = float(p_over_vec(pt, mu, alpha))
            stats["players_scored"] += 1
            c = cons.get((pn, "sog", pt))
            real = [q for q in qs if q.book not in DFS_BOOKS]
            best = {"over": None, "under": None}
            for q in real:
                for side, price in (("over", q.over), ("under", q.under)):
                    if price is not None and (best[side] is None or american_to_decimal(price) > best[side][1]):
                        best[side] = (q.book, american_to_decimal(price), int(price), q.two_sided)
            if c is None:                                      # no two-sided real book: evidence only, never a flag
                stats["one_sided_only"] += 1
                side = "over" if p_over >= 0.5 else "under"
                b = best["over"]
                rows.append(_row(ev, gkey, start, mins, away, home, qs[0], pt, mu, alpha, n_prior, p_over, None, 0, None,
                                 "over", b, None, False, "NO_CONSENSUS", now))
                continue
            diff = p_over - c.p_over
            side = "over" if diff >= 0 else "under"
            b = best[side]
            edge = abs(diff)
            p_model_side = p_over if side == "over" else 1.0 - p_over
            ev_model = (p_model_side * b[1] - 1.0) if b else None
            if c.n_books < MIN_BOOKS:
                reason = "THIN_BOOKS"
            elif c.disp > MAX_DISP:
                reason = "DISPERSED"
            elif edge < MIN_EDGE:
                reason = "SMALL_EDGE"
            elif edge > EDGE_CAP:
                reason = "EDGE_TOO_HIGH"
            elif ev_model is None or ev_model > EV_CAP:
                reason = "EV_TOO_HIGH"
            elif ev_model <= 0:
                reason = "NEG_EV_AT_BEST_PRICE"
            elif mins < CLOSE_BUFFER_MIN:
                reason = "NEAR_START"
            else:
                reason = "FLAG"
            rows.append(_row(ev, gkey, start, mins, away, home, qs[0], pt, mu, alpha, n_prior, p_over, c.p_over, c.n_books,
                             c.disp, side, b, ev_model, reason == "FLAG", reason, now))
    return sorted(rows, key=lambda r: (-int(r["flag"]), -(r["edge"] or 0.0))), stats


def _row(ev, gkey, start, mins, away, home, q, pt, mu, alpha, n_prior, p_over, cons_p, n_books, disp, side, best, ev_model,
         flag, reason, now) -> dict:
    p_side = p_over if side == "over" else 1.0 - p_over
    c_side = None if cons_p is None else (cons_p if side == "over" else 1.0 - cons_p)
    return {
        "sport": "nhl", "stat": "sog", "ts": now.isoformat(), "game_key": gkey, "event_id": ev.get("id"),
        "commence": start.isoformat(), "mins_to_start": round(mins, 1), "away": away, "home": home,
        "player": q.player, "player_id": q.player_id, "line": pt, "mu": round(mu, 3), "nb_alpha": round(alpha, 3),
        "n_prior": int(n_prior), "p_model_over": round(p_over, 4),
        "cons_p_over": None if cons_p is None else round(cons_p, 4), "n_books": n_books,
        "disp": None if disp is None else round(disp, 4), "side": side.upper(), "p_model_side": round(p_side, 4),
        "cons_p_side": None if c_side is None else round(c_side, 4),
        "edge": None if c_side is None else round(abs(p_over - cons_p), 4),
        "best_book": best[0] if best else None, "american": best[2] if best else None,
        "decimal": round(best[1], 4) if best else None, "best_two_sided": bool(best[3]) if best else None,
        "ev_model": None if ev_model is None else round(ev_model, 4),
        "flag": bool(flag), "reason": reason, "log_only": True, "validated": False,
    }


# ----------------------------------------------------------------------------- IO shell
def load_player_info() -> dict:
    from mlb_core import storage
    from nhl.config import PLAYERS_MASTER
    df = storage.read_csv(PLAYERS_MASTER)
    return {int(r.player_id): {"team": r.team, "position": r.position_code} for r in df.itertuples()}


def run_once(model_dir: str, now: datetime | None = None, max_age_min: float = MAX_AGE_MIN, out_dir: str | None = None,
             gcs: bool = False, gamelog_loader=None) -> dict:
    """One pass. Refuses a stale snapshot (shared guard). Local CSV by default; GCS only with gcs=True."""
    from ev import sweep_props_alerts as sw
    now = now or datetime.now(timezone.utc)
    spec = sw.SPORTS["nhl"]
    events, meta = sw.read_latest_props(spec, now, max_age_min)
    if events is None:
        log.warning("nhl sog scorer skipped: %s", meta.get("reason"))
        return {"status": "skipped", **meta}
    resolver, covered = sw.load_resolver(spec)
    artifact = train.load(model_dir)
    gl = (gamelog_loader or (lambda: train.load_gamelog("masters")))()
    rows, stats = score_events(events, now, artifact, gl, load_player_info(), resolver, covered, sw.team_fn(spec))
    day = now.astimezone(_ET).date().isoformat()
    summary = {"status": "ok", "ts": now.isoformat(), **meta, **stats, "rows": len(rows),
               "flagged": sum(1 for r in rows if r["flag"]), "resolver": resolver is not None,
               "model_version": artifact["meta"]["version"], "log_only": True, "validated": False}
    log.info("NHL SOG SCORE %s", json.dumps(summary, default=str))
    if rows:
        csv = pd.DataFrame(rows).to_csv(index=False)
        name = f"model_rows_{now.strftime('%H%M%S')}.csv"
        if out_dir:
            d = os.path.join(out_dir, day)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, name), "w").write(csv)
        if gcs:
            from mlb_core import storage
            storage.write_bytes(csv.encode(), f"{OUT_GCS_PREFIX}/{day}/{name}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--max-age-min", type=float, default=MAX_AGE_MIN)
    ap.add_argument("--out-dir", default=os.path.join("local_artifacts", "nhl_sog_scores"))
    ap.add_argument("--gcs", action="store_true", help="also write evidence CSV to GCS (explicit opt-in)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run_once(a.model_dir, max_age_min=a.max_age_min, out_dir=a.out_dir, gcs=a.gcs), default=str, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
