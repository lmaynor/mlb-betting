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

_SYSTEM_RULES = {
    "HR":   _HR_RULES,
    "NRFI": _NRFI_RULES,
    "YRFI": _NRFI_RULES,
    "K":    _K_RULES,
    "OUTS": _OUTS_RULES,
    "F5":   _F5_RULES,
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
