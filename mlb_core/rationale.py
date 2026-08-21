"""
Canned rationale generator for bet picks.
Each system has a rule list: (feature, test_fn, phrase_template).
Top 3 firing rules are joined into a plain-English notes string.
phrase_template may contain {v} which is replaced with the feature value.
"""

from __future__ import annotations
from typing import Any

def _fmt(template: str, v: Any) -> str:
    try:
        return template.format(v=v)
    except Exception:
        return template


# ── HR rules ────────────────────────────────────────────────────────────────
_HR_RULES = [
    ("barrel_rate",          lambda v: v >= 0.12,  "high barrel rate ({v:.0%})"),
    ("barrel_rate",          lambda v: v >= 0.08,  "elevated barrel rate ({v:.0%})"),
    ("hard_hit",             lambda v: v >= 0.45,  "hard contact ({v:.0%} hard-hit rate)"),
    ("max_ev",               lambda v: v >= 108,   "max EV {v:.0f} mph"),
    ("launch_speed",         lambda v: v >= 92,    "avg EV {v:.0f} mph"),
    ("la_mean",              lambda v: 14 <= v <= 28, "optimal launch angle ({v:.0f}°)"),
    ("batter_hr_vs_lhp",     lambda v: v >= 0.06,  "strong vs LHP ({v:.0%} HR rate)"),
    ("batter_hr_vs_rhp",     lambda v: v >= 0.06,  "strong vs RHP ({v:.0%} HR rate)"),
    ("hr_park_factor",       lambda v: v >= 1.08,  "hitter-friendly park ({v:.2f}x HR factor)"),
    ("hr_park_factor",       lambda v: v >= 1.03,  "above-avg HR park ({v:.2f}x)"),
    ("wind_out",             lambda v: v == 1,     "wind blowing out"),
    ("wind_speed_mph",       lambda v: v >= 12,    "wind {v:.0f} mph"),
    ("wind_in",              lambda v: v == 1,     "wind blowing in"),
    ("temperature_f",        lambda v: v >= 80,    "warm conditions ({v:.0f}°F)"),
    ("temperature_f",        lambda v: v <= 50,    "cold conditions ({v:.0f}°F) — suppressed"),
    ("hr_rate",              lambda v: v >= 0.08,  "hot streak ({v:.0%} HR rate L15)"),
    ("hr_rate",              lambda v: v >= 0.05,  "above-avg HR rate L15 ({v:.0%})"),
]

# ── NRFI rules ───────────────────────────────────────────────────────────────
_NRFI_RULES = [
    ("k_pct_L3",             lambda v: v >= 0.28,  "starter K% {v:.0%} L3"),
    ("k_pct_L3",             lambda v: v >= 0.22,  "above-avg K rate L3 ({v:.0%})"),
    ("whiff_pct_L3",         lambda v: v >= 0.32,  "high whiff rate ({v:.0%} L3)"),
    ("bb_pct_L3",            lambda v: v <= 0.06,  "strong command ({v:.0%} BB L3)"),
    ("xwoba_allowed_L3",     lambda v: v <= 0.290, "elite contact suppression (xwOBA {v:.3f} L3)"),
    ("xwoba_allowed_L3",     lambda v: v <= 0.320, "above-avg contact suppression (xwOBA {v:.3f})"),
    ("velo_mean_L3",         lambda v: v >= 95,    "high velo ({v:.1f} mph avg L3)"),
    ("wind_out",             lambda v: v == 1,     "wind blowing out"),
    ("wind_in",              lambda v: v == 1,     "wind blowing in"),
    ("wind_speed_mph",       lambda v: v >= 12,    "wind {v:.0f} mph"),
    ("is_cold",              lambda v: v == 1,     "cold conditions — suppressed offense"),
    ("park_factor",          lambda v: v <= 0.92,  "pitcher-friendly park ({v:.2f}x)"),
    ("park_factor",          lambda v: v >= 1.08,  "hitter-friendly park ({v:.2f}x) — risk"),
    ("ump_total_run_impact_L30", lambda v: v <= -0.15, "run-suppressing ump (impact {v:+.2f})"),
    ("ump_total_run_impact_L30", lambda v: v >= 0.15,  "run-friendly ump (impact {v:+.2f}) — risk"),
    ("platoon_edge",         lambda v: v >= 0.03,  "platoon edge"),
    ("days_rest",            lambda v: v >= 5,     "well-rested starter ({v:.0f} days rest)"),
    ("short_rest",           lambda v: v == 1,     "short rest — starter risk"),
]

