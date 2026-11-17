"""The contest's request layer: turns a signed-in player's request into engine calls and the engine's state into the JSON the page reads.

Everything here is pure orchestration over `contest.store` (no HTTP, no Flask, no Clerk): the Flask blueprint in `contest/api.py` hands
it a user id it has already authenticated, a body it has parsed, and the server clock. Every mutating call returns the player's fresh
feed, so the page replaces its state wholesale instead of merging partial responses.

Two rules this file exists to keep:
  * the client never names a price. A pick carries the line / price the player SAW; the engine checks it against the stored board and
    refuses with PRICE_CHANGED (carrying the current price) when it moved;
  * nobody sees another player's pick before its game kicks off: the pool comes from `store.visible_bets`, never from raw rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy.exc as sa_exc

from contest import halftime, store
from contest.cashout import CashoutRefused
from contest.names import MAX_RENAMES
from contest.rules import ContestRules
from contest.slips import Leg, Slip, SlipError, win_decimal
from contest.odds import payout_cents


class RequestError(Exception):
    """A request the contest refuses. `http` is the status the API answers with; `code` is machine-readable; `detail` carries data the page needs."""

    def __init__(self, http: int, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.http, self.code, self.message, self.detail = http, code, message, detail or {}

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


# engine error code -> HTTP status; anything unlisted is a 400
_HTTP = {"NOT_ENTERED": 403, "ELIMINATED": 403, "CONTEST_CLOSED": 409, "PRICE_CHANGED": 409, "QUOTE_STALE": 409, "VALUE_CHANGED": 409, "GAME_CLOSED": 409,
         "INVITE_INVALID": 403, "NAME_TAKEN": 409, "NO_BET": 404, "NOT_PENDING": 409, "NO_QUOTE": 409}


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (SlipError, CashoutRefused) as e:
        raise RequestError(_HTTP.get(e.code, 400), e.code, e.message, e.detail) from e
    except store.ContestError as e:
        raise RequestError(_HTTP.get(e.code, 400), e.code, getattr(e, "message", str(e))) from e
    except Exception as e:                       # name validation and the like
        code = getattr(e, "code", None)
        if code:
            raise RequestError(_HTTP.get(code, 400), code, getattr(e, "message", str(e))) from e
        raise


def contest_or_404(engine, slug: str) -> dict:
    c = store.contest_by_slug(engine, slug)
    if c is None:
        raise RequestError(404, "NO_CONTEST", f"no contest {slug!r}")
    return c


# ------------------------------------------------------------------------------------------------------------ JSON shapes

def rules_json(r: ContestRules) -> dict:
    return {
        "startBankrollCents": r.start_bankroll_cents, "minStakeCents": r.min_stake_cents, "maxStakeBp": r.max_stake_bp,
        "parlayMinLegs": r.parlay_min_legs, "parlayMaxLegs": r.parlay_max_legs, "teaserMinLegs": r.teaser_min_legs, "teaserMaxLegs": r.teaser_max_legs,
        "roundRobinMinPicks": r.round_robin_min_picks, "roundRobinMaxPicks": r.round_robin_max_picks, "roundRobinMaxParlays": r.round_robin_max_parlays,
        "roundRobinMinUnitCents": r.round_robin_min_unit_cents, "closeBufferMin": r.close_buffer_min,
        "blackjackEnabled": r.blackjack_enabled, "blackjackThresholdCents": r.blackjack_threshold_cents, "blackjackMinChipCents": r.blackjack_min_chip_cents,
        "cashoutEnabled": r.cashout_enabled, "cashoutMarginBp": r.cashout_margin_bp,
        "cashoutSigma": {"spread": r.cashout_sigma("spread"), "total": r.cashout_sigma("total")},
        "halftimeEnabled": r.halftime_enabled, "halftimeMaxStakeBp": r.halftime_max_stake_bp,
        "altLinesEnabled": r.alt_lines_enabled, "altMarginBp": r.alt_margin_bp, "altMaxPoints": r.alt_max_points,
        "keyWeights": {str(k): v for k, v in r.key_weights().items()},
        "sgpEnabled": r.sgp_enabled, "sgpMarginBp": r.sgp_margin_bp, "sgpRhoBp": round(r.sgp_rho("spread") * 10_000), "sgpRhoMlBp": round(r.sgp_rho("ml") * 10_000),
        "teaser": {f"{pts:.1f}": {str(n): a for n, a in row.items()} for pts, row in r.teaser_table().items()},
    }


def _game_status(g: dict, now: datetime) -> str:
    if g["status"] == "final":
        return "final"
    if g["status"] == "void":
        return "void"
    return "live" if store.parse_ts(g["commence_time"]) <= now else "scheduled"


def games_json(engine, contest_id: int, now: datetime) -> list[dict]:
    board = store.load_board(engine, contest_id)
    window = halftime.open_games(engine, contest_id)
    out = []
    for g in store.games_of(engine, contest_id):
        teams = json.loads(g["teams_json"]) if g["teams_json"] else {}
        team = lambda side: {"abbr": (teams.get(side) or {}).get("abbr") or (g[f"{side}_team"] or "")[:4].upper(), "name": g[f"{side}_team"] or "",
                             "espnId": (teams.get(side) or {}).get("espn_id"), "rank": (teams.get(side) or {}).get("rank")}
        q = lambda m, s: board.get((g["id"], m, s))
        sh, sa_ = q("spread", "home"), q("spread", "away")
        to, tu = q("total", "over"), q("total", "under")
        mh, ma = q("ml", "home"), q("ml", "away")
        row = {"id": g["id"], "round": g["round_label"] or "Games", "commence": store.parse_ts(g["commence_time"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "status": _game_status(g, now), "away": team("away"), "home": team("home"),
               "spread": {"line": sh.line, "away": sa_.price, "home": sh.price} if sh and sa_ else None,
               "total": {"line": to.line, "over": to.price, "under": tu.price} if to and tu else None,
               "ml": {"away": ma.price, "home": mh.price} if mh and ma else None}
        if g["id"] in window:
            row["halftime"] = {"open": True}
        st = json.loads(g["state_json"]) if g["state_json"] else None
        if st and row["status"] in ("live", "final"):
            row["state"] = {"homeScore": st.get("home_score", g["home_score"] or 0), "awayScore": st.get("away_score", g["away_score"] or 0),
                            "detail": st.get("detail") or ("Final" if row["status"] == "final" else "In play"),
                            **({"possession": st["possession"]} if st.get("possession") else {}),
                            **({"downDistance": st["down_distance"]} if st.get("down_distance") else {}),
                            **({"redZone": True} if st.get("red_zone") else {})}
        elif row["status"] == "final" and g["home_score"] is not None:
            row["state"] = {"homeScore": g["home_score"], "awayScore": g["away_score"], "detail": "Final"}
        out.append(row)
    return out


def _bet_json(b: dict, rules: ContestRules) -> dict:
    legs = [Leg(l["game_id"], l["market"], l["side"], l["line"], int(l["price"]), float(b["teaser_points"] or 0)) for l in b["legs"]]
    payout = payout_cents(int(b["stake_cents"]), win_decimal(b["kind"], legs, rules, b.get("ticket_price")))
    out = {"id": str(b["id"]), "kind": b["kind"], "groupId": b["group_id"], "stakeCents": int(b["stake_cents"]), "toWinCents": payout - int(b["stake_cents"]),
           "status": b["status"], "placedAt": b["placed_at"], "teaserPoints": float(b["teaser_points"] or 0),
           "legs": [{"gameId": l["game_id"], "market": l["market"], "side": l["side"], "line": l["line"], "price": int(l["price"]), "result": l["result"]} for l in b["legs"]]}
    if b["payout_cents"] is not None and b["status"] == "cashed_out":
        out["payoutCents"] = int(b["payout_cents"])
    if b.get("ticket_price") is not None:
        out["ticketPrice"] = int(b["ticket_price"])
    return out


def feed(engine, slug: str, user_id: str, now: datetime, suggested_name: str = "") -> dict:
    """Everything the page renders, for this player, at `now`. Works before the player has joined (they see the board and the standings)."""
    c = contest_or_404(engine, slug)
    cid, rules = c["id"], c["rules"]
    entry = store.entry_of(engine, cid, user_id)
    lb = store.leaderboard(engine, cid)
    mine = {r["user_id"] for r in lb if r["user_id"] == user_id}
    return {
        "name": c["name"], "sample": False, "rules": rules_json(rules), "nowIso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "me": {"joined": entry is not None, "displayName": entry["display_name"] if entry else "", "suggestedName": suggested_name,
               "renamesLeft": max(0, MAX_RENAMES - entry["name_changes"]) if entry else MAX_RENAMES},
        "balanceCents": entry["balance_cents"] if entry else 0,
        "history": store.bankroll_history(engine, cid, user_id),
        "games": games_json(engine, cid, now),
        "myBets": [_bet_json(b, rules) for b in store.bets_of(engine, cid, user_id)] if entry else [],
        "pool": [{"id": str(p["bet_id"]), "owner": p["owner"], "kind": p["kind"], "status": p["status"], "stakeCents": p["stake_cents"],
                  "legs": [None if l.get("hidden") else {"gameId": l["game_id"], "market": l["market"], "side": l["side"], "line": l["line"], "price": l["price"]} for l in p["legs"]]}
                 for p in store.visible_bets(engine, cid, user_id, now) if not p["mine"]],
        "leaderboard": [{"rank": r["rank"], "name": r["display_name"], "bankrollCents": r["bankroll_cents"], "openRiskCents": r["open_risk_cents"],
                         "won": r["won"], "lost": r["lost"], "pushed": r["pushed"], "status": r["status"], **({"tied": True} if r["tied"] else {}), **({"mine": True} if r["user_id"] in mine else {})} for r in lb],
    }


# ------------------------------------------------------------------------------------------------------------ actions

def _legs(body: dict) -> list[Leg]:
    try:
        return [Leg(int(p["gameId"]), str(p["market"]), str(p["side"]), None if p.get("line") is None else float(p["line"]), int(p["price"]),
                    float(body.get("teaserPoints") or 0) if body.get("kind") == "teaser" else 0.0) for p in body.get("picks") or []]
    except (KeyError, TypeError, ValueError) as e:
        raise RequestError(400, "BAD_REQUEST", f"bad pick: {e}") from e


def place(engine, slug: str, user_id: str, body: dict, now: datetime) -> dict:
    """Place a slip. body = {kind, picks: [{gameId, market, side, line, price}], stakeCents, key, teaserPoints?, robinSizes?, ticketPrice?}."""
    c = contest_or_404(engine, slug)
    key = str(body.get("key") or "")
    if not key:
        raise RequestError(400, "BAD_REQUEST", "a slip needs an idempotency key")
    kind, legs = str(body.get("kind") or ""), _legs(body)
    try:
        stake = int(body["stakeCents"])
    except (KeyError, TypeError, ValueError) as e:
        raise RequestError(400, "BAD_REQUEST", "stakeCents is required") from e
    board = store.load_board(engine, c["id"])
    # a straight on a game whose halftime window is open is a halftime bet: it stays hidden from other players until the window closes
    phase = "halftime" if c["rules"].halftime_enabled and legs and kind == "straight" and all(l.game_id in halftime.open_games(engine, c["id"]) for l in legs) else "pregame"
    if kind == "round_robin":
        _wrap(store.place_round_robin, engine, c["id"], user_id, legs, [int(k) for k in body.get("robinSizes") or []], stake, key, now, board)
    elif kind == "straight" and len(legs) > 1:        # several straights on one slip: all or nothing is NOT promised, each is its own bet under key#n
        for n, leg in enumerate(legs):
            _wrap(store.place_bet, engine, c["id"], user_id, Slip("straight", (leg,), stake, f"{key}#{n}"), now, board, phase)
    else:
        ticket = body.get("ticketPrice")
        _wrap(store.place_bet, engine, c["id"], user_id, Slip(kind, tuple(legs), stake, key, int(ticket) if ticket is not None else None), now, board, phase)
    return feed(engine, slug, user_id, now)


def join(engine, slug: str, user_id: str, body: dict, now: datetime) -> dict:
    c = contest_or_404(engine, slug)
    _wrap(store.join_contest, engine, c["id"], user_id, str(body.get("displayName") or ""), str(body.get("inviteCode") or "").strip(), now)
    return feed(engine, slug, user_id, now)


def rename(engine, slug: str, user_id: str, body: dict, now: datetime) -> dict:
    c = contest_or_404(engine, slug)
    _wrap(store.rename_entry, engine, c["id"], user_id, str(body.get("displayName") or ""), now)
    return feed(engine, slug, user_id, now)


def cash_out(engine, slug: str, user_id: str, body: dict, now: datetime) -> dict:
    """body = {betIds: [..], expectedCents}. The player names the value they were shown; a different current value refuses with VALUE_CHANGED."""
    c = contest_or_404(engine, slug)
    try:
        ids, expected = [int(i) for i in body.get("betIds") or []], int(body["expectedCents"])
    except (KeyError, TypeError, ValueError) as e:
        raise RequestError(400, "BAD_REQUEST", "betIds and expectedCents are required") from e
    if not ids:
        raise RequestError(400, "BAD_REQUEST", "no bets to cash out")
    _wrap(store.cash_out, engine, c["id"], user_id, ids, expected, now, store.load_board(engine, c["id"]))
    return feed(engine, slug, user_id, now)


# ------------------------------------------------------------------------------------------------------------ organiser

def _admin_state(engine, slug: str) -> dict:
    c = contest_or_404(engine, slug)
    cid = c["id"]
    return {"name": c["name"], "status": c["status"], "switches": {k: bool(getattr(c["rules"], k)) for k in store.RULE_SWITCHES}, "entries": store.entries_of(engine, cid), "invites": store.invites_of(engine, cid),
            "games": [{"id": g["id"], "away": g["away_team"], "home": g["home_team"], "commence": g["commence_time"], "status": g["status"],
                       "homeScore": g["home_score"], "awayScore": g["away_score"]} for g in store.games_of(engine, cid)],
            "ledgerMismatches": store.reconcile(engine, cid), "log": store.admin_log(engine, cid, 50)}


def admin_state(engine, slug: str) -> dict:
    return _admin_state(engine, slug)


def admin_action(engine, slug: str, actor: str, op: str, body: dict, now: datetime) -> dict:
    """An organiser action. `op`: rename | adjust | void-game | settle-game | invite | status. Each is logged with who did it."""
    c = contest_or_404(engine, slug)
    cid = c["id"]
    try:
        if op == "rename":
            name = _wrap(store.admin_rename, engine, int(body["entryId"]), str(body["displayName"]))
            detail = {"entry": int(body["entryId"]), "name": name}
        elif op == "adjust":
            bal = _wrap(store.adjust, engine, int(body["entryId"]), int(body["cents"]), str(body.get("note") or ""), now)
            detail = {"entry": int(body["entryId"]), "cents": int(body["cents"]), "note": str(body.get("note")), "balance": bal}
        elif op == "void-game":
            settled = _wrap(store.void_game, engine, int(body["gameId"]), now)
            detail = {"game": int(body["gameId"]), "bets_settled": len(settled)}
        elif op == "settle-game":
            settled = _wrap(store.settle_game, engine, int(body["gameId"]), int(body["homeScore"]), int(body["awayScore"]), now)
            detail = {"game": int(body["gameId"]), "home": int(body["homeScore"]), "away": int(body["awayScore"]), "bets_settled": len(settled)}
        elif op == "invite":
            code = str(body["code"]).strip()
            if not code:
                raise RequestError(400, "BAD_REQUEST", "an invite needs a code")
            store.create_invite(engine, cid, code, int(body.get("maxUses", 1)), now)
            detail = {"code": code, "max_uses": int(body.get("maxUses", 1))}
        elif op == "rules":
            sw = {k: v for k, v in body.items() if k != "note"}
            _wrap(store.set_rule_switches, engine, cid, sw)
            detail = {"switches": sw}
        elif op == "status":
            _wrap(store.set_status, engine, cid, str(body["status"]))
            detail = {"status": str(body["status"])}
        else:
            raise RequestError(404, "NO_ROUTE", f"no organiser action {op!r}")
    except (KeyError, TypeError, ValueError) as e:
        raise RequestError(400, "BAD_REQUEST", f"bad request: {e}") from e
    except sa_exc.IntegrityError as e:
        raise RequestError(409, "DUPLICATE", "that already exists") from e
    store.log_admin(engine, cid, actor, op, detail, now)
    return _admin_state(engine, slug)

