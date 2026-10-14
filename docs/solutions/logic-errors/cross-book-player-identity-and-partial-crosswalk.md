---
title: "Cross-book player identity: raw-name consensus never groups, and a partial roster crosswalk can hide a namesake"
module: mlb_core/data/player_names.py, nhl/data/players.py, nba/data/espn_players.py, ev/props.py
tags: [name-resolution, crosswalk, nhl, nba, consensus, fail-closed]
problem_type: logic_error
category: logic-errors
date: 2026-10-14
---

## Problem

A soft-line alert is "this book's price vs the consensus of the OTHER books at the same (player, stat, point)". Keyed on the raw
description string the consensus never forms (observed: `Alex Ovechkin` / `Alex Ovechkin (WSH)`, and a spelled-out first name in
another book), so `n_books` stayed 1 and every alert threshold failed quietly.

## Fix

Resolve every quote to the league's own player id (NHL roster API, NBA via ESPN rosters) using, in order: exact normalised name,
the book's team hint, the event's two teams (`within=`), then a compatible-first-name match (`alex`/`alexander`, small alias
list). **Ambiguous -> dropped, never guessed.** `PlayerResolver` returns `(id, how)`; unknown names are kept under the normalised
string so a call-up is not lost.

## The hazard to remember (not an observed mis-merge)

If a team's roster is missing from the crosswalk, a same-named player on that team is simply not a candidate, so the name resolves
"exactly" to the other one. On 2026-10-14 four NHL rosters (TBL, TOR, UTA, VAN) failed with HTTP 429 on one run. The current NHL
and NBA crosswalks contain no same-name pairs (MLB does: two Max Muncys), so nothing was mis-merged -- but the guard exists:
`players.covered_teams()` and the sweep turns the resolver OFF for any event whose teams are not both covered. The roster refresh
retries failed teams once and reports `failed_teams`; callers must treat a non-empty list as a PARTIAL crosswalk.
`scripts/build_sport_assets.py --players` refuses to write a partial headshot map for the same reason.
