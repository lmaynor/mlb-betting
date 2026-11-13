# N6 fixture: NHL morning-skate line-change blurbs

Route N6 (NHL late line-change prose verifier), Argon ruling
`~/workspace/beezy/profitability/argon-decisions-api-expansion-VERBATIM-2026-11-13.md`
section 2 (N6) and section 3(c). The ruling's pre-launch bar: a fixture of
>= 100 historical morning-skate blurbs with explicit `pp_only_change`
items and >= 25 verified 5v5 line shuffles, labels in
{no_change, line_shuffle, promotion_demotion, pp_only_change}, and 0%
leakage of `pp_only_change` into 5v5 suspension (enforced by the gate
tests in `tests/test_decisions_prep_n6.py`).

## Counts (honest, as built 2026-11-13)

| | count |
|---|---|
| total items in `items.jsonl` | 241 |
| retrieved blurbs | 0 |
| seeded blurbs (`seeded=true`) | 241 |
| no_change | 9 |
| line_shuffle | 43 |
| promotion_demotion | 130 |
| pp_only_change | 59 |
| verified 5v5 shuffles (line_shuffle + promotion_demotion) | 173 |

Targets: total >= 100 met (241); >= 25 verified 5v5 shuffles met (43
strict `line_shuffle` alone; 173 including `promotion_demotion`);
explicit `pp_only_change` items present (59). The shortfall is on the
retrieval side only: **zero** blurbs are genuinely retrieved prose (see
sources below), so every item is seeded from a verified deterministic
diff, as the build brief permits. Do not quote this fixture as
"historical morning-skate reports"; it is a diff-seeded label fixture.

Eligible population: 282 capture records (141 banked games x 2 teams);
41 are a team's first banked appearance in its season and have no prior
state to diff against, leaving 241 labelable team-games (2023-24 ARI
segment: 9; Oct 13 - Nov 12, 2026 window: 232).

## Prose sources tried, and their yield

1. **Banked NHL news/prose in GCS or the repo** — none exists. The NHL
   tree holds masters, `NHL/lines/` artifacts, `NHL/raw/{pbp,boxscores,
   shiftcharts}` and shots only; there is no `NHL/lineups/rotowire/`
   archive object and no confirmed-goalies cache in the bucket. The
   repo's RotoWire prose puller (`scripts/pull_rotowire_prose.py`) is
   NBA-only and disabled by default. Yield: 0.
2. **RotoWire NHL lineups page, live** — fetchable (HTTP 200 on
   2026-11-13 with the declared Beezy Research user-agent), but it
   serves only today/tomorrow; a `?date=2026-11-11` request redirects
   (301) instead of serving the banked-window date, and the module
   contract in `nhl/data/rotowire_lineups.py` confirms only today /
   tomorrow exist. No historical per-game morning-skate or lineup
   snapshots are retrievable for the banked window. Yield for the
   banked window: 0. RotoWire's NHL news page likewise carries no
   retrievable per-game morning-skate archive for these dates.
3. **Day-over-day diff of the lines artifacts**
   (`NHL/lines/lines_2026-11-11.json` vs `lines_2026-11-12.json`) —
   real but small and partly illusory: 14 of 33 teams show a changed
   line_id, most of which are pure line_id permutations of identical
   trios (e.g. CAR D1/D2 swapping labels); ~7 teams show a true forward
   membership change. The artifacts are trailing-10 aggregates with no
   per-game date and **no power-play units at all**
   (`nhl/data/lines.py` is even-strength only), so they cannot produce
   `pp_only_change` items or per-team-game labels. Yield as a fixture
   source: not used (insufficient and unverifiable per game); the
   artifacts are still attached to capture records as decision context
   when strictly pre-game (22 records).
4. **Per-game realized diffs from banked shift charts (used)** — the
   deterministic source that produced all 241 items: each team-game's
   realized 5v5 trios + PP units (from its banked shift chart) diffed
   against the same team's previous banked game in the same season.
   This is the capture dataset of `ev/decisions_prep/n6_capture.py`
   (see Capture below). Yield: 241 labelable team-games.

## Construction per class

Every item is one team-game. `expected_*` is the team's realized state
in its previous banked game (same season); `realized_*` is the current
game's realized state. Both come from the capture dataset, never from
prose. The blurb is a template written strictly from the diff (players,
lines and PP1 named in the blurb are exactly the diff contents); each
item carries `seeded=true`, a `seeded_from` citation (prior game id +
shift-chart key + machinery), the full expected/realized trios, the
realized trio seconds, and both PP unit states.

* `no_change` — the unordered set of verified 5v5 trios is identical
  AND the PP units are identical (or PP is not comparable for one game,
  which the item notes in `detail.pp_comparable=false`; 5v5 identity is
  still exact). Rank order by single-game TOI alone is NOT a change.
