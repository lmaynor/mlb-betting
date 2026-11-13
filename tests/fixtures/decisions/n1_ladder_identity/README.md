# N1 fixture — ladder market-identity labels

Hand-labeled fixture for route N1 (ladder raw-payload market-identity
adjudicator) of Argon's Decisions-API expansion ruling (Nov 13, 2026,
section N1). The ruling's kill criterion is evaluated on a fixture of
>= 500 historical ambiguous ladder labels across books: kill if false
canonicalization > 0.5%, scope disagreement > 1.0%, or quarantine-rate
drop < 30% vs parser-only. This fixture holds **660 labels** (target met
from the genuinely ambiguous, deterministically grounded population —
see Counts; no unambiguous entries were relabeled to reach the number).

Labels were produced by a deterministic labeling program over the banked
raw payloads (not by the extractor under test, and never from the
extractor's resolution of the same entry — see Grounding). The labeling
script was run once for this prep build; its rules are the ambiguity
definition below, applied mechanically, with every candidate that failed
a grounding bar excluded and counted.

## Source and sampling

- Source: ParlayAPI single-event payloads banked by the NFL EV sweep at
  `gs://concrete-crow-445205-m4-mlb-data/EVSweep/nfl_props/raw/{ET day}/*.json`.
  Inventory of the window 2026-10-04..2026-11-12: 27 ET days present,
  16 distinct books across the corpus (bet365, betmgm, betrivers,
  bovada, caesars, draftkings, fanduel, fanatics, fliff, hardrock,
  novig, parx, pinnacle, sleeper, thescore_bet, underdog), including the
  DFS / pick'em apps sleeper, underdog and fliff (prizepicks, dabble and
  betr do not appear in any sampled payload).
- Scanned in full: all 737 payloads of ET days 2026-10-04 (420 files)
  and 2026-11-08 (233 files), plus the latest snapshot per event for
  2026-10-12, 10-18, 10-25, 10-31, 11-01, 11-05, 11-11, 11-12 (84 files)
  — 71 distinct events. Market-key census over all 737 payloads: 22
  distinct `player_*` keys, of which exactly two are absent from the
  parser's table (`ev/props.py` `STAT_KEYS`), and **zero** `*_alternate`
  keys anywhere in the window (the feed's alternate tier is not banked;
  every ladder in this corpus rides inside plain main market keys). No
  outcome name or description anywhere in the corpus carries milestone
  text ("50+ ..." style): descriptions are player names only, so
  milestone semantics in this feed are structural (one-sided Yes
  quotes), not textual.
- Fixture entries are drawn from the latest snapshot of each event
  (sidedness evidence uses every snapshot of the event where the full
  day was scanned). Each entry is one distinct quote keyed by
  (book, event, player, market key, point); the same quote reappearing
  in later snapshots is not a second entry.
- The candidate pool after grounding was 6,431 entries (75 unmapped-key,
  2,538 one-sided ladder rungs, 3,818 two-sided off-main rungs). The
  fixture keeps all 75 unmapped-key entries and deterministic
  square-root-proportional-by-book stride samples of the two ladder
  classes (285 / 300), for cross-book balance.

## Ambiguity definition (operational)

A raw quote is in the ambiguity set iff the feed's plain main-market
mapping (`ev/props.py` `stat_of` + the parser's folding in
`parse_event_props`) does NOT settle its canonical identity — stat,
rung, side, and scope (main | alternate | milestone). Three classes:

1. **`unmapped_key`** — the market key is not in the parser's key table,
   so the stat itself is unsettled and the parser drops the quote.
   The only two such keys in the window:
   - `player_touchdown_passes` (pinnacle) — e.g. pinnacle quotes Cam Ward
     "Over Cam Ward" at 0.5 under this key while 10 other books quote
     the same player at 0.5 under mapped pass-TD keys.
   - `player_passing_interceptions` (fliff) — the mapped table carries
     `player_interceptions` / `player_pass_interceptions` only.
