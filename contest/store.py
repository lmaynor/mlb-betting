"""Contest database operations. Every state change is ONE transaction, and every money movement is a ledger row.

All SQL goes through `text()` with named params (pg8000 requirement, CONTEXT.md 15.2). Row locking (`FOR UPDATE`) is added only on
Postgres. Server time is passed in as `now`; nothing reads a client clock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy import text

from contest.grading import grade_leg, settle_bet
from contest.rules import ContestRules
from contest.slips import GameInfo, Leg, Quote, Slip, parse_ts, validate_round_robin, validate_slip


class ContestError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _lock(c, sql: str) -> str:
    return sql if c.dialect.name == "sqlite" else sql + " FOR UPDATE"


def _rules(c, contest_id: int) -> ContestRules:
    row = c.execute(text("SELECT rules_json, status FROM contest WHERE id = :i"), {"i": contest_id}).first()
    if row is None:
        raise ContestError("NO_CONTEST", f"contest {contest_id} does not exist")
    return ContestRules.from_json(row[0])


def _ledger(c, entry_id: int, now: datetime, kind: str, amount_cents: int, bet_id: int | None = None, note: str | None = None) -> int:
    """Move money: update the cached balance and append the ledger row in the same transaction. Returns the new balance."""
    c.execute(text("UPDATE contest_entry SET balance_cents = balance_cents + :a WHERE id = :e"), {"a": amount_cents, "e": entry_id})
    bal = c.execute(text("SELECT balance_cents FROM contest_entry WHERE id = :e"), {"e": entry_id}).scalar_one()
    c.execute(text("""INSERT INTO contest_ledger (entry_id, ts, kind, amount_cents, bet_id, balance_after_cents, note)
                      VALUES (:e, :ts, :k, :a, :b, :bal, :n)"""),
              {"e": entry_id, "ts": iso(now), "k": kind, "a": amount_cents, "b": bet_id, "bal": bal, "n": note})
    return int(bal)


# ------------------------------------------------------------------------------------------------------------ setup

def create_contest(engine, slug: str, name: str, rules: ContestRules, now: datetime,
                   starts_at: datetime | None = None, ends_at: datetime | None = None) -> int:
    with engine.begin() as c:
        return c.execute(text("""INSERT INTO contest (slug, name, sport, status, starts_at, ends_at, rules_json, created_at)
                                 VALUES (:s, :n, :sp, 'open', :st, :en, :r, :ca) RETURNING id"""),
                         {"s": slug, "n": name, "sp": rules.sport, "st": iso(starts_at) if starts_at else None,
                          "en": iso(ends_at) if ends_at else None, "r": rules.to_json(), "ca": iso(now)}).scalar_one()


def create_invite(engine, contest_id: int, code: str, max_uses: int, now: datetime) -> None:
    with engine.begin() as c:
        c.execute(text("INSERT INTO contest_invite (code, contest_id, max_uses, uses, created_at) VALUES (:c, :i, :m, 0, :n)"),
                  {"c": code, "i": contest_id, "m": max_uses, "n": iso(now)})


def upsert_game(engine, contest_id: int, espn_event_id: str, commence_time: datetime, away_team: str | None = None,
                home_team: str | None = None, bp_key: str | None = None, round_label: str | None = None) -> int:
    """Insert the game or refresh its schedule fields (never its status or score). Keyed by (contest, ESPN event id)."""
    with engine.begin() as c:
        row = c.execute(text("SELECT id FROM contest_game WHERE contest_id = :c AND espn_event_id = :e"),
                        {"c": contest_id, "e": str(espn_event_id)}).first()
        p = {"c": contest_id, "e": str(espn_event_id), "ct": iso(commence_time), "a": away_team, "h": home_team, "b": bp_key, "r": round_label}
        if row:
            c.execute(text("""UPDATE contest_game SET commence_time = :ct, away_team = COALESCE(:a, away_team),
                              home_team = COALESCE(:h, home_team), bp_key = COALESCE(:b, bp_key),
                              round_label = COALESCE(:r, round_label) WHERE id = :id"""), {**p, "id": row[0]})
            return int(row[0])
        sport = c.execute(text("SELECT sport FROM contest WHERE id = :c"), {"c": contest_id}).scalar_one()
        return c.execute(text("""INSERT INTO contest_game (contest_id, sport, espn_event_id, bp_key, away_team, home_team, commence_time, round_label)
                                 VALUES (:c, :sp, :e, :b, :a, :h, :ct, :r) RETURNING id"""), {**p, "sp": sport}).scalar_one()


def join_contest(engine, contest_id: int, user_id: str, display_name: str, invite_code: str, now: datetime) -> int:
    """Join with an invite code and receive the starting bankroll. Idempotent: a user already in gets their entry back and the
    invite is not consumed again."""
    with engine.begin() as c:
        rules = _rules(c, contest_id)
        row = c.execute(text("SELECT id FROM contest_entry WHERE contest_id = :c AND user_id = :u"), {"c": contest_id, "u": user_id}).first()
        if row:
            return int(row[0])
        used = c.execute(text("UPDATE contest_invite SET uses = uses + 1 WHERE code = :k AND contest_id = :c AND uses < max_uses"),
                         {"k": invite_code, "c": contest_id}).rowcount
        if used != 1:
            raise ContestError("INVITE_INVALID", "invite code is wrong or used up")
        eid = c.execute(text("""INSERT INTO contest_entry (contest_id, user_id, display_name, balance_cents, status, joined_at)
                                VALUES (:c, :u, :d, 0, 'active', :n) RETURNING id"""),
                        {"c": contest_id, "u": user_id, "d": display_name.strip()[:40] or "player", "n": iso(now)}).scalar_one()
        _ledger(c, eid, now, "START", rules.start_bankroll_cents, note="starting bankroll")
        return int(eid)


# ------------------------------------------------------------------------------------------------------------ placing

def _bet_dict(c, bet_id: int) -> dict:
    b = c.execute(text("""SELECT id, entry_id, kind, stake_cents, payout_cents, status, teaser_points, idempotency_key, phase,
                          group_id, placed_at, settled_at FROM contest_bet WHERE id = :i"""), {"i": bet_id}).mappings().one()
    legs = c.execute(text("""SELECT game_id, market, side, line, price, result FROM contest_leg WHERE bet_id = :i ORDER BY id"""),
                     {"i": bet_id}).mappings().all()
    return {**dict(b), "legs": [dict(x) for x in legs]}


def _open_entry(c, contest_id: int, user_id: str):
    """Common preamble of every placement: contest open, entry locked (Postgres) and found. -> (rules, entry_id, balance, status)."""
    rules = _rules(c, contest_id)
    if c.execute(text("SELECT status FROM contest WHERE id = :i"), {"i": contest_id}).scalar_one() != "open":
        raise ContestError("CONTEST_CLOSED", "this contest is not open for bets")
    entry = c.execute(text(_lock(c, "SELECT id, balance_cents, status FROM contest_entry WHERE contest_id = :c AND user_id = :u")),
                      {"c": contest_id, "u": user_id}).first()
    if entry is None:
        raise ContestError("NOT_ENTERED", "join the contest first")
    return rules, int(entry[0]), int(entry[1]), entry[2]


def _games_for(c, contest_id: int, legs) -> dict[int, GameInfo]:
    games: dict[int, GameInfo] = {}
    for leg in legs:
        g = c.execute(text("SELECT commence_time, status FROM contest_game WHERE id = :g AND contest_id = :c"),
                      {"g": leg.game_id, "c": contest_id}).first()
        if g:
            games[leg.game_id] = GameInfo(parse_ts(g[0]), g[1])
    return games


def _open_risk(c, entry_id: int) -> int:
    return int(c.execute(text("SELECT COALESCE(SUM(stake_cents), 0) FROM contest_bet WHERE entry_id = :e AND status = 'pending'"),
                         {"e": entry_id}).scalar_one())


def _insert_bet(c, entry_id: int, slip: Slip, now: datetime, phase: str, board, group_id: str | None = None) -> int:
    """Write one bet, its legs (with the exact line / price / quote time the bettor got) and the stake debit."""
    bet_id = c.execute(text("""INSERT INTO contest_bet (entry_id, kind, stake_cents, status, teaser_points, idempotency_key, phase, group_id, placed_at)
                               VALUES (:e, :k, :s, 'pending', :tp, :ik, :ph, :gr, :n) RETURNING id"""),
                       {"e": entry_id, "k": slip.kind, "s": slip.stake_cents, "tp": slip.legs[0].teaser_points if slip.kind == "teaser" else 0.0,
                        "ik": slip.idempotency_key, "ph": phase, "gr": group_id, "n": iso(now)}).scalar_one()
    for leg in slip.legs:
        q = board.get((leg.game_id, leg.market, leg.side)) if board else None
        c.execute(text("""INSERT INTO contest_leg (bet_id, game_id, market, side, line, price, quote_fetched_at)
                          VALUES (:b, :g, :m, :s, :l, :p, :q)"""),
                  {"b": bet_id, "g": leg.game_id, "m": leg.market, "s": leg.side, "l": leg.line, "p": leg.price,
                   "q": iso(q.fetched_at) if q else None})
    _ledger(c, entry_id, now, "STAKE", -slip.stake_cents, int(bet_id))
    return int(bet_id)


def place_bet(engine, contest_id: int, user_id: str, slip: Slip, now: datetime,
              board: Mapping[tuple, Quote] | None = None, phase: str = "pregame") -> dict:
    """Validate and place a slip atomically: lock the entry, re-check everything against live DB state, debit the stake, store the
    bet and legs (with the exact line / price / quote time the bettor got). A retry with the same idempotency key returns the
    original bet and moves no money. Raises SlipError / ContestError; on any error nothing is written."""
    with engine.begin() as c:
        rules, entry_id, balance, status = _open_entry(c, contest_id, user_id)
        dup = c.execute(text("SELECT id FROM contest_bet WHERE entry_id = :e AND idempotency_key = :k"),
                        {"e": entry_id, "k": slip.idempotency_key}).first()
        if dup:
            return _bet_dict(c, int(dup[0]))
        if status != "active":
            raise ContestError("ELIMINATED", "this entry is out of the contest")
        validate_slip(slip, rules, balance, _open_risk(c, entry_id), now, _games_for(c, contest_id, slip.legs), board)
        return _bet_dict(c, _insert_bet(c, entry_id, slip, now, phase, board))


def place_round_robin(engine, contest_id: int, user_id: str, picks: Sequence[Leg], sizes: Sequence[int], unit_cents: int,
                      key: str, now: datetime, board: Mapping[tuple, Quote] | None = None, phase: str = "pregame") -> list[dict]:
    """Place a round robin: every k-leg parlay of `picks` for each k in `sizes`, `unit_cents` each, ALL or NOTHING in one
    transaction, tied together by group id `key`. A retry with the same key returns the original parlays and moves no money."""
    with engine.begin() as c:
        rules, entry_id, balance, status = _open_entry(c, contest_id, user_id)
        dup = c.execute(text("SELECT id FROM contest_bet WHERE entry_id = :e AND group_id = :g ORDER BY id"), {"e": entry_id, "g": key}).all()
        if dup:
            return [_bet_dict(c, int(r[0])) for r in dup]
        if status != "active":
            raise ContestError("ELIMINATED", "this entry is out of the contest")
        slips = validate_round_robin(picks, sizes, unit_cents, rules, balance, _open_risk(c, entry_id), now,
                                     _games_for(c, contest_id, picks), board)
        return [_bet_dict(c, _insert_bet(c, entry_id, Slip("parlay", sl.legs, sl.stake_cents, f"{key}#{n}"), now, phase, board, key))
                for n, sl in enumerate(slips)]


# ------------------------------------------------------------------------------------------------------------ settling

def _grade_open_legs(c, game_id: int, home: int | None, away: int | None, now: datetime) -> None:
    rows = c.execute(text("""SELECT l.id, l.market, l.side, l.line, l.price, b.teaser_points
                             FROM contest_leg l JOIN contest_bet b ON b.id = l.bet_id
                             WHERE l.game_id = :g AND l.result IS NULL"""), {"g": game_id}).all()
    for lid, market, side, line, price, tp in rows:
        result = "void" if home is None else grade_leg(Leg(game_id, market, side, line, int(price), float(tp or 0)), home, away)
        c.execute(text("UPDATE contest_leg SET result = :r, graded_at = :t WHERE id = :i"), {"r": result, "t": iso(now), "i": lid})


def _settle_ready(c, contest_id: int, now: datetime) -> list[int]:
    """Settle every pending bet in the contest that its legs now decide; credit the ledger; eliminate busted entries."""
    rules = _rules(c, contest_id)
    bets = c.execute(text("""SELECT b.id, b.entry_id, b.kind, b.stake_cents, b.teaser_points FROM contest_bet b
                             JOIN contest_entry e ON e.id = b.entry_id
                             WHERE e.contest_id = :c AND b.status = 'pending' ORDER BY b.id"""), {"c": contest_id}).all()
    settled, touched = [], set()
    for bet_id, entry_id, kind, stake, tp in bets:
        legs = c.execute(text("SELECT game_id, market, side, line, price, result FROM contest_leg WHERE bet_id = :b ORDER BY id"),
                         {"b": bet_id}).all()
        graded = [(Leg(g, m, s, ln, int(p), float(tp or 0)), r) for g, m, s, ln, p, r in legs]
        out = settle_bet(kind, int(stake), graded, rules)
        if out is None:
            continue
        c.execute(text("UPDATE contest_bet SET status = :s, payout_cents = :p, settled_at = :t WHERE id = :i"),
                  {"s": out.status, "p": out.payout_cents, "t": iso(now), "i": bet_id})
        if out.payout_cents > 0:
            _ledger(c, entry_id, now, "PAYOUT" if out.status == "won" else "REFUND", out.payout_cents, bet_id)
        settled.append(int(bet_id))
        touched.add(entry_id)
    for entry_id in touched:
        _eliminate_if_bust(c, entry_id, rules, now)
    return settled


def _eliminate_if_bust(c, entry_id: int, rules: ContestRules, now: datetime) -> bool:
    """Out when the balance cannot cover the minimum stake AND nothing is still pending."""
    bal, status = c.execute(text("SELECT balance_cents, status FROM contest_entry WHERE id = :e"), {"e": entry_id}).one()
    if status != "active" or bal >= rules.min_stake_cents:
        return False
    if c.execute(text("SELECT COUNT(*) FROM contest_bet WHERE entry_id = :e AND status = 'pending'"), {"e": entry_id}).scalar_one():
        return False
    c.execute(text("UPDATE contest_entry SET status = 'eliminated', eliminated_at = :t WHERE id = :e"), {"t": iso(now), "e": entry_id})
    return True


def settle_game(engine, game_id: int, home_score: int, away_score: int, now: datetime) -> list[int]:
    """Record a final score, grade its open legs and settle whatever that decides. Idempotent for the same score; a DIFFERENT score
    for an already-final game is refused (a correction needs a human: see `adjust`). Returns the settled bet ids."""
    with engine.begin() as c:
        g = c.execute(text("SELECT contest_id, status, home_score, away_score FROM contest_game WHERE id = :g"), {"g": game_id}).first()
        if g is None:
            raise ContestError("NO_GAME", f"game {game_id} not found")
        contest_id, status, hs, as_ = g
        if status == "void":
            raise ContestError("GAME_VOID", "game was voided")
        if status == "final" and (hs, as_) != (home_score, away_score):
            raise ContestError("SCORE_MISMATCH", f"game {game_id} is already final at {hs}-{as_}")
        c.execute(text("UPDATE contest_game SET status = 'final', home_score = :h, away_score = :a WHERE id = :g"),
                  {"h": home_score, "a": away_score, "g": game_id})
        _grade_open_legs(c, game_id, home_score, away_score, now)
        return _settle_ready(c, contest_id, now)


def void_game(engine, game_id: int, now: datetime) -> list[int]:
    """Postponed / cancelled: every open leg on the game is void (refund / drop out of its parlay or teaser)."""
    with engine.begin() as c:
        g = c.execute(text("SELECT contest_id, status FROM contest_game WHERE id = :g"), {"g": game_id}).first()
        if g is None:
            raise ContestError("NO_GAME", f"game {game_id} not found")
        if g[1] == "final":
            raise ContestError("GAME_FINAL", "a final game cannot be voided")
        c.execute(text("UPDATE contest_game SET status = 'void' WHERE id = :g"), {"g": game_id})
        _grade_open_legs(c, game_id, None, None, now)
        return _settle_ready(c, g[0], now)


def adjust(engine, entry_id: int, amount_cents: int, note: str, now: datetime) -> int:
    """Admin correction: a signed ledger row. Never edits history. Returns the new balance."""
    if not note.strip():
        raise ContestError("NOTE_REQUIRED", "an adjustment needs a reason")
    with engine.begin() as c:
        return _ledger(c, entry_id, now, "ADJUST", amount_cents, note=note.strip())


# ------------------------------------------------------------------------------------------------------------ reading

def reconcile(engine, contest_id: int) -> list[dict]:
    """Entries whose cached balance differs from the ledger sum, or that went negative. Empty list = books balance."""
    with engine.connect() as c:
        rows = c.execute(text("""SELECT e.id, e.user_id, e.balance_cents, COALESCE(SUM(l.amount_cents), 0)
                                 FROM contest_entry e LEFT JOIN contest_ledger l ON l.entry_id = e.id
                                 WHERE e.contest_id = :c GROUP BY e.id, e.user_id, e.balance_cents"""), {"c": contest_id}).all()
    return [{"entry_id": i, "user_id": u, "balance_cents": int(b), "ledger_sum_cents": int(s)} for i, u, b, s in rows if int(b) != int(s) or int(b) < 0]


def leaderboard(engine, contest_id: int) -> list[dict]:
    """Ranked by bankroll = balance + stakes still at risk. Shows no picks, only totals and the settled record."""
    with engine.connect() as c:
        rows = c.execute(text("""SELECT e.id, e.display_name, e.balance_cents, e.status,
                  COALESCE(SUM(CASE WHEN b.status = 'pending' THEN b.stake_cents ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN b.status = 'won' THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN b.status = 'lost' THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN b.status IN ('push', 'void') THEN 1 ELSE 0 END), 0)
                  FROM contest_entry e LEFT JOIN contest_bet b ON b.entry_id = e.id
                  WHERE e.contest_id = :c GROUP BY e.id, e.display_name, e.balance_cents, e.status"""), {"c": contest_id}).all()
    out = [{"entry_id": i, "display_name": n, "balance_cents": int(bal), "open_risk_cents": int(risk), "bankroll_cents": int(bal) + int(risk),
            "status": st, "won": int(w), "lost": int(lo), "pushed": int(p)} for i, n, bal, st, risk, w, lo, p in rows]
    out.sort(key=lambda r: (-r["bankroll_cents"], r["display_name"].lower()))
    for rank, r in enumerate(out, 1):
        r["rank"] = rank
    return out


def visible_bets(engine, contest_id: int, viewer_user_id: str, now: datetime) -> list[dict]:
    """Every bet in the contest as `viewer_user_id` may see it. The owner sees everything. Anyone else sees a leg only once its game
    has kicked off (a hidden leg is `{"hidden": True}`), and the stake / payout only once every leg is visible. This is the
    server-side half of 'picks are hidden until tip-off': the API must return THIS, never raw rows."""
    with engine.connect() as c:
        bets = c.execute(text("""SELECT b.id, e.user_id, e.display_name, b.kind, b.stake_cents, b.payout_cents, b.status, b.placed_at, b.group_id
                                 FROM contest_bet b JOIN contest_entry e ON e.id = b.entry_id
                                 WHERE e.contest_id = :c ORDER BY b.id"""), {"c": contest_id}).all()
        out = []
        for bet_id, user_id, name, kind, stake, payout, status, placed, group_id in bets:
            mine = user_id == viewer_user_id
            legs = c.execute(text("""SELECT l.game_id, l.market, l.side, l.line, l.price, l.result, g.commence_time
                                     FROM contest_leg l JOIN contest_game g ON g.id = l.game_id WHERE l.bet_id = :b ORDER BY l.id"""),
                             {"b": bet_id}).all()
            shown, all_visible = [], True
            for gid, market, side, line, price, result, commence in legs:
                if mine or parse_ts(commence) <= now:
                    shown.append({"game_id": gid, "market": market, "side": side, "line": line, "price": price, "result": result})
                else:
                    shown.append({"hidden": True})
                    all_visible = False
            reveal_money = mine or all_visible
            out.append({"bet_id": bet_id, "owner": name, "mine": mine, "kind": kind, "status": status, "placed_at": placed, "group_id": group_id,
                        "stake_cents": stake if reveal_money else None, "payout_cents": payout if reveal_money else None, "legs": shown})
    return out
