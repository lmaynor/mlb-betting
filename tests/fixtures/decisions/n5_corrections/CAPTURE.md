# N5 input capture — first pass (2026-11-13)

Implements Argon's sequencing ruling 3(c) clock 1 for the N5
post-settlement correction sentinel: deterministic logging of decision-time
inputs paired with realized ground truth, in `ev/decisions_prep/n5_capture.py`.
Companion to this fixture's `README.md` (the F4 reconstruction set).

## What ran

```
python -m ev.decisions_prep.n5_capture \
  --settled-dir   <NHL/paper_track/settled/*.jsonl, 2026-11-02..2026-11-12> \
  --games-master  NHL/games_master.csv \
  --skater-master NHL/skater_boxscores_master.csv \
  --goalie-master NHL/goalie_boxscores_master.csv \
  --out           capture_2026-11-13.jsonl
```

Full dataset: `gs://concrete-crow-445205-m4-mlb-data/DecisionsPrep/n5_capture/capture_2026-11-13.jsonl`
(5,353 records). A 20-record sample (8 with corrections) is committed here
as `capture_sample.jsonl`.

## Results of the first pass

- Settled rows paired: **5,353** (NHL paper track, Nov 2–12, 2026).
- Boxscore matched: 5,272. **Unmatched: 81** (all 81 player-absent:
  did-not-dress players with no boxscore row in the masters, per the
  2026-11-13 join audit; zero game-join failures; carried with
  `boxscore_current: null`, never guessed).
- **Corrections detected: 31** — settled actual differs from the current
  master value: 25 saves, 3 assists, 3 points. Worked example:
  2026-11-02 CGY@SJS — an assist (and the point) moved from Kiefer Sherwood
  (settled 1, now 0) to Michael Misa (settled 0, now 1): a textbook
  post-settlement scoring change, caught by the pairing alone.
- `settled_actual` is the value the tracker consumed at `settled_ts`
  (roughly first-final + hours); `boxscore_current` is the master value at
  capture time — for the Nov 2–10 games that is a ≥ +48h comparison, i.e.
  the ruling's "first-final vs +24h/+48h boxscore diff" realized against
  the one settled ledger reachable in GCS.

## Record shape

`{capture_id, source, game_key, game_id, game_date, player_id, player,
stat, line, settled_actual, settled_result, settled_ts, void,
boxscore_current, boxscore_source, boxscore_captured_ts, delta,
correction_detected, unmatched_reason, prose_items, prose_note}`.

`unmatched_reason` (added 2026-11-13, join audit) is null on paired
records; on unpaired records it is `game_unresolved` (the game did not
resolve against the games master) or `player_absent_from_boxscore` (the
game resolved but the player has no boxscore row for it: did-not-dress).
The first pass predates the field; the join audit characterized its 81
unmatched records directly and found all 81 were player-absent with zero
game-join failures. Future runs are self-describing.

## Cadence and format (documented, not scheduled)

Run nightly after settlement: refresh the three masters + the settled
prefix from GCS, run the module, append output under
`DecisionsPrep/n5_capture/`. Consecutive runs diff via
`diff_captures()` (first-final vs +24h/+48h). Capture files must never be
written into the settled input directory; the reader also hard-filters to
`YYYY-MM-DD.jsonl` names.

## Known limits

- **Prose leg is empty for NHL**: no banked NHL postgame prose source
  exists, so `prose_items` is `[]` with `prose_note` saying so. The NBA
  archives (`NBA/news/nbcsports_archive/`, `NBA/news/rotowire_archive/`)
  exist, but no NBA settled ledger is reachable from this environment:
  the production tracker DB is Cloud SQL (`MLB_DB_URL`/`DB_URL`, see
  `ev/model_picks._db_path`), and the local `EV_Alerts/data/ev_bets.db`
  mirrors are dev stubs (freshest: 12 bets, 0 settled, verified
  2026-11-13). The tracker adapter (`read_tracker_settled`) is implemented
  and tested; its first-pass yield against the stub was 0 records, and the
  ledger leg must run where `DB_URL` is set.
- Masters are current-state snapshots, not revision histories: the
  capture detects that a value moved, not when the league moved it.
  Correction timestamps must come from the prose leg when it exists.
