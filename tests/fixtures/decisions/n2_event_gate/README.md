# N2 event-gate fixture

Ground-truth fixture for the Decisions-API N2 route (Argon ruling,
2026-11-13, section N2): prose about a scheduled game is classified by
whether the *event itself* changed. Player availability news is explicitly
`no_change` here; that is Build #2 territory, not event state.

## Contents

`items.jsonl` — 250 items:

- **50 disruption items** (2023–2026, all with a retrieved source):
  - `postponed`: 34
  - `suspended`: 6
  - `venue_change`: 4
  - `format_change`: 4
  - `completed_early`: 2
  - By league: MLB 29, NBA 11, NHL 5, NFL 5
- **200 normal items**, all labelled `no_change` (see "Normals" below).

## Disruption item schema

```json
{"league": "MLB",
 "game": {"away": "New York Mets", "home": "Atlanta Braves", "date": "2024-09-25"},
 "class": "postponed",
 "blurb_text": "...",
 "source_name": "CBS Sports",
 "source_url": "https://...",
 "retrieved": true,
 "seeded": false}
```

- `retrieved: true` (43 items): the blurb is drawn from article body text
  actually retrieved from the source during fixture research
  (2026-11-13, web search/open).
- `seeded: true` (7 items): only headline/summary-level facts could be
  retrieved (league/team social posts, a postponement roundup page, a
  tag listing). The blurb is composed strictly from those retrieved facts
  and marked seeded so downstream scoring can weight it accordingly.

Every factual claim in every blurb is backed by the item's `source_url`;
no item was written from memory.

## Label definitions

- `postponed` — game not played on its scheduled date; moved to another
  date (weather, wildfire smoke/fire, ice storm, travel, safety, coach's
  death, hurricane).
- `suspended` — game started, halted before completion, resumed later (or
  never resumed and settled under suspended-game rules: the 2023
  Marlins–Mets game whose score reverted).
- `venue_change` — game played at a different venue than scheduled, or a
  home-venue relocation in effect for the game (Rams wild-card game moved
  to Glendale; Rays' 2025 season at Steinbrenner Field).
- `format_change` — game still played on its scheduled date at its venue
  but materially reformatted: Sunday games moved into Saturday
  doubleheaders ahead of Hurricane Hilary; a four-hour start-time shift
  for the solar eclipse.
- `completed_early` — game ended before its full scheduled length with the
  partial result standing as final.
- `no_change` — the game proceeds as scheduled. Injury reports, lineup
  news, and ordinary recaps/previews are `no_change` for this gate.

## Normals

The 200 `no_change` items are real game-day prose drawn from the banked
NBC Sports EDGE / RotoWire-style corpus at
`~/workspace/beezy/nbcsports-evidence` (shard 1 + shard 2 ledgers,
~54k banked items). Selection rules, applied programmatically:

- blurb must carry a game context (recap/preview phrasing such as
  "win over the …", "loss to the …", "game against the …");
- any blurb containing disruption language (postpone, suspend, cancel,
  weather, rain, storm, delay, doubleheader, wildfire, hurricane, …) was
  excluded by a word-boundary filter;
- exact-text dedupe; at most 3 items per player; evenly spaced sample
  across the corpus date range (2021–2026) for a 200-item draw.

Schema: `{"league", "game": {"date", "context"}, "blurb_text", "source",
"label": "no_change"}`.

### Normals v2 (2026-11-13, join audit)

In v1 a normal's `game.date` was the prose PUBLISH date and there was
no team pair, so the eval's deterministic slate matcher could never
match a normal to its game and the material-change false rate was null
by construction (reported as not evaluable, never 0.0). v2 joins every
normal to the actual game its prose discusses, rebuilt by
`scripts/build_n2_normals_v2.py`:

- `game` is now `{"away", "home", "date", "context"}` with full team
  display names and the TRUE game date, item for item like the
  disruptions; the publish date moves to `prose_published_date`, the
  corpus subject to `player`, and each normal carries a unique
  `item_id` (`N2N-001`..`N2N-200`).
- Join evidence (first tier that yields exactly one game; the subject's
  own boxscore row is never required, ESPN omits inactives): boxscore
  slate uniqueness 140, headline weekday 50, banked official injury
  reports 10. Per-item provenance: `normals_v2_build.json`.
- 193 of the 200 v1 normals joined uniquely and were kept with their
  original blurb and context; 7 did not join uniquely (5 no candidate,
  2 ambiguous) and were replaced by 7 backfills drawn from the same
  corpus under the v1 selection rules above, keeping only draws that
  join uniquely. The fixture stays at 200 normals.
- The v1 normals are preserved verbatim in `normals_v1.jsonl`. The 50
  disruption items are byte-identical to v1, so their cached eval
  answers remain valid.

## Known limits

- `completed_early` has only 2 items, both NFL preseason games shortened
  by serious player injuries. No regular-season MLB/NBA/NHL examples were
  found in retrieved sources (a dedicated search for MLB rain-shortened
  completed games returned only minor-league results). The class is
  therefore the fixture's thinnest and its recall estimate will be noisy.
- No NBA or NHL `suspended` items were found; all 6 suspensions are MLB
  (5) and NFL (1). NHL coverage overall is thinner (5 items) than MLB/NBA.
- Two `venue_change` items are relocation-*context* rather than a
  same-game move: the Athletics' final Oakland Coliseum game and the
  Coyotes' final Arizona game were played as scheduled at the departing
  venue. They are included because the surrounding prose is venue-change
  prose a live watcher would have to classify, but scorers should know
  they differ from the two true relocations (Vikings–Rams, Rockies–Rays).
- Normals are all NBA, because the available banked prose corpus is
  NBA-only. Disruption items skew MLB for the opposite reason: MLB
  postponements are the most heavily covered.
- Normals include injury-availability notes by design: for this gate an
  injury note is `no_change`. A watcher that flags them is confusing
  Build #2 signal with event state.