2. **`onesided_ladder_rung`** — a one-sided rung of a multi-point ladder
   posted inside a plain main market key. Every such quote in the corpus
   is a `Yes`-named outcome with no Over/Under pair (Bovada's milestone
   ladders: e.g. Cam Ward pass yards "Yes" at 199.5, +125, while the
   cross-book main line is 178.5; Novig and betrivers/parx post the same
   shape in receiving markets). Milestone-threshold vs alternate-rung
   semantics differ exactly here: the parser folds `Yes` to "Over at
   point", which erases whether the quote is a reach-at-least-N
   milestone or a one-sided alt rung. One-sided rungs sitting AT the
   cross-book main point were excluded (77 candidates): main-vs-milestone
   cannot be grounded for them.
3. **`twosided_offmain_rung`** — a two-sided rung of a multi-point
   ladder inside a plain main market key whose point is not the
   cross-book main point (Novig's exchange ladders on 90/90 receiving
   player instances per the Nov 11 audit; Bovada's two-sided ladder
   rungs; Fanatics' 5-yard grids; DraftKings/BetMGM/Caesars/bet365
   second lines). The parser settles stat and rung but has no scope
   signal: the key carries no `_alternate` suffix, so every rung looks
   like a main line. The rung AT the modal main point is the main line
   and is never included (it is an exact deterministic match once
   consensus is consulted, and padding the fixture with settled mains
   would be dishonest).

