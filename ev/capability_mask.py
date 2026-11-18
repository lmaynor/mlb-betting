"""ev/capability_mask.py -- Module 1: Capability Mask.

Stops the engine from hallucinating +EV on physically impossible alt lines
(e.g. Andre Drummond 50+ points) and routes every prop quote to its correct
execution mode:
  - OVER_ELIGIBLE: True fat-tail upside, eligible for ladder Over betting.
  - ROUTE_TO_NO_TAKER: Physical impossibility or retail-skewed trap; lay NO.
  - SGP_PROXY_ONLY: Synthetic proxy preferred over retail novelty hold.
  - CONSENSUS_OUTLIER_ONLY: Direct model edge clamped; consensus outlier only.

Evaluated in three sequential stages:
  Stage 1: Empirical Capability Ceiling ("Drummond 50-Point Rule")
  Stage 2: Sport & Stat Variance-Generator Archetype Mask
  Stage 3: Concave Winner's-Curse Damper & Peer Corroboration Gate
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

RoutingDecision = Literal[
    "OVER_ELIGIBLE",
    "ROUTE_TO_NO_TAKER",
    "SGP_PROXY_ONLY",
    "CONSENSUS_OUTLIER_ONLY",
]

SHARP_EXCHANGES = frozenset({"pinnacle", "novig", "prophetx"})


@dataclass
class CapabilityDecision:
    p_over_masked: float
    p_under_masked: float
    routing: RoutingDecision
    reason_code: str
    disp: float | None = None
    edge_pp: float | None = None

    @property
    def p_lambda(self) -> float:
        """P_lambda is the final masked fair probability after all stages."""
        return self.p_over_masked


class ArchetypeResult(namedtuple("ArchetypeResult", ["routing", "reason_code"])):
    """Result of Stage 2 Archetype Gate evaluation."""

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.routing == other
        return super().__eq__(other)


class DamperResult(namedtuple("DamperResult", ["passed", "p_effective", "reason_code"])):
    """Result of Stage 3 Winner's-Curse Damper evaluation."""

    def __bool__(self) -> bool:
        return self.passed

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return self.passed == other
        return super().__eq__(other)


def check_empirical_ceiling(
    line: float,
    history_values: list[float],
    minutes_proj: float,
    minutes_hist: list[float] | None = None,
    p_over_raw: float = 0.5,
) -> CapabilityDecision:
    """Stage 1: Empirical Capability Ceiling ('The Drummond 50-Point Rule').

    Filters history_values to games within [-6.0, +6.0] minutes of minutes_proj.
    Falls back to trailing 50 games if < 15 minutes-matched games exist.
    If line > max(1.15 * q95, max_obs + 1.0) AND zero exceedances across >= 30 games:
      Clamp p_over_masked = min(p_over_raw, 0.005), route ROUTE_TO_NO_TAKER.
    """
    if not history_values:
        return CapabilityDecision(
            p_over_masked=p_over_raw,
            p_under_masked=1.0 - p_over_raw,
            routing="OVER_ELIGIBLE",
            reason_code="NO_HISTORY",
        )

    matched_vals: list[float] = []
    if minutes_hist and len(minutes_hist) == len(history_values):
        matched_vals = [
            v for v, m in zip(history_values, minutes_hist)
            if abs(m - minutes_proj) <= 6.0
        ]

    # Fallback to trailing 50 games if < 15 minutes-matched games exist
    if len(matched_vals) < 15:
        matched_vals = list(history_values[-50:])

    # Check sample size across >= 30 games (either matched or total history)
    n_sample = max(len(matched_vals), len(history_values))
    if n_sample < 30:
        return CapabilityDecision(
            p_over_masked=p_over_raw,
            p_under_masked=1.0 - p_over_raw,
            routing="OVER_ELIGIBLE",
            reason_code="INSUFFICIENT_SAMPLE_LT_30",
        )

    vals_to_check = matched_vals if len(matched_vals) >= 30 else history_values[-50:]
    q95 = float(np.percentile(vals_to_check, 95))
    max_obs = float(max(vals_to_check))
    ceiling_threshold = max(1.15 * q95, max_obs + 1.0)
    zero_exceedances = sum(1 for v in vals_to_check if v > line) == 0

    if line > ceiling_threshold and zero_exceedances:
        p_clamped = min(p_over_raw, 0.005)
        return CapabilityDecision(
            p_over_masked=p_clamped,
            p_under_masked=1.0 - p_clamped,
            routing="ROUTE_TO_NO_TAKER",
            reason_code="CAPABILITY_CEILING_ZERO",
        )

    return CapabilityDecision(
        p_over_masked=p_over_raw,
        p_under_masked=1.0 - p_over_raw,
        routing="OVER_ELIGIBLE",
        reason_code="CEILING_PASSED",
    )


