# Handoff -- 2026-10-17 -- fin-only icon, models by sport, Prop Research projections, props alerts routing

Branch `feat/brand-fin-models-prop-research`, stacked on `feat/brand-kit-solo-legal` (PR #80). Nothing deployed.

## What shipped
- **Icon / brand:** `app/icon.svg` and `app/favicon.ico` are the bare fin on a transparent canvas (Safari drew a frame around the tiled icon). The tile
  stays for the app icon (apple-icon, icon-192/512, avatars). `BrandWordmark` and the OG badge are the bare fin. `npm run brand:build` regenerates it all.
- **Tagline:** default title/description/OG no longer say "+EV Scanner and MLB Models" (now multi-sport; wordmark is lowercase `beezy.fyi`).
- **+EV terminal:** the ledger line is a strip of labelled numbers (record, flat, 1/4 Kelly, pending) with real padding; the paper caveat stays in the drawer.
- **Picks pages:** games strip is compact (both logos + start time on the viewer's own clock; codes and counts are in the tooltip / aria-label). MLB's
  `'8:00 PM ET'` slate text is resolved on the site date to an instant (`startInstant`). Day toggle, games strip and filter bar line up with the 1200px column.
- **Prop Research:** new `/edge/projections?league=` lists every model projection (MLB lambda predictions included) for one league through the same board,
  filters and games strip as the picks pages; `ResearchTabs` (PROP COCKPIT | ALL PROJECTIONS) on both views.
- **Models:** `/models` has an expandable section per sport (MLB open). New `/models/<sport>/<system>` model page for every NFL/NHL/NBA system, laid out like
  the MLB page (config, accuracy bars, feature list, status) from `lib/prop-model-specs.ts`; MLB gained pages for GAME, BATTER_TB, BATTER_HITS, PITCHER_ER.
- **Discord:** NFL/NHL/NBA props sweep alerts no longer land in #ops-alerts. `webhook_for("props_alerts")` = `DISCORD_WEBHOOK_ALERTS` only.

## Read these
- The ops-alerts cause: the `discord-webhook-alerts` secret was never created, so the old `ALERTS -> OPS` fallback sent every NFL props alert to ops.
  Now, with the secret unset, alerts are **logged, not posted**. To see them again create the admin alerts channel + webhook + secret, rerun
  `deploy/setup_props_alerts.sh` and mount it on `ev-nfl-props-sweep`. The NFL sweep's own run notices (started / budget exhausted) still go to ops.
  Not deployed: the sweep jobs need the feeds image rebuilt. `mlb_core/alerts/discord_routes.py` is also edited on `feat/discord-bot-postmessage`
  (the unresolved merge in the main checkout): expect a small conflict in `_CHAINS` / the docstring.
- BATTER_TB / BATTER_HITS show "Pending" for the out-of-sample metric and PITCHER_ER "Proxy": no study produced a number, so none is printed. GAME shows the
  recorded 0.5565 OOS AUC. Thresholds and Kelly fractions are copied from each system's config.
- Prop Research all-projections needs the real API: it showed the empty state locally (no API reachable), so the table with data was not eyeballed here.
  The strip, models pages and icon were checked in a browser.
