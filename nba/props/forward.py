"""nba.props.forward -- the FORWARD evaluation that decides whether the NBA prop model has an edge. Offline numbers cannot.

PRE-REGISTERED PROTOCOL (fixed before any odds exist; do not tune thresholds after looking):
  population  : evidence rows from nba.props.score with flag=True (every guard passed), first sighting per
                (game, player, stat, point, side, book), line-shopped to the single highest-model-EV quote per
                (game, player, stat, point, side) -- the same shape mlb/analysis/backtest_market.backtest produces.
  grading     : ev/grade_props.py machinery (grade_frame by league player id; voids/pushes handled there).
  CLV         : entry price vs the SAME BOOK's SAME-SIDE price in the last banked props snapshot strictly before tip
                (mlb_core.odds.utils.clv_pct_from_prices, the repo's standard price-ratio CLV). Also reported: entry price
                priced against the closing CONSENSUS fair probability (EV at the close).
  verdict     : mlb.analysis.backtest_market.verdict(cand) -- low-edge (<= LOW_EDGE_MAX) CLV must clear
                mlb_core.risk.clv.clv_verdict (mean >= +2%, t > 2) with the edge ladder monotonic and the 10%+ bucket not
                significantly negative -> PROMOTE_CANDIDATE, else NO_EDGE, else INSUFFICIENT_N.
                min_n = BAKEOFF_MIN_N (30) for an early read; the paper -> live gate is clv_verdict's own 100 bets with
                mean CLV >= +2% and t > 2, over >= 2 weeks of slates, AND hit rate within 3 pct points of mean model prob.
  CAVEATS     : CLV is only as good as snapshot density (banking runs a few times a day: closing = last bank before tip,
                not the true close); one player's props across stats/books are correlated, so n counts QUOTES not
                independent bets (use per-player-game clusters before trusting any t-stat); ROI is reported but is NOT
                the gate (repo rule: CLV, not ROI).
Nothing here posts anywhere or writes the bets table.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ev.props import DFS_BOOKS, build_consensus, parse_event_props
from mlb_core.odds.utils import clv_pct_from_prices

log = logging.getLogger("nba.props.forward")


# ----------------------------------------------------------------------------- closing line
def close_index(events: list, resolver=None, team_fn=None) -> dict:
    """{(player_norm, stat, point, event_id): {'p_over': consensus fair P(over), 'n_books': n, 'books': {book: (over, under)}}}
    from one banked props snapshot (the closing one). Same parsing/consensus as the scorer, so keys line up."""
    from nba.teams import team_code
    tf = team_fn or team_code
    out: dict = {}
    for ev in events or []:
        quotes, _ = parse_event_props(ev, "nba", resolver, tf)
        quotes = [q for q in quotes if not q.alt]
        cons = build_consensus(quotes, "proportional")
        books: dict = {}
        for q in quotes:
            if q.book in DFS_BOOKS or not q.two_sided:
                continue
            books.setdefault((q.player_norm, q.stat, q.point), {})[q.book] = (q.over, q.under)
        for k, c in cons.items():
            out[(*k, ev.get("id"))] = {"p_over": c.p_over, "n_books": c.n_books, "books": books.get(k, {})}
    return out


def attach_clv(evidence: pd.DataFrame, closing: dict) -> pd.DataFrame:
    """Adds clv_pct (same book, same side, price ratio), clv_cons_pct (entry price vs closing consensus fair) and
    close_n_books. NaN where the quote was no longer posted at the close."""
    df = evidence.copy()
    clv, clv_c, nb = [], [], []
    for r in df.to_dict("records"):
        c = closing.get((r["player_norm"], r["stat"], float(r["point"]), r.get("event_id")))
        if c is None:
            clv.append(np.nan)
            clv_c.append(np.nan)
            nb.append(np.nan)
            continue
        side_i = 0 if str(r["side"]).upper() == "OVER" else 1
        same = c["books"].get(r["book"])
        price = same[side_i] if same else None
        clv.append(clv_pct_from_prices(int(r["american"]), int(price)) if price is not None else np.nan)
        p_close = c["p_over"] if side_i == 0 else 1.0 - c["p_over"]
        clv_c.append((float(r["decimal"]) * p_close - 1.0) * 100.0)
        nb.append(c["n_books"])
    df["clv_pct"], df["clv_cons_pct"], df["close_n_books"] = clv, clv_c, nb
    return df


# ----------------------------------------------------------------------------- candidates + verdict
def candidates(graded: pd.DataFrame, flagged_only: bool = True) -> pd.DataFrame:
    """Graded evidence (grade_props.grade_frame output, optionally with clv columns) -> the frame
    backtest_market.verdict expects: edge, clv_pct, won, decimal, roi, roi_cons, n_books. Line-shopped per
    (game, player, stat, point, side) to the highest-model-EV quote."""
    g = graded[graded["result"].isin(["win", "loss", "push"])].copy()
    if flagged_only:
        g = g[g["flag"].astype(bool)]
    if not len(g):
        return pd.DataFrame(columns=["edge", "clv_pct", "won", "decimal", "roi", "roi_cons", "n_books"])
    g = g.sort_values("ev", ascending=False).drop_duplicates(["game_key", "player_norm", "stat", "point", "side"])
    g["won"] = g["result"].map({"win": 1.0, "loss": 0.0, "push": 0.5})
    g["edge"] = g["edge_vs_cons"].astype(float)
    g["decimal"] = g["decimal"].astype(float)
    g["consensus_dec"] = 1.0 / g["p_fair"].astype(float).clip(lower=1e-3)       # fair (no-vig) consensus price
    g["roi"] = np.where(g["won"] == 1.0, g["decimal"] - 1.0, np.where(g["won"] == 0.5, 0.0, -1.0))
    g["roi_cons"] = np.where(g["won"] == 1.0, g["consensus_dec"] - 1.0, np.where(g["won"] == 0.5, 0.0, -1.0))
    if "clv_pct" not in g:
        g["clv_pct"] = np.nan
    return g.reset_index(drop=True)


def forward_verdict(cand: pd.DataFrame, min_n: int | None = None) -> dict:
    """The pre-registered decision. Returns the backtest_market.verdict dict plus the live T17 promotion check."""
    from mlb.analysis.backtest_market import BAKEOFF_MIN_N, verdict
    from mlb_core.risk.clv import clv_verdict
    v = verdict(cand, min_n=BAKEOFF_MIN_N if min_n is None else min_n)
    clv = cand["clv_pct"].dropna() if len(cand) else pd.Series(dtype=float)
    t = None
    if len(clv) > 1 and clv.std(ddof=1) > 1e-9:
        t = float(clv.mean() / (clv.std(ddof=1) / np.sqrt(len(clv))))
    v["t17"] = clv_verdict(float(clv.mean()) if len(clv) else None, t, int(len(clv)))
    v["hit_rate"] = float((cand["won"] == 1.0).mean()) if len(cand) else None
    v["mean_model_prob"] = float(cand["p_model"].mean()) if len(cand) and "p_model" in cand else None
    return v


# ----------------------------------------------------------------------------- storage helpers (read-only)
def load_evidence(prefix: str, day: str) -> pd.DataFrame:
    from mlb_core import storage
    keys = [k for k in storage.list_keys(f"{prefix}/{day}/") if "/model_props_" in k and k.endswith(".csv")]
    frames = [pd.read_csv(io.BytesIO(storage.read_bytes(k))) for k in sorted(keys)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def closing_snapshot(sport_key: str, date: str, commence_iso: str):
    """Newest banked props snapshot of `date` strictly before `commence_iso`, or None. (OddsAccum/{sport}/raw/{date}/props_HHMM.json)"""
    from mlb_core import storage
    from nba.config import ODDSACCUM_PREFIX
    start = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    best = None
    for k in storage.list_keys(f"{ODDSACCUM_PREFIX}/{sport_key}/raw/{date}/"):
        name = k.rsplit("/", 1)[-1]
        if not name.startswith("props_") or not name.endswith(".json"):
            continue
        hhmm = name[len("props_"):-len(".json")]
        try:   # the accumulator names snapshots by ET date + ET HHMM (nba/odds/accumulator.py::_now)
            ts = datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            continue
        if ts < start and (best is None or ts > best[0]):
            best = (ts, k)
    return None if best is None else json.loads(storage.read_bytes(best[1]))


# ----------------------------------------------------------------------------- one day, end to end
def run(day: str, prefix: str = None, provider=None, resolver=None, min_n: int | None = None) -> dict:
    """Grade one ET day of NBA-model evidence, attach CLV from the banked closing snapshots, return the verdict inputs.

    Needs the day's games FINAL (grade_props.nba_actuals, ESPN) and the day's banked props snapshots. Read-only: writes
    nothing. `provider` overrides the actuals source (tests); `resolver` is the ESPN PlayerResolver used for the closing
    snapshot (default: the crosswalk via ev.sweep_props_alerts.load_resolver)."""
    from ev import grade_props as gp
    from nba.props import config as C
    prefix = prefix or C.GCS_EVIDENCE_PREFIX
    raw = load_evidence(prefix, day)
    if raw.empty:
        return {"status": "no_evidence", "day": day}
    first = gp.first_sightings(raw)
    graded = gp.grade_frame(first, "nba", provider=provider)
    if resolver is None:
        from ev.sweep_props_alerts import SPORTS, load_resolver
        resolver, _ = load_resolver(SPORTS["nba"])
    close: dict = {}
    for (eid, commence, gk), _ in first.groupby(["event_id", "commence", "game_key"]):
        snap = closing_snapshot("basketball_nba", gk.split(":")[1], commence)
        if snap:
            close.update(close_index([e for e in snap if e.get("id") == eid], resolver))
    if close:
        graded = attach_clv(graded, close)
    else:
        graded = graded.assign(clv_pct=np.nan, clv_cons_pct=np.nan, close_n_books=np.nan)
    cand = candidates(graded)
    return {"status": "ok", "day": day, "graded": gp.summarize(graded),
            "quotes_with_close": int(graded["clv_pct"].notna().sum()), "n_candidates": int(len(cand)),
            "verdict": forward_verdict(cand, min_n), "validated": False}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Forward evaluation of the NBA prop model for one ET day (read-only).")
    ap.add_argument("--day", required=True, help="ET game day YYYY-MM-DD")
    ap.add_argument("--min-n", type=int, default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(a.day, min_n=a.min_n), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
