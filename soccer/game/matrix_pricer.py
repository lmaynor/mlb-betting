"""soccer.game.matrix_pricer -- ONE 11x11 Dixon-Coles scoreline matrix, every match market derived from it.

Given expected goals (lambda_home, lambda_away) and the Dixon-Coles low-score correlation rho, the matrix
M[x][y] = tau_rho(x, y) * Pois(x; lambda_home) * Pois(y; lambda_away) over x, y in 0..10, normalized to sum 1.
tau adjusts only the four low-score cells (0,0), (0,1), (1,0), (1,1) -- where independent Poisson misprices
real football (too few 0-0 / 1-1 draws, too many 1-0 / 0-1 splits at typical rho < 0):

    tau(0, 0) = 1 - lambda_home * lambda_away * rho
    tau(0, 1) = 1 + lambda_home * rho
    tau(1, 0) = 1 + lambda_away * rho
    tau(1, 1) = 1 - rho

1X2, double chance, draw-no-bet, the totals ladder (with whole-goal pushes handled explicitly), BTTS and the
handicap ladder are all SUMS over this one matrix, so the priced markets can never contradict each other.
Pure stdlib math: no numpy, no fitted state, fully deterministic.
"""

from __future__ import annotations

import math

MAX_GOALS = 10
TOTAL_LINES = (1.5, 2.0, 2.5, 3.0, 3.5, 4.5)
HANDICAP_LINES = (-1.5, -0.5, 0.5, 1.5)   # from the HOME team's perspective (home line -1.5 == home wins by 2+)


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def tau(x: int, y: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    """The Dixon-Coles low-score adjustment; 1.0 outside the four adjusted cells."""
    if (x, y) == (0, 0):
        return 1.0 - lambda_home * lambda_away * rho
    if (x, y) == (0, 1):
        return 1.0 + lambda_home * rho
    if (x, y) == (1, 0):
        return 1.0 + lambda_away * rho
    if (x, y) == (1, 1):
        return 1.0 - rho
    return 1.0


def build_matrix(lambda_home: float, lambda_away: float, rho: float, max_goals: int = MAX_GOALS) -> list[list[float]]:
    """The normalized 11x11 scoreline matrix M[x][y] (x = home goals, y = away goals). Cells are clipped at 0
    before normalization (an extreme lambda * rho can push a tau cell marginally negative; a probability cannot
    be), then the matrix is renormalized to sum to exactly 1."""
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError(f"expected goals must be positive, got {lambda_home}, {lambda_away}")
    if not -0.25 <= rho <= 0.25:
        raise ValueError(f"rho must be in [-0.25, 0.25], got {rho}")
    ph = [_poisson_pmf(x, lambda_home) for x in range(max_goals + 1)]
    pa = [_poisson_pmf(y, lambda_away) for y in range(max_goals + 1)]
    matrix = []
    total = 0.0
    for x in range(max_goals + 1):
        row = []
        for y in range(max_goals + 1):
            v = tau(x, y, lambda_home, lambda_away, rho) * ph[x] * pa[y]
            v = max(0.0, v)
            row.append(v)
            total += v
        matrix.append(row)
    if total <= 0:
        raise ValueError("degenerate scoreline matrix")
    return [[v / total for v in row] for row in matrix]


def price_matrix(matrix: list[list[float]]) -> dict:
    """Every Phase 1 match market as sums over the matrix. Totals carry the raw over/under masses plus the push
    mass (whole-goal lines 2.0 / 3.0) and the no-push fair pair the books' two-way market prices."""
    n = len(matrix)
    p_home = sum(matrix[x][y] for x in range(n) for y in range(n) if x > y)
    p_draw = sum(matrix[k][k] for k in range(n))
    p_away = sum(matrix[x][y] for x in range(n) for y in range(n) if x < y)

    totals: dict[str, dict] = {}
    for line in TOTAL_LINES:
        over_raw = sum(matrix[x][y] for x in range(n) for y in range(n) if x + y > line)
        under_raw = sum(matrix[x][y] for x in range(n) for y in range(n) if x + y < line)
        push = sum(matrix[x][y] for x in range(n) for y in range(n) if x + y == line)
        denom = over_raw + under_raw
        totals[f"{line:g}"] = {
            "line": line,
            "p_over": over_raw / denom if denom > 0 else None,
            "p_under": under_raw / denom if denom > 0 else None,
            "p_over_raw": over_raw, "p_under_raw": under_raw, "p_push": push,
        }

    p_btts_yes = sum(matrix[x][y] for x in range(1, n) for y in range(1, n))
    handicaps: dict[str, float] = {}
    for line in HANDICAP_LINES:
        # home covers `line` when (home_goals + line) > away_goals; all lines are half-goals, so no push exists.
        handicaps[f"{line:+g}"] = sum(matrix[x][y] for x in range(n) for y in range(n) if x + line > y)

    decided = p_home + p_away
    return {
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_1x": p_home + p_draw, "p_x2": p_away + p_draw, "p_12": p_home + p_away,
        "p_home_dnb": p_home / decided if decided > 0 else None,
        "p_away_dnb": p_away / decided if decided > 0 else None,
        "totals": totals,
        "p_btts_yes": p_btts_yes, "p_btts_no": 1.0 - p_btts_yes,
        "handicaps": handicaps,
    }


def price_match(lambda_home: float, lambda_away: float, rho: float) -> tuple[list[list[float]], dict]:
    """(matrix, priced markets) for one fixture."""
    matrix = build_matrix(lambda_home, lambda_away, rho)
    return matrix, price_matrix(matrix)
