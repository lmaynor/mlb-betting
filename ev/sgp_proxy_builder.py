"""
ev.sgp_proxy_builder -- Two-Stage Pre-Filter & SGP Proxy Candidate Engine.

Reads standard BoardRow props from ev/odds_board.py (and odds_board_latest.json),
pre-filters multi-leg combos (Stage 1), computes true correlated joint fair
probability using empirical Gaussian copula rho, and predicts book SGP payout
(Stage 2) using fitted book copula parameters.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from ev.sgp_book_pricer import (
    SGP_ARCHETYPES,
    build_sgp_deeplink,
    copula_joint,
    predict_book_sgp_odds,
)
from mlb_core.odds.utils import (
    american_to_decimal,
    american_to_implied_prob,
    devig_two_way,
    implied_to_american,
)

log = logging.getLogger("ev.sgp_proxy_builder")

STANDARD_BETTABLE_BOOKS: Set[str] = {
    "fanduel", "draftkings", "betmgm", "caesars", "bet365",
    "fanatics", "hardrock", "espnbet", "betrivers", "novig", "prophetx",
}

MAX_CREDIBLE_EV_PCT = 45.0


@dataclass
class SgpLegCandidate:
    stat: str
    line: float
    side: str
    book_american: int
    fair_prob: float
    label: str = ""
    sid: Optional[str] = None
    market_id: Optional[str] = None
    selection_id: Optional[str] = None


@dataclass
class SgpCandidate:
    archetype: str
    archetype_label: str
    sport: str
    player: str
    player_norm: str
    game_key: str
    commence: str
    away: str
    home: str
    book: str
    legs: List[SgpLegCandidate]
    naive_product_american: int
    est_sgp_dec: float
    est_sgp_american: int
    fair_joint_prob: float
    fair_joint_american: int
    rho_true: float
    rho_book: float
    hold_book: float
    ev_pct: float
    team: Optional[str] = None
    player_id: Optional[Any] = None
    direct_market_label: Optional[str] = None
    direct_best_american: Optional[int] = None
    direct_best_book: Optional[str] = None
    vs_direct_pct: Optional[float] = None
    deeplink: Optional[str] = None
    runner_up_books: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["legs"] = [asdict(leg) for leg in self.legs]
        return d


def _fair_prob_of_row(
    row: Dict[str, Any],
    leave_out_book: Optional[str] = None,
) -> Optional[float]:
    """
    Compute leave-vig fair P(over) for a row.

    Priority 1: Leave-book-out de-vigged average across >= 2 two-sided books.
    Priority 2: curve_fair_over (from the player's fitted monotone curve).
    Priority 3: One-sided milestone vig haircut (median(implied_over) * 0.86)
                when >= 2 peer books quote Over with under=None.
    """
    books = row.get("books", [])
    if isinstance(books, dict):
        books = list(books.values())

    leave_b = leave_out_book.lower() if leave_out_book else None
    two_sided: List[float] = []
    onesided_implied: List[float] = []

    for b in books:
        b_name = str(b.get("book", "")).lower()
        if leave_b and b_name == leave_b:
            continue
        ov = b.get("over")
        un = b.get("under")
        if ov is not None and un is not None:
            try:
                p_o = american_to_implied_prob(float(ov))
                p_u = american_to_implied_prob(float(un))
                res = devig_two_way(p_o, p_u, "proportional")
                f = float(res[0]) if isinstance(res, (tuple, list)) else float(res)
                if not math.isnan(f) and 0.0 < f < 1.0:
                    two_sided.append(f)
            except Exception:
                continue
        elif ov is not None and un is None:
            try:
                p_o = american_to_implied_prob(float(ov))
                if 0.0 < p_o < 1.0:
                    onesided_implied.append(p_o)
            except Exception:
                continue

    if len(two_sided) >= 2:
        return float(sum(two_sided) / len(two_sided))

    curve = row.get("curve_fair_over")
    if curve is not None and 0.0 < float(curve) < 1.0:
        return float(curve)

    if len(two_sided) == 1:
        return float(two_sided[0])

    if len(onesided_implied) >= 2:
        med_implied = float(np.median(onesided_implied))
        return float(min(max(med_implied * 0.86, 0.01), 0.95))

    return None


def _n_books_backing(row: Dict[str, Any]) -> int:
    books = row.get("books", [])
    if isinstance(books, dict):
        books = list(books.values())
    return sum(1 for b in books if b.get("over") is not None)


def _best_price_on_row(
    row: Optional[Dict[str, Any]],
    side: str = "OVER",
    bettable: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Optional[int], Optional[float]]:
    """Return (best_book, best_american, best_decimal) for a given row and side."""
    if not row:
        return None, None, None
    books = row.get("books", [])
    if isinstance(books, dict):
        books = list(books.values())

    best_book: Optional[str] = None
    best_am: Optional[int] = None
    best_dec: float = -1.0

    for b in books:
        b_name = str(b.get("book", "")).lower()
        if bettable and b_name not in bettable:
            continue
        odds = b.get("over") if side.upper() == "OVER" else b.get("under")
        if odds is None:
            continue
        try:
            am = int(odds)
            dec = float(american_to_decimal(am))
            if dec > best_dec:
                best_dec = dec
                best_am = am
                best_book = b_name
        except Exception:
            continue

    return best_book, best_am, (best_dec if best_dec > 0.0 else None)


def build_sgp_proxy_candidates(
    rows: Sequence[Dict[str, Any]],
    min_ev_pct: float = 0.0,
    bettable: Optional[Set[str]] = None,
    sport: Optional[str] = None,
) -> List[SgpCandidate]:
    """
    Two-stage scan for SGP proxy value across banked BoardRow props.

    Stage 1: Validates single-leg support and fair probabilities in [0.04, 0.88].
    Stage 2: Applies fitted book copula (rho_book, hold_book), predicts SGP price,
             evaluates EV% (capped at 45%), and compares with direct markets.
    """
    allowed_books = bettable if bettable is not None else STANDARD_BETTABLE_BOOKS
    raw_candidates: List[SgpCandidate] = []

    # 1. Group by player in game
    by_player: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    # 2. Group by game and team for teammate combos
    by_game_team: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for r in rows:
        gk = str(r.get("game_key", ""))
        pn = str(r.get("player_norm", "") or r.get("player", "")).lower().strip()
        tm = str(r.get("team", "") or "").upper().strip()
        if gk and pn:
            by_player.setdefault((gk, pn), []).append(r)
        if gk and tm:
            by_game_team.setdefault((gk, tm), []).append(r)

    # --------------------------------------------------------------------------
    # Single-Player Proxies & Combos
    # --------------------------------------------------------------------------
    for (game_key, player_norm), p_rows in by_player.items():
        sample = p_rows[0]
        player = sample.get("player", player_norm)
        team = sample.get("team")
        commence = sample.get("commence", "")
        away = sample.get("away", "")
        home = sample.get("home", "")
        p_id = sample.get("player_id")
        sp = str(sport or sample.get("sport") or (game_key.split(":")[0] if ":" in game_key else "nba")).lower()

        # Stat map: (stat, line) -> row
        stat_map: Dict[Tuple[str, float], Dict[str, Any]] = {}
        for r in p_rows:
            stat_name = str(r.get("stat", "")).lower().strip()
            try:
                line = float(r.get("line", 0.0))
                stat_map[(stat_name, line)] = r
            except (ValueError, TypeError):
                continue

        # ----------------------------------------------------------------------
        # Archetype 1: NBA Triple-Double Proxy (10+ Reb + 10+ Ast [+ optional 10+ Pts])
        # ----------------------------------------------------------------------
        r_reb10 = stat_map.get(("rebounds", 9.5)) or stat_map.get(("rebounds", 10.0))
        r_ast10 = stat_map.get(("assists", 9.5)) or stat_map.get(("assists", 10.0))
        r_pts10 = stat_map.get(("points", 9.5)) or stat_map.get(("points", 10.0))

        if r_reb10 and r_ast10 and sp == "nba":
            arch = SGP_ARCHETYPES["nba_reb_ast_10_10"]
            f_reb = _fair_prob_of_row(r_reb10)
            f_ast = _fair_prob_of_row(r_ast10)
            f_pts = _fair_prob_of_row(r_pts10) if r_pts10 else None

            # Stage 1 Sanity: fair probs in [0.04, 0.88] and backed by >= 2 books or curve
            if (
                f_reb is not None and 0.04 <= f_reb <= 0.88
                and f_ast is not None and 0.04 <= f_ast <= 0.88
                and (_n_books_backing(r_reb10) >= 2 or r_reb10.get("curve_fair_over") is not None)
                and (_n_books_backing(r_ast10) >= 2 or r_ast10.get("curve_fair_over") is not None)
            ):
                main_pts_row = next((r for r in p_rows if str(r.get("stat", "")).lower() == "points" and not r.get("is_alt")), None)
                main_pts_line = float(main_pts_row.get("line", 20.0)) if main_pts_row else 20.0

                # Direct TD market lookup
                direct_row = next(
                    (r for r in p_rows if str(r.get("stat", "")).lower() in ("triple_double", "td", "player_triple_double")),
                    None,
                )
                dir_book, dir_am, dir_dec = _best_price_on_row(direct_row, "OVER", allowed_books)

                books_reb = {str(b.get("book", "")).lower(): b for b in r_reb10.get("books", []) if b.get("over") is not None}
                books_ast = {str(b.get("book", "")).lower(): b for b in r_ast10.get("books", []) if b.get("over") is not None}
                books_pts = {str(b.get("book", "")).lower(): b for b in r_pts10.get("books", []) if b.get("over") is not None} if r_pts10 else {}

                common_books = set(books_reb.keys()).intersection(books_ast.keys()).intersection(allowed_books)
                for b_name in common_books:
                    b_reb = books_reb[b_name]
                    b_ast = books_ast[b_name]
                    am_reb = int(b_reb["over"])
                    am_ast = int(b_ast["over"])

                    # 3-leg if book quotes points 10+ and f_pts valid
                    use_3leg = bool(b_name in books_pts and f_pts is not None and 0.04 <= f_pts <= 0.88)

                    if use_3leg and r_pts10:
                        b_pts = books_pts[b_name]
                        am_pts = int(b_pts["over"])
                        # Bivariate copula chained or 3D copula
                        p_true = copula_joint([f_pts, f_reb, f_ast], arch.rho_true)
                        pred = predict_book_sgp_odds(b_name, arch.key, [am_pts, am_reb, am_ast])
                        q_pts = american_to_implied_prob(am_pts)
                        q_reb = american_to_implied_prob(am_reb)
                        q_ast = american_to_implied_prob(am_ast)
                        p_naive = q_pts * q_reb * q_ast
                        legs = [
                            SgpLegCandidate(
                                stat="points",
                                line=float(r_pts10.get("line", 9.5)),
                                side="OVER",
                                book_american=am_pts,
                                fair_prob=round(f_pts, 4),
                                label=f"o{r_pts10.get('line', 9.5)} Pts",
                                sid=b_pts.get("over_sid") or b_pts.get("sid"),
                                market_id=b_pts.get("market_id"),
                                selection_id=b_pts.get("selection_id"),
                            ),
                            SgpLegCandidate(
                                stat="rebounds",
                                line=float(r_reb10.get("line", 9.5)),
                                side="OVER",
                                book_american=am_reb,
                                fair_prob=round(f_reb, 4),
                                label=f"o{r_reb10.get('line', 9.5)} Reb",
                                sid=b_reb.get("over_sid") or b_reb.get("sid"),
                                market_id=b_reb.get("market_id"),
                                selection_id=b_reb.get("selection_id"),
                            ),
                            SgpLegCandidate(
                                stat="assists",
                                line=float(r_ast10.get("line", 9.5)),
                                side="OVER",
                                book_american=am_ast,
                                fair_prob=round(f_ast, 4),
                                label=f"o{r_ast10.get('line', 9.5)} Ast",
                                sid=b_ast.get("over_sid") or b_ast.get("sid"),
                                market_id=b_ast.get("market_id"),
                                selection_id=b_ast.get("selection_id"),
                            ),
                        ]
                    else:
                        p_true_2leg = copula_joint([f_reb, f_ast], arch.rho_true)
                        # Conditional points factor: starter with 20+ pts line has ~95% conditional prob of 10+ pts
                        p_cond_pts = min(0.98, max(0.85, 0.70 + 0.012 * main_pts_line))
                        p_true = p_true_2leg * (p_cond_pts if direct_row else 1.0)
                        pred = predict_book_sgp_odds(b_name, arch.key, [am_reb, am_ast])
                        q_reb = american_to_implied_prob(am_reb)
                        q_ast = american_to_implied_prob(am_ast)
                        p_naive = q_reb * q_ast
                        legs = [
                            SgpLegCandidate(
                                stat="rebounds",
                                line=float(r_reb10.get("line", 9.5)),
                                side="OVER",
                                book_american=am_reb,
                                fair_prob=round(f_reb, 4),
                                label=f"o{r_reb10.get('line', 9.5)} Reb",
                                sid=b_reb.get("over_sid") or b_reb.get("sid"),
                                market_id=b_reb.get("market_id"),
                                selection_id=b_reb.get("selection_id"),
                            ),
                            SgpLegCandidate(
                                stat="assists",
                                line=float(r_ast10.get("line", 9.5)),
                                side="OVER",
                                book_american=am_ast,
                                fair_prob=round(f_ast, 4),
                                label=f"o{r_ast10.get('line', 9.5)} Ast",
                                sid=b_ast.get("over_sid") or b_ast.get("sid"),
                                market_id=b_ast.get("market_id"),
                                selection_id=b_ast.get("selection_id"),
                            ),
                        ]

                    ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                    if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                        continue

                    vs_dir = None
                    if dir_dec and dir_dec > 0.0:
                        vs_dir = round(((pred.decimal / dir_dec) - 1.0) * 100.0, 1)

                    dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nba")
                    raw_candidates.append(
                        SgpCandidate(
                            archetype=arch.key,
                            archetype_label=arch.label,
                            sport="nba",
                            player=player,
                            player_norm=player_norm,
                            game_key=game_key,
                            commence=commence,
                            away=away,
                            home=home,
                            book=b_name,
                            legs=legs,
                            naive_product_american=int(implied_to_american(p_naive)),
                            est_sgp_dec=pred.decimal,
                            est_sgp_american=pred.american,
                            fair_joint_prob=round(p_true, 4),
                            fair_joint_american=int(implied_to_american(p_true)),
                            rho_true=arch.rho_true,
                            rho_book=pred.rho_used,
                            hold_book=pred.hold_used,
                            ev_pct=round(ev_pct, 2),
                            team=team,
                            player_id=p_id,
                            direct_market_label="Triple-Double" if dir_am else None,
                            direct_best_american=dir_am,
                            direct_best_book=dir_book,
                            vs_direct_pct=vs_dir,
                            deeplink=dlink,
                        )
                    )

        # ----------------------------------------------------------------------
        # Archetype 2: NBA Stat Combos (P+R, P+A, R+A)
        # ----------------------------------------------------------------------
        main_pts = next((r for r in p_rows if str(r.get("stat", "")).lower() == "points" and not r.get("is_alt")), None)
        main_reb = next((r for r in p_rows if str(r.get("stat", "")).lower() == "rebounds" and not r.get("is_alt")), None)
        main_ast = next((r for r in p_rows if str(r.get("stat", "")).lower() == "assists" and not r.get("is_alt")), None)

        combo_configs = [
            (main_pts, main_reb, "pr", ("pr", "points_rebounds"), "nba_pts_reb_vs_pr", "P+R PROXY", "Pts", "Reb"),
            (main_pts, main_ast, "pa", ("pa", "points_assists"), "nba_pts_ast_vs_pa", "P+A PROXY", "Pts", "Ast"),
            (main_reb, main_ast, "ra", ("ra", "rebounds_assists"), "nba_reb_ast_vs_ra", "R+A PROXY", "Reb", "Ast"),
        ]

        if sp == "nba":
            for r1, r2, direct_code, direct_aliases, arch_key, arch_label, lbl1, lbl2 in combo_configs:
                if not r1 or not r2:
                    continue
                line1, line2 = float(r1.get("line", 0.0)), float(r2.get("line", 0.0))
                expected_sum = line1 + line2
                direct_row = next(
                    (r for r in p_rows if str(r.get("stat", "")).lower() in direct_aliases and abs(float(r.get("line", 0.0)) - expected_sum) <= 1.0),
                    None,
                )
                f1 = _fair_prob_of_row(r1)
                f2 = _fair_prob_of_row(r2)
                if (
                    f1 is not None and 0.04 <= f1 <= 0.88
                    and f2 is not None and 0.04 <= f2 <= 0.88
                    and (_n_books_backing(r1) >= 2 or r1.get("curve_fair_over") is not None)
                    and (_n_books_backing(r2) >= 2 or r2.get("curve_fair_over") is not None)
                ):
                    arch = SGP_ARCHETYPES[arch_key]
                    p_true = copula_joint([f1, f2], arch.rho_true)
                    dir_book, dir_am, dir_dec = _best_price_on_row(direct_row, "OVER", allowed_books)

                    b1_map = {str(b.get("book", "")).lower(): b for b in r1.get("books", []) if b.get("over") is not None}
                    b2_map = {str(b.get("book", "")).lower(): b for b in r2.get("books", []) if b.get("over") is not None}
                    common = set(b1_map.keys()).intersection(b2_map.keys()).intersection(allowed_books)

                    for b_name in common:
                        b1 = b1_map[b_name]
                        b2 = b2_map[b_name]
                        am1, am2 = int(b1["over"]), int(b2["over"])
                        q1, q2 = american_to_implied_prob(am1), american_to_implied_prob(am2)
                        p_naive = q1 * q2

                        pred = predict_book_sgp_odds(b_name, arch.key, [am1, am2])
                        ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                        if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                            continue

                        vs_dir = None
                        if dir_dec and dir_dec > 0.0:
                            vs_dir = round(((pred.decimal / dir_dec) - 1.0) * 100.0, 1)

                        legs = [
                            SgpLegCandidate(
                                stat=str(r1.get("stat")),
                                line=line1,
                                side="OVER",
                                book_american=am1,
                                fair_prob=round(f1, 4),
                                label=f"o{line1} {lbl1}",
                                sid=b1.get("over_sid") or b1.get("sid"),
                            ),
                            SgpLegCandidate(
                                stat=str(r2.get("stat")),
                                line=line2,
                                side="OVER",
                                book_american=am2,
                                fair_prob=round(f2, 4),
                                label=f"o{line2} {lbl2}",
                                sid=b2.get("over_sid") or b2.get("sid"),
                            ),
                        ]
                        dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nba")
                        raw_candidates.append(
                            SgpCandidate(
                                archetype=arch.key,
                                archetype_label=arch.label,
                                sport="nba",
                                player=player,
                                player_norm=player_norm,
                                game_key=game_key,
                                commence=commence,
                                away=away,
                                home=home,
                                book=b_name,
                                legs=legs,
                                naive_product_american=int(implied_to_american(p_naive)),
                                est_sgp_dec=pred.decimal,
                                est_sgp_american=pred.american,
                                fair_joint_prob=round(p_true, 4),
                                fair_joint_american=int(implied_to_american(p_true)),
                                rho_true=arch.rho_true,
                                rho_book=pred.rho_used,
                                hold_book=pred.hold_used,
                                ev_pct=round(ev_pct, 2),
                                team=team,
                                player_id=p_id,
                                direct_market_label=f"{direct_code.upper()} o{direct_row.get('line')}" if dir_am and direct_row else None,
                                direct_best_american=dir_am,
                                direct_best_book=dir_book,
                                vs_direct_pct=vs_dir,
                                deeplink=dlink,
                            )
                        )

        # ----------------------------------------------------------------------
        # Archetype 3: NHL 1+ Goal + 1+ Assist vs 2+ Points
        # ----------------------------------------------------------------------
        r_goal = stat_map.get(("goals", 0.5))
        r_ast = stat_map.get(("assists", 0.5))
        r_pts2 = stat_map.get(("points", 1.5))
        if r_goal and r_ast and sp == "nhl":
            arch = SGP_ARCHETYPES["nhl_goal_ast_vs_pts"]
            f_goal = _fair_prob_of_row(r_goal)
            f_ast = _fair_prob_of_row(r_ast)
            if (
                f_goal is not None and 0.04 <= f_goal <= 0.88
                and f_ast is not None and 0.04 <= f_ast <= 0.88
                and (_n_books_backing(r_goal) >= 2 or r_goal.get("curve_fair_over") is not None)
                and (_n_books_backing(r_ast) >= 2 or r_ast.get("curve_fair_over") is not None)
            ):
                p_true = copula_joint([f_goal, f_ast], arch.rho_true)
                dir_book, dir_am, dir_dec = _best_price_on_row(r_pts2, "OVER", allowed_books)

                books_g = {str(b.get("book", "")).lower(): b for b in r_goal.get("books", []) if b.get("over") is not None}
                books_a = {str(b.get("book", "")).lower(): b for b in r_ast.get("books", []) if b.get("over") is not None}
                common = set(books_g.keys()).intersection(books_a.keys()).intersection(allowed_books)

                for b_name in common:
                    b_g = books_g[b_name]
                    b_a = books_a[b_name]
                    am_g, am_a = int(b_g["over"]), int(b_a["over"])
                    q_g, q_a = american_to_implied_prob(am_g), american_to_implied_prob(am_a)
                    p_naive = q_g * q_a

                    pred = predict_book_sgp_odds(b_name, arch.key, [am_g, am_a])
                    ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                    if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                        continue

                    vs_dir = None
                    if dir_dec and dir_dec > 0.0:
                        vs_dir = round(((pred.decimal / dir_dec) - 1.0) * 100.0, 1)

                    legs = [
                        SgpLegCandidate(
                            stat="goals",
                            line=0.5,
                            side="OVER",
                            book_american=am_g,
                            fair_prob=round(f_goal, 4),
                            label="1+ Goal",
                            sid=b_g.get("over_sid") or b_g.get("sid"),
                        ),
                        SgpLegCandidate(
                            stat="assists",
                            line=0.5,
                            side="OVER",
                            book_american=am_a,
                            fair_prob=round(f_ast, 4),
                            label="1+ Assist",
                            sid=b_a.get("over_sid") or b_a.get("sid"),
                        ),
                    ]
                    dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nhl")
                    raw_candidates.append(
                        SgpCandidate(
                            archetype=arch.key,
                            archetype_label=arch.label,
                            sport="nhl",
                            player=player,
                            player_norm=player_norm,
                            game_key=game_key,
                            commence=commence,
                            away=away,
                            home=home,
                            book=b_name,
                            legs=legs,
                            naive_product_american=int(implied_to_american(p_naive)),
                            est_sgp_dec=pred.decimal,
                            est_sgp_american=pred.american,
                            fair_joint_prob=round(p_true, 4),
                            fair_joint_american=int(implied_to_american(p_true)),
                            rho_true=arch.rho_true,
                            rho_book=pred.rho_used,
                            hold_book=pred.hold_used,
                            ev_pct=round(ev_pct, 2),
                            team=team,
                            player_id=p_id,
                            direct_market_label="Over 1.5 Points" if dir_am else None,
                            direct_best_american=dir_am,
                            direct_best_book=dir_book,
                            vs_direct_pct=vs_dir,
                            deeplink=dlink,
                        )
                    )

    # --------------------------------------------------------------------------
    # Multi-Player Teammate Combos (NHL Linemates, NFL QB+WR, NBA Ast+Pts)
    # --------------------------------------------------------------------------
    for (game_key, team_code), t_rows in by_game_team.items():
        sample = t_rows[0]
        commence = sample.get("commence", "")
        away = sample.get("away", "")
        home = sample.get("home", "")
        sp = str(sport or sample.get("sport") or (game_key.split(":")[0] if ":" in game_key else "")).lower()

        # Group rows by player on this team
        team_players: Dict[str, List[Dict[str, Any]]] = {}
        for r in t_rows:
            pn = str(r.get("player_norm", "") or r.get("player", "")).lower().strip()
            if pn:
                team_players.setdefault(pn, []).append(r)

        # ----------------------------------------------------------------------
        # Teammate 1: NHL Linemates 1+ Point Stack
        # ----------------------------------------------------------------------
        if sp == "nhl":
            arch = SGP_ARCHETYPES["nhl_linemate_pts_stack"]
            skaters = []
            for pn, pl_rows in team_players.items():
                pts_row = next((r for r in pl_rows if str(r.get("stat")).lower() == "points" and float(r.get("line", 0.0)) == 0.5), None)
                if pts_row:
                    f_pts = _fair_prob_of_row(pts_row)
                    if f_pts is not None and 0.04 <= f_pts <= 0.88 and (_n_books_backing(pts_row) >= 2 or pts_row.get("curve_fair_over") is not None):
                        skaters.append({
                            "player": pts_row.get("player", pn),
                            "player_norm": pn,
                            "fair_prob": f_pts,
                            "row": pts_row,
                        })
            skaters.sort(key=lambda s: s["fair_prob"], reverse=True)
            top_skaters = skaters[:3]

            for i in range(len(top_skaters)):
                for j in range(i + 1, len(top_skaters)):
                    s1 = top_skaters[i]
                    s2 = top_skaters[j]
                    b1_map = {str(b.get("book", "")).lower(): b for b in s1["row"].get("books", []) if b.get("over") is not None}
                    b2_map = {str(b.get("book", "")).lower(): b for b in s2["row"].get("books", []) if b.get("over") is not None}
                    common = set(b1_map.keys()).intersection(b2_map.keys()).intersection(allowed_books)

                    for b_name in common:
                        b1 = b1_map[b_name]
                        b2 = b2_map[b_name]
                        am1, am2 = int(b1["over"]), int(b2["over"])
                        q1, q2 = american_to_implied_prob(am1), american_to_implied_prob(am2)
                        p_naive = q1 * q2

                        p_true = copula_joint([s1["fair_prob"], s2["fair_prob"]], arch.rho_true)
                        pred = predict_book_sgp_odds(b_name, arch.key, [am1, am2])
                        ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                        if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                            continue

                        legs = [
                            SgpLegCandidate(
                                stat="points",
                                line=0.5,
                                side="OVER",
                                book_american=am1,
                                fair_prob=round(s1["fair_prob"], 4),
                                label=f"{s1['player']} 1+ Pt",
                                sid=b1.get("over_sid") or b1.get("sid"),
                            ),
                            SgpLegCandidate(
                                stat="points",
                                line=0.5,
                                side="OVER",
                                book_american=am2,
                                fair_prob=round(s2["fair_prob"], 4),
                                label=f"{s2['player']} 1+ Pt",
                                sid=b2.get("over_sid") or b2.get("sid"),
                            ),
                        ]
                        dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nhl")
                        pair_player = f"{s1['player']} + {s2['player']} ({team_code})"
                        pair_norm = f"{s1['player_norm']}+{s2['player_norm']}"
                        raw_candidates.append(
                            SgpCandidate(
                                archetype=arch.key,
                                archetype_label=arch.label,
                                sport="nhl",
                                player=pair_player,
                                player_norm=pair_norm,
                                game_key=game_key,
                                commence=commence,
                                away=away,
                                home=home,
                                book=b_name,
                                legs=legs,
                                naive_product_american=int(implied_to_american(p_naive)),
                                est_sgp_dec=pred.decimal,
                                est_sgp_american=pred.american,
                                fair_joint_prob=round(p_true, 4),
                                fair_joint_american=int(implied_to_american(p_true)),
                                rho_true=arch.rho_true,
                                rho_book=pred.rho_used,
                                hold_book=pred.hold_used,
                                ev_pct=round(ev_pct, 2),
                                team=team_code,
                                deeplink=dlink,
                            )
                        )

        # ----------------------------------------------------------------------
        # Teammate 2: NFL QB Pass Yds + WR Rec Yds Stack
        # ----------------------------------------------------------------------
        if sp == "nfl":
            arch = SGP_ARCHETYPES["nfl_qb_wr_stack"]
            qb_row = None
            receivers = []
            for pn, pl_rows in team_players.items():
                p_pass = next((r for r in pl_rows if str(r.get("stat")).lower() == "pass_yds" and not r.get("is_alt")), None)
                if p_pass and qb_row is None:
                    f_pass = _fair_prob_of_row(p_pass)
                    if f_pass is not None and 0.04 <= f_pass <= 0.88 and (_n_books_backing(p_pass) >= 2 or p_pass.get("curve_fair_over") is not None):
                        qb_row = {
                            "player": p_pass.get("player", pn),
                            "player_norm": pn,
                            "fair_prob": f_pass,
                            "line": float(p_pass.get("line", 0.0)),
                            "row": p_pass,
                        }
                p_rec = next((r for r in pl_rows if str(r.get("stat")).lower() == "rec_yds" and not r.get("is_alt") and float(r.get("line", 0.0)) >= 40.5), None)
                if p_rec:
                    f_rec = _fair_prob_of_row(p_rec)
                    if f_rec is not None and 0.04 <= f_rec <= 0.88 and (_n_books_backing(p_rec) >= 2 or p_rec.get("curve_fair_over") is not None):
                        receivers.append({
                            "player": p_rec.get("player", pn),
                            "player_norm": pn,
                            "fair_prob": f_rec,
                            "line": float(p_rec.get("line", 0.0)),
                            "row": p_rec,
                        })
            receivers.sort(key=lambda w: w["line"], reverse=True)
            top_wrs = [w for w in receivers if not qb_row or w["player_norm"] != qb_row["player_norm"]][:2]

            if qb_row and top_wrs:
                for wr in top_wrs:
                    b1_map = {str(b.get("book", "")).lower(): b for b in qb_row["row"].get("books", []) if b.get("over") is not None}
                    b2_map = {str(b.get("book", "")).lower(): b for b in wr["row"].get("books", []) if b.get("over") is not None}
                    common = set(b1_map.keys()).intersection(b2_map.keys()).intersection(allowed_books)

                    for b_name in common:
                        b1 = b1_map[b_name]
                        b2 = b2_map[b_name]
                        am1, am2 = int(b1["over"]), int(b2["over"])
                        q1, q2 = american_to_implied_prob(am1), american_to_implied_prob(am2)
                        p_naive = q1 * q2

                        p_true = copula_joint([qb_row["fair_prob"], wr["fair_prob"]], arch.rho_true)
                        pred = predict_book_sgp_odds(b_name, arch.key, [am1, am2])
                        ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                        if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                            continue

                        legs = [
                            SgpLegCandidate(
                                stat="pass_yds",
                                line=qb_row["line"],
                                side="OVER",
                                book_american=am1,
                                fair_prob=round(qb_row["fair_prob"], 4),
                                label=f"{qb_row['player']} o{qb_row['line']} Pass Yds",
                                sid=b1.get("over_sid") or b1.get("sid"),
                            ),
                            SgpLegCandidate(
                                stat="rec_yds",
                                line=wr["line"],
                                side="OVER",
                                book_american=am2,
                                fair_prob=round(wr["fair_prob"], 4),
                                label=f"{wr['player']} o{wr['line']} Rec Yds",
                                sid=b2.get("over_sid") or b2.get("sid"),
                            ),
                        ]
                        dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nfl")
                        pair_player = f"{qb_row['player']} + {wr['player']} ({team_code})"
                        pair_norm = f"{qb_row['player_norm']}+{wr['player_norm']}"
                        raw_candidates.append(
                            SgpCandidate(
                                archetype=arch.key,
                                archetype_label=arch.label,
                                sport="nfl",
                                player=pair_player,
                                player_norm=pair_norm,
                                game_key=game_key,
                                commence=commence,
                                away=away,
                                home=home,
                                book=b_name,
                                legs=legs,
                                naive_product_american=int(implied_to_american(p_naive)),
                                est_sgp_dec=pred.decimal,
                                est_sgp_american=pred.american,
                                fair_joint_prob=round(p_true, 4),
                                fair_joint_american=int(implied_to_american(p_true)),
                                rho_true=arch.rho_true,
                                rho_book=pred.rho_used,
                                hold_book=pred.hold_used,
                                ev_pct=round(ev_pct, 2),
                                team=team_code,
                                deeplink=dlink,
                            )
                        )

        # ----------------------------------------------------------------------
        # Teammate 3: NBA Playmaker Ast + Scorer Pts Stack
        # ----------------------------------------------------------------------
        if sp == "nba":
            arch = SGP_ARCHETYPES["nba_teammate_ast_pts_stack"]
            playmakers = []
            scorers = []
            for pn, pl_rows in team_players.items():
                p_ast = next((r for r in pl_rows if str(r.get("stat")).lower() == "assists" and not r.get("is_alt") and float(r.get("line", 0.0)) >= 6.5), None)
                if p_ast:
                    f_ast = _fair_prob_of_row(p_ast)
                    if f_ast is not None and 0.04 <= f_ast <= 0.88 and (_n_books_backing(p_ast) >= 2 or p_ast.get("curve_fair_over") is not None):
                        playmakers.append({
                            "player": p_ast.get("player", pn),
                            "player_norm": pn,
                            "fair_prob": f_ast,
                            "line": float(p_ast.get("line", 0.0)),
                            "row": p_ast,
                        })
                p_pts = next((r for r in pl_rows if str(r.get("stat")).lower() == "points" and not r.get("is_alt") and float(r.get("line", 0.0)) >= 20.5), None)
                if p_pts:
                    f_pts = _fair_prob_of_row(p_pts)
                    if f_pts is not None and 0.04 <= f_pts <= 0.88 and (_n_books_backing(p_pts) >= 2 or p_pts.get("curve_fair_over") is not None):
                        scorers.append({
                            "player": p_pts.get("player", pn),
                            "player_norm": pn,
                            "fair_prob": f_pts,
                            "line": float(p_pts.get("line", 0.0)),
                            "row": p_pts,
                        })
            playmakers.sort(key=lambda p: p["line"], reverse=True)
            scorers.sort(key=lambda s: s["line"], reverse=True)

            if playmakers and scorers:
                pg = playmakers[0]
                scorer = next((s for s in scorers if s["player_norm"] != pg["player_norm"]), None)
                if scorer:
                    b1_map = {str(b.get("book", "")).lower(): b for b in pg["row"].get("books", []) if b.get("over") is not None}
                    b2_map = {str(b.get("book", "")).lower(): b for b in scorer["row"].get("books", []) if b.get("over") is not None}
                    common = set(b1_map.keys()).intersection(b2_map.keys()).intersection(allowed_books)

                    for b_name in common:
                        b1 = b1_map[b_name]
                        b2 = b2_map[b_name]
                        am1, am2 = int(b1["over"]), int(b2["over"])
                        q1, q2 = american_to_implied_prob(am1), american_to_implied_prob(am2)
                        p_naive = q1 * q2

                        p_true = copula_joint([pg["fair_prob"], scorer["fair_prob"]], arch.rho_true)
                        pred = predict_book_sgp_odds(b_name, arch.key, [am1, am2])
                        ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                        if ev_pct < min_ev_pct or ev_pct > MAX_CREDIBLE_EV_PCT:
                            continue

                        legs = [
                            SgpLegCandidate(
                                stat="assists",
                                line=pg["line"],
                                side="OVER",
                                book_american=am1,
                                fair_prob=round(pg["fair_prob"], 4),
                                label=f"{pg['player']} o{pg['line']} Ast",
                                sid=b1.get("over_sid") or b1.get("sid"),
                            ),
                            SgpLegCandidate(
                                stat="points",
                                line=scorer["line"],
                                side="OVER",
                                book_american=am2,
                                fair_prob=round(scorer["fair_prob"], 4),
                                label=f"{scorer['player']} o{scorer['line']} Pts",
                                sid=b2.get("over_sid") or b2.get("sid"),
                            ),
                        ]
                        dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs], sport="nba")
                        pair_player = f"{pg['player']} + {scorer['player']} ({team_code})"
                        pair_norm = f"{pg['player_norm']}+{scorer['player_norm']}"
                        raw_candidates.append(
                            SgpCandidate(
                                archetype=arch.key,
                                archetype_label=arch.label,
                                sport="nba",
                                player=pair_player,
                                player_norm=pair_norm,
                                game_key=game_key,
                                commence=commence,
                                away=away,
                                home=home,
                                book=b_name,
                                legs=legs,
                                naive_product_american=int(implied_to_american(p_naive)),
                                est_sgp_dec=pred.decimal,
                                est_sgp_american=pred.american,
                                fair_joint_prob=round(p_true, 4),
                                fair_joint_american=int(implied_to_american(p_true)),
                                rho_true=arch.rho_true,
                                rho_book=pred.rho_used,
                                hold_book=pred.hold_used,
                                ev_pct=round(ev_pct, 2),
                                team=team_code,
                                deeplink=dlink,
                            )
                        )

    # --------------------------------------------------------------------------
    # Deduplicate across sportsbooks for each (archetype, game_key, player_norm)
    # --------------------------------------------------------------------------
    dedup_groups: Dict[Tuple[str, str, str], List[SgpCandidate]] = {}
    for c in raw_candidates:
        k = (c.archetype, c.game_key, c.player_norm)
        dedup_groups.setdefault(k, []).append(c)

    final_candidates: List[SgpCandidate] = []
    for grp in dedup_groups.values():
        grp.sort(key=lambda x: x.ev_pct, reverse=True)
        top = grp[0]
        if len(grp) > 1:
            top.runner_up_books = [
                {
                    "book": other.book,
                    "ev_pct": other.ev_pct,
                    "est_sgp_american": other.est_sgp_american,
                }
                for other in grp[1:]
            ]
        final_candidates.append(top)

    final_candidates.sort(key=lambda c: c.ev_pct, reverse=True)
    return final_candidates


def save_sgp_candidates(
    candidates: Sequence[SgpCandidate],
    path: str = "data/sgp_candidates_latest.json",
) -> None:
    """Write pre-filtered SGP candidates to JSON."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "count": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
