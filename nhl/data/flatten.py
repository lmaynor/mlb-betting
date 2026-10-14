"""Flatten an NHL gamecenter boxscore (+ optional play-by-play) into tabular grains.

flatten_game(box, pbp=None) -> {"game": dict, "teams": [dict], "skaters": [dict],
                                "goalies": [dict], "shots": [dict]}

    game     -- one row per game
    teams    -- one row per (game, team)            (box stats summed from player rows + team SOG)
    skaters  -- one row per (game, skater)          forwards + defense
    goalies  -- one row per (game, goalie)
    shots    -- one row per shot-attempt event      sog / miss / block / goal  (needs pbp)

Gotchas baked in here (see nhl/README.md):
  * Box-score names are ABBREVIATED ("A. Johansson" -- DET dressed two of them). Full names
    come from play-by-play rosterSpots; the join key everywhere is the integer player_id.
  * The official final score of a shootout game credits the winner with +1 goal. Goals
    EXCLUDING the shootout are recomputed from goal events (`*_goals_ex_so`), because
    exchanges (Kalshi counts a shootout goal, most books do not) disagree on which total is
    "the score".
  * `blocked-shot` events are owned by the SHOOTING team; the blocker is blocking_player_id.
  * situationCode is 4 digits: away goalie in net, away skaters, home skaters, home goalie in net
    (a 0 goalie digit = net empty).
Missing stat blocks are tolerated: the game row is always emitted.
"""
import math

from nhl.data.settings import GOAL_X, SHOT_EVENTS


# -- small parsers --------------------------------------------------------------
def toi_seconds(toi):
    """'12:14' -> 734; '61:05' -> 3665; None/''/malformed -> None."""
    if not toi or ":" not in str(toi):
        return None
    try:
        m, s = str(toi).split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def _split_made_att(v):
    """'31/35' -> (31, 35); anything else -> (None, None)."""
    try:
        a, b = str(v).split("/")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return None, None


def _default(d, key=None):
    """NHL localized-name blocks look like {'default': 'Detroit', 'fr': ...}."""
    if key is not None:
        d = (d or {}).get(key)
    if isinstance(d, dict):
        return d.get("default")
    return d


def season_year(season_id):
    """20262027 -> 2026 (start year)."""
    return int(season_id) // 10000 if season_id else None


def _team_name(team: dict):
    place, common = _default(team, "placeName"), _default(team, "commonName")
    return " ".join(x for x in (place, common) if x) or None


def roster_names(pbp) -> dict:
    """{player_id: 'First Last'} from play-by-play rosterSpots (empty without pbp)."""
    out = {}
    for r in (pbp or {}).get("rosterSpots", []) or []:
        pid = r.get("playerId")
        first, last = _default(r, "firstName"), _default(r, "lastName")
        if pid is not None and (first or last):
            out[pid] = " ".join(x for x in (first, last) if x)
    return out


# -- shot geometry ---------------------------------------------------------------
def shot_geometry(x, y, team_id, home_id, home_defending_side):
    """(dist_ft, angle_deg) from the shot to the goal the shooting team attacks, else (None, None).

    The home team defends `home_defending_side`, so the AWAY team attacks that goal and the HOME
    team attacks the other one. Angle is measured off the straight-out line from the goal centre
    (0 = dead centre, 90 = along the goal line); shots from behind the goal line are mirrored in
    front (approximation -- there are very few).
    """
    if x is None or y is None or home_defending_side not in ("left", "right"):
        return None, None
    home_attacks_right = home_defending_side == "left"
    attacks_right = home_attacks_right if team_id == home_id else not home_attacks_right
    goal_x = GOAL_X if attacks_right else -GOAL_X
    dx = abs(goal_x - x)
    return round(math.hypot(dx, y), 2), round(math.degrees(math.atan2(abs(y), dx)), 2)


def _situation(code, team_id, away_id):
    """Decode situationCode relative to the shooting team.

    Returns (skaters_for, skaters_against, empty_net_against). Empty net is the OPPOSING goalie
    digit being 0.
    """
    if not code or len(str(code)) != 4 or not str(code).isdigit():
        return None, None, None
    ag, a_sk, h_sk, hg = (int(c) for c in str(code))
    if team_id == away_id:
        return a_sk, h_sk, hg == 0
    return h_sk, a_sk, ag == 0


