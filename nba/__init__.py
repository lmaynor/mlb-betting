"""NBA data package.

Additive, data-collection-first basketball expansion of the platform. Reuses
mlb_core.storage for transparent GCS/local I/O. NBA data lives in the shared
bucket under the NBA/ prefix. No NBA model or betting runner exists yet --
this package only fetches and flattens SportsBlaze box-score data.

See nba/README.md and handoffs/scope_nba_expansion_2026-06-14.md.
"""
