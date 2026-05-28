"""
runners/run_batter_tb.py — BATTER_TB Pro v1 daily runner.

Scores batter total-bases O/U props using the HR v6 model as a quality proxy.
Each batter's HR probability tilts a league-average discrete total-bases
distribution. This keeps common 0.5 lines anchored to "records at least one
total base" instead of treating total bases like a continuous Normal variable.

Ships LOG_ONLY (stake=0, kelly_triggered=False) until post-hoc calibration
confirms the proxy edge is real. Flip LOG_ONLY = False once ~100 settled bets
are in and the ROI/AUC checks out.

run() is called by main.py.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# Flip to False once ~100 settled bets confirm the proxy edge
LOG_ONLY = True

_LEAGUE_HR_RATE = 0.032
_TB_VALUES = (0, 1, 2, 3, 4, 5, 6, 7, 8)
_TB_BASE_PROBS = (0.360, 0.255, 0.170, 0.055, 0.090, 0.025, 0.025, 0.012, 0.008)
_TB_TILT_STRENGTH = 0.75


def _quality_mult_from_hr(model_hr_rate: float) -> float:
    quality_mult = 1.0 + 0.25 * (model_hr_rate - _LEAGUE_HR_RATE) / _LEAGUE_HR_RATE
    return max(0.6, min(1.6, quality_mult))


def _tb_distribution(model_hr_rate: float) -> dict[int, float]:
    """Return a discrete total-bases distribution tilted by HR quality."""
    import math

    quality_mult = _quality_mult_from_hr(float(model_hr_rate))
    base_mean = sum(v * p for v, p in zip(_TB_VALUES, _TB_BASE_PROBS))
    tilt = math.log(quality_mult)
    weights = [
        p * math.exp(_TB_TILT_STRENGTH * (v - base_mean) * tilt)
        for v, p in zip(_TB_VALUES, _TB_BASE_PROBS)
    ]
    total = sum(weights)
    if total <= 0:
        return dict(zip(_TB_VALUES, _TB_BASE_PROBS))
    return {v: w / total for v, w in zip(_TB_VALUES, weights)}


def _tb_p_over_under(line: float, model_hr_rate: float) -> tuple[float, float, float]:
    """Return (P over, P under, projected TB) for an O/U line."""
    dist = _tb_distribution(model_hr_rate)
    p_over = sum(p for tb, p in dist.items() if tb > line)
    p_under = sum(p for tb, p in dist.items() if tb < line)
    proj_tb = sum(tb * p for tb, p in dist.items())
    return float(p_over), float(p_under), float(proj_tb)


def _score_tb(predictions_df: pd.DataFrame, cfg: dict, run_date: str) -> pd.DataFrame:
    """Score BATTER_TB O/U bets from HR model predictions.

    predictions_df is the output of run_hr._build_predictions — one row per
    HR-priced batter with model_prob = P(HR). We map model_prob through a
    discrete TB distribution to get P(TB > line) / P(TB < line), then apply Kelly.
    """
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_batter_tb_odds
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    from runners.run_hr import _normalize_name

    events = _sgo.load_snapshot("Odds/sgo/latest.json")
    if not events:
        logger.warning("BATTER_TB: SGO snapshot empty")
        return pd.DataFrame()

    tb_map = extract_batter_tb_odds(events)
    if not tb_map:
        logger.warning("BATTER_TB: no TB odds in snapshot")
        return pd.DataFrame()
    logger.info(f"BATTER_TB: {len(tb_map)} players with TB odds")

    _engine   = _make_engine("unused")
    _game_pks = list(predictions_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched = prefetch_exposure(_engine, _game_pks, run_date, system="BATTER_TB")
    _pending: dict[int, float] = {}

    results = []
    for _, row in predictions_df.iterrows():
        norm_name = _normalize_name(row["player"])
        odds_info = tb_map.get(norm_name)
        if odds_info is None:
            continue
        line = odds_info.get("line")
        if line is None:
            continue

        model_hr_rate = float(row["model_prob"])
        p_over, p_under, proj_tb = _tb_p_over_under(line, model_hr_rate)

        mkt_over  = american_to_implied_prob(odds_info["over_odds"])
        mkt_under = american_to_implied_prob(odds_info["under_odds"])
        total = mkt_over + mkt_under
        if not total:
            continue
        fair_over  = mkt_over  / total
        fair_under = mkt_under / total

        edge_over  = p_over  - fair_over
        edge_under = p_under - fair_under

        if edge_over >= edge_under:
            side, edge, fair, odds, model_prob = "OVER",  edge_over,  fair_over,  odds_info["over_odds"],  p_over
        else:
            side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, odds_info["under_odds"], p_under

        if edge < cfg["min_edge"]:
            continue

        k_pct_val = kpct(edge, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, int(row["game_pk"]),
            _prefetched, _pending,
            cap_units=cfg.get("cap_units", 10.0),
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
            gp = int(row.get("game_pk", 0))
            _pending[gp] = _pending.get(gp, 0.0) + stake

        results.append({
            "player":          row["player"],
            "game_pk":         int(row["game_pk"]),
            "away_team":       row["away_team"],
            "home_team":       row["home_team"],
            "line":            float(line),
            "side":            side,
            "bet_type":        f"BATTER_TB_{side}_{line}",
            "proj_tb":         round(proj_tb, 4),
            "hr_quality_mult": round(_quality_mult_from_hr(model_hr_rate), 4),
            "model_prob":      round(model_prob, 4),
            "market_prob":     round(fair, 4),
            "edge":            round(edge, 4),
            "kelly_pct":       round(k_pct_val, 4),
            "odds":            odds,
            "stake":           stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
            "bookmaker":       odds_info.get("bookmaker"),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("edge", ascending=False)


def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"BATTER_TB run | type={run_type} | date={run_date} | log_only={LOG_ONLY}")

    from HR_Pro.config_hr import cfg
    from mlb_core.odds import sgo as _sgo
    from mlb_core.storage import check_build_sentinel
    from mlb_core.config import GCS_BUCKET
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets, post_error
    from runners.run_hr import _build_predictions as _hr_build_predictions

    # Snapshot freshness (quick pre-check before the expensive HR pipeline)
    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = _sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"BATTER_TB: aborting — {_reason}")
        return {"bets_logged": 0}

    # Shares the HR feature build sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "HR_Pro")
    if not _sok:
        msg = f"BATTER_TB: aborting — stale HR feature build: {_sreason}"
        logger.error(msg)
        post_error("BATTER_TB", msg)
        return {"bets_logged": 0}
    logger.info("BATTER_TB: sentinel ok -- %s", _sreason)

    # Run the full HR pipeline to get per-batter model_prob (TB quality proxy).
    # All HR-priced players are returned regardless of HR edge.
    hr_predictions = _hr_build_predictions(cfg, run_date)
    if hr_predictions.empty:
        logger.info("BATTER_TB: HR predictions empty — skipping")
        post_bets([], system="BATTER_TB", run_date=run_date)
        return {"bets_logged": 0}

    # TB-specific betting params (slightly wider edge threshold than HR)
    tb_cfg = {
        **cfg,
        "min_edge":     0.04,
        "kelly_fraction": 0.25,
        "min_kelly_pct":  0.005,
        "max_kelly_pct":  0.04,
        "cap_units":      10.0,
    }

    today_df = _score_tb(hr_predictions, tb_cfg, run_date)

    if today_df.empty:
        logger.info("BATTER_TB: no qualifying bets today")
        post_bets([], system="BATTER_TB", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="BATTER_TB")
    bets_logged = 0
    bet_rows    = []

    for _, row in today_df.iterrows():
        triggered = bool(row.get("kelly_triggered", False))
        bet_id = tracker.log_bet(
            game_date       = run_date,
            game_pk         = row.get("game_pk"),
            player          = row.get("player"),
            away_team       = row.get("away_team"),
            home_team       = row.get("home_team"),
            bet_type        = row.get("bet_type"),
            model_prob      = row.get("model_prob"),
            market_prob     = row.get("market_prob"),
            edge            = row.get("edge"),
            kelly_pct       = row.get("kelly_pct"),
            odds            = row.get("odds"),
            stake           = row.get("stake"),
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
    logger.info(f"BATTER_TB: {bets_logged} bets logged{log_suffix}")
    post_bets(bet_rows, system="BATTER_TB", run_date=run_date)

    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
