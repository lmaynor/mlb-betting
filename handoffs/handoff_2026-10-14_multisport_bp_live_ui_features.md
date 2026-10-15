# Handoff -- 2026-10-14 -- BettingPros live adapter, pooled fallback, Compare-books drawer, OG card, teammate-absence features

Branch `feat/multisport-bp-features` (one PR). Nothing deployed, no job/scheduler touched, no ParlayAPI credit spent, no network write.
Verified in a clean worktree: pytest 1,807 passed, jest 119 passed (7 skipped), `tsc --noEmit` clean, `bash -n` + shellcheck on the new script.

## Built
- `ev/bettingpros_live.py`: BettingPros /v3/offers -> `ev.props.Quote` (free; own 11-book map incl. Novig/ProphetX; DFS/consensus/open/Kalshi dropped), `merge_quotes` (ParlayAPI wins). NOT wired into any sweep yet.
- Pooled fallback in `ev.sweep_nfl_props.rows_from_snapshot` (`KEXEC_ALLOW_POOLED_FALLBACK`, default ON). Rows tagged `consensus_src/interpolated/exact_n_books`. **The DEMO executor (`props_exec.build_latest_local`) inherits the switch**; set it to 0 to restore exact-only. The pooled shift model is still unvalidated.
- `all_books` per-book strip on Sink A rows (`ev/props.py`), passed through `public_api._outlier_row`; "Compare N books" drawer on /ev; `/api/og/ev-recap-card`.
- `deploy/rollout_multisport_live.sh` (read-only v4 check, never drops the index; DRY_RUN/ASSUME_YES; see RUNBOOKS s9). Tested only against a stub gcloud.
- `mlb_core/odds/bettingpros_loader.py` + CLI + three evaluator hooks; teammate-absence features (NFL/NBA/NHL) + `nba/data/espn_injuries.py`. Details in CONTEXT s21.
- Fixed a pre-existing failing test on main (pandas 3 `merge_asof` resolution mismatch in `nba/props/features.py`).

## Spec deviations (deliberate)
- NBA stat keys use ev.props' canonical names (`points`, `rebounds`, `pra`), not the spec's `pts/reb`; the real BettingPros slug is `points-assists-rebounds`.
- Kalshi series and ParlayAPI market request lists were NOT widened (cost credits / unverifiable offline). Registry aliases only.
- `interceptions`/`kicking_pts` NFL model stats skipped. No walk-forward was run on real NFL data (none local).
- `interceptions` in the ev registry maps `player_interceptions`/`player_pass_interceptions` (the spec's `player_pass_tds` alias already means pass_tds).

## Evidence status
No edge is claimed anywhere. The new features are accuracy-only results on prior local data; the BettingPros loader has never seen real parquets.

## Unverified / open
- Whether BettingPros `updated`/`scheduled` are both UTC (`pregame_only` is off by default); NHL away/home code agreement; real injuries `report_status` values for NFL.
- Another session's uncommitted /edge cockpit work (main.py, beezy-vip/app/edge/*, nav, hero, ev-client restyle that removes SHADOW labelling and sizes shadow rows) was in the shared checkout and is deliberately NOT in this PR. Note it conflicts with the "shadow rows stay shadow" rule this PR's drawer test asserts.
