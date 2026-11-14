"""
ev.novig.maker_study -- the pre-registered measurement design for the Novig paper maker
falsification study (NOV-03), Argon ruling 2026-11-12 Q4, encoded exactly as ruled.

The study is ARMED (armed by Luc's launch decision 2026-11-14; the launch runner is
ev.novig.maker_runner; real-money stays refused (the only client the runner builds is the paper
sandbox + public read-only catalog)). This module is the evaluation config + the metric math,
so that when the study runs, every number it is judged by already exists in code, dated
and unchangeable without a visible diff. Bars are pre-registered: zero re-litigation.

Window: 2026-11-14 .. 2026-11-28 (14 calendar days) or n = 150 venue-matched paper fills with
usable T-15m closing consensus, whichever comes first; mandatory early-kill check at Day 7 or
n = 50 fills. The distinguishing measurement (named in advance, ruling Q1): post-placement
consensus pickoff drift, Dpickoff = mean over fills of (P_cons,side(t_post) - P_cons,side(t_close)),
paired with closing consensus markout.

Inputs to the metrics are plain dicts in the ruling's log shapes (QUOTE_LOG_FIELDS for every
posted quote, filled or not; FILL_FIELDS for every paper fill). p_cons_close is the T-15m
leave-Novig-out closing consensus for the quote's side. All probabilities for a fill are on the
SIDE WE BOUGHT. Markouts are in cents per contract.
"""

from __future__ import annotations

import math
from datetime import date

STUDY_ARMED = True                  # armed by Luc's launch decision 2026-11-14; see module docstring

WINDOW_START = date(2026, 11, 14)
WINDOW_END = date(2026, 11, 28)
WINDOW_DAYS = 14
TARGET_FILLS = 150
EARLY_KILL_DAY = 7
EARLY_KILL_FILLS = 50

# every posted quote (filled & unfilled), ruling Q4
QUOTE_LOG_FIELDS = ("order_id", "game_key", "sport", "stat", "player_norm", "line", "side",
                    "t_post", "t_end", "quote_price", "novig_bid_at_post", "novig_ask_at_post",
                    "novig_spread_at_post", "p_cons_at_post", "p_blend_at_post", "p_cons_close",
                    "terminal_status")
# every paper fill (from /v3/portfolio/fills, joined to its quote + closing consensus;
# p_novig_close is Novig's own T-15m close on our side, for the clv_same_book report field)
FILL_FIELDS = ("order_id", "game_key", "game_day", "sport", "stat", "side", "p_fill",
               "p_cons_at_post", "p_cons_close", "p_cons_fill_plus_5m", "p_cons_fill_plus_30m",
               "p_novig_close", "in_news_window")

# ------------------------------------------------------------- pre-registered bars (ruling Q4)
PROMOTION_BARS = {
    "volume": {"min_fills": 150, "min_game_days": 10},
    "pickoff_drift": {"mean_pp_max": 1.50, "upper_ci_pp_max": 2.00},
    "closing_markout": {"mean_c_min": 1.50, "lower_ci_c_min": 0.25},
    "markout_30m": {"mean_c_min": 1.00},
    "news_window_share": {"max": 0.12},
    "winners_curse_gap": {"pp_max": 1.25},
}
KILL_BARS = {
    "volume": {"max_fills_exclusive": 30, "over_days": 14},
    "pickoff_drift": {"mean_pp_over": 2.00, "min_fills": 45},
    "closing_markout": {"mean_c_at_most": 0.00, "min_fills": 50,
                        "lower_ci_c_below": -1.50, "lower_ci_at_fills": 150},
    "markout_30m": {"mean_c_at_most": -0.50, "min_fills": 50},
    "news_window_share": {"over": 0.25, "min_fills": 45},
    "winners_curse_gap": {"pp_over": 2.50},
}


# ------------------------------------------------------------- cluster-robust CI (game-day clusters)
def day_clustered_mean_ci(values: list, days: list, z: float = 1.96):
    """(mean, lo, hi) with a CR1 cluster-robust standard error clustered on game day -- the same
    clustering the repo's grading uses (ev grade_t: date-clustered, CR1). Falls back to a plain
    standard error when there is a single cluster."""
    n = len(values)
    if n == 0:
        return None, None, None
    mean = sum(values) / n
    clusters: dict = {}
    for v, d in zip(values, days):
        clusters.setdefault(d, []).append(v - mean)
    g = len(clusters)
    if g < 2 or n < 2:
        var = sum((v - mean) ** 2 for v in values) / max(n - 1, 1) / n
    else:
        meat = sum(sum(rs) ** 2 for rs in clusters.values())
        var = meat / (n * n) * (g / (g - 1.0)) * ((n - 1.0) / max(n - g, 1))
    se = math.sqrt(max(var, 0.0))
    return mean, mean - z * se, mean + z * se