# -- shots ---------------------------------------------------------------------
def flatten_shots(pbp, game_id) -> list:
    if not pbp:
        return []
    away_id = (pbp.get("awayTeam") or {}).get("id")
    home_id = (pbp.get("homeTeam") or {}).get("id")
    rows = []
    for play in pbp.get("plays", []) or []:
        event = SHOT_EVENTS.get(play.get("typeDescKey"))
        if event is None:
            continue
        d = play.get("details") or {}
        pd_ = play.get("periodDescriptor") or {}
        team_id = d.get("eventOwnerTeamId")
        x, y = d.get("xCoord"), d.get("yCoord")
        dist, angle = shot_geometry(x, y, team_id, home_id, play.get("homeTeamDefendingSide"))
        sk_for, sk_against, empty_net = _situation(play.get("situationCode"), team_id, away_id)
        rows.append({
            "game_id": game_id,
            "event_id": play.get("eventId"),
            "period": pd_.get("number"),
            "period_type": pd_.get("periodType"),
            "time_sec": toi_seconds(play.get("timeInPeriod")),
            "event": event,
            "team_id": team_id,
            "is_home": team_id == home_id,
            "shooter_id": d.get("shootingPlayerId") if d.get("shootingPlayerId") is not None
            else d.get("scoringPlayerId"),
            "goalie_id": d.get("goalieInNetId"),
            "blocking_player_id": d.get("blockingPlayerId"),
            "assist1_id": d.get("assist1PlayerId"),
            "assist2_id": d.get("assist2PlayerId"),
            "x": x,
            "y": y,
            "zone": d.get("zoneCode"),
            "dist_ft": dist,
            "angle_deg": angle,
            "shot_type": d.get("shotType"),
            "reason": d.get("reason"),
            "situation_code": play.get("situationCode"),
            "skaters_for": sk_for,
            "skaters_against": sk_against,
            "empty_net": empty_net,
        })
    return rows


def _goals_by_period(pbp, away_id, home_id) -> dict:
    """Goals excluding the shootout, split p1/p2/p3/ot per side.

    Without pbp the same columns come back as None (stable CSV schema; NOT zero -- zero would
    claim a 0-0 game).
    """
    cols = [f"{s}_{k}" for s in ("away", "home") for k in ("p1", "p2", "p3", "ot", "goals_ex_so")]
    if not pbp:
        return {c: None for c in cols}
    out = {c: 0 for c in cols}
    for play in pbp.get("plays", []) or []:
        if play.get("typeDescKey") != "goal":
            continue
        pd_ = play.get("periodDescriptor") or {}
        ptype = pd_.get("periodType")
        if ptype == "SO":
            continue
        owner = (play.get("details") or {}).get("eventOwnerTeamId")
        side = "away" if owner == away_id else "home" if owner == home_id else None
        if side is None:
            continue
        slot = "ot" if ptype == "OT" else f"p{pd_.get('number')}"
        if slot in ("p1", "p2", "p3", "ot"):
            out[f"{side}_{slot}"] += 1
            out[f"{side}_goals_ex_so"] += 1
    return out


# -- boxscore -------------------------------------------------------------------
def _skater_row(p, base, names):
    toi = toi_seconds(p.get("toi"))
    pid = p.get("playerId")
    return {
        **base,
        "player_id": pid,
        "name": names.get(pid) or _default(p, "name"),
        "position": p.get("position"),
        "sweater": p.get("sweaterNumber"),
        "toi_sec": toi,
        "played": bool(toi),
        "goals": p.get("goals"),
        "assists": p.get("assists"),
        "points": p.get("points"),
        "plus_minus": p.get("plusMinus"),
        "pim": p.get("pim"),
        "hits": p.get("hits"),
        "power_play_goals": p.get("powerPlayGoals"),
        "sog": p.get("sog"),
        "blocked_shots": p.get("blockedShots"),
        "shifts": p.get("shifts"),
        "giveaways": p.get("giveaways"),
        "takeaways": p.get("takeaways"),
        "faceoff_pct": p.get("faceoffWinningPctg"),
    }


