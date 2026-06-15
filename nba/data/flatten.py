"""Flatten a SportsBlaze /boxscores response into three tabular grains.

Input shape (per event):
    {
      "id", "season": {"year", "type"}, "date", "live", "status",
      "teams":      {"away": {"id","name","abbreviation"}, "home": {...}},
      "scores":     {"total": {"away","home"},
                     "periods": {"1": {"away","home"}, ...}},   # 5+ = OT
      "statistics": {"away": {"total": {<19 stats>}}, "home": {...}},
      "players":    {"away": [{"id","name","position","played","starter",
                               "statistics": {"total": {<19 stats>}}}, ...],
                     "home": [...]},
    }

Output: (games, team_box, player_box) -- each a list of flat dicts.
    games       -- one row per game
    team_box    -- one row per (game, team)
    player_box  -- one row per (game, player)

Missing statistics/players are tolerated: the game row is always emitted; team
and player rows are skipped only when their stat blocks are absent.
"""
from nba.config import STAT_FIELDS


def _periods(scores: dict, side: str) -> dict:
    """Return q1..q4 and ot (sum of periods >= 5) for one side."""
    periods = (scores or {}).get("periods", {}) or {}
    out = {f"q{i}": None for i in range(1, 5)}
    ot = 0
    have_ot = False
    for k, v in periods.items():
        try:
            num = int(k)
        except (TypeError, ValueError):
            continue
        val = (v or {}).get(side)
        if 1 <= num <= 4:
            out[f"q{num}"] = val
        elif num >= 5 and val is not None:
            ot += val
            have_ot = True
    out["ot"] = ot if have_ot else None
    return out


def _stats(block: dict) -> dict:
    """Pull the 19 stat fields out of a {'total': {...}} block."""
    total = (block or {}).get("total", {}) or {}
    return {f: total.get(f) for f in STAT_FIELDS}


def flatten_event(ev: dict):
    """Flatten one event -> (game_row, [team_rows], [player_rows])."""
    gid = ev["id"]
    season = ev.get("season", {}) or {}
    year = season.get("year")
    stype = season.get("type")
    date = (ev.get("date") or "")[:10]
    status = ev.get("status")
    live = ev.get("live")

    teams = ev.get("teams", {}) or {}
    away_t = teams.get("away", {}) or {}
    home_t = teams.get("home", {}) or {}
    scores = ev.get("scores", {}) or {}
    total = scores.get("total", {}) or {}

    ap = _periods(scores, "away")
    hp = _periods(scores, "home")

    game = {
        "game_id": gid,
        "season_year": year,
        "season_type": stype,
        "date": date,
        "status": status,
        "live": live,
        "away_id": away_t.get("id"),
        "away_abbr": away_t.get("abbreviation"),
        "away_name": away_t.get("name"),
        "home_id": home_t.get("id"),
        "home_abbr": home_t.get("abbreviation"),
        "home_name": home_t.get("name"),
        "away_points": total.get("away"),
        "home_points": total.get("home"),
    }
    for i in range(1, 5):
        game[f"away_q{i}"] = ap[f"q{i}"]
        game[f"home_q{i}"] = hp[f"q{i}"]
    game["away_ot"] = ap["ot"]
    game["home_ot"] = hp["ot"]

    stats = ev.get("statistics", {}) or {}
    team_rows = []
    for side, tinfo, opp in (("away", away_t, home_t), ("home", home_t, away_t)):
        if side not in stats:
            continue
        row = {
            "game_id": gid,
            "season_year": year,
            "season_type": stype,
            "date": date,
            "team_id": tinfo.get("id"),
            "team_abbr": tinfo.get("abbreviation"),
            "is_home": side == "home",
            "opp_id": opp.get("id"),
            "opp_abbr": opp.get("abbreviation"),
        }
        row.update(_stats(stats.get(side)))
        team_rows.append(row)

    players = ev.get("players", {}) or {}
    player_rows = []
    for side, tinfo in (("away", away_t), ("home", home_t)):
        for p in players.get(side, []) or []:
            row = {
                "game_id": gid,
                "season_year": year,
                "season_type": stype,
                "date": date,
                "team_id": tinfo.get("id"),
                "team_abbr": tinfo.get("abbreviation"),
                "is_home": side == "home",
                "player_id": p.get("id"),
                "name": p.get("name"),
                "position": p.get("position"),
                "starter": p.get("starter"),
                "played": p.get("played"),
            }
            row.update(_stats(p.get("statistics")))
            player_rows.append(row)

    return game, team_rows, player_rows


def flatten_boxscores(raw: dict):
    """Flatten a full /boxscores response -> (games, team_box, player_box)."""
    games, team_box, player_box = [], [], []
    for ev in (raw or {}).get("events", []) or []:
        g, t, p = flatten_event(ev)
        games.append(g)
        team_box.extend(t)
        player_box.extend(p)
    return games, team_box, player_box
