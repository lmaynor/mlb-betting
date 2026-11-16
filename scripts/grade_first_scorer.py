"""DEC-09 runner: grade the DEC-08 first-scorer classifier against its pre-registered bars.

Guard pattern mirrors scripts/bank_first_scorer.py: plan mode (default) reads the bank,
prints the funnel and accrual state, makes NO Decisions API calls and writes nothing.
Execute mode requires BOTH --execute AND FIRST_SCORER_GRADER_ENABLED=1; it makes the
per-game Decision 1 calls for priced games that have no banked classifier distribution
yet, and writes one run artifact (per-game records + grade output) to
FirstScorer/grades/. Classifier distributions are reused by event_id across runs (D8).

The GEMINI_API_KEY comes from the environment only (in production: GCP Secret Manager
secret 'gemini-api-key'); it is never read from a file and never logged.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mlb_core import storage  # noqa: E402
from ev import first_scorer_grader as G  # noqa: E402

logger = logging.getLogger("grade_first_scorer")
ENV_GATE = "FIRST_SCORER_GRADER_ENABLED"
GRADES_PREFIX = "FirstScorer/grades/"


def load_starters(storage, sport: str) -> dict:
    """NBA actual-starter flags keyed (game_date, player_key) from banked box scores.
    Returns {} for NFL or when the box-score shape is unusable (flagged by the caller)."""
    if sport != G.SPORT_NBA:
        return {}
    frames = []
    for k in sorted(storage.list_keys("NBA/hoopr/player_boxscores/")):
        if k.endswith(".parquet"):
            frames.append(storage.read_parquet(k))
    if not frames:
        return {}
    import pandas as pd
    box = pd.concat(frames, ignore_index=True)
    name_col = next((c for c in ("athlete_display_name", "athlete_name", "player_name")
                     if c in box.columns), None)
    if name_col is None or "starter" not in box.columns or "game_date" not in box.columns:
        logger.warning("box scores lack expected columns: %s", list(box.columns))
        return {}
    out = {}
    for _, r in box.iterrows():
        out[(str(r["game_date"]), G.normalize_player(r[name_col]))] = bool(r["starter"])
    return out


def load_prior_classifier(storage) -> dict:
    """{event_id: {player_key: prob}} banked by earlier execute runs (D8 reuse)."""
    prior = {}
    for k in sorted(storage.list_keys(GRADES_PREFIX)):
        if not k.endswith(".json"):
            continue
        try:
            art = json.loads(storage.read_bytes(k))
        except Exception:
            continue
        for rec in art.get("records", []):
            if rec.get("clf") and rec.get("event_id"):
                prior[rec["event_id"]] = rec["clf"]
    return prior


def run_sport(storage, sport: str, client=None, execute: bool = False,
              prior: dict | None = None) -> dict:
    games, funnel = G.assemble_games(storage, sport)
    outcomes = G.load_outcomes(storage, sport)
    starters = load_starters(storage, sport)
    prior = prior or {}
    funnel.update({"settled": 0, "graded_ll": 0, "scorer_unlisted": 0,
                   "classifier_calls": 0, "classifier_reused": 0,
                   "classifier_abstentions": 0, "join_failures": 0})
    records, staking_records, stored_records = [], [], []
    sensitivity = []          # NBA first_point variant, book/base arms only (D2)
    for game in games:
        if game.get("boards") is None or "listed" not in game:
            continue
        game_starters = None
        if starters:
            game_starters = {G.player_key(sport, r["player"]):
                             starters.get((game["date_et"], G.normalize_player(r["player"])), False)
                             for r in game["listed"]}
        clf = prior.get(game["event_id"])
        if clf is not None:
            funnel["classifier_reused"] += 1
        elif execute and client is not None:
            clf = G.call_classifier(client, game, outcomes, sport, game_starters)
            if clf is None:
                funnel["classifier_abstentions"] += 1
            else:
                funnel["classifier_calls"] += 1
        outcome = G.join_outcome(game, outcomes, sport)
        stored = {"event_id": game["event_id"], "sport": sport,
                  "date_et": game["date_et"], "commence_time": game["commence_time"],
                  "home_team": game["home_team"], "away_team": game["away_team"],
                  "grading_book": game.get("grading_book"),
                  "n_listed": len(game["listed"]), "clf": clf}
        stored_records.append(stored)
        if outcome is None:
            if game.get("home_team") not in (None,) and G.TEAM_ABBREV[sport].get(game.get("home_team")):
                pass  # simply not settled yet / not in the outcomes bank
            else:
                funnel["join_failures"] += 1
            continue
        funnel["settled"] += 1
        scorer = G.scorer_key(sport, outcome, "fg")
        book = G.book_fairs(game, sport, "power")
        rec = {"event_id": game["event_id"], "date_et": game["date_et"],
               "commence_time": game["commence_time"], "scorer": scorer,
               "book": book, "book_prop": G.book_fairs(game, sport, "proportional"),
               "base": G.base_rates(game, outcomes, sport, "fg"), "clf": clf}
        if clf:
            top = max(clf, key=lambda pk: clf[pk])
            rec["top_choice"] = top
            rec["top_decimal"] = game["best_price"][top][0]
            staking_records.append(rec)
        if scorer is not None and scorer in book:
            if clf:
                records.append(rec)
                funnel["graded_ll"] += 1
        else:
            funnel["scorer_unlisted"] += 1
        if sport == G.SPORT_NBA:
            scorer_pt = G.scorer_key(sport, outcome, "point")
            book_pt = book  # same listed set & prices; only settlement differs
            base_pt = G.base_rates(game, outcomes, sport, "point")
            if scorer_pt is not None and scorer_pt in book_pt:
                sensitivity.append({
                    "date_et": game["date_et"],
                    "ll_book_point": G.log_loss(book_pt[scorer_pt]),
                    "ll_base_point": G.log_loss(base_pt[scorer_pt])})
    result = G.grade(records, sport, staking_records)
    result["funnel"] = funnel
    result["records"] = stored_records
    if sensitivity:
        n_s = len(sensitivity)
        result["sensitivity_first_point"] = {
            "n": n_s,
            "mean_ll_book": sum(s["ll_book_point"] for s in sensitivity) / n_s,
            "mean_ll_base": sum(s["ll_base_point"] for s in sensitivity) / n_s}
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="make Decisions calls and write the run artifact (env gate also required)")
    ap.add_argument("--sport", choices=[G.SPORT_NBA, G.SPORT_NFL], default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    execute = bool(args.execute and os.environ.get(ENV_GATE) == "1")
    if args.execute and not execute:
        logger.warning("execute requested but %s != 1: running in PLAN mode", ENV_GATE)
    client = None
    if execute:
        from ev.decisions_client import DecisionsClient
        client = DecisionsClient()  # key from GEMINI_API_KEY env only; raises if unset
    prior = load_prior_classifier(storage) if execute else {}
    sports = [args.sport] if args.sport else [G.SPORT_NBA, G.SPORT_NFL]
    artifact = {"run_at": datetime.now(timezone.utc).isoformat(), "execute": execute,
                "sports": {}}
    for sport in sports:
        res = run_sport(storage, sport, client=client, execute=execute, prior=prior)
        artifact["sports"][sport] = res
        print(json.dumps({sport: {k: v for k, v in res.items() if k != "records"}},
                         indent=1, default=str))
    if execute:
        key = f"{GRADES_PREFIX}dec09_grade_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        flat_records = [r for s in artifact["sports"].values() for r in s.pop("records")]
        artifact["records"] = flat_records
        storage.write_bytes(json.dumps(artifact, default=str).encode(), key)
        logger.info("wrote %s", key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