def evaluate_archetype_gate(
    sport: str,
    stat: str,
    line: float,
    main_line: float,
    features: dict[str, Any] | None = None,
) -> ArchetypeResult:
    """Stage 2: Sport & Stat Variance-Generator Archetype Mask.

    Only allow OVER_ELIGIBLE on alt ladders (line > main_line * 1.15) when the
    player's underlying physical mechanics generate right-skewed fat tails.
    """
    if features is None:
        features = {}

    sport = sport.lower().strip()
    stat = stat.lower().strip()
    is_alt = line > (main_line * 1.15 + 1e-6)

    # ------------------ NBA ------------------
    if sport == "nba":
        if stat in ("points", "pts"):
            spread = abs(features.get("spread", 0.0))
            if spread >= 13.5:
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NBA_BLOWOUT_SCRIPT")
            fg3a_ratio = features.get("fg3a_per_fga", 0.0)
            if fg3a_ratio < 0.20 and line >= 30.0:
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NBA_INTERIOR_BIG_ALT")
            if features.get("star_hype", False) and line >= 40.0:
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NBA_STAR_HYPE")

            if is_alt or line >= 25.0:
                vacated = features.get("vacated_usage_share", 0.0)
                if fg3a_ratio >= 0.55 and vacated >= 0.04 and spread <= 5.5:
                    return ArchetypeResult("OVER_ELIGIBLE", "NBA_HIGH_3PAR_WING")
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NBA_POINTS_ALT_GATE")
            return ArchetypeResult("OVER_ELIGIBLE", "NBA_MAIN_POINTS")

        if stat in ("rebounds", "reb", "threes", "3pm", "three_pointers"):
            return ArchetypeResult("OVER_ELIGIBLE", "NBA_REB_THREES")

        if stat in ("assists", "ast"):
            starter_out = features.get("primary_ballhandler_out", False) or features.get("starter_out", False)
            ast_diff = features.get("potential_ast_l5", 0.0) - features.get("ast_l5", 0.0)
            if starter_out and ast_diff >= 2.5:
                return ArchetypeResult("OVER_ELIGIBLE", "NBA_BACKUP_PG_EXPANSION")
            return ArchetypeResult("SGP_PROXY_ONLY", "NBA_ASSISTS_STANDALONE_BLOCK")

        if stat in ("pra", "pr", "pa", "ra"):
            return ArchetypeResult("SGP_PROXY_ONLY", "NBA_COMBO_STANDALONE_BLOCK")

        if stat in ("dd", "td", "double_double", "triple_double", "first_basket"):
            if features.get("exchange_combo", False):
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NBA_EXCHANGE_COMBO_TD_FADE")
            return ArchetypeResult("SGP_PROXY_ONLY", "NBA_DD_TD_RETAIL_FADE")

        return ArchetypeResult("OVER_ELIGIBLE", "NBA_GENERIC")

    # ------------------ NFL ------------------
    if sport == "nfl":
        if stat in ("rec_yds", "receptions", "rec", "receiving_yards"):
            adot = features.get("adot", 10.0)
            if adot < 6.5 and line >= 79.5:
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_LOW_ADOT_HIGH_RUNG")

            if is_alt or line >= 60.0:
                tgt_share = features.get("target_share_l6", features.get("target_share", 0.0))
                gap = features.get("gap", features.get("role_exp_yds", 0.0) - features.get("realized_yds_l4", 0.0))
                if 0.08 <= tgt_share <= 0.24 and adot >= 9.5 and gap >= 8.0:
                    return ArchetypeResult("OVER_ELIGIBLE", "NFL_FAT_TAIL_WR")
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_REC_ALT_GATE")
            return ArchetypeResult("OVER_ELIGIBLE", "NFL_REC_MAIN")

        if stat in ("rush_yds", "rush_att", "carries", "rushing_yards"):
            if features.get("n_ol_out", 0) >= 2 or (features.get("ypc_l10", 4.5) <= 3.8 and line >= 89.5):
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_OL_OR_EFFICIENCY_DEFICIT")

            if is_alt or line >= 80.0:
                breakaway = features.get("breakaway_pct", 0.0)
                ol_out = features.get("n_ol_out", 0)
                team_spread = features.get("team_spread", 0.0)
                if breakaway >= 0.28 and ol_out == 0 and team_spread <= -4.5:
                    return ArchetypeResult("OVER_ELIGIBLE", "NFL_CLOCK_KILL_RB")
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_RUSH_ALT_GATE")
            return ArchetypeResult("OVER_ELIGIBLE", "NFL_RUSH_MAIN")

        if stat in ("pass_yds", "pass_comp", "pass_att", "interceptions", "passing_yards"):
            if line >= 300.0:
                wind = features.get("wind_mph", 0.0)
                shell = features.get("two_high_shell_rate", 0.0)
                team_spread = features.get("team_spread", 0.0)
                if wind > 14.0 or shell >= 0.55 or team_spread <= -7.5:
                    return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_PASS_ALT_ENVIRONMENT")
            return ArchetypeResult("CONSENSUS_OUTLIER_ONLY", "NFL_PASS_OVER_MODEL_CLAMP")

        if stat in ("anytime_td", "first_td", "kicking_pts", "td"):
            if features.get("star_hype", False) or features.get("public_hype", False):
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NFL_TD_PUBLIC_HYPE")
            return ArchetypeResult("CONSENSUS_OUTLIER_ONLY", "NFL_TD_POWER_DEVIG")

        return ArchetypeResult("OVER_ELIGIBLE", "NFL_GENERIC")

    # ------------------ NHL ------------------
    if sport == "nhl":
        if stat in ("sog", "shots_on_goal"):
            if is_alt or line >= 3.5:
                gap = features.get("icf_implied_sog_l5", 0.0) - features.get("realized_sog_l5", 0.0)
                if gap >= 1.1:
                    return ArchetypeResult("OVER_ELIGIBLE", "NHL_SOG_ICF_SURPLUS")
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NHL_SOG_ALT_GATE")
            return ArchetypeResult("OVER_ELIGIBLE", "NHL_SOG_MAIN")

        if stat in ("saves", "goalie_saves"):
            rotowire = features.get("rotowire_confirmed", False)
            espn = features.get("espn_confirmed", False)
            if not (rotowire and espn):
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NHL_STARTER_UNCONFIRMED")
            return ArchetypeResult("OVER_ELIGIBLE", "NHL_SAVES_CONFIRMED")

        if stat in ("blocked_shots", "blocks"):
            return ArchetypeResult("OVER_ELIGIBLE", "NHL_BLOCKED_SHOTS")

        if stat in ("goals", "goal"):
            if line == 1.5:
                zero_fee = features.get("is_zero_fee_exchange", False)
                ixg_gap = features.get("ixg_l10", 0.0) - features.get("goals_l10", 0.0)
                on_pp1 = features.get("on_pp1", False)
                if zero_fee and ixg_gap >= 1.8 and on_pp1:
                    return ArchetypeResult("OVER_ELIGIBLE", "NHL_2PLUS_GOALS_ZERO_FEE")
                return ArchetypeResult("ROUTE_TO_NO_TAKER", "NHL_2PLUS_GOALS_GATE")
            return ArchetypeResult("OVER_ELIGIBLE", "NHL_GOALS_MAIN")

        if stat in ("points", "assists", "pp_points"):
            if line == 1.5 and stat == "points":
                return ArchetypeResult("SGP_PROXY_ONLY", "NHL_2PLUS_POINTS_SGP_PROXY")
            return ArchetypeResult("OVER_ELIGIBLE", "NHL_POINTS_MAIN")

        return ArchetypeResult("OVER_ELIGIBLE", "NHL_GENERIC")

    # ------------------ MLB ------------------
    if sport == "mlb":
        if stat in ("k", "strikeouts", "pitcher_strikeouts"):
            if line >= 7.5:
                pitch_count = features.get("projected_pitch_count", 100.0)
                chase_bottom = features.get("chase_rate_bottom_quartile", False)
                opp_chase = features.get("opp_chase_rate", 0.30)
                if pitch_count < 92.0 or chase_bottom or opp_chase < 0.26:
                    return ArchetypeResult("ROUTE_TO_NO_TAKER", "MLB_PITCH_COUNT_OR_CHASE_DEFICIT")
            return ArchetypeResult("OVER_ELIGIBLE", "MLB_K_CALIBRATED")

        if stat in ("outs", "pitcher_outs", "er", "earned_runs"):
            return ArchetypeResult("OVER_ELIGIBLE", "MLB_PITCHER_MAIN")

        if stat in ("hr", "home_runs"):
            return ArchetypeResult("ROUTE_TO_NO_TAKER", "MLB_HR_NO_LAY")

        if stat in ("tb", "total_bases", "hits"):
            if is_alt:
                return ArchetypeResult("SGP_PROXY_ONLY", "MLB_BATTER_SGP_PROXY")
            return ArchetypeResult("OVER_ELIGIBLE", "MLB_BATTER_MAIN")

        if stat in ("sb", "stolen_bases", "nrfi", "f5", "full_game"):
            return ArchetypeResult("OVER_ELIGIBLE", "MLB_MAIN_ELIGIBLE")

        return ArchetypeResult("OVER_ELIGIBLE", "MLB_GENERIC")

    # ------------------ SOCCER ------------------
    if sport == "soccer":
        n_books = features.get("n_two_sided_books", 5)
        if n_books < 5:
            return ArchetypeResult("ROUTE_TO_NO_TAKER", "SOCCER_MIN_BOOKS_GATE")
        return ArchetypeResult("CONSENSUS_OUTLIER_ONLY", "SOCCER_CONSENSUS_ONLY")

    return ArchetypeResult("OVER_ELIGIBLE", "DEFAULT_OVER_ELIGIBLE")


