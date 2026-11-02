---
module: mlb_core.data.cockpit_form
tags: [nba, daily_projections, opponent-factor, replay-vs-live-drift]
problem_type: logic-error
---

# NBA live loader never set the opponent (found 2026-11-02)

`cockpit_form._load_nba` read `opp_abbr` from the player box master, which has no such column, so `_opp` was always None on the live board: the opponent
factor had zero entries and `TeamGames.expected_makes` returned None (the team-makes assist factor was inert). The master also spells five teams
GS / NO / NY / SA / PHO, while the engine keys on tricodes (GSW / NOP / NYK / SAS / PHX). Every replay (`nba/props/engine_eval.load_master`) derived both, so
replay evidence did not describe what served.

Fix: fold `team_abbr` to the tricode (`nba.props.data.sb_team`) and derive `_opp` from the two teams sharing a `game_id`. Gain measured -0.0011 log loss (z -6.9).
Lesson: when a replay and the live loader build the same frame by different code, test the live loader on the real master (see
`test_live_loader_derives_opponent_and_folds_team_codes`). `nba/props/engine_eval.py` still has the raw-code miss for those five teams.
