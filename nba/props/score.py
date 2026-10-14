"""nba.props.score -- LOG-ONLY scoring of a banked NBA props snapshot with the NBA prop model.

NEVER posts to Discord, NEVER writes the `bets` table, NEVER registers in the MLB registry (CONTEXT s19/s21). It emits
EVIDENCE ROWS: for every (book quote, stat, point) it could score, the model's P(over), the model's edge versus that
book and versus the leave-in consensus of the real books, and a `flag` that only says "this row passed every guard".
Nothing here is validated: no NBA prop odds history exists, so no edge is claimed (see nba/props handoff for the forward
evaluation that would decide it from banked snapshots).

Reuses the sweep's plumbing so snapshot handling cannot drift: ev.sweep_props_alerts.read_latest_props (refuses a
snapshot older than --max-age-min), load_resolver (ESPN crosswalk), and ev.props.parse_event_props / build_consensus
(DFS apps excluded, team totals skipped, ambiguous names dropped). The evidence schema is a SUPERSET of the sweep's
book_outliers CSV, so ev/grade_props.py (first_sightings -> grade_frame -> summarize) grades these rows unchanged.

REFUSALS (a row is never scored on a guess):
  * history stale     : newest box score older than MAX_HISTORY_GAP_DAYS before the event (SportsBlaze has no 2026-27
                        season yet). `--allow-rollover` opts in to carrying last season's history into the new season;
                        such rows are scored but can never be flagged.
  * unmatched player  : quote not resolved to an ESPN id, ESPN id not bridged to a SportsBlaze id (ambiguous/unknown
                        -> dropped), or the player's roster team is not one of the event's two teams.
  * thin history      : fewer than SCORE_MIN_HISTORY_GAMES prior played games.
  * partial crosswalk : an event whose teams are not both in the ESPN crosswalk is not scored.
MINUTES / INJURIES: the model sees no lineup or injury data. Uncertainty is handled twice: the predictive distribution is
fitted heteroskedastically (train.py) so it widens when minutes are volatile, and rows whose minutes situation is unstable
(volatile minutes, recent absences, a returning player) are ABSTAINED (scored, never flagged). Follow-up data source:
ESPN's injuries endpoint (site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries) -- not built here.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ev.props import DFS_BOOKS, build_consensus, parse_event_props
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way
from nba.props import config as C
from nba.props import data as D
from nba.props import dist as Dd
from nba.props.features import FeatureContext, ID_COLS

log = logging.getLogger("nba.props.score")
SYSTEM = "NBA_PROPS_MODEL"


@dataclass
class Resources:
    ctx: FeatureContext
    models: dict                  # stat -> StatModel (or any object with .family, .predict(X)->(mu,var))
    bridge: object                # nba.props.identity.Bridge
    resolver: object              # PlayerResolver over the ESPN crosswalk (ids = ESPN ids)
    xw_team: dict                 # espn_id -> roster tricode
    covered: set                  # tricodes present in the crosswalk
    history: dict                 # data.history_status(...)
    team_fn: object = None
    model_version: str = C.MODEL_VERSION


# ----------------------------------------------------------------------------- guards
def season_of(day: pd.Timestamp) -> int:
    """NBA season start year for an ET game day (Aug-Dec -> that year, Jan-Jul -> previous)."""
    return int(day.year if day.month >= 8 else day.year - 1)


def check_history(history: dict, event_day: pd.Timestamp, allow_rollover: bool = False) -> tuple[bool, bool, str | None]:
    """-> (ok, rollover, reason). Refuses when the newest box score is too far behind the event."""
    last = history.get("last_date")
    if not last or not history.get("n_rows"):
        return False, False, "NO_HISTORY"
    last_day = pd.Timestamp(last)
    gap = (event_day.normalize() - last_day.normalize()).days
    if gap < 0:
        return True, False, None                        # event older than newest history (replay of a past snapshot)
    if gap <= C.MAX_HISTORY_GAP_DAYS:
        return True, False, None
    if allow_rollover and gap <= C.ROLLOVER_MAX_GAP_DAYS and season_of(event_day) > int(history.get("last_season") or 0):
        return True, True, None
    return False, False, (f"STALE_HISTORY: newest box score {last_day.date()} is {gap} days before {event_day.date()} "
                          f"(max {C.MAX_HISTORY_GAP_DAYS}); refusing to score")


def abstain_reason(f: pd.Series) -> str | None:
    """Minutes-uncertainty abstain rules (heuristic, unvalidated -- documented in the handoff). None = stable enough."""
    if not np.isfinite(f.get("n_played", np.nan)) or f["n_played"] < C.SCORE_MIN_HISTORY_GAMES:
        return "THIN_HISTORY"
    if np.isfinite(f.get("min_std10", np.nan)) and f["min_std10"] >= C.ABSTAIN_MIN_STD10:
        return "VOLATILE_MINUTES"
    if np.isfinite(f.get("missed_l10", np.nan)) and f["missed_l10"] >= C.ABSTAIN_MISSED_L10:
        return "RECENT_ABSENCES"
    tg = f.get("team_games_since_last", np.nan)
    if np.isfinite(tg) and tg >= 5:
        return "RETURNING_FROM_ABSENCE"
    r = f.get("min_ratio_l5_l20", np.nan)
    if np.isfinite(r) and (r < C.ABSTAIN_MIN_RATIO[0] or r > C.ABSTAIN_MIN_RATIO[1]):
        return "MINUTES_TREND_SHIFT"
    if f.get("team_changed", 0) == 1:
        return "NEW_TEAM"
    return None


# ----------------------------------------------------------------------------- math
def outcome_probs(point: float, mu: float, var: float, family: str, model=None) -> tuple[float, float, float]:
    """(P(over), P(under), P(push)) at a quoted point. Half-point lines have no push mass. When the model carries a PIT
    recalibration map (StatModel.p_over / p_push) the calibrated probabilities are used; a bare stub falls back to the
    uncalibrated family."""
    if model is not None and hasattr(model, "p_over") and hasattr(model, "p_push"):
        po, pp = float(model.p_over(point, mu, var)), float(model.p_push(point, mu, var))
    else:
        po, pp = float(Dd.p_over(point, mu, var, family)), float(Dd.p_push(point, mu, var, family))
    return po, max(0.0, 1.0 - po - pp), pp


def ev_at(p_win: float, p_push: float, american: int) -> float:
    dec = american_to_decimal(american)
    p_loss = max(0.0, 1.0 - p_win - p_push)
    return p_win * (dec - 1.0) - p_loss


# ----------------------------------------------------------------------------- core
def _empty_stats() -> dict:
    return {"events": 0, "events_skipped_teams": 0, "events_partial_crosswalk": 0, "groups": 0, "groups_scored": 0,
            "refused": {}, "rows": 0, "flagged": 0, "unmapped_keys": {}, "ambiguous_names": 0,
            "unknown_names": 0}


def _refuse(stats: dict, reason: str, n: int = 1):
    key = reason.split(":")[0]
    stats["refused"][key] = stats["refused"].get(key, 0) + n


def score_events(events: list, now: datetime, res: Resources, allow_rollover: bool = False,
                 snapshot_meta: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Score every banked event. Pure function of (events, history, models, crosswalk): no I/O."""
    from nba.teams import team_code
    tf = res.team_fn or team_code
    stats = _empty_stats()
    out: list = []
    for ev in events:
        away, home = tf(ev.get("away_team")), tf(ev.get("home_team"))
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            stats["events_skipped_teams"] += 1
            continue
        if not away or not home:
            stats["events_skipped_teams"] += 1
            continue
        stats["events"] += 1
        if not ({away, home} <= set(res.covered)):
            stats["events_partial_crosswalk"] += 1       # a missing roster could hide a namesake: do not score
            continue
        event_day = D.et_day(start)
        ok, rollover, why = check_history(res.history, event_day, allow_rollover)
        quotes, qmeta = parse_event_props(ev, "nba", res.resolver, tf)
        stats["ambiguous_names"] += qmeta["unresolved"]["ambiguous"]      # dropped inside parse_event_props
        stats["unknown_names"] += qmeta["unresolved"]["unknown"]
        for k, v in qmeta["unmapped_keys"].items():
            stats["unmapped_keys"][k] = stats["unmapped_keys"].get(k, 0) + v
        scoreable = [q for q in quotes if q.stat in res.models and not q.alt and q.book not in DFS_BOOKS]
        if not ok:
            _refuse(stats, why, len({(q.player_norm, q.stat, q.point) for q in scoreable}))
            continue
        cons = build_consensus([q for q in quotes if not q.alt], "proportional")
        mins = (start - now).total_seconds() / 60.0
        gk = f"nba:{event_day.date().isoformat()}:{away}@{home}"
        # ---- per-player features/predictions (one query row per (player) -- stats share the feature row)
        pids = sorted({q.player_id for q in scoreable if q.player_id is not None})
        qrows, qmap = [], {}
        for espn_id in pids:
            sb_id, why_b = res.bridge.sb_id(espn_id)
            if sb_id is None:
                _refuse(stats, f"UNMATCHED_{why_b}")
                continue
            team = res.xw_team.get(int(espn_id))
            if team not in (away, home):
                _refuse(stats, "TEAM_NOT_IN_EVENT")
                continue
            opp = home if team == away else away
            qmap[espn_id] = len(qrows)
            qrows.append({"player_id": sb_id, "date": event_day, "team": team, "opp": opp, "is_home": team == home,
                          "season_year": season_of(event_day), "season_type": "Regular Season", "_espn": espn_id})
        if not qrows:
            continue
        qdf = pd.DataFrame(qrows)
        feats = res.ctx.features(qdf[ID_COLS]).reset_index(drop=True)
        preds = {}
        for stat, m in res.models.items():
            mu, var = m.predict(feats)
            preds[stat] = (np.asarray(mu, float), np.asarray(var, float), m.family)
        abst = [abstain_reason(feats.iloc[i]) for i in range(len(feats))]
        # ---- quotes -> evidence rows
        for q in scoreable:
            if q.player_id is None:
                _refuse(stats, "UNMATCHED_name_not_resolved")
                continue
            if q.player_id not in qmap:
                continue                                  # already counted as refused above
            c = cons.get((q.player_norm, q.stat, q.point))
            stats["groups"] += 1
            if c is None or not q.two_sided:
                continue
            i = qmap[q.player_id]
            mu_a, var_a, fam = preds[q.stat]
            mu, var = float(mu_a[i]), float(var_a[i])
            po, pu, pp = outcome_probs(q.point, mu, var, fam, res.models[q.stat])
            fo, fu = devig_two_way(american_to_implied_prob(q.over), american_to_implied_prob(q.under), "proportional")
            cands = []
            for side, price, p_m, p_c, p_b in (("OVER", q.over, po, c.p_over, float(fo)),
                                               ("UNDER", q.under, pu, 1.0 - c.p_over, float(fu))):
                cands.append((ev_at(p_m, pp, price), side, price, p_m, p_c, p_b))
            m_ev, side, price, p_m, p_c, p_b = max(cands, key=lambda t: t[0])
            ab = abst[i]
            gap = abs(p_m - p_c)
            if rollover:
                reason = "ROLLOVER_HISTORY"
            elif ab:
                reason = f"ABSTAIN_{ab}"
            elif c.n_books < C.MIN_BOOKS:
                reason = "THIN_BOOKS"
            elif c.disp > C.MAX_DISP:
                reason = "HIGH_DISP"
            elif mins < C.CLOSE_BUFFER_MIN:
                reason = "NEAR_START"
            elif m_ev > C.EV_CAP or gap > C.EV_CAP:
                reason = "EV_TOO_HIGH"
            elif m_ev < C.MIN_EV:
                reason = "LOW_EV"
            else:
                reason = "FLAG"
            out.append({
                "sport": "nba", "system": SYSTEM, "ts": now.isoformat(), "game_key": gk, "event_id": ev.get("id"),
                "commence": start.isoformat(), "mins_to_start": round(mins, 1), "away": away, "home": home,
                "book": q.book, "player": q.player, "player_norm": q.player_norm, "player_id": q.player_id,
                "stat": q.stat, "point": q.point, "alt": False, "side": side, "american": int(price),
                "decimal": round(american_to_decimal(price), 4), "p_fair": round(p_c, 4), "ev": round(m_ev, 4),
                "n_books": c.n_books, "disp": round(c.disp, 4), "flag": reason == "FLAG", "reason": reason,
                "p_model": round(p_m, 4), "mu": round(mu, 3), "var": round(var, 3), "family": fam,
                "p_model_over": round(po, 4), "p_push": round(pp, 4), "p_cons_over": round(c.p_over, 4),
                "edge_vs_cons": round(p_m - p_c, 4), "edge_vs_book": round(p_m - p_b, 4),
                "sb_player_id": qdf.loc[i, "player_id"], "abstain": ab, "rollover": rollover,
                "history_last_date": res.history.get("last_date"), "model_version": res.model_version,
                "n_played": float(feats.loc[i, "n_played"]), "min_ewm8": round(float(feats.loc[i, "min_ewm8"]), 2),
            })
            stats["groups_scored"] += 1
    df = pd.DataFrame(out)
    if len(df):
        df = df.sort_values(["flag", "ev"], ascending=[False, False]).reset_index(drop=True)
    stats["rows"] = int(len(df))
    stats["flagged"] = int(df["flag"].sum()) if len(df) else 0
    return df, {**stats, **(snapshot_meta or {})}