# ------------------------------------------------------------- metrics (ruling Q4 definitions)
def compute_metrics(quotes: list, fills: list) -> dict:
    """quotes: every posted quote (QUOTE_LOG_FIELDS + 'filled': bool). fills: FILL_FIELDS dicts.
    Returns the study's metric set; CIs are day-clustered where the ruling requires them."""
    n_posted = len(quotes)
    n_fills = len(fills)
    days = sorted({f["game_day"] for f in fills})
    drift_pp = [(f["p_cons_at_post"] - f["p_cons_close"]) * 100.0 for f in fills]
    drift_mean, drift_lo, drift_hi = day_clustered_mean_ci(drift_pp, [f["game_day"] for f in fills])
    close_c = [(f["p_cons_close"] - f["p_fill"]) * 100.0 for f in fills]
    close_mean, close_lo, close_hi = day_clustered_mean_ci(close_c, [f["game_day"] for f in fills])
    m30 = [(f["p_cons_fill_plus_30m"] - f["p_fill"]) * 100.0 for f in fills
           if f.get("p_cons_fill_plus_30m") is not None]
    m5 = [(f["p_cons_fill_plus_5m"] - f["p_fill"]) * 100.0 for f in fills
          if f.get("p_cons_fill_plus_5m") is not None]
    same_book = [(f["p_novig_close"] - f["p_fill"]) * 100.0 for f in fills
                 if f.get("p_novig_close") is not None]
    news_share = (sum(1 for f in fills if f.get("in_news_window")) / n_fills) if n_fills else None
    unfilled_drift = [(q["p_cons_at_post"] - q["p_cons_close"]) * 100.0
                      for q in quotes if not q.get("filled") and q.get("p_cons_close") is not None]
    filled_drift = drift_pp
    curse = None
    if filled_drift and unfilled_drift:
        curse = (sum(filled_drift) / len(filled_drift)) - (sum(unfilled_drift) / len(unfilled_drift))
    return {
        "n_posted": n_posted, "n_fills": n_fills, "n_game_days": len(days),
        "fill_rate": (n_fills / n_posted) if n_posted else None,
        "pickoff_drift_pp": drift_mean, "pickoff_drift_ci_pp": (drift_lo, drift_hi),
        "closing_markout_c": close_mean, "closing_markout_ci_c": (close_lo, close_hi),
        "markout_5m_c": (sum(m5) / len(m5)) if m5 else None,
        "markout_30m_c": (sum(m30) / len(m30)) if m30 else None,
        "clv_same_book_c": (sum(same_book) / len(same_book)) if same_book else None,
        "news_window_share": news_share,
        "winners_curse_gap_pp": curse,
    }


def evaluate_study(metrics: dict, *, days_elapsed: int) -> dict:
    """Apply the pre-registered bars to a metrics dict from compute_metrics. Returns per-bar
    booleans (None = not yet evaluable at this sample) and the verdicts: promotion requires ALL
    six promotion bars; ANY one kill bar ends Novig market making permanently."""
    n = metrics["n_fills"]
    P, K = PROMOTION_BARS, KILL_BARS
    promo = {
        "volume": n >= P["volume"]["min_fills"] and metrics["n_game_days"] >= P["volume"]["min_game_days"],
        "pickoff_drift": (metrics["pickoff_drift_pp"] is not None
                          and metrics["pickoff_drift_pp"] <= P["pickoff_drift"]["mean_pp_max"]
                          and metrics["pickoff_drift_ci_pp"][1] is not None
                          and metrics["pickoff_drift_ci_pp"][1] <= P["pickoff_drift"]["upper_ci_pp_max"]),
        "closing_markout": (metrics["closing_markout_c"] is not None
                            and metrics["closing_markout_c"] >= P["closing_markout"]["mean_c_min"]
                            and metrics["closing_markout_ci_c"][0] is not None
                            and metrics["closing_markout_ci_c"][0] >= P["closing_markout"]["lower_ci_c_min"]),
        "markout_30m": (metrics["markout_30m_c"] is not None
                        and metrics["markout_30m_c"] >= P["markout_30m"]["mean_c_min"]),
        "news_window_share": (metrics["news_window_share"] is not None
                              and metrics["news_window_share"] <= P["news_window_share"]["max"]),
        "winners_curse_gap": (metrics["winners_curse_gap_pp"] is not None
                              and metrics["winners_curse_gap_pp"] <= P["winners_curse_gap"]["pp_max"]),
    }
    kill = {
        "volume": days_elapsed >= K["volume"]["over_days"] and n < K["volume"]["max_fills_exclusive"],
        "pickoff_drift": (n >= K["pickoff_drift"]["min_fills"] and metrics["pickoff_drift_pp"] is not None
                          and metrics["pickoff_drift_pp"] > K["pickoff_drift"]["mean_pp_over"]),
        "closing_markout": (
            (n >= K["closing_markout"]["min_fills"] and metrics["closing_markout_c"] is not None
             and metrics["closing_markout_c"] <= K["closing_markout"]["mean_c_at_most"])
            or (n >= K["closing_markout"]["lower_ci_at_fills"]
                and metrics["closing_markout_ci_c"][0] is not None
                and metrics["closing_markout_ci_c"][0] < K["closing_markout"]["lower_ci_c_below"])),
        "markout_30m": (n >= K["markout_30m"]["min_fills"] and metrics["markout_30m_c"] is not None
                        and metrics["markout_30m_c"] <= K["markout_30m"]["mean_c_at_most"]),
        "news_window_share": (n >= K["news_window_share"]["min_fills"]
                              and metrics["news_window_share"] is not None
                              and metrics["news_window_share"] > K["news_window_share"]["over"]),
        "winners_curse_gap": (metrics["winners_curse_gap_pp"] is not None
                              and metrics["winners_curse_gap_pp"] > K["winners_curse_gap"]["pp_over"]),
    }
    return {"promotion": promo, "kill": kill,
            "promote": all(promo.values()), "killed": any(kill.values()),
            "early_kill_check_due": days_elapsed >= EARLY_KILL_DAY or n >= EARLY_KILL_FILLS}