# ── K rules ──────────────────────────────────────────────────────────────────
_K_RULES = [
    ("k_pct_L3",             lambda v: v >= 0.30,  "elite K rate L3 ({v:.0%})"),
    ("k_pct_L3",             lambda v: v >= 0.24,  "above-avg K rate L3 ({v:.0%})"),
    ("whiff_pct_L3",         lambda v: v >= 0.32,  "high whiff rate ({v:.0%})"),
    ("velo_mean_L3",         lambda v: v >= 95,    "high velo ({v:.1f} mph L3)"),
    ("bb_pct_L3",            lambda v: v <= 0.06,  "strong command ({v:.0%} BB)"),
    ("xwoba_allowed_L3",     lambda v: v <= 0.290, "dominant recently (xwOBA {v:.3f})"),
    ("days_rest",            lambda v: v >= 5,     "well-rested ({v:.0f} days rest)"),
    ("short_rest",           lambda v: v == 1,     "short rest — may limit IP"),
    ("ump_total_run_impact_L30", lambda v: v <= -0.15, "K-friendly ump"),
]

# ── OUTS rules (shares K feature CSV) ────────────────────────────────────────
_OUTS_RULES = [
    ("avg_ip_L5",            lambda v: v >= 6.0,   "deep into games recently ({v:.1f} IP avg L5)"),
    ("avg_ip_L5",            lambda v: v >= 5.0,   "solid innings load ({v:.1f} IP avg L5)"),
    ("k_pct_L3",             lambda v: v >= 0.26,  "high K rate — efficient outs ({v:.0%})"),
    ("bb_pct_L3",            lambda v: v <= 0.07,  "low walks — more IP ({v:.0%} BB)"),
    ("velo_mean_L3",         lambda v: v >= 95,    "high velo ({v:.1f} mph)"),
    ("days_rest",            lambda v: v >= 5,     "well-rested ({v:.0f} days rest)"),
    ("short_rest",           lambda v: v == 1,     "short rest — may limit outs"),
]

# ── F5 rules ──────────────────────────────────────────────────────────────────
_F5_RULES = [
    ("k_pct_L3",             lambda v: v >= 0.26,  "strong starter K rate ({v:.0%} L3)"),
    ("xwoba_allowed_L3",     lambda v: v <= 0.300, "elite contact suppression (xwOBA {v:.3f})"),
    ("velo_mean_L3",         lambda v: v >= 95,    "high velo ({v:.1f} mph)"),
    ("bb_pct_L3",            lambda v: v <= 0.07,  "strong command ({v:.0%} BB)"),
    ("wind_out",             lambda v: v == 1,     "wind blowing out"),
    ("wind_in",              lambda v: v == 1,     "wind blowing in"),
    ("park_factor",          lambda v: v <= 0.92,  "pitcher-friendly park ({v:.2f}x)"),
    ("park_factor",          lambda v: v >= 1.08,  "hitter-friendly park ({v:.2f}x)"),
    ("is_cold",              lambda v: v == 1,     "cold conditions — suppressed scoring"),
    ("days_rest",            lambda v: v >= 5,     "well-rested starter ({v:.0f} days rest)"),
    ("short_rest",           lambda v: v == 1,     "short rest — starter risk"),
    ("pitcher_is_home",      lambda v: v == 1,     "home starter"),
]

