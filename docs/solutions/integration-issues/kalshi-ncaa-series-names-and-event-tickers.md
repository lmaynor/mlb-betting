---
title: Kalshi college series: the football total series name moved, and a series lists EVERY game so markets must be picked by event ticker
module: contest.halftime
tags: [kalshi, ncaaf, ncaab, series, event-ticker, halftime, contest]
problem_type: integration-issue
category: integration-issues
date: 2026-11-17
---

## Series names (checked live 2026-11-17)

| market | football | basketball |
|---|---|---|
| moneyline | `KXNCAAFGAME` | `KXNCAAMBGAME` |
| spread | `KXNCAAFSPREAD` | `KXNCAAMBSPREAD` |
| total | `KXNCAAFTOTAL` (two A's) | `KXNCAAMBTOTAL` |

`KXNCAFTOTAL` (one A, the name probed on 2026-11-10 and pinned in `tests/test_ncaaf_banker.py` / `tests/test_kalshi_ncaa_wiring.py`) returned
ZERO open markets on 2026-11-17. `contest/halftime.py` reads both names. **The NCAAF banker still pins the one-A name and may be getting no
football totals: check it (it is outside the contest PRs).** `scripts/contest_halftime_capture.py` has the same stale name.

## Market shapes

- moneyline: title `"<Team> wins"`
- spread: title `"<Team> wins by over X points"`, `floor_strike` X, `strike_type: greater` (P(team margin > X))
- total: title `"Full Game: Over X points scored"`, `floor_strike` X (every market in the football total series was a full-game total)
- prices: `yes_bid_dollars` / `yes_ask_dollars` strings; the mid is the probability. The unauthenticated endpoint 429s a tight loop over the series.

## The bug this prevented

A series returns the whole day's games. The first halftime pricing took the total rung nearest 50% across ALL games (a total market carries no team
name), so one game would have been priced off another game's total. A game's markets are now selected by EVENT TICKER (`KXSERIES-26NOV21PITTLOU`,
the part after the series name): found from the team names on the moneyline / spread titles, fail closed if zero or several events match.
`tests/test_contest_halftime.py::test_other_games_markets_in_the_same_series_never_leak_in` guards it.
