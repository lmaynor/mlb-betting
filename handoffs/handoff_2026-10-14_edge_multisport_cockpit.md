# Handoff -- 2026-10-14 -- /edge multi-sport prop cockpit + /ev, nav, hero polish

Branch `feat/edge-multisport-cockpit` (one PR). NOT deployed. Backend must ship BEFORE the cockpit shows data: until
`GET /api/public/edge/cockpit` is live the /edge page falls back to the MLB model board (cockpit prop is null) and the
other sport tabs say "Cockpit feed unavailable".

## Built
- Backend: `public_api.get_multisport_cockpit(sport, limit, engine, min_ev)` + route `/api/public/edge/cockpit` (main.py).
  `mlb_core/data/cockpit_form.py` = per-sport game-log index, 10-min in-memory cache, last-10 vs line (`recentForm`).
- Frontend: `app/edge/cockpit.tsx` (list + inspector, FormSparkline, UsageSparkline, OddsMatrix), sport switcher in
  `edge-client.tsx`, proxy `app/api/edge-cockpit/route.ts`, `apiGetEdgeCockpit`.
- Polish: /ev MARKET badge + Kelly on shadow rows with >= 3 books, `.ev-card-grid`, segmented pills, `$` bankroll input,
  Methodology drawer; nav (The Edge in tier 1, tier 2 de-duplicated); hero pill + copy; hub copy.

## Deviations from the brief (deliberate)
- Spec file names do not exist: real keys are `NHL/skater_boxscores_master.csv`, `NHL/goalie_boxscores_master.csv`,
  `NBA/player_boxscores_master.csv` (spec aliases are tried as fallbacks). NFL weekly stats have no calendar date -> "W5" labels.
- `get_multisport_cockpit` takes an optional `engine=` (MLB rows need the DB); without it MLB reports `unavailable`.
- Bar colors follow the FLAGGED side: for an Under, green = stayed under. L5/L10 pills are side-aware.
- MLB form comes from the existing Statcast enrichment (stat must match the pick's stat), not a new master.
- The old MLB board is kept as the `MODEL BOARD` view, not removed.

## Open / decide
- Sizing stakes on unvalidated NFL/NHL/NBA consensus rows was an explicit product call; copy still says "not yet in the public track record".
- Overlap: this PR also carries the in-flight "Compare N books" drawer + `all_books` plumbing (ev-client.tsx, types.ts,
  public_api.py `_book_strip`, their tests) because the cockpit odds matrix depends on `all_books`.
- `ev/props.py` must write `all_books` for real data to appear in the matrix (separate change).
