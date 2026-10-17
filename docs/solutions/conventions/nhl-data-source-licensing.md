---
title: "NHL data sources: what we may ingest for a commercial product (MoneyPuck / Natural Stat Trick / Evolving Hockey)"
module: nhl/, nhl/xg/
tags: [nhl, moneypuck, natural-stat-trick, evolving-hockey, licensing, tos, xg]
problem_type: convention
category: conventions
date: 2026-10-16
---

beezy.fyi is a commercial product (paid tiers are built, `PRE_LAUNCH` is only a flag). Checked 2026-10-16 from the sites' own
pages; this is a reading of public terms, NOT legal advice.

| Source | What the site says | Consequence here |
|---|---|---|
| MoneyPuck (moneypuck.com/data.htm) | "free to use for non-commercial purposes and by journalists for ad-hoc use"; "For other purposes please inquire"; credit MoneyPuck wherever the data is shown; "Non-approved scraping ... will be blocked"; message them before scraping data not on the page. No formal licence. | Do NOT ingest into the beezy pipeline or train on it without written permission. Downloading the listed files for private research is what the page invites; feeding a paid product is the "other purposes" case. |
| Natural Stat Trick | Returns HTTP 403 to automated fetches; its terms page could not be retrieved. | Treat as no automated access. Do not evade the block. |
| Evolving Hockey | Terms of Use: no "bot, spider, or other automatic method"; data "only for your personal use"; may not be distributed or mirrored; only STATISTICAL MODELS built from it may be shared, with a link. TOS: no reproduce / re-distribute / sell / "use ... for commercial purposes"; subscription features. | No scraper (the `hockey_dfs_optimizer` archive's R pipeline is exactly what the terms bar). Not usable for a commercial product without written consent (admin@evolving-hockey.com). |
| NHL web API (`api-web.nhle.com`, what `nhl/` ingests) | Undocumented, no auth, rate-limited per IP; widely used. | Already our source of record (box scores, play-by-play, shots with x/y). |

## How we get the same information without them

`nhl/xg/` fits our own expected-goals model on the shots we already hold (7 seasons of NHL play-by-play shots: location, shot type,
strength, empty net, previous-attempt context). Out-of-season AUC 0.755 (attempts) vs 0.720 for a distance+angle logistic; see
docs/analysis/nhl_xg_saves_goals_validation_2026-10-16.md. Team and goalie xG-against, finishing talent and goals saved above
expected are all derivable from it. What it does NOT have vs MoneyPuck: faceoff/hit/giveaway/takeaway context (rush and turnover
shots), which needs the raw play-by-play file per game (already cached in GCS at `NHL/raw/pbp/`).

## If you want MoneyPuck anyway

Ask them in writing (contact is on the page) for permission to use the datasets in a paid product, and keep the "credit
MoneyPuck.com" requirement in every surface that shows a derived number. Until a yes is on file: nothing in this repo reads their
files, and none of their data is in GCS.

## The archives are not a data source either

The six archives reviewed 2026-10-16 contained no secrets or unsafe calls (scanned), but their data is derived from the sites above
(MoneyPuck shot CSV in `nhl-xG-model`, NHL-API JSON in `the-quant-rink`) and their code is research-grade (e.g. the xG archive splits
train/test at random and headlines aggregate goal error). We took ideas, not files; nothing from them is vendored.
