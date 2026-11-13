# N5 correction reconstruction set

Ground-truth fixture for the Decisions-API N5 route (Argon ruling,
2026-11-13, section N5; shadow-only): documented league stat corrections /
scoring reversals, used to test whether a prose watcher can catch a
correction from the announcement before (or as) the box score changes.

## Contents

`items.jsonl` — **25 player-stat correction items**, every one backed by a
retrieved source; nothing was invented to reach the count.

By league: NHL 9, NFL 9, NBA 4, MLB 3.

These 25 items come from **23 distinct announced correction events**: one
NHLPR scoring-change post credited two players (Batherson and Stützle,
assists on the same Cozens goal), and one Elias weekly list changed two
stat lines for the same player (Fred Warner, forced fumbles and assists,
same game).

## Item schema

```json
{"league": "NHL", "game_date": "2025-10-21", "player": "Drake Batherson",
 "team": "Ottawa Senators", "stat": "assists",
 "value_before": 0, "value_after": 1,
 "announced_date": "2025-10-22", "prose_announced": true,
 "source_name": "Yardbarker (NHL Public Relations)",
 "source_url": "https://..."}
```

## Sourcing method

Corrections were collected only from sources actually retrieved during
research (2026-11-13): league announcements as reported by AP/ESPN/beat
coverage, NHLPR "OFFICIAL SCORING CHANGE" posts (via Athlon/Yardbarker
write-ups that quote them verbatim), the MLB Scoring Changes reporting
stream (via theScore), and the Elias Sports Bureau weekly NFL stat-change
lists republished in full by MyFantasyLeague. Player-team assignments for
the NFL items were corroborated against retrieved 2026 roster/depth-chart
sources (Sportradar game pages, chargers.com, team coverage).

Conventions and derivations, stated once here rather than hidden:

- **NHL value basis.** Some NHL items are play-level (assists credited on
  one named goal: 0→1, or removed: 1→0). Others are season totals as
  reported *after* the change (Marner "has 19 assists following the
  adjustment"), with `value_before` derived by subtracting the single
  assist the correction added or removed (Marner 18→19, Brink 2→3,
  Hughes 7→8, Zucker 1→2). No NHL item involves more than a one-assist
  swing.
- **Henson (NBA, 2014).** The source states his total was changed *to*
  six points after a made three was reclassified as a two;
  `value_before` 7 is the arithmetic consequence (−1 point), not a
  separately quoted number.
- **Ohtani (MLB, 2026).** The one same-game ruling in the set: the
  apparent inside-the-park home run was officially recorded the same
  night as a triple plus an error on the right fielder, and reported as a
  scoring change in prose the next day. Included as a scoring reversal;
  flagged here because it was not a days-later reversal like the rest.
- **NFL game dates.** The Elias lists name games by matchup under a week
  heading. `game_date` is the Sunday of that Elias week (the 2026 Sunday
  cadence was verified against retrieved schedule pages: Week 5 = Oct 11,
  Week 6 = Oct 18, Week 9 = Nov 8). `announced_date` is the publish date
  of that week's list (Wednesdays; Week 9's list published Thu Nov 12).
- **Simpson (MLB, 2025).** The scoring change was announced in the
  press box during the game itself, so `announced_date` = `game_date`
  and it is the set's clearest "prose before box score" case.

## Known limits

- **Selection effect (the big one):** `prose_announced` is `true` for all
  25 items. That is not evidence that leagues always announce
  corrections; it is evidence that an unannounced correction leaves no
  retrievable source to reconstruct from. Silent box-score edits are
  precisely the recall risk N5 runs shadow-only to measure, and this set
  cannot measure it.
- **No systematic feeds.** Only the NHL (NHLPR posts) and NFL (Elias
  weekly lists) publish corrections in a regular, findable form. The NBA
  surfaces corrections only through ad-hoc league statements (hence just
  4 items, one from 2014 — this fixture has no date restriction), and MLB
  corrections are scattered across the scoring-changes stream and beat
  reporting (3 items). Counts by league reflect publication practice,
  not correction frequency.
- NHL corrections found are almost entirely assist-credit changes on
  goals; other stat families (shots, hits, saves) rarely get prose
  announcements and are underrepresented.
- NFL items skew toward defensive/IDP stat families (tackles, assists,
  quarterback hits) because those dominate the Elias lists; offensive
  counting stats are corrected less often in the published lists.