# ----------------------------------------------------------------------------- I/O wrappers
def load_resources(local_dir: str | None, models_dir: str, xwalk: pd.DataFrame | None = None) -> Resources:
    """Box-score history + feature context + models + ESPN crosswalk/bridge. `local_dir` reads masters from disk;
    default is mlb_core.storage (GCS when GCS_BUCKET is set). Raises when a required model/history file is missing --
    a scorer that cannot load its inputs must refuse, not guess."""
    from mlb_core import storage
    from nba.data import espn_players as ep
    from nba.props.identity import build_bridge
    from nba.props.train import StatModel
    pb, gm = D.load_masters(local_dir)
    rows, tg = D.prepare(pb, gm, D.gather_tips(gm, local_dir))
    ctx = FeatureContext(rows, tg)
    models = {s: StatModel.load(s, models_dir) for s in C.STATS}
    if xwalk is None:
        xwalk = pd.read_csv(f"{local_dir}/players_xwalk.csv") if local_dir else storage.read_csv(ep.PLAYERS_XWALK)
    bridge = build_bridge(rows, xwalk)
    return Resources(ctx, models, bridge, ep.build_resolver(xwalk),
                     {int(r.espn_id): r.team for r in xwalk.itertuples(index=False)}, set(xwalk["team"].dropna()),
                     D.history_status(rows))


