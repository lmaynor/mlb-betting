# Availability fixtures (Build #2)

Offline evaluation fixtures for `ev/decisions_availability.py`
(PlayerAvailabilityClassifier, Build #2 of Argon's Nov 11, 2026 Decisions-API
rulings; N2/N6 evaluation fixtures live in the prep package under
`tests/fixtures/decisions/n2_event_gate/` and `n6_line_verifier/`).

## goalie_items.jsonl (100, NHL item 2)

Labels and the `goalie`/`realized_starter` facts come from the real NHL
goalie boxscore master (`NHL/goalie_boxscores_master.csv`, 2024-2026 regular
seasons): the realized starter is the goalie flagged `starter` in the
boxscore for that team-game, and the other goalie is the second goalie on
that team-game's boxscore rows. Classes:

- `confirmed_starter` (50): item goalie IS the realized starter; RotoWire
  status Confirmed; prose states the confirmation.
- `unconfirmed` (30): item goalie IS the realized starter; status
  Unconfirmed/Likely; prose is hedged projection language. The gate must not
  confirm these (status conjunct fails) even though he started.
- `late_scratch` (20): item goalie is the NON-starter; RotoWire status is a
  stale Confirmed listing; the paired prose contains an early expectation
  followed by an explicit late update ruling him out. The gate must not
  confirm these: the trap class behind the ruling's 0-false-positive bar.

The prose texts are composed from the verified facts (names, teams, dates,
start outcomes) in RotoWire/ESPN style; they are not retrieved articles.

## status_items.jsonl (200, NBA item 3)

Grounded in the official NBA injury reports banked for the minutes program
(`reports/{date}/report_*.csv`) joined to hoopR player boxscores, using the
Arm S frame's decision-time rule: the designation is read from the last
report at least 60 minutes before that game's tip. Pools before sampling:
2,058 scratched / 4,078 played; sampled 100/100 (seed 20261113).

- `scratched_out` (100): designation Questionable/Doubtful/Probable at
  decision time; realized minutes 0 or no boxscore row, with the team-game
  verified present in the boxscores (absence is meaningful, not a data gap).
- `played` (100): designation at decision time; realized minutes > 0.

Blurb text is composed from the real designation and reason (an early
game-time-decision sentence plus a late resolution update matching the
realized outcome), in beat-report style; the outcome facts and designations
are records, the sentences are composed.

### Designation assertion and errata (2026-11-13, join audit)

Every designation in the fixture is re-derived from the banked reports
with the repo's own rule (`select_designation`, tip - 60, tip from the
boxscore mirror) by `scripts/derive_status_designations.py`; run against
the source data it fails (exit 1) on any disagreement, and the committed
derivation record `status_derivation.jsonl` is pinned to the fixture by
`tests/test_decisions_availability_fixture.py`. The join audit caught
S154 (Trey Lyles) carrying the 19:30 report's Questionable where the
rule yields the 20:30 report's Available; the full re-derivation found
9 such stale items, all corrected 2026-11-13 (designation field only;
blurbs and labels unchanged): S001 Questionable -> Out, S009
Questionable -> Out, S020 Questionable -> Out, S050 Questionable ->
Doubtful, S109 Questionable -> Available, S125 Questionable ->
Available, S137 Probable -> Available, S143 Questionable -> Available,
S154 Questionable -> Available. The corrected items' cached eval
answers were invalidated so they are re-asked under the corrected
designations. Note the pool description above is the sampling frame as
built; after correction the fixture also contains decision-time Out
and Available designations, which is what the rule actually yields.

## Bars (Nov 13 ruling / Nov 11 item verdicts)

- Goalie: 0 false positives (no late_scratch or unconfirmed item gated as a
  confirmed starter).
- Status: scratch recall >= 98%, false-scratch rate <= 2%.

Evaluation: `python -m ev.decisions_evals.eval_availability` (live Decisions
calls, token-paced; answers cached per fixture in `answers_*.jsonl`;
results in `eval_results.json` here).
