"""
runners/run_1i.py — 1st Inning 3-way ML daily runner.

Uses NRFI v18/v17 per-half YRFI probabilities to derive game-level
first-inning 3-way market probabilities by comparing first-inning scores:
  P(away) = P(away runs > home runs)
  P(home) = P(home runs > away runs)
  P(draw) = P(away runs == home runs)

The binary half-inning model directly identifies no-score, away-only, and
home-only cells. When both teams score, this runner allocates that slice
across away/home/draw using historical first-inning run comparisons when
available, with a neutral fallback until richer run-count modeling lands.

Bets are logged with bet_type in {"1I_AWAY", "1I_HOME", "1I_DRAW"}.
settle_bets.py/_settle_nrfi already handles these bet types.

Active sizing is enabled; bets trigger when edge and Kelly sizing clear
configured gates.

run() is called by main.py.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# Active sizing enabled.
LOG_ONLY = False

_DEFAULT_BOTH_SCORE_SHARES = {"away": 1.0 / 3.0, "home": 1.0 / 3.0, "draw": 1.0 / 3.0}
_SCORING_MASTER_KEYS = (
    "Scoring/scoring_master.csv",
    "MLB/scoring_master.csv",
    "scoring_master.csv",
)


def _normalise_shares(shares: dict | None) -> dict:
    if not shares:
        return dict(_DEFAULT_BOTH_SCORE_SHARES)
    vals = {k: max(0.0, float(shares.get(k, 0.0))) for k in ("away", "home", "draw")}
    total = sum(vals.values())
    if total <= 0:
        return dict(_DEFAULT_BOTH_SCORE_SHARES)
    return {k: vals[k] / total for k in vals}


def _find_run_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lookup = {c.lower(): c for c in df.columns}
    return next((lookup[c] for c in candidates if c in lookup), None)


def _load_both_score_shares() -> dict:
    """Estimate P(away/home/draw | both teams score in the 1st inning)."""
    from mlb_core.storage import exists, read_csv

    for key in _SCORING_MASTER_KEYS:
        try:
            if not exists(key):
                continue
            df = read_csv(key)
        except Exception as exc:
            logger.warning("1I: could not read %s for both-score shares: %s", key, exc)
            continue

        inning_col = _find_run_col(df, ("inning", "inning_number", "inn"))
        if inning_col:
            df = df[pd.to_numeric(df[inning_col], errors="coerce") == 1]

        away_col = _find_run_col(df, ("away_runs", "away_r", "away_score", "away_runs_1i"))
        home_col = _find_run_col(df, ("home_runs", "home_r", "home_score", "home_runs_1i"))
        if not away_col or not home_col:
            logger.warning("1I: %s lacks first-inning away/home run columns; using fallback shares", key)
            continue

        runs = pd.DataFrame({
            "away": pd.to_numeric(df[away_col], errors="coerce"),
            "home": pd.to_numeric(df[home_col], errors="coerce"),
        }).dropna()
        both = runs[(runs["away"] > 0) & (runs["home"] > 0)]
        if both.empty:
            logger.warning("1I: %s has no both-score first innings; using fallback shares", key)
            continue

        shares = {
            "away": float((both["away"] > both["home"]).mean()),
            "home": float((both["home"] > both["away"]).mean()),
            "draw": float((both["away"] == both["home"]).mean()),
        }
        logger.info("1I: loaded both-score outcome shares from %s: %s", key, shares)
        return _normalise_shares(shares)

    logger.info("1I: using neutral both-score outcome shares")
    return dict(_DEFAULT_BOTH_SCORE_SHARES)


def _derive_3way_probs(
    p_away_score,
    p_home_score,
    p_nrfi_prob,
    both_score_shares: dict | None = None,
) -> pd.DataFrame:
    """Convert half-inning score/no-score probabilities into 3-way ML probs."""
    shares = _normalise_shares(both_score_shares)
    p_away_score = pd.Series(p_away_score, dtype=float).clip(0.0, 1.0)
    p_home_score = pd.Series(p_home_score, dtype=float).clip(0.0, 1.0)
    p_nrfi_prob = pd.Series(p_nrfi_prob, dtype=float).clip(0.0, 1.0)

    raw_away_only = p_away_score * (1.0 - p_home_score)
    raw_home_only = p_home_score * (1.0 - p_away_score)
    raw_both = p_away_score * p_home_score
    raw_yrfi = (raw_away_only + raw_home_only + raw_both).clip(lower=1e-9)

    p_yrfi_prob = 1.0 - p_nrfi_prob
    away_only = raw_away_only / raw_yrfi * p_yrfi_prob
    home_only = raw_home_only / raw_yrfi * p_yrfi_prob
    both = raw_both / raw_yrfi * p_yrfi_prob

    out = pd.DataFrame({
        "p_3way_away": away_only + both * shares["away"],
        "p_3way_home": home_only + both * shares["home"],
        "p_3way_draw": p_nrfi_prob + both * shares["draw"],
    })
    norm = out.sum(axis=1).clip(lower=1e-9)
    return out.div(norm, axis=0).clip(0.0, 1.0)


def _build_game_probs(cfg: dict, run_date: str) -> pd.DataFrame:
    """Build normalised 3-way first-inning probabilities for every game.

    Reuses NRFI model loading and feature building; no duplicate training.
    Returns DataFrame with columns:
        game_pk, away_team, home_team, p_3way_away, p_3way_home, p_3way_draw
    One row per game where both starters were scored.
    """
    from mlb.runners.run_nrfi import (
        _load_v18_ensemble, _score_v18,
        _load_halfinn_model, _score,
        _load_calibrator, _load_calibrator_by_key,
        _build_today_feature_rows,
        _V18_CALIBRATOR_KEY,
    )

    _v18_boosters, _v18_meta = _load_v18_ensemble()
    if _v18_boosters is not None:
        logger.info("1I: scoring with v18 ensemble (pitcher/lineup/context stacked)")
        calibrator = _load_calibrator_by_key(_V18_CALIBRATOR_KEY)
    else:
        logger.info("1I: v18 not found — scoring with v17")
        booster, features, feature_means = _load_halfinn_model(cfg)
        calibrator = _load_calibrator(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("1I: no candidate starter rows — skipping")
        return pd.DataFrame()

    if _v18_boosters is not None:
        p_half = _score_v18(_v18_boosters, _v18_meta, feat_df)
    else:
        p_half = _score(booster, features, feature_means, feat_df)

    feat_df = feat_df.copy()
    feat_df["p_half_yrfi"] = p_half

    pivot = feat_df.pivot_table(
        index=["game_pk", "away_team", "home_team"],
        columns="_side",
        values="p_half_yrfi",
        aggfunc="first",
    ).reset_index()
    pivot = pivot.dropna(subset=["home", "away"])
    if pivot.empty:
        logger.warning("1I: no games with both starters scored")
        return pd.DataFrame()

    pivot["model_yrfi_prob"] = 1.0 - (1.0 - pivot["home"]) * (1.0 - pivot["away"])
    pivot["model_nrfi_prob"] = 1.0 - pivot["model_yrfi_prob"]

    if calibrator is not None:
        try:
            raw_yrfi = pivot["model_yrfi_prob"].values.copy()
            in_range = (raw_yrfi >= calibrator.X_min_) & (raw_yrfi <= calibrator.X_max_)
            cal_yrfi = raw_yrfi.copy()
            if in_range.any():
                cal_yrfi[in_range] = calibrator.predict(raw_yrfi[in_range])
            pivot["model_yrfi_prob"] = cal_yrfi
            pivot["model_nrfi_prob"] = 1.0 - pivot["model_yrfi_prob"]
            pivot["model_nrfi_prob"] = pivot["model_nrfi_prob"].clip(0.05, 0.95)
            pivot["model_yrfi_prob"] = pivot["model_yrfi_prob"].clip(0.05, 0.95)
            logger.info(f"1I: isotonic calibrator applied to {int(in_range.sum())}/{len(raw_yrfi)} games")
        except Exception as e:
            logger.warning(f"1I: calibrator failed: {e} — using raw probs")

    # _side is pitcher-side: home pitcher faces away batters in the top half,
    # away pitcher faces home batters in the bottom half.
    p_away_score = pivot["home"]
    p_home_score = pivot["away"]
    probs = _derive_3way_probs(
        p_away_score,
        p_home_score,
        pivot["model_nrfi_prob"],
        both_score_shares=_load_both_score_shares(),
    )
    pivot["p_3way_away"] = probs["p_3way_away"].values
    pivot["p_3way_home"] = probs["p_3way_home"].values
    pivot["p_3way_draw"] = probs["p_3way_draw"].values

    logger.info(f"1I: {len(pivot)} games with 3-way probs computed")
    return pivot[["game_pk", "away_team", "home_team",
                   "p_3way_away", "p_3way_home", "p_3way_draw"]]


def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import sgo
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.dk_scraper import resolve_team
    from mlb_core.odds.sgo import extract_1i_3way_odds
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine

    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"1I: aborting — {_reason}")
        return pd.DataFrame()

    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "NRFI_Pro_System")
    if not _sok:
        msg = f"1I: aborting — stale NRFI feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error("1I", msg)
        return pd.DataFrame()
    logger.info("1I: sentinel ok -- %s", _sreason)

    game_probs = _build_game_probs(cfg, run_date)
    if game_probs.empty:
        return pd.DataFrame()

    events = sgo.load_snapshot(_SGO_KEY)
    if not events:
        logger.warning("1I: SGO snapshot empty")
        return pd.DataFrame()

    odds_by_event = extract_1i_3way_odds(events)
    if not odds_by_event:
        logger.warning("1I: no 1st-inning 3-way odds in snapshot")
        return pd.DataFrame()
    logger.info(f"1I: {len(odds_by_event)} events with 3-way 1st-inning odds")

    # Index odds by (away_abbrev, home_abbrev)
    by_abbrev: dict = {}
    for ev_id, info in odds_by_event.items():
        away_abbr = resolve_team(info["away_team"])
        home_abbr = resolve_team(info["home_team"])
        if not away_abbr or not home_abbr:
            continue
        by_abbrev[(away_abbr, home_abbr)] = {**info, "event_id": ev_id}

    _engine   = _make_engine("unused")
    _game_pks = list(game_probs["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched = prefetch_exposure(_engine, _game_pks, run_date, system="1I")
    _pending: dict[int, float] = {}

    results = []
    for _, row in game_probs.iterrows():
        key       = (row["away_team"], row["home_team"])
        odds_info = by_abbrev.get(key)
        if odds_info is None:
            continue

        mkt_away = american_to_implied_prob(odds_info["away_odds"])
        mkt_home = american_to_implied_prob(odds_info["home_odds"])
        mkt_draw = american_to_implied_prob(odds_info["draw_odds"])
        total = mkt_away + mkt_home + mkt_draw
        if not total or pd.isna(total) or total <= 0:
            continue

        # Proportional devig across all three legs
        fair_away = mkt_away / total
        fair_home = mkt_home / total
        fair_draw = mkt_draw / total

        p_away = float(row["p_3way_away"])
        p_home = float(row["p_3way_home"])
        p_draw = float(row["p_3way_draw"])

        edges = {
            "1I_AWAY": (p_away - fair_away, odds_info["away_odds"], p_away, fair_away),
            "1I_HOME": (p_home - fair_home, odds_info["home_odds"], p_home, fair_home),
            "1I_DRAW": (p_draw - fair_draw, odds_info["draw_odds"], p_draw, fair_draw),
        }
        best_bt = max(edges, key=lambda b: edges[b][0])
        edge, odds, model_prob, fair = edges[best_bt]

        if edge < cfg["min_edge"]:
            continue

        k_pct_val = kpct(model_prob, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, int(row["game_pk"]),
            _prefetched, _pending,
            cap_units=cfg.get("cap_units", 2.0),
        )
        raw_stake = kelly_stake(
            model_prob, odds,
            bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"],
            max_pct=cfg["max_kelly_pct"],
        )
        stake = min(raw_stake, _cap)
        kelly_triggered = (edge >= cfg["min_edge"]) and (stake > 0) and (not LOG_ONLY)
        if kelly_triggered and stake > 0:
            gp = int(row["game_pk"])
            _pending[gp] = _pending.get(gp, 0.0) + stake

        results.append({
            "player":      f"{row['away_team']} @ {row['home_team']}",
            "game_pk":     int(row["game_pk"]),
            "away_team":   row["away_team"],
            "home_team":   row["home_team"],
            "bet_type":    best_bt,
            "model_prob":  round(model_prob, 4),
            "market_prob": round(fair, 4),
            "edge":        round(edge, 4),
            "kelly_pct":   round(k_pct_val, 4),
            "odds":        odds,
            "stake":       stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
            "bookmaker":   odds_info.get("bookmaker"),
            "p_3way_away": round(p_away, 4),
            "p_3way_home": round(p_home, 4),
            "p_3way_draw": round(p_draw, 4),
        })

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results).sort_values("edge", ascending=False)
    logger.info(f"1I: {len(out)} qualifying bets (edge >= {cfg['min_edge']:.0%})")
    return out


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"1I run | type={run_type} | date={run_date} | log_only={LOG_ONLY}")

    from mlb.systems.NRFI_Pro_System.config_nrfi import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("1I: no qualifying bets today")
        post_bets([], system="1I", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="1I")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", False))
        bet_id = tracker.log_bet(
            game_date       = run_date,
            game_pk         = int(row["game_pk"]),
            player          = row["player"],
            away_team       = row["away_team"],
            home_team       = row["home_team"],
            bet_type        = row["bet_type"],
            model_prob      = row["model_prob"],
            market_prob     = row["market_prob"],
            edge            = row["edge"],
            kelly_pct       = row["kelly_pct"],
            odds            = row["odds"],
            stake           = row["stake"],
            kelly_triggered = triggered,
            paper           = cfg["PAPER"],
            book            = row.get("bookmaker"),
        )
        if bet_id == -1:
            continue
        bets_logged += 1
        if triggered:
            bet_rows.append(row.to_dict())

    log_suffix = " (log-only — calibration gate not cleared)" if LOG_ONLY else ""
    logger.info(f"1I: {bets_logged} bets logged{log_suffix}")
    post_bets(bet_rows, system="1I", run_date=run_date)

    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
