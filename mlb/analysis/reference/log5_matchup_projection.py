"""
REFERENCE ONLY — NOT WIRED INTO ANY PIPELINE.

The Log5 / odds-ratio matchup method, extracted (and rewritten from Python 2.7)
from the external hobby project `MLBDailyProjections` (projections.py), reviewed
2026-07-01. That project was a 2017 DraftKings DFS engine (MySQL + gradient
descent + lineup optimizer) — all of which we discard. The ONE reusable,
era-agnostic idea is the matchup-rate primitive below.

────────────────────────────────────────────────────────────────────────────
WHAT IT IS
────────────────────────────────────────────────────────────────────────────
Log5 (Bill James) / the odds-ratio method (Tom Tango) answers: given a batter
who succeeds at rate B, a pitcher who allows success at rate P, and a league
baseline L, what's the expected rate in THIS batter-vs-pitcher matchup?

    matchup_rate = (B*P/L) / ( B*P/L + (1-B)(1-P)/(1-L) )

Equivalently in odds space, odds(matchup) = odds(B) * odds(P) / odds(L).
If the batter is league-average (B=L) it collapses to P, and vice-versa — the
desired property. Valid for probability-like rates in [0,1]: AVG, OBP, K%, BB%,
BABIP, wOBA (wOBA is ~[0,1]-scaled so it's a reasonable stretch).

The source layered two more adjustments on top, worth replicating:
  1. PLATOON: use the batter's split vs the OPPOSITE-hand pitcher split
     (LHB stats vs RHP, etc.; switch-hitters take the platoon-advantage side).
     Inputs were ZiPS projected platoon rates, not raw season splits — i.e. a
     projection system supplies B and P, log5 only does the *combination*.
  2. PARK: multiply the resulting rate by a handedness-split park run factor.

────────────────────────────────────────────────────────────────────────────
HONEST CAVEATS (why this is a primitive, not a plug-in model)
────────────────────────────────────────────────────────────────────────────
• The source applied the SAME formula to SLG / OPS / ISO. Those are NOT bounded
  in [0,1], so (1-x) is meaningless for them — that was a bug/hack. Only use
  log5 for genuine rate stats. For SLG/ISO use a linear-weights / run-value
  approach instead.
• Park adjustment as a naive post-multiply double-counts if B or P already came
  from park-neutralized projections (ZiPS is park-neutral-ish). Decide once
  where the park lives.
• This is a MODEL-VS-LINE primitive. Per our standing verdict
  (MEMORY: project_gen_preds_backtest_verdict) that's not the blocker — CLV is.
  Kept for whenever matchup-rate modeling resumes (e.g. sharpening K / batter-
  hits / HR priors), NOT as something to ship now.
"""

from __future__ import annotations


def log5(batter_rate: float, pitcher_rate: float, league_rate: float) -> float:
    """Odds-ratio (Log5) matchup rate for a probability-like stat in (0,1).

    Returns the expected success rate when `batter_rate` faces `pitcher_rate`
    against a `league_rate` baseline. All three must be in the open interval
    (0, 1); returns the clamped edge cases for 0/1 inputs.
    """
    b, p, lg = batter_rate, pitcher_rate, league_rate
    if not (0.0 < lg < 1.0):
        raise ValueError(f"league_rate must be in (0,1), got {lg}")
    # Degenerate inputs: if either side is certain, the matchup is certain.
    if b <= 0.0 or p <= 0.0:
        return 0.0
    if b >= 1.0 or p >= 1.0:
        return 1.0
    num = (b * p) / lg
    den = num + ((1.0 - b) * (1.0 - p)) / (1.0 - lg)
    return num / den


def log5_platoon(
    batter_rate_vs_hand: float,
    pitcher_rate_vs_hand: float,
    league_rate: float,
    park_factor: float = 1.0,
) -> float:
    """Log5 with a multiplicative park factor applied to the result.

    Caller is responsible for platoon matching: pass the batter's rate vs the
    pitcher's throwing hand and the pitcher's rate vs the batter's batting hand
    (switch-hitters -> take the platoon-advantage side). `park_factor` is a
    handedness-split run/rate multiplier centered at 1.0 (i.e. 105 -> 1.05).
    """
    return log5(batter_rate_vs_hand, pitcher_rate_vs_hand, league_rate) * park_factor


if __name__ == "__main__":  # tiny sanity check
    lg_k = 0.22
    # A high-K batter (30%) vs a high-K pitcher (28%) should exceed both.
    print("matchup K%:", round(log5(0.30, 0.28, lg_k), 4))
    # League-average batter vs a pitcher collapses to the pitcher's rate.
    print("B==L collapses to P:", round(log5(lg_k, 0.28, lg_k), 4), "(expect 0.28)")
