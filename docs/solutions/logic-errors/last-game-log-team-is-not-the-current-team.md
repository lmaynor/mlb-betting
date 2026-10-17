---
title: "A player's team from his LAST game-log row is wrong after a trade, retirement or release (Kopitar in the NHL rotation, NBA movers dropped)"
module: ev/daily_projections.py, ev/rosters.py
tags: [rosters, projections, nhl, nba, nfl, ambiguous_team, offseason]
problem_type: logic_error
category: logic-errors
date: 2026-10-17
---

## Problem

`GameLogs.team_of(rows)` returns the team on a player's most recent game-log row. Used for (a) `rotation_rows` (any player whose last team plays
soon becomes a no-line projection) and (b) `build_line_rows` (a quoted player whose log team is neither team of the quoted game is dropped as
`ambiguous_team`):

- **Retired / released players leak into rotation rows.** The served NHL board listed Anze Kopitar (LAK; blocked shots, points, shots) because his last
  row, April 2026, is a Kings game and the Kings played that night.
- **Offseason movers are dropped from the lines.** The book quotes the player on his NEW team's game; the log still says the old team. On the
  2026-10-17 NBA slate 64 of 414 quoted player-stat lines (17 players: LeBron James and Jaylen Brown to PHI, Kawhi Leonard to TOR, Paul George to BOS,
  Damian Lillard to POR, Anthony Davis to WAS ...) were dropped as `ambiguous_team`.
  Rotation rows for them were projected against the OLD team's game.

## Fix

`ev/rosters.py` (NHL official API / ESPN / nflverse rosters, cached `Rosters/{sport}.json`) and `resolve_team`: a quoted player's team is the roster
team when it is one of the two clubs in the quoted game (the quote is the ground truth that he plays in it), else the game-log team as before;
rotation rows take only players the roster carries, projected against the roster team and its game. Measured: NBA 340 -> 404 priced rows (+64
recovered), `ambiguous_team` 64 -> 0; NFL 6 recovered; NHL 0 (no quoted NHL mover on the slate). Kopitar is absent from the NHL rotation rows.

## Gotchas found while building it

- **Absence from a roster is not proof of retirement.** The NHL `/roster/{team}/current` endpoint omits injured-reserve players (Damon Severson has 3
  GP this season and is not listed; Mathew Barzal is on no roster). Players off a COMPLETE roster are therefore dropped from rotation rows (wanted for
  IR players; they cannot play) but a quoted player on no roster keeps the old behaviour, never a drop.
- **`NBA/players_xwalk.csv` accumulates**: retired and released players keep their last row forever, so it cannot be read as "the active roster". Only
  the rows stamped by the LATEST daily run are current (`nba_rows_from_xwalk`); an older run, or fewer than 28 teams, falls back to a live ESPN pull.
- **A partial pull must not exclude anyone**: `Roster.partial` makes `lookup` answer `unknown`, not `off`, for a player it simply did not load.
- **nflverse rosters**: one row per player per week; take the newest-week row and require ITS status to be ACT/INA (a player cut in week 5 still has
  an older ACT row). Two active players can share a normalized name (two Marcus Harris): those go to `ambiguous`, resolved only by the game-log team.
- A NHL API 429 on one team during the pull is normal (TBL on 2026-10-17); the client sleeps and `fetch_nhl` retries failed teams once.
