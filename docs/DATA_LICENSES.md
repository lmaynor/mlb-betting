# Data source licenses and terms

Per-source licensing notes for data this repo reads. Convention follows
docs/solutions/conventions/nhl-data-source-licensing.md: a source is listed here BEFORE its data ships in a
product surface, with what it allows, what it requires, and where that leaves us. When in doubt, the stricter
reading wins.

## Soccer vertical (Phase 1, added 2026-11-03)

All Phase 1 soccer sources are free; the vertical runs at $0 data spend. Paid soccer data (StatsBomb API,
Opta-class feeds, SportMonks xG add-on) is deferred behind the Phase 2 gate in soccer/props.py and gets its own
entry here before any purchase.

| Source | What we take | License / terms | Requirements on us |
| --- | --- | --- | --- |
| football-data.co.uk | Season CSVs: results, shots, Pinnacle opening/closing odds (soccer/data/ingest.py) | Free for personal and commercial use per the site's own terms; data supplied by the site's contributors / BetBrain lineage | Credit football-data.co.uk where the data is displayed; do not redistribute the raw CSVs as a dataset |
| Understat | Match-level xG via the public league pages (soccer/data/ingest.py) | No published API license; data is displayed publicly on understat.com. We read at page cadence (one league page per league per run, cached), never bulk-scraped | The xG definition is Understat's own model; every surface that shows it names the variant `understat_shot_v1`. Re-check terms before any redistribution beyond beezy.fyi displays |
| ClubElo | Daily team Elo ratings from api.clubelo.com | Free public API by Lars Schiefler; attribution requested | Credit ClubElo as the ratings prior source; the Elo numbers are an anchor/sanity input, never presented as our own model output |
| Reep Register (withqwerty) | Cross-provider club identity bridge: seed dictionary in soccer/data/reep_register.py, optional register CSV merge | Open-source project (github.com/withqwerty/reep); register data published for reuse | Credit the Reep Register; canonical codes derived from it are an internal join key, not a displayed dataset |
| StatsBomb Open Data | NOT read by Phase 1 code. Reserved for fitting/validation experiments (shot and prop distributions) | StatsBomb's open-data license: free with attribution; commercial use of the data itself is restricted, models trained on it must not be redistributed as data | Any use requires the StatsBomb attribution notice on the surface that shows it, and a fresh license read before it touches a shipped model |
| football-docs / Nutmeg (withqwerty) | Documentation and metric definitions consulted during development (metric cards: the xG variant contract) | Open source (MIT-style tooling); docs index their providers' own documentation | Definitions are cited by variant ID in code (`understat_shot_v1`); no provider documentation text is republished |
| API-Football (API-Sports) | Phase 2 ONLY (gated off): fixtures, lineups, injuries, player stats | Paid subscription terms (Pro $19/mo at scoping time); no xG product | No use until the Phase 2 gate clears and the plan is purchased; key lives in env / Secret Manager only |
