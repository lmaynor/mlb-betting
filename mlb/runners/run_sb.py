"""
runners/run_sb.py -- SB (stolen base) Pro v1 daily runner.

Odds source: ParlayAPI/SGO snapshot (extract_stolen_base_odds) -- confirmed
live 2026-08-20 (player_stolen_bases, 11 catalog books, 5-6 live per game).
Feature source: SB_Pro_System/data/model_features.csv
Model: xgb_sb_v1.json -- count:poisson -> lambda (expected SB/game)
Scoring: P(SB > line) = 1 - NegBin_CDF(floor(line), lambda, nb_alpha)

LOG_ONLY = True: brand-new system, zero settled bets. Every prediction is
still logged (the "log every scored prediction" contract every other
system follows); kelly_triggered is structurally always False here until
the 200-bet paper gate is cleared and this flag is flipped by hand.

Two devig paths, unlike every other O/U prop runner -- confirmed live that
this market genuinely ships both shapes (see sgo.extract_stolen_base_odds
docstring): devig_two_way for a real Over/Under quote, devig_unilateral
(HR's pattern) for a one-sided "Yes"-only quote.

run() is called by main.py.
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from mlb_core.risk.threshold_bets import score_threshold_bet

logger = logging.getLogger(__name__)

# New system, no settled bets yet. Flip to False only after the 200-bet
# paper gate clears (see handoffs/scope_stolen_base_model_2026-08-20.md s7).
LOG_ONLY = True


# -- Helpers -------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


def _negbin_p_over(line: float, mu: float, nb_alpha: float) -> float:
    """P(X > line) for NegBin(mu, alpha). Degrades to Poisson if alpha <= 0."""
    k = int(math.floor(line))
    if mu <= 0:
        return 0.5
    if nb_alpha <= 0:
        from scipy.stats import poisson
        return float(1.0 - poisson.cdf(k, mu))
    from scipy.stats import nbinom
    n = 1.0 / nb_alpha
    p = n / (n + mu)
    return float(1.0 - nbinom.cdf(k, n, p))


def _negbin_p_under(line: float, mu: float, nb_alpha: float) -> float:
    k = int(math.floor(line))
    if mu <= 0:
        return 0.5
    if nb_alpha <= 0:
        from scipy.stats import poisson
        return float(poisson.cdf(k, mu))
    from scipy.stats import nbinom
    n = 1.0 / nb_alpha
    p = n / (n + mu)
    return float(nbinom.cdf(k, n, p))


# -- Model + calibrator load ----------------------------------------------------

def _load_model(cfg: dict) -> tuple[xgb.Booster, list[str], dict, float]:
    """Returns (booster, features, feature_means, nb_alpha)."""
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import download_model, read_bytes

    booster = xgb.Booster()
    with tempfile.TemporaryDirectory() as tmpdir:
        if GCS_BUCKET:
            local = download_model(cfg["gcs_model_xgb"], Path(tmpdir) / "xgb_sb.json")
            booster.load_model(str(local))
            meta_raw = read_bytes(cfg["gcs_model_meta"])
        else:
            booster.load_model(cfg["model_xgb"])
            meta_raw = Path(cfg["model_meta"]).read_bytes()

    meta = json.loads(meta_raw)
    features = meta.get("features")
    if not features:
        raise RuntimeError("model_meta missing 'features' key")
    nb_alpha      = float(meta.get("nb_alpha", 0.10))
    feature_means = meta.get("feature_means", {}) or {}
    booster.best_ntree_limit = meta.get("best_iteration", 0)
    logger.info(
        f"SB model loaded | features={len(features)} | "
        f"nb_alpha={nb_alpha:.4f} | MAE={meta.get('mae_oos', '?')}"
    )
    return booster, features, feature_means, nb_alpha


def _load_calibrator(cfg: dict):
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_bytes, exists

    gcs_key = cfg.get("gcs_calibrator")
    if GCS_BUCKET and gcs_key and exists(gcs_key):
        try:
            return pickle.loads(read_bytes(gcs_key))
        except Exception as e:
            logger.warning(f"SB calibrator load failed: {e}")
            return None
    local_path = cfg.get("calibrator")
    if local_path and Path(local_path).exists():
        try:
            with open(local_path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"SB calibrator load failed: {e}")
    return None


# -- Today's feature rows --------------------------------------------------------

def _build_today_feature_rows(cfg: dict, run_date: str) -> pd.DataFrame:
    """
    Build feature rows for today's batter slate.

    Same discipline as BATTER_HITS/BATTER_TB: confirmed lineups only, NO
    historical-team fallback (see CONTEXT.md's own rule -- this is the exact
    market class that produced the 2026-06 fake-P&L incident).

    Catcher identity is resolved from the SAME confirmed-lineup pull used
    for batting order -- no extra API call. _get_lineup_for_game() already
    captures a `position` field per player (see mlb_core/data/lineups.py);
    filtering to "C" gives today's starting catcher per team side, exactly
    like the historical catcher_identity_master backfill does from the same
    underlying boxscore data.
    """
    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import read_csv
    from mlb_core.data.lineups import get_today_schedule, _pull_lineup_date

    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.warning(f"SB: no games for {run_date}")
        return pd.DataFrame()

    feat_key = cfg.get("gcs_model_features", cfg.get("model_features"))
    try:
        feat_df = read_csv(feat_key, low_memory=False) if GCS_BUCKET \
                  else pd.read_csv(cfg["model_features"], low_memory=False)
    except Exception as e:
        logger.error(f"SB: feature load failed: {e}")
        return pd.DataFrame()

    feat_df["game_date"] = pd.to_datetime(feat_df["game_date"])
    batter_latest = (
        feat_df.sort_values("game_date")
               .groupby("batter", as_index=False)
               .last()
    )
    logger.info(f"SB: {len(batter_latest):,} batters in snapshot")

    try:
        lineups = _pull_lineup_date(run_date, verbose=False)
    except Exception as e:
        logger.warning(f"SB: lineup fetch failed: {e}")
        lineups = pd.DataFrame()

    weather_today = _fetch_today_weather(sched)

    candidates = _candidates_from_lineups(lineups, batter_latest)
    logger.info(f"SB: {len(candidates):,} candidate batter-game rows")
    if candidates.empty:
        return pd.DataFrame()

    out = _join_pitcher_and_catcher(candidates, sched, lineups)
    out = _join_weather(out, weather_today)

    from mlb.runners.build_sb_features import STADIUMS_ROOF, TEAM_NAME_TO_ABBR
    home_abbr = out["home_team"].map(TEAM_NAME_TO_ABBR)
    out["is_dome"]          = home_abbr.map(lambda t: 1 if STADIUMS_ROOF.get(t) else 0).fillna(0).astype(int)
    out["temperature_f"]    = out.get("temperature_f", pd.Series(70, index=out.index)).fillna(70)
    out["post_pitch_clock"] = 1  # all 2026 games post pitch clock

    return out


def _candidates_from_lineups(lineups, batter_latest):
    rows = []
    if not lineups.empty:
        for _, r in lineups.iterrows():
            batter_id = r["player_id"]
            bat_row   = batter_latest[batter_latest["batter"] == batter_id]
            if bat_row.empty:
                continue
            bat = bat_row.iloc[0].to_dict()
            bat.update({
                "game_pk":          r["game_pk"],
                "home_team":        r.get("home_team"),
                "away_team":        r.get("away_team"),
                "batter_team_side": r["team_side"],
                "batting_order":    int(r["batting_order"]),
                "player_name":      r["player_name"],
            })
            rows.append(bat)

    if not rows:
        logger.warning("SB: no confirmed lineup candidates; skipping unsafe historical-team fallback")
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(subset=["batter", "game_pk"], keep="first")
    out["is_home"] = (out["batter_team_side"] == "home").astype(int)
    return out


def _join_pitcher_and_catcher(candidates, sched, lineups):
    """Attach today's opposing pitcher (handedness + B-Ref SB/CS-allowed) and
    opposing catcher (arm strength / pop time) -- the first runner in this
    codebase needing a THIRD player-entity per row."""
    from mlb_core.data.auxiliary_features import load_fangraphs_pitching, norm_statcast_name
    from mlb_core.data.aux_joins import join_catcher_aux

    games_idx = sched.set_index("game_pk")

    # Today's starting catcher per (game_pk, team_side) from the SAME
    # confirmed-lineup pull -- position=="C", no extra API call.
    catcher_by_side: dict = {}
    if not lineups.empty and "position" in lineups.columns:
        cat_rows = lineups[lineups["position"] == "C"]
        for _, cr in cat_rows.iterrows():
            catcher_by_side[(cr["game_pk"], cr["team_side"])] = cr["player_id"]
    else:
        logger.warning("SB: lineups missing 'position' column -- catcher features will be NaN")

    bref = pd.DataFrame()
    try:
        bref = load_fangraphs_pitching()
    except Exception as e:
        logger.warning(f"SB: bref load failed (non-fatal): {e}")

    out_rows = []
    for _, r in candidates.iterrows():
        game_pk = r["game_pk"]
        merged = dict(r)
        if game_pk in games_idx.index:
            game = games_idx.loc[game_pk]
            is_batter_home = r["batter_team_side"] == "home"
            opp_pitcher_id     = game.get("away_pitcher_id") if is_batter_home else game.get("home_pitcher_id")
            opp_pitcher_name   = game.get("away_pitcher_name") if is_batter_home else game.get("home_pitcher_name")
            opp_pitcher_throws = game.get("away_pitcher_throws") if is_batter_home else game.get("home_pitcher_throws")
            merged["opp_pitcher_id"] = opp_pitcher_id
            merged["p_throws_L"] = 1 if opp_pitcher_throws == "L" else 0
            # Reset to NaN before the lookup below -- `r` is the batter's
            # LATEST historical snapshot row, which still carries whatever
            # pitcher_sb_allowed/cs_allowed/pickoffs were true for a PAST
            # opponent. Without this reset, a failed bref match for TODAY's
            # pitcher would silently leave that stale prior-opponent value
            # in place instead of correctly showing "unknown" (found
            # alongside the identical catcher_* staleness bug, live
            # 2026-08-20; pitcher_pickoffs added 2026-08-21, same reset
            # applies to it for the same reason).
            merged["pitcher_sb_allowed"] = np.nan
            merged["pitcher_cs_allowed"] = np.nan
            merged["pitcher_pickoffs"]   = np.nan

            _bref_pitcher_cols = {"pitcher_sb_allowed", "pitcher_cs_allowed", "pitcher_pickoffs"}
            if not bref.empty and opp_pitcher_name and _bref_pitcher_cols & set(bref.columns):
                key = norm_statcast_name(opp_pitcher_name)
                year = pd.Timestamp(r.get("game_date", date.today())).year
                match = bref[(bref["name_norm"] == key) & (bref["year"] == year)]
                if not match.empty:
                    merged["pitcher_sb_allowed"] = match.iloc[0].get("pitcher_sb_allowed")
                    merged["pitcher_cs_allowed"] = match.iloc[0].get("pitcher_cs_allowed")
                    merged["pitcher_pickoffs"]   = match.iloc[0].get("pitcher_pickoffs")

            opp_side = "away" if is_batter_home else "home"
            merged["opp_catcher_id"] = catcher_by_side.get((game_pk, opp_side))
        out_rows.append(merged)

    out = pd.DataFrame(out_rows)
    if "opp_catcher_id" not in out.columns:
        out["opp_catcher_id"] = np.nan
    out["game_date"] = pd.to_datetime(out.get("game_date", pd.Timestamp.today()))

    # Each candidate row starts as the batter's LATEST historical snapshot
    # (from model_features.csv), which still carries whatever catcher_*
    # values were true against THAT game's opposing catcher -- stale by
    # definition for today's actually-opposing catcher. join_catcher_aux()
    # is a pandas merge, so leaving those stale columns in place produces
    # catcher_pop_2b_sba_x/_y collisions instead of a clean overwrite
    # (found live 2026-08-20 testing this exact path against today's real
    # slate). Drop them first so the merge has nothing to collide with.
    stale_catcher_cols = [c for c in out.columns if c.startswith("catcher_")]
    if stale_catcher_cols:
        out = out.drop(columns=stale_catcher_cols)

    try:
        out = join_catcher_aux(out, opp_catcher_col="opp_catcher_id")
    except Exception as e:
        logger.warning(f"SB: catcher aux join failed (non-fatal): {e}")
    return out


def _join_weather(df, weather_today):
    if df.empty or not weather_today:
        return df
    wx_rows = [{"game_pk": gp, **wx} for gp, wx in weather_today.items()]
    wx_df   = pd.DataFrame(wx_rows)
    overlap = set(df.columns) & set(wx_df.columns) - {"game_pk"}
    if overlap:
        wx_df = wx_df.drop(columns=list(overlap))
    return df.merge(wx_df, on="game_pk", how="left")


def _fetch_today_weather(sched):
    try:
        from mlb_core.data.weather import fetch_live_weather_for_slate
        out = fetch_live_weather_for_slate(sched)
        logger.info(f"SB weather: {len(out)}/{len(sched)} games")
        return out
    except Exception as e:
        logger.warning(f"SB: weather fetch failed: {e}")
        return {}


# -- Scoring ---------------------------------------------------------------------

def _build_predictions(cfg: dict, run_date: str) -> pd.DataFrame:
    from mlb_core.odds import american_to_implied_prob, kelly_stake, kelly_pct as kpct
    from mlb_core.odds.utils import devig_two_way, devig_unilateral
    from mlb_core.odds import sgo as _sgo
    from mlb_core.odds.sgo import extract_stolen_base_odds

    _SGO_KEY = "Odds/sgo/latest.json"
    _fresh, _reason = _sgo.check_snapshot_freshness(_SGO_KEY)
    if not _fresh:
        logger.error(f"SB: aborting -- {_reason}")
        return pd.DataFrame()

    from mlb_core.config import GCS_BUCKET
    from mlb_core.storage import check_build_sentinel
    _sok, _sreason = check_build_sentinel(GCS_BUCKET, "SB_Pro_System")
    if not _sok:
        msg = f"SB: aborting -- stale/failed feature build: {_sreason}"
        logger.error(msg)
        from mlb_core.notify.discord import post_error
        post_error("SB", msg)
        return pd.DataFrame()

    booster, features, feature_means, nb_alpha = _load_model(cfg)
    calibrator = _load_calibrator(cfg)

    feat_df = _build_today_feature_rows(cfg, run_date)
    if feat_df.empty:
        logger.warning("SB: no candidate rows -- skipping")
        return pd.DataFrame()

    events   = _sgo.load_snapshot(_SGO_KEY)
    sb_odds  = extract_stolen_base_odds(events) if events else {}
    if not sb_odds:
        logger.warning("SB: no odds in SGO snapshot")
        return pd.DataFrame()
    logger.info(f"SB: {len(sb_odds)} players with odds "
                f"({sum(1 for v in sb_odds.values() if not v['is_two_sided'])} one-sided only)")
    sb_2plus_odds = _sgo.extract_stolen_base_alt_line_odds(events, 2) if events else {}

    X = feat_df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")
    if feature_means:
        for col in features:
            mean = feature_means.get(col)
            if mean is not None:
                X[col] = X[col].fillna(float(mean))
    X = X.astype(float)
    dm = xgb.DMatrix(X, feature_names=features)
    ntree = getattr(booster, "best_ntree_limit", 0)
    preds = booster.predict(dm, iteration_range=(0, ntree)) if ntree else booster.predict(dm)

    feat_df = feat_df.copy()
    feat_df["lambda_sb"] = preds.clip(0.01, cfg.get("mc_cap", 10))
    feat_df["raw_lambda_sb"] = feat_df["lambda_sb"]

    if calibrator is not None:
        try:
            raw = feat_df["lambda_sb"].values.copy()
            x_min = getattr(calibrator, "X_min_", None)
            x_max = getattr(calibrator, "X_max_", None)
            in_range = np.ones(len(raw), dtype=bool)
            if x_min is not None and x_max is not None:
                in_range = (raw >= x_min) & (raw <= x_max)
            cal = raw.copy()
            if in_range.any():
                cal[in_range] = calibrator.predict(raw[in_range])
            feat_df["lambda_sb"] = np.clip(cal, 0.01, cfg.get("mc_cap", 10))
            feat_df["calibrator_in_range"] = in_range
            logger.info("SB: calibrator applied to %d/%d batters", int(in_range.sum()), len(raw))
        except Exception as e:
            logger.warning(f"SB calibrator predict failed: {e}")
            feat_df["calibrator_in_range"] = False
    else:
        feat_df["calibrator_in_range"] = False

    if "player_name" not in feat_df.columns:
        logger.warning("SB: player_name column missing from feature rows")
        return pd.DataFrame()

    feat_df["_name_key"] = feat_df["player_name"].apply(_normalize_name)
    name_to_idx = {n: i for i, n in enumerate(feat_df["_name_key"]) if n}
    _all_keys   = list(name_to_idx)

    def _resolve(raw_name):
        key = _normalize_name(raw_name)
        if not key:
            return None
        idx = name_to_idx.get(key)
        if idx is not None:
            return idx
        import difflib
        matches = difflib.get_close_matches(key, _all_keys, n=1, cutoff=0.85)
        return name_to_idx[matches[0]] if matches else None

    from mlb_core.risk.exposure import prefetch_exposure, apply_cap
    from mlb_core.tracking.bet_tracker import _make_engine
    _engine   = _make_engine("unused")
    _game_pks = list(feat_df["game_pk"].dropna().astype(int).unique())
    _bankroll, _prefetched = prefetch_exposure(_engine, _game_pks, run_date, system="SB")
    _pending: dict[int, float] = {}
    from mlb_core.risk.gates import is_suppressed as _is_suppressed
    from mlb_core.risk.calibration import apply as _cal_apply, EDGE_CAP as _EDGE_CAP
    from mlb.analysis import book_vig
    _gate_suppressed = _is_suppressed("SB")
    if _gate_suppressed:
        logger.warning("SB gate active -- logging only, no staked bets this run")

    results = []

    for player_name, odds_info in sb_odds.items():
        idx = _resolve(player_name)
        if idx is None:
            continue
        row = feat_df.iloc[idx]
        try:
            event_id = int(odds_info.get("event_id"))
            row_game_pk = int(row.get("game_pk"))
            if event_id != row_game_pk:
                logger.warning(
                    "SB: skipping %s due event/game mismatch odds_event=%s feature_game=%s",
                    player_name, event_id, row_game_pk,
                )
                continue
        except (TypeError, ValueError):
            logger.warning(
                "SB: skipping %s -- event_id/game_pk not comparable "
                "(odds_event=%r feature_game=%r)",
                player_name, odds_info.get("event_id"), row.get("game_pk"),
            )
            continue

        mu   = float(row["lambda_sb"])
        line = odds_info.get("line")
        if line is None:
            continue

        p_over = _negbin_p_over(line, mu, nb_alpha)

        if odds_info.get("is_two_sided"):
            p_under = _negbin_p_under(line, mu, nb_alpha)
            mkt_over  = american_to_implied_prob(odds_info["over_odds"])
            mkt_under = american_to_implied_prob(odds_info["under_odds"])
            if not (mkt_over + mkt_under):
                continue
            fair_over, fair_under = devig_two_way(mkt_over, mkt_under, method="shin")
            if pd.isna(fair_over) or pd.isna(fair_under):
                continue
            edge_over  = p_over  - fair_over
            edge_under = p_under - fair_under
            if edge_over >= edge_under:
                side, edge, fair, odds, model_prob = "OVER", edge_over, fair_over, odds_info["over_odds"], p_over
            else:
                side, edge, fair, odds, model_prob = "UNDER", edge_under, fair_under, odds_info["under_odds"], p_under
        else:
            # One-sided "Yes"-only quote (caesars/novig, confirmed live) --
            # no complementary side to devig against, same as HR's yn-yes market.
            mkt_over = american_to_implied_prob(odds_info["over_odds"])
            vig = book_vig.get_vig("sb_ou", odds_info.get("bookmaker"), default=0.10)
            fair_over = devig_unilateral(mkt_over, vig_pct=vig)
            side, edge, fair, odds, model_prob = "OVER", p_over - fair_over, fair_over, odds_info["over_odds"], p_over

        model_prob, _cal = _cal_apply("SB", model_prob)
        edge = model_prob - fair
        _edge_capped = _cal and edge > _EDGE_CAP

        logger.info(
            "SB pred | %s | raw_lam=%.4f lam=%.4f in_range=%s "
            "line=%.1f %s two_sided=%s | model=%.3f fair=%.3f edge=%+.3f",
            player_name,
            float(row.get("raw_lambda_sb", mu)), mu,
            bool(row.get("calibrator_in_range", False)),
            float(line), side, odds_info.get("is_two_sided"),
            model_prob, fair, edge,
        )

        k_pct_val = kpct(model_prob, odds, cfg["kelly_fraction"])
        _bankroll, _cap = apply_cap(
            _bankroll, int(row["game_pk"]), _prefetched, _pending,
            cap_units=cfg.get("cap_units", 10.0),
        )
        raw_stake = kelly_stake(
            model_prob, odds, bankroll=_bankroll,
            fraction=cfg["kelly_fraction"],
            min_pct=cfg["min_kelly_pct"], max_pct=cfg["max_kelly_pct"],
        )
        stake = min(raw_stake, _cap)

        _is_live = _sgo.is_live_event(odds_info.get("commence_time"))
        if _is_live:
            logger.warning(
                "SB: LIVE/in-play odds for %s (start=%s) -- suppressing bet",
                player_name, odds_info.get("commence_time"),
            )
        kelly_triggered = (
            edge >= cfg["min_edge"] and stake > 0 and not LOG_ONLY
            and not _gate_suppressed and not _edge_capped and not _is_live
        )
        if kelly_triggered and stake > 0:
            gp = int(row.get("game_pk", 0))
            _pending[gp] = _pending.get(gp, 0.0) + stake

        results.append({
            "player":       player_name,
            "game_pk":      int(row.get("game_pk", 0)),
            "away_team":    odds_info["away_team"],
            "home_team":    odds_info["home_team"],
            "line":         float(line),
            "side":         side,
            "bet_type":     f"SB_{side}_{line}",
            "raw_lambda_sb":round(float(row.get("raw_lambda_sb", mu)), 4),
            "lambda_sb":    round(mu, 4),
            "model_prob":   round(model_prob, 4),
            "market_prob":  round(fair, 4),
            "edge":         round(edge, 4),
            "kelly_pct":    round(k_pct_val, 4),
            "odds":         odds,
            "stake":        stake if kelly_triggered else 0.0,
            "kelly_triggered": kelly_triggered,
            "bookmaker":    odds_info.get("bookmaker"),
        })

        alt_info = sb_2plus_odds.get(player_name)
        if alt_info is not None:
            trow, _bankroll = score_threshold_bet(
                model_prob_raw=_negbin_p_over(1.5, mu, nb_alpha),
                alt_odds_info=alt_info,
                vig_market_key="sb_2plus",
                game_pk=int(row.get("game_pk", 0)),
                bankroll=_bankroll,
                prefetched_stakes=_prefetched,
                pending_stakes=_pending,
                cfg=cfg,
                gate_suppressed=_gate_suppressed or LOG_ONLY,
            )
            if trow is not None:
                logger.info(
                    "SB pred | %s | 2+ SB | lam=%.4f | model=%.3f fair=%.3f edge=%+.3f",
                    player_name, mu, trow["model_prob"], trow["market_prob"], trow["edge"],
                )
                results.append({
                    "player":       player_name,
                    "game_pk":      int(row.get("game_pk", 0)),
                    "away_team":    trow["away_team"] or odds_info["away_team"],
                    "home_team":    trow["home_team"] or odds_info["home_team"],
                    "line":         2.0,
                    "side":         "2PLUS",
                    "bet_type":     "SB_2PLUS_2.0",
                    "raw_lambda_sb":round(float(row.get("raw_lambda_sb", mu)), 4),
                    "lambda_sb":    round(mu, 4),
                    "model_prob":   trow["model_prob"],
                    "market_prob":  trow["market_prob"],
                    "edge":         trow["edge"],
                    "kelly_pct":    trow["kelly_pct"],
                    "odds":         trow["odds"],
                    "stake":        trow["stake"],
                    "kelly_triggered": trow["kelly_triggered"],
                    "bookmaker":    trow["bookmaker"],
                })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_values("edge", ascending=False)


# -- Entry point -------------------------------------------------------------------

def run(run_type: str = "morning", run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    logger.info(f"SB run | type={run_type} | date={run_date} | log_only={LOG_ONLY}")

    from mlb.systems.SB_Pro_System.config_sb import cfg
    from mlb_core.tracking import BetTracker
    from mlb_core.notify.discord import post_bets

    today_df = _build_predictions(cfg, run_date)

    if today_df.empty:
        logger.info("SB: no qualifying bets today")
        post_bets([], system="SB", run_date=run_date)
        return {"bets_logged": 0}

    tracker     = BetTracker(cfg["bet_db"], system="SB")
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
        # RULE (CONTEXT.md s5): a log-only system's Discord-bound rows must
        # still be gated on kelly_triggered, same as a graduated system's --
        # post_bets() posts whatever it's handed unconditionally.
        if triggered:
            bet_rows.append(row.to_dict())

    log_suffix = " (log-only -- new system, 200-bet gate not yet cleared)" if LOG_ONLY else ""
    logger.info(f"SB: {bets_logged} bets logged{log_suffix}")
    post_bets(bet_rows, system="SB", run_date=run_date)

    return {"bets_logged": bets_logged, "log_only": LOG_ONLY, "bet_rows": bet_rows}
