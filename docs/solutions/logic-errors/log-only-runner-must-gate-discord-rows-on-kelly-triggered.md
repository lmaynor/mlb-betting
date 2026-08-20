---
title: A log-only system posted every scored prediction to Discord, including negative-edge ones
module: mlb/runners/run_k.py, mlb/runners/run_f5.py, mlb_core/notify/discord.py
tags: [discord, post_bets, kelly_triggered, log_only, pitcher_er, f1h, contract]
problem_type: logic_error
category: logic-errors
date: 2026-08-20
---

## Problem

A user spotted a PITCHER_ER pick for Cade Cavalli posted to `#daily-picks`
with a **negative edge** -- something that should never clear the
`min_edge` gate and should never be Discord-visible while PITCHER_ER is
log-only (per CONTEXT.md's system table: "Live (log-only)").

## Root cause

`mlb_core.notify.discord.post_bets(bets, system, run_date)` posts
whatever list it is handed, **unconditionally** -- one embed field per row,
no internal filter. Every caller is responsible for pre-filtering to
`kelly_triggered=True` rows before calling it. Every model system does
this:

```python
for _, row in today_df.iterrows():
    triggered = bool(row.get("kelly_triggered", False))
    bet_id = tracker.log_bet(..., kelly_triggered=triggered, ...)
    if bet_id == -1:
        continue
    if triggered:                      # <-- the gate
        bets_logged += 1
        bet_rows.append(row.to_dict())
post_bets(bet_rows, system=..., run_date=run_date)
```

Two sub-market loops -- PITCHER_ER inside `run_k.py`'s `run()`, and F1H
inside `run_f5.py`'s `run()` -- were both missing that `if triggered:`
gate:

```python
# BEFORE (both files, same bug):
for bet in er_bets:                    # or sub_results.get(sys_key, [])
    ret = er_tracker.log_bet(..., kelly_triggered=bet["kelly_triggered"], ...)
    if ret != -1:
        er_logged += 1
        er_rows.append(bet)            # <-- appended UNCONDITIONALLY
post_bets(er_rows, system="PITCHER_ER", run_date=run_date)
```

Both PITCHER_ER and F1H are log-only systems whose `kelly_triggered` is
**structurally always False** (`PITCHER_ER_LOG_ONLY` / F1H's
`LOG_ONLY_SYSTEMS` force `stake=0.0`, which zeroes `kelly_triggered` too).
So `post_bets()` was receiving the FULL scored slate every run --
including negative-edge, zero-stake predictions -- and posting all of it
to `#daily-picks` looking exactly like real, actionable picks. This had
been true since PITCHER_ER/F1H were first shipped; it just took a
negative-edge row landing at the right/wrong moment for it to be visibly
wrong to a reader (a positive-edge non-triggered row looks plausible at a
glance; a negative-edge one doesn't).

## Why it's easy to miss

Every OTHER runner's Discord-bound list is built inline, right next to its
`log_bet()` call, in the SAME loop that also filters for `bets_logged`
counting -- one look and the gate is visibly there. PITCHER_ER and F1H are
both *sub-markets* scored by a DIFFERENT runner's `run()` (K's and F5's,
respectively) in a second, separate loop appended after the primary
system's own (correctly-gated) loop -- easy to copy the `log_bet()` call
faithfully while dropping the `if triggered:` wrapper around the
Discord-list append, since it reads as "just persist everything I scored."

## Fix

Add the identical gate both places:

```python
for bet in er_bets:
    ret = er_tracker.log_bet(..., kelly_triggered=bet["kelly_triggered"], ...)
    if ret == -1:
        continue
    if bet["kelly_triggered"]:
        er_logged += 1
        er_rows.append(bet)
post_bets(er_rows, system="PITCHER_ER", run_date=run_date)
```

Every prediction is still logged to the DB either way (the "log every
scored prediction" contract, CONTEXT.md s5, is unaffected) -- only the
Discord-bound rows need the gate.

## Prevention

CONTEXT.md's "Discord posting contract" (s5) now states this as an
explicit RULE. Any NEW sub-market loop added to an existing runner (the
template a future PITCHER_ER/F1H-shaped addition would copy) must be
checked against this rule specifically -- grep for `post_bets(` in the
runner and confirm the list it's handed was built inside an `if
triggered:` (or equivalent) branch, not appended unconditionally next to
`log_bet()`.