* `pp_only_change` — the unordered set of verified 5v5 trios is
  identical AND the PP unit sets (PP1 or PP2 membership, from
  `pp_unit_guess`) differ, with PP comparable in both games. The diff
  is confined to PP units by construction.
* `line_shuffle` — 5v5 trio membership differs AND no player crosses
  the top-six boundary (see below).
* `promotion_demotion` — 5v5 trio membership differs AND at least one
  player crosses the top-six boundary: line rank <= 2 in one game and
  rank >= 3 (or absent from the verified trios) in the other. The
  crossing players are listed in `detail.crossers`.

## Verification method + TOI threshold

Realized lines are derived with the repo's own machinery
(`ev/decisions_prep/n6_capture.py`, over
`NHL/raw/shiftcharts/{game_id}.json.gz` + `NHL/raw/pbp/{game_id}.json.gz`):

* 5v5 state per second from `nhl.data.toi_splits` (`shifts_frame`,
  `roster_frame`, `timeline`, `anchor_to_pbp`); 5v5 mask is
  eff_home == 5 and eff_away == 5, exactly as
  `nhl.data.lines.game_pair_seconds`.
* A forward trio is **verified** only when its EXACT shared 5v5 time
  (seconds all three players are simultaneously on the ice at 5v5) is
  **>= 120 seconds** in the single game (`TRIO_MIN_S`; seed pairs need
  >= 120 s shared 5v5 time). The 900 s / 600 s thresholds in
  `nhl.data.lines` are for 10-game aggregates and cannot apply to one
  game; 120 s is the stated single-game analogue. Each item carries
  `realized_trio_seconds` so the verification is auditable.
* A `line_shuffle` / `promotion_demotion` label therefore requires the
  new trio to have actually played together at 5v5 for >= 120 s in the
  realized chart — not merely to be listed somewhere.
* PP units come from `nhl.data.toi_splits.build_game`
  (`pp_unit_guess`: PP1 at >= 50% share of team PP seconds, >= 10 s).
  A `pp_only_change` label requires PP units derivable in BOTH games
  and identical verified 5v5 trio membership.

Sanity anchor: the capture's realized EDM top line in the trailing
window is McDavid + Draisaitl + Podkolzin (EDM-F1 of the current lines
artifact), reproduced from the shift charts alone.

## Known limits

* **All blurbs are seeded.** They are faithful renderings of verified
  diffs, but they are not beat-writer prose: vocabulary, hedging and
  noise of real morning-skate reports are absent, so classifier scores
  on this fixture are an upper bound on prose robustness.
* **Single-game realized lines are volatile.** A trio is one quiet
  night (or one blowout) away from dropping under the 120 s threshold,
  and mid-game bench blending registers as a "change" against the prior
  game. That is why `promotion_demotion` (130) dwarfs `no_change` (9):
  game-to-game realized churn in this banked data is genuinely high.
  The fixture measures the gate against realized charts (the ruling's
  kill criterion), not against coach intent.
* "Expected" is the prior banked game's realized state, a proxy for the
  trailing lines artifact a pre-game process would hold; the banked
  window is sparse in 2023-24 (ARI only) and dense in Oct-Nov 2026.
* PP unit guesses blur on short power plays and 5-on-3s (inherited from
  `toi_splits`); 7 of 282 capture records have no derivable PP units.

## Capture

`ev/decisions_prep/n6_capture.py` implements ruling 3(c) clock 1 for
N6: deterministic, no API calls, idempotent (rerunning over the same
banked inputs reproduces the same records except the run-level
`capture_ts`; pin it with `--capture-ts` for a byte-identical rerun).

Record: `{team, game_id, game_date, season_year, decision_context,
realized_5v5_trios, realized_pp_units, capture_ts}` (JSONL, one record
per team-game, sorted by date / game / team). `decision_context` is the
expected-lines state available before the game: the prior banked
team-game's realized trios/PP units (`source:
prior_banked_team_game_realized`) or nulls for a first appearance, plus
the latest strictly-pre-game dated lines artifact's lines for the team
when one exists.

First pass (2026-11-13): **282 records** over 141 banked games
(2024-03-28 to 2026-11-12), written to
`gs://concrete-crow-445205-m4-mlb-data/DecisionsPrep/n6_capture/capture_2026-11-13.jsonl`.
A 20-record sample is banked beside this fixture as
`capture_sample.jsonl`.

    GCS_BUCKET=concrete-crow-445205-m4-mlb-data \
      python -m ev.decisions_prep.n6_capture \
        --out gs://concrete-crow-445205-m4-mlb-data/DecisionsPrep/n6_capture/capture_2026-11-13.jsonl

Cadence (scheduling itself is out of scope for this prep): rerun after
each night's shift-chart banking so every team-game's decision context
is frozen before its realized chart exists in the same run — in
practice a nightly run once `nhl.data.shiftcharts_bank` has banked the
night's games, writing a dated `capture_<date>.jsonl` per run.
