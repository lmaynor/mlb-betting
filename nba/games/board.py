"""
nba.games.board -- the NBA "our line vs the market" games board payload (EVSweep/nba_games/latest.json).

What it IS: for each upcoming NBA game on the banked ParlayAPI feed, the market consensus (modal spread / total, de-vigged
win probability) next to the walk-forward-evaluated game models' own numbers (fair spread, fair total, win probability, and a
fair ladder of alternate lines), with the difference in points.

What it is NOT: an edge or EV signal. docs/analysis/nba_game_models_2026-10-20.md: over 2021-25 (6,604 games) the closing line
out-predicts these models on moneyline, spread and totals every season, the incremental coefficient on (model - market) is ~0,
and betting the disagreement lost 3-7%. So the payload carries NO ev / edge / pick fields, `validated` is false, and every
consumer must show the accuracy note. `reliable` is false until both teams have `EARLY_SEASON_GAMES` games (ratings are last
season's before that); unreliable rows are shown but must not be highlighted or posted.

    PYTHONPATH=. python -m nba.games.board [--hoopr-dir D] [--csv lines.csv] [--out latest.json] [--no-write]
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from nba.games import data, models as M, score as S
from nba.teams import NAME_OF

log = logging.getLogger("nba.games.board")

ENGINE = "nba_games_ridge_v1"
BOARD_KEY = "EVSweep/nba_games/latest.json"
HORIZON_HOURS = 168.0
MAX_LINES_AGE_MIN = 480.0          # banked 17:35 / 22:35 / 01:00 UTC; older than this the lines are flagged stale
MIN_BOOKS = 2                      # fewer books quoting a game's moneyline -> not a market worth comparing to
LADDER_STEPS = tuple(range(-6, 7))  # alternate-line ladder around the market line, in points

# Walk-forward 2021-25 headline numbers (docs/analysis/nba_game_models_2026-10-20.md) shown next to every board.
ACCURACY = {
    "games": 6604, "seasons": "2021-2025",
    "moneyline_logloss": {"market": 0.5968, "model": 0.6197},
    "spread_rmse": {"market": 13.60, "model": 14.05},
    "total_rmse": {"market": 17.99, "model": 18.70},
    "verdict": "The closing line is more accurate than the model on every market and every season; "
               "the model's disagreement with the line has not been profitable.",
}
LABEL = ("Model lines vs market. Informational, NOT +EV and NOT a recommendation: in our 2021-25 walk-forward the market "
         "line beat this model on moneyline, spread and totals.")


def _f(x, nd: int = 4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def _american(p: float):
    """Fair American odds for a probability (no vig)."""
    if p is None or not np.isfinite(p) or p <= 0.001 or p >= 0.999:
        return None
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def _ladder(mu: float, sigma: float, line: float, kind: str) -> list:
    """Our fair probability at alternate lines around `line` (home handicap for spreads, points for totals). The market has no
    alt prices to compare to (ParlayAPI returns none), so this is a fair-price ladder, not a comparison."""
    out = []
    for step in LADDER_STEPS:
        ln = float(line) + step
        p = float(M.p_home_cover(mu, sigma, ln)) if kind == "spread" else float(M.p_over(mu, sigma, ln))
        out.append({"line": ln, "p": _f(p), "fair_american": _american(p)})
    return out


def row_from(r: pd.Series) -> dict:
    mu_m, mu_t = float(r["mu_margin"]), float(r["mu_total"])
    sp, tot = r.get("sp_line"), r.get("tot_line")
    has_sp, has_tot = pd.notna(sp), pd.notna(tot)
    row = {
        "game_key": f"{r['date']}_{r['away']}_{r['home']}", "date": r["date"], "start": r.get("scheduled"),
        "home": r["home"], "away": r["away"], "home_name": NAME_OF.get(r["home"]), "away_name": NAME_OF.get(r["away"]),
        "n_books": int(r["n_books_ml"]) if pd.notna(r.get("n_books_ml")) else 0,
        "games_min": int(r["games_min"]) if pd.notna(r.get("games_min")) else None,
        "reliable": not bool(r["early_season"]),
        "market": {
            "spread_home": _f(sp, 1) if has_sp else None, "total": _f(tot, 1) if has_tot else None,
            "home_win_p": _f(r.get("ml_p_home")), "n_books_spread": int(r["n_books_sp"]) if pd.notna(r.get("n_books_sp")) else 0,
            "n_books_total": int(r["n_books_tot"]) if pd.notna(r.get("n_books_tot")) else 0,
            "pinnacle_home_win_p": _f(r.get("pin_p_home")),
        },
        "model": {
            "margin": _f(mu_m, 1), "spread_home": _f(-mu_m, 1), "total": _f(mu_t, 1),
            "home_win_p": _f(r["p_ml"]), "sigma_margin": _f(r["sigma_margin"], 2), "sigma_total": _f(r["sigma_total"], 2),
        },
        "diff": {
            # + = model likes the HOME team more than the market does (points of margin / win-probability points)
            "margin_pts": _f(mu_m + float(sp), 1) if has_sp else None,
            "total_pts": _f(mu_t - float(tot), 1) if has_tot else None,
            "home_win_pp": _f(100 * (float(r["p_ml"]) - float(r["ml_p_home"])), 1) if pd.notna(r.get("ml_p_home")) else None,
        },
    }
    row["ladders"] = {
        "spread_home": _ladder(mu_m, float(r["sigma_margin"]), float(sp), "spread") if has_sp else [],
        "total": _ladder(mu_t, float(r["sigma_total"]), float(tot), "total") if has_tot else [],
    }
    return row


def build_payload(hist: pd.DataFrame, lines: pd.DataFrame, lines_as_of: datetime | None, now: datetime | None = None,
                  kind: str = "ridge") -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = {"sport": "nba", "engine": ENGINE, "validated": False, "label": LABEL, "accuracy": ACCURACY,
            "ts": now.isoformat(timespec="seconds"), "lines_as_of": lines_as_of.isoformat(timespec="seconds") if lines_as_of else None}
    age = (now - lines_as_of).total_seconds() / 60.0 if lines_as_of else None
    if lines is None or not len(lines):
        return {**base, "status": "unavailable", "stale": True, "lines_age_minutes": _f(age, 1), "count": 0, "games": []}
    out, _, _ = S.slate(hist, lines, kind, train_from=2008)
    start = pd.to_datetime(out["scheduled"], utc=True, errors="coerce")
    keep = out[(start > pd.Timestamp(now)) & (start <= pd.Timestamp(now + timedelta(hours=HORIZON_HOURS)))
               & (out["n_books_ml"].fillna(0) >= MIN_BOOKS)].copy()
    keep["scheduled"] = start[keep.index].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    games = [row_from(r) for _, r in keep.sort_values(["scheduled", "home"]).iterrows()]
    stale = age is None or age > MAX_LINES_AGE_MIN
    return {**base, "status": "ok" if games else "no_games", "stale": bool(stale), "lines_age_minutes": _f(age, 1),
            "count": len(games), "reliable_count": sum(g["reliable"] for g in games), "games": games}


def latest_lines():
    """(frame, as-of datetime) of the newest banked ParlayAPI NBA game-line snapshot."""
    from mlb_core import storage
    from nba.config import oddsaccum_csv_key
    ptr = json.loads(storage.read_bytes("OddsAccum/basketball_nba/latest_game_lines.json"))
    frame = storage.read_csv(oddsaccum_csv_key("basketball_nba", ptr["date"], "game_lines", ptr["hhmm"]))
    return frame, datetime.fromisoformat(ptr["timestamp"])


def write(payload: dict) -> None:
    from mlb_core import storage
    storage.write_bytes(json.dumps(payload, default=str).encode(), BOARD_KEY)


def read() -> dict | None:
    from mlb_core import storage
    if not storage.exists(BOARD_KEY):
        return None
    return json.loads(storage.read_bytes(BOARD_KEY).decode())


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hoopr-dir", default=None, help="offline hoopR mirror (default: GCS NBA/hoopr)")
    ap.add_argument("--csv", default=None, help="a banked game_lines CSV instead of the latest in GCS")
    ap.add_argument("--out", default=None, help="also write the payload to this local file")
    ap.add_argument("--no-write", action="store_true", help="do not write EVSweep/nba_games/latest.json")
    ap.add_argument("--post", action="store_true", help="ALSO post the once-a-day Discord digest (off by default)")
    ap.add_argument("--post-dry-run", action="store_true", help="build the Discord digest and print it, post nothing")
    a = ap.parse_args(argv)
    if a.csv:
        lines, as_of = pd.read_csv(a.csv), datetime.now(timezone.utc)
    else:
        lines, as_of = latest_lines()
    hist = data.load(a.hoopr_dir, None)
    payload = build_payload(hist, lines, as_of)
    if a.out:
        open(a.out, "w").write(json.dumps(payload, indent=1, default=str))
    if not a.no_write:
        write(payload)
    if a.post or a.post_dry_run:
        from nba.games import board_post
        res = board_post.post_digest(payload, dry_run=a.post_dry_run)
        log.info("nba board digest: %s", {k: v for k, v in res.items() if k != "embed"})
        if a.post_dry_run and res.get("embed"):
            print(json.dumps(res["embed"], indent=1))
    log.info("nba game board: status=%s games=%s reliable=%s lines_age=%s min", payload["status"], payload["count"],
             payload.get("reliable_count"), payload["lines_age_minutes"])
    return 0 if payload["status"] != "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
