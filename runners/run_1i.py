"""
runners/run_1i.py — 1st Inning 3-way ML daily runner.

Uses NRFI v18/v17 per-half YRFI probabilities to derive game-level
first-inning 3-way market probabilities:
  P(away scores, home doesn't) = p_away * (1 - p_home)
  P(home scores, away doesn't) = p_home * (1 - p_away)
  P(neither scores / draw)     = (1 - p_away) * (1 - p_home)

The three raw probabilities are normalised to sum to 1 by dividing by
their sum (which excludes P(both score), a case covered by no leg).

Bets are logged with bet_type in {"1I_AWAY", "1I_HOME", "1I_DRAW"}.
settle_bets.py/_settle_nrfi already handles these bet types.

Ships LOG_ONLY (stake=0, kelly_triggered=False) until calibration confirms
edge. Flip LOG_ONLY = False once ~100 settled bets are in.

run() is called by main.py.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# Flip to False once ~100 settled bets confirm calibration
LOG_ONLY = True


def _build_game_probs(cfg: dict, run_date: str) -> pd.DataFrame:
    """Build normalised 3-way first-inning probabilities for every game.

    Reuses NRFI model loading and feature building; no duplicate training.
    Returns DataFrame with columns:
        game_pk, away_team, home_team, p_3way_away, p_3way_home, p_3way_draw
    One row per game where both starters were scored.
    """
    from runners.run_nrfi import (
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

    # 3-way raw probs from per-half independence assumption
    p_away_raw = pivot["away"] * (1.0 - pivot["home"])
    p_home_raw = pivot["home"] * (1.0 - pivot["away"])
    p_draw_raw = pivot["model_nrfi_prob"]

    # Normalise so the three legs sum to 1 (drops the P(both score) slice)
    norm_factor = (p_away_raw + p_home_raw + p_draw_raw).clip(lower=1e-9)
    pivot["p_3way_away"] = p_away_raw / norm_factor
    pivot["p_3way_home"] = p_home_raw / norm_factor
    pivot["p_3way_draw"] = p_draw_raw / norm_factor

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

        k_pct_val = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, int(row["game_pk"]),
            _prefetched, _pending,
            cap_units=cfg.get("cap_units", 2.0),
        )
        raw_stake = kelly_stake(
            edge, odds,
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

    from NRFI_Pro_System.config_nrfi import cfg
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