def apply_winners_curse_damper(
    p_model: float,
    q_book: float,
    p_peer_med: float,
    n_peer_books: int,
    sharp_books: list[str] | set[str] | None = None,
    has_sharp_peer: bool = False,
) -> DamperResult:
    """Stage 3: Concave Winner's-Curse Damper & Peer Corroboration Gate.

    - Hard Veto 1: pop_delta > 0.120 vetoes the Over.
    - Hard Veto 2: Peer corroboration lock requires:
        (n_peer_books >= 2 or has_sharp_peer) AND
        (p_peer_med - q_book >= 0.015) AND
        (p_model - p_peer_med <= 0.060).
    - Concave damper: for 0.080 < pop_delta <= 0.120,
        p_effective = q_book + 0.080 + 0.50 * (pop_delta - 0.080).
    """
    pop_delta = p_model - q_book

    # Hard Veto 1 (Upper Divergence Cap)
    if pop_delta > 0.120:
        return DamperResult(False, q_book, "DAMPER_VETO_UPPER_DIVERGENCE")

    # Check for sharp exchange representation
    if sharp_books:
        for b in sharp_books:
            if str(b).lower().strip() in SHARP_EXCHANGES:
                has_sharp_peer = True
                break

    # Hard Veto 2 (Peer Corroboration Lock)
    peer_count_ok = (n_peer_books >= 2) or has_sharp_peer
    peer_closer = (p_peer_med - q_book) >= 0.015
    model_corroborated = (p_model - p_peer_med) <= 0.060

    if not (peer_count_ok and peer_closer and model_corroborated):
        return DamperResult(False, q_book, "DAMPER_VETO_PEER_CORROBORATION")

    # Concave Damper
    if 0.080 < pop_delta <= 0.120:
        p_effective = q_book + 0.080 + 0.50 * (pop_delta - 0.080)
    else:
        p_effective = p_model

    return DamperResult(True, p_effective, "DAMPER_PASSED")


