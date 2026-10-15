# Handoff -- 2026-10-15 -- IA merge (7 nav items -> 5)

Branch `feat/ia-merge`, stacked on `feat/beezy-score-v2` (PR #63) which is stacked on `fix/ui-consistency-sweep` (PR #61). Frontend only.

## What changed
Top nav was Home, +EV Terminal, Picks, Prop Research, Analytics, Results, Tools: four items answered "what is happening today" and two
answered "what happened". Now one item per question (`lib/ia.ts`): Home | +EV Terminal | Picks | Results | Tools. Pages that used to be
nav items are tabs of a section (`SectionTabs`, scrolls away, outside the sticky chrome):
- Scanner: /ev, /edge (Prop Research)
- Picks: /picks (Today), /picks/mlb (Systems), /cheat-sheet (Daily Card)
- Results: /results (Ledger), /analytics?tab=bankroll|calibration|books|health, /models
Analytics' in-page tab buttons are gone; its tab is the URL (`?tab=`), so tabs are linkable and survive reload.
Retired: `/sports/mlb` (308 -> /picks; it duplicated Picks' Today/Systems/slate), the three redirect-only `/tools/*` pages (now 308s in
`next.config.ts`). Mobile bottom nav is Home | +EV | Picks | Results | Tools. Footer links regrouped.
`tests/ia.test.ts` fails if any nav/tab link points at a page that does not exist.

## Deliberately NOT done (the audit proposed more)
- /edge is still its own 1,500-line client (cockpit + MLB model board) at its own URL, presented as a tab of the Scanner. Folding its
  inspector into /ev's Compare drawer is a rewrite and needs a design pass; the user-visible duplication (two nav items for one question) is gone.
- The MLB model board (inside /edge) is not moved into /picks.
- `sport-hub-client.tsx` still contains MLB-only branches (MlbToday, MLB models tab, slate tab) that are now unreachable; remove in a follow-up
  rather than churn 100+ lines here.
- /tools/slate stays a Pro tool (it is login-walled), not a Picks tab.
- Old inbound links to /sports/mlb and the three tools keep working through the 308s; update external links (Discord, tweets) when convenient.
