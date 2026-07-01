# Reference: external feature recipes (not wired in)

`feature_recipes_retrosheet.py` — lookahead-safe MLB feature-engineering recipes
lifted verbatim (2026-07-01) from the external hobby project
`gmalbert/baseball-predictions` ("Betting Cleanup"), `src/models/extra_features.py`.

**Status: reference only.** Nothing here imports cleanly in our repo — the recipes
depend on that project's Retrosheet parquet schema and its `retrosheet` /
`src.ingestion.{fg_guts,fg_park,chadwick}` modules. Held as a recipe library for
whenever model work resumes.

**Why we're not integrating it:** our signed-off verdict
(`MEMORY: project_gen_preds_backtest_verdict`) is that no capturable model-vs-line
edge exists (CLV≈0). Better features sharpen accuracy against our own line, not the
market — so they don't touch the blocker. The pivot stays on open→close line
movement / CLV.

**Worth mining when model work resumes** (recipes we don't already have):
umpire run-environment tendency (home-plate + base umps, expanding lagged mean),
Pythagorean win% differential, team scoring consistency, SB success rate, platoon
advantage (SP hand × lineup handedness), SP-vs-specific-opponent history, and the
Retrosheet wind-direction / dome-site encodings. FIP / wOBA / Savant aggregates
overlap what we already ingest — skip those.