Explicit non-examples (never selected, matching
`select_ambiguity_set` in `ev/decisions_prep/n1_ladder_identity.py`):
single-point two-sided quotes under mapped keys; standalone one-sided
`Yes` quotes with no ladder (e.g. a book's only anytime-TD quote);
two-sided rungs at the cross-book main point.

## Label definitions

Each line of `labels.jsonl` is one quote:

- Entry fields: `book`, `event_id`, `game` ("AWAY @ HOME"),
  `commence_time`, `player` / `player_norm`, `raw_market_key`,
  `raw_description` (the outcome description exactly as printed,
  team suffix included), `raw_name` (the outcome name for the labeled
  side), `raw_point`, `raw_price` (American price of the labeled side),
  `raw_price_side_info` (`sides_present`, `over_price`, `under_price`,
  `yes_price` as posted in the source payload), `parser_candidate_stat`
  (the key table's stat for the raw key, or null), 
  `cross_book_main_point`, `source_object` / `source_day` (provenance
  back to the raw payload; raw payloads are not copied into the repo).
- `label.canonical_stat`: one of the parser's canonical stats
  (pass_yds, rush_yds, rec_yds, receptions, pass_tds, anytime_td,
  pass_attempts, pass_completions, rush_attempts, interceptions).
- `label.rung`: the quote's point (equals `raw_point`). For a milestone
  quote the rung is the feed point of the threshold (a "reach 200+"
  quote banked at point 199.5 has rung 199.5).
- `label.side`: the rung's priced direction — `over` for over/under
  rungs, `yes` for one-sided Yes milestone quotes. (Two-sided entries
  are anchored on their over side; both prices are in
  `raw_price_side_info`.)
- `label.scope`: `main` (the book's main line for that player/stat),
  `alternate` (a non-main rung of a two-sided ladder), or `milestone`
  (a one-sided reach-at-least-N threshold quote).

## Grounding (per class)

Labels derive only from deterministic evidence, strongest first.
(a) = same book + player + game anchor; (b) = cross-book consensus;
(c) = settled game logs — not needed anywhere in this fixture.

- `unmapped_key` — grounding (b) only, at full strength: the label stat
  is the UNIQUE canonical stat that >= 2 other books quote for the same
  player at the SAME point under their own mapped keys in the same
  payload (the entry's own book contributes nothing). Scope is main iff
  the point equals the leave-own-book-out modal main point for that
  stat. 10 candidates were excluded: 9 where a second stat also had
  >= 2 books at the same point (e.g. a QB's 1.5 point is also a rushing
  line for other books) and 1 with no cross-book main point. The
  parser's resolution of the same entry cannot be circular here — for
  an unmapped key the parser resolves nothing.
- `onesided_ladder_rung` — scope milestone is grounded by structure:
  the quote is `Yes`-named and one-sided in EVERY sampled snapshot of
  the event, sits in a >= 2-point ladder for that book/player/market,
  and its point differs from the cross-book modal main point computed
  from >= 2 other books' two-sided quotes ((b)); where the same book
  also posts a two-sided rung for the player/market, that rung anchors
  the ladder ((a), recorded in `grounding`). The stat is the key
  table's, corroborated by >= 1 (in practice >= 9) other books quoting
  the same stat for the same player in the payload — never by the
  extractor's treatment of this entry, whose scope output does not
  exist. 158 candidates whose rung was two-sided in some snapshot and
  77 one-sided-at-main candidates were excluded as ungroundable.
- `twosided_offmain_rung` — scope alternate is grounded by (b): the
  rung differs from the leave-own-book-out modal main point (>= 2 other
  two-sided books required); (a) applies as above when the book's own
  ladder contains its main rung. 41 ladder candidates with no
  cross-book main reference were excluded.

`grounding` per entry is `cross_book_consensus` or
`same_book_main_anchor+cross_book_consensus`; `grounding_detail`
spells out the evidence (book counts, modal point) for audit. No entry
carries `ungrounded_excluded`: ungroundable candidates were excluded
from the fixture entirely (326 counted above) rather than shipped.

## Counts

Total: **660** labels over **70 events**, 8 ET days, **14 books**.

By class: unmapped_key 75 · onesided_ladder_rung 285 ·
twosided_offmain_rung 300.
By scope: main 75 · alternate 300 · milestone 285.
By grounding: cross_book_consensus 195 ·
same_book_main_anchor+cross_book_consensus 465.

By book: bovada 257, novig 186, pinnacle 73, bet365 18, draftkings 18,
sleeper 16, underdog 16, caesars 14, betrivers 13, fanatics 12,
parx 11, betmgm 10, fanduel 9, fliff 7.
By stat: rec_yds 224, rush_yds 141, receptions 129, pass_yds 90,
pass_tds 73, anytime_td 2, rush_attempts 1.
By day: 2026-10-04: 51, 10-12: 40, 10-18: 120, 10-25: 167, 11-01: 199,
11-05: 6, 11-08: 57, 11-12: 20.

## Known limits

- Scope is confounded with class by construction (unmapped-key quotes
  grounded out as main-line quotes in all 75 cases; ladder classes are
  alternate/milestone by definition). The fixture tests stat
  canonicalization across all classes and scope on the ladder classes;
  it does not contain a grounded off-main unmapped-key example, because
  the window's two unmapped keys quote at consensus points.
- The cross-book main point is the modal two-sided point across other
  books — a deterministic proxy. Where books genuinely split (e.g.
  Jerry Jeudy receptions on 2026-10-25: 6 books at 1.5, 5 at 2.5), the
  label follows the mode; near-tie player-games are the fixture's
  softest labels.
- For events outside the two fully scanned days, sidedness was checked
  against that event's single latest snapshot only; "one-sided in every
  snapshot" is strongest for 2026-10-04 / 2026-11-08 entries (up to 28
  snapshots per event).
- The feed's true retail alternate ladders (DraftKings/FanDuel
  `*_alternate` markets) are not banked in this window at all, so the
  fixture cannot cover them; if the sweep ever banks the full tier,
  this fixture needs a new class, not a relabeling of these entries.
- Player identity follows the feed's own descriptions; team-defense
  "players" in anytime-TD markets (e.g. "Arizona Defense") were not
  specially handled and none landed in the labeled classes.