# ── BATTER_HITS rules ─────────────────────────────────────────────────────────
_BATTER_HITS_RULES = [
    ("hits_per_game_L20",       lambda v: v >= 1.5,   "averaging {v:.2f} hits/game L20"),
    ("hits_per_game_L20",       lambda v: v >= 1.2,   "above-avg contact rate ({v:.2f} H/G L20)"),
    ("babip_L20",                lambda v: v >= 0.330, "elevated BABIP ({v:.3f} L20)"),
    ("babip_L20",                lambda v: v <= 0.250, "low BABIP ({v:.3f} L20) — regression risk"),
    ("contact_pct_L20",          lambda v: v >= 0.80,  "high contact rate ({v:.0%} L20)"),
    ("ld_rate_L20",              lambda v: v >= 0.25,  "line drive machine ({v:.0%} LD rate L20)"),
    ("hard_hit_L20",             lambda v: v >= 0.45,  "hard contact ({v:.0%} hard-hit L20)"),
    ("hits_vs_hand_season",      lambda v: v >= 0.280, "hitting {v:.3f} vs this hand type this season"),
    ("pitcher_babip_allowed_L20",lambda v: v >= 0.320, "pitcher allowing high BABIP ({v:.3f} L20)"),
    ("pitcher_hits_per_9_L20",   lambda v: v >= 9.0,   "pitcher allowing {v:.1f} H/9 L20"),
    ("hits_park_factor",         lambda v: v >= 1.08,  "hitter-friendly park ({v:.2f}x hits factor)"),
    ("hits_park_factor",         lambda v: v <= 0.92,  "pitcher-friendly park ({v:.2f}x) — suppressed"),
    ("temperature_f",            lambda v: v >= 80,    "warm conditions ({v:.0f}F)"),
    ("temperature_f",            lambda v: v <= 50,    "cold conditions ({v:.0f}F) — suppressed"),
    ("ewma_batting_order",       lambda v: v <= 3.5,   "top-of-order spot ({v:.1f} avg batting order)"),
]

# ── GAME rules ────────────────────────────────────────────────────────────────
_GAME_RULES = [
    # Home starter edge
    ("home_k_pct_L3",              lambda v: v >= 0.28,  "home starter K% {v:.0%} L3"),
    ("home_xwoba_allowed_L3",      lambda v: v <= 0.290, "home starter elite contact suppression (xwOBA {v:.3f})"),
    ("home_xwoba_allowed_L3",      lambda v: v <= 0.320, "home starter strong contact suppression (xwOBA {v:.3f})"),
    ("home_velo_mean_L3",          lambda v: v >= 95,    "home starter high velo ({v:.1f} mph L3)"),
    ("home_bb_pct_L3",             lambda v: v <= 0.06,  "home starter strong command ({v:.0%} BB L3)"),
    ("home_starter_days_rest",     lambda v: v >= 5,     "home starter well-rested ({v:.0f} days)"),
    ("home_whiff_pct_L3",          lambda v: v >= 0.30,  "home starter high whiff rate ({v:.0%} L3)"),
    ("home_hard_hit_allowed_L3",   lambda v: v >= 0.45,  "home starter allowing hard contact ({v:.0%} L3) — risk"),
    ("home_hard_hit_allowed_L3",   lambda v: v <= 0.30,  "home starter suppressing hard contact ({v:.0%} L3)"),
    # Away starter weakness
    ("away_k_pct_L3",              lambda v: v <= 0.18,  "away starter low K rate ({v:.0%} L3) — risk"),
    ("away_xwoba_allowed_L3",      lambda v: v >= 0.340, "away starter weak contact suppression (xwOBA {v:.3f})"),
    ("away_bb_pct_L3",             lambda v: v >= 0.10,  "away starter high walk rate ({v:.0%} L3) — risk"),
    # Bullpen edge (key differentiator)
    ("home_bullpen_xwoba_L14",     lambda v: v <= 0.300, "home bullpen elite (xwOBA {v:.3f} L14)"),
    ("home_bullpen_ip_L7",         lambda v: v >= 12,    "home bullpen fresh ({v:.0f} IP L7)"),
    ("home_bullpen_whiff_pct_L14", lambda v: v >= 0.28,  "home bullpen high whiff rate ({v:.0%} L14)"),
    ("home_bullpen_hard_hit_L14",  lambda v: v >= 0.45,  "home bullpen allowing hard contact ({v:.0%} L14) — risk"),
    ("away_bullpen_xwoba_L14",     lambda v: v >= 0.340, "away bullpen weak (xwOBA {v:.3f} L14) — risk"),
    ("away_bullpen_ip_L7",         lambda v: v >= 18,    "away bullpen fatigued ({v:.0f} IP L7) — risk"),
    # Team offense
    ("home_team_woba_L20",         lambda v: v >= 0.340, "home offense hot ({v:.3f} wOBA L20)"),
    ("away_team_woba_L20",         lambda v: v <= 0.290, "away offense cold ({v:.3f} wOBA L20)"),
    ("home_run_diff_L20",          lambda v: v >= 15,    "home run differential +{v:.0f} L20"),
    ("home_team_hard_hit_L20",     lambda v: v >= 0.45,  "home offense hard contact ({v:.0%} L20)"),
    # Park / weather
    ("park_factor",                lambda v: v >= 1.08,  "hitter-friendly park ({v:.2f}x)"),
    ("park_factor",                lambda v: v <= 0.92,  "pitcher-friendly park ({v:.2f}x)"),
    ("temperature_f",              lambda v: v >= 80,    "warm conditions ({v:.0f}°F)"),
    ("wind_out",                   lambda v: v == 1,     "wind blowing out"),
]