def apply_capability_mask(
    p_model: float,
    q_book: float,
    sport: str,
    stat: str,
    line: float,
    main_line: float | None = None,
    features: dict[str, Any] | None = None,
    history_values: list[float] | None = None,
    minutes_proj: float | None = None,
    minutes_hist: list[float] | None = None,
    p_peer_med: float | None = None,
    n_peer_books: int = 0,
    sharp_books: list[str] | set[str] | None = None,
    has_sharp_peer: bool = False,
    disp: float | None = None,
) -> CapabilityDecision:
    """Applies Stage 1, Stage 2, and Stage 3 in order. Returns CapabilityDecision."""
    if main_line is None:
        main_line = line
    if features is None:
        features = {}

    # Stage 1: Empirical Capability Ceiling
    if history_values is not None and len(history_values) >= 15:
        ceiling_dec = check_empirical_ceiling(
            line=line,
            history_values=history_values,
            minutes_proj=minutes_proj if minutes_proj is not None else 0.0,
            minutes_hist=minutes_hist,
            p_over_raw=p_model,
        )
        if ceiling_dec.routing == "ROUTE_TO_NO_TAKER":
            ceiling_dec.edge_pp = (ceiling_dec.p_over_masked - q_book) * 100.0
            ceiling_dec.disp = disp
            return ceiling_dec

    # Stage 2: Archetype Gate
    arch_res = evaluate_archetype_gate(
        sport=sport,
        stat=stat,
        line=line,
        main_line=main_line,
        features=features,
    )
    if arch_res.routing != "OVER_ELIGIBLE":
        return CapabilityDecision(
            p_over_masked=p_model,
            p_under_masked=1.0 - p_model,
            routing=arch_res.routing,
            reason_code=arch_res.reason_code,
            disp=disp,
            edge_pp=(p_model - q_book) * 100.0,
        )

    # Stage 3: Winner's-Curse Damper & Peer Corroboration
    pop_delta = p_model - q_book
    if p_peer_med is not None or pop_delta > 0.120:
        peer_med = p_peer_med if p_peer_med is not None else q_book
        damper_res = apply_winners_curse_damper(
            p_model=p_model,
            q_book=q_book,
            p_peer_med=peer_med,
            n_peer_books=n_peer_books,
            sharp_books=sharp_books,
            has_sharp_peer=has_sharp_peer,
        )
        if not damper_res.passed:
            return CapabilityDecision(
                p_over_masked=damper_res.p_effective,
                p_under_masked=1.0 - damper_res.p_effective,
                routing="ROUTE_TO_NO_TAKER",
                reason_code=damper_res.reason_code,
                disp=disp,
                edge_pp=(damper_res.p_effective - q_book) * 100.0,
            )
        p_final = damper_res.p_effective
        return CapabilityDecision(
            p_over_masked=p_final,
            p_under_masked=1.0 - p_final,
            routing="OVER_ELIGIBLE",
            reason_code=damper_res.reason_code,
            disp=disp,
            edge_pp=(p_final - q_book) * 100.0,
        )

    # When no peer median is provided and pop_delta <= 0.120
    return CapabilityDecision(
        p_over_masked=p_model,
        p_under_masked=1.0 - p_model,
        routing="OVER_ELIGIBLE",
        reason_code="ARCHETYPE_PASSED",
        disp=disp,
        edge_pp=(p_model - q_book) * 100.0,
    )
