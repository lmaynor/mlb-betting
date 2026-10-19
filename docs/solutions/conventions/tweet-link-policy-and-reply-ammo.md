---
title: Main tweets are link-free; the link is a thread reply; reply snippets carry no link or brand
module: tweet_drafter.py
tags: [tweet-drafter, discord, x, reply-ammo, link-policy]
problem_type: convention
category: conventions
date: 2026-10-19
---

## Convention

- **A main tweet never carries a URL.** X deprioritizes posts from small accounts that contain an outbound link, so the draft ends
  with the plain-text watermark `beezy.fyi` (no scheme, no path). Gemini is told so in `SYSTEM_PROMPT` and both prompts, and
  `tweet_drafter.strip_urls()` enforces it on the output (any beezy.fyi URL becomes the watermark, any other URL is dropped).
- **The link is a reply the operator posts under their own tweet.** Typefully was removed 2026-10-19 (nothing posts to X
  automatically): `#tweet-queue` is the only output, and each draft's Discord message carries the reply text
  (`build_link_reply(label, url)`). picks -> `/picks`, recap/settlement -> `/results`. An X intent link cannot make a thread, so the
  reply is a manual second step.
- **Every drafting mode posts to Discord `#tweet-queue`** (`webhook_for("tweets")`) with the OG card image, a copyable code block
  and a 1-tap `x.com/intent/tweet` link. Discord limits (2000 chars of content, 6000 of embed text, 10 embeds) are handled by
  `post_embeds()`. Because it is the only sink, `require_posted()` raises on a failed or skipped post so the Job fails visibly.
- **`reply_ammo` snippets are for replying under other accounts' posts**: a plain market fact (book price vs the OTHER books'
  de-vigged fair), <= 180 chars, no URL, no `beezy`, no `@`. `is_link_free()` is the invariant and the builder returns None (skips the
  play) rather than truncating. The intent link is deliberately NOT offered for replies: it cannot target a tweet id, so a tap would
  post the snippet as a context-free standalone tweet.

## Not verified

The 50-80% reach penalty for outbound links is the premise of this convention, taken from outside advice, not measured on this
account. A bare `beezy.fyi` may still be auto-linked by X. Check how the first real
tweet-then-reply looks before relying on it.

## SETTLEMENT_SPORTS excludes NFL on purpose

`bets.sport` is `mlb|nhl|nba` (`bet_tracker.VALID_SPORTS`); NFL is shadow-only, so `/api/public/picks/recent?sport=nfl` answers 400 and
there is nothing to settle. Add "nfl" to `tweet_drafter.SETTLEMENT_SPORTS` and the settlement-card route's `SPORTS` only once NFL
bets are tracked (`tests/test_tweet_drafter.py::test_nfl_is_not_a_settlement_sport_because_nfl_bets_are_not_tracked` pins this).
