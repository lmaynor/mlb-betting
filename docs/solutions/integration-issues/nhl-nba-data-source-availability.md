---
title: "NHL/NBA data sources: what is reachable, what is blocked, and the NHL API's per-IP rate limit"
module: nhl/, nba/data/espn_players.py, scripts/build_sport_assets.py
tags: [nhl, nba, sportsblaze, espn, nhl-api, rate-limit, headshots, logos]
problem_type: integration_issue
category: integration-issues
date: 2026-10-14
---

Probed from a developer laptop 2026-10-14. Re-probe from Cloud Run before relying on any of it: the repo has seen
network-category blocks differ by egress (Kalshi's CDN blocks some Cloud Run IPs; gambling-category hosts are blocked on the office LAN).

| Source | Result |
|---|---|
| SportsBlaze NHL | `/teams/nhl` and `/players/nhl` 200; `/seasons`, `/schedule`, `/boxscores` all **404** -> cannot be the NHL box-score backbone |
| api-web.nhle.com (official NHL) | schedule, score, boxscore, landing, play-by-play, roster, standings all 200, no auth; **rate-limits per IP (HTTP 429)** when two jobs overlap -> honour `Retry-After`, run one NHL job at a time |
| cdn.nba.com static JSON, stats.nba.com | 403 / unreachable -> do not build on them |
| ESPN site API (rosters, scoreboard, summary) | 200; full names, athlete ids, box scores by athlete id -> NBA crosswalk + grading source. ESPN ids are NOT NBA personIds |
| Headshots | NHL `assets.nhle.com/mugs/nhl/latest/{id}.png`, NBA ESPN combiner `...&w=200&h=146`, MLB `img.mlbstatic.com/.../headshot/silo/current`: all transparent PNG cutouts, CORS `*` |
| Logos | NHL `assets.nhle.com/logos/nhl/svg/{ABBR}_{light|dark}.svg`; NBA `cdn.nba.com/logos/nba/{id}/global/{L|D}/logo.svg` |

Dark UI: use the **dark** logo variants -- several are near-invisible on a dark background in their light form (NBA Spurs, NHL Maple Leafs).
`headshot/67` (MLB) is a JPEG with a background; the transparent one is `headshot/silo`.
