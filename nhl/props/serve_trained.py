"""Train and score the servable NHL prop models.

    PYTHONPATH=. python -m nhl.props.serve_trained train [--seasons 2022,...,2026] [--out-dir DIR] [--write]
    PYTHONPATH=. python -m nhl.props.serve_trained score [--hours 72] [--no-write]

train   reads the NHL masters + shots (GCS via mlb_core.storage, or MLB_BASE_DATA for a local copy), builds the walk-forward xG
        tables (nhl.xg.build), the training frames (expected-starter mode), fits one final model per stat and saves the bundle:
        to --out-dir (local) always when given, to `Models/nhl_props/{version}/` + `latest.json` ONLY with --write.
score   loads the bundle named by `Models/nhl_props/latest.json`, builds pre-game features for every game in the next --hours
        (nhl.props.live_features: the training feature code on placeholder rows), predicts, and writes `EVSweep/nhl_props/
        trained_latest.json` (mu / alpha per player x game x stat; points = goals + assists) unless --no-write. The board reads that
        file only when NHL_TRAINED_PROPS=1 (ev.daily_projections.load_trained) and ignores it after 36 h.

Neither command deploys, schedules or bets anything. Everything the models cannot know (confirmed starters, lineups, injuries)
is listed in nhl.props.live_features.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from nhl.props import live_features as LF
from nhl.props import trained as T
from nhl.props import xgfeat as X
from nhl.xg import build as XB
from nhl.xg import data as D

log = logging.getLogger("nhl.props.serve_trained")
_ET = ZoneInfo("America/New_York")
OUT_KEY = "EVSweep/nhl_props/trained_latest.json"
FIRST_SEASON = 2021


def _peak_mb() -> float:
    """Peak resident set of this process in MB (ru_maxrss is KB on Linux, bytes on macOS)."""
    import resource
    import sys
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024.0 * 1024.0) if sys.platform == "darwin" else r / 1024.0


def xg_tables(shots: pd.DataFrame) -> dict:
    r = XB.build(shots)
    return {"skater": r["skater_game"], "goalie": r["goalie_game"], "team": r["team_game"]}


def training_frames(masters: dict, xg: dict) -> dict:
    tt = X.team_table(masters["games"], xg["team"])
    gt = X.goalie_table(masters["games"], masters["goalies"], xg["goalie"])
    st = X.skater_table(masters["games"], masters["skaters"], xg["skater"])
    return {"saves": X.saves_features(gt, tt), "skater": X.skater_features(st, tt, gt, "expected")}


def build_payload(preds: pd.DataFrame, games: pd.DataFrame, version: str, trained_through: str,
                  now: datetime) -> dict:
    """Predictions + the placeholder game table -> the JSON the board reads."""
    gk = {int(r.game_id): f"nhl:{r.date}:{r.away_abbr}@{r.home_abbr}" for r in games.itertuples(index=False)} if len(games) else {}
    rows = [{"player_id": int(r.player_id), "name": r.name, "team": r.team, "opp": r.opp, "date": str(r.date)[:10],
             "game_key": gk.get(int(r.game_id)), "stat": r.stat, "mu": round(float(r.mu), 5), "alpha": round(float(r.alpha), 5)}
            for r in preds.itertuples(index=False)]
    return {"engine": "nhl_trained_v1", "version": version, "trained_through": trained_through, "ts": now.isoformat(),
            "validated": False, "count": len(rows), "rows": rows}


def score(now: datetime, bundle: dict, masters: dict, xg: dict, upcoming: list, exclude=frozenset()) -> dict:
    live = LF.build_live(masters, xg, upcoming, exclude=exclude)
    preds = T.predict_live(bundle, live)
    man = bundle["manifest"]
    return build_payload(preds, live["games"], man["version"], man["trained_through"], now)


def schedule_upcoming(now: datetime, hours: float) -> list:
    """Unplayed NHL games starting within `hours`: [{date (ET), home, away}] via the board's ESPN schedule (canonical codes)."""
    from ev import daily_projections as dp
    return [{"date": g["start"].astimezone(_ET).date().isoformat(), "home": g["home"], "away": g["away"]}
            for g in dp.schedule_games("nhl", now, hours)]


