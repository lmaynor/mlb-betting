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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ev.sgp_book_pricer import (
    SGP_ARCHETYPES,
    SgpArchetype,
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

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["legs"] = [asdict(leg) for leg in self.legs]
        return d


def _fair_prob_of_row(row: Dict[str, Any]) -> Optional[float]:
    """Compute leave-vig fair P(over) for a row across two-sided books, falling back to curve."""
    books = row.get("books", [])
    if isinstance(books, dict):
        books = list(books.values())

    two_sided = []
    for b in books:
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

    if len(two_sided) >= 2:
        return float(sum(two_sided) / len(two_sided))

    curve = row.get("curve_fair_over")
    if curve is not None and 0.0 < float(curve) < 1.0:
        return float(curve)

    if len(two_sided) == 1:
        return float(two_sided[0])

    return None


def _best_price_on_row(
    row: Dict[str, Any],
    side: str = "OVER",
    bettable: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Optional[int], Optional[float]]:
    """Return (best_book, best_american, best_decimal) for a given row and side."""
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

    Stage 1: Groups by (game_key, player_norm), resolves single-leg fair probabilities,
             and filters with p_true_joint / (q_1 * q_2) >= 0.92.
    Stage 2: Applies fitted book copula (rho_book, hold_book), predicts SGP price,
             evaluates EV% and compares with direct markets.
    """
    allowed_books = bettable if bettable is not None else STANDARD_BETTABLE_BOOKS

    # Group by player in game
    by_player: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        gk = str(r.get("game_key", ""))
        pn = str(r.get("player_norm", "") or r.get("player", "")).lower().strip()
        if gk and pn:
            by_player.setdefault((gk, pn), []).append(r)

    candidates: List[SgpCandidate] = []

    for (game_key, player_norm), p_rows in by_player.items():
        sample = p_rows[0]
        player = sample.get("player", player_norm)
        team = sample.get("team")
        commence = sample.get("commence", "")
        away = sample.get("away", "")
        home = sample.get("home", "")
        p_id = sample.get("player_id")

        # Stat index: (stat, line) -> row
        stat_map: Dict[Tuple[str, float], Dict[str, Any]] = {}
        for r in p_rows:
            stat_name = str(r.get("stat", "")).lower().strip()
            line = float(r.get("line", 0.0))
            stat_map[(stat_name, line)] = r

        # ----------------------------------------------------------------------
        # Archetype 1: NBA Triple-Double Proxy (10+ Reb + 10+ Ast)
        # ----------------------------------------------------------------------
        r_reb10 = stat_map.get(("rebounds", 9.5))
        r_ast10 = stat_map.get(("assists", 9.5))
        if r_reb10 and r_ast10:
            arch = SGP_ARCHETYPES["nba_reb_ast_10_10"]
            f_reb = _fair_prob_of_row(r_reb10)
            f_ast = _fair_prob_of_row(r_ast10)
            if f_reb is not None and f_ast is not None:
                p_true = copula_joint([f_reb, f_ast], arch.rho_true)
                f_am = int(implied_to_american(p_true))

                # Direct market lookup
                direct_row = next(
                    (r for r in p_rows if str(r.get("stat", "")).lower() in ("triple_double", "td")),
                    None,
                )
                dir_book, dir_am, dir_dec = _best_price_on_row(direct_row, "OVER", allowed_books) if direct_row else (None, None, None)

                # Check books quoting both
                books_reb = {str(b.get("book", "")).lower(): b for b in r_reb10.get("books", []) if b.get("over") is not None}
                books_ast = {str(b.get("book", "")).lower(): b for b in r_ast10.get("books", []) if b.get("over") is not None}

                common_books = set(books_reb.keys()).intersection(books_ast.keys()).intersection(allowed_books)
                for b_name in common_books:
                    b_reb = books_reb[b_name]
                    b_ast = books_ast[b_name]
                    am_reb = int(b_reb["over"])
                    am_ast = int(b_ast["over"])
                    q_reb = american_to_implied_prob(am_reb)
                    q_ast = american_to_implied_prob(am_ast)

                    # Stage 1 Naive Product filter
                    p_naive = q_reb * q_ast
                    if p_naive <= 0.0 or (p_true / p_naive) < 0.92:
                        continue

                    # Stage 2 Parameterized Copula
                    pred = predict_book_sgp_odds(b_name, arch.key, [am_reb, am_ast])
                    ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                    if ev_pct < min_ev_pct:
                        continue

                    vs_dir = None
                    if dir_dec and dir_dec > 0.0:
                        vs_dir = round(((pred.decimal / dir_dec) - 1.0) * 100.0, 1)

                    legs = [
                        SgpLegCandidate(
                            stat="rebounds",
                            line=9.5,
                            side="OVER",
                            book_american=am_reb,
                            fair_prob=round(f_reb, 4),
                            label="o9.5 Reb",
                            sid=b_reb.get("sid"),
                            market_id=b_reb.get("market_id"),
                            selection_id=b_reb.get("selection_id"),
                        ),
                        SgpLegCandidate(
                            stat="assists",
                            line=9.5,
                            side="OVER",
                            book_american=am_ast,
                            fair_prob=round(f_ast, 4),
                            label="o9.5 Ast",
                            sid=b_ast.get("sid"),
                            market_id=b_ast.get("market_id"),
                            selection_id=b_ast.get("selection_id"),
                        ),
                    ]
                    dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs])

                    candidates.append(
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
                            fair_joint_american=f_am,
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
        # Archetype 2: NBA Points + Rebounds vs PR
        # ----------------------------------------------------------------------
        main_pts = next((r for r in p_rows if str(r.get("stat", "")).lower() == "points" and not r.get("is_alt")), None)
        main_reb = next((r for r in p_rows if str(r.get("stat", "")).lower() == "rebounds" and not r.get("is_alt")), None)
        if main_pts and main_reb:
            pts_line = float(main_pts.get("line", 0.0))
            reb_line = float(main_reb.get("line", 0.0))
            expected_pr = pts_line + reb_line
            # Look for PR row close to expected_pr
            pr_row = next(
                (r for r in p_rows if str(r.get("stat", "")).lower() in ("pr", "points_rebounds") and abs(float(r.get("line", 0.0)) - expected_pr) <= 1.0),
                None,
            )
            f_pts = _fair_prob_of_row(main_pts)
            f_reb = _fair_prob_of_row(main_reb)
            if f_pts is not None and f_reb is not None:
                arch = SGP_ARCHETYPES["nba_pts_reb_vs_pr"]
                p_true = copula_joint([f_pts, f_reb], arch.rho_true)
                f_am = int(implied_to_american(p_true))

                dir_book, dir_am, dir_dec = _best_price_on_row(pr_row, "OVER", allowed_books) if pr_row else (None, None, None)

                books_pts = {str(b.get("book", "")).lower(): b for b in main_pts.get("books", []) if b.get("over") is not None}
                books_reb = {str(b.get("book", "")).lower(): b for b in main_reb.get("books", []) if b.get("over") is not None}
                common = set(books_pts.keys()).intersection(books_reb.keys()).intersection(allowed_books)

                for b_name in common:
                    b_p = books_pts[b_name]
                    b_r = books_reb[b_name]
                    am_p = int(b_p["over"])
                    am_r = int(b_r["over"])
                    q_p = american_to_implied_prob(am_p)
                    q_r = american_to_implied_prob(am_r)
                    p_naive = q_p * q_r
                    if p_naive <= 0.0 or (p_true / p_naive) < 0.92:
                        continue
                    pred = predict_book_sgp_odds(b_name, arch.key, [am_p, am_r])
                    ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                    if ev_pct < min_ev_pct:
                        continue

                    vs_dir = None
                    if dir_dec and dir_dec > 0.0:
                        vs_dir = round(((pred.decimal / dir_dec) - 1.0) * 100.0, 1)

                    legs = [
                        SgpLegCandidate(
                            stat="points",
                            line=pts_line,
                            side="OVER",
                            book_american=am_p,
                            fair_prob=round(f_pts, 4),
                            label=f"o{pts_line} Pts",
                            sid=b_p.get("sid"),
                        ),
                        SgpLegCandidate(
                            stat="rebounds",
                            line=reb_line,
                            side="OVER",
                            book_american=am_r,
                            fair_prob=round(f_reb, 4),
                            label=f"o{reb_line} Reb",
                            sid=b_r.get("sid"),
                        ),
                    ]
                    dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs])
                    candidates.append(
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
                            fair_joint_american=f_am,
                            rho_true=arch.rho_true,
                            rho_book=pred.rho_used,
                            hold_book=pred.hold_used,
                            ev_pct=round(ev_pct, 2),
                            team=team,
                            player_id=p_id,
                            direct_market_label=f"PR o{pr_row.get('line')}" if dir_am and pr_row else None,
                            direct_best_american=dir_am,
                            direct_best_book=dir_book,
                            vs_direct_pct=vs_dir,
                            deeplink=dlink,
                        )
                    )

        # ----------------------------------------------------------------------
        # Archetype 3: NHL 1+ Goal + 1+ Assist vs 2+ Points (Points o1.5)
        # ----------------------------------------------------------------------
        r_goal = stat_map.get(("goals", 0.5))
        r_ast = stat_map.get(("assists", 0.5))
        r_pts2 = stat_map.get(("points", 1.5))
        if r_goal and r_ast:
            arch = SGP_ARCHETYPES["nhl_goal_ast_vs_pts"]
            f_goal = _fair_prob_of_row(r_goal)
            f_ast = _fair_prob_of_row(r_ast)
            if f_goal is not None and f_ast is not None:
                p_true = copula_joint([f_goal, f_ast], arch.rho_true)
                f_am = int(implied_to_american(p_true))

                dir_book, dir_am, dir_dec = _best_price_on_row(r_pts2, "OVER", allowed_books) if r_pts2 else (None, None, None)

                books_g = {str(b.get("book", "")).lower(): b for b in r_goal.get("books", []) if b.get("over") is not None}
                books_a = {str(b.get("book", "")).lower(): b for b in r_ast.get("books", []) if b.get("over") is not None}
                common = set(books_g.keys()).intersection(books_a.keys()).intersection(allowed_books)

                for b_name in common:
                    b_g = books_g[b_name]
                    b_a = books_a[b_name]
                    am_g = int(b_g["over"])
                    am_a = int(b_a["over"])
                    q_g = american_to_implied_prob(am_g)
                    q_a = american_to_implied_prob(am_a)
                    p_naive = q_g * q_a
                    if p_naive <= 0.0 or (p_true / p_naive) < 0.92:
                        continue
                    pred = predict_book_sgp_odds(b_name, arch.key, [am_g, am_a])
                    ev_pct = (p_true * pred.decimal - 1.0) * 100.0
                    if ev_pct < min_ev_pct:
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
                            sid=b_g.get("sid"),
                        ),
                        SgpLegCandidate(
                            stat="assists",
                            line=0.5,
                            side="OVER",
                            book_american=am_a,
                            fair_prob=round(f_ast, 4),
                            label="1+ Assist",
                            sid=b_a.get("sid"),
                        ),
                    ]
                    dlink = build_sgp_deeplink(b_name, game_key, [leg.__dict__ for leg in legs])
                    candidates.append(
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
                            fair_joint_american=f_am,
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

    # Sort descending by EV%
    candidates.sort(key=lambda c: c.ev_pct, reverse=True)
    return candidates


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