def _goalie_row(p, base, names):
    toi = toi_seconds(p.get("toi"))
    pid = p.get("playerId")
    es_sv, es_sa = _split_made_att(p.get("evenStrengthShotsAgainst"))
    pp_sv, pp_sa = _split_made_att(p.get("powerPlayShotsAgainst"))
    sh_sv, sh_sa = _split_made_att(p.get("shorthandedShotsAgainst"))
    return {
        **base,
        "player_id": pid,
        "name": names.get(pid) or _default(p, "name"),
        "sweater": p.get("sweaterNumber"),
        "toi_sec": toi,
        "played": bool(toi),
        "starter": p.get("starter"),
        "decision": p.get("decision"),           # W / L / O (OT loss) / None
        "shots_against": p.get("shotsAgainst"),
        "saves": p.get("saves"),
        "goals_against": p.get("goalsAgainst"),
        "save_pct": p.get("savePctg"),
        "es_saves": es_sv, "es_shots": es_sa,
        "pp_saves": pp_sv, "pp_shots": pp_sa,
        "sh_saves": sh_sv, "sh_shots": sh_sa,
        "pim": p.get("pim"),
    }


def _sum(rows, field):
    vals = [r.get(field) for r in rows if r.get(field) is not None]
    return sum(vals) if vals else None


def flatten_game(box: dict, pbp: dict = None) -> dict:
    gid = box["id"]
    away, home = box.get("awayTeam") or {}, box.get("homeTeam") or {}
    syear = season_year(box.get("season"))
    gtype = box.get("gameType")
    date = (box.get("gameDate") or "")[:10]
    outcome = box.get("gameOutcome") or {}
    last_pt = outcome.get("lastPeriodType")
    names = roster_names(pbp)

    game = {
        "game_id": gid,
        "season_year": syear,
        "game_type": gtype,
        "date": date,
        "start_utc": box.get("startTimeUTC"),
        "venue": _default(box, "venue"),
        "state": box.get("gameState"),
        "away_id": away.get("id"), "away_abbr": away.get("abbrev"), "away_name": _team_name(away),
        "home_id": home.get("id"), "home_abbr": home.get("abbrev"), "home_name": _team_name(home),
        "away_score": away.get("score"), "home_score": home.get("score"),
        "away_sog": away.get("sog"), "home_sog": home.get("sog"),
        "last_period_type": last_pt,
        "n_periods": (box.get("periodDescriptor") or {}).get("number"),
        "went_ot": last_pt in ("OT", "SO") if last_pt else None,
        "went_so": last_pt == "SO" if last_pt else None,
    }
    game.update(_goals_by_period(pbp, away.get("id"), home.get("id")))

    pbgs = box.get("playerByGameStats") or {}
    teams, skaters, goalies = [], [], []
    for side, team, opp in (("awayTeam", away, home), ("homeTeam", home, away)):
        block = pbgs.get(side) or {}
        base = {
            "game_id": gid, "season_year": syear, "game_type": gtype, "date": date,
            "team_id": team.get("id"), "team_abbr": team.get("abbrev"),
            "is_home": side == "homeTeam",
        }
        t_sk = [_skater_row(p, base, names) for grp in ("forwards", "defense") for p in block.get(grp, []) or []]
        t_g = [_goalie_row(p, base, names) for p in block.get("goalies", []) or []]
        skaters.extend(t_sk)
        goalies.extend(t_g)
        if side in pbgs or team.get("score") is not None:
            pim = [x for x in (_sum(t_sk, "pim"), _sum(t_g, "pim")) if x is not None]
            teams.append({
                "game_id": gid, "season_year": syear, "game_type": gtype, "date": date,
                "team_id": team.get("id"), "team_abbr": team.get("abbrev"),
                "is_home": side == "homeTeam",
                "opp_id": opp.get("id"), "opp_abbr": opp.get("abbrev"),
                "goals": team.get("score"), "goals_against": opp.get("score"),
                "sog": team.get("sog"),
                "shots_against": _sum(t_g, "shots_against"),
                "saves": _sum(t_g, "saves"),
                "hits": _sum(t_sk, "hits"),
                "blocked_shots": _sum(t_sk, "blocked_shots"),
                "giveaways": _sum(t_sk, "giveaways"),
                "takeaways": _sum(t_sk, "takeaways"),
                "power_play_goals": _sum(t_sk, "power_play_goals"),
                "pim": sum(pim) if pim else None,
            })

    return {"game": game, "teams": teams, "skaters": skaters, "goalies": goalies,
            "shots": flatten_shots(pbp, gid)}