def off_roster_ids(masters: dict, now: datetime) -> frozenset:
    """Player ids a COMPLETE active roster does not carry (retired, released, sent down): never scored. {} on any failure."""
    try:
        from ev import rosters
        from mlb_core.data.cockpit_form import norm_name
        r = rosters.load("nhl", now, write=False)
        if r is None or r.partial:
            return frozenset()
        sk = masters["skaters"].sort_values("date").drop_duplicates("player_id", keep="last")
        out = {int(p) for p, n, t in zip(sk["player_id"], sk["name"], sk["team_abbr"]) if r.lookup(norm_name(n), t)[0] == "off"}
        return frozenset(out)
    except Exception as exc:                                                            # noqa: BLE001
        log.info("roster exclusion unavailable: %s", exc)
        return frozenset()


def _load_inputs(seasons, data_dir: str | None = None) -> tuple:
    masters = D.load_masters(data_dir)
    shots = D.load_shots(data_dir, seasons=seasons)
    return masters, shots


def cmd_train(a) -> int:
    t0 = datetime.now(timezone.utc)
    seasons = [int(s) for s in a.seasons.split(",")] if a.seasons else range(FIRST_SEASON, datetime.now().year + 1)
    masters, shots = _load_inputs(seasons, a.data_dir)
    xg = xg_tables(shots)
    bundle = T.train_bundle(training_frames(masters, xg))
    man = bundle["manifest"]
    log.info("trained %s through %s", man["version"], man["trained_through"])
    for stat, art in bundle["models"].items():
        m = art["meta"]
        log.info("%s: n_train=%d val NegBin loglik %.4f (constant-mean %.4f) alpha %.3f", stat, m["n_train"],
                 m["val_nb_loglik"], m["val_nb_loglik_constant_mean"], m["nb_alpha"])
    if a.out_dir:
        def w_local(key, b):
            path = os.path.join(a.out_dir, key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b)
        T.save_bundle(bundle, w_local, publish=True)
        log.info("bundle written under %s/%s", a.out_dir, T.PREFIX)
    if a.write:
        from mlb_core import storage
        v = T.save_bundle(bundle, lambda k, b: storage.write_bytes(b, k))
        log.info("published %s/%s and latest.json", T.PREFIX, v)
    else:
        log.info("not published to storage (pass --write)")
    log.info("RESOURCES train: %.0f s, peak RSS %.0f MB", (datetime.now(timezone.utc) - t0).total_seconds(), _peak_mb())
    return 0


def cmd_score(a) -> int:
    from mlb_core import storage
    now = datetime.now(timezone.utc)
    bundle = T.load_bundle(storage.read_bytes) if not a.bundle_dir else T.load_bundle(
        lambda k: open(os.path.join(a.bundle_dir, k), "rb").read())
    seasons = range(FIRST_SEASON, datetime.now().year + 1)          # the SAME seasons training built xG over (no skew)
    masters, shots = _load_inputs(seasons)
    xg = xg_tables(shots)
    upcoming = schedule_upcoming(now, a.hours)
    log.info("%d upcoming games in %.0f h", len(upcoming), a.hours)
    if not upcoming:
        log.info("no games in the window: nothing scored, the last predictions file is left as it is")
        return 0
    payload = score(now, bundle, masters, xg, upcoming, off_roster_ids(masters, now))
    log.info("%d predictions (version %s)", payload["count"], payload["version"])
    if a.no_write:
        print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=1))
    else:
        storage.write_bytes(json.dumps(payload).encode(), OUT_KEY)
        log.info("wrote %s", OUT_KEY)
    log.info("RESOURCES score: %.0f s, peak RSS %.0f MB", (datetime.now(timezone.utc) - now).total_seconds(), _peak_mb())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--seasons", default=None, help="comma list of shot-master seasons to read (default 2021..current)")
    t.add_argument("--data-dir", default=None, help="read a local copy ({dir}/NHL/...) instead of storage")
    t.add_argument("--out-dir", default=None)
    t.add_argument("--write", action="store_true", help="publish the bundle to storage (default: do not)")
    s = sub.add_parser("score")
    s.add_argument("--hours", type=float, default=72.0)
    s.add_argument("--bundle-dir", default=None, help="load the bundle from a local directory instead of storage")
    s.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return cmd_train(a) if a.cmd == "train" else cmd_score(a)


if __name__ == "__main__":
    raise SystemExit(main())
