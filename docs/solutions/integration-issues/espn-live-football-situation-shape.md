---
title: ESPN live football situation: text on a normal down, nothing between plays, and no possession team id
module: contest.espn
tags: [espn, football, situation, down-distance, possession, contest]
problem_type: integration-issue
category: integration-issues
date: 2026-11-17
---

## Checked on a live game (EMU at WMU, 2026-11-17)

`competitions[0].situation` on a normal down:
`{down: 1, distance: 10, yardLine: 84, downDistanceText: "1st & 10 at EMU 16", shortDownDistanceText: "1st & 10", possessionText: "EMU 16", isRedZone: false, homeTimeouts: 3, awayTimeouts: 3, lastPlay: {...}}`.

Between plays (right after a touchdown, before the snap): `down: -1`, `distance: -1` (or 0), NO text fields, and `isRedZone: true` can still be set.

## Consequences in `contest/espn.py`

- the state line shows `downDistanceText` when present, nothing otherwise;
- `possessionText` is the FIELD position (a team abbreviation and a yard line), NOT who has the ball. The payload carried no possession team id, so
  the possession dot only appears when `situation.possession` exists. It is never guessed from the yard-line text;
- the red-zone flag is read only on a live down (`down > 0`), otherwise a touchdown would show "Red zone".

Basketball `situation.lastPlay.probability` carries ESPN's own home / away win probability (a model-free in-game reference), but nothing reads it.
Tests: `tests/test_contest_sync.py::test_the_live_football_payloads_seen_on_2026_11_17`.
