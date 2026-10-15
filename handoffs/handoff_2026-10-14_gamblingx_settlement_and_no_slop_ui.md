# Handoff -- 2026-10-14 -- #GamblingX settlement engine + no-slop-ui (PRs #54-#58)

Nothing here is deployed. No Cloud Run Job, scheduler, secret or Vercel setting was changed by this session.

## PR ledger
| PR | State | What |
|---|---|---|
| #54 | merged `4070e50` (squash) | /edge multi-sport cockpit (`get_multisport_cockpit`, `/api/public/edge/cockpit`, `mlb_core/data/cockpit_form.py`), /ev MARKET badge + 1/4 Kelly for 3+ book consensus rows, nav/hero polish. Backend must deploy before /edge cockpit tabs work. |
| #55 | merged `d7d4a33` | BettingPros live adapter (`ev/bettingpros_live.py`, not wired to a sweep), pooled-fallback consensus (`KEXEC_ALLOW_POOLED_FALLBACK`, default on, also reaches the DEMO Kalshi executor), Compare-books drawer, `/api/og/ev-recap-card`, history-join/availability features, `deploy/rollout_multisport_live.sh`. See `handoff_2026-10-14_multisport_bp_live_ui_features.md`. |
| #56 | merged `83488fa` | `tweet_drafter.py --mode settlement`, `mlb_core/social/gif_fetcher.py`, `/api/og/settlement-card`, #GamblingX voice filter. |
| #57 | CLOSED (auto) | Closed by GitHub when #54's branch was deleted after the squash merge; a closed PR cannot be retargeted. Superseded by #58. |
| #58 | open, ready for review | no-slop-ui refactor (same commit as #57, rebased on main): table-first /ev, single-row headers on /analytics and /sports/[sport], no nav badges/glows/hover transforms, `NO_SLOP_CHECKLIST.md`, `.claude/skills/no-slop-ui`, `tests/no-slop-ui.test.tsx`. |

## To go live
1. **#56**: rebuild the image (`deploy/deploy_service.sh`), provision a settlement Job (`python3 tweet_drafter.py` with `TWEET_MODE=settlement`, scheduled after `/settle` 09:00 UTC; `setup_tweet_jobs.sh` only has picks/recap), set `DISCORD_WEBHOOK_TWEETS` (or rely on `_OPS`), optionally `TENOR_API_KEY`/`GIPHY_API_KEY` (without one the GIF line is a Tenor search link). Vercel must deploy the card route (`BETTING_API_URL`/`BETTING_API_KEY`).
2. **#58**: look at `/ev` on a Vercel preview first -- it was only verified in jsdom (tsc + jest), never in a browser.
3. #54/#55 deploy notes: see their PR bodies and the multisport handoff.

## Known gaps / deviations
- `steam` mode not built: needs a public per-bet closing-line endpoint.
- Spec path `mlb/runners/tweet_drafter.py` does not exist; the script is at repo root (Dockerfile copies it from there).
- Settlement card has no player headshots (settled bet rows carry no league player id) and no season-curve sparkline.
- `/edge` fails no-slop rule 1 in part (`.edge-tagline` paragraph under the title); logged in the checklist, not fixed.
- `tests/test_nba_props_*` has 6 failures + 11 errors on main (reproduced with this work stashed): pre-existing, untouched.
- Process note: the first UI PR (#57) was stacked on #54's branch; deleting that branch on merge closed it. For stacked PRs, retarget to main before deleting the base branch.