def run_once(res: Resources, now: datetime | None = None, max_age_min: float = C.MAX_SNAPSHOT_AGE_MIN,
             allow_rollover: bool = False, events: list | None = None, out_dir: str | None = None,
             write_gcs: bool = False) -> dict:
    """One pass over the newest banked NBA props snapshot (or `events` supplied directly). Returns a summary dict."""
    from ev.sweep_props_alerts import SPORTS, read_latest_props
    now = now or datetime.now(timezone.utc)
    meta: dict = {}
    if events is None:
        events, meta = read_latest_props(SPORTS["nba"], now, max_age_min)
        if events is None:
            log.warning("NBA model scoring skipped: %s", meta.get("reason"))
            return {"status": "skipped", **meta}
    df, stats = score_events(events, now, res, allow_rollover, meta)
    summary = {"status": "ok" if stats["rows"] else "no_rows", "ts": now.isoformat(), "system": SYSTEM,
               "validated": False, **stats,
               "history": res.history, "models": {s: m.family for s, m in res.models.items()}}
    day = D.et_day(now).date().isoformat()
    name = f"model_props_{now.strftime('%H%M%S')}.csv"
    if out_dir:
        from pathlib import Path
        p = Path(out_dir) / day
        p.mkdir(parents=True, exist_ok=True)
        if len(df):
            df.to_csv(p / name, index=False)
        (p / f"summary_{now.strftime('%H%M%S')}.json").write_text(json.dumps(summary, default=str, indent=1))
    if write_gcs:                                          # explicit flag only; never exercised in this PR
        from mlb_core import storage
        if len(df):
            storage.write_bytes(df.to_csv(index=False).encode(), f"{C.GCS_EVIDENCE_PREFIX}/{day}/{name}")
    log.info("NBA MODEL SCORE %s", json.dumps({k: v for k, v in summary.items() if k != "history"}, default=str))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LOG-ONLY: score the newest banked NBA props snapshot with the prop model.")
    ap.add_argument("--local-dir", help="masters/xwalk dir (default: mlb_core.storage)")
    ap.add_argument("--models-dir", default=C.DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--models-gcs", action="store_true",
                    help="download the model artifacts from GCS (Models/NBA/props/) into a temp dir first (explicit)")
    ap.add_argument("--out-dir", default="nba_props_evidence", help="local evidence dir (default)")
    ap.add_argument("--gcs", action="store_true", help="also write evidence to GCS (explicit; off by default)")
    ap.add_argument("--events-file", help="score this JSON list of ParlayAPI events instead of the banked snapshot")
    ap.add_argument("--max-age-min", type=float, default=C.MAX_SNAPSHOT_AGE_MIN)
    ap.add_argument("--allow-rollover", action="store_true",
                    help="score the first days of a new season from last season's history (rows are never flagged)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    models_dir = a.models_dir
    if a.models_gcs:
        import tempfile
        from nba.props.train import fetch_models
        models_dir = str(fetch_models(tempfile.mkdtemp(prefix="nba_props_models_")))
    res = load_resources(a.local_dir, models_dir)
    events = json.load(open(a.events_file)) if a.events_file else None
    out = run_once(res, max_age_min=a.max_age_min, allow_rollover=a.allow_rollover, events=events, out_dir=a.out_dir,
                   write_gcs=a.gcs)
    print(json.dumps({k: v for k, v in out.items() if k != "history"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