# -- SB (stolen base) rules -----------------------------------------------
_SB_RULES = [
    ("sb_per_game_L20",       lambda v: v >= 0.4,   "averaging {v:.2f} SB/game L20"),
    ("sb_attempt_rate_L20",   lambda v: v >= 0.5,   "high attempt rate ({v:.2f} SB+CS/game L20)"),
    ("sb_success_pct_L50",    lambda v: v >= 0.80,  "efficient base-stealer ({v:.0%} success L50)"),
    ("sprint_speed_ft_sec",   lambda v: v >= 28.5,  "elite sprint speed ({v:.1f} ft/sec)"),
    ("times_on_base_L20",     lambda v: v >= 0.38,  "reaching base often ({v:.0%} L20)"),
    ("single_rate_L20",       lambda v: v >= 0.22,  "high singles rate ({v:.0%} L20) -- sets up steal chances"),
    ("catcher_pop_2b_sba",    lambda v: v >= 2.00,  "slow-armed catcher (pop time {v:.2f}s to 2B)"),
    ("catcher_pop_2b_sba",    lambda v: v <= 1.90,  "elite catcher arm (pop time {v:.2f}s to 2B) -- suppressed"),
    ("pitcher_sb_allowed",    lambda v: v >= 15,    "pitcher allows steals easily ({v:.0f} SB allowed this season)"),
    # pitcher_pickoffs: added 2026-08-21. Real 2024 B-Ref distribution
    # (IP>=20, n=543) is heavily zero-inflated -- median 0, 75th pct 1,
    # max 9 (Charlie Morton) -- so >=4 picks out a genuinely notable
    # pickoff move, not a typical pitcher.
    ("pitcher_pickoffs",      lambda v: v >= 4,     "known pickoff move ({v:.0f} pickoffs this season) -- suppressed"),
    ("p_throws_L",            lambda v: v == 1,     "facing a lefty -- real hold advantage vs 1B, suppressed"),
    ("ewma_batting_order",    lambda v: v <= 2.5,   "top-of-order spot ({v:.1f} avg batting order)"),
]

_SYSTEM_RULES = {
    "HR":          _HR_RULES,
    "1IOU":        _NRFI_RULES,
    "NRFI":        _NRFI_RULES,
    "YRFI":        _NRFI_RULES,
    "K":           _K_RULES,
    "OUTS":        _OUTS_RULES,
    "F5":          _F5_RULES,
    "BATTER_HITS": _BATTER_HITS_RULES,
    "SB":          _SB_RULES,
    "GAME":        _GAME_RULES,
}


def build_rationale(row: dict, system: str, max_phrases: int = 3) -> str:
    """
    Given a feature row dict and system name, return a plain-English
    rationale string of up to max_phrases clauses joined by ' · '.
    Returns "" if no rules fire or system not recognised.
    """
    rules = _SYSTEM_RULES.get(system, [])
    phrases = []
    seen_features: set[str] = set()

    for feature, test_fn, template in rules:
        if feature in seen_features:
            continue
        v = row.get(feature)
        if v is None:
            continue
        try:
            v_float = float(v)
        except (TypeError, ValueError):
            continue
        try:
            if test_fn(v_float):
                phrases.append(_fmt(template, v_float))
                seen_features.add(feature)
                if len(phrases) >= max_phrases:
                    break
        except Exception:
            continue

    return " · ".join(phrases)
